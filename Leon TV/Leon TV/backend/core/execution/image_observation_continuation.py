"""Explicit GET-only observation of one accepted, immutable image POST.

This approval can outlive the POST's code/lease without changing that POST or
granting another submission. Historical reconstruction is pure; every live
decision independently verifies the actual original control and parent proof.
"""
import copy
from datetime import timedelta

from core.creator.store import CreatorError, digest, utc_now
from core.execution.image_code_continuation import _collect_code_manifest, _lock, _manifest, _state
from core.execution.image_continuation_funding import _collect_funding, frozen_funding
from core.execution.image_continuation_proposal import _unknown
from core.execution.image_control import LoadedImageControl
from core.execution.image_policy import (
    FrozenImageRequest,
    ImageToolPolicy,
    MediaBudgetAnchor,
    _model,
    timestamp,
)
from core.execution.image_receipts import decode_submit
from core.execution.image_runtime_continuation import _hash, _id, _same
from core.execution.media_ledger import records
from core.execution.policy import now
from core.execution.store import ExecutionStore
from core.planner.review import json_copy

QUOTE = "image_observation_quote"
ACTIVATION = "image_observation_activation"
KINDS = {QUOTE, ACTIVATION}
COMMON = {"schema_version", "kind", "id", "scope_id", "task_id", "created_at"}
RECORD_FIELDS = {QUOTE: {"disclosure", "disclosure_hash"}, ACTIVATION: {
    "source_operation_id", "quote_id", "disclosure_hash", "previous_activation_id", "activation_sequence", "consent"}}
AUTHORITY_KIND = "image_observation_authority_v1"
DISCLOSURE_KIND = "image_observation_disclosure_v1"
BINDING_FIELDS = {"control_hash", "scope_id", "scope_hash", "task_id", "grant_id", "grant_hash",
    "source_operation_id", "source_descriptor_hash", "source_raw_hash", "source_receipt_hash", "source_capture_started_at",
    "accepted_task_id", "parent_authority", "original_budget_anchor", "intent_id", "intent_hash", "validity_activation_id",
    "validity_activation_hash", "parent_receipt_hash", "funding"}
DISCLOSURE_FIELDS = {"schema_version", "kind", *BINDING_FIELDS, "effective_policy", "new_code_manifest", "code_manifest_hash",
    "checkpoint", "previous_activation_id", "activation_sequence", "additional_risk_cny", "additional_paid_calls",
    "allowed_method", "allow_source_pending_fee", "required_contract_version"}
AUTHORITY_FIELDS = {"schema_version", "kind", *BINDING_FIELDS, "activation_id", "quote_id", "revision_sequence",
    "effective_policy", "code_manifest_hash", "additional_risk_cny", "additional_paid_calls", "allowed_method",
    "allow_source_pending_fee", "authority_hash"}
QUERY_KIND = "controlled_image_observation_http_v1"


def unavailable():
    return CreatorError("IMAGE_OBSERVATION_UNAVAILABLE", "Explicit accepted-task observation is unavailable", 409)


def require(value):
    if not value:
        raise unavailable()


def _zero(value):
    require(value["additional_risk_cny"] == "0.000000" and type(value["additional_paid_calls"]) is int
        and value["additional_paid_calls"] == 0 and value["allowed_method"] == "GET"
        and value["allow_source_pending_fee"] is True)


def _effective(original, valid_until):
    raw = {key: value for key, value in original.items() if key != "policy_hash"}
    raw["valid_until"] = valid_until
    return ImageToolPolicy.model_validate(raw).frozen()


