"""Explicit observation of an immutable accepted diagnostic, never another POST.

The old approval is historical evidence only. One new, nonrenewing approval
binds current code, the original account/parent/collector and the accepted task.
Historical validation is pure; each live boundary independently reads reality.
"""
import copy
from datetime import timedelta

from core.creator.store import CreatorError, digest
from core.execution import image_comparison as base
from core.execution.image_code_continuation import (
    _checkpoint,
    _collect_code_manifest,
    _lock,
    _manifest,
    _state,
)
from core.execution.image_comparison_schema import COMMON
from core.execution.image_comparison_schema import OBS_ACTIVATION as ACTIVATION
from core.execution.image_comparison_schema import OBS_FIELDS as FIELDS
from core.execution.image_comparison_schema import OBS_KINDS as KINDS
from core.execution.image_comparison_schema import OBS_QUOTE as QUOTE
from core.execution.image_control import LoadedImageControl
from core.execution.image_observation_continuation import _effective
from core.execution.image_policy import FrozenImageRequest, timestamp
from core.execution.image_receipts import decode_query, decode_submit
from core.execution.media_ledger import (
    account_halted,
    has_pending_fees,
    has_pending_unknown,
    records,
    unresolved_text_claims,
)
from core.execution.policy import now
from core.execution.store import ExecutionStore
from core.planner.review import json_copy

ERROR = "IMAGE_COMPARISON_OBSERVATION_UNAVAILABLE"
AUTHORITY_KIND = "image_comparison_observation_authority_v1"
BINDINGS = {
    "control_hash", "scope_id", "task_id", "source_operation_id", "source_operation_hash",
    "source_descriptor_hash", "source_raw_hash", "source_receipt_hash", "source_capture_started_at",
    "accepted_task_id", "parent_authority", "parent_quote_hash", "retained_unknowns", "original_baseline_hash",
}
LIMITS = {
    "post_cap": 0, "max_new_queries": 1, "additional_risk_cny": "0.000000", "additional_paid_calls": 0,
    "B_authorized": False, "formal_delivery_authorized": False, "automatic_followup_calls": 0,
    "exact_fee_required_for_GET": True,
}
DISCLOSURE_FIELDS = BINDINGS | set(LIMITS) | {
    "schema_version", "kind", "source_collector", "effective_policy", "response_policy", "new_code_manifest",
    "code_manifest_hash", "checkpoint",
}
AUTHORITY_FIELDS = BINDINGS | set(LIMITS) | {
    "schema_version", "kind", "activation_id", "quote_id", "effective_policy", "response_policy",
    "code_manifest_hash", "authority_hash",
}


def require(value):
    if not value:
        raise CreatorError(ERROR, "Exact accepted diagnostic observation authority is unavailable", 409)


def immutable_operation(operation):
    return {k: v for k, v in operation.items() if k not in {"fee_id", "artifact_id"}}


def bindings(payload, operation_id):
    require(type(operation_id) is str and base.OPERATION + ":" + operation_id in payload["media"])
    op = payload["media"][base.OPERATION + ":" + operation_id]
    require(op["kind"] == base.OPERATION and op["status"] == "accepted" and op["capture_started_at"] is not None)
    request = FrozenImageRequest.from_frozen(op["descriptor"])
    require(request.method == "POST" and request.frozen()["kind"] == "controlled_image_comparison_http_v1")
    receipt = decode_submit(request, op["raw"])
    require(receipt["classification"] == "accepted" and receipt["accepted_task_id"] is not None
            and digest(receipt) == digest(op["receipt"]))
    parent = base.authority_for(payload, op["activation_id"])
    require(digest(parent) == digest(op["descriptor"]["comparison_authority"])
            and parent["scope_id"] == op["scope_id"] and parent["task_id"] == op["task_id"])
    q = payload["media"][base.QUOTE + ":" + parent["quote_id"]]
    return {
        "control_hash": q["proposal"]["control_hash"], "scope_id": op["scope_id"], "task_id": op["task_id"],
        "source_operation_id": operation_id, "source_operation_hash": digest(immutable_operation(op)),
        "source_descriptor_hash": request.descriptor_hash, "source_raw_hash": digest(op["raw"]),
        "source_receipt_hash": receipt["receipt_hash"], "source_capture_started_at": op["capture_started_at"],
        "accepted_task_id": receipt["accepted_task_id"], "parent_authority": parent,
        "parent_quote_hash": digest(q), "retained_unknowns": copy.deepcopy(q["proposal"]["retained_unknowns"]),
        "original_baseline_hash": q["proposal"]["baseline_hash"],
    }


