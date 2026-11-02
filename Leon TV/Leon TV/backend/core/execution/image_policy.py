"""Frozen image HTTP descriptors; these consistency proofs grant no authority.

The caller must separately verify a durable media allocation, grant and begin
record. Nothing here reserves money, writes journals or submits a request.
"""
import base64
import copy
import hashlib
import json
import re
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
from typing import Annotated, ClassVar, Literal

from PIL import Image
from pydantic import ConfigDict, Field, model_validator

from core.creator.store import CreatorError, digest
from core.execution.policy import units
from core.planner import product_assets
from core.planner.protocol import ShotId, Strict, parse_json
from core.planner.review import json_copy

KIND = "controlled_image_http_v1"
SCOPE = "accepted_plan_image_tools_v1"
MAX_BYTES = 1048576
MAX_DESCRIPTOR_BYTES = 8 * MAX_BYTES
_SNAPSHOT_PROOFS = ContextVar("image_descriptor_snapshot_proofs", default=None)
_MAX_SNAPSHOT_PROOFS = 32
_MAX_SNAPSHOT_TOKEN_NODES = 65536
Hash = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Identifier = Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")]
AMOUNT = re.compile(r"^(?:0|[1-9][0-9]{0,2})(?:\.[0-9]{1,6})?$")


class ImagePolicyError(CreatorError):
    def __init__(self):
        super().__init__("IMAGE_TOOL_UNAVAILABLE", "Frozen image tool evidence is unavailable or inconsistent", 409)


def require(value):
    if not value:
        raise ImagePolicyError()


def timestamp(value):
    require(type(value) is str)
    result = datetime.fromisoformat(value)
    require(result.tzinfo is not None)
    return result


def amount(value):
    require(type(value) is str and AMOUNT.fullmatch(value) is not None)
    return units(value)


class FrozenModel(Strict):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)
    seal_field: ClassVar[str]

    @model_validator(mode="before")
    @classmethod
    def strict_literals(cls, value):
        if type(value) is dict:
            for field in ("schema_version", "first_frame_version"):
                if field in value:
                    require(type(value[field]) is int)
            if "operator_risk_approved" in value:
                require(type(value["operator_risk_approved"]) is bool)
        return value

    def frozen(self):
        value = self.model_dump()
        return {**value, self.seal_field: digest(value)}


class MediaBudgetAnchor(FrozenModel):
    seal_field = "budget_anchor_hash"
    schema_version: Literal[1] = 1
    account_scope_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")
    parent_authorization_hash: Hash
    parent_ledger_hash: Hash
    parent_allocation_hash: Hash
    parent_allocation_begin_hash: Hash
    original_budget_cny: str
    opening_risk_cny: str
    opening_paid_call_count: int = Field(ge=0, le=10000)
    scope_risk_cny: str
    scope_max_calls: int = Field(ge=1, le=12)
    account_limit_cny: str
    account_max_calls: int = Field(ge=1, le=10012)

    @model_validator(mode="after")
    def checked(self):
        require(amount(self.original_budget_cny) == 100000000)
        require(amount(self.scope_risk_cny) > 0)
        require(amount(self.opening_risk_cny) + amount(self.scope_risk_cny) == amount(self.account_limit_cny) <= 100000000)
        require(self.opening_paid_call_count + self.scope_max_calls == self.account_max_calls)
        return self


