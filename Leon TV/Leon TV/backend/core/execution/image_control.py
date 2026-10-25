"""Explicit image scope authority bound to a native paid asset manifest."""
import hashlib
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Literal

from pydantic import Field, model_validator

from core.creator.store import CreatorError, digest
from core.execution.asset_control import (
    ENVELOPE_FIELDS,
    AssetAdmission,
    AssetAuthority,
    LoadedAssetControl,
)
from core.execution.asset_control import EXTRA_CODE as ASSET_CODE
from core.execution.asset_media_handoff import manifest_origin
from core.execution.control import MAX_CONTROL, _absolute, _call_weight, _code_closure, _json, _read
from core.execution.fees import FrozenFeeSource
from core.execution.image_policy import ImageToolPolicy, MediaBudgetAnchor
from core.execution.media_ledger import _stable_handoff, native_context
from core.execution.policy import now, units
from core.execution.receipts import timestamp
from core.execution.reference_derivation import checked_profile
from core.execution.store import ExecutionStore
from core.planner.protocol import Strict
from core.planner.review import json_copy

KIND = "image_execution_control_v1"
MESSAGE = "Explicit image operator control is unavailable"
SHA = r"^[0-9a-f]{64}$"
EXTRA_CODE = ASSET_CODE | {"core/execution/image_control.py", "core/execution/image_operator.py",
    "core/execution/asset_media_handoff.py", "core/execution/scope_budget.py", "core/execution/media_runtime.py",
    "core/execution/media_ledger.py", "core/execution/image_policy.py", "core/execution/image_capture.py",
    "core/execution/image_fees.py", "core/execution/image_receipts.py", "core/execution/reference_derivation.py"}


def unavailable():
    return CreatorError("IMAGE_CONTROL_UNAVAILABLE", MESSAGE, 503)


def require(value):
    if not value:
        raise unavailable()


def document_value(document, parsed):
    value = document.model_dump()
    if "reference_profile" not in parsed:
        value.pop("reference_profile")
    return value


class ImageAdmission(AssetAdmission):
    asset_operation_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    asset_manifest_hash: str = Field(pattern=SHA)
    asset_proof_hash: str = Field(pattern=SHA)


class AllocationBegin(Strict):
    schema_version: Literal[1]
    kind: Literal["image_execution_allocation_begin_v1"]
    operation_key: str = Field(min_length=1, max_length=256)
    allocation_hash: str = Field(pattern=SHA)
    request_sha256: str = Field(pattern=SHA)
    status: Literal["begun"]
    begun_at: str


class ImageControlDocument(Strict):
    schema_version: Literal[1]
    kind: Literal["image_execution_control_v1"]
    mode: Literal["observe_only", "enabled"]
    roots: dict[str, str]
    admission: ImageAdmission
    policy: dict
    budget_anchor: dict
    scope_envelope: dict
    fee_source: FrozenFeeSource
    authority: AssetAuthority
    code_manifest: dict[str, str] = Field(min_length=1, max_length=1024)
    reference_profile: dict | None = None

    @model_validator(mode="after")
    def checked(self):
        from core.execution.control import _overlap
        require(set(self.roots) == {"creator", "planner", "execution"})
        roots = [_absolute(value) for value in self.roots.values()]
        require(all(not _overlap(left, right) for index, left in enumerate(roots) for right in roots[index + 1:]))
        for path in (self.authority.authorization_path, self.authority.parent_ledger_path, self.authority.allocation_begin_path):
            _absolute(path)
        policy = ImageToolPolicy.model_validate({key: value for key, value in self.policy.items() if key != "policy_hash"}).frozen()
        anchor = MediaBudgetAnchor.model_validate({key: value for key, value in self.budget_anchor.items() if key != "budget_anchor_hash"}).frozen()
        require(digest(policy) == digest(self.policy) and digest(anchor) == digest(self.budget_anchor))
        require(anchor["scope_max_calls"] == policy["max_submissions"])
        require(units(anchor["scope_risk_cny"]) == units(policy["per_submission_risk_cny"]) * policy["max_submissions"])
        require(units(anchor["opening_risk_cny"]) >= 76370000 and anchor["opening_paid_call_count"] >= 48)
        require(self.fee_source.account_namespace == anchor["account_scope_id"])
        envelope = {"kind": "image_execution_scope_envelope_v1", **{key: anchor[key] for key in ENVELOPE_FIELDS}}
        envelope["envelope_hash"] = digest(envelope)
        require(digest(envelope) == digest(self.scope_envelope))
        if self.reference_profile is not None:
            require(digest(checked_profile(self.reference_profile)) == digest(self.reference_profile))
        for name, sha in self.code_manifest.items():
            path = PurePosixPath(name)
            require(bool(name) and str(path) == name and not path.is_absolute() and ".." not in path.parts and "\\" not in name)
            require(len(name) <= 500 and len(sha) == 64 and all(char in "0123456789abcdef" for char in sha))
        json_copy(self.model_dump(), limit=MAX_CONTROL)
        return self


