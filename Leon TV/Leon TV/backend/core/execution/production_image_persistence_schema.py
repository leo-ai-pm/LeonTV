"""Prospective formal-image permission and receipt evidence, in original media."""
from core.execution.media_persistence_schema import POLICY as IMAGE_RECEIVE_POLICY

QUOTE = "production_image_persistence_quote"
ACTIVATION = "production_image_persistence_activation"
RECEIVE = "production_image_persistence_receive"
FIELDS = {
    QUOTE: {"disclosure", "disclosure_hash"},
    ACTIVATION: {"quote_id", "disclosure_hash", "consent"},
    RECEIVE: {"persistence_activation_id", "operation_id", "query_id", "query_hash", "receipt_hash",
              "url_sha256", "deadline_at", "status", "error", "http_status", "saved", "delivery_id"},
}
KINDS = set(FIELDS)
POLICY = {**IMAGE_RECEIVE_POLICY, "formal_delivery_authorized": True}
OPERATION_FIELD = "production_persistence_activation_id"
