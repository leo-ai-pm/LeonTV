"""Native, prospective-only permission for one completed diagnostic image."""
import copy
import hashlib
from datetime import timedelta

from core.creator.store import CreatorError, digest
from core.execution import image_comparison as base
from core.execution.image_code_continuation import _checkpoint, _collect_code_manifest, _state
from core.execution.image_comparison_schema import COMMON
from core.execution.image_control import LoadedImageControl
from core.execution.image_policy import FrozenImageRequest, timestamp
from core.execution.image_receipts import decode_query
from core.execution.media_persistence_schema import (
    ACTIVATION,
    FIELDS,
    KINDS,
    POLICY,
    QUOTE,
    RECEIVE,
)
from core.execution.policy import now
from core.execution.store import ExecutionStore

DISCLOSURE = {"profile", "comparison_activation_id", "comparison_authority_hash", "control_hash",
              "code_manifest", "code_manifest_hash", "valid_until", "checkpoint", "policy"}
SAVED = {"sha256", "byte_length", "artifact", "saved_at", "network_started_at", "durable_at"}


def require(value):
    if not value:
        raise CreatorError("MEDIA_PERSISTENCE_UNAVAILABLE", "Bounded native media reception is unavailable", 409)


def authority(payload, activation_id):
    a = payload["media"][ACTIVATION + ":" + activation_id]
    q = payload["media"][QUOTE + ":" + a["quote_id"]]
    validate_record(payload, q)
    validate_record(payload, a)
    return q["disclosure"]


def verified_current(control, activation_id):
    require(type(control) is LoadedImageControl)
    _, _, ready, payload = control._verified(for_dispatch=False)
    d = authority(payload, activation_id)
    source = payload["media"][base.ACTIVATION + ":" + d["comparison_activation_id"]]
    base._verify_from_current(control, payload, ready, source["quote_id"])
    require(control.control_hash == d["control_hash"] and now() < timestamp(d["valid_until"])
            and digest(_collect_code_manifest(control)) == d["code_manifest_hash"])
    base.candidate_gate(payload, source["id"])
    return payload, d


def _no_operation(payload, comparison_id):
    require(not any(r["activation_id"] == comparison_id for r in base.records(payload, base.OPERATION)))


def quote_media_persistence(control, comparison_activation_id, *, valid_until):
    """Explicit permission before operation birth; cannot adopt any old task."""
    require(type(control) is LoadedImageControl)
    store = ExecutionStore(control.roots["execution"])
    with base._lock(store, control.document.admission.task_id), store.transaction() as payload:
        _, _, ready, actual = control._verified(for_dispatch=False)
        require(digest(actual) == digest(payload))
        source = payload["media"][base.ACTIVATION + ":" + comparison_activation_id]
        base._verify_from_current(control, payload, ready, source["quote_id"])
        _no_operation(payload, comparison_activation_id)
        parent = base.authority_for(payload, comparison_activation_id)
        require(now() < timestamp(valid_until) <= min(now() + timedelta(hours=24), timestamp(parent["policy"]["valid_until"])))
        found = [q for q in base.records(payload, QUOTE) if q["disclosure"]["comparison_activation_id"] == comparison_activation_id]
        if found:
            require(len(found) == 1 and found[0]["disclosure"]["valid_until"] == valid_until)
            return {"quote": copy.deepcopy(found[0]), "replayed": True}
        manifest = _collect_code_manifest(control)
        disclosure = {"profile": "completed_image_persistence_v1", "comparison_activation_id": comparison_activation_id,
                      "comparison_authority_hash": digest(parent), "control_hash": control.control_hash,
                      "code_manifest": manifest, "code_manifest_hash": digest(manifest), "valid_until": valid_until,
                      "checkpoint": _checkpoint(_state(store), payload), "policy": copy.deepcopy(POLICY)}
        scope = payload["media"]["scope:" + source["scope_id"]]
        record = base._record(QUOTE, scope, disclosure=disclosure, disclosure_hash=digest(disclosure))
        base._put(payload, record)
        return {"quote": record, "replayed": False}


def activate_media_persistence(control, quote_id, *, disclosure_hash, consent):
    require(type(control) is LoadedImageControl and consent is True)
    store = ExecutionStore(control.roots["execution"])
    with base._lock(store, control.document.admission.task_id), store.transaction() as payload:
        _, _, ready, actual = control._verified(for_dispatch=False)
        require(digest(actual) == digest(payload))
        q = payload["media"][QUOTE + ":" + quote_id]
        d = q["disclosure"]
        source = payload["media"][base.ACTIVATION + ":" + d["comparison_activation_id"]]
        base._verify_from_current(control, payload, ready, source["quote_id"])
        require(q["disclosure_hash"] == disclosure_hash and control.control_hash == d["control_hash"]
                and digest(_collect_code_manifest(control)) == d["code_manifest_hash"] and now() < timestamp(d["valid_until"]))
        _no_operation(payload, source["id"])
        found = [a for a in base.records(payload, ACTIVATION) if a["quote_id"] == quote_id]
        if found:
            require(len(found) == 1)
            return {"activation": copy.deepcopy(found[0]), "replayed": True}
        state = _state(store)
        require(state["head_revision"] == d["checkpoint"]["head_revision"] + 1
                and set(state["frames"][-1]["delta"].get("media", {})) == {QUOTE + ":" + quote_id})
        scope = payload["media"]["scope:" + source["scope_id"]]
        record = base._record(ACTIVATION, scope, quote_id=quote_id, disclosure_hash=disclosure_hash, consent=True)
        base._put(payload, record)
        return {"activation": record, "replayed": False}