def _source(payload, operation_id):
    from core.execution.image_runtime_continuation import authority_for

    require(_id(operation_id))
    op = payload["media"]["operation:" + operation_id]
    require(op["kind"] == "operation" and op["status"] == "accepted" and op["capture_started_at"] is not None
        and op["raw"] is not None and op["receipt"] is not None)
    data = FrozenImageRequest.from_frozen(op["descriptor"]).frozen()
    require(data["kind"] == "controlled_image_http_v2" and data["method"] == "POST")
    parent = authority_for(payload, data["permit"]["runtime_activation_id"])
    require(_same(parent, data["continuation_authority"]))
    receipt = decode_submit(FrozenImageRequest.from_frozen(data), op["raw"])
    require(_same(receipt, op["receipt"]) and receipt["classification"] == "accepted"
        and type(receipt["accepted_task_id"]) is str and bool(receipt["accepted_task_id"]))
    scope = payload["media"]["scope:" + op["scope_id"]]
    grant = payload["media"]["grant:" + op["grant_id"]]
    require(op["task_id"] == scope["task_id"] == parent["task_id"] == data["permit"]["task_id"])
    require(scope["id"] == parent["scope_id"] and digest(scope) == parent["scope_hash"])
    require(op["grant_id"] == parent["grant_id"] == data["permit"]["grant_id"]
        and grant["scope_id"] == scope["id"] and op["quote_id"] == grant["quote_id"])
    require(digest({**grant, "revoked_at": None}) == parent["grant_hash"])
    require(data["permit"]["operation_id"] == op["id"] and data["permit"]["scope_submission_index"] == op["submission_index"])
    timestamp(op["capture_started_at"])
    return scope, grant, op, parent


def _bindings(payload, operation_id):
    scope, grant, op, parent = _source(payload, operation_id)
    fact = frozen_funding(payload, parent["validity_activation_id"])
    require(fact["intent_id"] == parent["intent_id"] and fact["intent_hash"] == parent["intent_hash"]
        and _same(fact["allocations"], parent["allocations"]))
    value = {"control_hash": parent["control_hash"], "scope_id": scope["id"], "scope_hash": digest(scope),
        "task_id": scope["task_id"], "grant_id": grant["id"], "grant_hash": parent["grant_hash"],
        "source_operation_id": op["id"], "source_descriptor_hash": op["descriptor"]["descriptor_hash"],
        "source_raw_hash": digest(op["raw"]), "source_receipt_hash": op["receipt"]["receipt_hash"],
        "source_capture_started_at": op["capture_started_at"], "accepted_task_id": op["receipt"]["accepted_task_id"],
        "parent_authority": copy.deepcopy(parent), "original_budget_anchor": copy.deepcopy(scope["anchor"]),
        "intent_id": parent["intent_id"], "intent_hash": parent["intent_hash"],
        "validity_activation_id": parent["validity_activation_id"], "validity_activation_hash": parent["validity_activation_hash"],
        "parent_receipt_hash": parent["parent_receipt_hash"], "funding": fact}
    return value


def latest_activation_id(payload, operation_id):
    found = [a for a in records(payload, ACTIVATION) if a["source_operation_id"] == operation_id]
    found.sort(key=lambda a: a["activation_sequence"])
    return found[-1]["id"] if found else None


def _record(kind, scope, **values):
    identifier = digest({"kind": kind, "scope_id": scope["id"], **values})[:32]
    return {"schema_version": 1, "kind": kind, "id": identifier, "scope_id": scope["id"],
        "task_id": scope["task_id"], "created_at": utc_now(), **values}


def validate_authority(value):
    """Strict pure consistency, never an authority or current-time cache."""
    try:
        from core.execution.image_runtime_continuation import validate_authority as parent_authority

        value = json_copy(value, limit=8 * 1048576)
        require(type(value) is dict and set(value) == AUTHORITY_FIELDS and type(value["schema_version"]) is int
            and value["schema_version"] == 1 and value["kind"] == AUTHORITY_KIND)
        require(value["authority_hash"] == digest({k: v for k, v in value.items() if k != "authority_hash"}))
        _zero(value)
        for name in ("activation_id", "quote_id", "scope_id", "task_id", "grant_id", "source_operation_id", "intent_id", "validity_activation_id"):
            require(_id(value[name]))
        for name in ("control_hash", "scope_hash", "grant_hash", "source_descriptor_hash", "source_raw_hash", "source_receipt_hash",
                     "intent_hash", "validity_activation_hash", "parent_receipt_hash", "code_manifest_hash"):
            require(_hash(value[name]))
        require(type(value["revision_sequence"]) is int and value["revision_sequence"] >= 1)
        require(type(value["accepted_task_id"]) is str and 1 <= len(value["accepted_task_id"]) <= 128
            and all(c.isascii() and (c.isalnum() or c in "_-") for c in value["accepted_task_id"]))
        timestamp(value["source_capture_started_at"])
        parent = parent_authority(value["parent_authority"])
        require(all(_same(value[key], parent[key]) for key in ("control_hash", "scope_id", "scope_hash", "task_id", "grant_id", "grant_hash",
            "intent_id", "intent_hash", "validity_activation_id", "validity_activation_hash", "parent_receipt_hash", "original_budget_anchor")))
        require(value["source_operation_id"] != parent["unknown_disposition"]["operation_id"])
        anchor = _model(value["original_budget_anchor"], MediaBudgetAnchor)
        require(_same(anchor, parent["original_budget_anchor"]))
        effective = _model(value["effective_policy"], ImageToolPolicy)
        require(_same(effective, _effective(parent["effective_policy"], effective["valid_until"])))
        require(effective["request_deadline_seconds"] == 120)
        fact = value["funding"]
        require(type(fact) is dict and fact["funding_hash"] == digest({k: v for k, v in fact.items() if k != "funding_hash"})
            and fact["control_hash"] == value["control_hash"] and fact["scope_id"] == value["scope_id"]
            and fact["intent_id"] == value["intent_id"] and fact["intent_hash"] == value["intent_hash"]
            and fact["validity_activation_id"] == value["validity_activation_id"] and fact["validity_activation_hash"] == value["validity_activation_hash"]
            and fact["parent_receipt_hash"] == value["parent_receipt_hash"] and _same(fact["allocations"], parent["allocations"]))
        return value
    except Exception:
        raise unavailable() from None


