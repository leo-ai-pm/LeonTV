"""Original-journal validity consent and retained funding; dispatch stays off."""
import copy
import json
import re
from datetime import timedelta

from core.creator.store import CreatorError, digest, utc_now
from core.execution.image_code_continuation import (
    _active,
    _collect_code_manifest,
    _lock,
    _manifest,
    _state,
)
from core.execution.image_continuation_proposal import (
    BASIS_FIELDS,
    PROPOSAL_FIELDS,
    _operations,
    _targets,
    _unknown,
    build_proposal,
)
from core.execution.image_control import ImageAdmission, LoadedImageControl
from core.execution.image_policy import FrozenImageRequest, timestamp
from core.execution.media_ledger import records
from core.execution.policy import money, now, units
from core.execution.store import MAX_STATE_BYTES, ExecutionStore
from core.planner.review import json_copy

QUOTE = "validity_continuation_quote"
INTENT = "validity_continuation_intent"
ACTIVATION = "validity_continuation_activation"
KINDS = {QUOTE, INTENT, ACTIVATION}
COMMON = {"schema_version", "kind", "id", "scope_id", "task_id", "created_at"}
FLAGS = {"dispatch_ready": False, "runtime_contract_pending": True}
RECORD_FIELDS = {
    QUOTE: {"proposal", "parent_evidence_ref", *FLAGS},
    INTENT: {"quote_id", "proposal_hash", "consent", "additional_risk_cny", "additional_paid_calls", *FLAGS},
    ACTIVATION: {"quote_id", "intent_id", "proposal_hash", "consent", "parent_receipt", *FLAGS},
}


def unavailable():
    return CreatorError("IMAGE_VALIDITY_CONTINUATION_UNAVAILABLE", "Explicit image validity continuation is unavailable", 409)


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
    return [item for item in records(payload, kind) if item["scope_id"] == scope_id]


def _values(record):
    return {key: record[key] for key in RECORD_FIELDS[record["kind"]]}


def _record(kind, scope, **values):
    values = {**values, **FLAGS}
    record_id = digest({"kind": kind, "scope_id": scope["id"], **values})[:32]
    return {"schema_version": 1, "kind": kind, "id": record_id, "scope_id": scope["id"],
        "task_id": scope["task_id"], "created_at": utc_now(), **values}


def _result(record, replayed):
    name = {QUOTE: "quote", INTENT: "intent", ACTIVATION: "activation"}[record["kind"]]
    return {name: copy.deepcopy(record), "replayed": replayed, "automatic_followup_calls": 0}


def _capacity(store, record, reserve_bytes):
    """Keep room for bounded remaining frames before retaining or writing funds."""
    from core.execution.control import _read
    from core.execution.store import MAX_STATE_BYTES as storage_limit

    require(type(reserve_bytes) is int and reserve_bytes >= 0)
    raw = _read(store.path("state.json"), maximum=storage_limit)
    candidate = {"media": {record["kind"] + ":" + record["id"]: record}}
    encoded = json.dumps(candidate, ensure_ascii=False, allow_nan=False).encode("utf-8")
    require(len(raw) + len(encoded) + reserve_bytes + 65536 <= MAX_STATE_BYTES)


def _admission(payload, scope):
    native = payload["tasks"][scope["task_id"]]
    source, handoff, origin = native["source"], scope["handoff"], scope["manifest_origin"]
    return ImageAdmission.model_validate({"task_id": scope["task_id"], "planner_task_revision": native["planner_task_revision"],
        **{key: source[key] for key in ("snapshot_hash", "source_hash", "brief_id", "brief_revision")},
        "plan_version": handoff["plan"]["version"], "plan_hash": handoff["plan"]["plan_hash"],
        "acceptance_input_hash": handoff["acceptance"]["input_hash"], "acceptance_report_hash": handoff["acceptance"]["report_hash"],
        "handoff_hash": handoff["handoff_hash"], "provenance_hash": handoff["provenance_hash"],
        "asset_operation_id": origin["asset_operation_id"], "asset_manifest_hash": scope["manifest"]["manifest_hash"],
        "asset_proof_hash": origin["asset_proof"]["proof_hash"]}).model_dump()


