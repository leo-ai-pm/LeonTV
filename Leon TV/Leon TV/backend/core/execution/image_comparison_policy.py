"""Diagnostic-only descriptors over an exact historical wire; no dispatch grant."""

import copy
import re
import uuid
from datetime import timedelta
from typing import Literal

from pydantic import Field

from core.creator.store import digest
from core.execution.image_comparison import PURPOSE, require, sha
from core.execution.image_policy import (
    FrozenImageRequest,
    ImageToolPermit,
    ImageToolPolicy,
    _model,
    timestamp,
)
from core.execution.policy import now
from core.planner.review import json_copy

KIND = "controlled_image_comparison_http_v1"
SCOPE = "bounded_image_comparison_v1"


class ComparisonPermit(ImageToolPermit):
    schema_version: Literal[5] = 5
    scope: Literal["bounded_image_comparison_v1"] = SCOPE
    job_kind: Literal["controlled_image_comparison_http_v1"] = KIND
    scope_submission_index: int = Field(ge=1, le=16)
    comparison_activation_id: str
    comparison_authority_hash: str
    purpose: Literal["bounded_http403_diagnostic_only"] = PURPOSE
    response_deadline_at: str
    response_contract_hash: str


def checked_authority(value):
    value = json_copy(value)
    keys = {
        "schema_version",
        "kind",
        "activation_id",
        "quote_id",
        "intent_id",
        "intent_hash",
        "scope_id",
        "task_id",
        "account_id",
        "grant_id",
        "proposal_hash",
        "allocation_hash",
        "parent_after_sha256",
        "code_manifest_hash",
        "purpose",
        "policy",
        "response_policy",
        "source_descriptor_hash",
        "wire_sha256",
        "submission_index",
        "max_posts",
        "authority_hash",
    }
    require(
        type(value) is dict
        and set(value) == keys
        and type(value["schema_version"]) is int
        and value["schema_version"] == 1
        and value["kind"] == "image_comparison_authority_v1"
        and value["purpose"] == PURPOSE
        and type(value["max_posts"]) is int
        and value["max_posts"] == 1
        and type(value["submission_index"]) is int
        and value["submission_index"] == 6
    )
    require(
        value["authority_hash"] == digest({k: v for k, v in value.items() if k != "authority_hash"})
    )
    for k in ("activation_id", "quote_id", "intent_id", "scope_id", "task_id", "grant_id"):
        require(type(value[k]) is str and re.fullmatch("[a-f0-9]{32}", value[k]))
    for k in (
        "intent_hash",
        "proposal_hash",
        "allocation_hash",
        "parent_after_sha256",
        "code_manifest_hash",
        "source_descriptor_hash",
        "wire_sha256",
    ):
        require(type(value[k]) is str and re.fullmatch("[a-f0-9]{64}", value[k]))
    from core.execution.image_response_lifecycle import validate_response_policy

    validate_response_policy(value["response_policy"])
    _model(value["policy"], ImageToolPolicy)
    return value


