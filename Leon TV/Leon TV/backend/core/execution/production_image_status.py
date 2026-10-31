"""Sanitized formal-shot facts. No session, observer, dispatch or file mutation."""
from pathlib import Path

from core.creator.store import digest
from core.execution import production_image_persistence_contract as contract
from core.execution.image_control import read_image_control
from core.execution.image_policy import FrozenImageRequest, timestamp
from core.execution.media_ledger import records
from core.execution.media_persistence import MediaPersistenceExecution
from core.execution.media_persistence_io import PrivateMediaFiles
from core.execution.policy import now
from core.execution.production_image_persistence_schema import OPERATION_FIELD, RECEIVE
from core.planner.product_assets import image_jobs


def _authority(store, payload, operation):
    """Read exact persisted authority without constructing an execution session."""
    d = contract.authority(payload, operation[OPERATION_FIELD])
    grant = payload["media"]["grant:" + operation["grant_id"]]
    if grant["revoked_at"] is not None:
        return "revoked"
    if now() >= timestamp(d["valid_until"]):
        return "expired"
    try:
        base = read_image_control(Path(d["context"]["control_path"]), Path(d["context"]["backend_root"]))
        contract.require(store.root == base.roots["execution"] and base.control_hash == d["control_hash"])
        if d["runtime_activation_id"] is None:
            _, _, _, fresh = base._verified(for_dispatch=True)
            source = None
        else:
            from core.execution.image_runtime_continuation import verified_current

            fresh, source = verified_current(base, d["runtime_activation_id"])
        contract.require(digest(fresh) == digest(payload)
                         and d["runtime_authority_hash"] == (digest(source) if source else None)
                         and digest(contract._collect_code_manifest(base)) == d["code_manifest_hash"])
        if operation["status"] == "accepted":
            contract.fee_unknown_gate(payload, operation)
        return "valid"
    except Exception:
        return "unavailable"


class _FileInspection:
    # Only the shared pure proof decoder is used. No receiver or control is
    # instantiated, so this object cannot publish, dispatch or repair.
    contract = contract


def _file(store, record, query):
    published = False
    try:
        with PrivateMediaFiles(store.root, record["id"], read_only=True) as files:
            published = files.exists(".blob")
            saved, _, suffix = MediaPersistenceExecution._proof(_FileInspection(), files, record, query)
            if record["saved"] is not None:
                contract.require(digest(saved) == digest(record["saved"]))
        return (True, True, "available") if suffix == ".blob" else (True, False, "not_published")
    except FileNotFoundError:
        return False, False, "missing" if record["saved"] is not None else "not_recorded"
    except OSError:
        # Lock contention/permissions/unreadable storage are not proof that
        # the bytes are corrupt. Current availability remains unverified.
        return None, None, "not_verified"
    except Exception:
        # A failed HTTP/worker may leave empty temporary proof files. These
        # have never established a saved file and cannot invalidate history.
        return False, False, "invalid" if record["saved"] is not None or published else "not_recorded"


