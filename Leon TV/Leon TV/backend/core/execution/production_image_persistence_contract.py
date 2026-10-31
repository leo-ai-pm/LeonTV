"""Explicit prospective formal-shot reception; original grant/budget unchanged."""
import copy
from datetime import timedelta
from pathlib import Path
from types import MethodType

from core.creator.store import CreatorError, digest
from core.execution import image_comparison as native
from core.execution.image_code_continuation import (
    _checkpoint,
    _collect_code_manifest,
    _manifest,
    _state,
)
from core.execution.image_comparison_schema import COMMON
from core.execution.image_control import LoadedImageControl
from core.execution.image_policy import FrozenImageRequest, timestamp
from core.execution.image_receipts import decode_query
from core.execution.image_runtime_control import RuntimeContinuedImageControl
from core.execution.media_ledger import (
    account_halted,
    has_pending_fees,
    has_pending_unknown,
    records,
    unresolved_text_claims,
)
from core.execution.media_persistence_contract import SAVED
from core.execution.policy import now
from core.execution.production_image_persistence_schema import (
    ACTIVATION,
    FIELDS,
    KINDS,
    OPERATION_FIELD,
    POLICY,
    QUOTE,
    RECEIVE,
)
from core.planner.product_assets import image_jobs

DISCLOSURE = {"profile", "context", "control_hash", "scope_hash", "grant_id", "grant_hash", "shot_id",
              "first_frame_version", "job_hash", "submission_index", "runtime_activation_id", "runtime_authority_hash",
              "code_manifest", "code_manifest_hash", "valid_until", "policy", "checkpoint"}
ERRORS = {"HTTP_NOT_200", "NETWORK_OR_WORKER", "INVALID_BYTES", "LOCAL_STORAGE", "INTERRUPTED_MISSING_BYTES",
          "RESOURCE_LIMIT", "TRUNCATED_RESPONSE", "TIMEOUT", "CAPACITY_EXCEEDED"}


def require(value):
    if not value:
        raise CreatorError("PRODUCTION_IMAGE_PERSISTENCE_UNAVAILABLE", "Exact formal image persistence is unavailable", 409)


def grant_hash(grant):
    return digest({k: v for k, v in grant.items() if k != "revoked_at"})


def bound_control(media):
    from core.execution.media_runtime import MediaExecution

    require(type(media) is MediaExecution)
    control = getattr(media, "image_control", None)
    require(type(control) in {LoadedImageControl, RuntimeContinuedImageControl})
    base = control if type(control) is LoadedImageControl else control.base
    guard = media.authority_guard
    require(type(guard) is MethodType and guard.__self__ is base and guard.__func__ is LoadedImageControl.guard
            and media.store.root == base.roots["execution"])
    if type(control) is RuntimeContinuedImageControl:
        require(media._bound_continuation_control() is control)
    else:
        require(media.continuation_control is None and media.continuation_activation_id is None)
    return control, base


def fresh_media(media, grant_id):
    control, base = bound_control(media)
    if type(control) is RuntimeContinuedImageControl:
        payload, authority = control.verified_current()
    else:
        _, _, _, payload = base._verified(for_dispatch=True)
        authority = None
    grant = payload["media"]["grant:" + grant_id]
    scope = payload["media"]["scope:" + grant["scope_id"]]
    require(grant["revoked_at"] is None and scope["task_id"] == base.document.admission.task_id)
    media._authority(scope, payload=payload)
    require(not account_halted(payload, scope["account_id"]) and not unresolved_text_claims(payload)
            and all(op["status"] == "settled" for op in payload["operations"].values() if op["account_id"] == scope["account_id"]))
    return payload, grant, scope, authority, base


def authority(payload, activation_id):
    activation = payload["media"][ACTIVATION + ":" + activation_id]
    quote = payload["media"][QUOTE + ":" + activation["quote_id"]]
    validate_record(payload, quote)
    validate_record(payload, activation)
    return quote["disclosure"]


