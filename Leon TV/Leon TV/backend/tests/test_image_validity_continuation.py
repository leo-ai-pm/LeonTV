"""Durable validity consent and funding leave original dispatch disabled."""
import copy
import socket
from datetime import timedelta

import pytest
from asset_operator_fixture import asset_control_case as asset_control_case
from test_image_continuation_proposal import prepared_proposal_case

from core.creator.store import CreatorError, digest
from core.execution import image_continuation_parent_io as parent_io
from core.execution import image_validity_continuation as continuation
from core.execution.policy import now, units
from core.execution.scope_budget import scope_envelope


def prepared_validity_case(asset_control_case):
    case = prepared_proposal_case(asset_control_case)
    path = parent_io.server_parent_path(case.control)
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    path.write_bytes(case.live_parent_path.read_bytes())
    path.chmod(0o600)
    case.server_parent_path = path
    return case


def quote(case):
    return continuation.quote_continuation(case.control, valid_until=case.valid_until)


def prepare(case, quoted):
    record = quoted["quote"]
    return continuation.prepare_continuation(case.control, record["id"],
        proposal_hash=record["proposal"]["proposal_hash"], consent=True)


def activate(case, quoted, prepared):
    return continuation.activate_continuation(case.control, prepared["intent"]["id"],
        proposal_hash=quoted["quote"]["proposal"]["proposal_hash"], consent=True)


def test_same_original_journal_quote_intent_activation_replays_and_preserves_unknown(asset_control_case, monkeypatch):
    case = prepared_validity_case(asset_control_case)
    store = case.env.runtime.store
    before = store.read_only()
    original_frames = copy.deepcopy(continuation._state(store)["frames"])
    original_parent = case.server_parent_path.read_bytes()
    account_id = case.control.document.budget_anchor["account_scope_id"]
    opening = scope_envelope(before, account_id)
    monkeypatch.setattr(socket.socket, "connect", lambda *args, **kwargs: pytest.fail("Network during continuation"))
    quoted = quote(case)
    assert quoted["quote"]["proposal"]["basis"]["checkpoint"]["payload_hash"] == digest(before)
    assert not quoted["replayed"] and case.server_parent_path.read_bytes() == original_parent
    qbytes = store.path("state.json").read_bytes()
    assert quote(case)["replayed"] and store.path("state.json").read_bytes() == qbytes
    prepared = prepare(case, quoted)
    intent = prepared["intent"]
    assert intent["additional_risk_cny"] == "1.000000" and intent["additional_paid_calls"] == 1
    held = scope_envelope(store.read_only(), account_id)
    assert held["risk_units"] == opening["risk_units"] + 1000000
    assert held["max_calls"] == opening["max_calls"] + 1
    ibytes = store.path("state.json").read_bytes()
    assert prepare(case, quoted)["replayed"] and store.path("state.json").read_bytes() == ibytes
    assert case.server_parent_path.read_bytes() == original_parent
    activated = activate(case, quoted, prepared)
    record = activated["activation"]
    assert record["dispatch_ready"] is False and record["runtime_contract_pending"] is True
    assert record["parent_receipt"]["allocation_reserved"] is True
    current = store.read_only()
    assert current["media"]["operation:" + case.unknown["id"]] == case.unknown
    assert all(current[name] == before[name] for name in before if name != "media")
    assert all(current["media"][key] == item for key, item in before["media"].items())
    assert continuation._state(store)["frames"][:-3] == original_frames
    assert scope_envelope(current, account_id) == held
    abytes, parent_after = store.path("state.json").read_bytes(), case.server_parent_path.read_bytes()
    assert activate(case, quoted, prepared)["replayed"]
    assert prepare(case, quoted)["replayed"]
    assert store.path("state.json").read_bytes() == abytes and case.server_parent_path.read_bytes() == parent_after
    assert continuation.require_reservation_intent(current, quoted["quote"]["proposal"]) == intent
    with pytest.raises(CreatorError):
        case.media.reserve_submit(case.task["id"], case.grant["id"], case.manifest["frames"][-1]["shot_id"], key="validity-still-no-runtime")


@pytest.mark.parametrize("consent", [False, 1, "true", None])
def test_exact_consent_required_before_retaining_any_new_budget(asset_control_case, consent):
    case = prepared_validity_case(asset_control_case)
    record = quote(case)["quote"]
    before = case.env.runtime.store.path("state.json").read_bytes()
    parent = case.server_parent_path.read_bytes()
    with pytest.raises(CreatorError):
        continuation.prepare_continuation(case.control, record["id"], proposal_hash=record["proposal"]["proposal_hash"], consent=consent)
    assert case.env.runtime.store.path("state.json").read_bytes() == before
    assert case.server_parent_path.read_bytes() == parent


