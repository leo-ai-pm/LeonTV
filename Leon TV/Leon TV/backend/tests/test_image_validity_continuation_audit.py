"""Independent native-journal attacks on authorization-only continuation."""
import copy
import hashlib
import json
from contextlib import contextmanager
from datetime import timedelta

import pytest
from asset_operator_fixture import asset_control_case as asset_control_case
from asset_operator_fixture import private_json
from test_image_validity_continuation import prepared_validity_case

from core.creator.store import CreatorError, digest
from core.execution import image_continuation_parent as parent
from core.execution import image_continuation_parent_io as parent_io
from core.execution import image_control, media_runtime
from core.execution import image_validity_continuation as continuation
from core.execution.receipts import timestamp
from core.execution.scope_budget import scope_envelope
from core.execution.store import ExecutionStore, validate_state


def quoted(case):
    return continuation.quote_continuation(case.control, valid_until=case.valid_until)


def prepared(case, result):
    quote = result["quote"]
    return continuation.prepare_continuation(case.control, quote["id"],
        proposal_hash=quote["proposal"]["proposal_hash"], consent=True)


def activated(case, result, intent):
    return continuation.activate_continuation(case.control, intent["intent"]["id"],
        proposal_hash=result["quote"]["proposal"]["proposal_hash"], consent=True)


def state_bytes(case):
    return case.env.runtime.store.path("state.json").read_bytes()


def envelope(case):
    return scope_envelope(case.env.runtime.store.read_only(), case.anchor["account_scope_id"])


def assert_old_records_unchanged(case, original):
    current = case.env.runtime.store.read_only()
    for namespace, records in original.items():
        assert all(current[namespace][key] == record for key, record in records.items())
        if namespace != "media":
            assert current[namespace] == records
    assert current["media"]["operation:" + case.unknown["id"]] == case.unknown


def test_exact_intent_holds_risk_before_parent_write_and_preserves_all_old_records(asset_control_case, monkeypatch):
    case = prepared_validity_case(asset_control_case)
    original = copy.deepcopy(case.env.runtime.store.read_only())
    frames = copy.deepcopy(json.loads(state_bytes(case))["frames"])
    opening = envelope(case)
    parent_bytes = case.server_parent_path.read_bytes()

    def forbidden(*args, **kwargs):
        pytest.fail("Quote or intent attempted a parent budget write")

    monkeypatch.setattr(parent_io, "reserve_extension", forbidden)
    quote = quoted(case)
    assert envelope(case) == opening and case.server_parent_path.read_bytes() == parent_bytes
    intent = prepared(case, quote)
    held = envelope(case)
    assert held == {"risk_units": opening["risk_units"] + 1000000, "max_calls": opening["max_calls"] + 1}
    assert intent["intent"]["consent"] is True and intent["intent"]["dispatch_ready"] is False
    after = state_bytes(case)
    assert prepared(case, quote)["replayed"] is True
    assert state_bytes(case) == after and envelope(case) == held
    assert case.server_parent_path.read_bytes() == parent_bytes
    assert json.loads(after)["frames"][:-2] == frames
    assert_old_records_unchanged(case, original)


def test_parent_failure_cannot_remove_the_intent_or_reserve_a_second_envelope(asset_control_case, monkeypatch):
    case = prepared_validity_case(asset_control_case)
    original = copy.deepcopy(case.env.runtime.store.read_only())
    quote = quoted(case)
    intent = prepared(case, quote)
    held, native = envelope(case), state_bytes(case)
    before_parent = case.server_parent_path.read_bytes()

    def interrupted(*args, **kwargs):
        raise OSError("Fictional interruption before the parent write")

    with monkeypatch.context() as patch:
        patch.setattr(parent_io, "reserve_extension", interrupted)
        for _ in range(2):
            with pytest.raises(CreatorError):
                activated(case, quote, intent)
            assert state_bytes(case) == native and envelope(case) == held
            assert case.server_parent_path.read_bytes() == before_parent
    assert activated(case, quote, intent)["replayed"] is False
    after_parent, after_native = case.server_parent_path.read_bytes(), state_bytes(case)
    assert len(json.loads(after_parent)["operations"]) == len(json.loads(before_parent)["operations"]) + 1
    assert activated(case, quote, intent)["replayed"] is True
    assert state_bytes(case) == after_native and case.server_parent_path.read_bytes() == after_parent
    assert envelope(case) == held
    assert_old_records_unchanged(case, original)


