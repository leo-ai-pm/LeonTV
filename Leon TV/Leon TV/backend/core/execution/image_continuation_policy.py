"""Explicit image continuation descriptors; pure proofs never grant dispatch.

The original four-call anchor remains intact. A separately approved runtime
authority describes the original allocation and its one-call extension.
"""
import json
import uuid
from datetime import timedelta
from typing import Literal

from pydantic import Field, model_validator

from core.creator.store import digest
from core.execution.image_policy import (
    MAX_DESCRIPTOR_BYTES,
    FrozenImageRequest,
    Hash,
    Identifier,
    ImagePolicyError,
    ImageReference,
    ImageToolPermit,
    ImageToolPolicy,
    MediaBudgetAnchor,
    _model,
    _seal,
    amount,
    require,
    timestamp,
)
from core.execution.policy import now
from core.planner import product_assets
from core.planner.protocol import ShotId, Strict
from core.planner.review import json_copy

KIND = "controlled_image_http_v2"
SCOPE = "accepted_plan_image_tools_v2"
RESPONSE_KIND = "controlled_image_http_v3"
RESPONSE_SCOPE = "accepted_plan_image_tools_v3"


class ImageContinuationPermit(ImageToolPermit):
    schema_version: Literal[2] = 2
    scope: Literal["accepted_plan_image_tools_v2"] = SCOPE
    job_kind: Literal["controlled_image_http_v2"] = KIND
    continuation_authority_hash: Hash
    runtime_activation_id: Identifier
    runtime_revision_sequence: int = Field(ge=1, le=10000)
    attempt_version: int = Field(ge=1, le=2)
    replacement_of: Identifier | None
    reference_bindings_hash: Hash

    @model_validator(mode="after")
    def checked_attempt(self):
        require((self.attempt_version == 2) is (self.replacement_of is not None))
        require(self.operation_id != self.replacement_of)
        return self


class ImageResponseContinuationPermit(ImageContinuationPermit):
    """A new native permit; historical v2 permits gain no response authority."""

    schema_version: Literal[4] = 4
    scope: Literal["accepted_plan_image_tools_v3"] = RESPONSE_SCOPE
    job_kind: Literal["controlled_image_http_v3"] = RESPONSE_KIND
    response_deadline_at: str
    response_contract_hash: Hash

    @model_validator(mode="after")
    def checked_response(self):
        require(timestamp(self.deadline_at) < timestamp(self.response_deadline_at))
        return self


class ImageReferenceBinding(Strict):
    source_operation_id: Identifier
    source_query_id: Identifier
    source_delivery_id: Identifier
    source_delivery_hash: Hash
    source_sha256: Hash
    shot_id: ShotId
    first_frame_version: Literal[1]

    @model_validator(mode="before")
    @classmethod
    def strict_version(cls, value):
        if type(value) is dict and "first_frame_version" in value:
            require(type(value["first_frame_version"]) is int)
        return value


def _checked_authority(value):
    from core.execution.image_runtime_continuation import validate_authority

    return validate_authority(value)


def _target(authority, shot_id):
    targets = [item for item in authority["remaining_jobs"] if item["shot_id"] == shot_id]
    require(len(targets) == 1)
    return targets[0]


def checked_reference_bindings(value, references):
    value = json_copy(value, limit=MAX_DESCRIPTOR_BYTES)
    require(type(value) is list and type(references) is list)
    result = [ImageReferenceBinding.model_validate(item).model_dump() for item in value]
    require(len(result) == len(references))
    require([item["shot_id"] for item in result] == [item["shot_id"] for item in references])
    require(len({item["source_operation_id"] for item in result}) == len(result))
    require(len({item["source_query_id"] for item in result}) == len(result))
    require(len({item["source_delivery_id"] for item in result}) == len(result))
    return result


def _source_identity(permit, bindings):
    require(all(permit["operation_id"] not in {item["source_operation_id"], item["source_query_id"], item["source_delivery_id"]}
                and permit["replacement_of"] != item["source_operation_id"] for item in bindings))


