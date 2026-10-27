"""Independent native response-contract audit; clocks and all HTTP are synthetic."""
import copy
import socket
import sys
import types
from datetime import timedelta

import httpx
import pytest
from test_image_continuation_policy import arguments, continuation_case, seal_authority
from test_image_policy import accepted_raw, make_submit, reseal

from core.creator.store import digest
from core.execution import image_continuation_policy as continuation
from core.execution import receipt_worker, receipts
from core.execution.image_policy import (
    FrozenImageRequest,
    ImagePolicyError,
    ImageToolPolicy,
    build_image_query,
)
from core.execution.image_receipts import decode_submit
from core.execution.image_response_lifecycle import make_response_policy


@pytest.fixture(autouse=True)
def no_real_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Response lifecycle audit attempted real network")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", forbidden)


def response_case(seconds=180):
    case = continuation_case()
    case["authority"].update(schema_version=3, kind="image_continuation_authority_v3",
                             response_policy=make_response_policy(seconds))
    seal_authority(case["authority"])
    case["permit"] = issue(case)
    return case


def issue(case):
    return continuation.make_continuation_permit(case["scope"], case["grant"], case["job"], case["authority"],
        operation_id="2" * 32, index=case["authority"]["remaining_jobs"][0]["planned_submission_index"],
        reference_bindings=case["reference_bindings"])


def native_request(seconds=180):
    return continuation.build_image_submit_v2(**arguments(response_case(seconds)))


def collected_after_dispatch_deadline(tmp_path, monkeypatch, request, *, lag_seconds):
    """Exercise the original collector/atomic receipt store without a subprocess sleep."""
    binding = request.binding()
    dispatch_deadline = receipts.timestamp(binding["deadline_at"])
    response_deadline = receipts.timestamp(binding.get("response_deadline_at", binding["deadline_at"]))
    clock = [dispatch_deadline - timedelta(seconds=1)]
    calls = []
    for module in (receipt_worker, receipts):
        monkeypatch.setattr(module, "now", lambda: clock[0])
        monkeypatch.setattr(module, "utc_now", lambda: clock[0].isoformat())

    def handler(outgoing):
        calls.append({"method": outgoing.method, "at": clock[0],
                      "operation_id": outgoing.headers["X-Client-Operation-Id"]})
        clock[0] = dispatch_deadline + timedelta(seconds=lag_seconds)
        return httpx.Response(200, headers={"x-request-id": "synthetic-original-request"},
                              json={"code": 0, "data": {"id": "synthetic-original-task"}})

    factory = types.ModuleType("d42_audit_synthetic_transport")
    factory.make_transport = lambda options: httpx.MockTransport(handler)
    monkeypatch.setitem(sys.modules, factory.__name__, factory)
    job = {"kind": binding["kind"], "descriptor": request.frozen(), "binding": binding,
           "worker_factory": factory.__name__ + ":make_transport", "worker_options": {}}
    store = receipts.ReceiptStore(tmp_path.resolve() / "receipts")
    operation, created = store.prepare(binding, digest(job), "a" * 64, response_deadline.isoformat(),
                                      (response_deadline + timedelta(seconds=5)).isoformat())
    assert created
    receipt_worker.collect(operation, operation.claim(binding), {**job, "api_key": "SYNTHETIC-AUDIT-KEY"})
    raw, inspection = store.ready_raw_binding(binding["operation_id"], binding)
    assert len(calls) == 1 and calls[0]["method"] == "POST"
    assert calls[0]["at"] < dispatch_deadline and calls[0]["operation_id"] == binding["operation_id"]
    assert inspection["status"] == "candidate_found" and inspection["notification_present"]
    assert raw is not None
    return raw


@pytest.mark.parametrize("version", [1, 2])
def test_original_collector_reproduces_legacy_late_response_without_reinterpreting_it(tmp_path, monkeypatch, version):
    request = make_submit() if version == 1 else continuation.build_image_submit_v2(**arguments(continuation_case()))
    raw = collected_after_dispatch_deadline(tmp_path, monkeypatch, request, lag_seconds=1)
    result = decode_submit(request, raw)
    # This was the D42 red reproducer when the desired assertion was accepted.
    # Existing permits must retain that behavior; no retrospective permission.
    assert result["classification"] == "unconfirmed" and result["accepted_task_id"] is None
    assert result["submit_request_id"] == "synthetic-original-request"
    assert "response_deadline_at" not in request.binding()


