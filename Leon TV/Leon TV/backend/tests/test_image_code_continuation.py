"""Code-only approval over the original native scope, grant and deadline."""
import copy
import json
import os
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

import pytest
from asset_operator_fixture import asset_control_case as asset_control_case
from image_operator_fixture import authorize, images_case, posts

from core.creator.store import CreatorError, digest
from core.execution import image_code_continuation as continuation
from core.execution.image_control import read_image_control
from core.execution.policy import now


def prepared(asset_control_case):
    case = images_case(asset_control_case, profile=False)
    _, case.grant = authorize(case)
    case.control = read_image_control(case.path, case.backend)
    case.changed_code = case.backend / "core/execution/new_approved_code.py"
    case.changed_code.write_text("# Explicit new public implementation.\n")
    case.state_path = case.env.runtime.store.path("state.json")
    return case


def quote(case):
    return continuation.quote_code_continuation(case.control, case.grant["id"])


def activate(case, quoted=None):
    q = (quoted or quote(case))["quote"]
    result = continuation.activate_code_continuation(case.control, q["id"], disclosure_hash=q["disclosure_hash"], consent=True)
    return result, continuation.ContinuedImageControl(case.control, result["activation"]["id"])


def denied(call):
    with pytest.raises(CreatorError) as error:
        call()
    assert error.value.code in {"IMAGE_CODE_CONTINUATION_UNAVAILABLE", "IMAGE_CONTROL_UNAVAILABLE", "CORRUPT_STORAGE"}


def test_server_disclosure_is_exact_code_only_and_replays_without_writes(asset_control_case):
    case = prepared(asset_control_case)
    original = copy.deepcopy(case.env.runtime.store.read_only())
    original_control = case.path.read_bytes()
    denied(lambda: case.control.verify_authority(for_dispatch=True))
    first = quote(case)
    q, d = first["quote"], first["quote"]["disclosure"]
    assert not first["replayed"] and d["new_paid_calls"] == 0 and d["new_risk_cny"] == "0.000000"
    assert d["control_hash"] == case.control.control_hash and d["grant_id"] == case.grant["id"]
    assert d["old_code_manifest"] == case.control.document.code_manifest
    assert d["new_code_manifest"] == continuation.current_code_manifest(case.control)
    assert d["code_diff"]["added"] == ["core/execution/new_approved_code.py"]
    assert not d["code_diff"]["changed"] and not d["code_diff"]["removed"]
    assert d["original_valid_until"] == case.policy["valid_until"]
    assert d["remaining_limits"]["used_submissions"] == 0
    assert d["remaining_limits"]["remaining_submissions"] == case.policy["max_submissions"]
    before = case.state_path.read_bytes()
    assert quote(case)["replayed"] and case.state_path.read_bytes() == before
    result, wrapped = activate(case, first)
    assert not result["replayed"] and wrapped.verify_authority(for_dispatch=True) is True
    before = case.state_path.read_bytes()
    assert activate(case, first)[0]["replayed"] and case.state_path.read_bytes() == before
    assert quote(case)["replayed"] and case.state_path.read_bytes() == before
    payload = case.env.runtime.store.read_only()
    assert all(payload[name] == value for name, value in original.items() if name != "media")
    assert all(payload["media"][key] == value for key, value in original["media"].items())
    assert len(payload["media"]) == len(original["media"]) + 2
    assert case.path.read_bytes() == original_control and posts(case) == []
    assert q["disclosure_hash"] == digest(d)


@pytest.mark.parametrize("consent", [False, 1, "true", None])
def test_exact_explicit_consent_required(asset_control_case, consent):
    case = prepared(asset_control_case)
    q = quote(case)["quote"]
    before = case.state_path.read_bytes()
    denied(lambda: continuation.activate_code_continuation(case.control, q["id"], disclosure_hash=q["disclosure_hash"], consent=consent))
    assert case.state_path.read_bytes() == before and not posts(case)


