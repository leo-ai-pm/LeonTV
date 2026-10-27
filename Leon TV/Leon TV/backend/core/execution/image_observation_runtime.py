"""GET-only use of the original media journal, lock, run and receipt collector."""
import copy
import uuid

from core.creator.store import digest
from core.execution.image_observation_policy import (
    KIND,
    build_observation_query,
    check_observation_capacity,
    make_observation_permit,
)
from core.execution.media_ledger import records, require
from core.execution.media_runtime import MediaExecution


class ImageObservationExecution(MediaExecution):
    def __init__(self, runtime, *, observation_control, **settings):
        from core.execution.image_observation_control import ImageObservationControl

        require(type(observation_control) is ImageObservationControl, "IMAGE_OBSERVATION_UNAVAILABLE")
        self.observation_control = observation_control
        self.observation_activation_id = observation_control.observation_activation_id
        super().__init__(runtime, **settings)

    def _authority(self, scope, *, payload=None):
        # An observation approval is never a media submission authority. Query
        # and capture below admit their complete GET candidate independently.
        require(False, "IMAGE_OBSERVATION_GET_ONLY")

    def quote(self, *args, **kwargs):
        require(False, "IMAGE_OBSERVATION_GET_ONLY")

    def grant(self, *args, **kwargs):
        require(False, "IMAGE_OBSERVATION_GET_ONLY")

    def reserve_submit(self, *args, **kwargs):
        require(False, "IMAGE_OBSERVATION_GET_ONLY")

    def submit(self, *args, **kwargs):
        require(False, "IMAGE_OBSERVATION_GET_ONLY")

    def revoke(self, *args, **kwargs):
        require(False, "IMAGE_OBSERVATION_GET_ONLY")

    def _record_authority(self, payload, scope, record):
        from core.execution.image_observation_continuation import candidate_gate

        require(record["kind"] == "query" and record["descriptor"]["kind"] == KIND, "IMAGE_OBSERVATION_GET_ONLY")
        actual, authority = self.observation_control.verified_current()
        require(digest(actual) == digest(payload), "IMAGE_OBSERVATION_UNAVAILABLE")
        require(digest(scope) == authority["scope_hash"]
            and digest(record["descriptor"]["observation_authority"]) == digest(authority), "IMAGE_OBSERVATION_UNAVAILABLE")
        candidate_gate(payload, record, exclude_candidate=True)
        return authority

    def _capture_guard_verified(self, payload, authority, frozen):
        """Pure native checks on the fresh snapshot owned by this decision."""
        from core.execution.image_observation_continuation import candidate_gate

        try:
            require(frozen["kind"] == KIND and frozen["method"] == "GET")
            record = payload["media"]["query:" + frozen["permit"]["operation_id"]]
            require(record["status"] == "submitting" and digest(record["descriptor"]) == digest(frozen))
            require(authority["activation_id"] == self.observation_activation_id
                and digest(authority) == digest(frozen["observation_authority"]))
            candidate_gate(payload, record, exclude_candidate=True)
            return True
        except Exception:
            return False

    def _capture_guard(self, frozen):
        try:
            return self.observation_control.guard_capture(self, frozen) is True
        except Exception:
            return False

    def _admit_query(self, payload, task_id, operation_id, query_id=None):
        from core.execution.image_observation_continuation import authority_for

        authority = authority_for(payload, self.observation_activation_id)
        require(task_id == authority["task_id"] and operation_id == authority["source_operation_id"],
            "IMAGE_OBSERVATION_UNAVAILABLE")
        if query_id is not None:
            query = payload["media"]["query:" + query_id]
            require(query["operation_id"] == operation_id and query["descriptor"]["kind"] == KIND
                and query["descriptor"]["permit"]["observation_activation_id"] == self.observation_activation_id,
                "IMAGE_OBSERVATION_UNAVAILABLE")

    def recover(self, task_id, operation_id):
        payload = self.store.read_only()
        query = payload.get("media", {}).get("query:" + operation_id)
        require(query is not None, "IMAGE_OBSERVATION_GET_ONLY")
        self._admit_query(payload, task_id, query["operation_id"], query["id"])
        return super().recover(task_id, operation_id)

    def record_delivery(self, task_id, operation_id, query_id, image_bytes):
        self._admit_query(self.store.read_only(), task_id, operation_id, query_id)
        return super().record_delivery(task_id, operation_id, query_id, image_bytes)

    def query(self, task_id, operation_id, *, key):
        from core.execution.image_observation_continuation import candidate_gate

        request = {"task_id": task_id, "operation_id": operation_id,
            "observation_activation_id": self.observation_activation_id}
        with self._lock(task_id):
            with self.store.transaction() as payload:
                old = self._replay(payload, "query", key, request)
                if old:
                    query_id = old["id"]
                else:
                    actual, authority = self.observation_control.verified_current()
                    require(digest(actual) == digest(payload), "IMAGE_OBSERVATION_UNAVAILABLE")
                    require(authority["source_operation_id"] == operation_id and authority["task_id"] == task_id,
                        "IMAGE_OBSERVATION_UNAVAILABLE")
                    operation = payload["media"]["operation:" + operation_id]
                    scope = payload["media"]["scope:" + operation["scope_id"]]
                    grant = payload["media"]["grant:" + authority["grant_id"]]
                    count = sum(q["operation_id"] == operation_id for q in records(payload, "query"))
                    require(count < authority["effective_policy"]["max_queries"], "MEDIA_QUERY_LIMIT")
                    query_id = uuid.uuid4().hex
                    permit = make_observation_permit(scope, grant, operation, authority,
                        operation_id=query_id, query_index=count + 1)
                    descriptor = build_observation_query(submit=operation["descriptor"], submit_raw=operation["raw"],
                        permit=permit, observation_authority=authority, query_index=count + 1)
                    check_observation_capacity(descriptor, self.capture.settings)
                    query = self._record("query", query_id, task_id, scope["id"], operation_id=operation_id,
                        descriptor=descriptor.frozen(), query_index=count + 1, status="reserved",
                        capture_started_at=None, raw=None, receipt=None)
                    candidate_gate(payload, query)
                    self._put(payload, query)
                    self._remember(payload, "query", key, request, query)
            return self._run(task_id, "query:" + query_id)


def observation_from_asset_design(runtime, task_id, asset_operation_id, *, observation_control, **settings):
    from core.execution.asset_media_handoff import manifest_origin

    require("manifest" not in settings and "manifest_origin" not in settings)
    ready = manifest_origin(runtime.store.read_only(), task_id, asset_operation_id)
    return ImageObservationExecution(runtime, observation_control=observation_control,
        manifest=copy.deepcopy(ready["manifest"]), manifest_origin=copy.deepcopy(ready["origin"]), **settings)