def test_parent_commit_then_native_activation_failure_reuses_the_one_reserved_allocation(asset_control_case, monkeypatch):
    case = prepared_validity_case(asset_control_case)
    quote = quoted(case)
    intent = prepared(case, quote)
    before_parent, native, held = case.server_parent_path.read_bytes(), state_bytes(case), envelope(case)
    original_transaction = ExecutionStore.transaction
    injected = []

    @contextmanager
    def fail_activation_commit(store):
        with original_transaction(store) as payload:
            old = set(payload.get("media", {}))
            yield payload
            if any(key not in old and record["kind"] == "validity_continuation_activation"
                   for key, record in payload.get("media", {}).items()):
                injected.append(True)
                raise CreatorError("SYNTHETIC_DISK_FAILURE", "Fictional activation commit failure", 409)

    with monkeypatch.context() as patch:
        patch.setattr(ExecutionStore, "transaction", fail_activation_commit)
        with pytest.raises(CreatorError):
            activated(case, quote, intent)
    assert injected == [True] and state_bytes(case) == native and envelope(case) == held
    reserved_parent = case.server_parent_path.read_bytes()
    assert len(json.loads(reserved_parent)["operations"]) == len(json.loads(before_parent)["operations"]) + 1
    assert activated(case, quote, intent)["replayed"] is False
    assert case.server_parent_path.read_bytes() == reserved_parent and envelope(case) == held
    after = state_bytes(case)
    assert activated(case, quote, intent)["replayed"] is True
    assert state_bytes(case) == after and case.server_parent_path.read_bytes() == reserved_parent


def test_funded_intent_requires_exact_consent_and_proposal_again_before_activation(asset_control_case):
    case = prepared_validity_case(asset_control_case)
    quote = quoted(case)
    intent = prepared(case, quote)
    native, held, live = state_bytes(case), envelope(case), case.server_parent_path.read_bytes()
    for consent in (False, 1, "true", None):
        with pytest.raises(CreatorError):
            continuation.activate_continuation(case.control, intent["intent"]["id"],
                proposal_hash=quote["quote"]["proposal"]["proposal_hash"], consent=consent)
    with pytest.raises(CreatorError):
        continuation.activate_continuation(case.control, intent["intent"]["id"], proposal_hash="0" * 64, consent=True)
    assert state_bytes(case) == native and envelope(case) == held and case.server_parent_path.read_bytes() == live


def test_code_artifact_and_late_collector_drift_stop_before_parent_write(asset_control_case):
    case = prepared_validity_case(asset_control_case)
    quote = quoted(case)
    intent = prepared(case, quote)
    native, held, live = state_bytes(case), envelope(case), case.server_parent_path.read_bytes()
    code = case.backend / "core/execution/unapproved_validity_audit.py"
    code.write_text("# Unapproved implementation after exact consent.\n")
    with pytest.raises(CreatorError):
        activated(case, quote, intent)
    code.unlink()
    artifact = case.control.roots["execution"] / "continuation-parent" / (quote["quote"]["proposal"]["proposal_hash"] + ".json")
    original_artifact = artifact.read_bytes()
    artifact.write_bytes(original_artifact + b" ")
    with pytest.raises(CreatorError):
        activated(case, quote, intent)
    artifact.write_bytes(original_artifact)
    collector = case.control.roots["execution"] / "media-receipts" / case.unknown["id"]
    collector.mkdir(mode=0o700)
    private_json(collector / "claim.json", {"fictional_late_evidence": True})
    with pytest.raises(CreatorError):
        activated(case, quote, intent)
    assert state_bytes(case) == native and envelope(case) == held and case.server_parent_path.read_bytes() == live


def test_an_identical_parent_copy_cannot_be_frozen_as_server_live_authority(asset_control_case):
    case = prepared_validity_case(asset_control_case)
    quote = quoted(case)
    proposal = quote["quote"]["proposal"]
    copied = case.root / "fictional-parent-copy.json"
    copied.write_bytes(case.server_parent_path.read_bytes())
    copied.chmod(0o600)
    evidence = parent.load_parent_evidence(case.control, copied)
    native, live = state_bytes(case), case.server_parent_path.read_bytes()
    with pytest.raises(CreatorError):
        parent_io.freeze_parent_evidence(case.control, proposal, evidence)
    assert parent_io.server_parent_path(case.control) == case.server_parent_path
    assert state_bytes(case) == native and case.server_parent_path.read_bytes() == live


