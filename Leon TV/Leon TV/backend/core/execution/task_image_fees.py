"""Versioned native task-quota observations; never infer a billing request ID.

The task page and account page remain separate original exports.  Their amounts
are never matched or added together.  A candidate grants no execution authority,
does not settle a bill, and cannot alter the original POST receipt.
"""
import hashlib
from decimal import Decimal
from typing import Literal

from pydantic import ConfigDict, Field, model_validator

from core.creator.store import CreatorError, digest
from core.execution.fees import (
    MAX_EXPORT_BYTES,
    FrozenFeeSource,
    parse_export,
    request_key,
    timestamp,
)
from core.execution.image_policy import FrozenImageRequest
from core.execution.image_receipts import IDENTIFIER, TASK_IDENTIFIER, decode_submit
from core.planner.protocol import Strict, parse_json
from core.planner.review import json_copy

SHA = r"^[0-9a-f]{64}$"
TASK_FIELDS = {"id", "created_at", "updated_at", "task_id", "platform", "model_name", "user_id", "group",
               "channel_id", "quota", "action", "status", "fail_reason", "result_url", "submit_time", "start_time",
               "finish_time", "progress", "properties", "data"}
INTEGER_FIELDS = {"id", "created_at", "updated_at", "user_id", "channel_id", "quota", "submit_time", "start_time", "finish_time"}


def _fail(code="FEE_EXPORT_INVALID"):
    raise CreatorError(code, "Exact native task fee evidence is unavailable or conflicting", 409)


def _require(value, code="FEE_EXPORT_INVALID"):
    if not value:
        _fail(code)


class FrozenTaskFeeSourceV1(Strict):
    """Server-owned account pin, separate from an immutable original scope."""
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)
    schema_version: Literal[1] = 1
    kind: Literal["native_image_task_fee_source_v1"] = "native_image_task_fee_source_v1"
    account_namespace: str = Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")
    fee_source_hash: str = Field(pattern=SHA)
    platform_user_id: int = Field(ge=1, le=9223372036854775807)
    account_export_hash: str = Field(pattern=SHA)
    origin: Literal["https://api.ai-tudou.net", "https://synthetic.invalid"]

    @model_validator(mode="before")
    @classmethod
    def integer_schema(cls, value):
        if type(value) is dict and "schema_version" in value:
            _require(type(value["schema_version"]) is int)
        return value

    def frozen(self):
        value = self.model_dump()
        return {**value, "task_fee_source_hash": digest(value)}


def checked_task_source(value):
    """Reconstruct the strict source; a caller cannot supply a replacement seal."""
    try:
        if isinstance(value, FrozenTaskFeeSourceV1):
            value = value.model_dump()
        _require(type(value) is dict)
        value = json_copy(value)
        seal = value.pop("task_fee_source_hash", None)
        result = FrozenTaskFeeSourceV1.model_validate(value).frozen()
        _require(seal is None or type(seal) is str and seal == result["task_fee_source_hash"])
        return result
    except CreatorError:
        raise
    except Exception:
        _fail()


def _page(raw):
    _require(type(raw) is bytes and 0 < len(raw) <= MAX_EXPORT_BYTES)
    value = parse_json(raw.decode("utf-8"))
    value = json_copy(value, limit=MAX_EXPORT_BYTES)
    _require(type(value) is dict and set(value) == {"success", "message", "data"})
    _require(value["success"] is True and type(value["message"]) is str)
    data = value["data"]
    _require(type(data) is dict and set(data) == {"page", "page_size", "total", "items"})
    _require(type(data["page"]) is int and data["page"] == 1)
    _require(type(data["page_size"]) is int and data["page_size"] == 100)
    _require(type(data["total"]) is int and 0 <= data["total"] <= 100)
    _require(type(data["items"]) is list and data["total"] == len(data["items"]))
    return data["items"]