@pytest.mark.parametrize("seconds", [1, 180])
def test_native_policy_binds_separate_full_dispatch_and_response_windows(seconds):
    request = native_request(seconds)
    value, binding = request.frozen(), request.binding()
    permit = value["permit"]
    assert value["schema_version"] == permit["schema_version"] == 4
    assert value["kind"] == permit["job_kind"] == binding["kind"] == "controlled_image_http_v3"
    assert value["scope"] == permit["scope"] == "accepted_plan_image_tools_v3"
    assert (receipts.timestamp(permit["deadline_at"]) - receipts.timestamp(permit["issued_at"])).total_seconds() == 120
    assert (receipts.timestamp(permit["response_deadline_at"]) - receipts.timestamp(permit["deadline_at"])).total_seconds() == seconds
    assert binding["response_window_seconds"] == seconds
    assert binding["response_contract_hash"] == permit["response_contract_hash"] == make_response_policy(seconds)["contract_hash"]
    assert binding["response_deadline_at"] == permit["response_deadline_at"]
    assert FrozenImageRequest.from_frozen(value) == request


def test_native_original_collector_accepts_response_after_dispatch_window(tmp_path, monkeypatch):
    request = native_request()
    raw = collected_after_dispatch_deadline(tmp_path, monkeypatch, request, lag_seconds=60)
    result = decode_submit(request, raw)
    assert result["classification"] == "accepted" and result["accepted_task_id"] == "synthetic-original-task"
    assert result["submit_request_id"] == "synthetic-original-request"


@pytest.mark.parametrize("mutation", ["response_missing", "response_early", "response_long", "contract",
                                     "dispatch_short", "dispatch_long", "schema_bool", "scope", "kind"])
def test_native_permit_rejects_resealed_time_and_version_changes(mutation):
    case = response_case()
    permit = case["permit"]
    if mutation == "response_missing":
        permit.pop("response_deadline_at")
    elif mutation in {"response_early", "response_long"}:
        permit["response_deadline_at"] = (receipts.timestamp(permit["deadline_at"]) +
            timedelta(seconds=0 if mutation == "response_early" else 181)).isoformat()
    elif mutation == "contract":
        permit["response_contract_hash"] = "0" * 64
    elif mutation in {"dispatch_short", "dispatch_long"}:
        permit["deadline_at"] = (receipts.timestamp(permit["issued_at"]) +
            timedelta(seconds=119 if mutation == "dispatch_short" else 121)).isoformat()
        permit["response_deadline_at"] = (receipts.timestamp(permit["deadline_at"]) + timedelta(seconds=180)).isoformat()
    elif mutation == "schema_bool":
        permit["schema_version"] = True
    elif mutation == "scope":
        permit["scope"] = "accepted_plan_image_tools_v2"
    else:
        permit["job_kind"] = "controlled_image_http_v2"
    with pytest.raises(ImagePolicyError):
        continuation.build_image_submit_v2(**arguments(case))


@pytest.mark.parametrize("native_authority", [False, True])
def test_authority_and_permit_versions_cannot_be_crossed(native_authority):
    legacy, native = continuation_case(), response_case()
    case = native if native_authority else legacy
    permit = copy.deepcopy(legacy["permit"] if native_authority else native["permit"])
    permit["continuation_authority_hash"] = case["authority"]["authority_hash"]
    case["permit"] = permit
    with pytest.raises(ImagePolicyError):
        continuation.build_image_submit_v2(**arguments(case))


@pytest.mark.parametrize("remaining_seconds", [120, 299])
def test_new_contract_refuses_short_lease_instead_of_shortening_either_window(monkeypatch, remaining_seconds):
    case = response_case()
    issued = receipts.timestamp(case["permit"]["issued_at"])
    policy = {key: value for key, value in case["authority"]["effective_policy"].items() if key != "policy_hash"}
    policy["valid_until"] = (issued + timedelta(seconds=remaining_seconds)).isoformat()
    case["authority"]["effective_policy"] = ImageToolPolicy.model_validate(policy).frozen()
    seal_authority(case["authority"])
    before = copy.deepcopy(case)
    monkeypatch.setattr(continuation, "now", lambda: issued)
    with pytest.raises(ImagePolicyError):
        issue(case)
    assert case == before


def test_query_carries_native_contract_with_its_own_full_windows():
    submit = native_request()
    raw = accepted_raw(submit)
    permit = copy.deepcopy(submit.frozen()["permit"])
    permit.pop("permit_hash")
    issued = receipts.timestamp(raw["received_at"])
    permit.update(operation_id="5" * 32, capture_id="5" * 32, nonce="6" * 32, action="query", query_index=1,
        paid_calls=0, risk_cny="0.000000", issued_at=issued.isoformat(), deadline_at=(issued + timedelta(seconds=120)).isoformat(),
        response_deadline_at=(issued + timedelta(seconds=300)).isoformat())
    query = build_image_query(submit=submit, submit_raw=raw, permit=permit, query_index=1)
    assert query.frozen()["schema_version"] == 4 and query.method == "GET"
    assert query.binding()["response_contract_hash"] == submit.binding()["response_contract_hash"]
    assert query.binding()["response_deadline_at"] != submit.binding()["response_deadline_at"]
    assert query.binding()["parent_descriptor_hash"] == submit.descriptor_hash
    changed = query.frozen()
    changed["kind"] = "controlled_image_http_v2"
    with pytest.raises(ImagePolicyError):
        FrozenImageRequest.from_frozen(reseal(changed))


