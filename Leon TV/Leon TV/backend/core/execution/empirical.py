"""Explicit empirical-risk authority over the existing execution/capture chain.

Risk allocation is neither a billing bound nor a statement of actual spend.
The server owns parent anchors and platform observations; HTTP clients cannot
write either. Original formal policies and normalization remain independent.
"""
import base64
import copy
import hashlib
import re
from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from core.creator.store import CreatorError, digest
from core.execution.policy import cost, money, protocol_endpoint_matches, units
from core.execution.receipts import ReceiptCaptureSettings, ReceiptStore
from core.planner.protocol import Strict
from experiments.planner_live.chat_receipt import SCOPE as DECODE_SCOPE
from experiments.planner_live.chat_receipt import inspect_chat_receipt
from experiments.planner_live.empirical_capture import (
    EmpiricalCaptureAdapter,
    EmpiricalIntent,
    FrozenEmpiricalDescriptor,
    RiskAllocation,
)

KIND = "empirical_risk_v1"
SCOPE = "frozen_brief_and_bounded_roles_empirical_risk_to_named_provider"
RISK_BASIS = "empirical_not_hard_billing_bound"
AMOUNT = r"(?:0|[1-9][0-9]{0,6})(?:\.[0-9]{1,6})?"
SHA = r"^[0-9a-f]{64}$"


def active(policy):
    return policy is not None and policy.get("kind") == KIND


def require(value):
    if not value:
        raise CreatorError("CORRUPT_STORAGE", "Empirical execution evidence failed integrity checks")


class BudgetAnchor(Strict):
    schema_version: Literal[1] = 1
    account_scope_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")
    parent_authorization_hash: str = Field(pattern=SHA)
    parent_ledger_hash: str = Field(pattern=SHA)
    parent_allocation_hash: str = Field(pattern=SHA)
    original_budget_cny: str
    opening_exposure_cny: str
    opening_call_count: int = Field(ge=0)
    scope_risk_cny: str
    scope_max_calls: int = Field(ge=1, le=6)
    account_limit_cny: str
    account_max_calls: int = Field(ge=1)
    currency: Literal["CNY"] = "CNY"

    @model_validator(mode="after")
    def checked(self):
        for value in (self.original_budget_cny, self.opening_exposure_cny, self.scope_risk_cny, self.account_limit_cny):
            if type(value) is not str or not re.fullmatch(AMOUNT, value):
                raise ValueError("Explicit empirical parent amounts required")
        if units(self.scope_risk_cny) <= 0 or units(self.opening_exposure_cny) + units(self.scope_risk_cny) != units(self.account_limit_cny) or units(self.account_limit_cny) > units(self.original_budget_cny):
            raise ValueError("Parent risk envelope must fit the original authorization")
        if self.opening_call_count + self.scope_max_calls != self.account_max_calls:
            raise ValueError("Parent cumulative call anchor required")
        return self

    def frozen(self):
        result = self.model_dump()
        return result | {"parent_anchor_hash": digest(result)}


class EmpiricalExecutionPolicy(Strict):
    kind: Literal["empirical_risk_v1"] = KIND
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")
    version: str = Field(min_length=1, max_length=80)
    provider: Literal["openai_compatible"] = "openai_compatible"
    model: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}$")
    endpoint: str
    wire_api: Literal["chat_completions"] = "chat_completions"
    chat_output_parameter: Literal["max_tokens", "max_completion_tokens"]
    role_workflow: Literal["bounded_revision_v1"] = "bounded_revision_v1"
    currency: Literal["CNY"] = "CNY"
    privacy_scope: Literal["frozen_brief_and_bounded_roles_empirical_risk_to_named_provider"] = SCOPE
    risk_basis: Literal["empirical_not_hard_billing_bound"] = RISK_BASIS
    per_call_risk_cny: Literal["1.000000"] = "1.000000"
    parent_anchor_hash: str = Field(pattern=SHA)
    input_cny_per_million: str
    output_cny_per_million: str
    max_output_tokens: int = Field(ge=1, le=8192)
    max_input_bytes: int = Field(ge=256, le=1048576)
    max_output_bytes: int = Field(ge=128, le=65536)
    request_deadline_seconds: int = Field(ge=1, le=120)
    max_calls: int = Field(ge=1, le=6)
    task_limit_cny: str
    account_limit_cny: str
    account_max_calls: int = Field(ge=1)
    account_scope_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")
    valid_until: str
    pricing_basis: str = Field(min_length=1, max_length=500)
    operator_risk_approved: Literal[True]
    validation_only: bool

    @model_validator(mode="after")
    def checked(self):
        if not protocol_endpoint_matches(self.model_dump()) or datetime.fromisoformat(self.valid_until).tzinfo is None:
            raise ValueError("Explicit empirical Chat endpoint and expiry required")
        for value in (self.input_cny_per_million, self.output_cny_per_million, self.task_limit_cny, self.account_limit_cny):
            if type(value) is not str or not re.fullmatch(AMOUNT, value):
                raise ValueError("Explicit empirical amounts required")
        if units(self.task_limit_cny) <= 0 or units(self.account_limit_cny) < units(self.task_limit_cny):
            raise ValueError("Explicit risk ceilings required")
        return self

    def frozen(self):
        result = self.model_dump()
        return result | {"policy_hash": digest(result)}