def test_authorization_record_does_not_enable_old_runtime_or_refresh_the_old_unknown(asset_control_case, monkeypatch):
    case = prepared_validity_case(asset_control_case)
    quote = quoted(case)
    intent = prepared(case, quote)
    activation = activated(case, quote, intent)["activation"]
    assert activation["dispatch_ready"] is False and activation["runtime_contract_pending"] is True
    native, held, live = state_bytes(case), envelope(case), case.server_parent_path.read_bytes()
    expired = timestamp(case.policy["valid_until"]) + timedelta(seconds=1)

    def forbidden(*args, **kwargs):
        pytest.fail("Authorization-only continuation attempted a capture")

    monkeypatch.setattr(case.media.capture, "capture", forbidden)
    with monkeypatch.context() as patch:
        patch.setattr(image_control, "now", lambda: expired)
        patch.setattr(media_runtime, "now", lambda: expired)
        with pytest.raises(CreatorError):
            case.control.verify_authority(for_dispatch=True)
        with pytest.raises(CreatorError):
            case.media.reserve_submit(case.task["id"], case.grant["id"],
                case.manifest["frames"][-1]["shot_id"], key="audit-no-validity-dispatch")
        assert case.media.submit(case.task["id"], case.unknown["id"])["replayed"] is True
    assert state_bytes(case) == native and envelope(case) == held and case.server_parent_path.read_bytes() == live
    assert case.env.runtime.store.read_only()["media"]["operation:" + case.unknown["id"]] == case.unknown


def test_rehashed_frame_coordinates_cannot_move_a_quote_birth_checkpoint(asset_control_case):
    case = prepared_validity_case(asset_control_case)
    quote = quoted(case)["quote"]
    original_bytes = state_bytes(case)
    state = json.loads(original_bytes)
    key = "validity_continuation_quote:" + quote["id"]
    frame = next(frame for frame in state["frames"] if key in frame["delta"].get("media", {}))
    changed = frame["delta"]["media"][key]
    proposal = changed["proposal"]
    proposal["basis"]["checkpoint"]["head_revision"] += 1
    proposal["basis_hash"] = digest(proposal["basis"])
    allocation = proposal["parent_allocation"]
    allocation["request"]["continuation_basis_hash"] = proposal["basis_hash"]
    allocation["operation_key"] = "image-continuation-" + proposal["basis_hash"][:32]
    allocation["quote_ref"] = "image-continuation-basis:" + proposal["basis_hash"]
    proposal["proposal_hash"] = digest({name: value for name, value in proposal.items() if name != "proposal_hash"})
    # Rebind the complete evidence reference and record identity as an attacker
    # can. Record-level seals should pass; the actual journal birth must fail.
    artifact_path = case.control.roots["execution"] / "continuation-parent" / (quote["proposal"]["proposal_hash"] + ".json")
    artifact = json.loads(artifact_path.read_bytes())
    artifact["proposal_hash"] = proposal["proposal_hash"]
    artifact["artifact_hash"] = digest({name: value for name, value in artifact.items() if name != "artifact_hash"})
    ref = changed["parent_evidence_ref"]
    ref.update(proposal_hash=proposal["proposal_hash"], artifact_hash=artifact["artifact_hash"],
        artifact_sha256=hashlib.sha256(json.dumps(artifact, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest(),
        path_hash=digest(str(artifact_path.with_name(proposal["proposal_hash"] + ".json"))))
    ref["ref_hash"] = digest({name: value for name, value in ref.items() if name != "ref_hash"})
    common = {"schema_version", "kind", "id", "scope_id", "task_id", "created_at"}
    changed["id"] = digest({"kind": changed["kind"], "scope_id": changed["scope_id"],
        **{name: value for name, value in changed.items() if name not in common}})[:32]
    new_key = "validity_continuation_quote:" + changed["id"]
    frame["delta"]["media"] = {new_key: changed}
    payload = case.env.runtime.store.read_only()
    del payload["media"][key]
    payload["media"][new_key] = changed
    continuation.validate_record(payload, changed)
    previous = None
    for frame in state["frames"]:
        frame["previous_hash"] = previous
        frame["hash"] = digest({name: value for name, value in frame.items() if name != "hash"})
        previous = frame["hash"]
    state["head_hash"] = previous
    with pytest.raises(CreatorError):
        validate_state(state)
    assert state_bytes(case) == original_bytes
