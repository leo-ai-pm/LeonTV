"""Verify a frozen historical read prefix against independent server anchors.

This is not parent-run recovery or new authorization. The root caller owns the
original store/ledger checks and unique platform fee match. Historical read-tool
validation below is pure and never runs the old controller or its later tail.
"""
import base64
import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from decimal import Decimal

from core.creator.store import SHA_RE, digest
from core.planner.protocol import execute_tool
from core.planner.store import check_source
from experiments.planner_live.chat_receipt import SCOPE, decode_chat_receipt
from experiments.planner_live.empirical_capture import FrozenEmpiricalDescriptor
from experiments.tudou_ab.ledger import fingerprint

PROOF_FIELDS = {"source", "initial_context", "descriptor", "raw", "intent", "summary", "step", "charge", "inspection"}
ANCHOR_FIELDS = {"proof_hash", "parent_operation", "parent_task_id", "source_snapshot_hash", "rules_hash", "protocol_sha256", "authorization_hash"}
INSPECTION_FIELDS = {"operation_id", "status", "candidate_count", "binding_verified", "isolated", "recovery_performed",
    "notification_present", "body_sha256", "response_received_at", "durable_confirmation_at", "original_deadline_at", "observer_until"}
STEP_FIELDS = {"after_charge_status", "charge_sha256", "intent_sha256", "key", "output_limit_observed",
    "raw_body_sha256", "read_after", "read_before", "receipt_sha256", "status", "summary_sha256", "tool_result"}
CHARGE_FIELDS = {"completion_tokens", "formal_invoice_confirmed", "group", "http_request_id", "key", "matching", "model",
    "native_quota", "operation", "platform_fee_cny", "production_normalizer_settled", "prompt_tokens", "provider_log_id",
    "provider_response_id", "provider_time", "raw_body_sha256", "source_export", "source_export_sha256", "token_name", "unit",
    "within_operation_risk_allocation"}
MAX_PROOF_BYTES = 8 * 1048576
MAX_CONTEXT_BYTES = 1048576


class ReadContextProofError(ValueError):
    def __init__(self):
        self.code = "EMPIRICAL_READ_CONTEXT_REJECTED"
        super().__init__(self.code)


def require(value):
    if not value:
        raise ReadContextProofError() from None


def timestamp(value):
    require(type(value) is str and len(value) <= 64)
    result = datetime.fromisoformat(value)
    require(result.tzinfo is not None)
    return result


def sha(value):
    require(type(value) is str and SHA_RE.fullmatch(value) is not None)


def no_thought_fields(value):
    if type(value) is dict:
        return "reasoning_content" not in value and all(no_thought_fields(item) for item in value.values())
    if type(value) is list:
        return all(no_thought_fields(item) for item in value)
    return True


@dataclass(frozen=True)
class VerifiedReadContext:
    context_json: str = field(repr=False)
    context_hash: str
    proof_hash: str
    anchors_hash: str
    source_snapshot_hash: str
    read_operation_key: str
    parent_task_id: str

    def messages(self):
        """Return a fresh context; this object alone is not an authority token."""
        return json.loads(self.context_json)

    def frozen(self):
        return {"version": 1, "kind": "verified_empirical_read_prefix", **asdict(self)}


