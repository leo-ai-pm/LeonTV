"""Explicit one-call asset design in the original execution journal and lock."""
import base64
import copy
import uuid
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from core.creator.store import KEY_RE, CreatorError, digest, utc_now
from core.execution.empirical import decode
from core.execution.fees import FrozenFeeSource
from core.execution.image_policy import FrozenModel, MediaBudgetAnchor
from core.execution.media_ledger import native_context
from core.execution.policy import now
from core.execution.receipts import ReceiptCaptureSettings, timestamp
from core.harness.store import unlock_file
from core.planner import product_assets
from core.planner.review import json_copy
from experiments.planner_live.empirical_capture import (
    EmpiricalCaptureAdapter,
    EmpiricalIntent,
    FrozenEmpiricalDescriptor,
    RiskAllocation,
    empirical_wire_for,
)

SCOPE = "asset_design_v1"
RULE_HASH = digest({"scope": SCOPE, "contract_version": product_assets.ASSET_CONTRACT_VERSION,
    "schema": product_assets.ASSET_OUTPUT_SCHEMA, "max_calls": 1})


def require(value, code="ASSET_DESIGN_UNAVAILABLE"):
    if not value:
        raise CreatorError(code, "Asset design evidence or authority is unavailable", 409)


class AssetDesignPolicy(FrozenModel):
    seal_field = "policy_hash"
    schema_version: Literal[1] = 1
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")
    version: str = Field(min_length=1, max_length=80)
    kind: Literal["asset_design_v1"] = SCOPE
    scope: Literal["asset_design_v1"] = SCOPE
    origin: Literal["https://api.ai-tudou.net", "https://synthetic.invalid"]
    model: Literal["gemini-3.8-flash"] = "gemini-3.8-flash"
    chat_output_parameter: Literal["max_completion_tokens"] = "max_completion_tokens"
    max_output_tokens: int = Field(default=8192, ge=1, le=8192)
    max_input_bytes: int = Field(default=1048576, ge=256, le=1048576)
    max_output_bytes: int = Field(default=65536, ge=128, le=65536)
    request_deadline_seconds: int = Field(default=120, ge=1, le=120)
    max_calls: Literal[1] = 1
    currency: Literal["CNY"] = "CNY"
    per_call_risk_cny: Literal["1.000000"] = "1.000000"
    input_cny_per_million: str
    output_cny_per_million: str
    risk_basis: Literal["empirical_not_hard_billing_bound"] = "empirical_not_hard_billing_bound"
    operator_risk_approved: Literal[True]
    valid_until: str
    validation_only: bool

    @model_validator(mode="before")
    @classmethod
    def exact_calls(cls, value):
        if type(value) is dict and "max_calls" in value:
            require(type(value["max_calls"]) is int)
        return value

    @model_validator(mode="after")
    def checked(self):
        from core.execution.image_policy import amount
        timestamp(self.valid_until)
        amount(self.input_cny_per_million)
        amount(self.output_cny_per_million)
        require(self.validation_only or self.origin == "https://api.ai-tudou.net")
        require(bool(self.version.strip()))
        self.version.encode("utf-8")
        return self


def disclosure_for(scope):
    return {"scope": SCOPE, "scope_id": scope["id"], "account_hash": scope["account_hash"],
        "policy": copy.deepcopy(scope["policy"]), "budget_anchor": copy.deepcopy(scope["anchor"]),
        "handoff_hash": scope["handoff"]["handoff_hash"], "provenance_hash": scope["handoff"]["provenance_hash"],
        "asset_input_hash": digest(scope["asset_input"]), "rules_hash": RULE_HASH,
        "fee_source_hash": digest(scope["fee_source"]), "data_categories": ["complete_accepted_source_plan_and_review", "asset_output_schema"],
        "max_calls": 1, "automatic_repair_calls": 0, "image_submissions": 0, "risk_released_after_fee": False, "grant_starts_generation": False}