def bind_birth(payload, record):
    activation_id = record["persistence_activation_id"]
    d = authority(payload, activation_id)
    a = payload["media"][ACTIVATION + ":" + activation_id]
    require(record["activation_id"] == d["comparison_activation_id"] and record["scope_id"] == a["scope_id"]
            and record["task_id"] == a["task_id"] and timestamp(a["created_at"]) <= timestamp(record["created_at"]) < timestamp(d["valid_until"]))
    return d


def completed(payload, operation_id, query_id):
    op = payload["media"][base.OPERATION + ":" + operation_id]
    query = payload["media"][base.QUERY + ":" + query_id]
    require("persistence_activation_id" in op and query["operation_id"] == op["id"]
            and query["activation_id"] == op["activation_id"] and "observation_activation_id" not in query
            and query["status"] == "completed" and op["status"] == "accepted" and op["fee_id"] is not None)
    bind_birth(payload, op)
    receipt = decode_query(FrozenImageRequest.from_frozen(query["descriptor"]), query["raw"])
    require(receipt["classification"] == "completed" and digest(receipt) == digest(query["receipt"]))
    return op, query, receipt


def saved_for_artifact(payload, operation, query, artifact):
    """New opt-in artifacts must follow the exact native saved reception."""
    found = [r for r in base.records(payload, RECEIVE) if r["operation_id"] == operation["id"]]
    require(len(found) == 1)
    r = found[0]
    require(r["persistence_activation_id"] == operation["persistence_activation_id"]
            and r["query_id"] == query["id"] and r["status"] == "saved" and r["saved"] is not None
            and r["query_hash"] == digest(query) and digest(r["saved"]["artifact"]) == digest(artifact))
    return r


def validate_record(payload, r):
    require(r["kind"] in KINDS and set(r) == COMMON | FIELDS[r["kind"]]
            and type(r["schema_version"]) is int and r["schema_version"] == 1)
    timestamp(r["created_at"])
    require(base.re.fullmatch(r"[a-f0-9]{32}", r["id"]) is not None)
    if r["kind"] != RECEIVE:
        values = {k: r[k] for k in FIELDS[r["kind"]]}
        require(r["id"] == digest({"kind": r["kind"], "scope_id": r["scope_id"], **values})[:32])
    if r["kind"] == QUOTE:
        d = r["disclosure"]
        require(type(d) is dict and set(d) == DISCLOSURE and d["profile"] == "completed_image_persistence_v1"
                and r["disclosure_hash"] == digest(d) and digest(d["policy"]) == digest(POLICY))
        a = payload["media"][base.ACTIVATION + ":" + d["comparison_activation_id"]]
        parent = base.authority_for(payload, a["id"])
        require(r["scope_id"] == a["scope_id"] and r["task_id"] == a["task_id"]
                and d["comparison_authority_hash"] == digest(parent) and d["code_manifest_hash"] == digest(d["code_manifest"]))
        from core.execution.image_code_continuation import _manifest
        _manifest(d["code_manifest"])
        require(timestamp(r["created_at"]) < timestamp(d["valid_until"]) <= min(timestamp(r["created_at"]) + timedelta(hours=24), timestamp(parent["policy"]["valid_until"])))
        require(type(d["control_hash"]) is str and base.re.fullmatch(r"[a-f0-9]{64}", d["control_hash"]))
    elif r["kind"] == ACTIVATION:
        q = payload["media"][QUOTE + ":" + r["quote_id"]]
        require(r["consent"] is True and r["scope_id"] == q["scope_id"] and r["task_id"] == q["task_id"]
                and r["disclosure_hash"] == q["disclosure_hash"]
                and timestamp(q["created_at"]) <= timestamp(r["created_at"]) < timestamp(q["disclosure"]["valid_until"]))
    else:
        op, query, receipt = completed(payload, r["operation_id"], r["query_id"])
        d = authority(payload, r["persistence_activation_id"])
        require(r["persistence_activation_id"] == op["persistence_activation_id"] and r["scope_id"] == op["scope_id"]
                and r["task_id"] == op["task_id"] and r["query_hash"] == digest(query)
                and r["receipt_hash"] == receipt["receipt_hash"]
                and r["url_sha256"] == hashlib.sha256(receipt["result_url"].encode()).hexdigest()
                and timestamp(r["created_at"]) < timestamp(r["deadline_at"]) == timestamp(d["valid_until"]))
        require(r["status"] in {"receiving", "saved", "failed", "unknown"}
                and (r["error"] is None or r["error"] in {"HTTP_NOT_200", "NETWORK_OR_WORKER", "INVALID_BYTES", "LOCAL_STORAGE", "INTERRUPTED_MISSING_BYTES", "RESOURCE_LIMIT", "TRUNCATED_RESPONSE", "TIMEOUT", "CAPACITY_EXCEEDED"})
                and (r["http_status"] is None or type(r["http_status"]) is int and 100 <= r["http_status"] <= 599))
        require((r["status"] == "receiving" and r["error"] is None and r["http_status"] is None
                 or r["status"] == "saved" and r["error"] is None and r["http_status"] == 200
                 or r["status"] in {"failed", "unknown"} and r["error"] is not None))
        if r["saved"] is None:
            require(r["status"] != "saved" and r["artifact_id"] is None)
        else:
            saved = r["saved"]
            require(type(saved) is dict and set(saved) == SAVED and r["status"] == "saved"
                    and type(saved["byte_length"]) is int and 0 < saved["byte_length"] <= POLICY["max_bytes"]
                    and saved["sha256"] == saved["artifact"]["delivery_sha256"]
                    and saved["byte_length"] == saved["artifact"]["delivery_size_bytes"]
                    and timestamp(r["created_at"]) <= timestamp(saved["network_started_at"]) <= timestamp(saved["saved_at"]) <= timestamp(saved["durable_at"]) <= timestamp(r["deadline_at"]) - timedelta(seconds=POLICY["cleanup_seconds"])
                    and (timestamp(saved["durable_at"]) - timestamp(saved["network_started_at"])).total_seconds() <= POLICY["absolute_timeout_seconds"])
        if r["artifact_id"] is not None:
            a = payload["media"][base.ARTIFACT + ":" + r["artifact_id"]]
            require(op["artifact_id"] == a["id"] and a["operation_id"] == op["id"] and a["query_id"] == query["id"]
                    and digest(a["artifact"]) == digest(r["saved"]["artifact"]))


