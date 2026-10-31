"""Cycle-free exact fields for diagnostic records in the original media journal."""

QUOTE = "image_comparison_quote"
INTENT = "image_comparison_intent"
ACTIVATION = "image_comparison_activation"
OPERATION = "image_comparison_operation"
QUERY = "image_comparison_query"
FEE = "image_comparison_fee"
ARTIFACT = "image_comparison_artifact"
KINDS = {QUOTE, INTENT, ACTIVATION, OPERATION, QUERY, FEE, ARTIFACT}
OBS_QUOTE = "image_comparison_observation_quote"
OBS_ACTIVATION = "image_comparison_observation_activation"
OBS_KINDS = {OBS_QUOTE, OBS_ACTIVATION}
OBS_FIELDS = {OBS_QUOTE: {"disclosure", "disclosure_hash"},
              OBS_ACTIVATION: {"quote_id", "disclosure_hash", "consent"}}
COMMON = {"schema_version", "kind", "id", "scope_id", "task_id", "created_at"}
FIELDS = {
    QUOTE: {"proposal"},
    INTENT: {"quote_id", "proposal_hash", "consent", "risk_cny", "max_calls"},
    ACTIVATION: {
        "quote_id",
        "intent_id",
        "proposal_hash",
        "consent",
        "allocation_hash",
        "parent_after_sha256",
    },
    OPERATION: {
        "activation_id",
        "idempotency_key",
        "descriptor",
        "status",
        "capture_started_at",
        "raw",
        "receipt",
        "fee_id",
        "artifact_id",
        "submission_index",
        "purpose",
    },
    QUERY: {
        "activation_id",
        "idempotency_key",
        "operation_id",
        "descriptor",
        "status",
        "capture_started_at",
        "raw",
        "receipt",
        "query_index",
    },
    FEE: {"operation_id", "candidate", "export_base64"},
    ARTIFACT: {"operation_id", "query_id", "artifact", "bytes_base64", "purpose"},
}


def record_fields(record):
    fields = COMMON | FIELDS[record["kind"]]
    if record["kind"] in {QUERY, FEE, ARTIFACT} and "observation_activation_id" in record:
        fields = fields | {"observation_activation_id"}
    if (
        record["kind"] == FEE
        and record.get("candidate", {}).get("kind") == "image_task_fee_candidate_v1"
    ):
        fields = fields | {"task_fee_source", "account_export_base64"}
    return fields