def verified_current(media, activation_id):
    # The initial native read is necessary when outside a transaction. Inside
    # the original lock/transaction it reuses only the committed read proof.
    snapshot = media.store.read_only()
    d = authority(snapshot, activation_id)
    payload, grant, scope, source, base = fresh_media(media, d["grant_id"])
    require(digest(snapshot) == digest(payload) and d["control_hash"] == base.control_hash
            and d["context"] == {"control_path": str(base.path), "backend_root": str(base.backend_root)}
            and d["scope_hash"] == digest(scope) and d["grant_hash"] == grant_hash(grant)
            and d["runtime_activation_id"] == (source["activation_id"] if source else None)
            and d["runtime_authority_hash"] == (digest(source) if source else None)
            and d["code_manifest_hash"] == digest(_collect_code_manifest(base))
            and now() < timestamp(d["valid_until"]))
    own = [op for op in records(payload, "operation") if op.get(OPERATION_FIELD) == activation_id]
    require(len(own) <= 1)
    if own:
        fee_unknown_gate(payload, own[0])
    return payload, d


def source_session(control):
    from core.execution.image_operator import build_image_session

    require(type(control) in {LoadedImageControl, RuntimeContinuedImageControl})
    return build_image_session(control)


def quote_production_image_persistence(control, grant_id, shot_id, *, valid_until):
    with source_session(control) as session:
        media, store = session.media, session.media.store
        task_id = control.document.admission.task_id
        with media._lock(task_id), store.transaction() as current:
            payload, grant, scope, source, base = fresh_media(media, grant_id)
            require(digest(payload) == digest(current))
            jobs = [job for job in image_jobs(scope["asset_input"], scope["manifest"]) if job["shot_id"] == shot_id]
            require(len(jobs) == 1)
            job = jobs[0]
            index = len([op for op in records(payload, "operation") if op["scope_id"] == scope["id"]]) + 1
            until = source["effective_policy"]["valid_until"] if source else scope["policy"]["valid_until"]
            require(now() < timestamp(valid_until) <= min(now() + timedelta(hours=24), timestamp(until)))
            found = [q for q in records(payload, QUOTE) if q["disclosure"]["grant_id"] == grant_id
                     and q["disclosure"]["shot_id"] == shot_id and q["disclosure"]["submission_index"] == index]
            if found:
                require(len(found) == 1 and found[0]["disclosure"]["valid_until"] == valid_until
                        and found[0]["disclosure"]["code_manifest_hash"] == digest(_collect_code_manifest(base))
                        and found[0]["disclosure"]["runtime_activation_id"] == (source["activation_id"] if source else None))
                return {"quote": copy.deepcopy(found[0]), "replayed": True}
            manifest = _collect_code_manifest(base)
            d = {"profile": "formal_image_persistence_v1", "context": {"control_path": str(base.path), "backend_root": str(base.backend_root)},
                 "control_hash": base.control_hash, "scope_hash": digest(scope), "grant_id": grant_id, "grant_hash": grant_hash(grant),
                 "shot_id": shot_id, "first_frame_version": 1, "job_hash": digest(job), "submission_index": index,
                 "runtime_activation_id": source["activation_id"] if source else None, "runtime_authority_hash": digest(source) if source else None,
                 "code_manifest": manifest, "code_manifest_hash": digest(manifest), "valid_until": valid_until,
                 "policy": copy.deepcopy(POLICY), "checkpoint": _checkpoint(_state(store), payload)}
            quote = native._record(QUOTE, scope, disclosure=d, disclosure_hash=digest(d))
            native._put(current, quote)
            return {"quote": quote, "replayed": False}