def validate_transition(payload, previous):
    if previous is None:
        return
    old = previous.get("media", {})
    fresh = [r for k, r in payload["media"].items() if k not in old and r["kind"] in KINDS]
    for r in fresh:
        require(len(fresh) == 1)
        without = {**payload, "media": {k: v for k, v in payload["media"].items() if k != r["kind"] + ":" + r["id"]}}
        require(digest(without) == digest(previous))
        if r["kind"] == QUOTE:
            d = r["disclosure"]
            _no_operation(previous, d["comparison_activation_id"])
            require(d["checkpoint"]["payload_hash"] == digest(previous)
                    and not any(q["disclosure"]["comparison_activation_id"] == d["comparison_activation_id"] for q in base.records(previous, QUOTE)))
        elif r["kind"] == ACTIVATION:
            q = old[QUOTE + ":" + r["quote_id"]]
            _no_operation(previous, q["disclosure"]["comparison_activation_id"])
            require(not any(a["quote_id"] == r["quote_id"] for a in base.records(previous, ACTIVATION)))
        else:
            require(r["status"] == "receiving" and all(r[k] is None for k in ("error", "http_status", "saved", "artifact_id"))
                    and not any(v["operation_id"] == r["operation_id"] for v in base.records(previous, RECEIVE)))
    for key, r in old.items():
        if r["kind"] not in KINDS:
            continue
        after = payload["media"][key]
        mutable = {"status", "error", "http_status", "saved", "artifact_id"} if r["kind"] == RECEIVE else set()
        require(digest({k: v for k, v in r.items() if k not in mutable}) == digest({k: v for k, v in after.items() if k not in mutable}))
        if r["kind"] == RECEIVE:
            require(after["status"] in ({"receiving", "saved", "failed", "unknown"} if r["status"] == "receiving" else {r["status"], "saved"} if r["status"] == "unknown" else {r["status"]}))
            require(all(r[k] is None or digest(r[k]) == digest(after[k]) for k in ("http_status", "saved", "artifact_id")))
    for key, op in payload["media"].items():
        if op["kind"] == base.OPERATION and "persistence_activation_id" in op:
            bind_birth(payload, op)
            if key not in old:
                require(ACTIVATION + ":" + op["persistence_activation_id"] in old)


def validate_history(state, payload):
    for frame in state["frames"]:
        for r in frame["delta"].get("media", {}).values():
            if r["kind"] == QUOTE:
                cp = r["disclosure"]["checkpoint"]
                require(cp["head_revision"] == frame["revision"] - 1 and cp["head_hash"] == frame["previous_hash"])
            elif r["kind"] == ACTIVATION:
                q = payload["media"][QUOTE + ":" + r["quote_id"]]
                revision = q["disclosure"]["checkpoint"]["head_revision"] + 1
                birth = state["frames"][revision - 1]
                require(frame["revision"] == revision + 1 and frame["previous_hash"] == birth["hash"]
                        and set(birth["delta"].get("media", {})) == {QUOTE + ":" + q["id"]})
