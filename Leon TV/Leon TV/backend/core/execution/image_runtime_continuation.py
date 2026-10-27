"""Exact runtime/code revisions over one already retained image continuation.

The old scope, grant, D37 consent and funding records stay immutable. Pure
reconstruction is separate from fresh fixed-file/source/code/lease authority.
"""
import copy
import re
from datetime import timedelta

from core.creator.store import CreatorError, digest, utc_now
from core.execution.image_code_continuation import _collect_code_manifest, _lock, _manifest, _state
from core.execution.image_continuation_funding import _collect_funding, frozen_funding
from core.execution.image_continuation_proposal import _unknown
from core.execution.image_control import LoadedImageControl
from core.execution.image_policy import ImageToolPolicy, MediaBudgetAnchor, _model, timestamp
from core.execution.media_ledger import records
from core.execution.policy import now, units
from core.execution.store import ExecutionStore
from core.planner.review import json_copy

QUOTE = "runtime_continuation_quote"
ACTIVATION = "runtime_continuation_activation"
KINDS = {QUOTE, ACTIVATION}
COMMON = {"schema_version", "kind", "id", "scope_id", "task_id", "created_at"}
RECORD_FIELDS = {QUOTE: {"disclosure", "disclosure_hash"}, ACTIVATION: {
    "quote_id", "disclosure_hash", "previous_activation_id", "activation_sequence", "consent"}}
FIELDS = RECORD_FIELDS
DISCLOSURE_KIND = "image_runtime_continuation_disclosure_v1"
AUTHORITY_KIND = "image_continuation_authority_v2"
DISCLOSURE_FIELDS = {"schema_version", "kind", "control_hash", "scope_id", "scope_hash", "grant_id", "grant_hash",
    "intent_id", "intent_hash", "validity_activation_id", "validity_activation_hash", "parent_receipt_hash", "funding",
    "admission", "unknown_disposition", "remaining_jobs", "original_policy", "original_budget_anchor", "effective_policy",
    "new_code_manifest", "code_manifest_hash", "checkpoint", "previous_activation_id", "activation_sequence",
    "additional_risk_cny", "additional_paid_calls", "required_contract_version"}
AUTHORITY_FIELDS = {"schema_version", "kind", "activation_id", "quote_id", "revision_sequence", "scope_id", "task_id",
    "grant_id", "control_hash", "scope_hash", "grant_hash", "intent_id", "intent_hash", "validity_activation_id",
    "validity_activation_hash", "parent_receipt_hash", "original_policy", "original_budget_anchor", "effective_policy",
    "code_manifest_hash", "unknown_disposition", "remaining_jobs", "allocations", "max_cumulative_submissions",
    "additional_risk_cny", "additional_paid_calls", "authority_hash"}
TARGET_FIELDS = {"shot_id", "job_hash", "first_frame_version", "attempt_version", "replacement_of", "new_operation_id", "new_nonce",
    "planned_submission_index", "reference_shot_ids", "references_require_exact_delivered_operation"}


def unavailable():
    return CreatorError("IMAGE_RUNTIME_CONTINUATION_UNAVAILABLE", "Explicit image runtime continuation is unavailable", 409)


def require(value):
    if not value:
        raise unavailable()


def _hash(value):
    return type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _id(value):
    return type(value) is str and re.fullmatch(r"[0-9a-f]{32}", value) is not None


def _same(left, right):
    return digest(left) == digest(right)


def _own(payload, kind, scope_id):
    return [r for r in records(payload, kind) if r["scope_id"] == scope_id]


def latest_activation_id(payload, scope_id):
    found = sorted(_own(payload, ACTIVATION, scope_id), key=lambda r: r["activation_sequence"])
    return found[-1]["id"] if found else None


def _record(kind, scope, **values):
    identifier = digest({"kind": kind, "scope_id": scope["id"], **values})[:32]
    return {"schema_version": 1, "kind": kind, "id": identifier, "scope_id": scope["id"],
        "task_id": scope["task_id"], "created_at": utc_now(), **values}


def _zero(value):
    require(value["additional_risk_cny"] == "0.000000" and type(value["additional_paid_calls"]) is int
        and value["additional_paid_calls"] == 0)