def _deliveries(payload, operations):
    return [{"operation_id": item["id"], "operation_hash": digest(item), "shot_id": item["shot_id"],
        "fee_id": item["fee_id"], "fee_candidate_hash": digest(payload["media"]["fee:" + item["fee_id"]]["candidate"]),
        "platform_observed_cny": payload["media"]["fee:" + item["fee_id"]]["candidate"]["platform_fee_cny"],
        "delivery_id": item["delivery_id"], "delivery_hash": digest(payload["media"]["delivery:" + item["delivery_id"]]),
        "quality_status": "not_reviewed", "final_bill": False} for item in operations if item["delivery_id"] is not None]


def _checked_unknown(value, operation):
    """Pure historical reconstruction; live collector checks belong to the coordinator."""
    descriptor = FrozenImageRequest.from_frozen(operation["descriptor"])
    expected = {"operation_id": operation["id"], "operation_hash": digest(operation), "descriptor_hash": descriptor.descriptor_hash,
        "permit_hash": operation["descriptor"]["permit"]["permit_hash"], "shot_id": operation["shot_id"],
        "submission_index": operation["submission_index"], "original_deadline_at": operation["descriptor"]["permit"]["deadline_at"],
        "capture_started_at": operation["capture_started_at"], "raw_hash": None, "receipt_hash": None, "fee_id": None,
        "status_after_proposal": "unknown", "original_record_must_remain_unchanged": True,
        "retained_risk_cny": operation["descriptor"]["permit"]["risk_cny"], "consumed_call_slots": 1, "call_slots_released": 0,
        "zero_http_proven": False, "zero_fee_proven": False, "old_request_retry_authorized": False,
        "new_replacement_authorized": False, "new_unknown_stops_continuation": True, "late_evidence_requires_new_review": True}
    require(type(value) is dict and set(value) == set(expected) | {"collector_inspection", "failure_phase"})
    require(all(_same(value[key], item) for key, item in expected.items()))
    inspection = {"operation_id": operation["id"], "status": "missing", "candidate_count": 0, "binding_verified": False,
        "isolated": True, "recovery_performed": False, "notification_present": False, "body_sha256": None,
        "response_received_at": None, "durable_confirmation_at": None,
        "original_deadline_at": descriptor.deadline_at, "observer_until": None}
    require(_same(value["collector_inspection"], inspection))
    phase = value["failure_phase"]
    if phase != {"status": "missing"}:
        from core.execution.media_failure_phase import STAGES
        require(type(phase) is dict and set(phase) == {"status", "stage", "recorded_at", "observer_stage_only",
            "http_outcome", "fee_outcome", "retry_authorized", "evidence_hash"})
        require(phase["status"] == "observed" and phase["stage"] in STAGES and phase["observer_stage_only"] is True
            and phase["http_outcome"] == phase["fee_outcome"] == "unknown" and phase["retry_authorized"] is False
            and _hash(phase["evidence_hash"]) and timestamp(phase["recorded_at"]) >= timestamp(operation["capture_started_at"]))