def activate_production_image_persistence(control, quote_id, *, disclosure_hash, consent):
    require(consent is True)
    with source_session(control) as session:
        media, store = session.media, session.media.store
        with media._lock(control.document.admission.task_id), store.transaction() as current:
            q = current["media"][QUOTE + ":" + quote_id]
            d = q["disclosure"]
            payload, grant, scope, source, base = fresh_media(media, d["grant_id"])
            require(digest(payload) == digest(current) and q["disclosure_hash"] == disclosure_hash
                    and base.control_hash == d["control_hash"] and digest(scope) == d["scope_hash"]
                    and grant_hash(grant) == d["grant_hash"] and digest(_collect_code_manifest(base)) == d["code_manifest_hash"]
                    and now() < timestamp(d["valid_until"])
                    and d["context"] == {"control_path": str(base.path), "backend_root": str(base.backend_root)}
                    and d["runtime_activation_id"] == (source["activation_id"] if source else None)
                    and d["runtime_authority_hash"] == (digest(source) if source else None))
            require(d["submission_index"] == len([op for op in records(current, "operation") if op["scope_id"] == scope["id"]]) + 1)
            found = [a for a in records(current, ACTIVATION) if a["quote_id"] == quote_id]
            if found:
                require(len(found) == 1)
                return {"activation": copy.deepcopy(found[0]), "replayed": True}
            state = _state(store)
            require(state["head_revision"] == d["checkpoint"]["head_revision"] + 1
                    and set(state["frames"][-1]["delta"].get("media", {})) == {QUOTE + ":" + quote_id})
            a = native._record(ACTIVATION, scope, quote_id=quote_id, disclosure_hash=disclosure_hash, consent=True)
            native._put(current, a)
            return {"activation": a, "replayed": False}


def bind_birth(payload, operation):
    d = authority(payload, operation[OPERATION_FIELD])
    grant = payload["media"]["grant:" + operation["grant_id"]]
    request = FrozenImageRequest.from_frozen(operation["descriptor"])
    job = request.frozen()["job"]
    activation = payload["media"][ACTIVATION + ":" + operation[OPERATION_FIELD]]
    require(operation["kind"] == "operation" and operation["scope_id"] == activation["scope_id"]
            and operation["task_id"] == activation["task_id"] and operation["grant_id"] == d["grant_id"]
            and operation["quote_id"] == grant["quote_id"] and operation["shot_id"] == d["shot_id"]
            and operation["submission_index"] == d["submission_index"] and digest(job) == d["job_hash"]
            and request.frozen()["permit"]["first_frame_version"] == d["first_frame_version"]
            and request.frozen()["permit"].get("runtime_activation_id") == d["runtime_activation_id"]
            and timestamp(activation["created_at"]) <= timestamp(operation["created_at"]) < timestamp(d["valid_until"]))
    return d


def completed(payload, operation_id, query_id):
    operation = payload["media"]["operation:" + operation_id]
    query = payload["media"]["query:" + query_id]
    require(OPERATION_FIELD in operation and operation["status"] == "accepted" and operation["fee_id"] is not None
            and query["kind"] == "query" and query["operation_id"] == operation_id and query["task_id"] == operation["task_id"]
            and query["scope_id"] == operation["scope_id"] and query["status"] == "completed")
    bind_birth(payload, operation)
    receipt = decode_query(FrozenImageRequest.from_frozen(query["descriptor"]), query["raw"])
    require(receipt["classification"] == "completed" and digest(receipt) == digest(query["receipt"]))
    return operation, query, receipt


def fee_unknown_gate(payload, operation):
    scope = payload["media"]["scope:" + operation["scope_id"]]
    require(operation["fee_id"] is not None and not account_halted(payload, scope["account_id"])
            and not unresolved_text_claims(payload))
    if operation["descriptor"]["kind"] in {"controlled_image_http_v2", "controlled_image_http_v3"}:
        from core.execution.media_continuation_gate import candidate_gate
        # Reuse the exact existing candidate gate on its original pre-candidate
        # view. Only this already accepted operation is removed; no unknown,
        # query, fee or other namespace is suppressed or changed.
        view = {**payload, "media": {k: v for k, v in payload["media"].items() if k != "operation:" + operation["id"]}}
        candidate_gate(view, operation)
    else:
        require(not has_pending_unknown(payload, scope["account_id"]) and not has_pending_fees(payload, scope["account_id"]))


def saved_for_delivery(payload, operation, query, delivery):
    found = [r for r in records(payload, RECEIVE) if r["operation_id"] == operation["id"]]
    require(len(found) == 1)
    r = found[0]
    require(r["persistence_activation_id"] == operation[OPERATION_FIELD] and r["query_id"] == query["id"]
            and r["query_hash"] == digest(query) and r["status"] == "saved" and r["saved"] is not None
            and digest(r["saved"]["artifact"]) == digest(delivery))
    return r