def _effective(original, valid_until):
    raw = {key: value for key, value in original.items() if key != "policy_hash"}
    raw.update(max_submissions=original["max_submissions"] + 1, valid_until=valid_until)
    return ImageToolPolicy.model_validate(raw).frozen()


def validate_authority(value):
    """Pure descriptor consistency only; a sealed dict grants no authority."""
    try:
        value = json_copy(value, limit=8 * 1048576)
        require(type(value) is dict and set(value) == AUTHORITY_FIELDS
            and type(value["schema_version"]) is int and value["schema_version"] == 2 and value["kind"] == AUTHORITY_KIND)
        require(value["authority_hash"] == digest({k: v for k, v in value.items() if k != "authority_hash"}))
        for field in ("activation_id", "quote_id", "scope_id", "task_id", "grant_id", "intent_id", "validity_activation_id"):
            require(_id(value[field]))
        for field in ("control_hash", "scope_hash", "grant_hash", "intent_hash", "validity_activation_hash", "parent_receipt_hash", "code_manifest_hash"):
            require(_hash(value[field]))
        require(type(value["revision_sequence"]) is int and value["revision_sequence"] >= 1)
        _zero(value)
        original = _model(value["original_policy"], ImageToolPolicy)
        anchor = _model(value["original_budget_anchor"], MediaBudgetAnchor)
        effective = _model(value["effective_policy"], ImageToolPolicy)
        require(_same(effective, _effective(original, effective["valid_until"])))
        require(timestamp(effective["valid_until"]) > timestamp(original["valid_until"]))
        require(original["request_deadline_seconds"] == effective["request_deadline_seconds"] == 120)
        require(original["max_submissions"] == anchor["scope_max_calls"]
            and units(original["per_submission_risk_cny"]) * original["max_submissions"] == units(anchor["scope_risk_cny"]))
        require(type(value["max_cumulative_submissions"]) is int
            and value["max_cumulative_submissions"] == effective["max_submissions"])
        allocations = value["allocations"]
        require(type(allocations) is list and len(allocations) == 2)
        for item, role in zip(allocations, ("original", "continuation"), strict=True):
            require(type(item) is dict and set(item) == {"role", "allocation_hash", "begin_hash", "risk_cny", "max_calls"}
                and item["role"] == role and _hash(item["allocation_hash"]) and _hash(item["begin_hash"])
                and type(item["max_calls"]) is int and item["max_calls"] >= 1)
        require(allocations[0]["allocation_hash"] == anchor["parent_allocation_hash"]
            and allocations[0]["begin_hash"] == anchor["parent_allocation_begin_hash"]
            and allocations[0]["risk_cny"] == anchor["scope_risk_cny"] and allocations[0]["max_calls"] == anchor["scope_max_calls"])
        require(allocations[1]["risk_cny"] == "1.000000" and allocations[1]["max_calls"] == 1
            and allocations[1]["allocation_hash"] != allocations[0]["allocation_hash"]
            and allocations[1]["begin_hash"] != allocations[0]["begin_hash"])
        require(sum(item["max_calls"] for item in allocations) == effective["max_submissions"]
            and sum(units(item["risk_cny"]) for item in allocations) == units(effective["per_submission_risk_cny"]) * effective["max_submissions"])
        targets = value["remaining_jobs"]
        require(type(targets) is list and 1 <= len(targets) <= effective["max_submissions"])
        seen = set()
        for target in targets:
            require(type(target) is dict and set(target) == TARGET_FIELDS and target["shot_id"] not in seen
                and _hash(target["job_hash"]) and type(target["first_frame_version"]) is int and target["first_frame_version"] == 1
                and type(target["attempt_version"]) is int and target["attempt_version"] in {1, 2}
                and type(target["planned_submission_index"]) is int and 1 <= target["planned_submission_index"] <= effective["max_submissions"]
                and target["new_operation_id"] is None and target["new_nonce"] is None
                and target["references_require_exact_delivered_operation"] is True and type(target["reference_shot_ids"]) is list)
            require((target["replacement_of"] is None and target["attempt_version"] == 1)
                or (_id(target["replacement_of"]) and target["attempt_version"] == 2))
            seen.add(target["shot_id"])
        indices = [target["planned_submission_index"] for target in targets]
        require(indices == list(range(indices[0], indices[0] + len(indices))) and indices[-1] == effective["max_submissions"])
        unknown = value["unknown_disposition"]
        require(type(unknown) is dict and _id(unknown["operation_id"]) and _hash(unknown["operation_hash"])
            and unknown["status_after_proposal"] == "unknown" and unknown["original_record_must_remain_unchanged"] is True
            and unknown["old_request_retry_authorized"] is False and unknown["zero_http_proven"] is False
            and unknown["zero_fee_proven"] is False and unknown["new_unknown_stops_continuation"] is True)
        require(sum(target["replacement_of"] == unknown["operation_id"] for target in targets) == 1)
        return value
    except Exception:
        raise unavailable() from None


