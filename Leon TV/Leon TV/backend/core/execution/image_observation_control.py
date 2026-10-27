"""Explicit GET-only control, retaining a fresh decision on each Key boundary."""
from dataclasses import dataclass

from core.creator.store import digest
from core.execution.image_control import LoadedImageControl, require
from core.execution.media_failure_phase import capture_stage


@dataclass(frozen=True)
class ImageObservationControl:
    base: LoadedImageControl
    observation_activation_id: str

    def __post_init__(self):
        require(type(self.base) is LoadedImageControl)

    def __getattr__(self, name):
        return getattr(self.base, name)

    def verified_current(self):
        from core.execution.image_observation_continuation import verified_current

        # The returned actual snapshot belongs to this call only. Nothing is
        # saved on the control or carried across a credential boundary.
        return verified_current(self.base, self.observation_activation_id)

    def verify_authority(self, backend_root=None, *, for_dispatch=False):
        require(backend_root is None or backend_root == self.base.backend_root)
        require(type(for_dispatch) is bool)
        if for_dispatch:
            self.verified_current()
        else:
            self.base.verify_authority(for_dispatch=False)
        return True

    def guard_capture(self, media, frozen, *, native_stage=None):
        from core.execution.image_observation_runtime import ImageObservationExecution

        require(type(media) is ImageObservationExecution
            and media.observation_activation_id == self.observation_activation_id
            and media.store.root == self.roots["execution"])
        payload, authority = self.verified_current()
        if native_stage is not None:
            capture_stage(native_stage)
        require(digest(authority) == digest(frozen["observation_authority"]))
        return media._capture_guard_verified(payload, authority, frozen)


def observation_status(control, operation_id=None):
    from core.execution.image_observation_continuation import authority_for
    from core.execution.image_operator import _admit_record, _resource
    from core.execution.media_ledger import projection, records
    from core.execution.store import ExecutionStore

    payload = ExecutionStore(control.roots["execution"]).read_only()
    authority = authority_for(payload, control.observation_activation_id)
    if operation_id is not None:
        require(operation_id == authority["source_operation_id"])
    try:
        actual, current = control.verified_current()
        require(digest(actual) == digest(payload) and digest(current) == digest(authority))
        valid = True
    except Exception:
        valid = False
    operation = _admit_record(control, payload, "operation", authority["source_operation_id"])
    queries = [q for q in records(payload, "query") if q["operation_id"] == operation["id"]]
    return {"mode": control.document.mode, "runtime_mode": "image_observation_v1",
        "current_authority_valid": valid, "dispatch_available": False, "query_available": False,
        "blocked_reason": "EXPLICIT_QUERY_REQUIRED" if valid else "IMAGE_OBSERVATION_UNAVAILABLE",
        "operation": _resource(operation), "queries": [_resource(q) for q in queries],
        "query_count": len(queries), "cumulative_query_limit": authority["effective_policy"]["max_queries"],
        "media": projection(payload, control.document.admission.task_id),
        "additional_risk_cny": "0.000000", "additional_paid_calls": 0, "automatic_followup_calls": 0}