def rebuild(value):
    authority = checked_authority(value["comparison_authority"])
    source = FrozenImageRequest.from_frozen(value["comparison_source"])
    original = source.frozen()
    require(
        original["kind"]
        in {"controlled_image_http_v1", "controlled_image_http_v2", "controlled_image_http_v3"}
        and source.method == "POST"
    )
    require(
        source.descriptor_hash == authority["source_descriptor_hash"]
        and sha(source.wire.encode()) == authority["wire_sha256"]
    )
    policy = authority["policy"]
    require(
        all(
            policy[k] == original["policy"][k]
            for k in (
                "origin",
                "model",
                "ratio",
                "resolution",
                "quality",
                "max_request_bytes",
                "max_response_bytes",
                "request_deadline_seconds",
                "validation_only",
                "per_submission_risk_cny",
            )
        )
    )
    permit = _model(value["permit"], ComparisonPermit)
    require(
        permit["comparison_activation_id"] == authority["activation_id"]
        and permit["comparison_authority_hash"] == authority["authority_hash"]
        and permit["parent_allocation_hash"] == authority["allocation_hash"]
        and permit["parent_allocation_begin_hash"] == authority["intent_hash"]
        and permit["parent_ledger_hash"] == authority["parent_after_sha256"]
        and permit["policy_hash"] == policy["policy_hash"]
        and permit["scope_submission_index"] == authority["submission_index"]
        and permit["task_id"] == authority["task_id"]
        and permit["grant_id"] == authority["grant_id"]
    )
    changed = {
        "schema_version",
        "operation_id",
        "capture_id",
        "nonce",
        "action",
        "scope",
        "job_kind",
        "policy_hash",
        "parent_ledger_hash",
        "parent_allocation_hash",
        "parent_allocation_begin_hash",
        "scope_submission_index",
        "query_index",
        "paid_calls",
        "risk_cny",
        "issued_at",
        "deadline_at",
        "permit_hash",
    }
    require(
        all(
            permit[k] == v
            for k, v in original["permit"].items()
            if k in ImageToolPermit.model_fields and k not in changed
        )
    )
    require(
        0
        < (timestamp(permit["deadline_at"]) - timestamp(permit["issued_at"])).total_seconds()
        <= 120
        and timestamp(permit["deadline_at"]) <= timestamp(policy["valid_until"])
    )
    require(
        permit["response_contract_hash"] == authority["response_policy"]["contract_hash"]
        and (
            timestamp(permit["response_deadline_at"]) - timestamp(permit["deadline_at"])
        ).total_seconds()
        == authority["response_policy"]["response_window_seconds"]
        and timestamp(permit["response_deadline_at"]) <= timestamp(policy["valid_until"])
    )
    result = {
        "schema_version": 1,
        "kind": KIND,
        "scope": SCOPE,
        "method": "POST",
        "endpoint": source.endpoint,
        "wire": source.wire,
        "policy": policy,
        "budget_anchor": original["budget_anchor"],
        "permit": permit,
        **{k: copy.deepcopy(original[k]) for k in ("asset_input", "manifest", "job", "references")},
        "comparison_authority": authority,
        "comparison_source": original,
        "parent_submit": None,
        "submit_raw": None,
        "query_index": 0,
        "accepted_task_id": None,
    }
    if permit["action"] == "query":
        from core.execution.image_receipts import decode_submit

        parent = FrozenImageRequest.from_frozen(value["parent_submit"])
        require(parent.frozen()["kind"] == KIND and parent.method == "POST")
        data = parent.frozen()
        receipt = decode_submit(parent, value["submit_raw"])
        require(receipt["classification"] == "accepted")
        task = receipt["accepted_task_id"]
        require(type(task) is str and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", task))
        mutable = {
            "operation_id",
            "capture_id",
            "nonce",
            "action",
            "query_index",
            "paid_calls",
            "risk_cny",
            "issued_at",
            "deadline_at",
            "response_deadline_at",
            "permit_hash",
        }
        require(
            all(permit[k] == v for k, v in data["permit"].items() if k not in mutable)
            and all(permit[k] != data["permit"][k] for k in ("operation_id", "capture_id", "nonce"))
            and timestamp(permit["issued_at"]) >= timestamp(receipt["received_at"])
        )
        require(
            type(value["query_index"]) is int
            and 1 <= value["query_index"] == permit["query_index"] <= policy["max_queries"]
        )
        result.update(
            method="GET",
            endpoint=policy["origin"] + "/v1/tasks/" + task,
            wire="",
            parent_submit=data,
            submit_raw=json_copy(value["submit_raw"]),
            query_index=permit["query_index"],
            accepted_task_id=task,
        )
    else:
        require(permit["action"] == "submit" and permit["risk_cny"] == "1.000000")
    require(set(value) == set(result) | {"descriptor_hash"})
    return result


def make_request(authority, source, *, identifier, parent=None, submit_raw=None, query_index=0):
    authority = checked_authority(authority)
    source = FrozenImageRequest.from_frozen(source).frozen()
    issued = now()
    deadline = issued + timedelta(seconds=120)
    response_deadline = deadline + timedelta(
        seconds=authority["response_policy"]["response_window_seconds"]
    )
    require(response_deadline <= timestamp(authority["policy"]["valid_until"]))
    values = {
        k: v
        for k, v in source["permit"].items()
        if k in ImageToolPermit.model_fields and k != "permit_hash"
    }
    values.update(
        schema_version=5,
        scope=SCOPE,
        job_kind=KIND,
        operation_id=identifier,
        capture_id=identifier,
        nonce=uuid.uuid4().hex,
        action="query" if parent is not None else "submit",
        policy_hash=authority["policy"]["policy_hash"],
        parent_ledger_hash=authority["parent_after_sha256"],
        parent_allocation_hash=authority["allocation_hash"],
        parent_allocation_begin_hash=authority["intent_hash"],
        scope_submission_index=authority["submission_index"],
        comparison_activation_id=authority["activation_id"],
        comparison_authority_hash=authority["authority_hash"],
        purpose=PURPOSE,
        query_index=query_index,
        paid_calls=0 if parent is not None else 1,
        risk_cny="0.000000" if parent is not None else "1.000000",
        issued_at=issued.isoformat(),
        deadline_at=deadline.isoformat(),
        response_deadline_at=response_deadline.isoformat(),
        response_contract_hash=authority["response_policy"]["contract_hash"],
    )
    permit = ComparisonPermit.model_validate(values).frozen()
    data = {
        "permit": permit,
        "comparison_authority": authority,
        "comparison_source": source,
        "parent_submit": parent,
        "submit_raw": submit_raw,
        "query_index": query_index,
    }
    # Build once, then pass through the same complete reconstruction used by worker.
    original = FrozenImageRequest.from_frozen(source)
    data.update(
        schema_version=1,
        kind=KIND,
        scope=SCOPE,
        method="POST",
        endpoint=original.endpoint,
        wire=original.wire,
        policy=authority["policy"],
        budget_anchor=source["budget_anchor"],
        accepted_task_id=None,
        **{k: copy.deepcopy(source[k]) for k in ("asset_input", "manifest", "job", "references")},
    )
    data["descriptor_hash"] = "0" * 64
    data = rebuild(data)
    return FrozenImageRequest.from_frozen({**data, "descriptor_hash": digest(data)})
