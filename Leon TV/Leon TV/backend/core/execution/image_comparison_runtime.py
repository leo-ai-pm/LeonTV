"""Explicit native diagnostic actions using the existing store/collector/decoder.

No scheduling, fallback, repeated POST, URL download or formal-shot delivery.
Each credential boundary independently rechecks actual source, funding and code.
"""

import base64
import copy
import uuid

from core.creator.store import CreatorError, digest, utc_now
from core.execution import image_comparison as contract
from core.execution.image_capture import ControlledImageCapture
from core.execution.image_comparison_policy import make_request
from core.execution.image_fees import candidate_for
from core.execution.image_policy import FrozenImageRequest, timestamp
from core.execution.image_receipts import decode_query, decode_submit, verify_delivery
from core.execution.image_response_lifecycle import response_deadline
from core.execution.policy import now
from core.execution.receipts import ReceiptCaptureSettings
from core.execution.store import ExecutionStore


class ComparisonExecution:
    def __init__(self, control, activation_id, *, receipt_capture=None, credential_loader=None):
        from core.execution.image_control import LoadedImageControl

        contract.require(type(control) is LoadedImageControl)
        self.control, self.activation_id = control, activation_id
        self.store = ExecutionStore(control.roots["execution"])
        self.credential_loader = credential_loader
        self.capture = ControlledImageCapture(
            self.store.root / "media-receipts",
            authority_verifier=self._capture_guard,
            settings=receipt_capture if receipt_capture is not None else ReceiptCaptureSettings(),
            static_roots=control.static_roots,
        )

    def _fresh(self):
        # One independently verified native head per boundary; no second store
        # read or memoized permission inside that same decision.
        _, _, ready, payload = self.control._verified(for_dispatch=False)
        activation = payload["media"][contract.ACTIVATION + ":" + self.activation_id]
        return contract._verify_from_current(self.control, payload, ready, activation["quote_id"])

    def _capture_guard(self, frozen):
        payload, _ = self._fresh()
        request = FrozenImageRequest.from_frozen(frozen)
        key = (
            (contract.OPERATION if request.method == "POST" else contract.QUERY)
            + ":"
            + request.binding()["operation_id"]
        )
        record = payload["media"][key]
        contract.require(
            record["activation_id"] == self.activation_id
            and record["status"] == "submitting"
            and digest(record["descriptor"]) == digest(frozen)
            and now() < timestamp(request.deadline_at)
        )
        contract.candidate_gate(payload, self.activation_id, exclude_id=record["id"])
        return True

    def reserve(self, *, key, persistence_activation_id=None):
        with (
            contract._lock(self.store, self.control.document.admission.task_id),
            self.store.transaction() as payload,
        ):
            actual, q = self._fresh()
            contract.require(digest(actual) == digest(payload))
            for op in contract.records(payload, contract.OPERATION):
                if op["activation_id"] == self.activation_id:
                    contract.require(op["idempotency_key"] == key and op.get("persistence_activation_id") == persistence_activation_id)
                    return {"operation": copy.deepcopy(op), "replayed": True}
            if persistence_activation_id is not None:
                from core.execution.media_persistence_contract import verified_current

                actual, permission = verified_current(self.control, persistence_activation_id)
                contract.require(digest(actual) == digest(payload) and permission["comparison_activation_id"] == self.activation_id)
            authority = contract.candidate_gate(payload, self.activation_id)
            source = payload["media"]["operation:" + q["proposal"]["case"]["source_operation_id"]][
                "descriptor"
            ]
            identifier = uuid.uuid4().hex
            request = make_request(authority, source, identifier=identifier)
            record = {
                "schema_version": 1,
                "kind": contract.OPERATION,
                "id": identifier,
                "scope_id": q["scope_id"],
                "task_id": q["task_id"],
                "created_at": utc_now(),
                "activation_id": self.activation_id,
                "idempotency_key": key,
                "descriptor": request.frozen(),
                "status": "reserved",
                "capture_started_at": None,
                "raw": None,
                "receipt": None,
                "fee_id": None,
                "artifact_id": None,
                "submission_index": authority["submission_index"],
                "purpose": contract.PURPOSE,
            }
            if persistence_activation_id is not None:
                record["persistence_activation_id"] = persistence_activation_id
            contract._put(payload, record)
            return {"operation": record, "replayed": False}

    def _dispatch(self, kind, identifier):
        with contract._lock(self.store, self.control.document.admission.task_id):
            actual, _ = self._fresh()
            record = actual["media"][kind + ":" + identifier]
            contract.require(record["activation_id"] == self.activation_id)
            if record["status"] != "reserved":
                return {
                    "operation" if kind == contract.OPERATION else "query": copy.deepcopy(record),
                    "replayed": True,
                }
            contract.candidate_gate(actual, self.activation_id, exclude_id=identifier)
            request = FrozenImageRequest.from_frozen(record["descriptor"])
            contract.require(now() < timestamp(request.deadline_at))
            with self.store.transaction() as current:
                contract.require(digest(current) == digest(actual))
                current["media"][kind + ":" + identifier].update(
                    status="submitting", capture_started_at=utc_now()
                )
            raw = None
            try:
                # Independent pre-Key, post-Key and final collector decisions.
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
            return self._finish(kind, identifier, raw)

    def _finish(self, kind, identifier, raw):
        with self.store.transaction() as current:
            record = current["media"][kind + ":" + identifier]
            before = digest(record)
            if record["raw"] is not None:
                contract.require(raw is None or digest(raw) == digest(record["raw"]))
            elif raw is None:
                record["status"] = "unknown"
            else:
                request = FrozenImageRequest.from_frozen(record["descriptor"])
                try:
                    receipt = (decode_submit if kind == contract.OPERATION else decode_query)(
                        request, raw
                    )
                except (
                    CreatorError,
                    ValueError,
                    TypeError,
                    KeyError,
                    UnicodeError,
                    RecursionError,
                ):
                    # Complete original bytes remain immutable in ReceiptStore.
                    # Invalid JSON/HTML cannot become a normalized native receipt.
                    record["status"] = "unknown"
                else:
                    record.update(
                        raw=copy.deepcopy(raw),
                        receipt=receipt,
                        status="unknown"
                        if receipt["classification"] == "unconfirmed"
                        else receipt["classification"],
                    )
            return {
                "operation" if kind == contract.OPERATION else "query": copy.deepcopy(record),
                "replayed": digest(record) == before,
            }

    def submit(self, operation_id):
        return self._dispatch(contract.OPERATION, operation_id)

    def recover(self, identifier, *, query=False):
        # A restart can inspect/adopt the original complete collector only.
        kind = contract.QUERY if query else contract.OPERATION
        with contract._lock(self.store, self.control.document.admission.task_id):
            payload, _ = self._fresh()
            op = payload["media"][kind + ":" + identifier]
            contract.require(op["activation_id"] == self.activation_id)
            if op["raw"] is not None or op["status"] == "reserved":
                result = {"operation" if not query else "query": copy.deepcopy(op), "replayed": True}
            else:
                raw, inspection = self.capture.ready_raw(FrozenImageRequest.from_frozen(op["descriptor"]))
                if raw is not None and (inspection["durable_confirmation_at"] is None or timestamp(
                    inspection["durable_confirmation_at"]
                ) > response_deadline(FrozenImageRequest.from_frozen(op["descriptor"]).binding())):
                    raw = None
                result = self._finish(kind, identifier, raw)
        return self._receive_completed_result(result) if query else result

    def observe_fee(self, operation_id, export_bytes, *, observed_at):
        return self._observe_fee(operation_id, export_bytes, observed_at=observed_at)

    def observe_task_fee(
        self, operation_id, task_export, account_export, task_source, *, observed_at
    ):
        return self._observe_fee(
            operation_id,
            task_export,
            observed_at=observed_at,
            account_export=account_export,
            task_source=task_source,
        )

    def _observe_fee(
        self, operation_id, export_bytes, *, observed_at, account_export=None, task_source=None
    ):
        with (
            contract._lock(self.store, self.control.document.admission.task_id),
            self.store.transaction() as payload,
        ):
            actual, q = self._fresh()
            contract.require(digest(actual) == digest(payload))
            op = payload["media"][contract.OPERATION + ":" + operation_id]
            contract.require(
                op["activation_id"] == self.activation_id and op["status"] == "accepted"
            )
            scope = payload["media"]["scope:" + q["scope_id"]]
            old = payload["media"].get(contract.FEE + ":" + op["fee_id"]) if op["fee_id"] else None
            if task_source is not None:
                from core.execution.task_image_fees import candidate_for_task, checked_task_source

                task_source = checked_task_source(task_source)
                candidate = candidate_for_task(
                    FrozenImageRequest.from_frozen(op["descriptor"]),
                    op["raw"],
                    export_bytes,
                    account_export,
                    scope["fee_source"],
                    task_source,
                    observed_at=old["candidate"]["observed_at"] if old else observed_at,
                )
            else:
                candidate = candidate_for(
                    FrozenImageRequest.from_frozen(op["descriptor"]),
                    op["raw"],
                    export_bytes,
                    scope["fee_source"],
                    observed_at=observed_at,
                )
            identifier = digest(candidate["platform_log_id"])[:32]
            if op["fee_id"] is not None:
                old = payload["media"][contract.FEE + ":" + op["fee_id"]]
                contract.require(digest(candidate) == digest(old["candidate"]))
                return {"fee": copy.deepcopy(old), "replayed": True}
            record = {
                "schema_version": 1,
                "kind": contract.FEE,
                "id": identifier,
                "scope_id": q["scope_id"],
                "task_id": q["task_id"],
                "created_at": utc_now(),
                "operation_id": operation_id,
                "candidate": candidate,
                "export_base64": base64.b64encode(export_bytes).decode(),
            }
            if task_source is not None:
                record.update(
                    task_fee_source=task_source,
                    account_export_base64=base64.b64encode(account_export).decode(),
                )
            contract._put(payload, record)
            op["fee_id"] = identifier
            return {"fee": record, "replayed": False}

    def query(self, operation_id, *, key):
        with (
            contract._lock(self.store, self.control.document.admission.task_id),
            self.store.transaction() as payload,
        ):
            actual, q = self._fresh()
            contract.require(digest(actual) == digest(payload))
            op = payload["media"][contract.OPERATION + ":" + operation_id]
            contract.require(
                op["activation_id"] == self.activation_id
                and op["status"] == "accepted"
                and op["fee_id"] is not None
            )
            existing = [
                r
                for r in contract.records(payload, contract.QUERY)
                if r["operation_id"] == operation_id
            ]
            old = next((r for r in existing if r["idempotency_key"] == key), None)
            if old is not None:
                identifier = old["id"]
            else:
                authority = contract.candidate_gate(payload, self.activation_id)
                contract.require(len(existing) < authority["policy"]["max_queries"])
                identifier = uuid.uuid4().hex
                source = op["descriptor"]["comparison_source"]
                request = make_request(
                    authority,
                    source,
                    identifier=identifier,
                    parent=op["descriptor"],
                    submit_raw=op["raw"],
                    query_index=len(existing) + 1,
                )
                record = {
                    "schema_version": 1,
                    "kind": contract.QUERY,
                    "id": identifier,
                    "scope_id": q["scope_id"],
                    "task_id": q["task_id"],
                    "created_at": utc_now(),
                    "activation_id": self.activation_id,
                    "idempotency_key": key,
                    "operation_id": operation_id,
                    "descriptor": request.frozen(),
                    "status": "reserved",
                    "capture_started_at": None,
                    "raw": None,
                    "receipt": None,
                    "query_index": len(existing) + 1,
                }
                contract._put(payload, record)
        return self._receive_completed_result(self._dispatch(contract.QUERY, identifier))

    def _receive_completed_result(self, result):
        query = result["query"]
        if query["status"] != "completed":
            return result
        payload = self.store.read_only()
        operation = payload["media"][contract.OPERATION + ":" + query["operation_id"]]
        if "persistence_activation_id" not in operation:
            return result
        from core.execution.media_persistence import MediaPersistenceExecution

        receive = MediaPersistenceExecution(self.control, operation["persistence_activation_id"])
        return {**result, "media_persistence": receive.receive_completed(operation["id"], query["id"])}

    def record_artifact(self, operation_id, query_id, image_bytes):
        with (
            contract._lock(self.store, self.control.document.admission.task_id),
            self.store.transaction() as payload,
        ):
            actual, q = self._fresh()
            contract.require(digest(actual) == digest(payload))
            op = payload["media"][contract.OPERATION + ":" + operation_id]
            query = payload["media"][contract.QUERY + ":" + query_id]
            contract.require(
                op["activation_id"] == self.activation_id
                and query["operation_id"] == operation_id
                and op["fee_id"] is not None
            )
            contract.require(type(image_bytes) is bytes and len(image_bytes) <= 2 * 1048576)
            artifact = verify_delivery(
                FrozenImageRequest.from_frozen(query["descriptor"]), query["raw"], image_bytes
            )
            if "persistence_activation_id" in op:
                from core.execution.media_persistence import MediaPersistenceExecution

                reception = MediaPersistenceExecution(self.control, op["persistence_activation_id"])
                contract.require(reception.verified_saved_bytes(payload, op, query, artifact) == image_bytes)
            if op["artifact_id"] is not None:
                old = payload["media"][contract.ARTIFACT + ":" + op["artifact_id"]]
                contract.require(digest(old["artifact"]) == digest(artifact))
                return {"artifact": copy.deepcopy(old), "replayed": True}
            identifier = digest({"operation_id": operation_id, "artifact": artifact})[:32]
            record = {
                "schema_version": 1,
                "kind": contract.ARTIFACT,
                "id": identifier,
                "scope_id": q["scope_id"],
                "task_id": q["task_id"],
                "created_at": utc_now(),
                "operation_id": operation_id,
                "query_id": query_id,
                "artifact": artifact,
                "bytes_base64": base64.b64encode(image_bytes).decode(),
                "purpose": contract.PURPOSE,
            }
            contract._put(payload, record)
            op["artifact_id"] = identifier
            return {"artifact": record, "replayed": False}