def validate_record(payload, record):
    require(record["kind"] in KINDS and set(record) == COMMON | FIELDS[record["kind"]]
            and type(record["schema_version"]) is int and record["schema_version"] == 1)
    timestamp(record["created_at"])
    require(native.re.fullmatch(r"[a-f0-9]{32}", record["id"]))
    if record["kind"] in {QUOTE, ACTIVATION}:
        values = {key: record[key] for key in FIELDS[record["kind"]]}
        require(record["id"] == digest({"kind": record["kind"], "scope_id": record["scope_id"], **values})[:32])
    if record["kind"] == QUOTE:
        d = record["disclosure"]
        require(type(d) is dict and set(d) == DISCLOSURE and d["profile"] == "formal_image_persistence_v1"
                and digest(d["policy"]) == digest(POLICY) and record["disclosure_hash"] == digest(d))
        scope = payload["media"]["scope:" + record["scope_id"]]
        grant = payload["media"]["grant:" + d["grant_id"]]
        require(grant["scope_id"] == scope["id"] and record["task_id"] == scope["task_id"]
                and digest(scope) == d["scope_hash"] and grant_hash(grant) == d["grant_hash"])
        jobs = [job for job in image_jobs(scope["asset_input"], scope["manifest"]) if job["shot_id"] == d["shot_id"]]
        require(len(jobs) == 1 and digest(jobs[0]) == d["job_hash"] and type(d["first_frame_version"]) is int
                and d["first_frame_version"] == 1 and type(d["submission_index"]) is int and d["submission_index"] >= 1)
        require(type(d["context"]) is dict and set(d["context"]) == {"control_path", "backend_root"})
        for value in d["context"].values():
            require(type(value) is str and Path(value).is_absolute() and ".." not in Path(value).parts and "\\" not in value)
        require(type(d["control_hash"]) is str and native.re.fullmatch(r"[a-f0-9]{64}", d["control_hash"]))
        _manifest(d["code_manifest"])
        require(d["code_manifest_hash"] == digest(d["code_manifest"]))
        until = scope["policy"]["valid_until"]
        if d["runtime_activation_id"] is not None:
            from core.execution.image_runtime_continuation import authority_for
            a = authority_for(payload, d["runtime_activation_id"])
            require(a["scope_id"] == scope["id"] and a["grant_id"] == grant["id"] and d["runtime_authority_hash"] == digest(a))
            until = a["effective_policy"]["valid_until"]
        else:
            require(d["runtime_authority_hash"] is None)
        require(timestamp(record["created_at"]) < timestamp(d["valid_until"]) <= min(timestamp(record["created_at"]) + timedelta(hours=24), timestamp(until)))
    elif record["kind"] == ACTIVATION:
        q = payload["media"][QUOTE + ":" + record["quote_id"]]
        require(record["consent"] is True and record["disclosure_hash"] == q["disclosure_hash"]
                and record["scope_id"] == q["scope_id"] and record["task_id"] == q["task_id"]
                and timestamp(q["created_at"]) <= timestamp(record["created_at"]) < timestamp(q["disclosure"]["valid_until"]))
    else:
        op, query, receipt = completed(payload, record["operation_id"], record["query_id"])
        d = authority(payload, record["persistence_activation_id"])
        require(record["persistence_activation_id"] == op[OPERATION_FIELD] and record["scope_id"] == op["scope_id"]
                and record["task_id"] == op["task_id"] and record["query_hash"] == digest(query)
                and record["receipt_hash"] == receipt["receipt_hash"] and record["url_sha256"] == receipt["result_url_sha256"]
                and timestamp(record["created_at"]) < timestamp(record["deadline_at"]) == timestamp(d["valid_until"]))
        require(record["status"] in {"receiving", "saved", "failed", "unknown"}
                and (record["error"] is None or record["error"] in ERRORS)
                and (record["http_status"] is None or type(record["http_status"]) is int and 100 <= record["http_status"] <= 599))
        require(record["status"] == "receiving" and record["error"] is None and record["http_status"] is None
                or record["status"] == "saved" and record["error"] is None and record["http_status"] == 200
                or record["status"] in {"failed", "unknown"} and record["error"] is not None)
        if record["saved"] is None:
            require(record["status"] != "saved" and record["delivery_id"] is None)
        else:
            saved = record["saved"]
            require(type(saved) is dict and set(saved) == SAVED and record["status"] == "saved"
                    and type(saved["byte_length"]) is int and 0 < saved["byte_length"] <= POLICY["max_bytes"]
                    and saved["sha256"] == saved["artifact"]["delivery_sha256"]
                    and saved["byte_length"] == saved["artifact"]["delivery_size_bytes"]
                    and timestamp(record["created_at"]) <= timestamp(saved["network_started_at"]) <= timestamp(saved["saved_at"]) <= timestamp(saved["durable_at"]) <= timestamp(record["deadline_at"]) - timedelta(seconds=5)
                    and (timestamp(saved["durable_at"]) - timestamp(saved["network_started_at"])).total_seconds() <= 45)
        if record["delivery_id"] is not None:
            delivery = payload["media"]["delivery:" + record["delivery_id"]]
            require(op["delivery_id"] == delivery["id"] and delivery["operation_id"] == op["id"] and delivery["query_id"] == query["id"]
                    and digest(delivery["delivery"]) == digest(record["saved"]["artifact"]))