def _proposal(payload, record, *, birth=False):
    """Validate stable facts from the already checked journal, without live IO."""
    from core.execution.image_continuation_parent_io import REF_FIELDS, REF_KIND

    proposal = json_copy(record["proposal"], limit=16 * 1048576)
    require(type(proposal) is dict and set(proposal) == PROPOSAL_FIELDS)
    require(type(proposal["schema_version"]) is int and proposal["schema_version"] == 1
        and proposal["kind"] == "image_continuation_proposal_v1")
    require(proposal["read_only"] is True and proposal["dispatch_available"] is False and proposal["approval_granted"] is False
        and proposal["additional_allocation_status"] == "proposed_only")
    require(type(proposal["paid_calls"]) is int and proposal["paid_calls"] == 0
        and type(proposal["automatic_followup_calls"]) is int and proposal["automatic_followup_calls"] == 0)
    require(proposal["basis_hash"] == digest(proposal["basis"])
        and proposal["proposal_hash"] == digest({key: item for key, item in proposal.items() if key != "proposal_hash"}))
    b = proposal["basis"]
    require(type(b) is dict and set(b) == BASIS_FIELDS and type(b["schema_version"]) is int and b["schema_version"] == 1
        and b["kind"] == "image_continuation_basis_v1")
    scope = payload["media"]["scope:" + record["scope_id"]]
    grant = payload["media"]["grant:" + b["grant_id"]]
    require(grant["scope_id"] == scope["id"] and b["scope_id"] == scope["id"] and record["task_id"] == scope["task_id"])
    require(b["scope_hash"] == digest(scope) and b["grant_hash"] == digest({**grant, "revoked_at": None}))
    admission = _admission(payload, scope)
    stable = {"account_id": scope["account_id"], "admission": admission, "source_hash": admission["source_hash"],
        "plan_hash": admission["plan_hash"], "asset_input_hash": digest(scope["asset_input"]),
        "manifest_hash": scope["manifest"]["manifest_hash"], "manifest_origin_hash": digest(scope["manifest_origin"]),
        "reference_profile_hash": None if scope.get("reference_profile") is None else scope["reference_profile"]["profile_hash"],
        "fee_source_hash": digest(scope["fee_source"]), "original_policy_hash": scope["policy"]["policy_hash"],
        "original_budget_anchor_hash": scope["anchor"]["budget_anchor_hash"], "original_valid_until": scope["policy"]["valid_until"],
        "production_quality_status": "not_reviewed", "required_next_contract": "versioned_validity_and_unknown_continuation"}
    require(all(_same(b[key], value) for key, value in stable.items()))
    require(_hash(b["control_hash"]) and _hash(b["original_code_manifest_hash"]))
    _manifest(b["current_code_manifest"])
    checkpoint = b["checkpoint"]
    require(type(checkpoint) is dict and set(checkpoint) == {"head_revision", "head_hash", "payload_hash"}
        and type(checkpoint["head_revision"]) is int and checkpoint["head_revision"] >= 1
        and _hash(checkpoint["head_hash"]) and _hash(checkpoint["payload_hash"]))
    policy = scope["policy"]
    until, created = timestamp(b["proposed_valid_until"]), timestamp(record["created_at"])
    require(timestamp(scope["created_at"]) <= created < until <= created + timedelta(hours=24)
        and until > timestamp(policy["valid_until"]) and policy["request_deadline_seconds"] == 120)
    # The proposal freezes the facts at its actual birth. Later approved
    # attempts must not turn that disclosure into a claim about a new head.
    # The birth checks below and validate_history reconstruct the real prefix;
    # here the old unknown and delivered records remain immutable bindings.
    frozen_ids = [item["operation_id"] for item in b["verified_deliveries"]] + [b["unknown_disposition"]["operation_id"]]
    require(len(frozen_ids) == len(set(frozen_ids)))
    operations = sorted((payload["media"]["operation:" + item] for item in frozen_ids), key=lambda item: item["submission_index"])
    require(all(item["scope_id"] == scope["id"] and item["grant_id"] == grant["id"] for item in operations))
    unknown = payload["media"]["operation:" + b["unknown_disposition"]["operation_id"]]
    require(unknown["status"] == "unknown" and unknown["delivery_id"] is None)
    _checked_unknown(b["unknown_disposition"], unknown)
    targets = _targets(scope, operations, unknown)
    require(_same(b["remaining_jobs"], targets) and _same(b["verified_deliveries"], _deliveries(payload, operations)))
    remaining = policy["max_submissions"] - len(operations)
    extra = len(targets) - remaining
    require(remaining >= 0 and extra == 1 and units(policy["per_submission_risk_cny"]) == 1000000)
    limits = {"original_max_submissions": policy["max_submissions"], "used_submissions": len(operations),
        "inherited_remaining_submissions": remaining, "undelivered_jobs": len(targets), "additional_paid_calls": 1,
        "proposed_cumulative_max_submissions": policy["max_submissions"] + 1, "proposed_remaining_submissions": len(targets),
        "max_queries_per_operation": policy["max_queries"], "used_queries": b["limits"]["used_queries"],
        "original_request_deadline_seconds": 120, "original_intents_released": 0}
    require(_same(b["limits"], limits))
    require(type(limits["used_queries"]) is int and 0 <= limits["used_queries"] <= len(operations) * policy["max_queries"])
    if birth:
        actual_operations, actual_unknown = _operations(payload, scope, grant)
        require(_same(actual_operations, operations) and _same(actual_unknown, unknown))
        require(limits["used_queries"] == sum(item["scope_id"] == scope["id"] for item in records(payload, "query")))
    parent = b["parent"]
    require(type(parent) is dict and set(parent) == {"evidence_hash", "authorization_hash", "frozen_parent_sha256",
        "current_parent_sha256", "old_operations_hash", "old_operation_count", "retained_risk_cny", "max_calls", "original_budget_cny"})
    require(all(_hash(parent[key]) for key in ("evidence_hash", "authorization_hash", "frozen_parent_sha256",
        "current_parent_sha256", "old_operations_hash")))
    require(parent["authorization_hash"] == scope["anchor"]["parent_authorization_hash"]
        and parent["frozen_parent_sha256"] == scope["anchor"]["parent_ledger_hash"]
        and type(parent["old_operation_count"]) is int and 1 <= parent["old_operation_count"] <= 10000
        and type(parent["max_calls"]) is int and parent["max_calls"] >= 1 and parent["original_budget_cny"] == "100.000000")
    risk = units(parent["retained_risk_cny"])
    require(0 < risk <= 99000000 and parent["retained_risk_cny"] == money(risk))
    budget = {"original_budget_cny": "100.000000", "inherited_retained_risk_cny": money(risk),
        "inherited_max_calls": parent["max_calls"], "additional_risk_cny": "1.000000", "proposed_retained_risk_cny": money(risk + 1000000),
        "proposed_max_calls": parent["max_calls"] + 1, "retained_risk_released_cny": "0.000000", "risk_basis": policy["risk_basis"], "final_bill": False}
    require(_same(b["budget"], budget))
    require(_same(b["proposed_effective_fields"], {"planning_only": True, "valid_until": b["proposed_valid_until"],
        "max_submissions": policy["max_submissions"] + 1, "request_deadline_seconds": 120,
        "other_policy_fields_must_equal_original": True, "dispatchable_descriptor_built": False, "original_scope_and_grant_unchanged": True}))
    previous_id = b["previous_code_activation_id"]
    if previous_id is None:
        require(b["previous_code_activation_hash"] is None)
    else:
        previous = payload["media"]["code_continuation_activation:" + previous_id]
        require(previous["scope_id"] == scope["id"] and digest(previous) == b["previous_code_activation_hash"])
    request = {"kind": "image_validity_continuation_parent_scope_v1", "max_new_calls": 1, "scope_risk_cny": "1.000000",
        "prior_control_hash": b["control_hash"], "prior_parent_operations_hash": parent["old_operations_hash"],
        "prior_parent_sha256": parent["current_parent_sha256"], "continuation_basis_hash": proposal["basis_hash"]}
    require(_same(proposal["parent_allocation"], {"schema_version": 1, "kind": "image_continuation_parent_allocation_proposal_v1",
        "operation_key": "image-continuation-" + proposal["basis_hash"][:32], "request": request,
        "upper_cny": "1.000000", "quote_ref": "image-continuation-basis:" + proposal["basis_hash"]}))
    ref = record["parent_evidence_ref"]
    require(type(ref) is dict and set(ref) == REF_FIELDS and type(ref["schema_version"]) is int and ref["schema_version"] == 1
        and ref["kind"] == REF_KIND)
    require(all(_hash(ref[key]) for key in REF_FIELDS - {"schema_version", "kind"}))
    require(ref["proposal_hash"] == proposal["proposal_hash"] and ref["control_hash"] == b["control_hash"]
        and ref["evidence_hash"] == parent["evidence_hash"] and ref["ref_hash"] == digest({key: item for key, item in ref.items() if key != "ref_hash"}))
    return proposal