def _disclosure(payload, quote):
    from core.execution import image_validity_continuation as validity

    d = json_copy(quote["disclosure"], limit=16 * 1048576)
    require(type(d) is dict and set(d) == DISCLOSURE_FIELDS and type(d["schema_version"]) is int
        and d["schema_version"] == 1 and d["kind"] == DISCLOSURE_KIND and d["required_contract_version"] == "image_continuation_v2")
    _zero(d)
    scope = payload["media"]["scope:" + quote["scope_id"]]
    a = payload["media"][validity.ACTIVATION + ":" + d["validity_activation_id"]]
    old_quote = payload["media"][validity.QUOTE + ":" + a["quote_id"]]
    intent = payload["media"][validity.INTENT + ":" + a["intent_id"]]
    b = old_quote["proposal"]["basis"]
    fact = frozen_funding(payload, a["id"])
    grant = payload["media"]["grant:" + b["grant_id"]]
    require(a["scope_id"] == old_quote["scope_id"] == intent["scope_id"] == grant["scope_id"] == scope["id"]
        and a["task_id"] == old_quote["task_id"] == intent["task_id"] == grant["task_id"] == scope["task_id"])
    bindings = {"control_hash": b["control_hash"], "scope_id": scope["id"], "scope_hash": digest(scope),
        "grant_id": grant["id"], "grant_hash": digest({**grant, "revoked_at": None}), "intent_id": intent["id"], "intent_hash": digest(intent),
        "validity_activation_id": a["id"], "validity_activation_hash": digest(a), "parent_receipt_hash": a["parent_receipt"]["receipt_hash"],
        "funding": fact, "admission": b["admission"], "unknown_disposition": b["unknown_disposition"], "remaining_jobs": b["remaining_jobs"],
        "original_policy": scope["policy"], "original_budget_anchor": scope["anchor"]}
    require(all(_same(d[key], value) for key, value in bindings.items()))
    require(_same(d["effective_policy"], _effective(scope["policy"], d["effective_policy"]["valid_until"])))
    until, created = timestamp(d["effective_policy"]["valid_until"]), timestamp(quote["created_at"])
    require(created < until <= created + timedelta(hours=24) and until > timestamp(scope["policy"]["valid_until"]))
    _manifest(d["new_code_manifest"])
    require(d["code_manifest_hash"] == digest(d["new_code_manifest"]))
    checkpoint = d["checkpoint"]
    require(type(checkpoint) is dict and set(checkpoint) == {"head_revision", "head_hash", "payload_hash"}
        and type(checkpoint["head_revision"]) is int and checkpoint["head_revision"] >= 1
        and _hash(checkpoint["head_hash"]) and _hash(checkpoint["payload_hash"]))
    require(type(d["activation_sequence"]) is int and d["activation_sequence"] >= 1)
    require(d["previous_activation_id"] is None or _id(d["previous_activation_id"]))
    return d