def validate_authority(value):
    from core.execution.image_comparison_policy import checked_authority
    from core.execution.image_runtime_continuation import _hash, _id

    value = json_copy(value, limit=16 * 1048576)
    require(type(value) is dict and set(value) == AUTHORITY_FIELDS and type(value["schema_version"]) is int
            and value["schema_version"] == 1 and value["kind"] == AUTHORITY_KIND)
    require(value["authority_hash"] == digest({k: v for k, v in value.items() if k != "authority_hash"}))
    require(all(type(value[k]) is type(v) and value[k] == v for k, v in LIMITS.items()))
    require(all(_id(value[k]) for k in ("activation_id", "quote_id", "scope_id", "task_id", "source_operation_id")))
    require(all(_hash(value[k]) for k in ("control_hash", "source_operation_hash", "source_descriptor_hash", "source_raw_hash",
                                        "source_receipt_hash", "parent_quote_hash", "original_baseline_hash", "code_manifest_hash")))
    parent = checked_authority(value["parent_authority"])
    require(all(value[k] == parent[k] for k in ("scope_id", "task_id")))
    require(digest(value["effective_policy"]) == digest(_effective(parent["policy"], value["effective_policy"]["valid_until"])))
    require(digest(value["response_policy"]) == digest(parent["response_policy"]))
    require(type(value["accepted_task_id"]) is str and base.re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value["accepted_task_id"]))
    require(type(value["retained_unknowns"]) is list and len(value["retained_unknowns"]) == 3)
    require(len({x["operation_id"] for x in value["retained_unknowns"]}) == 3)
    timestamp(value["source_capture_started_at"])
    return value


def _disclosure(payload, record):
    d = record["disclosure"]
    require(type(d) is dict and set(d) == DISCLOSURE_FIELDS and type(d["schema_version"]) is int
            and d["schema_version"] == 1 and d["kind"] == "image_comparison_observation_disclosure_v1")
    actual = bindings(payload, d["source_operation_id"])
    require(all(digest(d[k]) == digest(v) for k, v in actual.items()))
    require(all(type(d[k]) is type(v) and d[k] == v for k, v in LIMITS.items()))
    require(record["scope_id"] == d["scope_id"] and record["task_id"] == d["task_id"])
    require(digest(d["effective_policy"]) == digest(_effective(d["parent_authority"]["policy"], d["effective_policy"]["valid_until"])))
    require(digest(d["response_policy"]) == digest(d["parent_authority"]["response_policy"]))
    created, until = timestamp(record["created_at"]), timestamp(d["effective_policy"]["valid_until"])
    require(timestamp(payload["media"][base.OPERATION + ":" + d["source_operation_id"]]["raw"]["received_at"]) <= created
            < until <= created + timedelta(hours=24))
    _manifest(d["new_code_manifest"])
    require(d["code_manifest_hash"] == digest(d["new_code_manifest"]))
    checkpoint = d["checkpoint"]
    require(type(checkpoint) is dict and set(checkpoint) == {"head_revision", "head_hash", "payload_hash"}
            and type(checkpoint["head_revision"]) is int and checkpoint["head_revision"] >= 1)
    from core.execution.image_runtime_continuation import _hash

    require(_hash(checkpoint["head_hash"]) and _hash(checkpoint["payload_hash"]))
    proof = d["source_collector"]
    require(type(proof) is dict and set(proof) == {"mode", "evidence"} and proof["mode"] == "raw_present")
    files, inspection = proof["evidence"]["files"], proof["evidence"]["inspection"]
    require(type(files) is list and 3 <= len(files) <= 32)
    require(all(type(f) is dict and set(f) == {"name", "size", "sha256"} and type(f["size"]) is int
                and 0 <= f["size"] <= 8 * 1048576 and _hash(f["sha256"]) for f in files))
    names = [f["name"] for f in files]
    require(names == sorted(set(names)) and all(sum(n.endswith(suffix) for n in names) == 1
                                              for suffix in (".record.json", ".commit.json", ".ready.json")))
    op = payload["media"][base.OPERATION + ":" + d["source_operation_id"]]
    require(inspection["operation_id"] == op["id"] and inspection["status"] == "candidate_found"
            and type(inspection["candidate_count"]) is int and inspection["candidate_count"] == 1
            and inspection["binding_verified"] is True and inspection["notification_present"] is True
            and inspection["body_sha256"] == op["receipt"]["raw_body_sha256"]
            and inspection["response_received_at"] == op["raw"]["received_at"]
            and timestamp(inspection["durable_confirmation_at"]) <= timestamp(op["descriptor"]["permit"]["response_deadline_at"]))
    return d