def checked_anchor(value, policy):
    require(type(value) is dict)
    checked = BudgetAnchor.model_validate({key: item for key, item in value.items() if key != "parent_anchor_hash"}).frozen()
    require(digest(checked) == digest(value) and value["parent_anchor_hash"] == policy["parent_anchor_hash"])
    require(value["account_scope_id"] == policy["account_scope_id"] and value["account_max_calls"] == policy["account_max_calls"])
    require(units(value["account_limit_cny"]) == units(policy["account_limit_cny"]))
    require(units(value["scope_risk_cny"]) == units(policy["task_limit_cny"]) and value["scope_max_calls"] == policy["max_calls"])
    return checked


def guarded(callback, *values):
    if not callable(callback):
        return False
    try:
        detached = copy.deepcopy(values)
        before = digest(detached)
        result = callback(*detached)
        return type(result) is bool and result and before == digest(detached)
    except Exception:
        return False


def initial():
    return {"fee_status": "awaiting_receipt", "receipt_classification": None, "output_limit_observed": None,
            "fee_evidence": None, "platform_units": None, "capture_descriptor": None, "capture_binding": None}


def decode(raw, policy):
    """Private content plus safe estimates; no reasoning text escapes the decoder."""
    empty = {"provider_request_id": None, "provider_response_id": None, "input_tokens": None, "output_tokens": None,
             "finish_reason": None, "content": None, "content_hash": None, "local_units": None,
             "reasoning_output_tokens": None, "receipt_classification": "unconfirmed", "output_limit_observed": None}
    try:
        inspected = inspect_chat_receipt(raw, expected_model=policy["model"], max_output_tokens=policy["max_output_tokens"], scope=DECODE_SCOPE)
        metadata = inspected["metadata"]
        result = empty | {"provider_request_id": metadata["provider_request_id"], "provider_response_id": metadata["provider_response_id"],
                          "input_tokens": metadata["usage"]["prompt_tokens"], "output_tokens": metadata["usage"]["completion_tokens"],
                          "finish_reason": metadata["finish_reason"], "reasoning_output_tokens": metadata["reasoning_tokens"],
                          "receipt_classification": inspected["classification"], "output_limit_observed": metadata["output_limit_observed"],
                          "local_units": cost(metadata["usage"]["prompt_tokens"], metadata["usage"]["completion_tokens"], policy)}
        if inspected["classification"] == "truncated":
            return result, "RECEIPT_TRUNCATED"
        content = inspected["decoded"]["content"]
        if len(content.encode("utf-8")) > policy["max_output_bytes"]:
            return empty, "RECEIPT_OUT_OF_BOUNDS"
        result.update(content=content, content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest())
        return result, None
    except Exception:
        return empty, "RESPONSE_UNCONFIRMED"