class ImageToolPolicy(FrozenModel):
    seal_field = "policy_hash"
    schema_version: Literal[1] = 1
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")
    version: str = Field(min_length=1, max_length=80)
    scope: Literal["accepted_plan_image_tools_v1"] = SCOPE
    origin: Literal["https://api.ai-tudou.net", "https://synthetic.invalid"]
    model: Literal["gpt-image-2-all"] = "gpt-image-2-all"
    ratio: Literal["9:16"] = "9:16"
    resolution: Literal["1k"] = "1k"
    quality: Literal["medium"] = "medium"
    max_submissions: int = Field(ge=1, le=12)
    max_queries: int = Field(ge=1, le=120)
    max_request_bytes: int = Field(ge=128, le=MAX_BYTES)
    max_response_bytes: int = Field(ge=128, le=MAX_BYTES)
    request_deadline_seconds: int = Field(ge=1, le=120)
    valid_until: str
    per_submission_risk_cny: str
    risk_basis: Literal["empirical_not_hard_billing_bound"] = "empirical_not_hard_billing_bound"
    operator_risk_approved: Literal[True]
    validation_only: bool

    @model_validator(mode="after")
    def checked(self):
        timestamp(self.valid_until)
        require(0 < amount(self.per_submission_risk_cny) <= 100000000)
        require(self.validation_only or self.origin == "https://api.ai-tudou.net")
        self.version.encode("utf-8")
        require(bool(self.version.strip()))
        return self


class ImageToolPermit(FrozenModel):
    seal_field = "permit_hash"
    schema_version: Literal[1] = 1
    operation_id: Identifier
    task_id: Identifier
    capture_id: Identifier
    nonce: Identifier
    action: Literal["submit", "query"]
    scope: Literal["accepted_plan_image_tools_v1"] = SCOPE
    job_kind: Literal["controlled_image_http_v1"] = KIND
    policy_hash: Hash
    budget_anchor_hash: Hash
    parent_authorization_hash: Hash
    parent_ledger_hash: Hash
    parent_allocation_hash: Hash
    parent_allocation_begin_hash: Hash
    grant_id: Identifier
    disclosure_hash: Hash
    handoff_hash: Hash
    provenance_hash: Hash
    asset_input_hash: Hash
    manifest_hash: Hash
    job_hash: Hash
    shot_id: ShotId
    first_frame_version: Literal[1]
    source_snapshot_hash: Hash
    plan_version: int = Field(ge=1, le=2)
    plan_hash: Hash
    acceptance_input_hash: Hash
    acceptance_report_hash: Hash
    scope_submission_index: int = Field(ge=1, le=12)
    query_index: int = Field(ge=0, le=120)
    paid_calls: int = Field(ge=0, le=1)
    risk_cny: str
    issued_at: str
    deadline_at: str

    @model_validator(mode="after")
    def checked(self):
        require(self.capture_id == self.operation_id)
        require(type(self.first_frame_version) is int)
        require(timestamp(self.issued_at) < timestamp(self.deadline_at))
        if self.action == "submit":
            require(self.paid_calls == 1 and self.query_index == 0 and amount(self.risk_cny) > 0)
        else:
            require(self.paid_calls == 0 and self.query_index >= 1 and amount(self.risk_cny) == 0)
        return self