def validate_record(payload, record):
    require(record["kind"] in KINDS and set(record) == COMMON | FIELDS[record["kind"]]
            and type(record["schema_version"]) is int and record["schema_version"] == 1)
    values = {k: record[k] for k in FIELDS[record["kind"]]}
    require(record["id"] == digest({"kind": record["kind"], "scope_id": record["scope_id"], **values})[:32])
    if record["kind"] == QUOTE:
        require(record["disclosure_hash"] == digest(record["disclosure"]))
        _disclosure(payload, record)
    else:
        q = payload["media"][QUOTE + ":" + record["quote_id"]]
        require(record["consent"] is True and record["disclosure_hash"] == q["disclosure_hash"]
                and record["scope_id"] == q["scope_id"] and record["task_id"] == q["task_id"])
        require(timestamp(q["created_at"]) <= timestamp(record["created_at"]) < timestamp(q["disclosure"]["effective_policy"]["valid_until"]))


def authority_for(payload, activation_id):
    a = payload["media"][ACTIVATION + ":" + activation_id]
    q = payload["media"][QUOTE + ":" + a["quote_id"]]
    validate_record(payload, q)
    validate_record(payload, a)
    d = q["disclosure"]
    result = {"schema_version": 1, "kind": AUTHORITY_KIND, "activation_id": a["id"], "quote_id": q["id"],
              **{k: copy.deepcopy(d[k]) for k in BINDINGS | set(LIMITS) | {"effective_policy", "response_policy", "code_manifest_hash"}}}
    return validate_authority({**result, "authority_hash": digest(result)})


def candidate_gate(payload, authority, *, own_query=None, require_fee=True):
    """Only exact standard historical unknowns and the exact current GET differ."""
    a = validate_authority(authority)
    op = payload["media"][base.OPERATION + ":" + a["source_operation_id"]]
    parent = a["parent_authority"]
    require(digest(base.original_view(payload)) == a["original_baseline_hash"]
            and not account_halted(payload, parent["account_id"]) and not unresolved_text_claims(payload))
    excluded = set()
    for item in a["retained_unknowns"]:
        key = "operation:" + item["operation_id"]
        historical = payload["media"][key]
        require(historical["kind"] == "operation" and historical["status"] == "unknown"
                and digest(historical) == item["operation_hash"])
        excluded.add(key)
    if not require_fee and op["fee_id"] is None:
        require(op["status"] == "accepted")
        excluded.add(base.OPERATION + ":" + op["id"])
    else:
        require(op["fee_id"] is not None)
    if own_query is not None:
        key = base.QUERY + ":" + own_query
        q = payload["media"][key]
        require(q["operation_id"] == op["id"] and q["observation_activation_id"] == a["activation_id"]
                and q["descriptor"]["method"] == "GET")
        excluded.add(key)
    view = {**payload, "media": {k: v for k, v in payload["media"].items() if k not in excluded}}
    require(not has_pending_fees(view, parent["account_id"]) and not has_pending_unknown(view, parent["account_id"]))
    require(all(r["status"] == "settled" for r in payload["operations"].values() if r["account_id"] == parent["account_id"]))
    require(not any(q["status"] in {"reserved", "submitting", "unknown"}
                    and payload["media"]["scope:" + q["scope_id"]]["account_id"] == parent["account_id"]
                    for kind in ("query", base.QUERY) for q in records(view, kind)))
    return a


