"""Fictional v2 descriptors and the original detached collector; no live state."""
import base64
import copy
import hashlib
import json
import socket
from datetime import datetime, timedelta

import httpx
import pytest
from test_image_capture import adapter_for, requests
from test_image_policy import SYNTHETIC_PNG, accepted_raw, reseal
from test_product_assets import asset_case

from core.creator.store import CreatorError, digest
from core.execution.image_capture import ControlledImageCapture, worker_request
from core.execution.image_continuation_policy import (
    KIND,
    SCOPE,
    bind_reference_sources,
    build_image_submit_v2,
    make_continuation_permit,
)
from core.execution.image_policy import (
    FrozenImageRequest,
    ImagePolicyError,
    ImageToolPolicy,
    MediaBudgetAnchor,
    build_image_query,
)
from core.execution.image_receipts import decode_query, decode_submit
from core.execution.image_runtime_continuation import validate_authority
from core.execution.policy import now
from core.execution.receipts import ObservationEnded
from core.planner import product_assets


@pytest.fixture(autouse=True)
def no_real_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Continuation descriptor test attempted real network")
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", forbidden)


def seal_authority(value):
    value["authority_hash"] = digest({key: item for key, item in value.items() if key != "authority_hash"})
    return value


def continuation_case(shot_index=2):
    asset_input, candidate = asset_case(4)
    parent, report = asset_input["acceptance_payload"], asset_input["acceptance_report"]
    parent["binding"]["task_id"] = "1" * 32
    report["binding"] = copy.deepcopy(parent["binding"])
    asset_input = product_assets.build_asset_input("first_review", parent, report,
        {"asset_task_id": "SYNTHETIC-CONTINUATION-ASSETS", "manifest_version": 1})
    candidate["binding"] = copy.deepcopy(asset_input["binding"])
    manifest = product_assets.validate_asset_output(json.dumps(candidate, ensure_ascii=False), asset_input)
    jobs = product_assets.image_jobs(asset_input, manifest)
    task_id = asset_input["acceptance_payload"]["binding"]["task_id"]
    original = ImageToolPolicy(id="fictional-continuation", version="1", origin="https://synthetic.invalid",
        max_submissions=4, max_queries=3, max_request_bytes=1048576, max_response_bytes=1048576,
        request_deadline_seconds=120, valid_until=(now() - timedelta(hours=1)).isoformat(),
        per_submission_risk_cny="1.000000", operator_risk_approved=True, validation_only=True).frozen()
    anchor = MediaBudgetAnchor(account_scope_id="fictional-continuation-account", parent_authorization_hash="a" * 64,
        parent_ledger_hash="b" * 64, parent_allocation_hash="c" * 64, parent_allocation_begin_hash="d" * 64,
        original_budget_cny="100", opening_risk_cny="77.37", opening_paid_call_count=49,
        scope_risk_cny="4.000000", scope_max_calls=4, account_limit_cny="81.37", account_max_calls=53).frozen()
    effective = {key: value for key, value in original.items() if key != "policy_hash"}
    effective.update(max_submissions=5, valid_until=(now() + timedelta(minutes=60)).isoformat())
    effective = ImageToolPolicy.model_validate(effective).frozen()
    scope = {"id": "e" * 32, "task_id": task_id, "policy": original, "anchor": anchor,
        "asset_input": asset_input, "manifest": manifest, "handoff": {"handoff_hash": "f" * 64, "provenance_hash": "0" * 64}}
    grant = {"id": "4" * 32, "disclosure_hash": "e" * 64}
    unknown = "7" * 32
    targets = [{"shot_id": job["shot_id"], "job_hash": digest(job), "first_frame_version": 1,
        "attempt_version": 2 if index == 2 else 1, "replacement_of": unknown if index == 2 else None,
        "new_operation_id": None, "new_nonce": None, "planned_submission_index": index + 1,
        "reference_shot_ids": copy.deepcopy(job["reference_shot_ids"]), "references_require_exact_delivered_operation": True}
        for index, job in enumerate(jobs[1:], 2)]
    authority = seal_authority({"schema_version": 2, "kind": "image_continuation_authority_v2",
        "activation_id": "b" * 32, "quote_id": "c" * 32, "revision_sequence": 1, "scope_id": scope["id"],
        "task_id": task_id, "grant_id": grant["id"], "control_hash": "1" * 64,
        "scope_hash": digest(scope), "grant_hash": digest(grant), "intent_id": "d" * 32, "intent_hash": "2" * 64,
        "validity_activation_id": "f" * 32, "validity_activation_hash": "3" * 64, "parent_receipt_hash": "4" * 64,
        "original_policy": original, "original_budget_anchor": anchor, "effective_policy": effective, "code_manifest_hash": "5" * 64,
        "unknown_disposition": {"operation_id": unknown, "operation_hash": "6" * 64, "status_after_proposal": "unknown",
            "original_record_must_remain_unchanged": True, "old_request_retry_authorized": False,
            "zero_http_proven": False, "zero_fee_proven": False, "new_unknown_stops_continuation": True},
        "remaining_jobs": targets, "allocations": [
            {"role": "original", "allocation_hash": anchor["parent_allocation_hash"], "begin_hash": anchor["parent_allocation_begin_hash"],
                "risk_cny": anchor["scope_risk_cny"], "max_calls": 4},
            {"role": "continuation", "allocation_hash": "8" * 64, "begin_hash": "9" * 64,
                "risk_cny": "1.000000", "max_calls": 1}],
        "max_cumulative_submissions": 5, "additional_risk_cny": "0.000000", "additional_paid_calls": 0})
    validate_authority(authority)
    job = jobs[shot_index - 1]
    refs = [{"shot_id": job["reference_shot_ids"][0], "first_frame_version": 1, "mime_type": "image/png",
        "bytes_base64": base64.b64encode(SYNTHETIC_PNG).decode(), "sha256": hashlib.sha256(SYNTHETIC_PNG).hexdigest()}]
    binding = {"source_operation_id": "8" * 32, "source_query_id": "9" * 32,
        "source_delivery_id": "a" * 32, "source_delivery_hash": "a" * 64, "source_sha256": refs[0]["sha256"],
        "shot_id": refs[0]["shot_id"], "first_frame_version": 1}
    permit = make_continuation_permit(scope, grant, job, authority, operation_id="2" * 32, index=shot_index + 1,
        reference_bindings=[binding])
    return {"scope": scope, "grant": grant, "job": job, "authority": authority, "references": refs,
        "reference_bindings": [binding], "permit": permit}