def parse_task_export(raw):
    """Parse a complete original /api/task/self page using the observed v1 shape."""
    try:
        rows = {}
        for row in _page(raw):
            _require(type(row) is dict and set(row) == TASK_FIELDS)
            for name in INTEGER_FIELDS:
                _require(type(row[name]) is int and 0 <= row[name] <= 9223372036854775807)
            _require(row["id"] > 0 and row["user_id"] > 0)
            for name in ("task_id", "platform", "model_name", "group", "action", "status"):
                _require(type(row[name]) is str and 0 < len(row[name]) <= 512)
            _require(TASK_IDENTIFIER.fullmatch(row["task_id"]) is not None)
            for name, maximum in (("fail_reason", 8192), ("result_url", 8192), ("progress", 128)):
                _require(type(row[name]) is str and len(row[name]) <= maximum)
            _require(type(row["properties"]) is dict and type(row["data"]) is dict)
            key = row["task_id"]
            _require(key not in rows or digest(rows[key]) == digest(row), "FEE_EVIDENCE_CONFLICT")
            rows[key] = row
        return rows
    except CreatorError:
        raise
    except Exception:
        _fail()


def task_key(provider_task_id):
    """Conservative whole-journal identity, independent of a local scope."""
    _require(type(provider_task_id) is str and TASK_IDENTIFIER.fullmatch(provider_task_id) is not None)
    return "tudou-task-sha256:" + hashlib.sha256(provider_task_id.encode("utf-8")).hexdigest()


def claim_keys(candidate):
    """Derive both available identities from an already verified image candidate."""
    _require(type(candidate) is dict and candidate.get("kind") in {"image_fee_candidate_v1", "image_task_fee_candidate_v1"})
    keys = []
    if candidate.get("provider_task_id") is not None:
        keys.append(task_key(candidate["provider_task_id"]))
    if candidate.get("submit_request_id") is not None:
        value = candidate["submit_request_id"]
        _require(type(value) is str and IDENTIFIER.fullmatch(value) is not None)
        keys.append(request_key(value))
    _require(bool(keys))
    result = tuple(sorted(keys))
    if candidate["kind"] == "image_fee_candidate_v1":
        _require(candidate.get("submit_request_id") is not None
                 and candidate.get("platform_log_id") == request_key(candidate["submit_request_id"]))
    else:
        _require(candidate.get("provider_task_id") is not None
                 and candidate.get("platform_log_id") == task_key(candidate["provider_task_id"]))
    _require("claim_keys" not in candidate or type(candidate["claim_keys"]) is list and candidate["claim_keys"] == list(result))
    return result


def _account(export_bytes, source, task_source):
    _require(type(export_bytes) is bytes and 0 < len(export_bytes) <= MAX_EXPORT_BYTES)
    _require(hashlib.sha256(export_bytes).hexdigest() == task_source["account_export_hash"], "FEE_EVIDENCE_CONFLICT")
    # Existing parser checks the entire consumption page's native schema and
    # duplicate identities.  Its amounts and request keys are not used here.
    parse_export(export_bytes)
    matching = []
    for row in _page(export_bytes):
        _require(type(row["user_id"]) is int and 0 < row["user_id"] <= 9223372036854775807)
        if row["token_name"] == source.token_name and row["group"] == source.group:
            matching.append(row["user_id"])
    _require(bool(matching), "FEE_EXPORT_PENDING")
    _require(all(value == task_source["platform_user_id"] for value in matching), "FEE_EVIDENCE_CONFLICT")