def bind_reference_sources(sources, references):
    """Bind original native deliveries, without copying their bytes into HTTP.

    The durable controller must independently match these sources to its own
    journal and verify any representation conversion proof.
    """
    from core.execution.reference_derivation import _source

    try:
        sources = json_copy(sources, limit=MAX_DESCRIPTOR_BYTES)
        references = json_copy(references, limit=MAX_DESCRIPTOR_BYTES)
        require(type(sources) is list and type(references) is list and len(sources) == len(references))
        result = []
        for source, reference in zip(sources, references, strict=True):
            _source(source)
            checked = ImageReference.model_validate(reference).model_dump()
            require(source["shot_id"] == checked["shot_id"] and source["first_frame_version"] == checked["first_frame_version"])
            result.append({"source_operation_id": source["operation_id"], "source_query_id": source["query_id"],
                "source_delivery_id": source["delivery_id"], "source_delivery_hash": source["delivery"]["delivery_hash"],
                "source_sha256": source["delivery"]["delivery_sha256"], "shot_id": source["shot_id"], "first_frame_version": 1})
        return checked_reference_bindings(result, references)
    except Exception:
        raise ImagePolicyError() from None


def _authority(policy, anchor, permit, authority):
    require(digest(policy) == digest(authority["effective_policy"]))
    require(digest(anchor) == digest(authority["original_budget_anchor"]))
    require(permit["policy_hash"] == policy["policy_hash"] and permit["budget_anchor_hash"] == anchor["budget_anchor_hash"])
    require(permit["continuation_authority_hash"] == authority["authority_hash"]
            and permit["runtime_activation_id"] == authority["activation_id"]
            and permit["runtime_revision_sequence"] == authority["revision_sequence"])
    require(permit["task_id"] == authority["task_id"] and permit["grant_id"] == authority["grant_id"])
    for field in ("parent_authorization_hash", "parent_ledger_hash", "parent_allocation_hash", "parent_allocation_begin_hash"):
        require(permit[field] == anchor[field])
    target = _target(authority, permit["shot_id"])
    require(permit["scope_submission_index"] == target["planned_submission_index"]
            and permit["attempt_version"] == target["attempt_version"] and permit["replacement_of"] == target["replacement_of"])
    require(permit["job_hash"] == target["job_hash"] and permit["first_frame_version"] == target["first_frame_version"])
    require(permit["operation_id"] != authority["unknown_disposition"]["operation_id"])
    require(permit["scope_submission_index"] <= policy["max_submissions"] == authority["max_cumulative_submissions"])
    require(permit["query_index"] <= policy["max_queries"])
    issued, deadline = timestamp(permit["issued_at"]), timestamp(permit["deadline_at"])
    require(0 < (deadline - issued).total_seconds() <= policy["request_deadline_seconds"])
    require(deadline <= timestamp(policy["valid_until"]))
    if authority["schema_version"] in {3, 4}:
        from core.execution.image_response_lifecycle import validate_response_policy

        response_policy = validate_response_policy(authority["response_policy"])
        require(permit["schema_version"] == 4 and permit["job_kind"] == RESPONSE_KIND
                and permit["scope"] == RESPONSE_SCOPE)
        require(policy["request_deadline_seconds"] == response_policy["dispatch_window_seconds"]
                and (deadline - issued).total_seconds() == response_policy["dispatch_window_seconds"])
        response_deadline = timestamp(permit["response_deadline_at"])
        require((response_deadline - deadline).total_seconds() == response_policy["response_window_seconds"])
        require(permit["response_contract_hash"] == response_policy["contract_hash"])
        require(response_deadline <= timestamp(policy["valid_until"]))
    else:
        require(authority["schema_version"] == 2 and permit["schema_version"] == 2
                and permit["job_kind"] == KIND and permit["scope"] == SCOPE)
    if permit["action"] == "submit":
        require(amount(permit["risk_cny"]) == amount(policy["per_submission_risk_cny"]))


