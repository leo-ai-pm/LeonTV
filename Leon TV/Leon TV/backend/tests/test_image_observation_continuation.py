"""Accepted fictional v2 POSTs gain only explicitly approved GET observation."""
import copy
from datetime import timedelta

import pytest
from asset_operator_fixture import asset_control_case as asset_control_case
from test_image_runtime_continuation import prepared_runtime_case

from core.creator.store import CreatorError, digest
from core.execution import image_observation_continuation as observation
from core.execution import image_operator, image_runtime_continuation
from core.execution.image_runtime_control import RuntimeContinuedImageControl
from core.execution.media_ledger import has_pending_fees, has_pending_unknown, records
from core.execution.policy import now
from core.execution.scope_budget import scope_envelope
from core.execution.store import validate_state


def test_observation_review_is_explicit_off_and_sanitizes_malformed_inputs(monkeypatch, capsys):
    from core.execution import image_observation_operator

    monkeypatch.setenv("LEONTV_IMAGE_CONTROL_FILE", "/private/fictional-implicit-observer-control")
    monkeypatch.setattr(image_observation_operator, "read_image_control", lambda *a, **k: pytest.fail("implicit observer"))
    assert image_observation_operator.main([]) == 0
    value = json_value(capsys.readouterr().out)
    assert value["mode"] == "off" and value["paid_calls"] == 0 and value["dispatch_available"] is False
    for argv in (["start"], ["quote", "--operation", "f" * 32], ["status", "--parent-ledger", "/private/secret"]):
        assert image_observation_operator.main(argv) == 1
        output = capsys.readouterr()
        assert output.err == "" and "/private/secret" not in output.out
        assert json_value(output.out)["error"] == "IMAGE_OBSERVATION_REVIEW_UNAVAILABLE"


def json_value(value):
    import json
    return json.loads(value)


def accepted_observation_source(asset_control_case):
    case = prepared_runtime_case(asset_control_case)
    control = RuntimeContinuedImageControl(case.control, case.runtime_activation["id"])
    target = case.runtime_authority["remaining_jobs"][0]
    with image_operator.build_image_session(control, receipt_capture=case.settings) as session:
        op = session.media.reserve_submit(case.task["id"], case.grant["id"], target["shot_id"], key="observation-source-reserve")["operation"]
        case.accepted_operation = session.media.submit(case.task["id"], op["id"])["operation"]
    assert case.accepted_operation["status"] == "accepted"
    return case


def prepared_observation_case(asset_control_case):
    case = accepted_observation_source(asset_control_case)
    case.observation_valid_until = (now() + timedelta(hours=6)).isoformat()
    case.observation_quote = observation.quote_observation(case.control, case.accepted_operation["id"],
        valid_until=case.observation_valid_until)["quote"]
    case.observation_activation = observation.activate_observation(case.control, case.observation_quote["id"],
        disclosure_hash=case.observation_quote["disclosure_hash"], consent=True)["activation"]
    case.observation_authority = observation.authority_for(case.env.runtime.store.read_only(), case.observation_activation["id"])
    return case