def candidate_for_task(request, submit_raw, task_export_bytes, account_export_bytes, fee_source, task_source, *, observed_at):
    """Observe quota by an exact accepted native task, without inventing a POST ID."""
    try:
        _require(type(request) is FrozenImageRequest, "FEE_EVIDENCE_CONFLICT")
        request = FrozenImageRequest.from_frozen(request.frozen())
        frozen = request.frozen()
        _require(frozen["method"] == "POST", "FEE_EVIDENCE_CONFLICT")
        source = FrozenFeeSource.model_validate(fee_source.model_dump() if isinstance(fee_source, FrozenFeeSource) else fee_source)
        pinned = checked_task_source(task_source)
        _require(pinned["account_namespace"] == source.account_namespace == frozen["budget_anchor"]["account_scope_id"]
                 and pinned["fee_source_hash"] == digest(source.model_dump())
                 and pinned["origin"] == frozen["policy"]["origin"], "FEE_EVIDENCE_CONFLICT")
        receipt = decode_submit(request, submit_raw)
        _require(receipt["request_id_status"] not in {"conflicting", "invalid"}, "FEE_EVIDENCE_CONFLICT")
        _require(receipt["classification"] == "accepted" and receipt["accepted_task_id"] is not None, "FEE_EXPORT_PENDING")
        _account(account_export_bytes, source, pinned)
        rows = parse_task_export(task_export_bytes)
        _require(receipt["accepted_task_id"] in rows, "FEE_EXPORT_PENDING")
        row = rows[receipt["accepted_task_id"]]
        _require(row["user_id"] == pinned["platform_user_id"] and row["group"] == source.group
                 and row["model_name"] == frozen["policy"]["model"] and row["platform"] == "gpt-image-2"
                 and row["action"] == "generate", "FEE_EVIDENCE_CONFLICT")
        _require(row["status"] == "SUCCESS", "FEE_EXPORT_PENDING")
        _require(not row["fail_reason"], "FEE_EVIDENCE_CONFLICT")
        issued, received, observed = timestamp(frozen["permit"]["issued_at"]), timestamp(receipt["received_at"]), timestamp(observed_at)
        _require(issued <= received <= observed and issued - 5 <= row["created_at"] <= received + 5
                 and issued - 5 <= row["submit_time"] <= received + 5
                 and row["created_at"] <= row["updated_at"] <= observed + 5
                 and 0 < row["submit_time"] <= row["start_time"] <= row["finish_time"] <= row["updated_at"], "FEE_EVIDENCE_CONFLICT")
        fee = Decimal(row["quota"]) / source.quota_per_cny
        risk = Decimal(frozen["policy"]["per_submission_risk_cny"])
        result = {"schema_version": 2, "kind": "image_task_fee_candidate_v1", "proof_basis": "exact_native_task_quota",
            "operation_id": request.binding()["operation_id"], "task_id": frozen["permit"]["task_id"],
            "descriptor_hash": request.descriptor_hash, "raw_hash": receipt["raw_hash"], "raw_body_sha256": receipt["raw_body_sha256"],
            "submit_receipt_hash": receipt["receipt_hash"], "submission_classification": receipt["classification"],
            "provider_task_id": receipt["accepted_task_id"], "submit_request_id": receipt["submit_request_id"],
            "request_id_status": receipt["request_id_status"], "platform_log_id": task_key(receipt["accepted_task_id"]),
            "source_export_hash": hashlib.sha256(task_export_bytes).hexdigest(), "account_export_hash": pinned["account_export_hash"],
            "fee_source_hash": digest(source.model_dump()), "task_fee_source_hash": pinned["task_fee_source_hash"],
            "task_row_hash": digest(row), "model": frozen["policy"]["model"], "platform_units": row["quota"],
            "platform_fee_cny": format(fee, ".6f"), "observed_at": observed_at,
            "observation_kind": "platform_task_quota_observation", "per_submission_risk_cny": format(risk, ".6f"),
            "overrun": fee > risk, "stop_recommended": fee > risk, "global_claim_status": "not_claimed",
            "completion_verified": False, "provider_bill_confirmed": False, "settlement_performed": False, "risk_released": False}
        result["claim_keys"] = list(claim_keys(result))
        return {**result, "candidate_hash": digest(result)}
    except CreatorError:
        raise
    except Exception:
        _fail()


def verify_task_candidate(candidate, request, submit_raw, task_export_bytes, account_export_bytes, fee_source, task_source):
    """Exact replay of both original pages, the original receipt and frozen pins."""
    try:
        if type(candidate) is not dict:
            return False
        expected = candidate_for_task(request, submit_raw, task_export_bytes, account_export_bytes, fee_source, task_source,
                                      observed_at=candidate["observed_at"])
        return digest(expected) == digest(candidate)
    except Exception:
        return False