def make_continuation_permit(scope, grant, job, authority, *, operation_id, index,
                             action="submit", query_index=0, reference_bindings=()):
    """Compile a fresh nonce and bounded request window from explicit authority."""
    try:
        authority = _checked_authority(authority)
        require(scope["id"] == authority["scope_id"] and digest(scope) == authority["scope_hash"])
        require(grant["id"] == authority["grant_id"] and digest(grant) == authority["grant_hash"])
        target = _target(authority, job["shot_id"])
        require(type(index) is int and index == target["planned_submission_index"])
        require(digest(job) == target["job_hash"])
        bindings = [] if type(reference_bindings) is tuple and reference_bindings == () else reference_bindings
        # Bytes and native source reconstruction happen in the caller/builder.
        bindings = json_copy(bindings, limit=MAX_DESCRIPTOR_BYTES)
        require(type(bindings) is list)
        bindings = [ImageReferenceBinding.model_validate(item).model_dump() for item in bindings]
        require([item["shot_id"] for item in bindings] == job["reference_shot_ids"])
        policy, anchor, binding = authority["effective_policy"], authority["original_budget_anchor"], scope["asset_input"]["binding"]
        issued = now()
        deadline = min(issued + timedelta(seconds=policy["request_deadline_seconds"]), timestamp(policy["valid_until"]))
        response = {}
        permit_type = ImageContinuationPermit
        if authority["schema_version"] in {3, 4}:
            from core.execution.image_response_lifecycle import validate_response_policy

            response_policy = validate_response_policy(authority["response_policy"])
            deadline = issued + timedelta(seconds=response_policy["dispatch_window_seconds"])
            response_deadline = deadline + timedelta(seconds=response_policy["response_window_seconds"])
            require(response_deadline <= timestamp(policy["valid_until"]))
            response = {"response_deadline_at": response_deadline.isoformat(),
                        "response_contract_hash": response_policy["contract_hash"]}
            permit_type = ImageResponseContinuationPermit
        value = {"operation_id": operation_id, "capture_id": operation_id, "task_id": scope["task_id"], "nonce": uuid.uuid4().hex,
            "action": action, "policy_hash": policy["policy_hash"], "budget_anchor_hash": anchor["budget_anchor_hash"],
            **{key: anchor[key] for key in ("parent_authorization_hash", "parent_ledger_hash", "parent_allocation_hash", "parent_allocation_begin_hash")},
            "grant_id": grant["id"], "disclosure_hash": grant["disclosure_hash"], "handoff_hash": scope["handoff"]["handoff_hash"],
            "provenance_hash": scope["handoff"]["provenance_hash"], "asset_input_hash": digest(scope["asset_input"]),
            "manifest_hash": scope["manifest"]["manifest_hash"], "job_hash": digest(job), "shot_id": job["shot_id"], "first_frame_version": 1,
            **{key: binding[key] for key in ("source_snapshot_hash", "plan_version", "plan_hash", "acceptance_input_hash", "acceptance_report_hash")},
            "scope_submission_index": index, "query_index": query_index, "paid_calls": 1 if action == "submit" else 0,
            "risk_cny": policy["per_submission_risk_cny"] if action == "submit" else "0.000000",
            "issued_at": issued.isoformat(), "deadline_at": deadline.isoformat(),
            "continuation_authority_hash": authority["authority_hash"], "runtime_activation_id": authority["activation_id"],
            "runtime_revision_sequence": authority["revision_sequence"], "attempt_version": target["attempt_version"],
            "replacement_of": target["replacement_of"], "reference_bindings_hash": digest(bindings), **response}
        checked = _model(value, permit_type)
        _authority(policy, anchor, checked, authority)
        return {key: item for key, item in checked.items() if key != "permit_hash"}
    except Exception:
        raise ImagePolicyError() from None


