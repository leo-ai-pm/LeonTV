"""Explicit local asset-design authority; no ordinary bootstrap or environment hook."""
import hashlib
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import Field, model_validator

from core.creator.store import CreatorError, digest
from core.execution.control import (
    MAX_CONTROL,
    Admission,
    Authority,
    _absolute,
    _call_weight,
    _code_closure,
    _json,
    _overlap,
    _private_path,
    _read,
)
from core.execution.fees import FrozenFeeSource
from core.execution.image_policy import MediaBudgetAnchor
from core.execution.media_ledger import native_context
from core.execution.policy import now, units
from core.execution.receipts import timestamp
from core.execution.store import ExecutionStore
from core.planner.protocol import Strict
from core.planner.review import json_copy

KIND = "asset_design_control_v1"
MESSAGE = "Explicit asset operator control is unavailable"
SHA = r"^[0-9a-f]{64}$"
EXTRA_CODE = {"core/execution/asset_control.py", "core/execution/asset_operator.py",
              "core/execution/asset_design.py", "core/execution/asset_design_ledger.py",
              "core/planner/product_assets.py", "core/planner/actors.py"}
ENVELOPE_FIELDS = ("original_budget_cny", "opening_risk_cny", "opening_paid_call_count", "scope_risk_cny",
                   "scope_max_calls", "account_limit_cny", "account_max_calls")


def unavailable():
    return CreatorError("ASSET_CONTROL_UNAVAILABLE", MESSAGE, 503)


def require(value):
    if not value:
        raise unavailable()


class AssetAuthority(Authority):
    allocation_begin_path: str
    allocation_begin_sha256: str = Field(pattern=SHA)


class AssetAdmission(Admission):
    plan_version: int = Field(ge=1, le=2)
    plan_hash: str = Field(pattern=SHA)
    acceptance_input_hash: str = Field(pattern=SHA)
    acceptance_report_hash: str = Field(pattern=SHA)
    handoff_hash: str = Field(pattern=SHA)
    provenance_hash: str = Field(pattern=SHA)


class AllocationBegin(Strict):
    schema_version: Literal[1]
    kind: Literal["asset_design_allocation_begin_v1"]
    operation_key: str = Field(min_length=1, max_length=256)
    allocation_hash: str = Field(pattern=SHA)
    request_sha256: str = Field(pattern=SHA)
    status: Literal["begun"]
    begun_at: str


class AssetControlDocument(Strict):
    schema_version: Literal[1]
    kind: Literal["asset_design_control_v1"]
    mode: Literal["observe_only", "enabled"]
    roots: dict[str, str]
    admission: AssetAdmission
    policy: dict
    budget_anchor: dict
    scope_envelope: dict
    fee_source: FrozenFeeSource
    authority: AssetAuthority
    code_manifest: dict[str, str] = Field(min_length=1, max_length=1024)

    @model_validator(mode="after")
    def checked(self):
        from core.execution.asset_design import AssetDesignPolicy
        require(set(self.roots) == {"creator", "planner", "execution"})
        roots = [_absolute(value) for value in self.roots.values()]
        require(all(not _overlap(left, right) for index, left in enumerate(roots) for right in roots[index + 1:]))
        for value in (self.authority.authorization_path, self.authority.parent_ledger_path, self.authority.allocation_begin_path):
            _absolute(value)
        policy = AssetDesignPolicy.model_validate({k: v for k, v in self.policy.items() if k != "policy_hash"}).frozen()
        anchor = MediaBudgetAnchor.model_validate({k: v for k, v in self.budget_anchor.items() if k != "budget_anchor_hash"}).frozen()
        require(digest(policy) == digest(self.policy) and digest(anchor) == digest(self.budget_anchor))
        require(anchor["scope_max_calls"] == policy["max_calls"] == 1 and units(anchor["scope_risk_cny"]) == units(policy["per_call_risk_cny"]) == 1000000)
        require(units(anchor["original_budget_cny"]) == 100000000 and units(anchor["opening_risk_cny"]) >= 75370000 and anchor["opening_paid_call_count"] >= 47)
        envelope = {"kind": "asset_design_scope_envelope_v1", **{name: anchor[name] for name in ENVELOPE_FIELDS}}
        envelope["envelope_hash"] = digest(envelope)
        require(digest(self.scope_envelope) == digest(envelope))
        require(self.fee_source.account_namespace == anchor["account_scope_id"])
        for name, sha in self.code_manifest.items():
            path = PurePosixPath(name)
            require(bool(name) and str(path) == name and not path.is_absolute() and ".." not in path.parts and "\\" not in name)
            require(len(name) <= 500 and len(sha) == 64 and all(char in "0123456789abcdef" for char in sha))
        json_copy(self.model_dump(), limit=MAX_CONTROL)
        return self


