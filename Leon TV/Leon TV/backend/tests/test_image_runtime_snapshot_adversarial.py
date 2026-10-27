"""Independent attacks on same-decision v2 snapshots and fresh boundaries."""
import copy
import socket
from datetime import timedelta
from types import SimpleNamespace

import httpx
import pytest
from asset_operator_fixture import asset_control_case as asset_control_case
from image_operator_fixture import posts
from test_image_runtime_continuation import prepared_runtime_case

from core.creator.store import CreatorError, digest, utc_now
from core.execution import image_capture, image_operator
from core.execution import image_runtime_continuation as continuation
from core.execution.image_runtime_control import RuntimeContinuedImageControl
from core.execution.media_ledger import records
from core.execution.media_runtime import MediaExecution
from core.execution.policy import now


@pytest.fixture(autouse=True)
def no_external_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Runtime snapshot audit attempted external network")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", forbidden)


def test_replaced_control_cannot_supply_the_final_collector_guard_verdict():
    media = MediaExecution.__new__(MediaExecution)
    media.continuation_control = SimpleNamespace(guard_capture=lambda *_args: True)
    # A positive answer from an arbitrary object cannot replace the exact
    # bound control's fresh read at the last independent collector boundary.
    assert media._capture_guard({}) is False


@pytest.mark.parametrize("attack", ["grant_after_key", "funding_before_collector"])
def test_later_boundary_cannot_reuse_an_earlier_current_snapshot(asset_control_case, monkeypatch, attack):
    case = prepared_runtime_case(asset_control_case)
    store = case.env.runtime.store
    control = RuntimeContinuedImageControl(case.control, case.runtime_activation["id"])
    parent_before, captured_before = case.server_parent_path.read_bytes(), len(posts(case))
    stages, checks = [], []
    original_verified = case.control.__class__._verified
    operator_stage, collector_stage = image_operator.capture_stage, image_capture.capture_stage

    def checked(self, *args, **kwargs):
        checks.append((stages[-1] if stages else None, kwargs.get("for_dispatch")))
        return original_verified(self, *args, **kwargs)

    def stage(name):
        operator_stage(name)
        stages.append(name)
        if attack == "grant_after_key" and name == "credential_check":
            with store.transaction() as payload:
                payload["media"]["grant:" + case.grant["id"]]["revoked_at"] = utc_now()
        elif attack == "funding_before_collector" and name == "deadline_after_key":
            # Semantically identical parent JSON is not the frozen raw-file
            # evidence. This happens after both Key-boundary checks succeeded.
            case.server_parent_path.write_bytes(parent_before + b" ")

    def capture_stage(name):
        collector_stage(name)
        stages.append(name)

    def no_real_key():
        pytest.fail("Synthetic continuation requested a real credential")

    try:
        with image_operator.build_image_session(control, receipt_capture=case.settings,
                credential_loader=no_real_key) as session:
            target = case.runtime_authority["remaining_jobs"][0]
            reserved = session.media.reserve_submit(case.task["id"], case.grant["id"], target["shot_id"],
                key="snapshot-boundary-" + attack)["operation"]
            monkeypatch.setattr(case.control.__class__, "_verified", checked)
            monkeypatch.setattr(image_operator, "capture_stage", stage)
            monkeypatch.setattr(image_capture, "capture_stage", capture_stage)
            result = session.media.submit(case.task["id"], reserved["id"])["operation"]
            assert result["status"] == "unknown" and result["raw"] is None and result["receipt"] is None
            assert result["capture_started_at"] is not None
            assert result["descriptor"] == reserved["descriptor"]
            assert not session.media.capture.receipts.operation(result["id"]).root.exists()
            assert any(stage_name == "control_before_key" for stage_name, _ in checks)
            assert any(stage_name == "control_after_key" for stage_name, _ in checks)
            assert all(for_dispatch is False for _, for_dispatch in checks)
            if attack == "grant_after_key":
                assert "deadline_after_key" not in stages and "image_authority" not in stages
            else:
                assert "deadline_after_key" in stages and "image_authority" in stages
                assert any(stage_name == "image_authority" for stage_name, _ in checks)
                assert "image_credential" not in stages
    finally:
        case.server_parent_path.write_bytes(parent_before)

    payload = store.read_only()
    assert payload["media"]["operation:" + case.unknown["id"]] == case.unknown
    assert len(records(payload, "operation")) == 3
    assert len(posts(case)) == captured_before and case.server_parent_path.read_bytes() == parent_before