def _fresh_quote(control, payload, quote):
    require(type(control) is LoadedImageControl)
    d = _disclosure(payload, quote)
    require(control.control_hash == d["control_hash"] and now() < timestamp(d["effective_policy"]["valid_until"]))
    require(digest(base.original_view(payload)) == d["original_baseline_hash"])
    require(payload["media"]["grant:" + d["parent_authority"]["grant_id"]]["revoked_at"] is None)
    from core.execution.image_continuation_parent_io import server_parent_path

    require(base.sha(base._read(server_parent_path(control), maximum=8 * 1048576)) == d["parent_authority"]["parent_after_sha256"])
    require(digest(_collect_code_manifest(control)) == d["code_manifest_hash"])
    for item in d["retained_unknowns"]:
        historical = payload["media"]["operation:" + item["operation_id"]]
        require(digest(historical) == item["operation_hash"] and digest(base.collector(control, historical)) == digest(item["collector"]))
    op = payload["media"][base.OPERATION + ":" + d["source_operation_id"]]
    require(digest(base.collector(control, op)) == digest(d["source_collector"]))
    return d


def verified_current(control, activation_id):
    require(type(control) is LoadedImageControl)
    _, _, _, actual = control._verified(for_dispatch=False)
    authority = authority_for(actual, activation_id)
    q = actual["media"][QUOTE + ":" + authority["quote_id"]]
    _fresh_quote(control, actual, q)
    return actual, authority


def _put(payload, kind, scope, **values):
    record = base._record(kind, scope, **values)
    payload["media"][kind + ":" + record["id"]] = record
    return record


def quote_observation(control, operation_id, *, valid_until):
    require(type(control) is LoadedImageControl)
    store = ExecutionStore(control.roots["execution"])
    with _lock(store, control.document.admission.task_id), store.transaction() as payload:
        _, _, _, actual = control._verified(for_dispatch=False)
        require(digest(actual) == digest(payload))
        found = [q for q in records(payload, QUOTE) if q["disclosure"]["source_operation_id"] == operation_id]
        if found:
            require(len(found) == 1 and found[0]["disclosure"]["effective_policy"]["valid_until"] == valid_until)
            _fresh_quote(control, payload, found[0])
            return {"quote": copy.deepcopy(found[0]), "replayed": True}
        binding = bindings(payload, operation_id)
        op = payload["media"][base.OPERATION + ":" + operation_id]
        scope = payload["media"]["scope:" + op["scope_id"]]
        require(now() < timestamp(valid_until) <= now() + timedelta(hours=24))
        state = _state(store)
        manifest = _collect_code_manifest(control)
        d = {"schema_version": 1, "kind": "image_comparison_observation_disclosure_v1", **binding, **LIMITS,
             "source_collector": base.collector(control, op),
             "effective_policy": _effective(binding["parent_authority"]["policy"], valid_until),
             "response_policy": binding["parent_authority"]["response_policy"], "new_code_manifest": manifest,
             "code_manifest_hash": digest(manifest), "checkpoint": _checkpoint(state, payload)}
        q = _put(payload, QUOTE, scope, disclosure=d, disclosure_hash=digest(d))
        _fresh_quote(control, actual, q)
        # An ephemeral authority checks the same global gate, without claiming
        # activation. It cannot dispatch and is never saved or exposed.
        a = {"schema_version": 1, "kind": AUTHORITY_KIND, "activation_id": "0" * 32, "quote_id": q["id"],
             **{k: d[k] for k in BINDINGS | set(LIMITS) | {"effective_policy", "response_policy", "code_manifest_hash"}}}
        candidate_gate(actual, {**a, "authority_hash": digest(a)}, require_fee=False)
        return {"quote": q, "replayed": False}