def _disclosure(payload, quote):
    d = json_copy(quote["disclosure"], limit=16 * 1048576)
    require(type(d) is dict and set(d) == DISCLOSURE_FIELDS and type(d["schema_version"]) is int
        and d["schema_version"] == 1 and d["kind"] == DISCLOSURE_KIND
        and d["required_contract_version"] == "accepted_image_observation_v1")
    _zero(d)
    binding = _bindings(payload, d["source_operation_id"])
    require(all(_same(d[k], v) for k, v in binding.items()))
    require(d["scope_id"] == quote["scope_id"] and d["task_id"] == quote["task_id"])
    effective = _effective(d["parent_authority"]["effective_policy"], d["effective_policy"]["valid_until"])
    require(_same(effective, d["effective_policy"]))
    created, until = timestamp(quote["created_at"]), timestamp(effective["valid_until"])
    require(created < until <= created + timedelta(hours=24))
    source = payload["media"]["operation:" + d["source_operation_id"]]
    require(timestamp(source["raw"]["received_at"]) <= created
        and timestamp(d["source_capture_started_at"]) <= created)
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
        require(kind in KINDS and set(record) == COMMON | RECORD_FIELDS[kind]
            and type(record["schema_version"]) is int and record["schema_version"] == 1)
        require(all(_id(record[k]) for k in ("id", "scope_id", "task_id")))
        values = {key: record[key] for key in RECORD_FIELDS[kind]}
        require(record["id"] == digest({"kind": kind, "scope_id": record["scope_id"], **values})[:32])
        require(payload["media"]["scope:" + record["scope_id"]]["task_id"] == record["task_id"])
        if kind == QUOTE:
            require(record["disclosure_hash"] == digest(record["disclosure"]))
            _disclosure(payload, record)
        else:
            q = payload["media"][QUOTE + ":" + record["quote_id"]]
            d = q["disclosure"]
            require(record["consent"] is True and record["scope_id"] == q["scope_id"]
                and record["disclosure_hash"] == q["disclosure_hash"] and record["source_operation_id"] == d["source_operation_id"]
                and record["previous_activation_id"] == d["previous_activation_id"]
                and type(record["activation_sequence"]) is int and record["activation_sequence"] == d["activation_sequence"])
            require(timestamp(q["created_at"]) <= timestamp(record["created_at"]) < timestamp(d["effective_policy"]["valid_until"]))
    except Exception:
        raise unavailable() from None


def authority_for(payload, activation_id):
    """Reconstruct a historical approval without claiming it is current."""
    try:
        a = payload["media"][ACTIVATION + ":" + activation_id]
        q = payload["media"][QUOTE + ":" + a["quote_id"]]
        validate_record(payload, q)
        validate_record(payload, a)
        d = q["disclosure"]
        result = {"schema_version": 1, "kind": AUTHORITY_KIND, "activation_id": a["id"], "quote_id": q["id"],
            "revision_sequence": a["activation_sequence"], **{key: copy.deepcopy(d[key]) for key in BINDING_FIELDS},
            **{key: copy.deepcopy(d[key]) for key in ("effective_policy", "code_manifest_hash", "additional_risk_cny", "additional_paid_calls",
                "allowed_method", "allow_source_pending_fee")}}
        result["authority_hash"] = digest(result)
        return validate_authority(result)
    except Exception:
        raise unavailable() from None


