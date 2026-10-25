"""The continuation review CLI has no authority, credential or network path."""
import json
import socket

import pytest
from asset_operator_fixture import asset_control_case as asset_control_case
from test_image_continuation_proposal import prepared_proposal_case

from core.execution import image_continuation_operator as operator


def test_default_review_is_off_and_ignores_environment_configuration(monkeypatch, capsys):
    monkeypatch.setenv("LEONTV_IMAGE_CONTROL_FILE", "/never/read")
    monkeypatch.setenv("LEONTV_EXECUTION_CONTROL_FILE", "/never/read")
    monkeypatch.setattr(operator, "read_image_control", lambda *a, **k: pytest.fail("implicit authority"))
    assert operator.main([]) == 0
    value = json.loads(capsys.readouterr().out)
    assert value["mode"] == "off" and value["dispatch_available"] is value["approval_granted"] is False


def test_proposal_export_and_verification_only_emit_safe_metadata(asset_control_case, monkeypatch, capsys):
    case = prepared_proposal_case(asset_control_case)
    monkeypatch.setattr(operator, "BACKEND", case.backend)
    from core.execution import image_operator, store

    def forbidden(*args, **kwargs):
        pytest.fail("review constructed execution, loaded key, mutated state or used network")

    monkeypatch.setattr(store.ExecutionStore, "transaction", forbidden)
    monkeypatch.setattr(image_operator, "build_image_session", forbidden)
    monkeypatch.setattr(image_operator, "tudou_credential", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    original = case.env.runtime.store.path("state.json").read_bytes()
    parent_path = case.live_parent_path
    parent_original = parent_path.read_bytes()
    directory = case.root / "review-export"
    directory.mkdir(mode=0o700)
    target = directory / "proposal.json"
    args = ["--control", str(case.path), "--parent-ledger", str(parent_path)]
    assert operator.main(["propose", *args, "--valid-until", case.valid_until, "--output", str(target)]) == 0
    output = capsys.readouterr().out
    value = json.loads(output)
    assert value["additional_allocation_status"] == "proposed_only"
    assert value["dispatch_available"] is value["approval_granted"] is False
    assert value["paid_calls"] == value["automatic_followup_calls"] == 0
    assert "live_parent_path" not in output and str(case.root) not in output
    assert "current_code_manifest" not in output and case.unknown["id"] not in output
    assert target.stat().st_mode & 0o777 == 0o600
    exported = target.read_bytes()
    assert operator.main(["verify", *args, "--proposal", str(target)]) == 0
    assert json.loads(capsys.readouterr().out)["proposal_hash"] == value["proposal_hash"]
    assert operator.main(["propose", *args, "--valid-until", case.valid_until, "--output", str(target)]) == 0
    capsys.readouterr()
    assert target.read_bytes() == exported
    assert original == case.env.runtime.store.path("state.json").read_bytes()
    assert parent_original == parent_path.read_bytes()


@pytest.mark.parametrize("attack", ["static", "native_state", "parent_ledger", "symlink", "public_mode", "existing_other"])
def test_document_export_rejects_unsafe_or_conflicting_targets(asset_control_case, monkeypatch, capsys, attack):
    case = prepared_proposal_case(asset_control_case)
    monkeypatch.setattr(operator, "BACKEND", case.backend)
    directory = case.root / "review-export"
    directory.mkdir(mode=0o700)
    target = directory / "proposal.json"
    if attack == "static":
        target = case.backend / "code/proposal.json"
    elif attack == "native_state":
        target = case.env.runtime.store.root / "new-review.json"
    elif attack == "parent_ledger":
        target = case.live_parent_path
    elif attack == "symlink":
        original = directory / "original.json"
        original.write_text("{}")
        original.chmod(0o600)
        target.symlink_to(original)
    elif attack == "public_mode":
        directory.chmod(0o755)
    else:
        target.write_text("{}")
        target.chmod(0o600)
    assert operator.main(["propose", "--control", str(case.path), "--parent-ledger", str(case.live_parent_path),
        "--valid-until", case.valid_until, "--output", str(target)]) == 1
    output = capsys.readouterr().out
    assert json.loads(output)["error"] == operator.ERROR_CODE and str(target) not in output


def test_bad_arguments_are_safe_and_never_disclose_private_inputs(capsys):
    cases = [
        ["propose", "--control", "/private/nonexistent-sensitive.json"],
        ["status", "--unknown-sensitive-flag", "/private/unknown-sensitive.json"],
        ["/private/invalid-sensitive-choice.json", "--control", "/private/choice-sensitive.json"],
        ["propose", "--control"],
    ]
    for args in cases:
        assert operator.main(args) == 1
        captured = capsys.readouterr()
        value = json.loads(captured.out)
        assert value["error"] == operator.ERROR_CODE
        assert value["dispatch_available"] is value["approval_granted"] is False
        assert value["paid_calls"] == value["automatic_followup_calls"] == 0
        assert captured.err == "" and "sensitive" not in captured.out and "/private/" not in captured.out
