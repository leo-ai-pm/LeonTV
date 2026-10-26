"""Explicit current runtime approval over the original immutable image control."""
from dataclasses import dataclass

from core.creator.store import digest
from core.execution.image_control import LoadedImageControl, require
from core.execution.image_runtime_continuation import authority_for, verify_current
from core.execution.store import ExecutionStore


@dataclass(frozen=True)
class RuntimeContinuedImageControl:
    base: LoadedImageControl
    runtime_activation_id: str

    def __post_init__(self):
        require(type(self.base) is LoadedImageControl)

    def __getattr__(self, name):
        return getattr(self.base, name)

    def verify_authority(self, backend_root=None, *, for_dispatch=False):
        if backend_root is not None:
            require(backend_root == self.base.backend_root)
        require(type(for_dispatch) is bool)
        if for_dispatch:
            payload = ExecutionStore(self.roots["execution"]).read_only()
            verify_current(self.base, payload, self.runtime_activation_id)
        else:
            self.base.verify_authority(for_dispatch=False)
        return True

    def guard_runtime(self, scope, authority):
        payload = ExecutionStore(self.roots["execution"]).read_only()
        verify_current(self.base, payload, self.runtime_activation_id)
        expected = authority_for(payload, self.runtime_activation_id)
        require(digest(authority) == digest(expected) and digest(scope) == expected["scope_hash"])
        return True


def runtime_status(control, operation_id=None):
    """Read historical facts and current authority without reserving an attempt."""
    from core.execution.image_operator import _admit_record, _resource
    from core.execution.media_continuation_gate import candidate_gate, is_continuation
    from core.execution.media_ledger import (
        account_halted,
        has_pending_fees,
        has_pending_unknown,
        projection,
        records,
    )
    from core.execution.policy import now
    from core.execution.receipts import timestamp

    payload = ExecutionStore(control.roots["execution"]).read_only()
    authority = authority_for(payload, control.runtime_activation_id)
    try:
        verify_current(control.base, payload, control.runtime_activation_id)
        valid = True
    except Exception:
        valid = False
    scope = payload["media"]["scope:" + authority["scope_id"]]
    operations = [op for op in records(payload, "operation") if op["scope_id"] == scope["id"]]
    pending = [op for op in operations if op["capture_started_at"] is None]
    ready = False
    if valid and len(pending) == 1 and is_continuation(pending[0]):
        try:
            candidate_gate(payload, pending[0], exclude_candidate=True)
            ready = now() < timestamp(pending[0]["descriptor"]["permit"]["deadline_at"])
        except Exception:
            pass
    targets = authority["remaining_jobs"]
    delivered = {op["shot_id"] for op in operations if op["delivery_id"] is not None}
    reason = "IMAGE_RUNTIME_CONTINUATION_UNAVAILABLE"
    if valid:
        view = {**payload, "media": {k: v for k, v in payload["media"].items()
            if k != "operation:" + authority["unknown_disposition"]["operation_id"]}}
        if account_halted(payload, scope["account_id"]):
            reason = "EMPIRICAL_RISK_OVERRUN"
        elif has_pending_unknown(view, scope["account_id"]):
            reason = "MEDIA_RESPONSE_UNCONFIRMED"
        elif any(q["status"] in {"reserved", "submitting", "unknown"}
                 and payload["media"]["scope:" + q["scope_id"]]["account_id"] == scope["account_id"] for q in records(payload, "query")):
            reason = "MEDIA_QUERY_RESPONSE_UNCONFIRMED"
        elif pending and now() >= timestamp(pending[0]["descriptor"]["permit"]["deadline_at"]):
            reason = "MEDIA_PERMIT_EXPIRED"
        elif has_pending_fees(view, scope["account_id"]) and not ready:
            reason = "MEDIA_PLATFORM_FEE_REQUIRED"
        elif len(operations) >= authority["max_cumulative_submissions"]:
            reason = "SCOPE_CALL_LIMIT"
        else:
            reason = "RESERVE_EXACT_CANDIDATE"
    result = {"mode": control.document.mode, "runtime_mode": "image_continuation_v2",
        "runtime_authority_valid": valid, "dispatch_available": ready,
        "blocked_reason": None if ready else reason,
        "global_unknown_retained": has_pending_unknown(payload, scope["account_id"]),
        "global_pending_fees": has_pending_fees(payload, scope["account_id"]),
        "remaining_jobs": sum(target["shot_id"] not in delivered for target in targets),
        "cumulative_max_submissions": authority["max_cumulative_submissions"],
        "additional_risk_cny": "0.000000", "additional_paid_calls": 0,
        "media": projection(payload, control.document.admission.task_id),
        "automatic_followup_calls": 0, "query_available": False}
    if not result["remaining_jobs"]:
        result.update(dispatch_available=False, blocked_reason="SCOPE_FINISHED")
    if operation_id is not None:
        operation = _admit_record(control, payload, "operation", operation_id)
        queries = [q for q in records(payload, "query") if q["operation_id"] == operation_id]
        result.update(operation=_resource(operation), queries=[_resource(q) for q in queries],
            query_available=valid and is_continuation(operation) and operation["status"] == "accepted"
                and operation["descriptor"]["permit"]["runtime_activation_id"] == control.runtime_activation_id
                and len(queries) < authority["effective_policy"]["max_queries"])
    return result