def _global_gate(payload, authority, *, exclude_query=None):
    from core.execution.media_ledger import (
        account_halted,
        has_pending_fees,
        has_pending_unknown,
        unresolved_text_claims,
    )

    scope = payload["media"]["scope:" + authority["scope_id"]]
    account = scope["account_id"]
    require(not account_halted(payload, account) and not unresolved_text_claims(payload))
    require(all(op["status"] == "settled" for op in payload["operations"].values() if op["account_id"] == account))
    old_unknown = authority["parent_authority"]["unknown_disposition"]
    unknown = payload["media"]["operation:" + old_unknown["operation_id"]]
    require(digest(unknown) == old_unknown["operation_hash"] and unknown["status"] == "unknown"
        and unknown["raw"] is None and unknown["fee_id"] is None and unknown["delivery_id"] is None)
    require(unknown["scope_id"] == scope["id"] and unknown["task_id"] == scope["task_id"])
    media = dict(payload["media"])
    # Exclusions are typed media keys. Never forward an ID to a helper whose
    # exclusion also applies to another namespace.
    del media["operation:" + unknown["id"]]
    require(authority["allow_source_pending_fee"] is True)
    del media["operation:" + authority["source_operation_id"]]
    if exclude_query is not None:
        del media["query:" + exclude_query]
    view = {**payload, "media": media}
    require(not has_pending_unknown(view, account) and not has_pending_fees(view, account))
    require(not any(q["status"] in {"reserved", "submitting", "unknown"}
        and payload["media"]["scope:" + q["scope_id"]]["account_id"] == account for q in records(view, "query")))


def candidate_gate(payload, candidate, *, exclude_candidate=False):
    """Pure GET candidate gate; each live capture must separately fresh-verify."""
    try:
        require(type(exclude_candidate) is bool and type(candidate) is dict and candidate.get("kind") == "query")
        require(set(candidate) == COMMON | {"descriptor", "operation_id", "query_index", "status", "capture_started_at", "raw", "receipt"}
            and type(candidate["schema_version"]) is int and candidate["schema_version"] == 1
            and type(candidate["query_index"]) is int)
        require(all(_id(candidate[k]) for k in ("id", "scope_id", "task_id", "operation_id")))
        require(candidate["status"] in {"reserved", "submitting"} and candidate["raw"] is None and candidate["receipt"] is None)
        require((candidate["status"] == "reserved") is (candidate["capture_started_at"] is None))
        data = FrozenImageRequest.from_frozen(candidate["descriptor"]).frozen()
        require(data["kind"] == QUERY_KIND and data["method"] == "GET")
        permit = data["permit"]
        require(timestamp(permit["issued_at"]) <= timestamp(candidate["created_at"]) <= timestamp(permit["deadline_at"]))
        if candidate["capture_started_at"] is not None:
            require(timestamp(candidate["created_at"]) <= timestamp(candidate["capture_started_at"]) <= timestamp(permit["deadline_at"]))
        authority = authority_for(payload, permit["observation_activation_id"])
        require(latest_activation_id(payload, authority["source_operation_id"]) == authority["activation_id"])
        require(_same(data["observation_authority"], authority))
        scope, grant, source, _ = _source(payload, authority["source_operation_id"])
        require(grant["revoked_at"] is None and digest(grant) == authority["grant_hash"])
        require(candidate["id"] == permit["operation_id"] and candidate["scope_id"] == scope["id"]
            and candidate["task_id"] == scope["task_id"] and candidate["operation_id"] == source["id"])
        require(_same(data["parent_submit"], source["descriptor"]) and _same(data["submit_raw"], source["raw"]))
        require(candidate["query_index"] == permit["query_index"] == data["query_index"])
        queries = [q for q in records(payload, "query") if q["operation_id"] == source["id"] and q["id"] != candidate["id"]]
        require(candidate["query_index"] == len(queries) + 1 <= authority["effective_policy"]["max_queries"])
        require(all(r["descriptor"]["permit"]["nonce"] != permit["nonce"]
            for kind in ("operation", "query") for r in records(payload, kind) if r["id"] != candidate["id"]))
        key = "query:" + candidate["id"]
        if exclude_candidate:
            persisted = payload["media"].get(key)
            require(persisted is not None and digest(persisted) == digest(candidate)
                and candidate["status"] in {"reserved", "submitting"})
        else:
            require(key not in payload["media"] and candidate["status"] == "reserved")
        _global_gate(payload, authority, exclude_query=candidate["id"] if exclude_candidate else None)
        return copy.deepcopy(authority)
    except Exception:
        raise unavailable() from None


