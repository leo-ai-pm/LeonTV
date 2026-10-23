"""One empirical Chat request, with independent intent and risk references.

The trusted caller durably records the actual intent, reserves risk and begins
the parent ledger before constructing this view. This module checks the frozen
links; it neither reads that ledger nor certifies billing or input bounds.
There is no client route, config lookup, retry, settlement or tool execution.
"""
import copy
import json
import re
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from core.creator.store import ID_RE, SHA_RE, digest
from core.execution.policy import now, protocol_endpoint_matches
from core.execution.receipt_transport import capture_once
from core.execution.receipts import ReceiptCaptureSettings, ReceiptStore, timestamp
from core.planner.protocol import parse_json

KIND = "empirical_planner_text_once"
RISK_KIND = "empirical_risk_allocation"
RISK_BASIS = "empirical_not_hard_billing_bound"
MAX_REQUEST_BYTES = 1048576
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}")
REFERENCE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]*(?:/[A-Za-z0-9][A-Za-z0-9_.:-]*){0,7}")
MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}")


class EmpiricalInputRejected(Exception):
    """Fixed safe error; never includes input, key or upstream exception text."""

    def __init__(self):
        super().__init__("EMPIRICAL_INPUT_REJECTED")


def check(value):
    if not value:
        raise EmpiricalInputRejected()


def identifier(value, pattern=IDENTIFIER):
    check(type(value) is str and pattern.fullmatch(value) is not None)


def sha(value):
    identifier(value, SHA_RE)


def checked_time(value):
    try:
        return timestamp(value)
    except Exception:
        raise EmpiricalInputRejected() from None


def empirical_wire_for(context, *, model, chat_output_parameter="max_tokens", max_output_tokens=2048):
    """The explicit experimental Chat contract; formal wire_for is unchanged."""
    payload = {"model": model, chat_output_parameter: max_output_tokens, "messages": [
        {"role": "user" if message["role"] == "tool" else message["role"],
            "content": json.dumps(message["content"], ensure_ascii=False)} for message in context],
        "stream": False, "n": 1, "temperature": 0}
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


@dataclass(frozen=True)
class EmpiricalIntent:
    capture_id: str
    intent_id: str
    task_id: str
    operation_key: str
    authorization_hash: str
    source_snapshot_hash: str
    rules_hash: str
    context_hash: str
    wire_hash: str
    endpoint: str
    model: str
    chat_output_parameter: str
    max_output_tokens: int
    issued_at: str
    deadline_at: str
    request_deadline_seconds: int = 120
    environment: str = "empirical_live"
    detailed_tool_feedback: bool = True
    version: int = 1
    kind: str = KIND
    scope: str = KIND

    def __post_init__(self):
        identifier(self.capture_id, ID_RE)
        identifier(self.task_id)
        for name in ("intent_id", "operation_key"):
            identifier(getattr(self, name), REFERENCE)
            check(len(getattr(self, name)) <= 256)
        for name in ("authorization_hash", "source_snapshot_hash", "rules_hash", "context_hash", "wire_hash"):
            sha(getattr(self, name))
        identifier(self.model, MODEL)
        check(type(self.version) is int and self.version == 1 and self.kind == KIND and self.scope == KIND)
        check(type(self.detailed_tool_feedback) is bool)
        check(type(self.environment) is str and self.environment in {"synthetic", "empirical_live"})
        check(type(self.chat_output_parameter) is str and self.chat_output_parameter in {"max_tokens", "max_completion_tokens"})
        check(type(self.max_output_tokens) is int and 1 <= self.max_output_tokens <= 8192)
        check(type(self.request_deadline_seconds) is int and 1 <= self.request_deadline_seconds <= 120)
        check(0 < (checked_time(self.deadline_at) - checked_time(self.issued_at)).total_seconds() <= self.request_deadline_seconds)
        try:
            check(type(self.endpoint) is str and len(self.endpoint) <= 2048 and protocol_endpoint_matches(self.routing()))
        except Exception:
            raise EmpiricalInputRejected() from None

    def routing(self):
        """Wire formatting fields only. This is not an ExecutionPolicy."""
        return {"endpoint": self.endpoint, "model": self.model, "wire_api": "chat_completions",
            "chat_output_parameter": self.chat_output_parameter, "max_output_tokens": self.max_output_tokens}

    @property
    def intent_hash(self):
        return digest(asdict(self))

    def frozen(self):
        return {**asdict(self), "intent_hash": self.intent_hash}


