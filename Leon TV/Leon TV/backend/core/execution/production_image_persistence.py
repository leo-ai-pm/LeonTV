"""Formal adapter to the D47 single-intent receiver; original delivery only."""
import copy

from core.creator.store import digest
from core.execution import production_image_persistence_contract as contract
from core.execution.media_persistence import MediaPersistenceExecution
from core.execution.media_persistence_io import PrivateMediaFiles
from core.execution.production_image_persistence_schema import OPERATION_FIELD, RECEIVE


class ProductionImagePersistenceExecution(MediaPersistenceExecution):
    contract = contract
    receive_kind = RECEIVE
    operation_activation_field = OPERATION_FIELD
    registration_field = "delivery_id"

    def __init__(self, media, persistence_activation_id):
        contract.bound_control(media)
        self.media, self.store = media, media.store
        self.activation_id = persistence_activation_id

    def _current(self):
        return contract.verified_current(self.media, self.activation_id)

    def _lock(self):
        _, base = contract.bound_control(self.media)
        return self.media._lock(base.document.admission.task_id)

    def _register(self, operation, query, image):
        self.media.record_delivery(operation["task_id"], operation["id"], query["id"], image)
        payload = self.store.read_only()
        return payload["media"]["operation:" + operation["id"]]["delivery_id"]

    def verified_saved_bytes(self, payload, operation, query, delivery):
        record = contract.saved_for_delivery(payload, operation, query, delivery)
        self._current()
        with PrivateMediaFiles(self.store.root, record["id"], read_only=True) as files:
            saved, image, suffix = self._proof(files, record, query)
        contract.require(suffix == ".blob" and digest(saved) == digest(record["saved"]))
        return image

    def receive_completed(self, operation_id, query_id):
        result = super().receive_completed(operation_id, query_id)
        # The unconfigured fallback of the shared engine retains D47's public
        # names. Formal execution never exposes a diagnostic artifact alias.
        if "artifact" in result:
            result["delivery_registered"] = result.pop("artifact")
        if "artifact_id" in result:
            result["delivery_id"] = result.pop("artifact_id")
        return result

    def _summary(self, record, *, received, replayed):
        delivery_id = record["delivery_id"]
        if delivery_id is None and record["saved"] is not None:
            payload = self.store.read_only()
            operation = payload["media"]["operation:" + record["operation_id"]]
            if operation["delivery_id"] is not None:
                delivery = payload["media"]["delivery:" + operation["delivery_id"]]
                contract.require(delivery["operation_id"] == record["operation_id"] and delivery["query_id"] == record["query_id"]
                                 and digest(delivery["delivery"]) == digest(record["saved"]["artifact"]))
                delivery_id = delivery["id"]
        pointer_pending = delivery_id is not None and record["delivery_id"] is None
        return {"reception": copy.deepcopy(record), "replayed": replayed, "accepted": True, "completed": True,
                "received": received, "persisted": received, "historical_saved": record["saved"] is not None,
                "file_availability": "verified" if received is True else "unavailable" if received is False else "not_verified",
                "delivery_registered": delivery_id is not None, "delivery_id": delivery_id,
                "reception_pointer_pending": pointer_pending, "needs_input": not received or delivery_id is None or pointer_pending,
                "retry_authorized": False, "failure_cause": "unknown" if record["http_status"] == 404 else None,
                "quality": "not_reviewed", "human_acceptance": "pending"}