def descriptor_for(operation, policy, anchor):
    permit = operation["permit"]
    intent = EmpiricalIntent(capture_id=operation["id"], intent_id=operation["id"], task_id=operation["task_id"],
        operation_key=operation["id"], authorization_hash=permit["disclosure_hash"], source_snapshot_hash=operation["snapshot_hash"],
        rules_hash=permit["rules_hash"], context_hash=operation["context_hash"], wire_hash=operation["wire_hash"],
        endpoint=policy["endpoint"], model=policy["model"], chat_output_parameter=policy["chat_output_parameter"],
        max_output_tokens=policy["max_output_tokens"], issued_at=permit["issued_at"], deadline_at=permit["deadline_at"],
        request_deadline_seconds=policy["request_deadline_seconds"], environment="synthetic" if policy["validation_only"] else "empirical_live",
        detailed_tool_feedback=False)
    allocation = RiskAllocation(allocation_id=operation["id"], intent_id=operation["id"], intent_hash=intent.intent_hash,
        operation_key=operation["id"], authorization_hash=permit["disclosure_hash"], parent_ledger_hash=anchor["parent_ledger_hash"],
        begin_hash=permit["risk_begin_hash"], begun_at=permit["issued_at"])
    return FrozenEmpiricalDescriptor.build(intent, allocation, operation["context"], operation["wire"])


def observation(evidence, operation, policy):
    fields = {"schema_version", "operation_id", "wire_hash", "raw_body_sha256", "model", "provider_response_id", "provider_request_id",
              "prompt_tokens", "completion_tokens", "platform_log_id", "platform_fee_cny", "source_export_hash", "observed_at"}
    require(type(evidence) is dict and set(evidence) == fields)
    require(type(evidence["schema_version"]) is int and evidence["schema_version"] == 1)
    require(operation["raw"] is not None and operation["normalized"] is not None)
    receipt = operation["normalized"]
    require(receipt["receipt_classification"] in {"complete", "truncated"})
    require(evidence["operation_id"] == operation["id"] and evidence["wire_hash"] == operation["wire_hash"])
    require(evidence["raw_body_sha256"] == hashlib.sha256(base64.b64decode(operation["raw"]["body_base64"], validate=True)).hexdigest())
    require(evidence["model"] == policy["model"] and evidence["provider_response_id"] == receipt["provider_response_id"] and evidence["provider_request_id"] == receipt["provider_request_id"])
    require(type(evidence["prompt_tokens"]) is int and evidence["prompt_tokens"] == receipt["input_tokens"])
    require(type(evidence["completion_tokens"]) is int and evidence["completion_tokens"] == receipt["output_tokens"])
    require(type(evidence["platform_log_id"]) is str and re.fullmatch(r"[A-Za-z0-9_.:/-]{1,128}", evidence["platform_log_id"]))
    require(type(evidence["source_export_hash"]) is str and re.fullmatch(SHA, evidence["source_export_hash"]))
    require(type(evidence["platform_fee_cny"]) is str and re.fullmatch(AMOUNT, evidence["platform_fee_cny"]))
    require(type(evidence["observed_at"]) is str and datetime.fromisoformat(evidence["observed_at"]).tzinfo is not None)
    return copy.deepcopy(evidence) | {"evidence_hash": digest(evidence), "platform_units": units(evidence["platform_fee_cny"])}


def projection(operation):
    value = operation["empirical"]
    return {"accounting_kind": KIND, "parent_anchor_hash": operation["parent_anchor_hash"], "risk_cny": money(operation["hold_units"]),
            "local_estimate_cny": money(operation["local_units"]) if operation["local_units"] is not None else None,
            "platform_observed_cny": money(value["platform_units"]) if value["platform_units"] is not None else None,
            "fee_status": value["fee_status"], "receipt_classification": value["receipt_classification"], "output_limit_observed": value["output_limit_observed"]}


def ledger_projection(payload, task):
    account = payload["accounts"][task["account_id"]]
    operations = [op for op in payload["operations"].values() if op["account_id"] == task["account_id"]]
    own = [op for op in operations if op["task_id"] == task["id"]]
    def local(ops):
        return sum(op["local_units"] or 0 for op in ops)

    def known(ops, key):
        values = [op["local_units"] if key == "local" else op["empirical"]["platform_units"] for op in ops]
        return money(sum(values)) if all(value is not None for value in values) else None
    opening = units(account["anchor"]["opening_exposure_cny"])
    risk = sum(op["hold_units"] for op in own)
    shared_risk = opening + sum(op["hold_units"] for op in operations)
    return {"accounting_kind": KIND, "opening_exposure_cny": money(opening), "opening_call_count": account["anchor"]["opening_call_count"],
            "risk_allocated_cny": money(risk), "local_estimate_cny": known(own, "local"), "platform_observed_cny": known(own, "platform"),
            "account_risk_allocated_cny": money(shared_risk), "account_local_estimate_cny": known(operations, "local"), "account_platform_observed_cny": known(operations, "platform"),
            "locally_accounted_cny": money(local(own)), "account_locally_accounted_cny": money(local(operations)),
            "held_cny": money(risk), "account_held_cny": money(shared_risk)}


