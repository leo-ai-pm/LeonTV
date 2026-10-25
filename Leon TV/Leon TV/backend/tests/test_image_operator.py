"""Explicit image CLI over fictional native asset receipts and detached mock HTTP."""
import copy
import json
import os
import subprocess
import sys
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from asset_operator_fixture import asset_control_case as asset_control_case
from image_operator_fixture import images_case
from test_image_fees import export, row_for
from test_image_receipts import image_bytes

from core.creator.store import CreatorError, digest
from core.execution import image_control, image_operator
from core.execution.image_control import read_image_control
from core.execution.image_policy import FrozenImageRequest
from core.execution.policy import now
from core.execution.receipts import ReceiptCaptureSettings


def loaded(case):
    return read_image_control(case.path, case.backend)


def factory(case, *, loader=None):
    return lambda control: image_operator.build_image_session(control, receipt_capture=case.settings,
        credential_loader=loader or (lambda: pytest.fail("synthetic or local command loaded a live credential")))


def run(case, command, *args, session_factory=None):
    parsed = image_operator.parser().parse_args([command, *args])
    return image_operator.execute(loaded(case), command, parsed, session_factory=session_factory or factory(case))


def authorize(case):
    quote = run(case, "quote", "--key", "image-operator-quote-001")["quote"]
    grant = run(case, "grant", "--quote", quote["id"], "--disclosure-hash", quote["disclosure_hash"],
        "--consent", "--key", "image-operator-grant-001")["grant"]
    return quote, grant


def reserve(case, grant, shot="S01"):
    return run(case, "reserve", "--grant", grant["id"], "--shot", shot, "--key", "image-reserve-" + shot)["operation"]


def native(case, kind, record_id):
    return case.env.runtime.store.read_only()["media"][kind + ":" + record_id]


def posts(case):
    return [json.loads(line) for line in case.counter.read_text().splitlines()] if case.counter.exists() else []


def fee_export(case, operation_id, *, quota=20000, request_id=None):
    op = native(case, "operation", operation_id)
    row = row_for(FrozenImageRequest.from_frozen(op["descriptor"]), op["raw"],
        request_id=request_id or op["receipt"]["submit_request_id"], quota=quota)
    path = case.path.parent / (operation_id + "-export.json")
    path.write_bytes(export(row))
    path.chmod(0o600)
    return path


def delivery_file(case, name="downloaded-image.png", blob=None):
    path = case.path.parent / name
    path.write_bytes(image_bytes() if blob is None else blob)
    path.chmod(0o600)
    return path


def test_default_off_never_reads_implicit_control_or_builds_session(monkeypatch, capsys):
    monkeypatch.setenv("LEONTV_EXECUTION_CONTROL_FILE", "/never/read/text-control.json")
    monkeypatch.setattr(image_operator, "read_image_control", lambda *args: pytest.fail("implicit image control"))
    assert image_operator.main(["status"]) == 0
    assert json.loads(capsys.readouterr().out) == {"mode": "off", "dispatch_available": False, "automatic_followup_calls": 0}
    assert image_operator.main(["quote", "--key", "image-off-quote"]) == 1
    assert json.loads(capsys.readouterr().out)["error"] == "IMAGE_CONTROL_UNAVAILABLE"


