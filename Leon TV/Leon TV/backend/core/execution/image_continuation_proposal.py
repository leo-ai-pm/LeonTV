"""Read-only proposals for an expired image scope with one retained unknown.

This module describes a future versioned continuation. It does not authorize
one: the old scope, policy, permits, journal, parent ledger and capture remain
unchanged. Proposed fields are not a dispatchable descriptor or a grant.
"""
import copy
import hashlib
from datetime import timedelta, timezone
from pathlib import Path

from core.creator.store import CreatorError, digest
from core.execution.control import _json, _read
from core.execution.image_code_continuation import (
    _active,
    _checkpoint,
    _collect_code_manifest,
)
from core.execution.image_control import LoadedImageControl
from core.execution.image_policy import FrozenImageRequest, timestamp
from core.execution.media_failure_phase import failure_projection
from core.execution.media_ledger import account_halted, records, unresolved_text_claims
from core.execution.policy import money, now, units
from core.execution.receipts import ReceiptStore
from core.execution.scope_budget import scope_envelope
from core.execution.store import MAX_STATE_BYTES, ExecutionStore, validate_state
from core.planner.product_assets import image_jobs
from core.planner.review import json_copy

ERROR_CODE = "IMAGE_CONTINUATION_PROPOSAL_UNAVAILABLE"
MAX_WINDOW = timedelta(hours=24)
PROPOSAL_FIELDS = {"schema_version", "kind", "basis", "basis_hash", "parent_allocation", "read_only",
    "dispatch_available", "approval_granted", "additional_allocation_status", "paid_calls", "automatic_followup_calls", "proposal_hash"}
BASIS_FIELDS = {"schema_version", "kind", "control_hash", "scope_id", "scope_hash", "grant_id", "grant_hash", "account_id",
    "admission", "source_hash", "plan_hash", "asset_input_hash", "manifest_hash", "manifest_origin_hash", "reference_profile_hash",
    "fee_source_hash", "original_policy_hash", "original_budget_anchor_hash", "original_valid_until", "proposed_valid_until",
    "checkpoint", "original_code_manifest_hash", "current_code_manifest", "previous_code_activation_id", "previous_code_activation_hash",
    "parent", "unknown_disposition", "limits", "budget", "proposed_effective_fields", "verified_deliveries", "remaining_jobs",
    "production_quality_status", "required_next_contract"}


def unavailable():
    return CreatorError(ERROR_CODE, "Read-only image continuation proposal is unavailable", 409)


def require(value):
    if not value:
        raise unavailable()


def _scope_and_grant(control, payload, ready):
    matching = control._matching(payload, ready)
    require(len(matching) == 1)
    scope = matching[0]
    grants = [item for item in records(payload, "grant")
        if item["scope_id"] == scope["id"] and item["revoked_at"] is None]
    require(len(grants) == 1)
    grant = grants[0]
    require(grant["task_id"] == scope["task_id"] and not account_halted(payload, scope["account_id"]))
    require(not unresolved_text_claims(payload))
    return scope, grant


def _operations(payload, scope, grant):
    account_id = scope["account_id"]
    same_account_scopes = {item["id"] for item in records(payload, "scope") if item["account_id"] == account_id}
    account_operations = [item for item in records(payload, "operation") if item["scope_id"] in same_account_scopes]
    unknowns = [item for item in account_operations if item["status"] == "unknown"]
    require(len(unknowns) == 1)
    unknown = unknowns[0]
    require(unknown["scope_id"] == scope["id"] and unknown["grant_id"] == grant["id"])
    require(unknown["raw"] is None and unknown["receipt"] is None and unknown["fee_id"] is None
        and unknown["delivery_id"] is None and unknown["capture_started_at"] is not None)
    for item in account_operations:
        if item["id"] == unknown["id"]:
            continue
        require(item["status"] not in {"reserved", "submitting", "unknown"} and item["fee_id"] is not None)
    require(all(item["status"] not in {"reserved", "submitting", "unknown"}
        for item in records(payload, "query") if item["scope_id"] in same_account_scopes))
    require(all(item["status"] == "settled" for item in payload["operations"].values() if item["account_id"] == account_id))
    asset_scopes = {item["id"] for item in payload.get("asset_design", {}).values()
        if item["kind"] == "scope" and item["account_id"] == account_id}
    require(all(item["status"] not in {"reserved", "submitting", "unknown"} and item["fee_id"] is not None
        for item in payload.get("asset_design", {}).values() if item["kind"] == "operation" and item["scope_id"] in asset_scopes))
    own = sorted((item for item in account_operations if item["scope_id"] == scope["id"]), key=lambda item: item["submission_index"])
    require(own and own[0]["status"] == "accepted" and own[0]["fee_id"] is not None and own[0]["delivery_id"] is not None)
    require(all(item["grant_id"] == grant["id"] and (item["id"] == unknown["id"] or item["delivery_id"] is not None) for item in own))
    return own, unknown


