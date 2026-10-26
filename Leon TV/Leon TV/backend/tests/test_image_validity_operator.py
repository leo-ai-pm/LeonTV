"""Authorization CLI boundaries, independent of the paid media runtime."""
import json
import socket

import pytest
from asset_operator_fixture import asset_control_case as asset_control_case

from core.execution import image_validity_operator as operator
from core.execution.policy import units


def test_default_operator_is_off_without_implicit_control(monkeypatch, capsys):
    monkeypatch.setenv("LEONTV_IMAGE_CONTROL_FILE", "/private/never-read.json")
    monkeypatch.setattr(operator, "read_image_control", lambda *a, **k: pytest.fail("implicit control"))
    assert operator.main([]) == 0
    value = json.loads(capsys.readouterr().out)
    assert value == {"mode": "off", "dispatch_ready": False, "runtime_contract_pending": True,
        "paid_calls": 0, "automatic_followup_calls": 0}


def test_invalid_arguments_never_reveal_private_values_or_allow_parent_path(capsys):
    secret = "/private/sensitive-parent-file.json"
    for args in [["--unknown", secret], [secret], ["quote", "--valid-until"],
                 ["activate", "--intent", secret], ["status", "--parent-ledger", secret],
                 ["prepare", "--quote", "a" * 32, "--proposal-hash", "b" * 64]]:
        assert operator.main(args) == 1
        output = capsys.readouterr()
        assert output.err == "" and secret not in output.out
        assert json.loads(output.out) == {"error": operator.ERROR_CODE, "dispatch_ready": False,
            "runtime_contract_pending": True, "paid_calls": 0, "automatic_followup_calls": 0}


def test_native_cli_consent_reservation_and_activation_emit_metadata_only(asset_control_case, monkeypatch, capsys):
    from test_image_validity_continuation import prepared_validity_case

    from core.execution import image_operator
    from experiments.tudou_ab.ledger import BudgetLedger

    case = prepared_validity_case(asset_control_case)
    before_projection = case.media.get(case.task["id"])["media"]
    monkeypatch.setattr(operator, "BACKEND", case.backend)

    def forbidden(*args, **kwargs):
        pytest.fail("Authorization CLI used paid execution or legacy budget writer")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(image_operator, "tudou_credential", forbidden)
    monkeypatch.setattr(image_operator, "build_image_session", forbidden)
    monkeypatch.setattr(BudgetLedger, "__init__", forbidden)
    args = ["--control", str(case.path)]
    assert operator.main(["quote", *args, "--valid-until", case.valid_until]) == 0
    quoted = json.loads(capsys.readouterr().out)
    original = case.env.runtime.store.path("state.json").read_bytes()
    consent_args = [*args, "--quote", quoted["resource_id"], "--proposal-hash", quoted["proposal_hash"]]
    assert operator.main(["prepare", *consent_args]) == 1
    capsys.readouterr()
    assert case.env.runtime.store.path("state.json").read_bytes() == original
    assert operator.main(["prepare", *consent_args, "--consent"]) == 0
    prepared = json.loads(capsys.readouterr().out)
    assert prepared["reservation_intent_count"] == 1 and prepared["activation_count"] == 0
    assert operator.main(["activate", *args, "--intent", prepared["resource_id"],
        "--proposal-hash", quoted["proposal_hash"], "--consent"]) == 0
    activated = json.loads(capsys.readouterr().out)
    assert activated["journal_stage"] == "activation_recorded_runtime_pending"
    final = case.env.runtime.store.path("state.json").read_bytes()
    assert operator.main(["status", *args]) == 0
    status = json.loads(capsys.readouterr().out)
    assert case.env.runtime.store.path("state.json").read_bytes() == final
    projection = case.media.get(case.task["id"])["media"]
    assert units(projection["reserved_risk_cny"]) == units(before_projection["reserved_risk_cny"]) + 1000000
    assert projection["paid_call_count"] == before_projection["paid_call_count"] == 2
    assert projection["continuation"]["additional_reserved_call_slots"] == 1
    assert projection["continuation"]["accounts"][0]["retained_risk_cny"] == status["retained_risk_cny"]
    for value in [quoted, prepared, activated, status]:
        assert value["dispatch_ready"] is False and value["runtime_contract_pending"] is True
        assert value["paid_calls"] == value["automatic_followup_calls"] == 0
        output = json.dumps(value)
        assert str(case.root) not in output and case.unknown["id"] not in output
        assert "parent_evidence" not in output and "parent_receipt" not in output and "current_code_manifest" not in output
