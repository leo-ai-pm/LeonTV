"""Exact image fee candidates; no financial claims, settlement or risk release."""
import hashlib
from decimal import Decimal

from core.creator.store import CreatorError, digest
from core.execution.fees import FrozenFeeSource, parse_export, request_key, timestamp
from core.execution.image_receipts import decode_submit


def _fail(code):
    raise CreatorError(code, "Exact image platform fee evidence is unavailable or conflicting", 409)


def candidate_for(request, submit_raw, export_bytes, source, *, observed_at):
    """Match only an actual POST request identifier, never time or task ID."""
    try:
        source = FrozenFeeSource.model_validate(source.model_dump() if isinstance(source, FrozenFeeSource) else source)
    except Exception:
        _fail("FEE_EXPORT_INVALID")
    receipt = decode_submit(request, submit_raw)
    if receipt["request_id_status"] == "conflicting":
        _fail("FEE_EXPORT_AMBIGUOUS")
    if receipt["submit_request_id"] is None:
        _fail("FEE_EXPORT_PENDING")
    rows = parse_export(export_bytes)
    key = request_key(receipt["submit_request_id"])
    if key not in rows:
        _fail("FEE_EXPORT_PENDING")
    row = rows[key]
    if row["type"] != 2:
        _fail("FEE_EXPORT_PENDING")
    frozen = request.frozen()
    policy, permit = frozen["policy"], frozen["permit"]
    try:
        issued, received, observed = timestamp(permit["issued_at"]), timestamp(receipt["received_at"]), timestamp(observed_at)
        if not (issued <= received <= observed and issued - 5 <= row["created_at"] <= received + 5
                and row["request_id"] == receipt["submit_request_id"] and row["token_name"] == source.token_name
                and row["group"] == source.group and row["model_name"] == policy["model"]):
            _fail("FEE_EVIDENCE_CONFLICT")
        fee = Decimal(row["quota"]) / source.quota_per_cny
        risk = Decimal(policy["per_submission_risk_cny"])
        result = {"schema_version": 1, "kind": "image_fee_candidate_v1",
                  "operation_id": request.binding()["operation_id"], "task_id": permit["task_id"],
                  "descriptor_hash": request.descriptor_hash, "raw_hash": receipt["raw_hash"],
                  "raw_body_sha256": receipt["raw_body_sha256"], "submit_receipt_hash": receipt["receipt_hash"],
                  "submission_classification": receipt["classification"], "provider_task_id": receipt["accepted_task_id"],
                  "submit_request_id": receipt["submit_request_id"], "platform_log_id": key,
                  "source_export_hash": hashlib.sha256(export_bytes).hexdigest(),
                  "fee_source_hash": digest(source.model_dump()), "model": policy["model"],
                  "platform_units": row["quota"], "platform_fee_cny": format(fee, ".6f"),
                  "observed_at": observed_at, "observation_kind": "platform_fee_observation",
                  "per_submission_risk_cny": format(risk, ".6f"), "overrun": fee > risk,
                  "stop_recommended": fee > risk, "global_claim_status": "not_claimed",
                  "completion_verified": False, "settlement_performed": False, "risk_released": False}
        return {**result, "candidate_hash": digest(result)}
    except CreatorError:
        raise
    except Exception:
        _fail("FEE_EVIDENCE_CONFLICT")


def verify_candidate(candidate, request, submit_raw, export_bytes, source):
    """Pure exact replay. Repeated verification neither claims nor spends."""
    try:
        if type(candidate) is not dict:
            return False
        expected = candidate_for(request, submit_raw, export_bytes, source, observed_at=candidate["observed_at"])
        return digest(expected) == digest(candidate)
    except Exception:
        return False
