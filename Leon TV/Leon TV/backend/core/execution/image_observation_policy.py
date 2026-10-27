"""GET-only observation descriptors over one immutable accepted image POST.

Consistency is not dispatch authority. The native journal and current explicit
observation approval must independently admit every new query.
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
    ImageToolPermit,
    ImageToolPolicy,
    _model,
    _seal,
    require,
    timestamp,
)
from core.execution.policy import now
from core.planner.review import json_copy

KIND = "controlled_image_observation_http_v1"
SCOPE = "accepted_image_observation_v1"


class ImageObservationPermit(ImageToolPermit):
    schema_version: Literal[3] = 3
    scope: Literal["accepted_image_observation_v1"] = SCOPE
    job_kind: Literal["controlled_image_observation_http_v1"] = KIND
    action: Literal["query"] = "query"
    paid_calls: Literal[0] = 0
    risk_cny: Literal["0.000000"] = "0.000000"
    observation_authority_hash: Hash
    observation_activation_id: Identifier
    observation_revision_sequence: int = Field(ge=1, le=10000)
    source_operation_id: Identifier
    source_descriptor_hash: Hash
    source_raw_hash: Hash
    source_receipt_hash: Hash
    accepted_task_id_hash: Hash

    @model_validator(mode="before")
    @classmethod
    def strict_counts(cls, value):
        if type(value) is dict:
            for field in ("schema_version", "paid_calls", "query_index", "observation_revision_sequence"):
                require(field not in value or type(value[field]) is int)
        return value


def _authority(value):
    from core.execution.image_observation_continuation import validate_authority

    return validate_authority(value)


def _parent(submit, raw, authority):
    from core.execution.image_receipts import decode_submit

    parent = submit.frozen() if type(submit) is FrozenImageRequest else json_copy(submit, limit=MAX_DESCRIPTOR_BYTES)
    require(type(parent) is dict and parent.get("kind") in {"controlled_image_http_v2", "controlled_image_http_v3"}
            and parent.get("method") == "POST" and parent.get("parent_submit") is None)
    checked = FrozenImageRequest.from_frozen(parent)
    parent = checked.frozen()
    raw = json_copy(raw, limit=2 * 1048576)
    receipt = decode_submit(checked, raw)
    require(receipt["classification"] == "accepted")
    require(authority["source_operation_id"] == parent["permit"]["operation_id"]
            and authority["source_descriptor_hash"] == parent["descriptor_hash"]
            and authority["source_raw_hash"] == digest(raw)
            and authority["source_receipt_hash"] == receipt["receipt_hash"]
            and authority["accepted_task_id"] == receipt["accepted_task_id"])
    require(authority["task_id"] == parent["permit"]["task_id"]
            and authority["grant_id"] == parent["permit"]["grant_id"])
    require(digest(authority["parent_authority"]) == digest(parent["continuation_authority"])
            and digest(authority["original_budget_anchor"]) == digest(parent["budget_anchor"]))
    policy = {k: v for k, v in parent["policy"].items() if k != "policy_hash"}
    policy["valid_until"] = authority["effective_policy"]["valid_until"]
    require(digest(ImageToolPolicy.model_validate(policy).frozen()) == digest(authority["effective_policy"]))
    return parent, raw, receipt


def make_observation_permit(scope, grant, operation, authority, *, operation_id, query_index):
    """A fresh query identity; no paid slot or historical time is renewed."""
    try:
        authority = _authority(authority)
        require(digest(scope) == authority["scope_hash"] and scope["id"] == authority["scope_id"])
        require(grant["id"] == authority["grant_id"] and digest(grant) == authority["grant_hash"])
        require(operation["id"] == authority["source_operation_id"] and operation["status"] == "accepted")
        parent, _, receipt = _parent(operation["descriptor"], operation["raw"], authority)
        require(digest(operation["receipt"]) == digest(receipt))
        require(type(query_index) is int and 1 <= query_index <= authority["effective_policy"]["max_queries"])
        issued = now()
        require(issued >= timestamp(receipt["received_at"]))
        deadline = min(issued + timedelta(seconds=120), timestamp(authority["effective_policy"]["valid_until"]))
        fields = set(ImageToolPermit.model_fields) - {"schema_version", "scope", "job_kind", "action", "policy_hash",
            "operation_id", "capture_id", "nonce", "query_index", "paid_calls", "risk_cny", "issued_at", "deadline_at"}
        value = {k: parent["permit"][k] for k in fields}
        value.update(operation_id=operation_id, capture_id=operation_id, nonce=uuid.uuid4().hex,
            policy_hash=authority["effective_policy"]["policy_hash"], query_index=query_index,
            issued_at=issued.isoformat(), deadline_at=deadline.isoformat(),
            observation_authority_hash=authority["authority_hash"], observation_activation_id=authority["activation_id"],
            observation_revision_sequence=authority["revision_sequence"], source_operation_id=authority["source_operation_id"],
            source_descriptor_hash=authority["source_descriptor_hash"], source_raw_hash=authority["source_raw_hash"],
            source_receipt_hash=authority["source_receipt_hash"], accepted_task_id_hash=digest(authority["accepted_task_id"]))
        return ImageObservationPermit.model_validate(value).frozen()
    except Exception:
        raise ImagePolicyError() from None


def _data(*, submit, submit_raw, permit, observation_authority, query_index):
    authority = _authority(observation_authority)
    parent, raw, receipt = _parent(submit, submit_raw, authority)
    permit = _model(permit, ImageObservationPermit)
    require(type(query_index) is int and query_index == permit["query_index"]
            and 1 <= query_index <= authority["effective_policy"]["max_queries"])
    require(permit["observation_activation_id"] == authority["activation_id"]
            and permit["observation_revision_sequence"] == authority["revision_sequence"]
            and permit["observation_authority_hash"] == authority["authority_hash"])
    for key in ("source_operation_id", "source_descriptor_hash", "source_raw_hash", "source_receipt_hash"):
        require(permit[key] == authority[key])
    require(permit["accepted_task_id_hash"] == digest(authority["accepted_task_id"]))
    require(permit["policy_hash"] == authority["effective_policy"]["policy_hash"])
    changed = {"schema_version", "scope", "job_kind", "action", "policy_hash", "operation_id", "capture_id", "nonce",
        "query_index", "paid_calls", "risk_cny", "issued_at", "deadline_at"}
    for key in set(ImageToolPermit.model_fields) - changed:
        require(permit[key] == parent["permit"][key])
    require(all(permit[key] != parent["permit"][key] for key in ("operation_id", "capture_id", "nonce")))
    issued, deadline = timestamp(permit["issued_at"]), timestamp(permit["deadline_at"])
    require(issued >= timestamp(receipt["received_at"]) and 0 < (deadline - issued).total_seconds() <= 120)
    require(deadline <= timestamp(authority["effective_policy"]["valid_until"]))
    return {"schema_version": 1, "kind": KIND, "scope": SCOPE, "method": "GET",
        "endpoint": parent["policy"]["origin"] + "/v1/tasks/" + receipt["accepted_task_id"], "wire": "",
        "policy": authority["effective_policy"], "budget_anchor": parent["budget_anchor"], "permit": permit,
        "asset_input": parent["asset_input"], "manifest": parent["manifest"], "job": parent["job"],
        "observation_authority": authority, "parent_submit": parent, "submit_raw": raw,
        "query_index": query_index, "accepted_task_id": receipt["accepted_task_id"]}


def rebuild(value):
    require(type(value) is dict and value.get("kind") == KIND and value.get("method") == "GET")
    return _data(submit=value["parent_submit"], submit_raw=value["submit_raw"], permit=value["permit"],
        observation_authority=value["observation_authority"], query_index=value["query_index"])


def build_observation_query(*, submit, submit_raw, permit, observation_authority, query_index):
    try:
        return FrozenImageRequest.from_frozen(_seal(_data(submit=submit, submit_raw=submit_raw, permit=permit,
            observation_authority=observation_authority, query_index=query_index)))
    except Exception:
        raise ImagePolicyError() from None


def check_observation_capacity(request, settings):
    """Reject an oversized complete collector IPC before saving a GET intent."""
    from core.execution.receipts import MAX_IPC

    frozen = request.frozen()
    require(frozen["kind"] == KIND and frozen["method"] == "GET")
    job = {"kind": KIND, "descriptor": frozen, "binding": request.binding(),
        "worker_factory": settings.worker_factory, "worker_options": json_copy(settings.worker_options)}
    # Keys are bounded by UTF-8 bytes. JSON can escape a one-byte control
    # character as six ASCII bytes, so budget the worst serialized key length.
    ipc = {"capability": "x" * 64, "api_key": "\x01" * 16384, "job": job}
    require(len(json.dumps(ipc, ensure_ascii=False).encode("utf-8")) <= MAX_IPC)