def validate_transition(payload, previous):
    if previous is None:
        return
    old = previous.get("media", {})
    added = [record for key, record in payload["media"].items() if key not in old and record["kind"] in KINDS]
    for record in added:
        require(len(added) == 1)
        without = {**payload, "media": {k: v for k, v in payload["media"].items() if k != record["kind"] + ":" + record["id"]}}
        require(digest(without) == digest(previous))
        if record["kind"] == QUOTE:
            d = record["disclosure"]
            require(d["checkpoint"]["payload_hash"] == digest(previous)
                    and d["submission_index"] == len([op for op in records(previous, "operation") if op["scope_id"] == record["scope_id"]]) + 1)
            require(not any(q["disclosure"]["grant_id"] == d["grant_id"] and q["disclosure"]["shot_id"] == d["shot_id"]
                            and q["disclosure"]["submission_index"] == d["submission_index"] for q in records(previous, QUOTE)))
        elif record["kind"] == ACTIVATION:
            q = old[QUOTE + ":" + record["quote_id"]]
            require(not any(a["quote_id"] == q["id"] for a in records(previous, ACTIVATION)))
        else:
            require(record["status"] == "receiving" and all(record[k] is None for k in ("saved", "error", "http_status", "delivery_id"))
                    and not any(r["operation_id"] == record["operation_id"] for r in records(previous, RECEIVE)))
            fee_unknown_gate(previous, previous["media"]["operation:" + record["operation_id"]])
    for key, before in old.items():
        if before["kind"] not in KINDS:
            continue
        after = payload["media"][key]
        mutable = {"status", "error", "http_status", "saved", "delivery_id"} if before["kind"] == RECEIVE else set()
        require(digest({k: v for k, v in before.items() if k not in mutable}) == digest({k: v for k, v in after.items() if k not in mutable}))
        if mutable:
            allowed = {"receiving", "saved", "failed", "unknown"} if before["status"] == "receiving" else {before["status"], "saved"} if before["status"] == "unknown" else {before["status"]}
            require(after["status"] in allowed and all(before[k] is None or digest(before[k]) == digest(after[k]) for k in ("http_status", "saved", "delivery_id")))
    for key, op in payload["media"].items():
        if op["kind"] == "operation" and OPERATION_FIELD in op:
            bind_birth(payload, op)
            if key not in old:
                require(ACTIVATION + ":" + op[OPERATION_FIELD] in old)
                require(not any(r.get(OPERATION_FIELD) == op[OPERATION_FIELD] for r in records(previous, "operation")))


def validate_history(state, payload):
    for frame in state["frames"]:
        for record in frame["delta"].get("media", {}).values():
            if record["kind"] == QUOTE:
                cp = record["disclosure"]["checkpoint"]
                require(cp["head_revision"] == frame["revision"] - 1 and cp["head_hash"] == frame["previous_hash"])
            elif record["kind"] == ACTIVATION:
                q = payload["media"][QUOTE + ":" + record["quote_id"]]
                revision = q["disclosure"]["checkpoint"]["head_revision"] + 1
                require(frame["revision"] == revision + 1
                        and set(state["frames"][revision - 1]["delta"].get("media", {})) == {QUOTE + ":" + q["id"]})