def descriptor_for(scope, grant, operation_id, issued_at):
    policy, anchor = scope["policy"], scope["anchor"]
    context = product_assets.asset_messages(scope["asset_input"])
    wire = empirical_wire_for(context, model=policy["model"], chat_output_parameter=policy["chat_output_parameter"], max_output_tokens=policy["max_output_tokens"])
    require(len(wire.encode("utf-8")) <= policy["max_input_bytes"])
    issued = timestamp(issued_at)
    deadline = min(issued + timedelta(seconds=policy["request_deadline_seconds"]), timestamp(policy["valid_until"]))
    intent = EmpiricalIntent(capture_id=operation_id, intent_id=operation_id, task_id=scope["task_id"], operation_key=operation_id,
        authorization_hash=grant["disclosure_hash"], source_snapshot_hash=scope["asset_input"]["binding"]["source_snapshot_hash"],
        rules_hash=RULE_HASH, context_hash=digest(context), wire_hash=digest(wire), endpoint=policy["origin"] + "/v1/chat/completions",
        model=policy["model"], chat_output_parameter=policy["chat_output_parameter"], max_output_tokens=policy["max_output_tokens"],
        issued_at=issued_at, deadline_at=deadline.isoformat(), request_deadline_seconds=policy["request_deadline_seconds"],
        environment="synthetic" if policy["validation_only"] else "empirical_live", detailed_tool_feedback=False)
    begin = digest({"scope_id": scope["id"], "operation_id": operation_id, "parent_begin_hash": anchor["parent_allocation_begin_hash"],
        "budget_anchor_hash": anchor["budget_anchor_hash"], "risk_cny": policy["per_call_risk_cny"], "call_index": 1})
    allocation = RiskAllocation(allocation_id=operation_id, intent_id=operation_id, intent_hash=intent.intent_hash,
        operation_key=operation_id, authorization_hash=grant["disclosure_hash"], parent_ledger_hash=anchor["parent_ledger_hash"],
        begin_hash=begin, begun_at=issued_at)
    return FrozenEmpiricalDescriptor.build(intent, allocation, context, wire)


def permit_for(scope, grant, descriptor):
    return {"kind": SCOPE, "scope": SCOPE, "operation_id": descriptor.intent.capture_id, "task_id": scope["task_id"],
        "scope_id": scope["id"], "account_id": scope["account_id"], "grant_id": grant["id"], "quote_id": grant["quote_id"],
        "disclosure_hash": grant["disclosure_hash"], "policy_hash": scope["policy"]["policy_hash"],
        "budget_anchor_hash": scope["anchor"]["budget_anchor_hash"], "handoff_hash": scope["handoff"]["handoff_hash"],
        "provenance_hash": scope["handoff"]["provenance_hash"], "asset_input_hash": digest(scope["asset_input"]), "rules_hash": RULE_HASH,
        "parent_authorization_hash": scope["anchor"]["parent_authorization_hash"], "parent_allocation_hash": scope["anchor"]["parent_allocation_hash"],
        "parent_allocation_begin_hash": scope["anchor"]["parent_allocation_begin_hash"], "descriptor_hash": descriptor.descriptor_hash,
        "binding": descriptor.binding(), "call_index": 1, "risk_cny": "1.000000"}


def classify(scope, operation, raw, fee=None):
    receipt, error = decode(raw, scope["policy"])
    timely = timestamp(operation["descriptor"]["intent"]["issued_at"]) <= timestamp(raw["received_at"]) <= timestamp(operation["descriptor"]["intent"]["deadline_at"])
    manifest = None
    if error is None and timely:
        try:
            manifest = product_assets.validate_asset_output(receipt["content"], scope["asset_input"])
        except Exception:
            pass
    status = "unknown" if receipt["receipt_classification"] == "unconfirmed" or not timely else "truncated" if error else "invalid" if manifest is None else "awaiting_fee"
    if manifest is not None and fee is not None:
        status = "blocked" if fee["overrun"] else "manifest_ready"
    return receipt, manifest, status