def _without(payload, key):
    return {**payload, "media": {k: v for k, v in payload["media"].items() if k != key}}


def _birth(state, record):
    key = record["kind"] + ":" + record["id"]
    found = [f for f in state["frames"] if key in f["delta"].get("media", {})]
    require(len(found) == 1 and set(found[0]["delta"]) == {"media"} and _same(found[0]["delta"]["media"], {key: record}))
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
                prior = latest_activation_id(previous, d["source_operation_id"])
                require(d["previous_activation_id"] == prior and d["activation_sequence"] == (1 if prior is None else old[ACTIVATION + ":" + prior]["activation_sequence"] + 1))
                require(old["grant:" + d["grant_id"]]["revoked_at"] is None)
                _global_gate(previous, d)
            else:
                q = old[QUOTE + ":" + record["quote_id"]]
                d = q["disclosure"]
                require(d["previous_activation_id"] == latest_activation_id(previous, d["source_operation_id"]))
                require(digest(_without(previous, QUOTE + ":" + q["id"])) == d["checkpoint"]["payload_hash"])
                require(old["grant:" + d["grant_id"]]["revoked_at"] is None)
                _global_gate(previous, d)
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
        for q in records(payload, QUOTE):
            f, d = _birth(state, q), q["disclosure"]
            require(f["revision"] == d["checkpoint"]["head_revision"] + 1 and f["previous_hash"] == d["checkpoint"]["head_hash"])
        for a in records(payload, ACTIVATION):
            f, q = _birth(state, a), _birth(state, payload["media"][QUOTE + ":" + a["quote_id"]])
            require(f["revision"] == q["revision"] + 1 and f["previous_hash"] == q["hash"])
    except Exception:
        raise unavailable() from None


def _fresh_binding(control, actual, ready, operation_id):
    from core.execution.receipts import ReceiptStore

    require(type(control) is LoadedImageControl and control.document.mode == "enabled")
    binding = _bindings(actual, operation_id)
    scope, grant, source, parent = _source(actual, operation_id)
    require(binding["control_hash"] == control.control_hash and grant["revoked_at"] is None)
    matching = control._matching(actual, ready)
    require(len(matching) == 1 and digest(matching[0]) == digest(scope))
    disposition = parent["unknown_disposition"]
    require(_same(_unknown(control, actual["media"]["operation:" + disposition["operation_id"]]), disposition))
    collector = ReceiptStore(control.roots["execution"] / "media-receipts")
    collector.check_private((control.backend_root / "code", control.backend_root.parent / "frontend/public", *control.static_roots))
    original = FrozenImageRequest.from_frozen(source["descriptor"])
    raw, inspection = collector.ready_raw_binding(source["id"], original.binding())
    require(raw is not None and inspection["binding_verified"] is True and inspection["candidate_count"] == 1
        and inspection["notification_present"] is True and _same(raw, source["raw"]))
    fact = _collect_funding(control, actual, parent["validity_activation_id"])
    require(_same(fact, binding["funding"]))
    return binding


def verified_current(control, activation_id):
    """One fresh actual snapshot for this single independent decision only."""
    try:
        require(type(control) is LoadedImageControl)
        _, _, ready, actual = control._verified(for_dispatch=False)
        authority = authority_for(actual, activation_id)
        require(latest_activation_id(actual, authority["source_operation_id"]) == activation_id)
        binding = _fresh_binding(control, actual, ready, authority["source_operation_id"])
        require(all(_same(authority[k], v) for k, v in binding.items()))
        q = actual["media"][QUOTE + ":" + authority["quote_id"]]
        require(_same(_collect_code_manifest(control), q["disclosure"]["new_code_manifest"]))
        require(now() < timestamp(authority["effective_policy"]["valid_until"]))
        return actual, authority
    except Exception:
        raise unavailable() from None


def verify_current(control, payload, activation_id):
    try:
        actual, _ = verified_current(control, activation_id)
        require(_same(actual, payload))
        return True
    except Exception:
        raise unavailable() from None


