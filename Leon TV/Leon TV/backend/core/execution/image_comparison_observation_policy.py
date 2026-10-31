"""Compact GET-only descriptor: retain the exact old diagnostic parent once."""
import json
import uuid
from datetime import timedelta
from typing import Literal

from core.creator.store import digest
from core.execution import image_comparison_observation as contract
from core.execution.image_observation_policy import ImageObservationPermit
from core.execution.image_policy import (
    FrozenImageRequest,
    ImageToolPermit,
    _model,
    _seal,
    timestamp,
)
from core.execution.policy import now
from core.execution.receipts import MAX_IPC

KIND = "controlled_image_comparison_observation_http_v1"
SCOPE = "accepted_comparison_observation_v1"


class ComparisonObservationPermit(ImageObservationPermit):
    schema_version: Literal[6] = 6
    scope: Literal["accepted_comparison_observation_v1"] = SCOPE
    job_kind: Literal["controlled_image_comparison_observation_http_v1"] = KIND
    response_deadline_at: str
    response_contract_hash: str


def _data(parent, raw, authority, permit, query_index):
    a = contract.validate_authority(authority)
    submit = FrozenImageRequest.from_frozen(parent)
    parent = submit.frozen()
    from core.execution.image_receipts import decode_submit

    receipt = decode_submit(submit, raw)
    contract.require(parent["kind"] == "controlled_image_comparison_http_v1" and parent["method"] == "POST"
                     and receipt["classification"] == "accepted")
    contract.require(a["source_operation_id"] == parent["permit"]["operation_id"]
                     and a["source_descriptor_hash"] == parent["descriptor_hash"]
                     and a["source_raw_hash"] == digest(raw) and a["source_receipt_hash"] == receipt["receipt_hash"]
                     and a["accepted_task_id"] == receipt["accepted_task_id"]
                     and digest(a["parent_authority"]) == digest(parent["comparison_authority"]))
    p = _model(permit, ComparisonObservationPermit)
    contract.require(p["observation_activation_id"] == a["activation_id"] and p["observation_authority_hash"] == a["authority_hash"]
                     and p["observation_revision_sequence"] == 1 and p["accepted_task_id_hash"] == digest(a["accepted_task_id"]))
    contract.require(all(p[k] == a[k] for k in ("source_operation_id", "source_descriptor_hash", "source_raw_hash", "source_receipt_hash")))
    changed = {"schema_version", "scope", "job_kind", "action", "policy_hash", "operation_id", "capture_id", "nonce",
               "query_index", "paid_calls", "risk_cny", "issued_at", "deadline_at"}
    contract.require(all(p[k] == parent["permit"][k] for k in set(ImageToolPermit.model_fields) - changed))
    contract.require(all(p[k] != parent["permit"][k] for k in ("operation_id", "capture_id", "nonce")))
    contract.require(type(query_index) is int and 1 <= query_index == p["query_index"] <= a["effective_policy"]["max_queries"]
                     and p["policy_hash"] == a["effective_policy"]["policy_hash"])
    issued, deadline, response = timestamp(p["issued_at"]), timestamp(p["deadline_at"]), timestamp(p["response_deadline_at"])
    contract.require(issued >= timestamp(receipt["received_at"]) and (deadline - issued).total_seconds() == 120
                     and (response - deadline).total_seconds() == a["response_policy"]["response_window_seconds"]
                     and p["response_contract_hash"] == a["response_policy"]["contract_hash"]
                     and response + timedelta(seconds=5) <= timestamp(a["effective_policy"]["valid_until"]))
    return {"schema_version": 1, "kind": KIND, "scope": SCOPE, "method": "GET", "wire": "",
            "endpoint": parent["policy"]["origin"] + "/v1/tasks/" + receipt["accepted_task_id"],
            "policy": a["effective_policy"], "budget_anchor": parent["budget_anchor"], "permit": p,
            "parent_submit": parent, "submit_raw": raw, "observation_authority": a,
            "query_index": query_index, "accepted_task_id": receipt["accepted_task_id"]}


def rebuild(value):
    contract.require(type(value) is dict and value.get("kind") == KIND and value.get("method") == "GET")
    return _data(value["parent_submit"], value["submit_raw"], value["observation_authority"], value["permit"], value["query_index"])


def make_request(authority, operation, *, identifier, query_index, settings):
    a = contract.validate_authority(authority)
    parent = operation["descriptor"]
    fields = set(ImageToolPermit.model_fields) - {"schema_version", "scope", "job_kind", "action", "policy_hash",
             "operation_id", "capture_id", "nonce", "query_index", "paid_calls", "risk_cny", "issued_at", "deadline_at"}
    values = {k: parent["permit"][k] for k in fields}
    issued = now()
    deadline = issued + timedelta(seconds=120)
    response = deadline + timedelta(seconds=a["response_policy"]["response_window_seconds"])
    values.update(operation_id=identifier, capture_id=identifier, nonce=uuid.uuid4().hex,
                  policy_hash=a["effective_policy"]["policy_hash"], query_index=query_index,
                  issued_at=issued.isoformat(), deadline_at=deadline.isoformat(),
                  response_deadline_at=response.isoformat(), response_contract_hash=a["response_policy"]["contract_hash"],
                  observation_authority_hash=a["authority_hash"], observation_activation_id=a["activation_id"],
                  observation_revision_sequence=1, accepted_task_id_hash=digest(a["accepted_task_id"]),
                  **{k: a[k] for k in ("source_operation_id", "source_descriptor_hash", "source_raw_hash", "source_receipt_hash")})
    permit = ComparisonObservationPermit.model_validate(values).frozen()
    request = FrozenImageRequest.from_frozen(_seal(_data(parent, operation["raw"], a, permit, query_index)))
    frozen = request.frozen()
    job = {"kind": KIND, "descriptor": frozen, "binding": request.binding(),
           "worker_factory": settings.worker_factory, "worker_options": settings.worker_options}
    ipc = {"capability": "x" * 64, "api_key": "\x01" * 16384, "job": job}
    contract.require(len(json.dumps(ipc, ensure_ascii=False).encode()) <= MAX_IPC)
    return request