def validate_record(payload, record):
    try:
        kind = record["kind"]
        require(kind in KINDS and set(record) == COMMON | RECORD_FIELDS[kind] and type(record["schema_version"]) is int
            and record["schema_version"] == 1 and all(_id(record[k]) for k in ("id", "task_id", "scope_id")))
        values = {key: record[key] for key in RECORD_FIELDS[kind]}
        require(record["id"] == digest({"kind": kind, "scope_id": record["scope_id"], **values})[:32])
        scope = payload["media"]["scope:" + record["scope_id"]]
        require(record["task_id"] == scope["task_id"])
        if kind == QUOTE:
            require(record["disclosure_hash"] == digest(record["disclosure"]))
            _disclosure(payload, record)
        else:
            quote = payload["media"][QUOTE + ":" + record["quote_id"]]
            require(quote["scope_id"] == record["scope_id"] and quote["disclosure_hash"] == record["disclosure_hash"] and record["consent"] is True)
            d = quote["disclosure"]
            require(record["previous_activation_id"] == d["previous_activation_id"]
                and type(record["activation_sequence"]) is int and record["activation_sequence"] == d["activation_sequence"])
            require(timestamp(quote["created_at"]) <= timestamp(record["created_at"]) < timestamp(d["effective_policy"]["valid_until"]))
    except Exception:
        raise unavailable() from None


def authority_for(payload, activation_id):
    """Reconstruct any historical approved revision; no freshness is asserted."""
    try:
        a = payload["media"][ACTIVATION + ":" + activation_id]
        q = payload["media"][QUOTE + ":" + a["quote_id"]]
        validate_record(payload, q)
        validate_record(payload, a)
        d = q["disclosure"]
        result = {"schema_version": 2, "kind": AUTHORITY_KIND, "activation_id": a["id"], "quote_id": q["id"],
            "revision_sequence": a["activation_sequence"], "scope_id": a["scope_id"], "task_id": a["task_id"],
            **{key: copy.deepcopy(d[key]) for key in ("grant_id", "control_hash", "scope_hash", "grant_hash", "intent_id", "intent_hash",
                "validity_activation_id", "validity_activation_hash", "parent_receipt_hash", "original_policy", "original_budget_anchor",
                "effective_policy", "code_manifest_hash", "unknown_disposition", "remaining_jobs")},
            "allocations": copy.deepcopy(d["funding"]["allocations"]), "max_cumulative_submissions": d["effective_policy"]["max_submissions"],
            "additional_risk_cny": "0.000000", "additional_paid_calls": 0}
        result["authority_hash"] = digest(result)
        return validate_authority(result)
    except Exception:
        raise unavailable() from None


def _without(payload, key):
    return {**payload, "media": {k: v for k, v in payload["media"].items() if k != key}}


def _birth(state, record):
    key = record["kind"] + ":" + record["id"]
    found = [frame for frame in state["frames"] if key in frame["delta"].get("media", {})]
    require(len(found) == 1 and set(found[0]["delta"]) == {"media"}
        and _same(found[0]["delta"]["media"], {key: record}))
    return found[0]


def validate_transition(payload, previous=None):
    try:
        if previous is None:
            return
        old = previous.get("media", {})
        added = [r for k, r in payload.get("media", {}).items() if k not in old and r["kind"] in KINDS]
        for record in added:
            key = record["kind"] + ":" + record["id"]
            require(len(added) == 1 and _same(_without(payload, key), previous))
            if record["kind"] == QUOTE:
                d = record["disclosure"]
                require(d["checkpoint"]["payload_hash"] == digest(previous))
                prior_id = latest_activation_id(previous, record["scope_id"])
                prior = None if prior_id is None else old[ACTIVATION + ":" + prior_id]
                require(d["previous_activation_id"] == prior_id and d["activation_sequence"] == (1 if prior is None else prior["activation_sequence"] + 1))
                require(old["grant:" + d["grant_id"]]["revoked_at"] is None)
            else:
                q = old[QUOTE + ":" + record["quote_id"]]
                d = q["disclosure"]
                require(d["previous_activation_id"] == latest_activation_id(previous, record["scope_id"]))
                require(digest(_without(previous, QUOTE + ":" + q["id"])) == d["checkpoint"]["payload_hash"])
                require(old["grant:" + d["grant_id"]]["revoked_at"] is None)
    except Exception:
        raise unavailable() from None