class ImageReference(Strict):
    shot_id: ShotId
    first_frame_version: Literal[1]
    mime_type: Literal["image/png", "image/jpeg"]
    bytes_base64: str = Field(min_length=1, max_length=4 * ((MAX_BYTES + 2) // 3))
    sha256: Hash

    @model_validator(mode="before")
    @classmethod
    def strict_version(cls, value):
        if type(value) is dict and "first_frame_version" in value:
            require(type(value["first_frame_version"]) is int)
        return value

    @model_validator(mode="after")
    def checked(self):
        require(type(self.first_frame_version) is int)
        raw = base64.b64decode(self.bytes_base64, validate=True)
        require(0 < len(raw) <= MAX_BYTES)
        require(base64.b64encode(raw).decode("ascii") == self.bytes_base64)
        require(hashlib.sha256(raw).hexdigest() == self.sha256)
        with Image.open(BytesIO(raw)) as image:
            require(image.format == {"image/png": "PNG", "image/jpeg": "JPEG"}[self.mime_type])
            require(0 < image.width <= 4096 and 0 < image.height <= 4096)
            require(getattr(image, "n_frames", 1) == 1 and not getattr(image, "is_animated", False))
            image.verify()
        with Image.open(BytesIO(raw)) as image:
            image.load()
        return self


def _model(value, cls):
    if isinstance(value, cls):
        value = value.frozen()
    value = json_copy(value, limit=MAX_DESCRIPTOR_BYTES)
    require(type(value) is dict)
    seal = value.pop(cls.seal_field, None)
    result = cls.model_validate(value).frozen()
    require(seal is None or type(seal) is str and seal == result[cls.seal_field])
    return result


def _authority(policy, anchor, permit):
    require(permit["policy_hash"] == policy["policy_hash"] and permit["budget_anchor_hash"] == anchor["budget_anchor_hash"])
    for field in ("parent_authorization_hash", "parent_ledger_hash", "parent_allocation_hash", "parent_allocation_begin_hash"):
        require(permit[field] == anchor[field])
    require(policy["max_submissions"] == anchor["scope_max_calls"])
    require(amount(policy["per_submission_risk_cny"]) * policy["max_submissions"] == amount(anchor["scope_risk_cny"]))
    require(permit["scope_submission_index"] <= policy["max_submissions"])
    require(permit["query_index"] <= policy["max_queries"])
    issued, deadline = timestamp(permit["issued_at"]), timestamp(permit["deadline_at"])
    require(0 < (deadline - issued).total_seconds() <= policy["request_deadline_seconds"])
    require(deadline <= timestamp(policy["valid_until"]))
    if permit["action"] == "submit":
        require(amount(permit["risk_cny"]) == amount(policy["per_submission_risk_cny"]))


def _submit_data(*, policy, budget_anchor, permit, asset_input, manifest, shot_id, references):
    policy, anchor, permit = _model(policy, ImageToolPolicy), _model(budget_anchor, MediaBudgetAnchor), _model(permit, ImageToolPermit)
    require(permit["action"] == "submit" and permit["shot_id"] == shot_id)
    _authority(policy, anchor, permit)
    asset_input = product_assets.checked_asset_payload(asset_input)
    require(permit["task_id"] == asset_input["acceptance_payload"]["binding"]["task_id"])
    manifest = json_copy(manifest)
    jobs = product_assets.image_jobs(asset_input, manifest, expected_manifest_hash=permit["manifest_hash"])
    matches = [job for job in jobs if job["shot_id"] == shot_id]
    require(len(matches) == 1)
    job = matches[0]
    require(permit["asset_input_hash"] == digest(asset_input) and permit["job_hash"] == digest(job))
    for field in ("source_snapshot_hash", "plan_version", "plan_hash", "acceptance_input_hash", "acceptance_report_hash"):
        require(permit[field] == job[field])
    references = json_copy(references, limit=MAX_DESCRIPTOR_BYTES)
    require(type(references) is list)
    references = [ImageReference.model_validate(reference).model_dump() for reference in references]
    require([reference["shot_id"] for reference in references] == job["reference_shot_ids"])
    body = {"model": policy["model"], "prompt": job["prompt"], "size": policy["ratio"], "resolution": policy["resolution"], "quality": policy["quality"]}
    if references:
        body["images"] = [f"data:{reference['mime_type']};base64,{reference['bytes_base64']}" for reference in references]
    wire = json.dumps(body, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    require(len(wire.encode("utf-8")) <= policy["max_request_bytes"])
    return {"schema_version": 1, "kind": KIND, "scope": SCOPE, "method": "POST",
        "endpoint": policy["origin"] + "/v1/images/generations/async", "wire": wire,
        "policy": policy, "budget_anchor": anchor, "permit": permit, "asset_input": asset_input,
        "manifest": manifest, "job": job, "references": references,
        "parent_submit": None, "submit_raw": None, "query_index": 0, "accepted_task_id": None}


def _query_data(*, submit, submit_raw, permit, query_index):
    from core.execution.image_receipts import decode_submit

    parent = submit.frozen() if isinstance(submit, FrozenImageRequest) else json_copy(submit, limit=MAX_DESCRIPTOR_BYTES)
    require(type(parent) is dict and parent.get("method") == "POST" and parent.get("parent_submit") is None)
    submit = FrozenImageRequest.from_frozen(parent)
    parent = submit.frozen()
    submit_raw = json_copy(submit_raw, limit=2 * MAX_BYTES)
    receipt = decode_submit(submit, submit_raw)
    require(receipt["classification"] == "accepted")
    accepted = receipt["accepted_task_id"]
    require(type(accepted) is str and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", accepted) is not None)
    permit = _model(permit, ImageToolPermit)
    require(type(query_index) is int and query_index >= 1 and permit["action"] == "query" and permit["query_index"] == query_index)
    _authority(parent["policy"], parent["budget_anchor"], permit)
    changed = {"operation_id", "capture_id", "nonce", "action", "query_index", "paid_calls", "risk_cny", "issued_at", "deadline_at", "permit_hash"}
    require(all(permit[field] == value for field, value in parent["permit"].items() if field not in changed))
    require(all(permit[field] != parent["permit"][field] for field in ("operation_id", "capture_id", "nonce")))
    require(timestamp(permit["issued_at"]) >= timestamp(receipt["received_at"]))
    return {**{key: value for key, value in parent.items() if key != "descriptor_hash"}, "method": "GET",
        "endpoint": parent["policy"]["origin"] + "/v1/tasks/" + accepted, "wire": "", "permit": permit,
        "parent_submit": parent, "submit_raw": submit_raw, "query_index": query_index, "accepted_task_id": accepted}


def _seal(value):
    return {**value, "descriptor_hash": digest(value)}


@contextmanager
def _descriptor_snapshot():
    """Reuse pure full-input proofs during one synchronous journal rebuild.

    Only successfully reconstructed strict JSON inputs enter this bounded set.
    No authority, time, filesystem or credential result is retained. Every new
    journal snapshot gets a fresh set, including nested reconstructions.
    """
    from core.execution.image_receipts import _delivery_snapshot
    from core.execution.reference_derivation import _reference_snapshot

    facts = {"inputs": set(), "bytes": 0, "frozen": {}, "frozen_bytes": 0,
             "tokens": {}, "token_bytes": 0, "token_nodes": 0,
             "active": True, "thread": threading.get_ident()}
    token = _SNAPSHOT_PROOFS.set(facts)
    try:
        with _reference_snapshot(), _delivery_snapshot():
            yield
    finally:
        facts["active"] = False
        facts["inputs"].clear()
        facts["frozen"].clear()
        facts["tokens"].clear()
        for pool in facts.get("value_proofs", {}).values():
            pool["inputs"].clear()
            pool["bytes"] = 0
        facts.get("value_proofs", {}).clear()
        facts["bytes"] = facts["frozen_bytes"] = 0
        facts["token_bytes"] = facts["token_nodes"] = 0
        _SNAPSHOT_PROOFS.reset(token)


def _snapshot_proofs():
    facts = _SNAPSHOT_PROOFS.get()
    return facts if facts is not None and facts["active"] and facts["thread"] == threading.get_ident() else None


def _checked(value):
    try:
        proofs = _snapshot_proofs()
        serialized = None
        if proofs is not None:
            from core.planner.review import json_value

            # One strict serialization replaces json_copy + a second full
            # serialization. Default whitespace preserves json_copy's exact
            # byte-size limit and input order. This proof is local
            # to this read, keyed by the entire actual input, never its seal.
            require(json_value(value))
            serialized = json.dumps(value, ensure_ascii=False, allow_nan=False)
            require(len(serialized.encode("utf-8")) <= MAX_DESCRIPTOR_BYTES)
            if serialized in proofs["inputs"]:
                return parse_json(serialized)
            value = parse_json(serialized)
        else:
            value = json_copy(value, limit=MAX_DESCRIPTOR_BYTES)
        require(type(value) is dict and value.get("descriptor_hash") == digest({key: item for key, item in value.items() if key != "descriptor_hash"}))
        if value.get("kind") == "controlled_image_comparison_http_v1":
            from core.execution.image_comparison_policy import rebuild

            rebuilt = rebuild(value)
        elif value.get("kind") == "controlled_image_comparison_observation_http_v1":
            from core.execution.image_comparison_observation_policy import rebuild

            rebuilt = rebuild(value)
        elif value.get("kind") == "controlled_image_observation_http_v1":
            from core.execution.image_observation_policy import rebuild

            rebuilt = rebuild(value)
        elif value.get("kind") in {"controlled_image_http_v2", "controlled_image_http_v3"}:
            from core.execution.image_continuation_policy import rebuild

            rebuilt = rebuild(value)
        elif value.get("method") == "POST":
            rebuilt = _submit_data(policy=value["policy"], budget_anchor=value["budget_anchor"], permit=value["permit"],
                asset_input=value["asset_input"], manifest=value["manifest"], shot_id=value["job"]["shot_id"], references=value["references"])
        else:
            require(value.get("method") == "GET")
            rebuilt = _query_data(submit=value["parent_submit"], submit_raw=value["submit_raw"], permit=value["permit"], query_index=value["query_index"])
        require(digest(value) == digest(_seal(rebuilt)))
        if serialized is not None and len(proofs["inputs"]) < _MAX_SNAPSHOT_PROOFS:
            size = len(serialized.encode("utf-8"))
            if proofs["bytes"] + size <= MAX_DESCRIPTOR_BYTES:
                proofs["inputs"].add(serialized)
                proofs["bytes"] += size
        return value
    except Exception:
        raise ImagePolicyError() from None


@dataclass(frozen=True)
class FrozenImageRequest:
    _serialized: str

    @classmethod
    def from_frozen(cls, value):
        proofs = _snapshot_proofs()
        if proofs is not None and cls is FrozenImageRequest:
            from core.execution.validation_read_proofs import _key, _thaw

            try:
                nodes = [0]
                captured, _ = _key(value, _budget=nodes)
            except (ValueError, TypeError, RecursionError):
                pass
            else:
                # A complete typed tree, not an object id or claimed hash.
                # Validation consumes this same immutable captured input.
                key = (captured, MAX_DESCRIPTOR_BYTES, MAX_BYTES)
                if key in proofs["tokens"]:
                    return cls(proofs["tokens"][key])
                checked = _checked(_thaw(captured))
                serialized = json.dumps(checked, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
                size = len(serialized.encode("utf-8"))
                if (len(proofs["tokens"]) < _MAX_SNAPSHOT_PROOFS
                        and proofs["token_bytes"] + size <= MAX_DESCRIPTOR_BYTES
                        and proofs["token_nodes"] + nodes[0] <= _MAX_SNAPSHOT_TOKEN_NODES):
                    proofs["tokens"][key] = serialized
                    proofs["token_bytes"] += size
                    proofs["token_nodes"] += nodes[0]
                return cls(serialized)
        checked = _checked(value)
        return cls(json.dumps(checked, ensure_ascii=False, allow_nan=False, separators=(",", ":")))

    def frozen(self):
        serialized = self._serialized
        proofs = _snapshot_proofs()
        if proofs is None or type(serialized) is not str:
            return _checked(parse_json(serialized))
        # The complete immutable JSON token is the key, never its claimed seal
        # or object identity. Only a successful full reconstruction is stored;
        # mutable returned dictionaries cannot modify this private proof.
        key = (serialized, MAX_DESCRIPTOR_BYTES, MAX_BYTES)
        if key in proofs["frozen"]:
            return copy.deepcopy(proofs["frozen"][key])
        value = _checked(parse_json(serialized))
        size = len(serialized.encode("utf-8"))
        if len(proofs["frozen"]) < _MAX_SNAPSHOT_PROOFS and proofs["frozen_bytes"] + size <= MAX_DESCRIPTOR_BYTES:
            proofs["frozen"][key] = copy.deepcopy(value)
            proofs["frozen_bytes"] += size
        return value

    def binding(self):
        value = self.frozen()
        permit = value["permit"]
        proof_fields = {key: item for key, item in permit.items() if key.endswith("_hash")}
        continuation = {key: permit[key] for key in ("runtime_activation_id", "runtime_revision_sequence", "attempt_version", "replacement_of")} if value["kind"] in {"controlled_image_http_v2", "controlled_image_http_v3"} else {}
        if value["kind"] == "controlled_image_comparison_http_v1":
            continuation = {"comparison_activation_id": permit["comparison_activation_id"], "purpose": permit["purpose"]}
            continuation.update(response_deadline_at=permit["response_deadline_at"],
                response_window_seconds=value["comparison_authority"]["response_policy"]["response_window_seconds"],
                response_contract_hash=permit["response_contract_hash"])
        if value["kind"] == "controlled_image_http_v3":
            continuation.update(response_deadline_at=permit["response_deadline_at"],
                response_window_seconds=value["continuation_authority"]["response_policy"]["response_window_seconds"],
                response_contract_hash=permit["response_contract_hash"])
        if value["kind"] == "controlled_image_observation_http_v1":
            continuation = {key: permit[key] for key in ("observation_activation_id", "observation_revision_sequence", "source_operation_id")}
        if value["kind"] == "controlled_image_comparison_observation_http_v1":
            continuation = {key: permit[key] for key in ("observation_activation_id", "observation_revision_sequence", "source_operation_id")}
            continuation.update(response_deadline_at=permit["response_deadline_at"],
                response_window_seconds=value["observation_authority"]["response_policy"]["response_window_seconds"],
                response_contract_hash=permit["response_contract_hash"])
        return {"kind": value["kind"], "scope": value["scope"], "operation_id": permit["operation_id"], "task_id": permit["task_id"],
            "capture_id": permit["capture_id"], "nonce": permit["nonce"], "deadline_at": permit["deadline_at"],
            "action": permit["action"], "method": value["method"], "descriptor_hash": value["descriptor_hash"],
            "wire_hash": digest(value["wire"]), "query_index": value["query_index"],
            "parent_descriptor_hash": value["parent_submit"]["descriptor_hash"] if value["parent_submit"] else None,
            "submit_raw_hash": digest(value["submit_raw"]) if value["submit_raw"] else None, **proof_fields, **continuation}

    @property
    def method(self):
        return self.frozen()["method"]

    @property
    def endpoint(self):
        return self.frozen()["endpoint"]

    @property
    def wire(self):
        return self.frozen()["wire"]

    @property
    def validation_only(self):
        return self.frozen()["policy"]["validation_only"]

    @property
    def deadline_at(self):
        return self.frozen()["permit"]["deadline_at"]

    @property
    def descriptor_hash(self):
        return self.frozen()["descriptor_hash"]

    @property
    def max_response_bytes(self):
        return self.frozen()["policy"]["max_response_bytes"]


def build_image_submit(*, policy, budget_anchor, permit, asset_input, manifest, shot_id, references=()):
    """Build exact paid POST bytes from a verified product manifest/job only."""
    try:
        references = [] if references == () and type(references) is tuple else references
        return FrozenImageRequest.from_frozen(_seal(_submit_data(policy=policy, budget_anchor=budget_anchor, permit=permit,
            asset_input=asset_input, manifest=manifest, shot_id=shot_id, references=references)))
    except Exception:
        raise ImagePolicyError() from None


def build_image_query(*, submit, submit_raw, permit, query_index):
    """Build an independent GET permit for the task actually accepted by POST."""
    try:
        candidate = submit.frozen() if isinstance(submit, FrozenImageRequest) else submit
        if type(candidate) is dict and candidate.get("kind") in {"controlled_image_http_v2", "controlled_image_http_v3"}:
            from core.execution.image_continuation_policy import build_image_query_v2

            return build_image_query_v2(submit=candidate, submit_raw=submit_raw, permit=permit, query_index=query_index)
        return FrozenImageRequest.from_frozen(_seal(_query_data(submit=submit, submit_raw=submit_raw, permit=permit, query_index=query_index)))
    except Exception:
        raise ImagePolicyError() from None