def _shot(store, payload, scope, job):
    row = {"shot_id": job["shot_id"], "first_frame_version": 1,
           "operation_id": None, "query_id": None, "opt_in": False,
           "generation": "not_started", "completion": "not_observed", "reception": "not_enabled",
           "received": None, "persisted": None, "delivery_registered": False, "delivery_origin": "none",
           "historical_saved": False, "file_availability": "not_recorded", "authority": "not_applicable",
           "fee_observed": False, "http_status": None, "error_code": None, "needs_input": False,
           "retry_authorized": False, "quality": "not_reviewed", "human_acceptance": "pending"}
    own = [op for op in records(payload, "operation") if op["scope_id"] == scope["id"] and op["shot_id"] == job["shot_id"]
           and FrozenImageRequest.from_frozen(op["descriptor"]).frozen()["permit"]["first_frame_version"] == 1]
    if not own:
        return row
    operation = max(own, key=lambda op: op["submission_index"])
    managed = OPERATION_FIELD in operation
    delivery = payload["media"].get("delivery:" + str(operation["delivery_id"]))
    receives = [r for r in records(payload, RECEIVE) if r["operation_id"] == operation["id"]]
    contract.require(len(receives) <= 1)
    reception = receives[0] if receives else None
    queries = [q for q in records(payload, "query") if q["operation_id"] == operation["id"]]
    query_id = reception["query_id"] if reception else delivery["query_id"] if delivery else None
    query = payload["media"].get("query:" + query_id) if query_id else max(queries, key=lambda q: q["query_index"], default=None)
    row.update(operation_id=operation["id"], query_id=query["id"] if query else None,
               opt_in=managed, generation=operation["status"], fee_observed=operation["fee_id"] is not None,
               completion=(query["status"] if query["status"] in {"completed", "failed"} else "unknown"
                           if query["status"] == "unknown" else "pending") if query else "not_observed",
               delivery_registered=delivery is not None,
               delivery_origin=("managed" if managed else "legacy") if delivery else "none")
    if not managed:
        if delivery:
            row.update(file_availability="not_verified", error_code="LEGACY_FILE_NOT_VERIFIED", needs_input=True)
        elif operation["status"] in {"unknown", "rejected"} or row["completion"] in {"completed", "failed", "unknown"}:
            row["needs_input"] = True
        return row
    row.update(authority=_authority(store, payload, operation), reception="not_started", received=False, persisted=False)
    if reception:
        row.update(reception=reception["status"], historical_saved=reception["saved"] is not None,
                   http_status=reception["http_status"], error_code=reception["error"])
        received, persisted, availability = _file(store, reception, query)
        row.update(received=received, persisted=persisted, file_availability=availability)
        if availability in {"missing", "invalid"}:
            row["error_code"] = "FILE_MISSING" if availability == "missing" else "FILE_INVALID"
        elif availability == "available" and not delivery:
            row["error_code"] = "DELIVERY_PENDING"
        elif availability == "not_published":
            row["error_code"] = "PERSISTENCE_PENDING"
        elif availability == "not_verified" and row["error_code"] is None:
            row["error_code"] = "LOCAL_STORAGE"
    done = row["file_availability"] == "available" and delivery is not None and row["historical_saved"] and reception is not None and reception["delivery_id"] == delivery["id"]
    row["needs_input"] = not done and (row["completion"] in {"completed", "failed", "unknown"}
                                      or row["generation"] in {"unknown", "rejected"} or row["authority"] != "valid")
    if row["needs_input"] and row["error_code"] is None:
        row["error_code"] = {"expired": "AUTHORITY_EXPIRED", "revoked": "AUTHORITY_REVOKED", "unavailable": "AUTHORITY_UNAVAILABLE"}.get(
            row["authority"], "PERSISTENCE_PENDING")
    return row


def production_images(store, payload, task_id):
    result = {"schema_version": 1, "task_id": task_id, "source_snapshot_hash": payload["tasks"][task_id]["source"]["snapshot_hash"],
              "evidence_kind": None, "scope_id": None, "status": "not_configured", "read_only": True,
              "required_count": 0, "registered_count": 0, "verified_file_count": 0, "needs_input_count": 0, "shots": []}
    scopes = [s for s in records(payload, "scope") if s["task_id"] == task_id]
    if not scopes:
        return result
    scope = max(scopes, key=lambda s: s["ordinal"])
    shots = [_shot(store, payload, scope, job) for job in image_jobs(scope["asset_input"], scope["manifest"])]
    count = sum(s["needs_input"] for s in shots)
    result.update(scope_id=scope["id"], evidence_kind=scope["handoff"]["evidence_kind"], shots=shots,
                  status="attention" if count else "observed", required_count=len(shots), needs_input_count=count,
                  registered_count=sum(s["delivery_registered"] for s in shots),
                  verified_file_count=sum(s["file_availability"] == "available" for s in shots))
    return result