def validate_history(state, payload):
    try:
        from core.execution.store import EMPTY
        prefix = copy.deepcopy(EMPTY)
        for frame in state["frames"]:
            for record in frame["delta"].get("media", {}).values():
                if record["kind"] == QUOTE:
                    d = _disclosure(prefix, record)
                    require(d["checkpoint"] == {"head_revision": frame["revision"] - 1,
                        "head_hash": frame["previous_hash"], "payload_hash": digest(prefix)})
            for namespace, values in frame["delta"].items():
                prefix.setdefault(namespace, {}).update(copy.deepcopy(values))
        require(_same(prefix, payload))
        for quote in records(payload, QUOTE):
            frame = _birth(state, quote)
            d = quote["disclosure"]
            require(frame["revision"] == d["checkpoint"]["head_revision"] + 1 and frame["previous_hash"] == d["checkpoint"]["head_hash"])
        for activation in records(payload, ACTIVATION):
            frame = _birth(state, activation)
            prior = _birth(state, payload["media"][QUOTE + ":" + activation["quote_id"]])
            require(frame["revision"] == prior["revision"] + 1 and frame["previous_hash"] == prior["hash"])
    except Exception:
        raise unavailable() from None


def _current_from_verified(control, actual, ready, validity_activation_id):
    """Collect facts from this decision's already verified original snapshot."""
    from core.execution import image_validity_continuation as validity

    candidates = control._matching(actual, ready)
    require(len(candidates) == 1)
    scope = candidates[0]
    a = actual["media"][validity.ACTIVATION + ":" + validity_activation_id]
    require(a["scope_id"] == scope["id"])
    q = actual["media"][validity.QUOTE + ":" + a["quote_id"]]
    b = q["proposal"]["basis"]
    grant = actual["media"]["grant:" + b["grant_id"]]
    require(grant["revoked_at"] is None and _same(_unknown(control, actual["media"]["operation:" + b["unknown_disposition"]["operation_id"]]), b["unknown_disposition"]))
    funding = _collect_funding(control, actual, a["id"])
    return scope, grant, q, a, funding


def _current(control, payload, validity_activation_id):
    require(type(control) is LoadedImageControl and control.document.mode == "enabled")
    _, _, ready, actual = control._verified(for_dispatch=False)
    require(_same(actual, payload))
    return _current_from_verified(control, actual, ready, validity_activation_id)


def verified_current(control, activation_id):
    """Return a new actual snapshot and its current authority for one decision."""
    try:
        require(type(control) is LoadedImageControl and control.document.mode == "enabled")
        _, _, ready, payload = control._verified(for_dispatch=False)
        authority = authority_for(payload, activation_id)
        require(latest_activation_id(payload, authority["scope_id"]) == activation_id)
        _, _, _, _, funding = _current_from_verified(control, payload, ready, authority["validity_activation_id"])
        q = payload["media"][QUOTE + ":" + authority["quote_id"]]
        require(_same(funding, q["disclosure"]["funding"]))
        require(_same(_collect_code_manifest(control), q["disclosure"]["new_code_manifest"]))
        require(now() < timestamp(authority["effective_policy"]["valid_until"]))
        return payload, authority
    except Exception:
        raise unavailable() from None


def verify_current(control, payload, activation_id):
    """Fresh checks; a caller's snapshot never stands in for the actual store."""
    try:
        actual, _ = verified_current(control, activation_id)
        require(_same(actual, payload))
        return True
    except Exception:
        raise unavailable() from None