def validate_record(payload, record):
    """Pure historical record checks; no live file or current authority reads."""
    try:
        kind = record["kind"]
        require(kind in KINDS and set(record) == COMMON | RECORD_FIELDS[kind]
            and type(record["schema_version"]) is int and record["schema_version"] == 1
            and _id(record["id"]) and _id(record["scope_id"]) and _id(record["task_id"]))
        require(record["dispatch_ready"] is False and record["runtime_contract_pending"] is True)
        require(record["id"] == digest({"kind": kind, "scope_id": record["scope_id"], **_values(record)})[:32])
        if kind == QUOTE:
            _proposal(payload, record)
            return
        quote = payload["media"][QUOTE + ":" + record["quote_id"]]
        require(quote["scope_id"] == record["scope_id"] and quote["task_id"] == record["task_id"])
        proposal = quote["proposal"]
        require(record["proposal_hash"] == proposal["proposal_hash"] and record["consent"] is True)
        require(timestamp(quote["created_at"]) <= timestamp(record["created_at"]) < timestamp(proposal["basis"]["proposed_valid_until"]))
        if kind == INTENT:
            require(record["additional_risk_cny"] == "1.000000" and type(record["additional_paid_calls"]) is int
                and record["additional_paid_calls"] == 1)
        else:
            from core.execution.image_continuation_parent_io import validate_extension_receipt
            intent = payload["media"][INTENT + ":" + record["intent_id"]]
            require(intent["scope_id"] == record["scope_id"] and intent["quote_id"] == record["quote_id"]
                and intent["proposal_hash"] == record["proposal_hash"])
            require(timestamp(intent["created_at"]) <= timestamp(record["created_at"]))
            validate_extension_receipt(record["parent_receipt"], proposal, intent=intent)
            require(record["parent_receipt"]["authority_hash"] == quote["parent_evidence_ref"]["authority_hash"]
                and timestamp(record["parent_receipt"]["begin"]["reserved_observed_at"]) <= timestamp(record["created_at"]))
    except Exception:
        raise unavailable() from None