def _submit_data(*, policy, budget_anchor, permit, continuation_authority, asset_input,
                 manifest, shot_id, references, reference_bindings):
    policy, anchor = _model(policy, ImageToolPolicy), _model(budget_anchor, MediaBudgetAnchor)
    authority = _checked_authority(continuation_authority)
    permit = _model(permit, ImageResponseContinuationPermit if authority["schema_version"] in {3, 4} else ImageContinuationPermit)
    require(permit["action"] == "submit" and permit["shot_id"] == shot_id)
    _authority(policy, anchor, permit, authority)
    asset_input = product_assets.checked_asset_payload(asset_input)
    require(permit["task_id"] == asset_input["acceptance_payload"]["binding"]["task_id"])
    manifest = json_copy(manifest)
    jobs = product_assets.image_jobs(asset_input, manifest, expected_manifest_hash=permit["manifest_hash"])
    matches = [job for job in jobs if job["shot_id"] == shot_id]
    require(len(matches) == 1)
    job = matches[0]
    target = _target(authority, shot_id)
    require(permit["asset_input_hash"] == digest(asset_input) and permit["job_hash"] == digest(job))
    require(target["reference_shot_ids"] == job["reference_shot_ids"])
    for field in ("source_snapshot_hash", "plan_version", "plan_hash", "acceptance_input_hash", "acceptance_report_hash"):
        require(permit[field] == job[field])
    references = json_copy(references, limit=MAX_DESCRIPTOR_BYTES)
    require(type(references) is list)
    references = [ImageReference.model_validate(reference).model_dump() for reference in references]
    require([reference["shot_id"] for reference in references] == job["reference_shot_ids"])
    bindings = checked_reference_bindings(reference_bindings, references)
    require(permit["reference_bindings_hash"] == digest(bindings))
    _source_identity(permit, bindings)
    body = {"model": policy["model"], "prompt": job["prompt"], "size": policy["ratio"], "resolution": policy["resolution"], "quality": policy["quality"]}
    if references:
        body["images"] = [f"data:{reference['mime_type']};base64,{reference['bytes_base64']}" for reference in references]
    wire = json.dumps(body, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    require(len(wire.encode("utf-8")) <= policy["max_request_bytes"])
    version = {"schema_version": 4, "kind": RESPONSE_KIND, "scope": RESPONSE_SCOPE} if authority["schema_version"] in {3, 4} else {
        "schema_version": 2, "kind": KIND, "scope": SCOPE}
    return {**version, "method": "POST",
        "endpoint": policy["origin"] + "/v1/images/generations/async", "wire": wire,
        "policy": policy, "budget_anchor": anchor, "permit": permit, "asset_input": asset_input,
        "manifest": manifest, "job": job, "references": references, "reference_bindings": bindings,
        "continuation_authority": authority, "parent_submit": None, "submit_raw": None,
        "query_index": 0, "accepted_task_id": None}


def _query_data(*, submit, submit_raw, permit, query_index):
    from core.execution.image_receipts import decode_submit

    parent = submit.frozen() if isinstance(submit, FrozenImageRequest) else json_copy(submit, limit=MAX_DESCRIPTOR_BYTES)
    require(type(parent) is dict and parent.get("kind") in {KIND, RESPONSE_KIND}
            and parent.get("method") == "POST" and parent.get("parent_submit") is None)
    parent_request = FrozenImageRequest.from_frozen(parent)
    parent = parent_request.frozen()
    raw = json_copy(submit_raw, limit=2 * 1048576)
    receipt = decode_submit(parent_request, raw)
    require(receipt["classification"] == "accepted")
    accepted = receipt["accepted_task_id"]
    permit = _model(permit, ImageResponseContinuationPermit if parent["kind"] == RESPONSE_KIND else ImageContinuationPermit)
    require(type(query_index) is int and query_index >= 1 and permit["action"] == "query" and permit["query_index"] == query_index)
    _authority(parent["policy"], parent["budget_anchor"], permit, parent["continuation_authority"])
    _source_identity(permit, parent["reference_bindings"])
    changed = {"operation_id", "capture_id", "nonce", "action", "query_index", "paid_calls", "risk_cny", "issued_at", "deadline_at", "permit_hash"}
    if parent["kind"] == RESPONSE_KIND:
        changed.add("response_deadline_at")
    require(all(permit[field] == value for field, value in parent["permit"].items() if field not in changed))
    require(all(permit[field] != parent["permit"][field] for field in ("operation_id", "capture_id", "nonce")))
    require(timestamp(permit["issued_at"]) >= timestamp(receipt["received_at"]))
    return {**{key: value for key, value in parent.items() if key != "descriptor_hash"}, "method": "GET",
        "endpoint": parent["policy"]["origin"] + "/v1/tasks/" + accepted, "wire": "", "permit": permit,
        "parent_submit": parent, "submit_raw": raw, "query_index": query_index, "accepted_task_id": accepted}


def rebuild(value):
    """Reconstruct the native version; caller checks the complete descriptor seal."""
    require(type(value) is dict and value.get("kind") in {KIND, RESPONSE_KIND})
    if value.get("method") == "POST":
        return _submit_data(policy=value["policy"], budget_anchor=value["budget_anchor"], permit=value["permit"],
            continuation_authority=value["continuation_authority"], asset_input=value["asset_input"], manifest=value["manifest"],
            shot_id=value["job"]["shot_id"], references=value["references"], reference_bindings=value["reference_bindings"])
    require(value.get("method") == "GET")
    return _query_data(submit=value["parent_submit"], submit_raw=value["submit_raw"], permit=value["permit"], query_index=value["query_index"])


def build_image_submit_v2(*, policy, budget_anchor, permit, continuation_authority, asset_input,
                          manifest, shot_id, references=(), reference_bindings=()):
    try:
        references = [] if type(references) is tuple and references == () else references
        reference_bindings = [] if type(reference_bindings) is tuple and reference_bindings == () else reference_bindings
        value = _submit_data(policy=policy, budget_anchor=budget_anchor, permit=permit, continuation_authority=continuation_authority,
            asset_input=asset_input, manifest=manifest, shot_id=shot_id, references=references, reference_bindings=reference_bindings)
        return FrozenImageRequest.from_frozen(_seal(value))
    except Exception:
        raise ImagePolicyError() from None


def build_image_query_v2(*, submit, submit_raw, permit, query_index):
    try:
        return FrozenImageRequest.from_frozen(_seal(_query_data(submit=submit, submit_raw=submit_raw, permit=permit, query_index=query_index)))
    except Exception:
        raise ImagePolicyError() from None