@dataclass(frozen=True)
class RiskAllocation:
    allocation_id: str
    intent_id: str
    intent_hash: str
    operation_key: str
    authorization_hash: str
    parent_ledger_hash: str
    begin_hash: str
    begun_at: str
    risk_fen: int = 100
    risk_basis: str = RISK_BASIS
    status: str = "submitting"
    version: int = 1
    kind: str = RISK_KIND

    def __post_init__(self):
        for name in ("allocation_id", "intent_id", "operation_key"):
            identifier(getattr(self, name), REFERENCE)
            check(len(getattr(self, name)) <= 256)
        for name in ("intent_hash", "authorization_hash", "parent_ledger_hash", "begin_hash"):
            sha(getattr(self, name))
        checked_time(self.begun_at)
        check(type(self.version) is int and self.version == 1 and self.kind == RISK_KIND)
        check(type(self.risk_fen) is int and self.risk_fen == 100)
        check(self.risk_basis == RISK_BASIS and self.status == "submitting")

    @property
    def risk_allocation_hash(self):
        return digest(asdict(self))

    def frozen(self):
        return {**asdict(self), "risk_allocation_hash": self.risk_allocation_hash}


@dataclass(frozen=True)
class FrozenEmpiricalDescriptor:
    intent: EmpiricalIntent
    allocation: RiskAllocation
    context_json: str
    wire: str

    def __post_init__(self):
        try:
            check(type(self.intent) is EmpiricalIntent and type(self.allocation) is RiskAllocation)
            # Revalidate at the boundary, including direct descriptor construction.
            self.intent.__post_init__()
            self.allocation.__post_init__()
            check(type(self.context_json) is str and type(self.wire) is str)
            check(len(self.context_json.encode("utf-8")) <= MAX_REQUEST_BYTES and len(self.wire.encode("utf-8")) <= MAX_REQUEST_BYTES)
            context = parse_json(self.context_json)
            check(type(context) is list and 0 < len(context) <= 128)
            check(all(type(message) is dict and set(message) == {"role", "content"}
                and message["role"] in {"system", "user", "assistant", "tool"} for message in context))
            check(digest(context) == self.intent.context_hash and digest(self.wire) == self.intent.wire_hash)
            payload = parse_json(self.wire)
            expected = parse_json(empirical_wire_for(context, model=self.intent.model,
                chat_output_parameter=self.intent.chat_output_parameter, max_output_tokens=self.intent.max_output_tokens))
            check(payload == expected)
            check(type(payload["stream"]) is bool and payload["stream"] is False)
            check(type(payload["n"]) is int and payload["n"] == 1)
            check(type(payload["temperature"]) is int and payload["temperature"] == 0)
            check(type(payload[self.intent.chat_output_parameter]) is int)
            check(self.allocation.intent_id == self.intent.intent_id and self.allocation.intent_hash == self.intent.intent_hash)
            check(self.allocation.operation_key == self.intent.operation_key and self.allocation.authorization_hash == self.intent.authorization_hash)
            check(checked_time(self.intent.issued_at) <= checked_time(self.allocation.begun_at) < checked_time(self.intent.deadline_at))
        except Exception:
            raise EmpiricalInputRejected() from None

    @classmethod
    def build(cls, intent, allocation, context, wire):
        try:
            check(type(context) is list)
            # The original content key order is already embedded in wire strings.
            return cls(intent, allocation, json.dumps(context, ensure_ascii=False, allow_nan=False), wire)
        except Exception:
            raise EmpiricalInputRejected() from None

    @classmethod
    def from_frozen(cls, value):
        """Rehydrate only an exact server-frozen view; never load a file here."""
        try:
            check(type(value) is dict and set(value) == {"intent", "allocation", "context_json", "wire", "descriptor_hash"})
            check(type(value["intent"]) is dict and set(value["intent"]) == {field.name for field in fields(EmpiricalIntent)} | {"intent_hash"})
            check(type(value["allocation"]) is dict and set(value["allocation"]) == {field.name for field in fields(RiskAllocation)} | {"risk_allocation_hash"})
            intent, allocation = dict(value["intent"]), dict(value["allocation"])
            intent_hash, risk_hash = intent.pop("intent_hash"), allocation.pop("risk_allocation_hash")
            result = cls(EmpiricalIntent(**intent), RiskAllocation(**allocation), value["context_json"], value["wire"])
            check(result.intent.intent_hash == intent_hash and result.allocation.risk_allocation_hash == risk_hash)
            check(result.descriptor_hash == value["descriptor_hash"])
            return result
        except Exception:
            raise EmpiricalInputRejected() from None

    def _value(self):
        return {"intent": self.intent.frozen(), "allocation": self.allocation.frozen(), "context_json": self.context_json, "wire": self.wire}

    @property
    def descriptor_hash(self):
        return digest(self._value())

    def frozen(self):
        return {**self._value(), "descriptor_hash": self.descriptor_hash}

    def binding(self):
        intent, allocation = self.intent, self.allocation
        return {"operation_id": intent.capture_id, "version": 1, "kind": KIND, "scope": KIND,
            "intent_id": intent.intent_id, "intent_hash": intent.intent_hash, "task_id": intent.task_id,
            "operation_key": intent.operation_key, "authorization_hash": intent.authorization_hash,
            "source_snapshot_hash": intent.source_snapshot_hash, "rules_hash": intent.rules_hash,
            "context_hash": intent.context_hash, "wire_hash": intent.wire_hash, "deadline_at": intent.deadline_at,
            "risk_allocation_id": allocation.allocation_id, "risk_allocation_hash": allocation.risk_allocation_hash,
            "parent_ledger_hash": allocation.parent_ledger_hash, "begin_hash": allocation.begin_hash,
            "descriptor_hash": self.descriptor_hash}