def test_import_has_no_config_models_or_image_dispatch_side_effect():
    result = subprocess.run([sys.executable, "-c", "import sys; import core.execution.image_operator; assert 'config' not in sys.modules; assert 'models' not in sys.modules"],
        cwd=Path(__file__).resolve().parents[1], env=os.environ.copy(), capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr


def test_observe_only_status_is_read_only_and_factory_keeps_text_disabled(asset_control_case, monkeypatch):
    case = images_case(asset_control_case, mode="observe_only")
    before = case.env.runtime.store.path("state.json").read_bytes()
    monkeypatch.setattr(image_operator, "tudou_credential", lambda: pytest.fail("status loaded key"))
    status = run(case, "status", session_factory=lambda *args: pytest.fail("status opened runtime"))
    assert status["mode"] == "observe_only" and not status["dispatch_available"]
    assert not status["control_authority_valid"] and not posts(case)
    with factory(case)(loaded(case)) as session:
        assert session.runtime.enabled is False and session.runtime.policy is None and session.runtime.api_key is None
        assert session.media.manifest == case.manifest and session.media.manifest_origin == case.origin
    assert case.env.runtime.store.path("state.json").read_bytes() == before


def test_actual_cli_explicit_images_fee_query_delivery_and_finished_scope(asset_control_case, monkeypatch, capsys):
    case = images_case(asset_control_case, profile=False)
    original = copy.deepcopy(case.env.runtime.store.read_only())
    monkeypatch.setattr(image_operator, "BACKEND", case.backend)
    original_execute = image_operator.execute
    monkeypatch.setattr(image_operator, "execute", lambda control, command, args: original_execute(control, command, args, session_factory=factory(case)))

    def cli(command, *args):
        assert image_operator.main([command, "--control", str(case.path), *args]) == 0
        output = capsys.readouterr().out
        for private in (str(case.path.parent), "PRIVATE-REQUIREMENTS", "reasoning_content", "body_base64", "bytes_base64",
                        "SYNTHETIC-IMAGE-OPERATOR-KEY", '"manifest":', '"prompt":', "https://"):
            assert private not in output
        return json.loads(output)

    quote = cli("quote", "--key", "actual-image-quote")
    assert quote["manifest_hash"] == case.manifest["manifest_hash"] and quote["reference_profile_hash"]
    grant = cli("grant", "--quote", quote["quote"]["id"], "--disclosure-hash", quote["quote"]["disclosure_hash"],
        "--consent", "--key", "actual-image-grant")["grant"]
    assert posts(case) == []
    for index, frame in enumerate(case.manifest["frames"]):
        op = cli("reserve", "--grant", grant["id"], "--shot", frame["shot_id"], "--key", "actual-reserve-" + str(index))["operation"]
        assert len(posts(case)) == index * 2
        op_id = op["id"]
        assert cli("start", "--operation", op_id)["operation"]["status"] == "accepted"
        status = cli("status", "--operation", op_id)
        assert status["control_authority_valid"] and not status["dispatch_available"]
        assert status["blocked_reason"] == "PLATFORM_FEE_REQUIRED" and status["query_available"]
        assert cli("start", "--operation", op_id)["replayed"]
        path = fee_export(case, op_id)
        cli("fees-import", "--operation", op_id, str(path))
        assert cli("fees-import", "--operation", op_id, str(path))["replayed"]
        query = cli("query", "--operation", op_id, "--key", "actual-query-" + str(index))["query"]
        assert query["status"] == "completed"
        assert cli("query", "--operation", op_id, "--key", "actual-query-" + str(index))["replayed"]
        assert cli("recover", "--operation", query["id"])["replayed"]
        image = delivery_file(case)
        imported = cli("delivery-import", "--operation", op_id, "--query", query["id"], str(image))
        assert imported["visual_quality"] == "not_reviewed"
        assert cli("delivery-import", "--operation", op_id, "--query", query["id"], str(image))["replayed"]
        assert len(posts(case)) == (index + 1) * 2
    status = cli("status")
    assert status["control_authority_valid"] and status["blocked_reason"] == "SCOPE_FINISHED"
    assert not status["dispatch_available"] and status["media"]["risk_released"] is False
    current = case.env.runtime.store.read_only()
    assert all(current[name] == value for name, value in original.items())


def test_unknown_retains_intent_and_single_post_without_automatic_queries(asset_control_case):
    case = images_case(asset_control_case, fault="unknown")
    _, grant = authorize(case)
    op = reserve(case, grant)
    assert run(case, "start", "--operation", op["id"])["operation"]["status"] == "unknown"
    status = run(case, "status", "--operation", op["id"])
    assert status["blocked_reason"] == "RESPONSE_UNCONFIRMED" and not status["query_available"]
    assert run(case, "start", "--operation", op["id"])["replayed"]
    run(case, "recover", "--operation", op["id"])
    with pytest.raises(CreatorError):
        run(case, "reserve", "--grant", grant["id"], "--shot", "S02", "--key", "unknown-other-shot")
    assert len(posts(case)) == 1 and posts(case)[0]["method"] == "POST"


def test_overrun_observation_blocks_generation_but_original_read_permit_stays_separate(asset_control_case):
    case = images_case(asset_control_case)
    _, grant = authorize(case)
    op = reserve(case, grant)
    run(case, "start", "--operation", op["id"])
    path = fee_export(case, op["id"], quota=1250000)
    run(case, "fees-import", "--operation", op["id"], str(path))
    status = run(case, "status", "--operation", op["id"])
    assert status["control_authority_valid"] and not status["dispatch_available"]
    assert status["blocked_reason"] == "EMPIRICAL_RISK_OVERRUN" and status["query_available"]
    assert status["media"]["extra_exposure_cny"] == "1.500000"
    assert len(posts(case)) == 1


def test_fee_import_replay_keeps_observed_at_and_single_claim(asset_control_case, monkeypatch):
    case = images_case(asset_control_case)
    _, grant = authorize(case)
    op = reserve(case, grant)
    run(case, "start", "--operation", op["id"])
    query = run(case, "query", "--operation", op["id"], "--key", "fee-request-id-separation")["query"]
    wrong = fee_export(case, op["id"], request_id="image-" + query["id"])
    before_wrong = case.env.runtime.store.path("state.json").read_bytes()
    with pytest.raises(CreatorError) as error:
        run(case, "fees-import", "--operation", op["id"], str(wrong))
    assert error.value.code == "FEE_EXPORT_PENDING"
    assert case.env.runtime.store.path("state.json").read_bytes() == before_wrong
    path = fee_export(case, op["id"])
    run(case, "fees-import", "--operation", op["id"], str(path))
    before = case.env.runtime.store.path("state.json").read_bytes()
    monkeypatch.setattr(image_operator, "utc_now", lambda: pytest.fail("replay changed observed_at"))
    assert run(case, "fees-import", "--operation", op["id"], str(path))["replayed"]
    assert case.env.runtime.store.path("state.json").read_bytes() == before
    media = case.env.runtime.store.read_only()["media"]
    assert sum(record["kind"] == "fee" for record in media.values()) == 1
    assert sum(record["kind"] == "claim" and record.get("owner") == "media" for record in media.values()) == 1


def test_competing_exact_export_observation_replays_first_durable_time(asset_control_case):
    case = images_case(asset_control_case)
    _, grant = authorize(case)
    op = reserve(case, grant)
    run(case, "start", "--operation", op["id"])
    path = fee_export(case, op["id"])
    first = {"value": True}

    def competing_factory(control):
        session = factory(case)(control)
        observe = session.media.observe_fee

        def race(task_id, operation_id, blob, *, observed_at):
            if first["value"]:
                first["value"] = False
                stable = native(case, "operation", operation_id)["raw"]["received_at"]
                assert stable != observed_at
                observe(task_id, operation_id, blob, observed_at=stable)
            return observe(task_id, operation_id, blob, observed_at=observed_at)
        session.media.observe_fee = race
        return session

    assert run(case, "fees-import", "--operation", op["id"], str(path), session_factory=competing_factory)["replayed"]
    media = case.env.runtime.store.read_only()["media"]
    assert sum(record["kind"] == "fee" for record in media.values()) == 1
    assert sum(record["kind"] == "claim" and record.get("owner") == "media" for record in media.values()) == 1


def test_observe_only_rejects_every_write_before_runtime_or_file(asset_control_case):
    cases = [("quote", ["--key", "observe-image-quote"]),
    ("grant", ["--quote", "0" * 32, "--disclosure-hash", "0" * 64, "--consent", "--key", "observe-image-grant"]),
    ("reserve", ["--grant", "0" * 32, "--shot", "S01", "--key", "observe-image-reserve"]),
    ("start", ["--operation", "0" * 32]), ("query", ["--operation", "0" * 32, "--key", "observe-image-query"]),
    ("recover", ["--operation", "0" * 32]), ("fees-import", ["--operation", "0" * 32, "/not/read"]),
    ("legacy-fees-import", ["--operation", "0" * 32, "/not/read"]),
    ("delivery-import", ["--operation", "0" * 32, "--query", "0" * 32, "/not/read"]),
    ("revoke", ["--grant", "0" * 32])]
    case = images_case(asset_control_case, mode="observe_only")
    before = case.env.runtime.store.path("state.json").read_bytes()
    for command, args in cases:
        with pytest.raises(CreatorError) as error:
            run(case, command, *args, session_factory=lambda *args: pytest.fail("observe-only write runtime"))
        assert error.value.code == "IMAGE_CONTROL_UNAVAILABLE"
        assert case.env.runtime.store.path("state.json").read_bytes() == before and not posts(case)


@pytest.mark.parametrize("problem", ["code_drift", "expiry"])
def test_unavailable_dispatch_keeps_local_fee_recover_delivery_and_revoke(asset_control_case, monkeypatch, problem):
    case = images_case(asset_control_case)
    _, grant = authorize(case)
    op = reserve(case, grant)
    run(case, "start", "--operation", op["id"])
    query = run(case, "query", "--operation", op["id"], "--key", "image-before-drift-query")["query"]
    path = fee_export(case, op["id"])
    if problem == "code_drift":
        (case.backend / "core/execution/image_operator.py").write_text("# later independent public code\n")
    else:
        future = now() + timedelta(hours=2)
        monkeypatch.setattr(image_control, "now", lambda: future)
    assert run(case, "status")["control_authority_valid"] is False
    run(case, "recover", "--operation", query["id"])
    run(case, "fees-import", "--operation", op["id"], str(path))
    image = delivery_file(case)
    run(case, "delivery-import", "--operation", op["id"], "--query", query["id"], str(image))
    run(case, "revoke", "--grant", grant["id"])
    with pytest.raises(CreatorError):
        run(case, "query", "--operation", op["id"], "--key", "image-after-drift-query")
    assert len(posts(case)) == 2


def test_delivery_import_is_private_bounded_actual_bytes_and_never_downloads(asset_control_case):
    case = images_case(asset_control_case)
    _, grant = authorize(case)
    op = reserve(case, grant)
    run(case, "start", "--operation", op["id"])
    query = run(case, "query", "--operation", op["id"], "--key", "delivery-validation-query")["query"]
    before = case.env.runtime.store.path("state.json").read_bytes()
    for problem in ("bad_image", "oversize", "public_mode", "symlink", "url"):
        path = delivery_file(case, blob=b"bad" if problem == "bad_image" else b"x" * (2 * 1048576 + 1) if problem == "oversize" else None)
        if problem == "public_mode":
            path.chmod(0o644)
        elif problem == "symlink":
            link = path.parent / "symlink-image.png"
            link.symlink_to(path)
            path = link
        elif problem == "url":
            path = "https://synthetic.invalid/forbidden-image.png"
        with pytest.raises((CreatorError, OSError)):
            run(case, "delivery-import", "--operation", op["id"], "--query", query["id"], str(path))
        assert case.env.runtime.store.path("state.json").read_bytes() == before and len(posts(case)) == 2


def test_legacy_bridge_uses_same_parent_account_and_preserves_admission(asset_control_case):
    case = images_case(asset_control_case, legacy=True)
    before = copy.deepcopy(case.env.runtime.store.read_only())
    for operation_id, blob in case.text_exports.items():
        path = case.path.parent / (operation_id + "-legacy.json")
        path.write_bytes(blob)
        path.chmod(0o600)
        assert run(case, "legacy-fees-import", "--operation", operation_id, str(path))["replayed"]
    assert digest(case.env.runtime.store.read_only()) == digest(before)
    with pytest.raises(CreatorError):
        run(case, "quote", "--task", "f" * 32, "--key", "wrong-image-task", session_factory=lambda *args: pytest.fail("foreign task"))
    assert not posts(case)


def test_revoked_image_grant_is_reported_without_releasing_reserved_scope(asset_control_case):
    case = images_case(asset_control_case)
    _, grant = authorize(case)
    run(case, "revoke", "--grant", grant["id"])
    status = run(case, "status")
    assert status["control_authority_valid"] is True and status["dispatch_available"] is False
    assert status["blocked_reason"] == "MEDIA_GRANT_REVOKED"
    assert status["media"]["risk_released"] is False and not posts(case)


@pytest.mark.parametrize("arguments", [["reserve", "--grant", "x", "--shot", "S01", "--key", "k", "--references", "{}"],
    ["quote", "--key", "k", "--manifest", "{}"], ["start", "--operation", "x", "--api-key", "DO-NOT-ECHO"],
    ["query", "--operation", "x", "--key", "k", "--url", "https://invalid.example"],
    ["grant", "--quote", "x", "--disclosure-hash", "y", "--key", "k"]])
def test_parser_rejects_arbitrary_inputs_without_echo(arguments, capsys):
    assert image_operator.main(arguments) == 1
    output = capsys.readouterr()
    assert json.loads(output.out)["error"] == "IMAGE_CONTROL_UNAVAILABLE"
    assert "DO-NOT-ECHO" not in output.out and not output.err


def test_bound_capture_rechecks_guard_after_loading_and_never_uses_runtime_key():
    calls, active = [], {"value": True}
    control = SimpleNamespace(document=SimpleNamespace(policy={"validation_only": False}),
        verify_authority=lambda **kwargs: calls.append("authority"))
    media = SimpleNamespace(_capture_guard=lambda frozen: active["value"])
    transport = SimpleNamespace(settings=ReceiptCaptureSettings(), receipts=object(),
        capture=lambda request, key: calls.append(("transport", key)))
    request = SimpleNamespace(validation_only=False, deadline_at=(now() + timedelta(minutes=1)).isoformat(), frozen=lambda: {"durable": "synthetic proof"})

    def loader():
        calls.append("loader")
        active["value"] = False
        return "SYNTHETIC-PRIVATE-KEY"
    adapter = image_operator._BoundImageCapture(control, media, transport, loader)
    with pytest.raises(CreatorError):
        adapter.capture(request, "UNAUTHORIZED-IN-MEMORY-RUNTIME-KEY")
    assert calls == ["authority", "loader", "authority"]
    active["value"] = True
    adapter.loader = lambda: "SYNTHETIC-NEW-KEY"
    adapter.capture(request, "UNAUTHORIZED-IN-MEMORY-RUNTIME-KEY")
    assert calls[-1] == ("transport", "SYNTHETIC-NEW-KEY")


def test_live_capture_rejects_synthetic_transport_before_loading():
    called = []
    control = SimpleNamespace(document=SimpleNamespace(policy={"validation_only": False}), verify_authority=lambda **kwargs: True)
    media = SimpleNamespace(_capture_guard=lambda frozen: True)
    transport = SimpleNamespace(settings=ReceiptCaptureSettings(worker_factory="synthetic:transport"), receipts=object())
    request = SimpleNamespace(validation_only=False, deadline_at=(now() + timedelta(minutes=1)).isoformat(), frozen=lambda: {})
    adapter = image_operator._BoundImageCapture(control, media, transport, lambda: called.append("loader"))
    with pytest.raises(CreatorError):
        adapter.capture(request, "RUNTIME-KEY")
    assert not called