def quote_runtime_continuation(control, *, valid_until):
    try:
        from core.execution import image_validity_continuation as validity

        require(type(control) is LoadedImageControl)
        until = timestamp(valid_until)
        require(now() < until <= now() + timedelta(hours=24))
        store = ExecutionStore(control.roots["execution"])
        with _lock(store, control.document.admission.task_id), store.transaction() as payload:
            activations = records(payload, validity.ACTIVATION)
            own = [a for a in activations if a["task_id"] == control.document.admission.task_id]
            require(len(own) == 1)
            scope, grant, old_quote, a, funding = _current(control, payload, own[0]["id"])
            require(until > timestamp(scope["policy"]["valid_until"]))
            manifest = _collect_code_manifest(control)
            prior_id = latest_activation_id(payload, scope["id"])
            for q in reversed(_own(payload, QUOTE, scope["id"])):
                d = q["disclosure"]
                if _same(d["new_code_manifest"], manifest) and timestamp(d["effective_policy"]["valid_until"]) == until:
                    if prior_id is not None and payload["media"][ACTIVATION + ":" + prior_id]["quote_id"] == q["id"]:
                        return {"quote": copy.deepcopy(q), "replayed": True, "automatic_followup_calls": 0}
                    frame = _birth(_state(store), q)
                    if _state(store)["head_hash"] == frame["hash"]:
                        return {"quote": copy.deepcopy(q), "replayed": True, "automatic_followup_calls": 0}
            b = old_quote["proposal"]["basis"]
            state = _state(store)
            d = {"schema_version": 1, "kind": DISCLOSURE_KIND, "control_hash": control.control_hash,
                "scope_id": scope["id"], "scope_hash": digest(scope), "grant_id": grant["id"], "grant_hash": digest(grant),
                "intent_id": a["intent_id"], "intent_hash": funding["intent_hash"], "validity_activation_id": a["id"],
                "validity_activation_hash": digest(a), "parent_receipt_hash": a["parent_receipt"]["receipt_hash"], "funding": funding,
                "admission": copy.deepcopy(b["admission"]), "unknown_disposition": copy.deepcopy(b["unknown_disposition"]),
                "remaining_jobs": copy.deepcopy(b["remaining_jobs"]), "original_policy": copy.deepcopy(scope["policy"]),
                "original_budget_anchor": copy.deepcopy(scope["anchor"]), "effective_policy": _effective(scope["policy"], valid_until),
                "new_code_manifest": manifest, "code_manifest_hash": digest(manifest),
                "checkpoint": {"head_revision": state["head_revision"], "head_hash": state["head_hash"], "payload_hash": digest(payload)},
                "previous_activation_id": prior_id, "activation_sequence": 1 if prior_id is None else payload["media"][ACTIVATION + ":" + prior_id]["activation_sequence"] + 1,
                "additional_risk_cny": "0.000000", "additional_paid_calls": 0, "required_contract_version": "image_continuation_v2"}
            q = _record(QUOTE, scope, disclosure=d, disclosure_hash=digest(d))
            payload["media"][QUOTE + ":" + q["id"]] = q
            return {"quote": copy.deepcopy(q), "replayed": False, "automatic_followup_calls": 0}
    except Exception:
        raise unavailable() from None


def activate_runtime_continuation(control, quote_id, *, disclosure_hash, consent):
    try:
        require(type(control) is LoadedImageControl and _id(quote_id) and _hash(disclosure_hash) and consent is True)
        store = ExecutionStore(control.roots["execution"])
        with _lock(store, control.document.admission.task_id), store.transaction() as payload:
            quote = payload["media"][QUOTE + ":" + quote_id]
            require(quote["disclosure_hash"] == disclosure_hash)
            d = _disclosure(payload, quote)
            _current(control, payload, d["validity_activation_id"])
            require(now() < timestamp(d["effective_policy"]["valid_until"]) and _same(_collect_code_manifest(control), d["new_code_manifest"]))
            for a in _own(payload, ACTIVATION, quote["scope_id"]):
                if a["quote_id"] == quote_id:
                    require(latest_activation_id(payload, quote["scope_id"]) == a["id"])
                    return {"activation": copy.deepcopy(a), "replayed": True, "automatic_followup_calls": 0}
            require(d["previous_activation_id"] == latest_activation_id(payload, quote["scope_id"]))
            require(_state(store)["head_hash"] == _birth(_state(store), quote)["hash"])
            scope = payload["media"]["scope:" + quote["scope_id"]]
            a = _record(ACTIVATION, scope, quote_id=quote_id, disclosure_hash=disclosure_hash,
                previous_activation_id=d["previous_activation_id"], activation_sequence=d["activation_sequence"], consent=True)
            payload["media"][ACTIVATION + ":" + a["id"]] = a
            return {"activation": copy.deepcopy(a), "replayed": False, "automatic_followup_calls": 0}
    except Exception:
        raise unavailable() from None