def arguments(case):
    return {"policy": case["authority"]["effective_policy"], "budget_anchor": case["authority"]["original_budget_anchor"],
        "permit": case["permit"], "continuation_authority": case["authority"], "asset_input": case["scope"]["asset_input"],
        "manifest": case["scope"]["manifest"], "shot_id": case["job"]["shot_id"],
        "references": case["references"], "reference_bindings": case["reference_bindings"]}


def descriptor(shot_index=2):
    return build_image_submit_v2(**arguments(continuation_case(shot_index)))


def query_for(submit, raw=None):
    raw = raw or accepted_raw(submit)
    permit = copy.deepcopy(submit.frozen()["permit"])
    permit.pop("permit_hash")
    issued = max(now(), datetime.fromisoformat(raw["received_at"]))
    permit.update(operation_id="5" * 32, capture_id="5" * 32, nonce="6" * 32, action="query", query_index=1,
        paid_calls=0, risk_cny="0.000000", issued_at=issued.isoformat(), deadline_at=(issued + timedelta(seconds=120)).isoformat())
    return build_image_query(submit=submit, submit_raw=raw, permit=permit, query_index=1)


@pytest.mark.parametrize("shot_index", [2, 3, 4])
def test_cumulative_targets_use_two_real_allocations_without_rewriting_original_anchor(shot_index):
    request = descriptor(shot_index)
    value, binding = request.frozen(), request.binding()
    assert value["kind"] == binding["kind"] == KIND and value["scope"] == binding["scope"] == SCOPE
    assert value["budget_anchor"]["scope_max_calls"] == 4 and value["policy"]["max_submissions"] == 5
    assert value["budget_anchor"]["parent_allocation_hash"] == "c" * 64
    assert len(value["continuation_authority"]["allocations"]) == 2
    assert value["permit"]["scope_submission_index"] == shot_index + 1
    assert value["permit"]["attempt_version"] == (2 if shot_index == 2 else 1)
    assert value["permit"]["replacement_of"] == ("7" * 32 if shot_index == 2 else None)
    assert binding["runtime_activation_id"] == "b" * 32 and binding["runtime_revision_sequence"] == 1
    assert binding["reference_bindings_hash"] == digest(value["reference_bindings"])
    assert value["continuation_authority"]["additional_paid_calls"] == 0
    assert value["continuation_authority"]["unknown_disposition"]["zero_http_proven"] is False
    assert json.loads(request.wire)["images"] == ["data:image/png;base64," + value["references"][0]["bytes_base64"]]
    assert FrozenImageRequest.from_frozen(value) == request