class AssetDesignExecution:
    def __init__(self, runtime, *, policy=None, budget_anchor=None, authority_guard=None, fee_source=None, receipt_capture=None, credential_loader=None):
        self.runtime, self.store = runtime, runtime.store
        self.policy = policy.frozen() if isinstance(policy, AssetDesignPolicy) else copy.deepcopy(policy)
        self.anchor = budget_anchor.frozen() if isinstance(budget_anchor, MediaBudgetAnchor) else copy.deepcopy(budget_anchor)
        self.authority_guard, self.credential_loader = authority_guard, credential_loader
        self.fee_source = FrozenFeeSource.model_validate(fee_source).model_dump() if fee_source is not None else None
        self.capture = EmpiricalCaptureAdapter(self.store.root / "asset-design-receipts", settings=receipt_capture or ReceiptCaptureSettings())
        backend = Path(__file__).resolve().parents[2]
        self.capture.receipts.check_private((backend / "code", backend.parent / "frontend/public"))

    @contextmanager
    def _lock(self, task_id):
        lock = self.store.execution_lock(task_id)
        require(lock is not None, "ACTION_NOT_ALLOWED")
        try:
            yield
        finally:
            unlock_file(lock)

    @staticmethod
    def _put(payload, kind, record_id, task_id, scope_id, **values):
        key = kind + ":" + record_id
        require(key not in payload.setdefault("asset_design", {}), "ASSET_DESIGN_CONFLICT")
        record = {"schema_version": 1, "kind": kind, "id": record_id, "task_id": task_id, "scope_id": scope_id, "created_at": utc_now(), **values}
        payload["asset_design"][key] = record
        return record

    @staticmethod
    def _replay(payload, verb, key, request):
        json_copy(request)
        require(type(key) is str and KEY_RE.fullmatch(key) is not None, "INVALID_INPUT")
        old = payload.get("asset_design", {}).get("key:" + verb + ":" + key)
        if old is not None:
            require(old["request_hash"] == digest(request), "IDEMPOTENCY_CONFLICT")
            return payload["asset_design"][old["resource_key"]]
        return None

    def _remember(self, payload, verb, key, request, resource):
        self._put(payload, "key", verb + ":" + key, resource["task_id"], resource["scope_id"], verb=verb,
            request=json_copy(request), request_hash=digest(request), resource_key=resource["kind"] + ":" + resource["id"])

    def _authority(self, scope):
        require(self.policy is not None and self.anchor is not None and self.fee_source is not None and callable(self.authority_guard), "ASSET_DESIGN_DISABLED")
        require(digest(scope["policy"]) == digest(self.policy) and digest(scope["anchor"]) == digest(self.anchor)
            and digest(scope["fee_source"]) == digest(self.fee_source), "ASSET_DESIGN_POLICY_STALE")
        require(now() < timestamp(scope["policy"]["valid_until"]), "ASSET_DESIGN_POLICY_EXPIRED")
        candidate = json_copy(scope, limit=16 * 1048576)
        original = digest(candidate)
        try:
            result = self.authority_guard(candidate)
            json_copy(candidate, limit=16 * 1048576)
            require(result is True and digest(candidate) == original)
        except Exception:
            raise CreatorError("ASSET_DESIGN_AUTHORITY_REJECTED", "Independent asset design authority is required", 409) from None

    def quote(self, task_id, *, key):
        from core.execution.asset_design_ledger import records
        request = {"task_id": task_id}
        with self._lock(task_id), self.store.transaction() as payload:
            old = self._replay(payload, "quote", key, request)
            if old:
                return {"quote": copy.deepcopy(old), "replayed": True}
            require(self.policy is not None and self.anchor is not None, "ASSET_DESIGN_DISABLED")
            policy = AssetDesignPolicy.model_validate({k: v for k, v in self.policy.items() if k != "policy_hash"}).frozen()
            anchor = MediaBudgetAnchor.model_validate({k: v for k, v in self.anchor.items() if k != "budget_anchor_hash"}).frozen()
            require(digest(policy) == digest(self.policy) and digest(anchor) == digest(self.anchor))
            frozen_scopes = [scope for scope in records(payload, "scope") if scope["task_id"] == task_id
                and scope["policy"]["policy_hash"] == policy["policy_hash"] and scope["anchor"]["budget_anchor_hash"] == anchor["budget_anchor_hash"]]
            require(len(frozen_scopes) <= 1)
            if frozen_scopes:
                context, handoff = copy.deepcopy(frozen_scopes[0]["asset_input"]), copy.deepcopy(frozen_scopes[0]["handoff"])
            else:
                context, handoff = native_context(payload, task_id)
            require(policy["validation_only"] is (handoff["evidence_kind"] == "synthetic"), "ASSET_DESIGN_ENVIRONMENT_MISMATCH")
            account = payload["accounts"][payload["tasks"][task_id]["account_id"]]
            identity = {"task_id": task_id, "policy_hash": policy["policy_hash"], "budget_anchor_hash": anchor["budget_anchor_hash"], "handoff_hash": handoff["handoff_hash"]}
            scope_id = digest(identity)[:32]
            scope = payload.get("asset_design", {}).get("scope:" + scope_id)
            if scope is None:
                scope = {"schema_version": 1, "kind": "scope", "id": scope_id, "task_id": task_id, "scope_id": scope_id,
                    "created_at": utc_now(), "account_id": account["id"], "account_hash": digest(account), "ordinal": len(records(payload, "scope")) + 1,
                    "policy": policy, "anchor": anchor, "asset_input": context, "handoff": handoff, "fee_source": copy.deepcopy(self.fee_source)}
                self._authority(scope)
                self._put(payload, "scope", scope_id, task_id, scope_id, **{k: v for k, v in scope.items() if k not in {"schema_version", "kind", "id", "task_id", "scope_id", "created_at"}})
                scope = payload["asset_design"]["scope:" + scope_id]
            self._authority(scope)
            record = self._put(payload, "quote", uuid.uuid4().hex, task_id, scope_id, disclosure=disclosure_for(scope), disclosure_hash=None)
            record["disclosure_hash"] = digest({k: v for k, v in record.items() if k != "disclosure_hash"})
            self._remember(payload, "quote", key, request, record)
            return {"quote": copy.deepcopy(record), "replayed": False}

    def grant(self, task_id, quote_id, *, disclosure_hash, consent, key):
        from core.execution.media_ledger import account_halted, unresolved_text_claims
        require(consent is True, "CONSENT_REQUIRED")
        request = {"task_id": task_id, "quote_id": quote_id, "disclosure_hash": disclosure_hash, "consent": consent}
        with self._lock(task_id), self.store.transaction() as payload:
            old = self._replay(payload, "grant", key, request)
            if old:
                return {"grant": copy.deepcopy(old), "replayed": True}
            quote = payload.get("asset_design", {}).get("quote:" + quote_id)
            require(quote is not None and quote["task_id"] == task_id and quote["disclosure_hash"] == disclosure_hash, "ASSET_DESIGN_DISCLOSURE_REQUIRED")
            scope = payload["asset_design"]["scope:" + quote["scope_id"]]
            self._authority(scope)
            require(not unresolved_text_claims(payload), "MEDIA_LEGACY_FEE_BRIDGE_REQUIRED")
            require(not account_halted(payload, scope["account_id"]), "EMPIRICAL_RISK_OVERRUN")
            grant = self._put(payload, "grant", uuid.uuid4().hex, task_id, scope["id"], quote_id=quote_id, disclosure_hash=disclosure_hash, revoked_at=None)
            self._remember(payload, "grant", key, request, grant)
            return {"grant": copy.deepcopy(grant), "replayed": False}

    def reserve(self, task_id, grant_id, *, key):
        from core.execution.asset_design_ledger import records
        from core.execution.media_ledger import (
            account_halted,
            has_pending_fees,
            has_pending_unknown,
            unresolved_text_claims,
        )
        request = {"task_id": task_id, "grant_id": grant_id}
        with self._lock(task_id), self.store.transaction() as payload:
            old = self._replay(payload, "reserve", key, request)
            if old:
                return {"operation": copy.deepcopy(old), "replayed": True}
            grant = payload.get("asset_design", {}).get("grant:" + grant_id)
            require(grant is not None and grant["task_id"] == task_id and grant["revoked_at"] is None, "ASSET_DESIGN_GRANT_REQUIRED")
            scope = payload["asset_design"]["scope:" + grant["scope_id"]]
            self._authority(scope)
            account_id = scope["account_id"]
            require(not account_halted(payload, account_id), "EMPIRICAL_RISK_OVERRUN")
            require(not has_pending_unknown(payload, account_id), "RESPONSE_UNCONFIRMED")
            require(not has_pending_fees(payload, account_id), "PLATFORM_FEE_REQUIRED")
            require(not unresolved_text_claims(payload), "MEDIA_LEGACY_FEE_BRIDGE_REQUIRED")
            require(not any(op["account_id"] == account_id and op["status"] != "settled" for op in payload["operations"].values()), "RESPONSE_UNCONFIRMED")
            require(not any(op["scope_id"] == scope["id"] for op in records(payload, "operation")), "CALL_LIMIT")
            operation_id, issued = uuid.uuid4().hex, utc_now()
            descriptor = descriptor_for(scope, grant, operation_id, issued)
            operation = self._put(payload, "operation", operation_id, task_id, scope["id"], grant_id=grant_id, quote_id=grant["quote_id"],
                permit=permit_for(scope, grant, descriptor), descriptor=descriptor.frozen(), status="reserved", capture_started_at=None,
                raw=None, receipt=None, fee_id=None, manifest=None)
            self._remember(payload, "reserve", key, request, operation)
            return {"operation": copy.deepcopy(operation), "replayed": False}

    def _dispatch_gate(self, payload, operation):
        from core.execution.media_ledger import (
            account_halted,
            has_pending_fees,
            has_pending_unknown,
            unresolved_text_claims,
        )
        scope = payload["asset_design"]["scope:" + operation["scope_id"]]
        self._authority(scope)
        require(payload["asset_design"]["grant:" + operation["grant_id"]]["revoked_at"] is None, "ASSET_DESIGN_GRANT_REVOKED")
        require(not account_halted(payload, scope["account_id"]), "EMPIRICAL_RISK_OVERRUN")
        require(not has_pending_unknown(payload, scope["account_id"], exclude_operation_id=operation["id"]), "RESPONSE_UNCONFIRMED")
        require(not has_pending_fees(payload, scope["account_id"], exclude_operation_id=operation["id"]), "PLATFORM_FEE_REQUIRED")
        require(not unresolved_text_claims(payload), "MEDIA_LEGACY_FEE_BRIDGE_REQUIRED")
        require(now() < timestamp(operation["descriptor"]["intent"]["deadline_at"]), "ASSET_DESIGN_DEADLINE")
        return scope

    @staticmethod
    def _adopt(payload, operation, raw):
        require(operation["raw"] is None or digest(operation["raw"]) == digest(raw), "ASSET_DESIGN_RECEIPT_CONFLICT")
        scope = payload["asset_design"]["scope:" + operation["scope_id"]]
        receipt, manifest, status = classify(scope, operation, raw)
        operation.update(raw=raw, receipt=receipt, manifest=manifest, status=status)

    def start(self, task_id, operation_id):
        with self._lock(task_id):
            with self.store.transaction() as payload:
                operation = payload.get("asset_design", {}).get("operation:" + operation_id)
                require(operation is not None and operation["task_id"] == task_id, "NOT_FOUND")
                if operation["capture_started_at"] is not None:
                    return {"operation": copy.deepcopy(operation), "replayed": True}
                scope = self._dispatch_gate(payload, operation)
                if scope["policy"]["validation_only"]:
                    require(self.capture.settings.worker_factory is not None, "ASSET_DESIGN_TEST_TRANSPORT_REQUIRED")
                    api_key = self.runtime.api_key
                else:
                    require(self.capture.settings.worker_factory is None and not self.capture.settings.worker_options, "ASSET_DESIGN_LIVE_TRANSPORT_REQUIRED")
                    require(callable(self.credential_loader), "ASSET_DESIGN_CREDENTIAL_REQUIRED")
                    try:
                        api_key = self.credential_loader()
                    except Exception:
                        raise CreatorError("ASSET_DESIGN_CREDENTIAL_REQUIRED", "Explicit asset design credentials are unavailable", 409) from None
                require(type(api_key) is str and bool(api_key), "ASSET_DESIGN_CREDENTIAL_REQUIRED")
                operation.update(status="submitting", capture_started_at=utc_now())
                descriptor = FrozenEmpiricalDescriptor.from_frozen(operation["descriptor"])
            try:
                # Recheck persisted native begin before the shared collector can POST.
                current = self.store.read_only()
                persisted = current["asset_design"]["operation:" + operation_id]
                self._dispatch_gate(current, persisted)
                require(persisted["status"] == "submitting" and digest(persisted["descriptor"]) == digest(descriptor.frozen()))
                raw = self.capture.capture(descriptor, api_key)
            except Exception:
                with self.store.transaction() as payload:
                    payload["asset_design"]["operation:" + operation_id]["status"] = "unknown"
                    return {"operation": copy.deepcopy(payload["asset_design"]["operation:" + operation_id]), "replayed": False}
            with self.store.transaction() as payload:
                operation = payload["asset_design"]["operation:" + operation_id]
                self._adopt(payload, operation, raw)
                return {"operation": copy.deepcopy(operation), "replayed": False}

    def recover(self, task_id, operation_id):
        with self._lock(task_id):
            payload = self.store.read_only()
            operation = payload.get("asset_design", {}).get("operation:" + operation_id)
            require(operation is not None and operation["task_id"] == task_id, "NOT_FOUND")
            if operation["raw"] is not None or operation["capture_started_at"] is None:
                return {"operation": copy.deepcopy(operation), "replayed": True}
            descriptor = FrozenEmpiricalDescriptor.from_frozen(operation["descriptor"])
            raw, _ = self.capture.receipts.ready_raw_binding(operation_id, descriptor.binding())
            with self.store.transaction() as payload:
                operation = payload["asset_design"]["operation:" + operation_id]
                before = digest(operation)
                if raw is None:
                    operation["status"] = "unknown"
                else:
                    self._adopt(payload, operation, raw)
                return {"operation": copy.deepcopy(operation), "replayed": digest(operation) == before}

    def observe_fee(self, task_id, operation_id, export_bytes, *, observed_at):
        from core.execution.asset_design_ledger import fee_candidate
        from core.execution.media_ledger import fee_claimed, unresolved_text_claims
        with self._lock(task_id), self.store.transaction() as payload:
            operation = payload.get("asset_design", {}).get("operation:" + operation_id)
            require(operation is not None and operation["task_id"] == task_id, "NOT_FOUND")
            scope = payload["asset_design"]["scope:" + operation["scope_id"]]
            candidate = fee_candidate(scope, operation, export_bytes, observed_at)
            if operation["fee_id"] is not None:
                old = payload["asset_design"]["fee:" + operation["fee_id"]]
                require(digest(old["candidate"]) == digest(candidate), "FEE_EVIDENCE_CONFLICT")
                return {"operation": copy.deepcopy(operation), "replayed": True}
            require(not fee_claimed(payload, candidate["platform_log_id"], operation_id), "FEE_EVIDENCE_CONFLICT")
            require(not unresolved_text_claims(payload), "MEDIA_LEGACY_FEE_BRIDGE_REQUIRED")
            blob, fee_id = base64.b64encode(export_bytes).decode(), candidate["platform_log_id"]
            self._put(payload, "fee", fee_id, task_id, scope["id"], operation_id=operation_id, candidate=candidate, export_base64=blob)
            self._put(payload, "claim", fee_id, task_id, scope["id"], operation_id=operation_id, candidate_hash=digest(candidate))
            operation["fee_id"] = fee_id
            _, _, operation["status"] = classify(scope, operation, operation["raw"], candidate)
            return {"operation": copy.deepcopy(operation), "replayed": False}

    def revoke(self, task_id, grant_id):
        with self._lock(task_id), self.store.transaction() as payload:
            grant = payload.get("asset_design", {}).get("grant:" + grant_id)
            require(grant is not None and grant["task_id"] == task_id, "NOT_FOUND")
            grant["revoked_at"] = grant["revoked_at"] or utc_now()
            return {"grant": copy.deepcopy(grant)}

    def get(self, task_id):
        from core.execution.asset_design_ledger import projection
        payload = self.store.read_only()
        if task_id not in payload["tasks"]:
            raise CreatorError("NOT_FOUND", "Execution record not found", 404)
        return {"asset_design": projection(payload, task_id)}

    def verified_manifest(self, task_id, operation_id):
        from core.execution.asset_design_ledger import verified_manifest
        return verified_manifest(self.store.read_only(), task_id, operation_id)