@dataclass(frozen=True)
class LoadedAssetControl:
    document: AssetControlDocument
    control_hash: str
    path: Path
    backend_root: Path
    static_roots: tuple = field(default=(), repr=False)

    @property
    def roots(self):
        return {key: Path(value) for key, value in self.document.roots.items()}

    def check_private(self, static_roots=()):
        try:
            static = (self.backend_root / "code", self.backend_root.parent / "frontend/public",
                      *self.static_roots, *(Path(root).resolve() for root in static_roots))
            proofs = [self.path, Path(self.document.authority.authorization_path), Path(self.document.authority.parent_ledger_path),
                      Path(self.document.authority.allocation_begin_path)]
            for path in [*proofs, *self.roots.values()]:
                _private_path(path, static)
            require(len(set(proofs)) == 4 and all(not proof.is_relative_to(root) for proof in proofs for root in self.roots.values()))
            return True
        except Exception:
            raise unavailable() from None

    def _admission(self):
        from core.creator.store import validate_state as validate_briefs
        from core.planner.store import validate_state as validate_planner
        admission = self.document.admission
        planner = _json(_read(self.roots["planner"] / "state.json"))
        briefs = _json(_read(self.roots["creator"] / "state.json"))
        validate_planner(planner)
        validate_briefs(briefs)
        task = planner["tasks"][admission.task_id]
        require(task["request"]["mode"] == "normal" and task["head_revision"] == admission.planner_task_revision)
        source = task["source"]
        require(all(source[name] == getattr(admission, name) for name in ("snapshot_hash", "source_hash", "brief_id", "brief_revision")))
        brief = briefs["briefs"][admission.brief_id]["versions"][admission.brief_revision - 1]
        require(brief["revision"] == admission.brief_revision and brief["source_hash"] == admission.source_hash)
        require(all(source[name] == brief["content"][name] for name in ("title", "script", "requirements", "production", "budget_cny")))
        payload = ExecutionStore(self.roots["execution"]).read_only()
        native = payload["tasks"][admission.task_id]
        require(native["planner_task_revision"] == admission.planner_task_revision and digest(native["source"]) == digest(source))
        frozen = [record for record in payload.get("asset_design", {}).values()
                  if record["kind"] == "scope" and record["task_id"] == admission.task_id
                  and digest(record["policy"]) == digest(self.document.policy)
                  and digest(record["anchor"]) == digest(self.document.budget_anchor)]
        require(len(frozen) <= 1)
        if frozen:
            from core.execution.media_ledger import _stable_handoff
            current_context, current_handoff = native_context(payload, admission.task_id, historical=True)
            context, handoff = frozen[0]["asset_input"], frozen[0]["handoff"]
            require(digest(context) == digest(current_context))
            require(digest(_stable_handoff(handoff)) == digest(_stable_handoff(current_handoff)))
        else:
            context, handoff = native_context(payload, admission.task_id)
        expected = {"plan_version": handoff["plan"]["version"], "plan_hash": handoff["plan"]["plan_hash"],
                    "acceptance_input_hash": handoff["acceptance"]["input_hash"], "acceptance_report_hash": handoff["acceptance"]["report_hash"],
                    "handoff_hash": handoff["handoff_hash"], "provenance_hash": handoff["provenance_hash"]}
        require(all(value == getattr(admission, key) for key, value in expected.items()))
        require(self.document.policy["validation_only"] is (handoff["evidence_kind"] == "synthetic"))
        account = payload["accounts"][native["account_id"]]
        anchor = self.document.budget_anchor
        require(account["id"] == anchor["account_scope_id"] and account["anchor"]["parent_authorization_hash"] == anchor["parent_authorization_hash"])
        require(units(anchor["opening_risk_cny"]) >= account["limit_units"] and anchor["opening_paid_call_count"] >= account["max_calls"])
        return context, handoff

    def _opening(self):
        from core.execution.scope_budget import scope_envelope
        payload = ExecutionStore(self.roots["execution"]).read_only()
        anchor = self.document.budget_anchor
        own = [key for key, record in payload.get("asset_design", {}).items()
               if record["kind"] == "scope" and record["task_id"] == self.document.admission.task_id
               and digest(record["policy"]) == digest(self.document.policy)
               and digest(record["anchor"]) == digest(anchor)]
        require(len(own) <= 1)
        if own:
            # The original store already reconstructs this scope's birth frame
            # against that frame's opening. Future allocations retain its risk.
            return
        opening = scope_envelope(payload, anchor["account_scope_id"])
        require(units(anchor["opening_risk_cny"]) >= opening["risk_units"])
        require(anchor["opening_paid_call_count"] >= opening["max_calls"])

    def verify_authority(self, backend_root=None, *, for_dispatch=False):
        try:
            backend = _absolute(self.backend_root if backend_root is None else backend_root)
            require(backend == self.backend_root and type(for_dispatch) is bool)
            self.check_private()
            raw = _read(self.path, maximum=MAX_CONTROL)
            require(hashlib.sha256(raw).hexdigest() == self.control_hash)
            parsed = _json(raw)
            document = AssetControlDocument.model_validate(parsed)
            require(digest(document.model_dump()) == digest(parsed) == digest(self.document.model_dump()))
            authority, anchor, policy = document.authority, document.budget_anchor, document.policy
            raw_authorization = _read(authority.authorization_path)
            require(hashlib.sha256(raw_authorization).hexdigest() == authority.authorization_sha256)
            authorization = _json(raw_authorization)
            require(digest(authorization) == anchor["parent_authorization_hash"])
            require(authorization["status"] == "approved_bounded_live_api_trial" and authorization["budget"]["currency"] == "CNY")
            require(type(authorization["budget"]["hard_total"]) is int and authorization["budget"]["hard_total"] == 100)
            require(authorization["provider_origin"] == policy["origin"])
            raw_ledger = _read(authority.parent_ledger_path)
            require(hashlib.sha256(raw_ledger).hexdigest() == anchor["parent_ledger_hash"])
            ledger = _json(raw_ledger)
            require(type(ledger["schema_version"]) is int and ledger["schema_version"] == 1)
            require(ledger["task_id"] == authorization["task_id"] and ledger["authorization_hash"] == anchor["parent_authorization_hash"] and ledger["currency"] == "CNY")
            require(type(ledger["limit_fen"]) is int and ledger["limit_fen"] == 10000)
            operations = ledger["operations"]
            require(type(operations) is dict and len(operations) <= 10000)
            selected = operations[authority.allocation_key]
            require(type(selected) is dict and selected["operation_key"] == authority.allocation_key and digest(selected) == anchor["parent_allocation_hash"])
            request = selected["request"]
            require(type(request) is dict and selected["request_sha256"] == digest(request))
            require(type(selected["upper_fen"]) is int and selected["upper_fen"] == 100)
            others = [value for key, value in operations.items() if key != authority.allocation_key]
            require(all(type(value) is dict and type(value.get("upper_fen")) is int and 0 <= value["upper_fen"] <= 10000 for value in operations.values()))
            require(sum(value["upper_fen"] for value in others) * 10000 == units(anchor["opening_risk_cny"]))
            require(sum(value["upper_fen"] for value in operations.values()) <= 10000)
            require(anchor["opening_paid_call_count"] >= sum(_call_weight(value) for value in others))
            raw_begin = _read(authority.allocation_begin_path)
            require(hashlib.sha256(raw_begin).hexdigest() == authority.allocation_begin_sha256)
            parsed_begin = _json(raw_begin)
            begin = AllocationBegin.model_validate(parsed_begin).model_dump()
            require(digest(begin) == digest(parsed_begin) == anchor["parent_allocation_begin_hash"])
            require(begin["operation_key"] == authority.allocation_key and begin["allocation_hash"] == digest(selected) and begin["request_sha256"] == selected["request_sha256"])
            require(timestamp(begin["begun_at"]) <= now())
            expected = {"kind": "asset_design_scope_v1", "model": policy["model"], "endpoint": policy["origin"] + "/v1/chat/completions",
                        "chat_output_parameter": policy["chat_output_parameter"], "source_hash": document.admission.source_hash,
                        "admission": document.admission.model_dump(), "policy_hash": policy["policy_hash"], "max_new_calls": 1,
                        "per_call_risk_cny": "1.000000", "scope_risk_cny": "1.000000", "risk_basis": policy["risk_basis"],
                        "scope_envelope_hash": document.scope_envelope["envelope_hash"], "backend_manifest_hash": digest(document.code_manifest),
                        "fee_source_hash": digest(document.fee_source.model_dump()), "account_scope_id": anchor["account_scope_id"]}
            require(all(name in request and type(request[name]) is type(value) and digest(request[name]) == digest(value) for name, value in expected.items()))
            self._admission()
            if for_dispatch:
                self._opening()
                require(document.mode == "enabled" and now() < timestamp(policy["valid_until"]))
                require(selected["status"] == "reserved" and selected.get("provider_task_id") is None and selected.get("actual_charge_fen") is None)
                require(request.get("scope_status") == "open")
                _code_closure(backend, document.code_manifest)
                require(EXTRA_CODE <= set(document.code_manifest))
                total = 0
                for name, sha in document.code_manifest.items():
                    code = _read(backend / name, maximum=4 * 1048576, private=False)
                    total += len(code)
                    require(total <= 128 * 1048576 and hashlib.sha256(code).hexdigest() == sha)
            return True
        except Exception:
            raise unavailable() from None

    def guard(self, scope):
        try:
            self.verify_authority(for_dispatch=True)
            context, handoff = self._admission()
            json_copy(scope, limit=16 * 1048576)
            require(scope["task_id"] == self.document.admission.task_id)
            require(digest(scope["policy"]) == digest(self.document.policy) and digest(scope["anchor"]) == digest(self.document.budget_anchor))
            require(digest(scope["fee_source"]) == digest(self.document.fee_source.model_dump()))
            require(digest(scope["asset_input"]) == digest(context) and digest(scope["handoff"]) == digest(handoff))
            return True
        except Exception:
            return False


def read_asset_control(path, backend_root, *, static_roots=()):
    try:
        path, backend = _absolute(path), _absolute(backend_root)
        raw = _read(path, maximum=MAX_CONTROL)
        parsed = _json(raw)
        document = AssetControlDocument.model_validate(parsed)
        require(digest(document.model_dump()) == digest(parsed))
        loaded = LoadedAssetControl(document, hashlib.sha256(raw).hexdigest(), path, backend, tuple(static_roots))
        loaded.verify_authority()
        return loaded
    except Exception:
        raise unavailable() from None