@pytest.mark.parametrize("field,value", [("attempt_version", 1), ("replacement_of", None), ("scope_submission_index", 6),
    ("runtime_revision_sequence", 2), ("runtime_activation_id", "a" * 32), ("continuation_authority_hash", "a" * 64),
    ("reference_bindings_hash", "b" * 64), ("parent_allocation_hash", "f" * 64), ("grant_id", "d" * 32),
    ("schema_version", True), ("attempt_version", True), ("operation_id", "7" * 32)])
def test_fully_resealed_permit_cannot_change_target_revision_or_funding(field, value):
    request = descriptor().frozen()
    request["permit"][field] = value
    if field == "operation_id":
        request["permit"]["capture_id"] = value
    request["permit"]["permit_hash"] = digest({key: item for key, item in request["permit"].items() if key != "permit_hash"})
    with pytest.raises(ImagePolicyError):
        FrozenImageRequest.from_frozen(reseal(request))


@pytest.mark.parametrize("change", ["wire", "policy", "source_id", "source_extra", "source_version", "source_tuple", "self_source", "downgrade"])
def test_fully_resealed_descriptor_cannot_swap_wire_provenance_or_profile(change):
    value = descriptor().frozen()
    if change == "wire":
        value["wire"] += " "
    elif change == "policy":
        value["policy"]["max_submissions"] = 6
        value["policy"]["policy_hash"] = digest({k: v for k, v in value["policy"].items() if k != "policy_hash"})
    elif change == "source_id":
        value["reference_bindings"][0]["source_operation_id"] = "b" * 32
    elif change == "source_extra":
        value["reference_bindings"][0]["signed_url"] = "https://fictional.invalid/forbidden"
    elif change == "source_version":
        value["reference_bindings"][0]["first_frame_version"] = True
    elif change == "source_tuple":
        value["reference_bindings"] = tuple(value["reference_bindings"])
    elif change == "self_source":
        value["reference_bindings"][0]["source_operation_id"] = value["permit"]["operation_id"]
        value["permit"]["reference_bindings_hash"] = digest(value["reference_bindings"])
        value["permit"]["permit_hash"] = digest({k: v for k, v in value["permit"].items() if k != "permit_hash"})
    else:
        value["kind"] = "controlled_image_http_v1"
        value["scope"] = "accepted_plan_image_tools_v1"
    with pytest.raises(ImagePolicyError):
        FrozenImageRequest.from_frozen(reseal(value))