def retained_extensions(payload, account_id):
    return [item for item in records(payload, INTENT)
        if payload["media"]["scope:" + item["scope_id"]]["account_id"] == account_id]


def require_reservation_intent(payload, proposal):
    try:
        scope_id = proposal["basis"]["scope_id"]
        found = _own(payload, INTENT, scope_id)
        require(len(found) == 1)
        intent = found[0]
        quote = payload["media"][QUOTE + ":" + intent["quote_id"]]
        require(_same(quote["proposal"], proposal))
        validate_record(payload, quote)
        validate_record(payload, intent)
        return copy.deepcopy(intent)
    except Exception:
        raise unavailable() from None


def _without(payload, *keys):
    return {**payload, "media": {key: item for key, item in payload["media"].items() if key not in keys}}


def validate_transition(payload, previous=None):
    try:
        for scope in records(payload, "scope"):
            require(len(_own(payload, INTENT, scope["id"])) <= 1 and len(_own(payload, ACTIVATION, scope["id"])) <= 1)
        if previous is None:
            return
        old = previous.get("media", {})
        added = [record for key, record in payload.get("media", {}).items() if key not in old and record["kind"] in KINDS]
        for record in added:
            require(len(added) == 1 and _same(_without(payload, record["kind"] + ":" + record["id"]), previous))
            scope = old["scope:" + record["scope_id"]]
            if record["kind"] == QUOTE:
                from core.execution.scope_budget import scope_envelope
                require(not _own(previous, INTENT, scope["id"]))
                b = record["proposal"]["basis"]
                require(b["checkpoint"]["payload_hash"] == digest(previous))
                envelope = scope_envelope(previous, scope["account_id"])
                require(units(b["budget"]["inherited_retained_risk_cny"]) == envelope["risk_units"]
                    and b["budget"]["inherited_max_calls"] == envelope["max_calls"])
                prior = _active(previous, scope["id"])
                require(b["previous_code_activation_id"] == (None if prior is None else prior["id"]))
                require(old["grant:" + b["grant_id"]]["revoked_at"] is None)
                _proposal(previous, record, birth=True)
            elif record["kind"] == INTENT:
                require(not _own(previous, INTENT, scope["id"]))
                quote = old[QUOTE + ":" + record["quote_id"]]
                require(digest(_without(previous, QUOTE + ":" + quote["id"])) == quote["proposal"]["basis"]["checkpoint"]["payload_hash"])
                require(old["grant:" + quote["proposal"]["basis"]["grant_id"]]["revoked_at"] is None)
            else:
                require(not _own(previous, ACTIVATION, scope["id"]))
                intent = old[INTENT + ":" + record["intent_id"]]
                quote = old[QUOTE + ":" + intent["quote_id"]]
                require(digest(_without(previous, QUOTE + ":" + quote["id"], INTENT + ":" + intent["id"]))
                    == quote["proposal"]["basis"]["checkpoint"]["payload_hash"])
                require(old["grant:" + quote["proposal"]["basis"]["grant_id"]]["revoked_at"] is None)
    except Exception:
        raise unavailable() from None


