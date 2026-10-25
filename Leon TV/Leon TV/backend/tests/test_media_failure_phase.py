"""Synthetic image failures retain only fixed private observer-stage evidence."""
import copy
import json
import stat
import subprocess
from types import SimpleNamespace

import pytest
from asset_operator_fixture import asset_control_case as asset_control_case
from image_operator_fixture import images_case
from media_ledger_fixture import authorize, media_case, posts, reserve
from test_execution_empirical import empirical_env as empirical_env
from test_image_policy import make_submit

from core.creator.store import CreatorError, digest, utc_now
from core.execution import media_failure_phase
from core.execution.image_capture import ControlledImageCapture
from core.execution.image_operator import _BoundImageCapture
from core.execution.media_failure_phase import (
    CaptureTrace,
    capture_stage,
    checked_failure_phase,
    failure_projection,
    read_failure_phase,
    record_failure_phase,
)
from core.execution.receipts import ReceiptCaptureSettings, ReceiptStore


def test_pre_capture_authority_failure_remains_unknown_with_fixed_private_phase(empirical_env):
    case = media_case(empirical_env)
    _, grant = authorize(case)
    original = reserve(case, grant)

    def rejected(candidate):
        raise RuntimeError("PRIVATE-KEY-SENTINEL https://private.invalid/script")

    case.media.capture.authority_verifier = rejected
    result = case.media.submit(case.task["id"], original["id"])["operation"]
    assert result["status"] == "unknown" and result["raw"] is result["receipt"] is None
    assert result["descriptor"] == original["descriptor"] and posts(case) == []
    diagnostic = case.media.store.root / "media-failures" / (original["id"] + ".json")
    assert diagnostic.is_file(), "A precise fixed observer stage must survive the unknown catch"
    value = json.loads(diagnostic.read_text())
    assert value["stage"] == "image_authority"
    assert value["http_outcome"] == value["fee_outcome"] == "unknown"
    assert value["retry_authorized"] is False
    assert "PRIVATE-KEY-SENTINEL" not in diagnostic.read_text()
    assert "https://" not in diagnostic.read_text()
    before = diagnostic.read_bytes()
    state_before = case.media.store.path("state.json").read_bytes()
    assert failure_projection(case.media.store.root, result)["stage"] == "image_authority"
    assert case.media.submit(case.task["id"], original["id"])["replayed"]
    assert case.media.recover(case.task["id"], original["id"])["replayed"]
    assert diagnostic.read_bytes() == before and case.media.store.path("state.json").read_bytes() == state_before
    with pytest.raises(CreatorError) as blocked:
        reserve(case, grant, "S02", "new-after-local-failure")
    assert blocked.value.code == "MEDIA_RESPONSE_UNCONFIRMED" and posts(case) == []


def fictional_record():
    request = make_submit()
    binding = request.binding()
    return {"kind": "operation", "id": binding["operation_id"], "task_id": binding["task_id"],
        "scope_id": "a" * 32, "capture_started_at": utc_now(), "descriptor": request.frozen(), "status": "submitting"}


def test_missing_read_is_zero_write_and_failure_is_private_first_write_only(tmp_path):
    record = fictional_record()
    assert read_failure_phase(tmp_path, record) is None and list(tmp_path.iterdir()) == []
    first = record_failure_phase(tmp_path, record, "collector_prepare")
    path = tmp_path / "media-failures" / (record["id"] + ".json")
    before = {str(item.relative_to(tmp_path)): item.read_bytes() for item in tmp_path.rglob("*") if item.is_file()}
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert record_failure_phase(tmp_path, record, "worker_launch") == first
    assert read_failure_phase(tmp_path, record) == first
    assert {str(item.relative_to(tmp_path)): item.read_bytes() for item in tmp_path.rglob("*") if item.is_file()} == before
    assert failure_projection(tmp_path, record) == {"status": "observed", **{key: first[key] for key in (
        "stage", "recorded_at", "observer_stage_only", "http_outcome", "fee_outcome", "retry_authorized", "evidence_hash")}}