def test_durable_intent_retains_budget_after_parent_write_failure(asset_control_case, monkeypatch):
    case = prepared_validity_case(asset_control_case)
    quoted, opening = quote(case), case.env.runtime.store.read_only()
    prepared = prepare(case, quoted)
    after_intent = case.env.runtime.store.path("state.json").read_bytes()
    account = case.control.document.budget_anchor["account_scope_id"]

    def failed(*args, **kwargs):
        raise OSError("Fictional parent writer interruption")

    with monkeypatch.context() as patch:
        patch.setattr(parent_io, "reserve_extension", failed)
        with pytest.raises(CreatorError):
            activate(case, quoted, prepared)
    assert case.env.runtime.store.path("state.json").read_bytes() == after_intent
    assert scope_envelope(case.env.runtime.store.read_only(), account)["risk_units"] == scope_envelope(opening, account)["risk_units"] + 1000000
    assert prepare(case, quoted)["replayed"]
    assert not activate(case, quoted, prepared)["replayed"]


def test_no_second_quote_or_intent_after_reservation_and_no_code_drift_approval(asset_control_case):
    case = prepared_validity_case(asset_control_case)
    quoted = quote(case)
    prepared = prepare(case, quoted)
    before = case.env.runtime.store.path("state.json").read_bytes()
    with pytest.raises(CreatorError):
        continuation.quote_continuation(case.control, valid_until=(now() + timedelta(hours=3)).isoformat())
    name = next(iter(case.control.document.code_manifest))
    (case.backend / name).write_text("# Unapproved new implementation.\n")
    with pytest.raises(CreatorError):
        activate(case, quoted, prepared)
    assert case.env.runtime.store.path("state.json").read_bytes() == before


def test_quote_checkpoint_drift_stops_before_intent_or_parent_write(asset_control_case):
    case = prepared_validity_case(asset_control_case)
    quoted = quote(case)
    case.media.quote(case.task["id"], key="validity-intervening-frame")
    before = case.env.runtime.store.path("state.json").read_bytes()
    parent = case.server_parent_path.read_bytes()
    with pytest.raises(CreatorError):
        prepare(case, quoted)
    assert case.env.runtime.store.path("state.json").read_bytes() == before
    assert case.server_parent_path.read_bytes() == parent
    fresh = quote(case)
    assert fresh["quote"]["id"] != quoted["quote"]["id"] and not fresh["replayed"]
    assert not prepare(case, fresh)["replayed"]


def test_capacity_refusal_precedes_artifact_intent_and_parent_reservation(asset_control_case, monkeypatch):
    case = prepared_validity_case(asset_control_case)
    store = case.env.runtime.store
    original, parent = store.path("state.json").read_bytes(), case.server_parent_path.read_bytes()
    with monkeypatch.context() as patch:
        patch.setattr(continuation, "MAX_STATE_BYTES", 1)
        with pytest.raises(CreatorError):
            quote(case)
    assert store.path("state.json").read_bytes() == original
    assert not (store.root / "continuation-parent").exists()
    quoted = quote(case)
    qbytes = store.path("state.json").read_bytes()
    with monkeypatch.context() as patch:
        patch.setattr(continuation, "MAX_STATE_BYTES", 1)
        with pytest.raises(CreatorError):
            prepare(case, quoted)
    assert store.path("state.json").read_bytes() == qbytes
    prepared = prepare(case, quoted)
    ibytes = store.path("state.json").read_bytes()
    attempted = []
    with monkeypatch.context() as patch:
        patch.setattr(continuation, "MAX_STATE_BYTES", 1)
        patch.setattr(parent_io, "reserve_extension", lambda *args, **kwargs: attempted.append(True))
        with pytest.raises(CreatorError):
            activate(case, quoted, prepared)
    assert attempted == [] and store.path("state.json").read_bytes() == ibytes
    assert case.server_parent_path.read_bytes() == parent


def test_pure_record_validation_does_not_call_control_or_store(asset_control_case, monkeypatch):
    case = prepared_validity_case(asset_control_case)
    quoted = quote(case)
    prepared = prepare(case, quoted)
    activate(case, quoted, prepared)
    payload = case.env.runtime.store.read_only()

    def forbidden(*args, **kwargs):
        pytest.fail("Historical pure validation attempted live IO")

    monkeypatch.setattr(case.control.__class__, "_verified", forbidden)
    monkeypatch.setattr(case.env.runtime.store.__class__, "read_only", forbidden)
    monkeypatch.setattr(parent_io, "verify_extension", forbidden)
    for item in payload["media"].values():
        if item["kind"] in continuation.KINDS:
            continuation.validate_record(payload, item)
            changed = copy.deepcopy(item)
            changed["dispatch_ready"] = True
            with pytest.raises(CreatorError):
                continuation.validate_record(payload, changed)
    assert sum(units(item["additional_risk_cny"]) for item in continuation.retained_extensions(payload,
        case.control.document.budget_anchor["account_scope_id"])) == 1000000