def quote_observation(control, operation_id, *, valid_until):
    try:
        require(type(control) is LoadedImageControl and _id(operation_id))
        until = timestamp(valid_until)
        require(now() < until <= now() + timedelta(hours=24))
        store = ExecutionStore(control.roots["execution"])
        with _lock(store, control.document.admission.task_id), store.transaction() as payload:
            _, _, ready, actual = control._verified(for_dispatch=False)
            require(_same(actual, payload))
            binding = _fresh_binding(control, actual, ready, operation_id)
            scope = payload["media"]["scope:" + binding["scope_id"]]
            manifest = _collect_code_manifest(control)
            prior = latest_activation_id(payload, operation_id)
            for q in reversed(records(payload, QUOTE)):
                d = q["disclosure"]
                if d["source_operation_id"] == operation_id and _same(d["new_code_manifest"], manifest) and timestamp(d["effective_policy"]["valid_until"]) == until:
                    if prior is not None and payload["media"][ACTIVATION + ":" + prior]["quote_id"] == q["id"]:
                        return {"quote": copy.deepcopy(q), "replayed": True, "automatic_followup_calls": 0}
                    if _state(store)["head_hash"] == _birth(_state(store), q)["hash"]:
                        return {"quote": copy.deepcopy(q), "replayed": True, "automatic_followup_calls": 0}
            _global_gate(payload, binding | {"allow_source_pending_fee": True})
            require(len([q for q in records(payload, "query") if q["operation_id"] == operation_id]) < scope["policy"]["max_queries"])
            state = _state(store)
            d = {"schema_version": 1, "kind": DISCLOSURE_KIND, **binding,
                "effective_policy": _effective(binding["parent_authority"]["effective_policy"], valid_until),
                "new_code_manifest": manifest, "code_manifest_hash": digest(manifest),
                "checkpoint": {"head_revision": state["head_revision"], "head_hash": state["head_hash"], "payload_hash": digest(payload)},
                "previous_activation_id": prior, "activation_sequence": 1 if prior is None else payload["media"][ACTIVATION + ":" + prior]["activation_sequence"] + 1,
                "additional_risk_cny": "0.000000", "additional_paid_calls": 0, "allowed_method": "GET",
                "allow_source_pending_fee": True, "required_contract_version": "accepted_image_observation_v1"}
            q = _record(QUOTE, scope, disclosure=d, disclosure_hash=digest(d))
            payload["media"][QUOTE + ":" + q["id"]] = q
            return {"quote": copy.deepcopy(q), "replayed": False, "automatic_followup_calls": 0}
    except Exception:
        raise unavailable() from None


def activate_observation(control, quote_id, *, disclosure_hash, consent):
    try:
        require(type(control) is LoadedImageControl and _id(quote_id) and _hash(disclosure_hash) and consent is True)
        store = ExecutionStore(control.roots["execution"])
        with _lock(store, control.document.admission.task_id), store.transaction() as payload:
            q = payload["media"][QUOTE + ":" + quote_id]
            require(q["disclosure_hash"] == disclosure_hash)
            d = _disclosure(payload, q)
            _, _, ready, actual = control._verified(for_dispatch=False)
            require(_same(actual, payload))
            _fresh_binding(control, actual, ready, d["source_operation_id"])
            require(now() < timestamp(d["effective_policy"]["valid_until"]) and _same(_collect_code_manifest(control), d["new_code_manifest"]))
            for a in records(payload, ACTIVATION):
                if a["quote_id"] == quote_id:
                    require(latest_activation_id(payload, d["source_operation_id"]) == a["id"])
                    return {"activation": copy.deepcopy(a), "replayed": True, "automatic_followup_calls": 0}
            _global_gate(payload, d)
            require(d["previous_activation_id"] == latest_activation_id(payload, d["source_operation_id"]))
            require(_state(store)["head_hash"] == _birth(_state(store), q)["hash"])
            a = _record(ACTIVATION, payload["media"]["scope:" + q["scope_id"]], source_operation_id=d["source_operation_id"],
                quote_id=quote_id, disclosure_hash=disclosure_hash, previous_activation_id=d["previous_activation_id"],
                activation_sequence=d["activation_sequence"], consent=True)
            payload["media"][ACTIVATION + ":" + a["id"]] = a
            return {"activation": copy.deepcopy(a), "replayed": False, "automatic_followup_calls": 0}
    except Exception:
        raise unavailable() from None