def _birth(state, record):
    key = record["kind"] + ":" + record["id"]
    found = [frame for frame in state["frames"] if key in frame["delta"].get("media", {})]
    require(len(found) == 1 and set(found[0]["delta"]) == {"media"}
        and _same(found[0]["delta"]["media"], {key: record}))
    return found[0]


def validate_history(state, payload):
    try:
        from core.execution.store import EMPTY

        prefix = copy.deepcopy(EMPTY)
        for frame in state["frames"]:
            for record in frame["delta"].get("media", {}).values():
                if record["kind"] == QUOTE:
                    checkpoint = record["proposal"]["basis"]["checkpoint"]
                    require(checkpoint["head_revision"] == frame["revision"] - 1
                        and checkpoint["head_hash"] == frame["previous_hash"]
                        and checkpoint["payload_hash"] == digest(prefix))
                    _proposal(prefix, record, birth=True)
            for namespace, values in frame["delta"].items():
                prefix.setdefault(namespace, {}).update(copy.deepcopy(values))
        require(_same(prefix, payload))
        for quote in records(payload, QUOTE):
            birth = _birth(state, quote)
            checkpoint = quote["proposal"]["basis"]["checkpoint"]
            require(birth["revision"] == checkpoint["head_revision"] + 1 and birth["previous_hash"] == checkpoint["head_hash"])
        for intent in records(payload, INTENT):
            birth, prior = _birth(state, intent), _birth(state, payload["media"][QUOTE + ":" + intent["quote_id"]])
            require(birth["revision"] == prior["revision"] + 1 and birth["previous_hash"] == prior["hash"])
        for activation in records(payload, ACTIVATION):
            birth, prior = _birth(state, activation), _birth(state, payload["media"][INTENT + ":" + activation["intent_id"]])
            require(birth["revision"] == prior["revision"] + 1 and birth["previous_hash"] == prior["hash"])
    except Exception:
        raise unavailable() from None


def _head_is(state, record):
    birth = _birth(state, record)
    require(state["head_revision"] == birth["revision"] and state["head_hash"] == birth["hash"])