@pytest.mark.parametrize("field,value", [
    ("schema_version", True), ("kind", "other"), ("stage", "PRIVATE-KEY-SENTINEL"),
    ("observer_stage_only", 1), ("retry_authorized", True), ("http_outcome", "not_dispatched"),
    ("fee_outcome", "0.000000"), ("native_record_kind", "query"), ("scope_id", "b" * 32),
    ("capture_started_at", "2020-01-01T00:00:00+00:00"), ("recorded_at", "2999-01-01T00:00:00+00:00"),
])
def test_even_resealed_failure_cannot_change_binding_or_truthful_classification(tmp_path, field, value):
    record = fictional_record()
    original = record_failure_phase(tmp_path, record, "image_deadline")
    changed = copy.deepcopy(original)
    changed[field] = value
    changed["evidence_hash"] = digest({key: item for key, item in changed.items() if key != "evidence_hash"})
    with pytest.raises(CreatorError):
        checked_failure_phase(changed, record)


@pytest.mark.parametrize("field", ["operation_id", "task_id", "nonce", "descriptor_hash", "wire_hash", "deadline_at", "grant_id"])
def test_full_binding_is_reconstructed_rather_than_trusting_resealed_hash(tmp_path, field):
    record = fictional_record()
    original = record_failure_phase(tmp_path, record, "image_authority")
    changed = copy.deepcopy(original)
    changed["binding"][field] = "f" * 64
    changed["binding_hash"] = digest(changed["binding"])
    changed["evidence_hash"] = digest({key: item for key, item in changed.items() if key != "evidence_hash"})
    with pytest.raises(CreatorError):
        checked_failure_phase(changed, record)


@pytest.mark.parametrize("value", [False, 0.0])
def test_equal_but_differently_typed_json_binding_is_rejected(tmp_path, value):
    record = fictional_record()
    original = record_failure_phase(tmp_path, record, "image_authority")
    assert original["binding"]["query_index"] == 0
    changed = copy.deepcopy(original)
    changed["binding"]["query_index"] = value
    changed["binding_hash"] = digest(changed["binding"])
    changed["evidence_hash"] = digest({key: item for key, item in changed.items() if key != "evidence_hash"})
    with pytest.raises(CreatorError):
        checked_failure_phase(changed, record)


def test_no_backfill_for_stopped_historical_unknown_or_reserved_operation(tmp_path):
    record = fictional_record()
    for status in ("reserved", "unknown", "accepted"):
        record["status"] = status
        with pytest.raises(CreatorError):
            record_failure_phase(tmp_path, record, "capture_entry")
    assert list(tmp_path.iterdir()) == []


def test_corrupt_symlink_hardlink_and_public_mode_are_not_adopted(tmp_path):
    record = fictional_record()
    record_failure_phase(tmp_path, record, "collector_observe")
    path = tmp_path / "media-failures" / (record["id"] + ".json")
    original = path.read_bytes()
    path.write_text("{}")
    with pytest.raises(CreatorError):
        read_failure_phase(tmp_path, record)
    path.write_bytes(original)
    path.chmod(0o644)
    with pytest.raises(CreatorError):
        read_failure_phase(tmp_path, record)
    path.chmod(0o600)
    other = tmp_path / "other.json"
    path.replace(other)
    path.symlink_to(other)
    with pytest.raises(CreatorError):
        read_failure_phase(tmp_path, record)
    path.unlink()
    path.hardlink_to(other)
    with pytest.raises(CreatorError):
        read_failure_phase(tmp_path, record)


def test_diagnostic_write_failure_still_persists_unknown_and_retains_call(empirical_env, monkeypatch):
    case = media_case(empirical_env)
    _, grant = authorize(case)
    operation = reserve(case, grant)
    case.media.capture.authority_verifier = lambda candidate: False
    def failed(*args, **kwargs):
        raise OSError("PRIVATE-KEY-SENTINEL")
    monkeypatch.setattr("core.execution.media_runtime.record_failure_phase", failed)
    result = case.media.submit(case.task["id"], operation["id"])["operation"]
    assert result["status"] == "unknown" and result["raw"] is result["receipt"] is None
    assert case.media.get(case.task["id"])["media"]["paid_call_count"] == 1
    assert case.media.submit(case.task["id"], operation["id"])["replayed"] and posts(case) == []
    assert not (case.media.store.root / "media-failures").exists()


def test_context_is_fresh_restores_nested_scope_and_is_noop_outside_native_image():
    capture_stage("arbitrary-not-a-stage")
    outer = CaptureTrace()
    inner = CaptureTrace()
    with outer.active():
        capture_stage("credential_load")
        with inner.active():
            capture_stage("collector_prepare")
        assert outer.stage == "credential_load" and inner.stage == "collector_prepare"
        with pytest.raises(CreatorError):
            capture_stage("PRIVATE-KEY-SENTINEL")
    capture_stage("collector_close")
    assert outer.stage == "credential_load" and CaptureTrace().stage == "capture_entry"
    assert media_failure_phase._CURRENT.get() is None


