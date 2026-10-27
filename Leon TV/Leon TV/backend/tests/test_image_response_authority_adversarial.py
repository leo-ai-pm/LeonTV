"""Independent response authority attacks using only public synthetic fixtures."""
import copy
import socket

import httpx
import pytest
from asset_operator_fixture import asset_control_case as asset_control_case
from image_operator_fixture import posts
from test_image_continuation_policy import continuation_case, seal_authority
from test_image_runtime_continuation import prepared_runtime_case

from core.creator.store import CreatorError, digest
from core.execution import image_runtime_continuation as continuation
from core.execution.image_response_lifecycle import make_response_policy
from core.execution.image_runtime_control import RuntimeContinuedImageControl
from core.execution.scope_budget import scope_envelope


@pytest.fixture(autouse=True)
def no_external_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Response authority audit attempted real network")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", forbidden)


def response_authority():
    value = continuation_case()["authority"]
    value.update(schema_version=3, kind=continuation.RESPONSE_AUTHORITY_KIND,
                 response_policy=make_response_policy(180))
    return seal_authority(value)


@pytest.mark.parametrize("attack", ["schema_bool", "schema_string", "extra", "missing_policy",
    "schema_downgrade", "kind_downgrade", "policy_schema_bool", "window_bool", "window_float",
    "dispatch_changed", "cleanup_changed", "policy_extra", "calls_bool", "caps_bool",
    "clear_unknown", "retry_unknown", "zero_fee", "extra_budget"])
def test_fully_rehashed_authority_rejects_type_injection_and_mixed_versions(attack):
    value = response_authority()
    policy = value["response_policy"]
    if attack == "schema_bool":
        value["schema_version"] = True
    elif attack == "schema_string":
        value["schema_version"] = "3"
    elif attack == "extra":
        value["implicit_response_authorized"] = True
    elif attack == "missing_policy":
        del value["response_policy"]
    elif attack == "schema_downgrade":
        value["schema_version"] = 2
    elif attack == "kind_downgrade":
        value["kind"] = continuation.AUTHORITY_KIND
    elif attack == "policy_schema_bool":
        policy["schema_version"] = True
    elif attack == "window_bool":
        policy["response_window_seconds"] = True
    elif attack == "window_float":
        policy["response_window_seconds"] = 180.0
    elif attack == "dispatch_changed":
        policy["dispatch_window_seconds"] = 121
    elif attack == "cleanup_changed":
        policy["cleanup_grace_seconds"] = 6
    elif attack == "policy_extra":
        policy["extend_existing_receipts"] = True
    elif attack == "calls_bool":
        value["additional_paid_calls"] = False
    elif attack == "caps_bool":
        value["max_cumulative_submissions"] = True
    elif attack == "clear_unknown":
        value["unknown_disposition"]["new_unknown_stops_continuation"] = False
    elif attack == "retry_unknown":
        value["unknown_disposition"]["old_request_retry_authorized"] = True
    elif attack == "zero_fee":
        value["unknown_disposition"]["zero_fee_proven"] = True
    else:
        value["additional_risk_cny"] = "1.000000"
    policy["contract_hash"] = digest({key: item for key, item in policy.items() if key != "contract_hash"})
    with pytest.raises(CreatorError):
        continuation.validate_authority(seal_authority(value))


def test_legacy_authority_shape_stays_exact_and_has_no_implicit_response_policy():
    original = continuation_case()["authority"]
    expected = copy.deepcopy(original)
    checked = continuation.validate_authority(original)
    assert checked == expected and set(checked) == continuation.AUTHORITY_FIELDS
    assert "response_policy" not in checked and original == expected
    checked["remaining_jobs"][0]["shot_id"] = "changed-copy-only"
    assert original == expected
    injected = copy.deepcopy(original)
    injected["response_policy"] = make_response_policy(180)
    with pytest.raises(CreatorError):
        continuation.validate_authority(seal_authority(injected))


def test_native_frozen_quote_and_authority_reject_rehashed_policy_substitution(asset_control_case):
    case = prepared_runtime_case(asset_control_case)
    store = case.env.runtime.store
    parent_before, http_before = case.server_parent_path.read_bytes(), len(posts(case))
    before = store.read_only()
    account = case.control.document.budget_anchor["account_scope_id"]
    budget_before = scope_envelope(before, account)
    old_authority = copy.deepcopy(case.runtime_authority)
    q = continuation.quote_runtime_continuation(case.control, valid_until=case.runtime_valid_until,
        response_window_seconds=180)["quote"]
    frozen_quote_state = store.path("state.json").read_bytes()
    # A hash of another valid response policy is not consent to this quote.
    alternate = copy.deepcopy(q["disclosure"])
    alternate["response_policy"] = make_response_policy(60)
    with pytest.raises(CreatorError):
        continuation.activate_runtime_continuation(case.control, q["id"],
            disclosure_hash=digest(alternate), consent=True)
    assert store.path("state.json").read_bytes() == frozen_quote_state
    payload = store.read_only()
    for fault in ("schema_bool", "required_version", "unknown_extra", "policy_kind", "policy_missing"):
        damaged = copy.deepcopy(q)
        d = damaged["disclosure"]
        if fault == "schema_bool":
            d["schema_version"] = True
        elif fault == "required_version":
            d["required_contract_version"] = "image_continuation_v2"
        elif fault == "unknown_extra":
            d["implicit_consent"] = True
        elif fault == "policy_kind":
            d["kind"] = continuation.DISCLOSURE_KIND
        else:
            del d["response_policy"]
        damaged["disclosure_hash"] = digest(d)
        damaged["id"] = digest({"kind": continuation.QUOTE, "scope_id": damaged["scope_id"],
            "disclosure": d, "disclosure_hash": damaged["disclosure_hash"]})[:32]
        with pytest.raises(CreatorError):
            continuation.validate_record(payload, damaged)
    activation = continuation.activate_runtime_continuation(case.control, q["id"],
        disclosure_hash=q["disclosure_hash"], consent=True)["activation"]
    control = RuntimeContinuedImageControl(case.control, activation["id"])
    actual, authority = control.verified_current()
    scope = actual["media"]["scope:" + authority["scope_id"]]
    stable = store.path("state.json").read_bytes()
    assert control.guard_runtime(scope, authority)
    for fault in ("valid_alternate_policy", "complete_legacy_downgrade"):
        forged = copy.deepcopy(authority)
        if fault == "valid_alternate_policy":
            forged["response_policy"] = make_response_policy(60)
        else:
            forged.update(schema_version=2, kind=continuation.AUTHORITY_KIND)
            del forged["response_policy"]
        # Pure self-consistency is deliberately insufficient for native authority.
        continuation.validate_authority(seal_authority(forged))
        with pytest.raises(CreatorError):
            control.guard_runtime(scope, forged)
    assert store.path("state.json").read_bytes() == stable
    assert continuation.authority_for(actual, case.runtime_activation["id"]) == old_authority
    assert authority["response_policy"] == q["disclosure"]["response_policy"]
    assert authority["intent_id"] == old_authority["intent_id"]
    assert authority["allocations"] == old_authority["allocations"]
    assert scope_envelope(actual, account) == budget_before
    assert actual["media"]["operation:" + case.unknown["id"]] == case.unknown
    assert case.server_parent_path.read_bytes() == parent_before and len(posts(case)) == http_before
