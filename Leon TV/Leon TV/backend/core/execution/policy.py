"""Server-owned execution policy and exact local monetary arithmetic."""
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import ROUND_CEILING, Decimal
from typing import Literal
from urllib.parse import urlparse

from pydantic import Field, model_validator

from core.creator.store import digest
from core.planner.protocol import Strict

SCOPE = "frozen_brief_and_tool_history_to_named_provider"


def wire_api(policy):
    value = policy.get("wire_api", "chat_completions")
    if value not in {"chat_completions", "responses"}:
        raise ValueError("Explicit supported text protocol required")
    return value


def chat_output_parameter(policy):
    if wire_api(policy) == "responses":
        if "chat_output_parameter" in policy:
            raise ValueError("Responses cannot select a Chat output parameter")
        return None
    value = policy.get("chat_output_parameter", "max_tokens")
    if value not in {"max_tokens", "max_completion_tokens"}:
        raise ValueError("Explicit supported Chat output parameter required")
    return value


def protocol_endpoint_matches(policy):
    parsed = urlparse(policy["endpoint"])
    suffix = "/responses" if wire_api(policy) == "responses" else "/chat/completions"
    return parsed.scheme == "https" and bool(parsed.hostname) and not any([parsed.username, parsed.password, parsed.query, parsed.fragment]) and parsed.path.endswith(suffix)


@dataclass(frozen=True)
class TrustedInputBounder:
    """Reviewed server-injected bound; no bytes-equal-tokens fallback exists."""

    policy_hash: str
    verification_ref: str
    upper_bound: object

    def estimate(self, wire, policy):
        if self.policy_hash != policy["policy_hash"] or not self.verification_ref or not callable(self.upper_bound):
            return None
        try:
            policy_copy = dict(policy)
            result = self.upper_bound(wire.encode("utf-8"), policy_copy)
            return result if policy_copy == policy and type(result) is int and result > 0 else None
        except Exception:
            return None


def now():
    return datetime.now(timezone.utc)


def units(value):
    return int((Decimal(value) * 1000000).to_integral_value(rounding=ROUND_CEILING))


def money(value):
    return format(Decimal(value) / 1000000, ".6f")


def cost(input_tokens, output_tokens, policy):
    return int((Decimal(input_tokens) * Decimal(policy["input_cny_per_million"]) + Decimal(output_tokens) * Decimal(policy["output_cny_per_million"])).to_integral_value(rounding=ROUND_CEILING))


class ExecutionPolicy(Strict):
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")
    version: str = Field(min_length=1, max_length=80)
    provider: Literal["openai_compatible"] = "openai_compatible"
    model: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}$")
    endpoint: str
    wire_api: Literal["chat_completions", "responses"] = "chat_completions"
    chat_output_parameter: Literal["max_tokens", "max_completion_tokens"] = "max_tokens"
    role_workflow: Literal["bounded_revision_v1"] = "bounded_revision_v1"
    currency: Literal["CNY"] = "CNY"
    input_cny_per_million: str
    output_cny_per_million: str
    max_input_tokens: int = Field(ge=1, le=4000000)
    max_output_tokens: int = Field(ge=1, le=8192)
    max_input_bytes: int = Field(ge=256, le=1048576)
    max_output_bytes: int = Field(ge=128, le=65536)
    request_deadline_seconds: int = Field(ge=1, le=120)
    max_calls: int = Field(ge=1, le=6)
    task_limit_cny: str
    account_limit_cny: str
    account_max_calls: int = Field(ge=1, le=10000)
    account_scope_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")
    valid_until: str
    privacy_scope: Literal["frozen_brief_and_tool_history_to_named_provider"] = SCOPE
    pricing_basis: str = Field(min_length=1, max_length=500)
    input_bound_basis: str = Field(min_length=1, max_length=500)
    operator_approved: Literal[True]
    billing_bound_verified: Literal[True]
    validation_only: bool

    @model_validator(mode="after")
    def checked(self):
        if self.wire_api == "responses" and "chat_output_parameter" in self.model_fields_set:
            raise ValueError("Responses cannot select a Chat output parameter")
        if not protocol_endpoint_matches(self.model_dump()):
            raise ValueError("Explicit compatible text endpoint required")
        for amount in [self.input_cny_per_million, self.output_cny_per_million, self.task_limit_cny, self.account_limit_cny]:
            if not re.fullmatch(r"(?:0|[1-9][0-9]{0,6})(?:\.[0-9]{1,6})?", amount):
                raise ValueError("Fixed nonnegative CNY decimal required")
        if units(self.task_limit_cny) <= 0 or units(self.account_limit_cny) < units(self.task_limit_cny):
            raise ValueError("Explicit task/account ceilings required")
        if datetime.fromisoformat(self.valid_until).tzinfo is None or cost(self.max_input_tokens, self.max_output_tokens, self.model_dump()) <= 0:
            raise ValueError("Explicit expiry and conservative reservation required")
        return self

    def model_dump(self, *args, **kwargs):
        result = super().model_dump(*args, **kwargs)
        if "chat_output_parameter" not in self.model_fields_set:
            result.pop("chat_output_parameter", None)
        if "role_workflow" not in self.model_fields_set:
            result.pop("role_workflow", None)
        return result

    def frozen(self):
        result = self.model_dump()
        if "wire_api" not in self.model_fields_set:
            result.pop("wire_api")  # Legacy hashes must not gain a default field.
        result["policy_hash"] = digest(result)
        return result


def safe_policy(policy):
    result = {key: value for key, value in policy.items() if key not in {"account_scope_id", "operator_approved", "billing_bound_verified", "validation_only"}}
    for key in ["task_limit_cny", "account_limit_cny"]:
        result[key] = money(units(result[key]))
    result["wire_api"] = wire_api(policy)  # Projection only, never backfill storage.
    selected = chat_output_parameter(policy)
    if selected is not None:
        result["chat_output_parameter"] = selected
    return result


class QuoteInput(Strict):
    client_request_key: str = Field(pattern=r"^[A-Za-z0-9_-]{8,128}$")
    expected_execution_revision: int = Field(ge=0)
    expected_task_revision: int = Field(ge=1)


class GrantInput(Strict):
    client_request_key: str = Field(pattern=r"^[A-Za-z0-9_-]{8,128}$")
    expected_execution_revision: int = Field(ge=1)
    quote_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    accepted_disclosure_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    accepted_scope: Literal["frozen_brief_and_tool_history_to_named_provider"]
    consent: Literal[True]

    @model_validator(mode="before")
    @classmethod
    def exact_consent(cls, value):
        if isinstance(value, dict) and type(value.get("consent")) is not bool:
            raise ValueError("Explicit boolean consent required")
        return value


class RevokeInput(Strict):
    client_action_key: str = Field(pattern=r"^[A-Za-z0-9_-]{8,128}$")
    expected_execution_revision: int = Field(ge=1)


class ExecutionAction(RevokeInput):
    action: Literal["resume", "stop", "reconcile"]
