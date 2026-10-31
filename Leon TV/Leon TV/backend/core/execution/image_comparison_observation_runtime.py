"""Explicit fee, one durable GET intent and isolated diagnostic artifact."""
import base64
import copy
import uuid
from datetime import timedelta

from core.creator.store import digest, utc_now
from core.execution import image_comparison as base
from core.execution import image_comparison_observation as contract
from core.execution.image_capture import ControlledImageCapture
from core.execution.image_comparison_observation_policy import KIND, make_request
from core.execution.image_comparison_runtime import ComparisonExecution
from core.execution.image_control import LoadedImageControl
from core.execution.image_policy import FrozenImageRequest, timestamp
from core.execution.image_receipts import verify_delivery
from core.execution.image_response_lifecycle import response_deadline
from core.execution.policy import now
from core.execution.receipts import ReceiptCaptureSettings
from core.execution.store import ExecutionStore
from core.execution.task_image_fees import candidate_for_task, checked_task_source


class ComparisonObservationExecution(ComparisonExecution):
    def __init__(self, control, observation_activation_id, *, receipt_capture=None, credential_loader=None):
        contract.require(type(control) is LoadedImageControl)
        self.control, self.observation_activation_id = control, observation_activation_id
        self.store = ExecutionStore(control.roots["execution"])
        self.credential_loader = credential_loader
        self.capture = ControlledImageCapture(self.store.root / "media-receipts", authority_verifier=self._capture_guard,
            settings=receipt_capture if receipt_capture is not None else ReceiptCaptureSettings(), static_roots=control.static_roots)

    def quote(self, *args, **kwargs):
        contract.require(False)

    grant = reserve = reserve_submit = submit = revoke = _dispatch = _observe_fee = quote

    def _fresh(self):
        return contract.verified_current(self.control, self.observation_activation_id)

    def _capture_guard(self, frozen):
        payload, a = self._fresh()
        request = FrozenImageRequest.from_frozen(frozen)
        contract.require(request.method == "GET" and frozen["kind"] == KIND
                         and digest(frozen["observation_authority"]) == digest(a))
        record = payload["media"][base.QUERY + ":" + request.binding()["operation_id"]]
        contract.require(record["observation_activation_id"] == a["activation_id"] and record["status"] == "submitting"
                         and digest(record["descriptor"]) == digest(frozen) and now() < timestamp(request.deadline_at))
        contract.candidate_gate(payload, a, own_query=record["id"])
        return True

    def query(self, operation_id, *, key):
        with contract._lock(self.store, self.control.document.admission.task_id):
            with self.store.transaction() as payload:
                actual, a = self._fresh()
                contract.require(digest(actual) == digest(payload) and operation_id == a["source_operation_id"])
                old = [q for q in base.records(payload, base.QUERY)
                       if q["operation_id"] == operation_id and q["idempotency_key"] == key]
                if old:
                    contract.require(len(old) == 1 and old[0].get("observation_activation_id") == a["activation_id"])
                    return {"query": copy.deepcopy(old[0]), "replayed": True}
                contract.require(now() + timedelta(seconds=600) < timestamp(a["effective_policy"]["valid_until"]))
                operation = payload["media"][base.OPERATION + ":" + operation_id]
                contract.require(operation["artifact_id"] is None)
                queries = [q for q in base.records(payload, base.QUERY) if q["operation_id"] == operation_id]
                contract.require(not any("observation_activation_id" in q for q in queries)
                                 and len(queries) < a["effective_policy"]["max_queries"])
                contract.candidate_gate(payload, a)
                identifier = uuid.uuid4().hex
                request = make_request(a, operation, identifier=identifier, query_index=len(queries) + 1, settings=self.capture.settings)
                # This new profile is born as a durable submitting intent. A
                # crash before claim consumes it and can only recover locally.
                # It removes a redundant reserved->submitting full transaction;
                # all three fresh credential/collector checks remain below.
                created = utc_now()
                query = {"schema_version": 1, "kind": base.QUERY, "id": identifier, "scope_id": a["scope_id"],
                         "task_id": a["task_id"], "created_at": created, "activation_id": operation["activation_id"],
                         "observation_activation_id": a["activation_id"], "idempotency_key": key,
                         "operation_id": operation_id, "descriptor": request.frozen(), "status": "submitting",
                         "capture_started_at": created, "raw": None, "receipt": None, "query_index": len(queries) + 1}
                base._put(payload, query)
            raw = None
            try:
                self._capture_guard(request.frozen())
                if self.credential_loader is None:
                    from core.execution.image_operator import tudou_credential

                    credential = tudou_credential()
                else:
                    credential = self.credential_loader()
                self._capture_guard(request.frozen())
                raw = self.capture.capture(request, credential)
            except Exception:
                pass
            finally:
                credential = None
            return self._finish(base.QUERY, identifier, raw)

    def _finish(self, kind, identifier, raw):
        payload, a = self._fresh()
        contract.require(kind == base.QUERY)
        query = payload["media"][base.QUERY + ":" + identifier]
        contract.require(query["observation_activation_id"] == a["activation_id"])
        contract.candidate_gate(payload, a, own_query=identifier)
        return super()._finish(kind, identifier, raw)

    def recover(self, identifier, *, query=True):
        contract.require(query is True)
        with contract._lock(self.store, self.control.document.admission.task_id):
            payload, a = self._fresh()
            record = payload["media"][base.QUERY + ":" + identifier]
            contract.require(record["observation_activation_id"] == a["activation_id"])
            if record["raw"] is not None:
                return {"query": copy.deepcopy(record), "replayed": True}
            request = FrozenImageRequest.from_frozen(record["descriptor"])
            raw, inspection = self.capture.ready_raw(request)
            if raw is not None and (inspection["durable_confirmation_at"] is None
                    or timestamp(inspection["durable_confirmation_at"]) > response_deadline(request.binding())):
                raw = None
            return self._finish(base.QUERY, identifier, raw)

    def observe_fee(self, *args, **kwargs):
        # This profile deliberately supports only the original exact task
        # evidence; a missing POST request-id can never be manufactured.
        contract.require(False)

    def observe_task_fee(self, operation_id, task_export, account_export, task_source, *, observed_at):
        with contract._lock(self.store, self.control.document.admission.task_id), self.store.transaction() as payload:
            actual, a = self._fresh()
            contract.require(digest(actual) == digest(payload) and operation_id == a["source_operation_id"]
                             and now() + timedelta(seconds=120) < timestamp(a["effective_policy"]["valid_until"]))
            operation = payload["media"][base.OPERATION + ":" + operation_id]
            source = checked_task_source(task_source)
            scope = payload["media"]["scope:" + a["scope_id"]]
            old = payload["media"][base.FEE + ":" + operation["fee_id"]] if operation["fee_id"] is not None else None
            candidate = candidate_for_task(FrozenImageRequest.from_frozen(operation["descriptor"]), operation["raw"],
                task_export, account_export, scope["fee_source"], source,
                observed_at=old["candidate"]["observed_at"] if old else observed_at)
            if old is not None:
                contract.require(digest(candidate) == digest(old["candidate"]))
                return {"fee": copy.deepcopy(old), "replayed": True}
            contract.candidate_gate(payload, a, require_fee=False)
            record = {"schema_version": 1, "kind": base.FEE, "id": digest(candidate["platform_log_id"])[:32],
                      "scope_id": a["scope_id"], "task_id": a["task_id"], "created_at": utc_now(),
                      "operation_id": operation_id, "observation_activation_id": a["activation_id"], "candidate": candidate,
                      "export_base64": base64.b64encode(task_export).decode(), "task_fee_source": source,
                      "account_export_base64": base64.b64encode(account_export).decode()}
            base._put(payload, record)
            operation["fee_id"] = record["id"]
            return {"fee": record, "replayed": False}

    def record_artifact(self, operation_id, query_id, image_bytes):
        with contract._lock(self.store, self.control.document.admission.task_id), self.store.transaction() as payload:
            actual, a = self._fresh()
            contract.require(digest(actual) == digest(payload) and operation_id == a["source_operation_id"]
                             and now() + timedelta(seconds=120) < timestamp(a["effective_policy"]["valid_until"]))
            operation = payload["media"][base.OPERATION + ":" + operation_id]
            query = payload["media"][base.QUERY + ":" + query_id]
            contract.require(query["operation_id"] == operation_id and query.get("observation_activation_id") == a["activation_id"]
                             and type(image_bytes) is bytes and 0 < len(image_bytes) <= 2 * 1048576)
            artifact = verify_delivery(FrozenImageRequest.from_frozen(query["descriptor"]), query["raw"], image_bytes)
            if operation["artifact_id"] is not None:
                old = payload["media"][base.ARTIFACT + ":" + operation["artifact_id"]]
                contract.require(digest(old["artifact"]) == digest(artifact))
                return {"artifact": copy.deepcopy(old), "replayed": True}
            contract.candidate_gate(payload, a)
            record = {"schema_version": 1, "kind": base.ARTIFACT,
                      "id": digest({"operation_id": operation_id, "artifact": artifact})[:32],
                      "scope_id": a["scope_id"], "task_id": a["task_id"], "created_at": utc_now(),
                      "operation_id": operation_id, "query_id": query_id, "observation_activation_id": a["activation_id"],
                      "artifact": artifact, "bytes_base64": base64.b64encode(image_bytes).decode(), "purpose": base.PURPOSE}
            base._put(payload, record)
            operation["artifact_id"] = record["id"]
            return {"artifact": record, "replayed": False}