def verify_read_context(proof: dict, expected: dict) -> VerifiedReadContext:
    """Revalidate original successful history; never trust a saved read bool.

    expected must come from the root's independently frozen original baselines,
    not from self-reported proof hashes. Re-run this function on reopening.
    """
    try:
        require(type(proof) is dict and set(proof) == PROOF_FIELDS)
        require(type(expected) is dict and set(expected) == ANCHOR_FIELDS)
        expected = dict(expected)
        for name in ANCHOR_FIELDS - {"parent_operation", "parent_task_id"}:
            sha(expected[name])
        for name in ("parent_operation", "parent_task_id"):
            require(type(expected[name]) is str and 0 < len(expected[name]) <= 256)
        encoded = json.dumps(proof, ensure_ascii=False, allow_nan=False)
        require(len(encoded.encode("utf-8")) <= MAX_PROOF_BYTES)
        # Detach every nested input before validation and context construction.
        proof = json.loads(encoded)
        require(digest(proof) == expected["proof_hash"])
        source = proof["source"]
        check_source(source)
        require(source["snapshot_hash"] == expected["source_snapshot_hash"])
        descriptor = FrozenEmpiricalDescriptor.from_frozen(proof["descriptor"])
        parent = descriptor.intent
        require(parent.scope == SCOPE and parent.operation_key == expected["parent_operation"] and parent.task_id == expected["parent_task_id"])
        require(parent.source_snapshot_hash == source["snapshot_hash"] and parent.rules_hash == expected["rules_hash"]
            and parent.authorization_hash == expected["authorization_hash"])
        context = proof["initial_context"]
        require(type(context) is list and len(context) == 2 and no_thought_fields(context))
        require(all(type(message) is dict and set(message) == {"role", "content"} for message in context))
        require([message["role"] for message in context] == ["system", "user"])
        require(context == json.loads(descriptor.context_json) and digest(context) == parent.context_hash)
        require(type(context[1]["content"]) is dict and context[1]["content"].get("task_id") == parent.task_id
            and context[1]["content"].get("source_snapshot_hash") == source["snapshot_hash"])

        original = proof["intent"]
        require(type(original) is dict)
        pairs = {"operation": parent.operation_key, "capture_id": parent.capture_id, "issued_at": parent.issued_at,
            "deadline_at": parent.deadline_at, "source_snapshot_hash": source["snapshot_hash"],
            "rules_sha256": parent.rules_hash, "protocol_sha256": expected["protocol_sha256"],
            "context_sha256": fingerprint(context), "wire_sha256": hashlib.sha256(descriptor.wire.encode("utf-8")).hexdigest(),
            "body_sha256": fingerprint(json.loads(descriptor.wire)),
            "endpoint_sha256": hashlib.sha256(parent.endpoint.encode("utf-8")).hexdigest(),
            "model_sha256": hashlib.sha256(parent.model.encode("utf-8")).hexdigest()}
        require(all(original.get(name) == value for name, value in pairs.items()))
        sha(original["plan_sha256"])
        require(type(original.get("key")) is str and 0 < len(original["key"]) <= 256)
        raw = proof["raw"]
        decoded = decode_chat_receipt(raw, expected_model=parent.model, max_output_tokens=parent.max_output_tokens, scope=SCOPE)
        require(decoded["output_limit_observed"] is True)
        body_sha = hashlib.sha256(base64.b64decode(raw["body_base64"], validate=True)).hexdigest()
        inspection = proof["inspection"]
        require(type(inspection) is dict and set(inspection) == INSPECTION_FIELDS)
        require(inspection["operation_id"] == parent.capture_id and inspection["status"] == "candidate_found")
        require(type(inspection["candidate_count"]) is int and inspection["candidate_count"] == 1)
        require(all(inspection[name] is True for name in ("binding_verified", "isolated", "notification_present")))
        require(inspection["recovery_performed"] is False and inspection["body_sha256"] == body_sha)
        received = timestamp(raw["received_at"])
        require(timestamp(inspection["response_received_at"]) == received)
        require(timestamp(inspection["original_deadline_at"]) == timestamp(parent.deadline_at))
        observer = timestamp(inspection["observer_until"])
        deadline = timestamp(parent.deadline_at)
        require(observer <= deadline)
        require(timestamp(parent.issued_at) <= timestamp(descriptor.allocation.begun_at) <= received
            <= timestamp(inspection["durable_confirmation_at"]) < min(observer, deadline))

        summary, step, charge = proof["summary"], proof["step"], proof["charge"]
        require(type(summary) is dict and summary.get("state") == "received")
        summary_pairs = {"key": original["key"], "operation": parent.operation_key, "model_requested": parent.model,
            "model_returned": decoded["parsed"]["model"], "wire_sha256": pairs["wire_sha256"], "usage": decoded["usage"],
            "provider_request_id": decoded["provider_request_id"], "provider_response_id": decoded["provider_response_id"]}
        require(all(name in summary and summary[name] == value for name, value in summary_pairs.items()))
        if "http_status" in summary:
            require(type(summary["http_status"]) is int and summary["http_status"] == 200)
        for name, value in (("capture_id", parent.capture_id), ("original_deadline_at", parent.deadline_at),
            ("output_parameter", parent.chat_output_parameter)):
            if name in summary:
                require(summary[name] == value)
        if "requested_output_limit" in summary:
            require(type(summary["requested_output_limit"]) is int and summary["requested_output_limit"] == parent.max_output_tokens)
        require(type(step) is dict and set(step) == STEP_FIELDS and step["status"] == "processed"
            and step["read_before"] is False and step["read_after"] is True and step["output_limit_observed"] is True)
        require(step["key"] == original["key"] and type(step["after_charge_status"]) is str)
        require(type(charge) is dict and set(charge) == CHARGE_FIELDS)
        charge_pairs = {"key": original["key"], "operation": parent.operation_key, "model": decoded["parsed"]["model"],
            "provider_response_id": decoded["provider_response_id"], "http_request_id": decoded["provider_request_id"],
            "raw_body_sha256": body_sha, "unit": "export fee(CNY)",
            "matching": "unique token/model/group/usage/time tuple, separate HTTP, body and platform-log IDs"}
        require(all(charge[name] == value for name, value in charge_pairs.items()))
        require(charge["within_operation_risk_allocation"] is True and charge["formal_invoice_confirmed"] is False
            and charge["production_normalizer_settled"] is False)
        for name in ("prompt_tokens", "completion_tokens"):
            require(type(charge[name]) is int and charge[name] == decoded["usage"][name])
        sha(charge["source_export_sha256"])
        for name in ("provider_log_id", "provider_time", "source_export", "token_name", "group"):
            require(type(charge[name]) is str and 0 < len(charge[name]) <= 512)
        require(type(charge["platform_fee_cny"]) is str)
        amount = Decimal(charge["platform_fee_cny"])
        require(amount.is_finite() and 0 <= amount <= Decimal(descriptor.allocation.risk_fen) / 100)
        for name, record in (("intent_sha256", original), ("summary_sha256", summary), ("receipt_sha256", decoded["parsed"]), ("charge_sha256", charge)):
            require(step.get(name) == fingerprint(record))
        require(step.get("raw_body_sha256") == body_sha)
        feedback, plan, was_read = execute_tool(decoded["content"], source, False, detailed_feedback=True)
        require(plan is None and was_read is True and feedback.get("ok") is True and feedback.get("tool") == "read_frozen_brief")
        require(step.get("tool_result") == feedback and no_thought_fields(feedback))
        prefix = json.loads(descriptor.context_json) + [{"role": "assistant", "content": decoded["content"]}, {"role": "tool", "content": feedback}]
        context_json = json.dumps(prefix, ensure_ascii=False, allow_nan=False)
        require(len(context_json.encode("utf-8")) <= MAX_CONTEXT_BYTES)
        return VerifiedReadContext(context_json, digest(prefix), expected["proof_hash"], digest(expected),
            source["snapshot_hash"], parent.operation_key, parent.task_id)
    except ReadContextProofError:
        raise
    except Exception:
        raise ReadContextProofError() from None