class EmpiricalTransport:
    """Product authority validation before the existing neutral capture adapter."""
    def __init__(self, execution_store, anchor, anchor_guard, settings):
        self.store, self.anchor, self.anchor_guard, self.settings = execution_store, anchor, anchor_guard, settings

    def perform(self, operation, policy, api_key):
        from core.execution.roles import validate_operation_tags
        from core.execution.transport import ResponseUnconfirmed, wire_for
        try:
            require(type(self.settings) is ReceiptCaptureSettings and guarded(self.anchor_guard, self.anchor))
            payload = self.store.read_only()
            require(digest(payload["operations"][operation["id"]]) == digest(operation) and operation["status"] == "submitting")
            require(digest(payload["quotes"][operation["quote_id"]]["policy"]) == digest(policy))
            require(operation["wire"] == wire_for(operation["context"], policy) and len(operation["wire"].encode("utf-8")) <= policy["max_input_bytes"])
            validate_operation_tags(operation, policy, verify_context=True)
            descriptor = descriptor_for(operation, policy, checked_anchor(self.anchor, policy))
            require(digest(descriptor.frozen()) == digest(operation["empirical"]["capture_descriptor"]))
            require(digest(descriptor.binding()) == digest(operation["empirical"]["capture_binding"]))
            return EmpiricalCaptureAdapter(self.store.root / "receipts", settings=self.settings).capture(descriptor, api_key)
        except (CreatorError, ValueError, TypeError, KeyError, AttributeError):
            raise ResponseUnconfirmed("PERMIT_REJECTED") from None


def inspect_operation(root, operation):
    return {"inspection": ReceiptStore(root / "receipts").inspect_binding(operation["id"], operation["empirical"]["capture_binding"])}


def validate_operation(operation, task, quote, grant):
    policy, permit, value = quote["policy"], operation["permit"], operation["empirical"]
    require(set(value) == set(initial()))
    require(operation["accounting_kind"] == task["accounting_kind"] == quote["accounting_kind"] == grant["accounting_kind"] == permit["accounting_kind"] == KIND)
    require(operation["parent_anchor_hash"] == task["parent_anchor_hash"] == quote["parent_anchor_hash"] == grant["parent_anchor_hash"] == permit["parent_anchor_hash"] == policy["parent_anchor_hash"])
    require(permit["risk_basis"] == RISK_BASIS and not any(key in permit for key in ("input_token_upper", "bounder_ref", "max_input_tokens")))
    require(permit["disclosure_hash"] == grant["disclosure_hash"])
    expected_begin = {"operation_id": operation["id"], "task_id": task["id"], "parent_anchor_hash": policy["parent_anchor_hash"], "step": operation["step"], "risk_units": operation["hold_units"]}
    require(permit["risk_begin_hash"] == digest(expected_begin))
    descriptor = FrozenEmpiricalDescriptor.from_frozen(value["capture_descriptor"])
    require(digest(descriptor.binding()) == digest(value["capture_binding"]))
    require(descriptor.intent.capture_id == descriptor.intent.intent_id == operation["id"] and descriptor.intent.task_id == task["id"])
    require(descriptor.intent.context_hash == operation["context_hash"] and descriptor.intent.wire_hash == operation["wire_hash"])
    require(descriptor.intent.authorization_hash == permit["disclosure_hash"] and descriptor.allocation.begin_hash == permit["risk_begin_hash"])
    require(descriptor.intent.endpoint == policy["endpoint"] and descriptor.intent.model == policy["model"] and descriptor.intent.chat_output_parameter == policy["chat_output_parameter"])
    require(descriptor.intent.max_output_tokens == policy["max_output_tokens"] and descriptor.intent.deadline_at == permit["deadline_at"] and descriptor.intent.issued_at == permit["issued_at"])
    if operation["normalized"] is None:
        require(operation["local_units"] is None and value["receipt_classification"] is None and value["output_limit_observed"] is None)
        require(value["fee_status"] == "awaiting_receipt" and value["fee_evidence"] is None and value["platform_units"] is None)
    else:
        receipt, _ = decode(operation["raw"], policy)
        require(digest(receipt) == digest(operation["normalized"]) and operation["local_units"] == receipt["local_units"])
        require(value["receipt_classification"] == receipt["receipt_classification"] and value["output_limit_observed"] is receipt["output_limit_observed"])
        if value["fee_evidence"] is not None:
            record = observation({key: item for key, item in value["fee_evidence"].items() if key not in {"platform_units", "evidence_hash"}}, operation, policy)
            require(digest(record) == digest(value["fee_evidence"]) and value["platform_units"] == record["platform_units"])
        else:
            require(value["platform_units"] is None)
        overrun = max(operation["local_units"] or 0, value["platform_units"] or 0) > operation["hold_units"]
        expected = "overrun" if overrun else "matched" if value["fee_evidence"] else "awaiting_platform_fee" if operation["local_units"] is not None else "awaiting_receipt"
        require(value["fee_status"] == expected)
        if operation["status"] == "settled":
            require(expected == "matched" and receipt["receipt_classification"] == "complete")