@dataclass(frozen=True)
class LoadedImageControl(LoadedAssetControl):
    document: ImageControlDocument

    def check_private(self, static_roots=()):
        try:
            return super().check_private(static_roots)
        except Exception:
            raise unavailable() from None

    def _asset(self, payload):
        admission, anchor = self.document.admission, self.document.budget_anchor
        ready = manifest_origin(payload, admission.task_id, admission.asset_operation_id)
        origin = ready["origin"]
        require(origin["manifest_hash"] == admission.asset_manifest_hash)
        require(origin["asset_proof"]["proof_hash"] == admission.asset_proof_hash)
        scope = payload["asset_design"]["scope:" + origin["asset_proof"]["scope_id"]]
        require(scope["task_id"] == admission.task_id and scope["account_id"] == anchor["account_scope_id"])
        require(scope["anchor"]["parent_authorization_hash"] == anchor["parent_authorization_hash"])
        require(digest(scope["fee_source"]) == digest(self.document.fee_source.model_dump()))
        require(units(anchor["opening_risk_cny"]) >= units(scope["anchor"]["account_limit_cny"]))
        require(anchor["opening_paid_call_count"] >= scope["anchor"]["account_max_calls"])
        return ready

    def verified_asset(self):
        """Read the original receipt, fee and manifest; never grant dispatch."""
        try:
            return self._asset(ExecutionStore(self.roots["execution"]).read_only())
        except Exception:
            raise unavailable() from None

    def _matching(self, payload, ready):
        return [record for record in payload.get("media", {}).values()
                if record["kind"] == "scope" and record["task_id"] == self.document.admission.task_id
                and digest(record["policy"]) == digest(self.document.policy)
                and digest(record["anchor"]) == digest(self.document.budget_anchor)
                and digest(record["manifest"]) == digest(ready["manifest"])
                and digest(record.get("manifest_origin")) == digest(ready["origin"])
                and digest(record.get("reference_profile")) == digest(self.document.reference_profile)]

    def _admission(self, payload):
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
        require(all(source[key] == getattr(admission, key) for key in ("snapshot_hash", "source_hash", "brief_id", "brief_revision")))
        brief = briefs["briefs"][admission.brief_id]["versions"][admission.brief_revision - 1]
        require(brief["revision"] == admission.brief_revision and brief["source_hash"] == admission.source_hash)
        require(all(source[key] == brief["content"][key] for key in ("title", "script", "requirements", "production", "budget_cny")))
        native = payload["tasks"][admission.task_id]
        require(native["planner_task_revision"] == admission.planner_task_revision and digest(native["source"]) == digest(source))
        ready = self._asset(payload)
        frozen = self._matching(payload, ready)
        require(len(frozen) <= 1)
        if frozen:
            current_context, current_handoff = native_context(payload, admission.task_id, historical=True)
            context, handoff = frozen[0]["asset_input"], frozen[0]["handoff"]
            require(digest(context) == digest(current_context))
            require(digest(_stable_handoff(handoff)) == digest(_stable_handoff(current_handoff)))
        else:
            context, handoff = native_context(payload, admission.task_id)
        expected = {"plan_version": handoff["plan"]["version"], "plan_hash": handoff["plan"]["plan_hash"],
            "acceptance_input_hash": handoff["acceptance"]["input_hash"], "acceptance_report_hash": handoff["acceptance"]["report_hash"],
            "handoff_hash": handoff["handoff_hash"], "provenance_hash": handoff["provenance_hash"]}
        require(all(getattr(admission, key) == value for key, value in expected.items()))
        require(self.document.policy["validation_only"] is (handoff["evidence_kind"] == "synthetic"))
        require(ready["origin"]["asset_proof"]["asset_input_hash"] == digest(context))
        account = payload["accounts"][native["account_id"]]
        anchor = self.document.budget_anchor
        require(account["id"] == anchor["account_scope_id"] and account["anchor"]["parent_authorization_hash"] == anchor["parent_authorization_hash"])
        require(units(anchor["opening_risk_cny"]) >= account["limit_units"] and anchor["opening_paid_call_count"] >= account["max_calls"])
        return context, handoff, ready

    def _opening(self, payload, ready):
        from core.execution.scope_budget import scope_envelope
        matching = self._matching(payload, ready)
        require(len(matching) <= 1)
        if matching:
            return
        anchor = self.document.budget_anchor
        opening = scope_envelope(payload, anchor["account_scope_id"])
        require(units(anchor["opening_risk_cny"]) >= opening["risk_units"])
        require(anchor["opening_paid_call_count"] >= opening["max_calls"])

    def _verified(self, backend_root=None, *, for_dispatch=False):
        try:
            backend = _absolute(self.backend_root if backend_root is None else backend_root)
            require(backend == self.backend_root and type(for_dispatch) is bool)
            self.check_private()
            raw = _read(self.path, maximum=MAX_CONTROL)
            require(hashlib.sha256(raw).hexdigest() == self.control_hash)
            parsed = _json(raw)
            document = ImageControlDocument.model_validate(parsed)
            require(digest(document_value(document, parsed)) == digest(parsed))
            require(digest(document.model_dump()) == digest(self.document.model_dump()))
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
            require(type(ledger["schema_version"]) is int and ledger["schema_version"] == 1 and ledger["currency"] == "CNY")
            require(ledger["task_id"] == authorization["task_id"] and ledger["authorization_hash"] == anchor["parent_authorization_hash"])
            require(type(ledger["limit_fen"]) is int and ledger["limit_fen"] == 10000)
            operations = ledger["operations"]
            require(type(operations) is dict and len(operations) <= 10000)
            selected = operations[authority.allocation_key]
            require(type(selected) is dict and selected["operation_key"] == authority.allocation_key)
            require(digest(selected) == anchor["parent_allocation_hash"])
            request = selected["request"]
            require(type(request) is dict and selected["request_sha256"] == digest(request))
            require(type(selected["upper_fen"]) is int and selected["upper_fen"] * 10000 == units(anchor["scope_risk_cny"]))
            others = [value for key, value in operations.items() if key != authority.allocation_key]
            require(all(type(value) is dict and type(value.get("upper_fen")) is int and 0 <= value["upper_fen"] <= 10000 for value in operations.values()))
            require(sum(value["upper_fen"] for value in others) * 10000 == units(anchor["opening_risk_cny"]))
            require(sum(value["upper_fen"] for value in operations.values()) <= 10000)
            require(anchor["opening_paid_call_count"] >= sum(_call_weight(value) for value in others))
            payload = ExecutionStore(self.roots["execution"]).read_only()
            context, handoff, ready = self._admission(payload)
            asset_scope = payload["asset_design"]["scope:" + ready["origin"]["asset_proof"]["scope_id"]]
            allocations = [value for value in others if digest(value) == asset_scope["anchor"]["parent_allocation_hash"]]
            require(len(allocations) == 1)
            allocation = allocations[0]
            require(allocation["upper_fen"] == 100 and allocation["request"]["kind"] == "asset_design_scope_v1")
            require(type(allocation["request"]["max_new_calls"]) is int and allocation["request"]["max_new_calls"] == 1)
            require(allocation["request_sha256"] == digest(allocation["request"]))
            raw_begin = _read(authority.allocation_begin_path)
            require(hashlib.sha256(raw_begin).hexdigest() == authority.allocation_begin_sha256)
            parsed_begin = _json(raw_begin)
            begin = AllocationBegin.model_validate(parsed_begin).model_dump()
            require(digest(begin) == digest(parsed_begin) == anchor["parent_allocation_begin_hash"])
            require(begin["operation_key"] == authority.allocation_key and begin["allocation_hash"] == digest(selected))
            require(begin["request_sha256"] == selected["request_sha256"] and timestamp(begin["begun_at"]) <= now())
            expected = {"kind": "image_execution_scope_v1", "model": policy["model"], "endpoint": policy["origin"] + "/v1/images/generations/async",
                "query_endpoint_prefix": policy["origin"] + "/v1/tasks/", "source_hash": document.admission.source_hash,
                "admission": document.admission.model_dump(), "policy_hash": policy["policy_hash"], "max_new_calls": policy["max_submissions"],
                "max_queries": policy["max_queries"], "per_submission_risk_cny": policy["per_submission_risk_cny"],
                "scope_risk_cny": anchor["scope_risk_cny"], "risk_basis": policy["risk_basis"],
                "scope_envelope_hash": document.scope_envelope["envelope_hash"], "backend_manifest_hash": digest(document.code_manifest),
                "fee_source_hash": digest(document.fee_source.model_dump()), "account_scope_id": anchor["account_scope_id"],
                "asset_operation_id": document.admission.asset_operation_id, "asset_manifest_hash": document.admission.asset_manifest_hash,
                "asset_proof_hash": document.admission.asset_proof_hash, "manifest_origin_hash": ready["origin"]["origin_hash"],
                "reference_profile_hash": None if document.reference_profile is None else document.reference_profile["profile_hash"]}
            require(all(key in request and type(request[key]) is type(value) and digest(request[key]) == digest(value) for key, value in expected.items()))
            if for_dispatch:
                self._opening(payload, ready)
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
            return context, handoff, ready, payload
        except Exception:
            raise unavailable() from None

    def verify_authority(self, backend_root=None, *, for_dispatch=False):
        self._verified(backend_root, for_dispatch=for_dispatch)
        return True

    def guard(self, scope):
        try:
            context, handoff, ready, payload = self._verified(for_dispatch=True)
            json_copy(scope, limit=16 * 1048576)
            require(scope["task_id"] == self.document.admission.task_id)
            require(digest(scope["policy"]) == digest(self.document.policy) and digest(scope["anchor"]) == digest(self.document.budget_anchor))
            require(digest(scope["fee_source"]) == digest(self.document.fee_source.model_dump()))
            require(digest(scope["asset_input"]) == digest(context) and digest(scope["handoff"]) == digest(handoff))
            require(digest(scope["manifest"]) == digest(ready["manifest"]) and digest(scope.get("manifest_origin")) == digest(ready["origin"]))
            require(digest(scope.get("reference_profile")) == digest(self.document.reference_profile))
            identity = {"task_id": scope["task_id"], "policy_hash": self.document.policy["policy_hash"],
                "budget_anchor_hash": self.document.budget_anchor["budget_anchor_hash"], "manifest_hash": self.document.admission.asset_manifest_hash,
                "handoff_hash": handoff["handoff_hash"], "manifest_origin_hash": digest(ready["origin"])}
            if self.document.reference_profile is not None:
                identity["reference_profile_hash"] = self.document.reference_profile["profile_hash"]
            require(scope["id"] == scope["scope_id"] == digest(identity)[:32])
            require(scope["account_id"] == self.document.budget_anchor["account_scope_id"])
            account = payload["accounts"][scope["account_id"]]
            require(scope["account_hash"] == digest(account))
            require(type(scope["schema_version"]) is int and scope["schema_version"] == 1 and scope["kind"] == "scope")
            return True
        except Exception:
            return False


def read_image_control(path, backend_root, *, static_roots=()):
    try:
        path, backend = _absolute(path), _absolute(backend_root)
        raw = _read(path, maximum=MAX_CONTROL)
        parsed = _json(raw)
        document = ImageControlDocument.model_validate(parsed)
        require(digest(document_value(document, parsed)) == digest(parsed))
        loaded = LoadedImageControl(document, hashlib.sha256(raw).hexdigest(), path, backend, tuple(static_roots))
        loaded.verify_authority()
        return loaded
    except Exception:
        raise unavailable() from None
