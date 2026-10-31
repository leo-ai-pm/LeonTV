"""Completed receipt -> one durable reception -> local bytes -> native artifact."""
import copy
import hashlib
import os
import subprocess
import uuid
from contextlib import ExitStack
from datetime import timedelta

from core.creator.store import CreatorError, digest, utc_now
from core.execution import image_comparison as base
from core.execution import media_persistence_contract as contract
from core.execution.image_control import LoadedImageControl
from core.execution.image_policy import FrozenImageRequest, timestamp
from core.execution.image_receipts import verify_delivery
from core.execution.media_persistence_io import PrivateMediaFiles, launch_receive, target
from core.execution.media_persistence_schema import POLICY, RECEIVE
from core.execution.policy import now
from core.execution.store import ExecutionStore
from core.planner.protocol import parse_json


def intent_hash(record):
    return digest({k: v for k, v in record.items() if k not in {"status", "error", "http_status", "saved", "artifact_id"}})


class MediaPersistenceExecution:
    def __init__(self, control, persistence_activation_id):
        contract.require(type(control) is LoadedImageControl)
        self.control, self.activation_id = control, persistence_activation_id
        self.store = ExecutionStore(control.roots["execution"])

    def _bound(self, payload, operation_id, query_id):
        op, query, receipt = contract.completed(payload, operation_id, query_id)
        contract.require(op["persistence_activation_id"] == self.activation_id)
        return op, query, receipt

    def _save_state(self, identifier, **values):
        with self.store.transaction() as payload:
            actual, _ = contract.verified_current(self.control, self.activation_id)
            contract.require(digest(actual) == digest(payload))
            record = payload["media"][RECEIVE + ":" + identifier]
            record.update(values)
            return copy.deepcopy(record)

    def _proof(self, files, record, query):
        proof_raw = files.read(".ready.json", 16384)
        value = parse_json(proof_raw.decode("utf-8"))
        contract.require(type(value) is dict and set(value) == {"intent_hash", "query_hash", "receipt_hash", "saved"}
                         and value["intent_hash"] == intent_hash(record) and value["query_hash"] == record["query_hash"]
                         and value["receipt_hash"] == record["receipt_hash"])
        saved = value["saved"]
        confirmation = parse_json(files.read(".commit.json", 2048).decode("utf-8"))
        contract.require(type(confirmation) is dict and set(confirmation) == {"intent_hash", "proof_sha256", "confirmed_at", "confirmation_hash"}
                         and confirmation["confirmation_hash"] == digest({k: v for k, v in confirmation.items() if k != "confirmation_hash"})
                         and confirmation["intent_hash"] == intent_hash(record) and confirmation["proof_sha256"] == hashlib.sha256(proof_raw).hexdigest())
        contract.require(type(saved) is dict and set(saved) == contract.SAVED - {"durable_at"})
        saved = {**saved, "durable_at": confirmation["confirmed_at"]}
        contract.require(timestamp(record["created_at"]) <= timestamp(saved["network_started_at"]) <= timestamp(saved["saved_at"]) <= timestamp(record["deadline_at"])
                         and timestamp(saved["saved_at"]) <= timestamp(saved["durable_at"]) <= timestamp(record["deadline_at"]) - timedelta(seconds=POLICY["cleanup_seconds"])
                         and (timestamp(saved["durable_at"]) - timestamp(saved["network_started_at"])).total_seconds() <= POLICY["absolute_timeout_seconds"])
        image, suffix = files.read_payload(POLICY["max_bytes"])
        artifact = verify_delivery(FrozenImageRequest.from_frozen(query["descriptor"]), query["raw"], image)
        contract.require(saved["sha256"] == hashlib.sha256(image).hexdigest() and saved["byte_length"] == len(image)
                         and digest(saved["artifact"]) == digest(artifact))
        return saved, image, suffix

    def _receive_new(self, record, query, receipt):
        """Only the caller that durably created the intent may enter this."""
        identifier = record["id"]
        try:
            # A new complete freshness boundary after intent commit, immediately
            # before process launch. It cannot reuse a transaction's old proof.
            payload, _ = contract.verified_current(self.control, self.activation_id)
            _, current, current_receipt = self._bound(payload, record["operation_id"], record["query_id"])
            actual = payload["media"][RECEIVE + ":" + identifier]
            contract.require(digest(actual) == digest(record) and digest(current) == digest(query)
                             and digest(receipt) == digest(current_receipt)
                             and now() + timedelta(seconds=POLICY["absolute_timeout_seconds"] + POLICY["cleanup_seconds"]) < timestamp(record["deadline_at"]))
            target(receipt["result_url"])
            with PrivateMediaFiles(self.store.root, identifier) as files:
                with ExitStack() as descriptors:
                    fd = files.new_part()
                    descriptors.callback(os.close, fd)
                    proof_fd = files.new_proof_fd()
                    descriptors.callback(os.close, proof_fd)
                    confirmation_fd = files.new_confirmation_fd()
                    descriptors.callback(os.close, confirmation_fd)
                    started = utc_now()
                    result = launch_receive(query, record, fd, proof_fd, confirmation_fd, started_at=started)
                if not result["ok"]:
                    error = result.get("error")
                    if error not in {"INVALID_BYTES", "HTTP_NOT_200", "TIMEOUT", "RESOURCE_LIMIT", "TRUNCATED_RESPONSE", "NETWORK_OR_WORKER"}:
                        error = "NETWORK_OR_WORKER"
                    return self._save_state(identifier, status="unknown" if error in {"TIMEOUT", "NETWORK_OR_WORKER"} else "failed",
                                            error=error,
                                            http_status=result.get("http_status"))
                saved, _, _ = self._proof(files, record, query)
                contract.verified_current(self.control, self.activation_id)
                files.publish()
            return self._save_state(identifier, status="saved", saved=saved, error=None, http_status=200)
        except Exception as exc:
            # Includes process timeout, source/lease drift, and local IO errors.
            # Never reconnect. A complete proof+file can be recovered locally.
            error = "CAPACITY_EXCEEDED" if isinstance(exc, CreatorError) and exc.code == "MEDIA_STORAGE_CAPACITY" else "TIMEOUT" if isinstance(exc, (TimeoutError, subprocess.TimeoutExpired)) else "LOCAL_STORAGE" if isinstance(exc, OSError) else "NETWORK_OR_WORKER"
            return self._save_state(identifier, status="failed" if error == "CAPACITY_EXCEEDED" else "unknown", error=error)

    def receive_completed(self, operation_id, query_id):
        try:
            return self._receive_completed(operation_id, query_id)
        except Exception:
            # No compensating write when freshness/lease/storage is unavailable.
            # Preserve the original completed task and whatever durable intent
            # already exists; do not publish or create proof files on this path.
            payload = self.store.read_only()
            self._bound(payload, operation_id, query_id)
            found = [r for r in base.records(payload, RECEIVE) if r["operation_id"] == operation_id]
            if found:
                result = self._summary(found[0], received=None, replayed=True)
            else:
                result = {"reception": None, "accepted": True, "completed": True, "received": False,
                          "artifact": False, "needs_input": True, "retry_authorized": False,
                          "quality": "not_reviewed", "human_acceptance": "pending"}
            return {**result, "received": None, "persisted": None, "needs_input": True,
                    "file_availability": "not_verified", "current_authority_valid": False,
                    "reception_error": "AUTHORITY_OR_STORAGE_UNAVAILABLE"}

    def verified_saved_bytes(self, payload, operation, query, artifact):
        """Read-only proof gate used by the original artifact writer too."""
        record = contract.saved_for_artifact(payload, operation, query, artifact)
        contract.verified_current(self.control, self.activation_id)
        with PrivateMediaFiles(self.store.root, record["id"]) as files:
            saved, image, suffix = self._proof(files, record, query)
        contract.require(suffix == ".blob" and digest(saved) == digest(record["saved"]))
        return image

    def _receive_completed(self, operation_id, query_id):
        with base._lock(self.store, self.control.document.admission.task_id):
            with self.store.transaction() as current:
                # One committed decision snapshot under the original task/store
                # locks. No preliminary full reads, mutable-payload permission,
                # or reuse across the later worker/publication boundaries.
                actual, d = contract.verified_current(self.control, self.activation_id)
                contract.require(digest(actual) == digest(current))
                op, query, receipt = self._bound(current, operation_id, query_id)
                found = [r for r in base.records(current, RECEIVE) if r["operation_id"] == operation_id]
                replayed = bool(found)
                if not found:
                    contract.require(now() + timedelta(seconds=120) < timestamp(d["valid_until"]))
                    record = {"schema_version": 1, "kind": RECEIVE, "id": uuid.uuid4().hex,
                              "scope_id": op["scope_id"], "task_id": op["task_id"], "created_at": utc_now(),
                              "persistence_activation_id": self.activation_id, "operation_id": operation_id,
                              "query_id": query_id, "query_hash": digest(query), "receipt_hash": receipt["receipt_hash"],
                              "url_sha256": receipt["result_url_sha256"], "deadline_at": d["valid_until"],
                              "status": "receiving", "error": None, "http_status": None, "saved": None, "artifact_id": None}
                    base._put(current, record)
                else:
                    contract.require(len(found) == 1 and found[0]["query_id"] == query_id
                                     and found[0]["persistence_activation_id"] == self.activation_id)
                    record = copy.deepcopy(found[0])
            if not replayed:
                record = self._receive_new(record, query, receipt)
            if record["status"] in {"receiving", "unknown", "saved"}:
                try:
                    with PrivateMediaFiles(self.store.root, record["id"]) as files:
                        saved, image, suffix = self._proof(files, record, query)
                        if suffix == ".part":
                            contract.verified_current(self.control, self.activation_id)
                            files.publish()
                        elif files.exists(".part"):
                            contract.verified_current(self.control, self.activation_id)
                            files.finish_link()
                    if record["saved"] is None:
                        record = self._save_state(record["id"], status="saved", saved=saved, error=None, http_status=200)
                except Exception:
                    if record["status"] == "receiving":
                        record = self._save_state(record["id"], status="unknown", error="INTERRUPTED_MISSING_BYTES")
                    return self._summary(record, received=False, replayed=replayed)
            else:
                return self._summary(record, received=False, replayed=replayed)
        # The existing artifact method owns the task lock independently. No
        # nested lock, and a concurrent registrar can only replay exact bytes.
        try:
            contract.verified_current(self.control, self.activation_id)
            from core.execution.image_comparison_runtime import ComparisonExecution

            result = ComparisonExecution(self.control, op["activation_id"]).record_artifact(operation_id, query_id, image)
            with base._lock(self.store, self.control.document.admission.task_id):
                record = self._save_state(record["id"], artifact_id=result["artifact"]["id"])
        except Exception:
            # A registration error may itself mean that the file disappeared
            # or changed. Recheck evidence without creating/unlinking anything;
            # historical saved metadata alone is never current availability.
            available = False
            try:
                with PrivateMediaFiles(self.store.root, record["id"], read_only=True) as files:
                    _, _, suffix = self._proof(files, record, query)
                available = suffix == ".blob"
            except Exception:
                pass
            return {**self._summary(record, received=available, replayed=replayed), "registration_error": True}
        return self._summary(record, received=True, replayed=replayed)

    def _summary(self, record, *, received, replayed):
        artifact_id = record["artifact_id"]
        if artifact_id is None and record["saved"] is not None:
            payload = self.store.read_only()
            operation = payload["media"][base.OPERATION + ":" + record["operation_id"]]
            if operation["artifact_id"] is not None:
                artifact = payload["media"][base.ARTIFACT + ":" + operation["artifact_id"]]
                contract.require(artifact["operation_id"] == record["operation_id"] and artifact["query_id"] == record["query_id"]
                                 and digest(artifact["artifact"]) == digest(record["saved"]["artifact"]))
                artifact_id = artifact["id"]
        pointer_pending = artifact_id is not None and record["artifact_id"] is None
        return {"reception": copy.deepcopy(record), "replayed": replayed, "accepted": True, "completed": True,
                "received": received, "persisted": received, "historical_saved": record["saved"] is not None,
                "file_availability": "verified" if received is True else "unavailable" if received is False else "not_verified",
                "artifact": artifact_id is not None, "artifact_id": artifact_id, "reception_pointer_pending": pointer_pending,
                "needs_input": not received or artifact_id is None or pointer_pending, "retry_authorized": False,
                "failure_cause": "unknown" if record["http_status"] == 404 else None,
                "quality": "not_reviewed", "human_acceptance": "pending"}