def test_query_inherits_exact_attempt_provenance_and_has_independent_nonce_and_deadline():
    submit = descriptor()
    query = query_for(submit)
    parent, value = submit.frozen(), query.frozen()
    assert value["parent_submit"] == parent and value["continuation_authority"] == parent["continuation_authority"]
    assert value["reference_bindings"] == parent["reference_bindings"]
    for field in ("attempt_version", "replacement_of", "reference_bindings_hash", "runtime_activation_id", "runtime_revision_sequence"):
        assert value["permit"][field] == parent["permit"][field]
    for field in ("operation_id", "capture_id", "nonce"):
        assert value["permit"][field] != parent["permit"][field]
    assert value["permit"]["paid_calls"] == 0 and value["permit"]["risk_cny"] == "0.000000"
    assert query.endpoint.endswith("/v1/tasks/synthetic-image-task-1") and query.wire == ""
    assert query.binding()["parent_descriptor_hash"] == parent["descriptor_hash"]


def test_query_rejects_a_different_resealed_attempt_and_reference_sources():
    submit = descriptor()
    value = query_for(submit).frozen()
    value["permit"]["reference_bindings_hash"] = "a" * 64
    value["permit"]["permit_hash"] = digest({k: v for k, v in value["permit"].items() if k != "permit_hash"})
    with pytest.raises(ImagePolicyError):
        FrozenImageRequest.from_frozen(reseal(value))


@pytest.mark.parametrize("change", ["long_deadline", "lease_deadline", "query_nonce", "query_limit"])
def test_original_request_deadline_and_query_identity_limits_still_apply(change):
    case = continuation_case()
    if change in {"long_deadline", "lease_deadline"}:
        if change == "long_deadline":
            case["permit"]["deadline_at"] = (datetime.fromisoformat(case["permit"]["issued_at"]) + timedelta(seconds=121)).isoformat()
        else:
            effective = {key: value for key, value in case["authority"]["effective_policy"].items() if key != "policy_hash"}
            effective["valid_until"] = (datetime.fromisoformat(case["permit"]["issued_at"]) + timedelta(seconds=30)).isoformat()
            case["authority"]["effective_policy"] = ImageToolPolicy.model_validate(effective).frozen()
            seal_authority(case["authority"])
            case["permit"].update(continuation_authority_hash=case["authority"]["authority_hash"],
                policy_hash=case["authority"]["effective_policy"]["policy_hash"])
        with pytest.raises(ImagePolicyError):
            build_image_submit_v2(**arguments(case))
    else:
        submit = build_image_submit_v2(**arguments(case))
        query = query_for(submit).frozen()
        permit = {key: value for key, value in query["permit"].items() if key != "permit_hash"}
        index = 1
        if change == "query_nonce":
            permit["nonce"] = submit.frozen()["permit"]["nonce"]
        else:
            index = permit["query_index"] = 4
        with pytest.raises(ImagePolicyError):
            build_image_query(submit=submit, submit_raw=query["submit_raw"], permit=permit, query_index=index)


def test_expired_v2_history_reconstructs_but_cannot_capture_a_new_request(tmp_path, monkeypatch):
    import core.execution.image_continuation_policy as continuation

    case = continuation_case()
    issued = datetime.fromisoformat("2020-01-01T01:00:00+00:00")
    original = {key: value for key, value in case["authority"]["original_policy"].items() if key != "policy_hash"}
    original["valid_until"] = "2020-01-01T00:00:00+00:00"
    original = ImageToolPolicy.model_validate(original).frozen()
    effective = {key: value for key, value in original.items() if key != "policy_hash"}
    effective.update(max_submissions=5, valid_until="2020-01-01T02:00:00+00:00")
    case["scope"]["policy"] = original
    case["authority"].update(original_policy=original, effective_policy=ImageToolPolicy.model_validate(effective).frozen(),
        scope_hash=digest(case["scope"]))
    seal_authority(case["authority"])
    with monkeypatch.context() as patch:
        patch.setattr(continuation, "now", lambda: issued)
        case["permit"] = make_continuation_permit(case["scope"], case["grant"], case["job"], case["authority"],
            operation_id="2" * 32, index=3, reference_bindings=case["reference_bindings"])
    request = build_image_submit_v2(**arguments(case))
    assert FrozenImageRequest.from_frozen(request.frozen()) == request
    adapter = adapter_for(tmp_path)
    with pytest.raises(CreatorError):
        adapter.capture(request, "SYNTHETIC-CONTINUATION-KEY")
    assert not requests(tmp_path)