@pytest.mark.parametrize("attack", ["wrong_hash", "changed_code", "unrelated_new_frame", "revoked", "expired"])
def test_stale_or_different_activation_is_rejected_without_mutation(asset_control_case, monkeypatch, attack):
    case = prepared(asset_control_case)
    q = quote(case)["quote"]
    wanted = q["disclosure_hash"]
    if attack == "wrong_hash":
        wanted = "0" * 64
    elif attack == "changed_code":
        case.changed_code.write_text("# Changed after quote.\n")
    elif attack == "unrelated_new_frame":
        approved_bytes = case.changed_code.read_bytes()
        case.changed_code.unlink()
        case.media.quote(case.task["id"], key="intervening-code-approval-frame")
        case.changed_code.write_bytes(approved_bytes)
    elif attack == "revoked":
        case.media.revoke(case.task["id"], case.grant["id"])
    else:
        monkeypatch.setattr(continuation, "now", lambda: now() + timedelta(days=100))
    before = case.state_path.read_bytes()
    denied(lambda: continuation.activate_code_continuation(case.control, q["id"], disclosure_hash=wanted, consent=True))
    assert case.state_path.read_bytes() == before and not posts(case)


@pytest.mark.parametrize("attack", ["manifest_change", "new_file", "removed_file", "revoked", "expired", "control_drift", "parent_drift"])
def test_wrapper_rechecks_actual_code_authority_grant_and_deadline(asset_control_case, monkeypatch, attack):
    case = prepared(asset_control_case)
    _, wrapped = activate(case)
    assert wrapped.verify_authority(for_dispatch=True)
    scope = next(r for r in case.env.runtime.store.read_only()["media"].values() if r["kind"] == "scope")
    assert wrapped.guard(scope)
    if attack == "manifest_change":
        case.changed_code.write_text("# More unapproved code.\n")
    elif attack == "new_file":
        (case.backend / "core/execution/not_approved.py").write_text("# Omitted module.\n")
    elif attack == "removed_file":
        case.changed_code.unlink()
    elif attack == "revoked":
        case.media.revoke(case.task["id"], case.grant["id"])
    elif attack == "expired":
        monkeypatch.setattr(continuation, "now", lambda: now() + timedelta(days=100))
    elif attack == "control_drift":
        case.path.write_bytes(case.path.read_bytes() + b" ")
    else:
        path = Path(case.control.document.authority.parent_ledger_path)
        path.write_bytes(path.read_bytes() + b" ")
    before = case.state_path.read_bytes()
    denied(lambda: wrapped.verify_authority(for_dispatch=True))
    assert wrapped.guard(scope) is False and case.state_path.read_bytes() == before
    if attack not in {"control_drift", "parent_drift"}:
        assert wrapped.verify_authority(for_dispatch=False)
    assert not posts(case)


def test_scope_guard_is_exact_and_active_approval_cannot_be_downgraded(asset_control_case):
    case = prepared(asset_control_case)
    first, old = activate(case)
    scope = next(r for r in case.env.runtime.store.read_only()["media"].values() if r["kind"] == "scope")
    for field, value in (("id", "0" * 32), ("account_hash", "0" * 64), ("manifest", {}), ("policy", {}), ("schema_version", True)):
        changed = copy.deepcopy(scope)
        changed[field] = value
        assert old.guard(changed) is False
    case.changed_code.write_text("# Second explicitly approved implementation.\n")
    denied(lambda: old.verify_authority(for_dispatch=True))
    second_q = quote(case)
    second, current = activate(case, second_q)
    assert second["activation"]["activation_sequence"] == 2
    assert second["activation"]["previous_activation_id"] == first["activation"]["id"]
    assert current.verify_authority(for_dispatch=True)
    denied(lambda: old.verify_authority(for_dispatch=True))
    old_q = case.env.runtime.store.read_only()["media"][continuation.QUOTE + ":" + first["activation"]["quote_id"]]
    denied(lambda: continuation.activate_code_continuation(case.control, old_q["id"], disclosure_hash=old_q["disclosure_hash"], consent=True))
    assert not posts(case)