def test_fresh_verifier_rejects_stale_or_forged_payload_and_rechecks_external_proofs(asset_control_case, monkeypatch):
    case = prepared_runtime_case(asset_control_case)
    store = case.env.runtime.store
    control = RuntimeContinuedImageControl(case.control, case.runtime_activation["id"])
    captured_before, parent_before = len(posts(case)), case.server_parent_path.read_bytes()
    actual, authority = control.verified_current()
    assert authority == case.runtime_authority and digest(actual) == digest(store.read_only())

    forged = copy.deepcopy(actual)
    forged["media"]["operation:" + case.unknown["id"]]["raw"] = {"fictional": "cleared"}
    with pytest.raises(CreatorError):
        continuation.verify_current(case.control, forged, case.runtime_activation["id"])
    assert digest(control.verified_current()[0]) == digest(actual)

    # An independently valid, later head does not permit a caller's old head
    # to act as the current transaction snapshot.
    case.media.quote(case.task["id"], key="snapshot-audit-intervening-native-frame")
    with pytest.raises(CreatorError):
        continuation.verify_current(case.control, actual, case.runtime_activation["id"])
    current, _ = control.verified_current()
    assert digest(current) != digest(actual)
    state_before_checks = store.path("state.json").read_bytes()

    with image_operator.build_image_session(control, receipt_capture=case.settings) as session:
        media = session.media
        scope = current["media"]["scope:" + authority["scope_id"]]
        assert media._authority(scope, payload=current) == authority
        for invalid_snapshot in (actual, forged):
            with pytest.raises(CreatorError):
                media._authority(scope, payload=invalid_snapshot)
        settings = {"policy": media.policy, "budget_anchor": media.anchor, "manifest": media.manifest,
            "authority_guard": media.authority_guard, "fee_source": media.fee_source,
            "receipt_capture": case.settings, "reference_profile": media.reference_profile,
            "manifest_origin": media.manifest_origin, "continuation_activation_id": control.runtime_activation_id,
            "continuation_guard": control.guard_runtime, "continuation_control": control}
        for changed in ({"continuation_control": object()},
                {"continuation_activation_id": "f" * 32},
                {"continuation_guard": lambda *_args: False}):
            with pytest.raises(CreatorError):
                MediaExecution(session.runtime, **{**settings, **changed})
        foreign_runtime = SimpleNamespace(store=SimpleNamespace(root=store.root / "foreign"))
        with pytest.raises(CreatorError):
            MediaExecution(foreign_runtime, **settings)
        equivalent_control = RuntimeContinuedImageControl(case.control, control.runtime_activation_id)
        with pytest.raises(CreatorError):
            equivalent_control.guard_capture(media, case.first["descriptor"])
    assert store.path("state.json").read_bytes() == state_before_checks

    code = case.backend / next(iter(case.control.document.code_manifest))
    creator = case.control.roots["creator"] / "state.json"
    for path, corrupted in ((code, b"# Unapproved later source.\n"),
            (creator, b"{}"), (case.server_parent_path, parent_before + b" "),
            (case.path, case.path.read_bytes() + b" ")):
        original = path.read_bytes()
        try:
            path.write_bytes(corrupted)
            with pytest.raises(CreatorError):
                control.verified_current()
        finally:
            path.write_bytes(original)
        assert digest(control.verified_current()[0]) == digest(current)
    with monkeypatch.context() as patch:
        patch.setattr(continuation, "now", lambda: now() + timedelta(hours=5))
        with pytest.raises(CreatorError):
            control.verified_current()
    assert store.path("state.json").read_bytes() == state_before_checks
    assert case.server_parent_path.read_bytes() == parent_before and len(posts(case)) == captured_before