def _unknown(control, record):
    descriptor = FrozenImageRequest.from_frozen(record["descriptor"])
    receipt_store = ReceiptStore(control.roots["execution"] / "media-receipts")
    receipt_store.check_private((control.backend_root / "code", control.backend_root.parent / "frontend/public", *control.static_roots))
    inspection = receipt_store.inspect_binding(record["id"], descriptor.binding())
    # Evidence already captured for this old request must be inspected under its
    # original contract before a proposal can describe an evidence-free hold.
    require(inspection["status"] == "missing" and inspection["candidate_count"] == 0)
    return {"operation_id": record["id"], "operation_hash": digest(record),
        "descriptor_hash": descriptor.descriptor_hash, "permit_hash": record["descriptor"]["permit"]["permit_hash"],
        "shot_id": record["shot_id"], "submission_index": record["submission_index"],
        "original_deadline_at": record["descriptor"]["permit"]["deadline_at"],
        "capture_started_at": record["capture_started_at"], "raw_hash": None, "receipt_hash": None,
        "fee_id": None, "status_after_proposal": "unknown", "original_record_must_remain_unchanged": True,
        "retained_risk_cny": record["descriptor"]["permit"]["risk_cny"], "consumed_call_slots": 1,
        "call_slots_released": 0, "zero_http_proven": False, "zero_fee_proven": False,
        "collector_inspection": inspection, "failure_phase": failure_projection(control.roots["execution"], record),
        "old_request_retry_authorized": False, "new_replacement_authorized": False,
        "new_unknown_stops_continuation": True, "late_evidence_requires_new_review": True}


def _targets(scope, operations, unknown):
    jobs = image_jobs(scope["asset_input"], scope["manifest"])
    require(len(jobs) == scope["policy"]["max_submissions"])
    delivered = {item["shot_id"] for item in operations if item["delivery_id"] is not None}
    require(unknown["shot_id"] not in delivered)
    targets = []
    for job in jobs:
        if job["shot_id"] in delivered:
            continue
        replacement = unknown["id"] if job["shot_id"] == unknown["shot_id"] else None
        targets.append({"shot_id": job["shot_id"], "job_hash": digest(job), "first_frame_version": 1,
            "attempt_version": 2 if replacement else 1, "replacement_of": replacement,
            "new_operation_id": None, "new_nonce": None,
            "planned_submission_index": len(operations) + len(targets) + 1,
            "reference_shot_ids": copy.deepcopy(job["reference_shot_ids"]),
            "references_require_exact_delivered_operation": True})
    require(sum(item["replacement_of"] is not None for item in targets) == 1)
    return targets


def _parent_summary(evidence):
    return {key: copy.deepcopy(evidence[key]) for key in ("evidence_hash", "authorization_hash", "frozen_parent_sha256",
        "current_parent_sha256", "old_operations_hash", "old_operation_count", "retained_risk_cny", "max_calls", "original_budget_cny")}