def activate_observation(control, quote_id, *, disclosure_hash, consent):
    require(type(control) is LoadedImageControl and consent is True)
    store = ExecutionStore(control.roots["execution"])
    with _lock(store, control.document.admission.task_id), store.transaction() as payload:
        _, _, _, actual = control._verified(for_dispatch=False)
        require(digest(actual) == digest(payload))
        q = payload["media"][QUOTE + ":" + quote_id]
        require(disclosure_hash == q["disclosure_hash"])
        _fresh_quote(control, actual, q)
        found = [a for a in records(payload, ACTIVATION) if a["quote_id"] == quote_id]
        if found:
            return {"activation": copy.deepcopy(found[0]), "replayed": True}
        state = _state(store)
        require(state["head_revision"] == q["disclosure"]["checkpoint"]["head_revision"] + 1
                and set(state["frames"][-1]["delta"].get("media", {})) == {QUOTE + ":" + quote_id})
        scope = payload["media"]["scope:" + q["scope_id"]]
        a = _put(payload, ACTIVATION, scope, quote_id=quote_id, disclosure_hash=disclosure_hash, consent=True)
        candidate_gate(payload, authority_for(payload, a["id"]), require_fee=False)
        return {"activation": a, "replayed": False}


def validate_action_birth(previous, record):
    a = authority_for(previous, record["observation_activation_id"])
    require(record["scope_id"] == a["scope_id"] and record["task_id"] == a["task_id"]
            and record["operation_id"] == a["source_operation_id"])
    q = previous["media"][QUOTE + ":" + a["quote_id"]]
    require(timestamp(previous["media"][ACTIVATION + ":" + a["activation_id"]]["created_at"])
            <= timestamp(record["created_at"]) < timestamp(a["effective_policy"]["valid_until"]))
    require(digest(bindings(previous, a["source_operation_id"])) == digest({k: q["disclosure"][k] for k in BINDINGS}))
    candidate_gate(previous, a, require_fee=record["kind"] != base.FEE)
    if record["kind"] == base.QUERY:
        queries = [r for r in records(previous, base.QUERY) if r["operation_id"] == record["operation_id"]]
        require(not any("observation_activation_id" in r for r in queries)
                and len(queries) < a["effective_policy"]["max_queries"])
        require(record["status"] == "submitting" and record["capture_started_at"] == record["created_at"])


def validate_action_binding(payload, record):
    a = authority_for(payload, record["observation_activation_id"])
    require(record["operation_id"] == a["source_operation_id"] and record["scope_id"] == a["scope_id"]
            and record["task_id"] == a["task_id"])
    if record["kind"] == base.ARTIFACT:
        query = payload["media"][base.QUERY + ":" + record["query_id"]]
        require(query.get("observation_activation_id") == a["activation_id"])