def test_code_and_lease_observation_approval_retains_original_post_funding_and_independent_checks(asset_control_case, monkeypatch):
    case = accepted_observation_source(asset_control_case)
    store = case.env.runtime.store
    before = store.read_only()
    original = copy.deepcopy(case.accepted_operation)
    frames = observation._state(store)["frames"]
    parent = case.server_parent_path.read_bytes()
    account = case.control.document.budget_anchor["account_scope_id"]
    envelope = scope_envelope(before, account)
    # Changed fictional code invalidates runtime dispatch, not the historical
    # accepted POST or its already-retained original allocations.
    name = next(iter(case.control.document.code_manifest))
    (case.backend / name).write_text("# Explicit fictional observation implementation revision.\n")
    with pytest.raises(CreatorError):
        image_runtime_continuation.verify_current(case.control, before, case.runtime_activation["id"])
    later = now() + timedelta(hours=5)
    monkeypatch.setattr(observation, "now", lambda: later)
    until = (later + timedelta(hours=2)).isoformat()
    q = observation.quote_observation(case.control, original["id"], valid_until=until)["quote"]
    for consent in (False, 1, "true", None):
        native = store.path("state.json").read_bytes()
        with pytest.raises(CreatorError):
            observation.activate_observation(case.control, q["id"], disclosure_hash=q["disclosure_hash"], consent=consent)
        assert store.path("state.json").read_bytes() == native
    a = observation.activate_observation(case.control, q["id"], disclosure_hash=q["disclosure_hash"], consent=True)["activation"]
    actual, authority = observation.verified_current(case.control, a["id"])
    assert authority["allowed_method"] == "GET" and authority["additional_paid_calls"] == 0
    assert authority["additional_risk_cny"] == "0.000000" and authority["source_operation_id"] == original["id"]
    assert authority["source_descriptor_hash"] == original["descriptor"]["descriptor_hash"]
    assert authority["source_raw_hash"] == digest(original["raw"])
    assert authority["source_receipt_hash"] == original["receipt"]["receipt_hash"]
    assert authority["source_capture_started_at"] == original["capture_started_at"]
    assert actual["media"]["operation:" + original["id"]] == original
    assert actual["media"]["operation:" + case.unknown["id"]] == case.unknown
    assert has_pending_unknown(actual, account) and has_pending_fees(actual, account)
    assert all(digest(actual[n]) == digest(v) for n, v in before.items() if n != "media")
    assert all(actual["media"][k] == v for k, v in before["media"].items())
    assert observation._state(store)["frames"][:len(frames)] == frames
    assert scope_envelope(actual, account) == envelope and case.server_parent_path.read_bytes() == parent
    original_verified, decisions = case.control.__class__._verified, []

    def checked(self, *args, **kwargs):
        decisions.append(kwargs.get("for_dispatch"))
        return original_verified(self, *args, **kwargs)

    monkeypatch.setattr(case.control.__class__, "_verified", checked)
    assert observation.verify_current(case.control, actual, a["id"])
    assert observation.verify_current(case.control, actual, a["id"])
    assert decisions == [False, False]
    frozen = store.path("state.json").read_bytes()
    assert observation.quote_observation(case.control, original["id"], valid_until=until)["replayed"]
    assert observation.activate_observation(case.control, q["id"], disclosure_hash=q["disclosure_hash"], consent=True)["replayed"]
    assert store.path("state.json").read_bytes() == frozen and case.server_parent_path.read_bytes() == parent
    assert len(records(store.read_only(), "query")) == len(records(before, "query"))
    native_records = observation.records

    def exhausted_queries(payload, kind):
        if kind == "query":
            return [{"operation_id": original["id"], "scope_id": original["scope_id"], "status": "completed"}
                for _ in range(authority["effective_policy"]["max_queries"])]
        return native_records(payload, kind)

    # Exercise the counter boundary through the real management entry points,
    # without issuing extra mock GETs just to fill a ceiling. Existing approval
    # replays are read-only; a new quote still cannot reset the consumed count.
    with monkeypatch.context() as patch:
        patch.setattr(observation, "records", exhausted_queries)
        assert observation.quote_observation(case.control, original["id"], valid_until=until)["replayed"]
        assert observation.activate_observation(case.control, q["id"], disclosure_hash=q["disclosure_hash"], consent=True)["replayed"]
        with pytest.raises(CreatorError):
            observation.quote_observation(case.control, original["id"], valid_until=(later + timedelta(hours=3)).isoformat())
    assert store.path("state.json").read_bytes() == frozen and case.server_parent_path.read_bytes() == parent


def test_observation_quote_birth_code_drift_and_latest_approval_are_not_self_sealed_authority(asset_control_case):
    case = prepared_observation_case(asset_control_case)
    store = case.env.runtime.store
    old_a = case.observation_activation
    old = case.observation_authority
    until = (now() + timedelta(hours=7)).isoformat()
    q = observation.quote_observation(case.control, case.accepted_operation["id"], valid_until=until)["quote"]
    name = next(iter(case.control.document.code_manifest))
    (case.backend / name).write_text("# Another unapproved fictional observation revision.\n")
    native = store.path("state.json").read_bytes()
    with pytest.raises(CreatorError):
        observation.activate_observation(case.control, q["id"], disclosure_hash=q["disclosure_hash"], consent=True)
    assert store.path("state.json").read_bytes() == native
    q = observation.quote_observation(case.control, case.accepted_operation["id"], valid_until=until)["quote"]
    a = observation.activate_observation(case.control, q["id"], disclosure_hash=q["disclosure_hash"], consent=True)["activation"]
    assert a["activation_sequence"] == 2 and a["previous_activation_id"] == old_a["id"]
    assert observation.authority_for(store.read_only(), old_a["id"]) == old
    with pytest.raises(CreatorError):
        observation.verify_current(case.control, store.read_only(), old_a["id"])
    assert observation.verify_current(case.control, store.read_only(), a["id"])
    state = observation._state(store)
    qkey = observation.QUOTE + ":" + q["id"]
    frame = next(f for f in state["frames"] if qkey in f["delta"].get("media", {}))
    frame["delta"]["media"][qkey]["disclosure"]["checkpoint"]["head_revision"] -= 1
    with pytest.raises(CreatorError):
        validate_state(state)