def test_native_reference_binding_uses_verified_original_delivery_not_paths_or_urls():
    case = continuation_case()
    delivery = {"delivery_sha256": hashlib.sha256(SYNTHETIC_PNG).hexdigest(), "delivery_size_bytes": len(SYNTHETIC_PNG),
        "width": 9, "height": 16, "format": "PNG"}
    delivery["delivery_hash"] = digest(delivery)
    source = {"operation_id": "8" * 32, "query_id": "9" * 32, "delivery_id": "a" * 32,
        "shot_id": "S01", "first_frame_version": 1, "delivery": delivery, "bytes_base64": base64.b64encode(SYNTHETIC_PNG).decode()}
    bindings = bind_reference_sources([source], case["references"])
    assert bindings[0]["source_sha256"] == hashlib.sha256(SYNTHETIC_PNG).hexdigest()
    assert bindings[0]["source_delivery_hash"] == delivery["delivery_hash"]
    assert not {"bytes_base64", "path", "url"} & set(bindings[0])
    source["delivery"]["delivery_sha256"] = "f" * 64
    source["delivery"]["delivery_hash"] = digest({k: v for k, v in source["delivery"].items() if k != "delivery_hash"})
    with pytest.raises(ImagePolicyError):
        bind_reference_sources([source], case["references"])


def test_self_declared_fictional_authority_cannot_enable_capture(tmp_path, monkeypatch):
    import core.execution.image_capture as capture

    def forbidden(*args, **kwargs):
        pytest.fail("No independent native verifier may reach collector prepare")
    monkeypatch.setattr(capture, "capture_once", forbidden)
    adapter = ControlledImageCapture(tmp_path / "receipts")
    submit = descriptor()
    with pytest.raises(CreatorError):
        adapter.capture(submit, "SYNTHETIC-CONTINUATION-KEY")
    assert not (tmp_path / "receipts").exists()


def test_v2_post_and_same_task_query_use_original_detached_worker_exactly_once(tmp_path):
    submit = descriptor()
    adapter = adapter_for(tmp_path)
    raw = adapter.capture(submit, "SYNTHETIC-CONTINUATION-KEY")
    assert decode_submit(submit, raw)["classification"] == "accepted"
    query = query_for(submit, raw)
    queried = adapter.capture(query, "SYNTHETIC-CONTINUATION-KEY")
    assert decode_query(query, queried)["classification"] == "completed"
    assert len(requests(tmp_path)) == 2
    with pytest.raises(ObservationEnded):
        adapter.capture(submit, "SYNTHETIC-CONTINUATION-KEY")
    assert len(requests(tmp_path)) == 2
    assert adapter.ready_raw(query)[0] == queried


def test_worker_rejects_a_v1_job_wrapper_around_a_valid_v2_descriptor():
    submit = descriptor()
    binding = submit.binding()
    job = {"kind": "controlled_image_http_v1", "descriptor": submit.frozen(), "binding": binding,
        "worker_factory": "image_tool_fixture:make_transport", "worker_options": {}, "api_key": "SYNTHETIC-KEY"}
    claim = {"binding": binding, "created_at": submit.frozen()["permit"]["issued_at"],
        "cleanup_cutoff": (datetime.fromisoformat(binding["deadline_at"]) + timedelta(seconds=5)).isoformat()}
    with pytest.raises(CreatorError):
        worker_request(job, claim)