def validate_transition(operation, before):
    old, value = before["empirical"], operation["empirical"]
    for key in ("capture_descriptor", "capture_binding"):
        require(digest(old[key]) == digest(value[key]))
    for key in ("receipt_classification", "output_limit_observed", "fee_evidence", "platform_units"):
        if old[key] is not None:
            require(digest(old[key]) == digest(value[key]))
    allowed = {"awaiting_receipt": {"awaiting_receipt", "awaiting_platform_fee", "matched", "overrun"},
               "awaiting_platform_fee": {"awaiting_platform_fee", "matched", "overrun"}, "matched": {"matched", "overrun"}, "overrun": {"overrun"}}
    require(value["fee_status"] in allowed[old["fee_status"]])
    if before["local_units"] is not None:
        require(type(operation["local_units"]) is int and operation["local_units"] == before["local_units"])


def validate_payload(payload, previous=None):
    tags = {"accounting_kind", "parent_anchor_hash"}
    used_logs = set()
    for account in payload["accounts"].values():
        empirical = account.get("accounting_kind") == KIND
        require((set(account) & (tags | {"anchor"})) == (tags | {"anchor"} if empirical else set()))
        if empirical:
            require(set(account) == {"id", "limit_units", "max_calls", "accounting_kind", "parent_anchor_hash", "anchor"})
            require(account["parent_anchor_hash"] == account["anchor"]["parent_anchor_hash"])
            require(type(account["limit_units"]) is int and account["limit_units"] == units(account["anchor"]["account_limit_cny"]))
            require(type(account["max_calls"]) is int and account["max_calls"] == account["anchor"]["account_max_calls"])
    for task in payload["tasks"].values():
        empirical = task.get("accounting_kind") == KIND
        require((set(task) & tags) == (tags if empirical else set()))
        if empirical:
            account = payload["accounts"][task["account_id"]]
            require(account["accounting_kind"] == KIND and account["parent_anchor_hash"] == task["parent_anchor_hash"])
    for namespace in ("quotes", "grants"):
        for record in payload[namespace].values():
            task = payload["tasks"][record["task_id"]]
            empirical = task.get("accounting_kind") == KIND
            require((set(record) & tags) == (tags if empirical else set()))
            if empirical:
                require(record["accounting_kind"] == KIND and record["parent_anchor_hash"] == task["parent_anchor_hash"])
                if namespace == "quotes":
                    account = payload["accounts"][task["account_id"]]
                    anchor = checked_anchor(account["anchor"], record["policy"])
                    require(account["parent_anchor_hash"] == anchor["parent_anchor_hash"])
                    require(record["per_call_risk_cny"] == record["per_call_hold_cny"] == record["policy"]["per_call_risk_cny"])
    for operation in payload["operations"].values():
        empirical = payload["tasks"][operation["task_id"]].get("accounting_kind") == KIND
        require((set(operation) & tags) == (tags if empirical else set()) and ("empirical" in operation) == empirical)
        require((set(operation["permit"]) & tags) == (tags if empirical else set()))
        if not empirical:
            continue
        quote, grant = payload["quotes"][operation["quote_id"]], payload["grants"][operation["grant_id"]]
        if previous is None or previous["operations"].get(operation["id"]) is not operation:
            validate_operation(operation, payload["tasks"][operation["task_id"]], quote, grant)
        evidence = operation["empirical"]["fee_evidence"]
        if evidence:
            require(evidence["platform_log_id"] not in used_logs)
            used_logs.add(evidence["platform_log_id"])