@pytest.mark.parametrize("point,stage", [("control_before", "control_before_key"),
    ("native_before", "native_before_key"), ("control_after", "control_after_key"),
    ("native_after", "native_after_key")])
def test_bound_operator_keeps_precise_double_guard_phase(point, stage):
    calls = {"control": 0, "native": 0}
    def guard(kind):
        calls[kind] += 1
        if point == kind + ("_before" if calls[kind] == 1 else "_after"):
            raise RuntimeError("PRIVATE-KEY-SENTINEL")
        return True
    request = make_submit()
    control = SimpleNamespace(verify_authority=lambda **kwargs: guard("control"),
        document=SimpleNamespace(policy={"validation_only": True}))
    media = SimpleNamespace(_capture_guard=lambda value: guard("native"))
    capture = SimpleNamespace(settings=ReceiptCaptureSettings(worker_factory="synthetic:factory"), receipts=None,
        capture=lambda *args: pytest.fail("rejected double guard reached collector"))
    adapter = _BoundImageCapture(control, media, capture, lambda: pytest.fail("synthetic test loaded key"))
    with CaptureTrace().active() as trace, pytest.raises(RuntimeError):
        adapter.capture(request, None)
    assert trace.stage == stage and media_failure_phase._CURRENT.get() is None


@pytest.mark.parametrize("point,stage", [("prepare", "collector_prepare"), ("launch", "worker_launch")])
def test_actual_collector_boundary_retains_fixed_stage(tmp_path, monkeypatch, point, stage):
    request = make_submit()
    settings = ReceiptCaptureSettings(worker_factory="synthetic:factory")
    adapter = ControlledImageCapture(tmp_path / "receipts", authority_verifier=lambda value: True, settings=settings)
    def failed(*args, **kwargs):
        raise OSError("PRIVATE-KEY-SENTINEL")
    if point == "prepare":
        monkeypatch.setattr(ReceiptStore, "prepare", failed)
    monkeypatch.setattr(subprocess, "Popen", failed)
    with CaptureTrace().active() as trace, pytest.raises(OSError):
        adapter.capture(request, "SYNTHETIC-KEY")
    assert trace.stage == stage and media_failure_phase._CURRENT.get() is None


def test_status_exposes_only_verified_metadata_without_runtime_key_or_dispatch(asset_control_case):
    from test_image_operator import authorize as operator_authorize
    from test_image_operator import factory, reserve, run
    case = images_case(asset_control_case)
    _, grant = operator_authorize(case)
    operation = reserve(case, grant)
    real_factory = factory(case)
    def rejecting_factory(control):
        session = real_factory(control)
        session.media.capture.capture_adapter.authority_verifier = lambda candidate: False
        return session
    result = run(case, "start", "--operation", operation["id"], session_factory=rejecting_factory)
    assert result["operation"]["status"] == "unknown"
    store = case.env.runtime.store
    before = {str(path.relative_to(store.root)): path.read_bytes() for path in store.root.rglob("*") if path.is_file()}
    status = run(case, "status", "--operation", operation["id"],
        session_factory=lambda *args: pytest.fail("status opened runtime or credential loader"))
    diagnostic = status["operation"]["failure_diagnostic"]
    assert diagnostic["status"] == "observed" and diagnostic["stage"] == "image_authority"
    assert diagnostic["observer_stage_only"] and diagnostic["retry_authorized"] is False
    assert diagnostic["http_outcome"] == diagnostic["fee_outcome"] == "unknown"
    assert status["blocked_reason"] == "RESPONSE_UNCONFIRMED" and status["automatic_followup_calls"] == 0
    assert {str(path.relative_to(store.root)): path.read_bytes() for path in store.root.rglob("*") if path.is_file()} == before
    assert all(part not in json.dumps(diagnostic) for part in ("binding", "descriptor", "wire", "https://", "prompt", "api_key"))


def test_success_creates_no_failure_sidecar(empirical_env):
    case = media_case(empirical_env)
    _, grant = authorize(case)
    operation = reserve(case, grant)
    result = case.media.submit(case.task["id"], operation["id"])["operation"]
    assert result["status"] == "accepted" and len(posts(case)) == 1
    assert not (case.media.store.root / "media-failures").exists()
    assert failure_projection(case.media.store.root, result) == {"status": "missing"}