@pytest.mark.parametrize("command", ["quote", "grant"])
def test_active_continuation_cannot_create_new_original_quote_or_grant(asset_control_case, command):
    case = prepared(asset_control_case)
    _, wrapped = activate(case)
    from core.execution.image_operator import build_image_session
    with build_image_session(wrapped, receipt_capture=case.settings) as session:
        before = case.state_path.read_bytes()
        if command == "quote":
            denied(lambda: session.media.quote(case.task["id"], key="continuation-no-new-quote"))
        else:
            original_quote = case.env.runtime.store.read_only()["media"]["quote:" + case.grant["quote_id"]]
            denied(lambda: session.media.grant(case.task["id"], original_quote["id"], disclosure_hash=original_quote["disclosure_hash"],
                consent=True, key="continuation-no-new-grant"))
        assert case.state_path.read_bytes() == before
    assert not posts(case)


def test_import_and_default_cli_have_no_implicit_configuration_or_authority(monkeypatch, capsys):
    monkeypatch.setenv("LEONTV_EXECUTION_CONTROL_FILE", "/never/read")
    monkeypatch.setattr(continuation, "read_image_control", lambda *a, **k: pytest.fail("implicit control"))
    assert continuation.main(["status"]) == 0
    assert json.loads(capsys.readouterr().out)["mode"] == "off"
    assert continuation.main(["quote", "--grant", "a" * 32]) == 1
    assert json.loads(capsys.readouterr().out)["error"] == "IMAGE_CODE_CONTINUATION_UNAVAILABLE"
    result = subprocess.run([sys.executable, "-c", "import sys; import core.execution.image_code_continuation; assert 'config' not in sys.modules; assert 'models' not in sys.modules"],
        cwd=Path(__file__).resolve().parents[1], env=os.environ.copy(), capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr


def test_cli_only_safe_metadata_and_no_runtime_key_or_network(asset_control_case, monkeypatch, capsys):
    case = prepared(asset_control_case)
    monkeypatch.setattr(continuation, "BACKEND", case.backend)
    from core.execution import image_operator
    monkeypatch.setattr(image_operator, "tudou_credential", lambda: pytest.fail("loaded a key"))
    monkeypatch.setattr(image_operator, "build_image_session", lambda *a, **k: pytest.fail("constructed runtime"))
    assert continuation.main(["quote", "--control", str(case.path), "--grant", case.grant["id"]]) == 0
    output = capsys.readouterr().out
    q = json.loads(output)["quote"]
    assert str(case.root) not in output and '"admission"' not in output and '"roots"' not in output
    assert continuation.main(["activate", "--control", str(case.path), "--quote", q["id"], "--disclosure-hash", q["disclosure_hash"], "--consent"]) == 0
    activation = json.loads(capsys.readouterr().out)["activation"]
    assert continuation.main(["status", "--control", str(case.path), "--activation", activation["id"]]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["code_continuation_active"] and status["control_authority_valid"]
    assert not status["dispatch_available"] and status["automatic_followup_calls"] == 0
    assert not posts(case)


def test_historical_disclosure_rejects_rehashed_scope_caps_source_and_expiry_changes(asset_control_case):
    case = prepared(asset_control_case)
    q = quote(case)["quote"]
    payload = case.env.runtime.store.read_only()
    attacks = [
        lambda d: d.update(new_paid_calls=1),
        lambda d: d.update(new_risk_cny="1.000000"),
        lambda d: d.update(original_valid_until=(now() + timedelta(days=100)).isoformat()),
        lambda d: d["remaining_limits"].update(max_submissions=99),
        lambda d: d["remaining_limits"].update(remaining_submissions=99),
        lambda d: d.update(source_hash="0" * 64),
        lambda d: d.update(scope_hash="0" * 64),
        lambda d: d.update(grant_hash="0" * 64),
        lambda d: d.update(policy_hash="0" * 64),
        lambda d: d["admission"].update(plan_hash="0" * 64),
        lambda d: d["admission"].update(source_hash="0" * 64),
        lambda d: d["code_diff"].update(added=[]),
        lambda d: d["new_code_manifest"].update({"../private.py": "0" * 64}),
        lambda d: d["checkpoint"].update(head_revision=True),
    ]
    for attack in attacks:
        changed = copy.deepcopy(q)
        attack(changed["disclosure"])
        changed["disclosure_hash"] = digest(changed["disclosure"])
        changed["id"] = changed["disclosure_hash"][:32]
        denied(lambda: continuation.validate_record(payload, changed))
    assert not posts(case)


def test_original_grant_dispatch_uses_new_code_but_preserves_original_scope_and_permit_contract(asset_control_case):
    case = prepared(asset_control_case)
    before = copy.deepcopy(case.env.runtime.store.read_only())
    _, wrapped = activate(case)
    from core.execution.image_operator import build_image_session
    with build_image_session(wrapped, receipt_capture=case.settings,
            credential_loader=lambda: pytest.fail("synthetic request loaded a live key")) as session:
        scope = next(r for r in before["media"].values() if r["kind"] == "scope")
        operation = session.media.reserve_submit(case.task["id"], case.grant["id"], "S01", key="approved-code-original-grant")["operation"]
        assert operation["scope_id"] == scope["id"] and operation["submission_index"] == 1
        assert operation["descriptor"]["policy"] == scope["policy"]
        assert operation["descriptor"]["budget_anchor"] == scope["anchor"]
        permit = operation["descriptor"]["permit"]
        assert permit["schema_version"] == 1 and permit["grant_id"] == case.grant["id"]
        assert permit["deadline_at"] <= scope["policy"]["valid_until"]
        received = session.media.submit(case.task["id"], operation["id"])["operation"]
        assert received["status"] == "accepted" and len(posts(case)) == 1
        assert session.media.submit(case.task["id"], operation["id"])["replayed"]
        assert len(posts(case)) == 1
        with pytest.raises(CreatorError) as error:
            session.media.reserve_submit(case.task["id"], case.grant["id"], "S02", key="no-code-approval-fee-bypass")
        assert error.value.code == "MEDIA_PLATFORM_FEE_REQUIRED" and len(posts(case)) == 1
    after = case.env.runtime.store.read_only()
    assert all(after[name] == value for name, value in before.items() if name != "media")
    assert all(after["media"][key] == value for key, value in before["media"].items())
    assert sum(r["kind"] == "grant" for r in after["media"].values()) == 1
    assert sum(r["kind"] == "scope" for r in after["media"].values()) == 1


def test_revoked_original_grant_stops_unstarted_native_continuation(asset_control_case):
    case = prepared(asset_control_case)
    _, wrapped = activate(case)
    from core.execution.image_operator import build_image_session
    with build_image_session(wrapped, receipt_capture=case.settings,
            credential_loader=lambda: pytest.fail("revoked request loaded a live key")) as session:
        operation = session.media.reserve_submit(case.task["id"], case.grant["id"], "S01", key="original-grant-before-revoke")["operation"]
        session.media.revoke(case.task["id"], case.grant["id"])
        before = case.state_path.read_bytes()
        with pytest.raises(CreatorError):
            session.media.submit(case.task["id"], operation["id"])
        assert case.state_path.read_bytes() == before and not posts(case)
        with pytest.raises(CreatorError):
            session.media.reserve_submit(case.task["id"], case.grant["id"], "S02", key="revoked-cannot-reserve-next")
        assert case.state_path.read_bytes() == before and not posts(case)