def build_proposal(control, parent_evidence, *, valid_until):
    """Reconstruct a sealed future plan; perform no activation or paid action."""
    try:
        from core.execution.image_continuation_parent import (
            MAX_EVIDENCE_BYTES,
            verify_parent_evidence,
        )

        require(type(control) is LoadedImageControl)
        _, _, ready, payload = control._verified(for_dispatch=False)
        require(control.document.mode == "enabled")
        verify_parent_evidence(evidence=parent_evidence, control=control)
        parent_evidence = json_copy(parent_evidence, limit=MAX_EVIDENCE_BYTES)
        scope, grant = _scope_and_grant(control, payload, ready)
        policy, anchor = scope["policy"], scope["anchor"]
        decision_now = now()
        deadline = timestamp(valid_until)
        require(decision_now < deadline <= decision_now + MAX_WINDOW and deadline > timestamp(policy["valid_until"]))
        require(policy["request_deadline_seconds"] == 120)
        normalized_until = deadline.astimezone(timezone.utc).isoformat()
        operations, unknown = _operations(payload, scope, grant)
        targets = _targets(scope, operations, unknown)
        remaining = policy["max_submissions"] - len(operations)
        additional = len(targets) - remaining
        require(remaining >= 0 and additional == 1)
        envelope = scope_envelope(payload, scope["account_id"])
        require(units(parent_evidence["retained_risk_cny"]) == envelope["risk_units"]
            and type(parent_evidence["max_calls"]) is int and parent_evidence["max_calls"] == envelope["max_calls"])
        require(units(parent_evidence["original_budget_cny"]) == units(anchor["original_budget_cny"]) == 100000000)
        additional_risk = additional * units(policy["per_submission_risk_cny"])
        require(additional_risk == 1000000 and envelope["risk_units"] + additional_risk <= 100000000)
        store = ExecutionStore(control.roots["execution"])
        raw_state = _read(store.path("state.json"), maximum=MAX_STATE_BYTES)
        state = _json(raw_state)
        require(digest(validate_state(state)) == digest(payload))
        previous = _active(payload, scope["id"])
        basis = {"schema_version": 1, "kind": "image_continuation_basis_v1", "control_hash": control.control_hash,
            "scope_id": scope["id"], "scope_hash": digest(scope), "grant_id": grant["id"], "grant_hash": digest(grant),
            "account_id": scope["account_id"], "admission": control.document.admission.model_dump(),
            "source_hash": control.document.admission.source_hash, "plan_hash": control.document.admission.plan_hash,
            "asset_input_hash": digest(scope["asset_input"]), "manifest_hash": scope["manifest"]["manifest_hash"],
            "manifest_origin_hash": digest(scope.get("manifest_origin")),
            "reference_profile_hash": None if scope.get("reference_profile") is None else scope["reference_profile"]["profile_hash"],
            "fee_source_hash": digest(scope["fee_source"]), "original_policy_hash": policy["policy_hash"],
            "original_budget_anchor_hash": anchor["budget_anchor_hash"], "original_valid_until": policy["valid_until"],
            "proposed_valid_until": normalized_until, "checkpoint": _checkpoint(state, payload),
            "original_code_manifest_hash": digest(control.document.code_manifest), "current_code_manifest": _collect_code_manifest(control),
            "previous_code_activation_id": None if previous is None else previous["id"],
            "previous_code_activation_hash": None if previous is None else digest(previous),
            "parent": _parent_summary(parent_evidence), "unknown_disposition": _unknown(control, unknown),
            "limits": {"original_max_submissions": policy["max_submissions"], "used_submissions": len(operations),
                "inherited_remaining_submissions": remaining, "undelivered_jobs": len(targets), "additional_paid_calls": additional,
                "proposed_cumulative_max_submissions": policy["max_submissions"] + additional,
                "proposed_remaining_submissions": len(targets), "max_queries_per_operation": policy["max_queries"],
                "used_queries": sum(item["scope_id"] == scope["id"] for item in records(payload, "query")),
                "original_request_deadline_seconds": policy["request_deadline_seconds"], "original_intents_released": 0},
            "budget": {"original_budget_cny": money(100000000), "inherited_retained_risk_cny": money(envelope["risk_units"]),
                "inherited_max_calls": envelope["max_calls"], "additional_risk_cny": money(additional_risk),
                "proposed_retained_risk_cny": money(envelope["risk_units"] + additional_risk),
                "proposed_max_calls": envelope["max_calls"] + additional,
                "retained_risk_released_cny": "0.000000", "risk_basis": policy["risk_basis"], "final_bill": False},
            "proposed_effective_fields": {"planning_only": True, "valid_until": normalized_until,
                "max_submissions": policy["max_submissions"] + additional,
                "request_deadline_seconds": policy["request_deadline_seconds"], "other_policy_fields_must_equal_original": True,
                "dispatchable_descriptor_built": False, "original_scope_and_grant_unchanged": True},
            "verified_deliveries": [{"operation_id": item["id"], "operation_hash": digest(item), "shot_id": item["shot_id"],
                "fee_id": item["fee_id"], "fee_candidate_hash": digest(payload["media"]["fee:" + item["fee_id"]]["candidate"]),
                "platform_observed_cny": payload["media"]["fee:" + item["fee_id"]]["candidate"]["platform_fee_cny"],
                "delivery_id": item["delivery_id"], "delivery_hash": digest(payload["media"]["delivery:" + item["delivery_id"]]),
                "quality_status": "not_reviewed", "final_bill": False} for item in operations if item["delivery_id"] is not None],
            "remaining_jobs": targets, "production_quality_status": "not_reviewed",
            "required_next_contract": "versioned_validity_and_unknown_continuation"}
        basis_hash = digest(basis)
        request = {"kind": "image_validity_continuation_parent_scope_v1", "max_new_calls": additional,
            "scope_risk_cny": money(additional_risk), "prior_control_hash": control.control_hash,
            "prior_parent_operations_hash": parent_evidence["old_operations_hash"],
            "prior_parent_sha256": parent_evidence["current_parent_sha256"], "continuation_basis_hash": basis_hash}
        result = {"schema_version": 1, "kind": "image_continuation_proposal_v1", "basis": basis, "basis_hash": basis_hash,
            "parent_allocation": {"schema_version": 1, "kind": "image_continuation_parent_allocation_proposal_v1",
                "operation_key": "image-continuation-" + basis_hash[:32], "request": request,
                "upper_cny": money(additional_risk), "quote_ref": "image-continuation-basis:" + basis_hash},
            "read_only": True, "dispatch_available": False, "approval_granted": False,
            "additional_allocation_status": "proposed_only", "paid_calls": 0, "automatic_followup_calls": 0}
        # Read-only collection spans several files. Reject observed drift at its
        # end; this is a fresh proposal snapshot, never cached future authority.
        require(_read(store.path("state.json"), maximum=MAX_STATE_BYTES) == raw_state)
        require(hashlib.sha256(_read(Path(parent_evidence["live_parent_path"]), maximum=MAX_EVIDENCE_BYTES)).hexdigest()
            == parent_evidence["current_parent_sha256"])
        require(hashlib.sha256(_read(control.path)).hexdigest() == control.control_hash)
        authority = control.document.authority
        require(hashlib.sha256(_read(authority.authorization_path)).hexdigest() == authority.authorization_sha256)
        require(hashlib.sha256(_read(authority.parent_ledger_path)).hexdigest() == anchor["parent_ledger_hash"])
        require(hashlib.sha256(_read(authority.allocation_begin_path)).hexdigest() == authority.allocation_begin_sha256)
        require(_collect_code_manifest(control) == basis["current_code_manifest"])
        require(_unknown(control, unknown) == basis["unknown_disposition"])
        return {**result, "proposal_hash": digest(result)}
    except Exception:
        raise unavailable() from None


def verify_proposal(value, control, parent_evidence):
    """Require exact fresh reconstruction, including fields after full resealing."""
    try:
        value = json_copy(value, limit=16 * 1048576)
        require(type(value) is dict and set(value) == PROPOSAL_FIELDS and type(value.get("basis")) is dict
            and set(value["basis"]) == BASIS_FIELDS)
        require(type(value["schema_version"]) is int and value["schema_version"] == 1
            and value["kind"] == "image_continuation_proposal_v1")
        require(value["read_only"] is True and value["dispatch_available"] is False and value["approval_granted"] is False
            and value["additional_allocation_status"] == "proposed_only")
        require(type(value["paid_calls"]) is int and value["paid_calls"] == 0
            and type(value["automatic_followup_calls"]) is int and value["automatic_followup_calls"] == 0)
        require(value["basis_hash"] == digest(value["basis"])
            and value["proposal_hash"] == digest({key: item for key, item in value.items() if key != "proposal_hash"}))
        rebuilt = build_proposal(control, parent_evidence, valid_until=value["basis"]["proposed_valid_until"])
        require(digest(value) == digest(rebuilt))
        return True
    except Exception:
        raise unavailable() from None
