"""Pure permits for a newly disclosed plan over existing image funding only."""
import copy
import socket
from datetime import timedelta

import httpx
import pytest
from test_image_continuation_policy import seal_authority
from test_image_policy import accepted_raw
from test_image_retained_unknown import plan_case

from core.creator.store import digest
from core.execution import image_continuation_policy as continuation
from core.execution.image_continuation_policy import build_image_submit_v2, make_continuation_permit
from core.execution.image_policy import FrozenImageRequest, ImagePolicyError, build_image_query
from core.execution.image_receipts import decode_submit
from core.execution.image_response_lifecycle import make_response_policy
from core.execution.image_runtime_continuation import validate_authority
from core.execution.receipts import timestamp
from core.planner.product_assets import image_jobs


@pytest.fixture(autouse=True)
def no_external_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Retained-risk policy tests attempted external network")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", forbidden)


def retained_case(tmp_path):
    case = plan_case(tmp_path)
    authority = copy.deepcopy(case["base_authority"])
    authority.update(schema_version=4, kind="image_continuation_authority_v4",
        response_policy=make_response_policy(180), retained_unknown_plan=copy.deepcopy(case["plan"]),
        remaining_jobs=copy.deepcopy(case["plan"]["funded_targets"]))
    case["authority"] = seal_authority(authority)
    validate_authority(case["authority"])
    funded = case["plan"]["funded_targets"][0]
    case["job"] = next(job for job in image_jobs(case["scope"]["asset_input"], case["scope"]["manifest"])
        if job["shot_id"] == funded["shot_id"])
    return case


def issue(case, *, operation_id="f1" * 16, index=5, job=None, action="submit", query_index=0):
    return make_continuation_permit(case["scope"], case["grant"], job or case["job"], case["authority"],
        operation_id=operation_id, index=index, action=action, query_index=query_index,
        reference_bindings=case["reference_bindings"])


def arguments(case, permit):
    return {"policy": case["authority"]["effective_policy"], "budget_anchor": case["scope"]["anchor"],
        "permit": permit, "continuation_authority": case["authority"], "asset_input": case["scope"]["asset_input"],
        "manifest": case["scope"]["manifest"], "shot_id": case["job"]["shot_id"],
        "references": case["references"], "reference_bindings": case["reference_bindings"]}


def test_last_existing_slot_funds_exact_new_third_attempt_without_increasing_cap(tmp_path):
    case = retained_case(tmp_path)
    fields = ("scope", "grant", "base_authority", "authority", "plan", "payload", "references", "reference_bindings")
    before = copy.deepcopy({key: case[key] for key in fields})
    permit = issue(case)
    request = build_image_submit_v2(**arguments(case, permit))
    data = request.frozen()
    target = case["plan"]["funded_targets"][0]
    assert data["kind"] == "controlled_image_http_v3" and data["schema_version"] == 4
    assert permit["scope_submission_index"] == 5 and permit["attempt_version"] == 2
    assert permit["replacement_of"] == target["replacement_of"] in case["plan"]["new_unknown_ids"]
    assert data["policy"]["max_submissions"] == 5 and data["budget_anchor"]["scope_max_calls"] == 4
    assert data["continuation_authority"]["additional_paid_calls"] == 0
    assert data["continuation_authority"]["allocations"] == before["base_authority"]["allocations"]
    assert (timestamp(permit["response_deadline_at"]) - timestamp(permit["deadline_at"])).total_seconds() == 180
    assert FrozenImageRequest.from_frozen(data) == request
    assert {key: case[key] for key in fields} == before
    raw = accepted_raw(request)
    raw["received_at"] = (timestamp(permit["deadline_at"]) + timedelta(seconds=60)).isoformat()
    assert decode_submit(request, raw)["classification"] == "accepted"


def test_deferred_fourth_and_sixth_slot_cannot_get_a_permit(tmp_path):
    case = retained_case(tmp_path)
    fourth = next(job for job in image_jobs(case["scope"]["asset_input"], case["scope"]["manifest"])
        if job["shot_id"] == case["plan"]["deferred_targets"][0]["shot_id"])
    for job, index in ((fourth, 5), (fourth, 6), (case["job"], 6), (case["job"], True)):
        with pytest.raises(ImagePolicyError):
            issue(case, index=index, job=job)


def test_new_submit_cannot_reuse_either_retained_unknown_identity(tmp_path):
    case = retained_case(tmp_path)
    for old in case["plan"]["retained_unknowns"]:
        with pytest.raises(ImagePolicyError):
            issue(case, operation_id=old["operation_id"])


@pytest.mark.parametrize("attack", ["plan_hash", "substitution", "index", "attempt", "replacement", "contract"])
def test_descriptor_rejects_tampered_retained_plan_or_new_permit(tmp_path, attack):
    case = retained_case(tmp_path)
    permit = issue(case)
    if attack == "plan_hash":
        case["authority"]["retained_unknown_plan"]["plan_hash"] = "0" * 64
        seal_authority(case["authority"])
    elif attack == "substitution":
        case["authority"]["retained_unknown_plan"]["old_request_retry_authorized"] = True
        plan = case["authority"]["retained_unknown_plan"]
        plan["plan_hash"] = digest({k: v for k, v in plan.items() if k != "plan_hash"})
        seal_authority(case["authority"])
    elif attack == "index":
        permit["scope_submission_index"] = 6
    elif attack == "attempt":
        permit["attempt_version"] = 1
        permit["replacement_of"] = None
    elif attack == "replacement":
        permit["replacement_of"] = case["authority"]["unknown_disposition"]["operation_id"]
    else:
        permit["response_contract_hash"] = "0" * 64
    with pytest.raises(ImagePolicyError):
        build_image_submit_v2(**arguments(case, permit))


def test_accepted_new_attempt_keeps_independent_v3_query_contract(tmp_path, monkeypatch):
    case = retained_case(tmp_path)
    submit = build_image_submit_v2(**arguments(case, issue(case)))
    raw = accepted_raw(submit)
    monkeypatch.setattr(continuation, "now", lambda: timestamp(raw["received_at"]))
    permit = issue(case, operation_id="f2" * 16, action="query", query_index=1)
    query = build_image_query(submit=submit, submit_raw=raw, permit=permit, query_index=1)
    data = query.frozen()
    assert data["kind"] == "controlled_image_http_v3" and data["method"] == "GET"
    assert data["permit"]["paid_calls"] == 0 and data["permit"]["scope_submission_index"] == 5
    assert data["continuation_authority"]["retained_unknown_plan"] == case["plan"]
    assert data["parent_submit"] == submit.frozen() and data["submit_raw"] == raw
    assert data["permit"]["nonce"] != submit.frozen()["permit"]["nonce"]
