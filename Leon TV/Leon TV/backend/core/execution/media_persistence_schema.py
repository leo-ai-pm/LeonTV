"""Exact opt-in fields, separate from historical zero-followup profiles."""

QUOTE = "media_persistence_quote"
ACTIVATION = "media_persistence_activation"
RECEIVE = "media_persistence_receive"
FIELDS = {
    QUOTE: {"disclosure", "disclosure_hash"},
    ACTIVATION: {"quote_id", "disclosure_hash", "consent"},
    RECEIVE: {"persistence_activation_id", "operation_id", "query_id", "query_hash", "receipt_hash",
              "url_sha256", "deadline_at", "status", "error", "http_status", "saved", "artifact_id"},
}
KINDS = set(FIELDS)
POLICY = {"automatic_receive": True, "download_cap": 1, "max_bytes": 2 * 1048576,
          "absolute_timeout_seconds": 45, "cleanup_seconds": 5, "network_timeout_seconds": 10,
          "max_retained_bytes": 32 * 1048576, "max_retained_entries": 64,
          "new_POST_cap": 0, "new_GET_cap": 0, "additional_risk_cny": "0.000000",
          "formal_delivery_authorized": False, "retry_authorized": False}