def validate_query_record(payload, record):
    from core.execution.image_comparison_observation_policy import KIND

    a = authority_for(payload, record["observation_activation_id"])
    require(record["operation_id"] == a["source_operation_id"] and record["scope_id"] == a["scope_id"])
    op = payload["media"][base.OPERATION + ":" + record["operation_id"]]
    request = FrozenImageRequest.from_frozen(record["descriptor"])
    data = request.frozen()
    require(data["kind"] == KIND and data["method"] == "GET" and digest(data["observation_authority"]) == digest(a)
            and record["activation_id"] == op["activation_id"] and data["permit"]["operation_id"] == record["id"]
            and record["task_id"] == a["task_id"] and type(record["query_index"]) is int
            and record["query_index"] == data["query_index"])
    require(type(record["idempotency_key"]) is str and base.re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", record["idempotency_key"]))
    require(timestamp(payload["media"][ACTIVATION + ":" + a["activation_id"]]["created_at"])
            <= timestamp(data["permit"]["issued_at"]) <= timestamp(record["created_at"])
            <= timestamp(record["capture_started_at"]) <= timestamp(data["permit"]["deadline_at"]))
    require(record["status"] in {"submitting", "unknown", "pending", "completed", "failed"})
    if record["raw"] is None:
        require(record["receipt"] is None and record["status"] in {"submitting", "unknown"})
    else:
        receipt = decode_query(request, record["raw"])
        require(digest(receipt) == digest(record["receipt"])
                and record["status"] == ("unknown" if receipt["classification"] == "unconfirmed" else receipt["classification"]))


def validate_transition(payload, previous=None):
    if previous is None:
        return
    old = previous.get("media", {})
    new = [r for k, r in payload["media"].items() if k not in old and r["kind"] in KINDS]
    for r in new:
        require(len(new) == 1)
        without = {**payload, "media": {k: v for k, v in payload["media"].items() if k != r["kind"] + ":" + r["id"]}}
        require(digest(without) == digest(previous))
        if r["kind"] == QUOTE:
            d = r["disclosure"]
            require(d["checkpoint"]["payload_hash"] == digest(previous))
            require(not any(q["disclosure"]["source_operation_id"] == d["source_operation_id"] for q in records(previous, QUOTE)))
        else:
            require(QUOTE + ":" + r["quote_id"] in old)
            require(not [a for a in records(previous, ACTIVATION) if a["quote_id"] == r["quote_id"]])
    for key, before in old.items():
        if before["kind"] in KINDS:
            require(digest(before) == digest(payload["media"][key]))


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


def status_summary(control, activation_id):
    """Verified safe scalars. A local download without artifact is not inferred."""
    require(type(control) is LoadedImageControl)
    valid = False
    try:
        payload, a = verified_current(control, activation_id)
        valid = True
    except Exception:
        payload = ExecutionStore(control.roots["execution"]).read_only()
        a = authority_for(payload, activation_id)
    op = payload["media"][base.OPERATION + ":" + a["source_operation_id"]]
    queries = [q for q in records(payload, base.QUERY) if q["operation_id"] == op["id"]]
    own = [q for q in queries if "observation_activation_id" in q]
    fee = payload["media"][base.FEE + ":" + op["fee_id"]] if op["fee_id"] else None
    available = False
    if valid and not own and op["artifact_id"] is None and len(queries) < a["effective_policy"]["max_queries"]:
        try:
            candidate_gate(payload, a)
            available = now() + timedelta(seconds=600) < timestamp(a["effective_policy"]["valid_until"])
        except Exception:
            pass
    return {
        "runtime_mode": "comparison_observation_v1", "operation_id": op["id"], "activation_id": activation_id,
        "current_authority_valid": valid, "source_authority_expired": now() >= timestamp(a["parent_authority"]["policy"]["valid_until"]),
        "valid_until": a["effective_policy"]["valid_until"], "dispatch_available": False, "query_available": available,
        "post_cap": 0, "additional_risk_cny": "0.000000", "additional_paid_calls": 0, "B_authorized": False,
        "automatic_followup_calls": 0, "retry_authorized": False, "accepted": op["status"] == "accepted",
        "completed": any(q["status"] == "completed" for q in queries), "received": True if op["artifact_id"] else None,
        "received_basis": "native_artifact_bytes" if op["artifact_id"] else "not_recorded",
        "artifact": "recorded" if op["artifact_id"] else "pending", "query_count_all_versions": len(queries),
        "observation_query_intents": len(own), "max_new_queries": 1, "cumulative_query_limit": a["effective_policy"]["max_queries"],
        "fee_observation": "observed" if fee else "pending", "platform_observed_cny": fee["candidate"]["platform_fee_cny"] if fee else None,
        "overrun": fee["candidate"]["overrun"] if fee else None, "final_bill_confirmed": False,
        "old_unknowns_retained": len(a["retained_unknowns"]), "new_unknown_queries": sum(q["status"] == "unknown" for q in own),
        "request_id_status": op["receipt"]["request_id_status"], "quality": "not_reviewed", "human_acceptance": "pending",
        "formal_delivered": len(records(payload, "delivery")), "formal_required": 4, "formal_delivery_authorized": False,
        "causal_attribution": "undetermined",
    }