def test_legacy_descriptor_rejects_response_fields_even_when_resealed():
    value = continuation.build_image_submit_v2(**arguments(continuation_case())).frozen()
    value["permit"].update(response_deadline_at=value["permit"]["deadline_at"], response_contract_hash="a" * 64)
    value["permit"]["permit_hash"] = digest({key: item for key, item in value["permit"].items() if key != "permit_hash"})
    with pytest.raises(ImagePolicyError):
        FrozenImageRequest.from_frozen(reseal(value))


def test_explicit_get_observation_accepts_native_parent_without_renewing_its_response_contract(monkeypatch):
    from core.execution import image_observation_policy as observation
    from core.execution.image_observation_continuation import validate_authority

    case = response_case()
    submit = continuation.build_image_submit_v2(**arguments(case))
    parent = submit.frozen()
    raw = accepted_raw(submit)
    raw["received_at"] = (receipts.timestamp(submit.deadline_at) + timedelta(seconds=60)).isoformat()
    receipt = decode_submit(submit, raw)
    assert receipt["classification"] == "accepted"
    source = {"id": parent["permit"]["operation_id"], "status": "accepted", "descriptor": parent,
              "raw": raw, "receipt": receipt}
    original_source = copy.deepcopy(source)
    original_authority = case["authority"]
    later = receipts.timestamp(original_authority["effective_policy"]["valid_until"]) + timedelta(seconds=1)
    effective = {key: value for key, value in original_authority["effective_policy"].items() if key != "policy_hash"}
    effective["valid_until"] = (later + timedelta(hours=1)).isoformat()
    inherited = {key: original_authority[key] for key in (
        "control_hash", "scope_id", "scope_hash", "task_id", "grant_id", "grant_hash", "intent_id", "intent_hash",
        "validity_activation_id", "validity_activation_hash", "parent_receipt_hash", "original_budget_anchor")}
    funding = {key: inherited[key] for key in ("control_hash", "scope_id", "intent_id", "intent_hash",
        "validity_activation_id", "validity_activation_hash", "parent_receipt_hash")}
    funding["allocations"] = copy.deepcopy(original_authority["allocations"])
    funding["funding_hash"] = digest(funding)
    authority = seal_authority({**inherited, "schema_version": 1, "kind": "image_observation_authority_v1",
        "activation_id": "a" * 32, "quote_id": "e" * 32, "revision_sequence": 1,
        "source_operation_id": source["id"], "source_descriptor_hash": submit.descriptor_hash,
        "source_raw_hash": digest(raw), "source_receipt_hash": receipt["receipt_hash"],
        "source_capture_started_at": (receipts.timestamp(submit.deadline_at) - timedelta(seconds=1)).isoformat(),
        "accepted_task_id": receipt["accepted_task_id"], "parent_authority": copy.deepcopy(original_authority),
        "funding": funding, "effective_policy": ImageToolPolicy.model_validate(effective).frozen(),
        "code_manifest_hash": "e" * 64, "additional_risk_cny": "0.000000", "additional_paid_calls": 0,
        "allowed_method": "GET", "allow_source_pending_fee": True})
    validate_authority(authority)
    monkeypatch.setattr(observation, "now", lambda: later)
    permit = observation.make_observation_permit(case["scope"], case["grant"], source, authority,
                                                operation_id="5" * 32, query_index=1)
    query = observation.build_observation_query(submit=submit, submit_raw=raw, permit=permit,
                                                observation_authority=authority, query_index=1)
    assert query.method == "GET" and query.frozen()["kind"] == "controlled_image_observation_http_v1"
    assert query.frozen()["parent_submit"] == original_source["descriptor"]
    assert source == original_source and query.binding()["source_operation_id"] == source["id"]
    assert "response_deadline_at" not in query.binding() and "response_contract_hash" not in permit
    assert receipts.timestamp(permit["issued_at"]) > receipts.timestamp(original_authority["effective_policy"]["valid_until"])
    with pytest.raises(ImagePolicyError):
        observation.build_observation_query(submit=submit, submit_raw=raw,
            permit={**permit, "response_deadline_at": parent["permit"]["response_deadline_at"]},
            observation_authority=authority, query_index=1)