def require_current_continuation(control, payload, proposal):
    """Fresh consent/code/source/unknown check, without taking the parent lock."""
    try:
        require(type(control) is LoadedImageControl and control.document.mode == "enabled")
        _, _, _, actual = control._verified(for_dispatch=False)
        require(_same(payload, actual))
        b = proposal["basis"]
        scope, grant = actual["media"]["scope:" + b["scope_id"]], actual["media"]["grant:" + b["grant_id"]]
        require(control.control_hash == b["control_hash"] and _same(control.document.admission.model_dump(), b["admission"]))
        require(digest(scope) == b["scope_hash"] and grant["revoked_at"] is None and digest(grant) == b["grant_hash"])
        require(now() < timestamp(b["proposed_valid_until"]) and digest(control.document.code_manifest) == b["original_code_manifest_hash"])
        require(_same(_collect_code_manifest(control), b["current_code_manifest"]) and _same(_unknown(control, actual["media"]["operation:" + b["unknown_disposition"]["operation_id"]]), b["unknown_disposition"]))
        intents = _own(actual, INTENT, scope["id"])
        if intents and not _own(actual, ACTIVATION, scope["id"]):
            intent = require_reservation_intent(actual, proposal)
            _head_is(_state(ExecutionStore(control.roots["execution"])), intent)
        return True
    except Exception:
        raise unavailable() from None


def _fresh(control, payload, quote):
    import hashlib

    from core.execution import image_continuation_parent_io as parent_io
    from core.execution.control import _read
    from core.execution.image_continuation_parent import MAX_PARENT_BYTES, verify_parent_transition

    proposal = quote["proposal"]
    require_current_continuation(control, payload, proposal)
    parent_io.verify_frozen_parent_reference(control, proposal, quote["parent_evidence_ref"])
    before = parent_io.read_frozen_parent_evidence(control, proposal)
    raw = _read(parent_io.server_parent_path(control), maximum=MAX_PARENT_BYTES)
    if hashlib.sha256(raw).hexdigest() != before["current_parent_sha256"]:
        require_reservation_intent(payload, proposal)
        verify_parent_transition(before, raw, proposal["parent_allocation"])
    return before


def quote_continuation(base, *, valid_until):
    try:
        from core.execution import image_continuation_parent_io as parent_io
        require(type(base) is LoadedImageControl)
        store = ExecutionStore(base.roots["execution"])
        with _lock(store, base.document.admission.task_id):
            _, _, ready, payload = base._verified(for_dispatch=False)
            matching = base._matching(payload, ready)
            require(len(matching) == 1)
            scope = matching[0]
            wanted = timestamp(valid_until)
            quotes = sorted(_own(payload, QUOTE, scope["id"]), key=lambda item: item["proposal"]["basis"]["checkpoint"]["head_revision"])
            intents = _own(payload, INTENT, scope["id"])
            if quotes and timestamp(quotes[-1]["proposal"]["basis"]["proposed_valid_until"]) == wanted:
                try:
                    _fresh(base, payload, quotes[-1])
                    if not intents:
                        _head_is(_state(store), quotes[-1])
                    return _result(quotes[-1], True)
                except CreatorError:
                    require(not intents)
            require(not intents)
            evidence = parent_io.load_current_parent_evidence(base)
            proposal = build_proposal(base, evidence, valid_until=valid_until)
            tentative = _record(QUOTE, scope, proposal=proposal, parent_evidence_ref={})
            # The reference is small and strictly bounded. Reserve its maximum,
            # the later bounded receipt, and two further frame envelopes before
            # creating even the external evidence artifact.
            _capacity(store, tentative, parent_io.MAX_RECEIPT_BYTES + 3 * 65536)
            ref = parent_io.freeze_parent_evidence(base, proposal, evidence)
            record = _record(QUOTE, scope, proposal=proposal, parent_evidence_ref=ref)
            with store.transaction() as current:
                require(digest(current) == proposal["basis"]["checkpoint"]["payload_hash"])
                state = _state(store)
                checkpoint = proposal["basis"]["checkpoint"]
                require(state["head_revision"] == checkpoint["head_revision"] and state["head_hash"] == checkpoint["head_hash"])
                _fresh(base, current, record)
                _capacity(store, record, parent_io.MAX_RECEIPT_BYTES + 2 * 65536)
                current.setdefault("media", {})[QUOTE + ":" + record["id"]] = record
            return _result(record, False)
    except Exception:
        raise unavailable() from None