class EmpiricalCaptureAdapter:
    def __init__(self, receipt_root: Path, *, settings: ReceiptCaptureSettings | None = None):
        selected = settings if settings is not None else ReceiptCaptureSettings()
        check(type(selected) is ReceiptCaptureSettings and selected.cleanup_grace_seconds == 5)
        # Settings are server-owned, copied so later caller edits cannot alter a job.
        self.settings = ReceiptCaptureSettings(selected.observation_wait_seconds, 5,
            selected.worker_factory, copy.deepcopy(selected.worker_options))
        self.receipts = ReceiptStore(receipt_root)

    @staticmethod
    def _descriptor(descriptor):
        check(type(descriptor) is FrozenEmpiricalDescriptor)
        return FrozenEmpiricalDescriptor.from_frozen(descriptor.frozen())

    def capture(self, descriptor: FrozenEmpiricalDescriptor, api_key: str) -> dict:
        descriptor = self._descriptor(descriptor)
        check(type(api_key) is str and bool(api_key) and len(api_key.encode("utf-8")) <= 16384)
        check(checked_time(descriptor.allocation.begun_at) <= now() < checked_time(descriptor.intent.deadline_at))
        if descriptor.intent.environment == "empirical_live":
            check(self.settings.worker_factory is None and not self.settings.worker_options)
        job = {"kind": KIND, "descriptor": descriptor.frozen(), "binding": descriptor.binding(),
            "worker_factory": self.settings.worker_factory, "worker_options": copy.deepcopy(self.settings.worker_options)}
        return capture_once(self.receipts, descriptor.binding(), job, api_key, self.settings)

    def inspect(self, descriptor: FrozenEmpiricalDescriptor) -> dict:
        descriptor = self._descriptor(descriptor)
        return {"inspection": self.receipts.inspect_binding(descriptor.intent.capture_id, descriptor.binding())}


def worker_request(job, claim):
    """Validate the distinct empirical job before the shared single HTTP path."""
    check(set(job) == {"kind", "descriptor", "binding", "worker_factory", "worker_options", "api_key"} and job["kind"] == KIND)
    descriptor = FrozenEmpiricalDescriptor.from_frozen(job["descriptor"])
    binding = descriptor.binding()
    check(job["binding"] == claim["binding"] == binding)
    check((checked_time(claim["cleanup_cutoff"]) - checked_time(binding["deadline_at"])).total_seconds() == 5)
    check(checked_time(descriptor.allocation.begun_at) <= checked_time(claim["created_at"]))
    if descriptor.intent.environment == "empirical_live":
        check(job["worker_factory"] is None and not job["worker_options"])
    return binding, descriptor.intent.endpoint, descriptor.wire, descriptor.intent.environment == "synthetic"