def prepare_continuation(base, quote_id, *, proposal_hash, consent):
    try:
        from core.execution import image_continuation_parent_io as parent_io
        require(type(base) is LoadedImageControl and _id(quote_id) and _hash(proposal_hash) and consent is True)
        store = ExecutionStore(base.roots["execution"])
        with _lock(store, base.document.admission.task_id), store.transaction() as payload:
            quote = payload["media"][QUOTE + ":" + quote_id]
            require(quote["proposal"]["proposal_hash"] == proposal_hash)
            _fresh(base, payload, quote)
            found = _own(payload, INTENT, quote["scope_id"])
            if found:
                require(found[0]["quote_id"] == quote_id)
                return _result(found[0], True)
            _head_is(_state(store), quote)
            scope = payload["media"]["scope:" + quote["scope_id"]]
            record = _record(INTENT, scope, quote_id=quote_id, proposal_hash=proposal_hash, consent=True,
                additional_risk_cny="1.000000", additional_paid_calls=1)
            _capacity(store, record, parent_io.MAX_RECEIPT_BYTES + 65536)
            payload["media"][INTENT + ":" + record["id"]] = record
            return _result(record, False)
    except Exception:
        raise unavailable() from None


def activate_continuation(base, intent_id, *, proposal_hash, consent):
    try:
        from core.execution import image_continuation_parent_io as parent_io
        require(type(base) is LoadedImageControl and _id(intent_id) and _hash(proposal_hash) and consent is True)
        store = ExecutionStore(base.roots["execution"])
        with _lock(store, base.document.admission.task_id):
            payload = store.read_only()
            intent = payload["media"][INTENT + ":" + intent_id]
            quote = payload["media"][QUOTE + ":" + intent["quote_id"]]
            proposal = quote["proposal"]
            require(intent["proposal_hash"] == proposal_hash == proposal["proposal_hash"])
            before = _fresh(base, payload, quote)
            found = _own(payload, ACTIVATION, intent["scope_id"])
            if found:
                require(found[0]["intent_id"] == intent_id)
                parent_io.verify_extension(base, proposal, before_evidence=before, receipt=found[0]["parent_receipt"])
                return _result(found[0], True)
            _head_is(_state(store), intent)
            tentative = _record(ACTIVATION, payload["media"]["scope:" + intent["scope_id"]], quote_id=quote["id"], intent_id=intent_id,
                proposal_hash=proposal_hash, consent=True, parent_receipt={})
            _capacity(store, tentative, parent_io.MAX_RECEIPT_BYTES)
            receipt = parent_io.reserve_extension(base, proposal, before_evidence=before)
            parent_io.verify_extension(base, proposal, before_evidence=before, receipt=receipt)
            with store.transaction() as current:
                require(_same(current, payload))
                _head_is(_state(store), intent)
                _fresh(base, current, quote)
                parent_io.verify_extension(base, proposal, before_evidence=before, receipt=receipt)
                scope = current["media"]["scope:" + intent["scope_id"]]
                record = _record(ACTIVATION, scope, quote_id=quote["id"], intent_id=intent_id,
                    proposal_hash=proposal_hash, consent=True, parent_receipt=receipt)
                _capacity(store, record, 0)
                current["media"][ACTIVATION + ":" + record["id"]] = record
            return _result(record, False)
    except Exception:
        raise unavailable() from None
