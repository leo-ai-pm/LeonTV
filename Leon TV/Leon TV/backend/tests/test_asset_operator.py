"""Actual metadata CLI and detached synthetic HTTP; all parent facts fictional."""
import copy
import json
import os
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

import pytest
from asset_operator_fixture import asset_control_case as asset_control_case
from test_image_fees import export

from core.creator.store import CreatorError, digest
from core.execution import asset_operator
from core.execution.asset_control import read_asset_control
from core.execution.policy import now
from core.execution.receipts import timestamp


def loaded(case):
    return read_asset_control(case.path, case.backend)


def factory(case, *, loader=None):
    def make(control):
        return asset_operator.build_asset_session(control, receipt_capture=case.settings,
            credential_loader=loader or (lambda: pytest.fail("synthetic/operator read unexpectedly loaded a key")))
    return make


def run(case, command, *args, session_factory=None):
    parsed = asset_operator.parser().parse_args([command, *args])
    return asset_operator.execute(loaded(case), command, parsed, session_factory=session_factory or factory(case))


def authorize(case):
    quote = run(case, "quote", "--key", "operator-quote-001")["quote"]
    grant = run(case, "grant", "--quote", quote["id"], "--disclosure-hash", quote["disclosure_hash"], "--consent", "--key", "operator-grant-001")["grant"]
    return quote, grant


def reserve(case, grant):
    return run(case, "reserve", "--grant", grant["id"], "--key", "operator-reserve-001")["operation"]


def operation(case, operation_id):
    return case.env.runtime.store.read_only()["asset_design"]["operation:" + operation_id]


def fee_export(case, op, *, request_id=None, quota=20000):
    receipt, raw = op["receipt"], op["raw"]
    row = {"id": 1, "user_id": 1, "created_at": int(timestamp(raw["received_at"]).timestamp()), "type": 2, "content": "SYNTHETIC ASSET DESIGN",
        "username": "synthetic", "token_name": case.source["token_name"], "model_name": "gemini-3.8-flash", "quota": quota,
        "prompt_tokens": receipt["input_tokens"], "completion_tokens": receipt["output_tokens"], "use_time": 1, "is_stream": False,
        "channel": 1, "channel_name": "synthetic", "token_id": 1, "group": case.source["group"], "ip": "127.0.0.1",
        "request_id": request_id or "synthetic-asset-platform-" + op["id"], "other": "{}"}
    path = case.root / "asset-fee-export.json"
    path.write_bytes(export(row))
    path.chmod(0o600)
    return path


def posts(case):
    return case.counter.read_text().splitlines() if case.counter.exists() else []


def test_absent_control_is_off_and_does_not_build_session(monkeypatch, capsys):
    monkeypatch.setenv("LEONTV_EXECUTION_CONTROL_FILE", "/never/read/old-text.json")
    monkeypatch.setattr(asset_operator, "read_asset_control", lambda *args: pytest.fail("must not load implicit control"))
    assert asset_operator.main(["status"]) == 0
    assert json.loads(capsys.readouterr().out) == {"mode": "off", "dispatch_available": False, "automatic_followup_calls": 0}
    assert asset_operator.main(["quote", "--key", "off-quote-001"]) == 1
    assert json.loads(capsys.readouterr().out)["error"] == "ASSET_CONTROL_UNAVAILABLE"


def test_cli_import_does_not_import_config_or_models():
    result = subprocess.run([sys.executable, "-c", "import sys; import core.execution.asset_operator; assert 'config' not in sys.modules; assert 'models' not in sys.modules"],
        cwd=Path(__file__).resolve().parents[1], env=os.environ.copy(), capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr


def test_status_uses_original_read_only_without_key_or_recovery(asset_control_case, monkeypatch):
    case = asset_control_case(mode="observe_only")
    before = (case.env.runtime.store.root / "state.json").read_bytes()
    monkeypatch.setattr(asset_operator, "build_asset_session", lambda *args: pytest.fail("status created runtime"))
    monkeypatch.setattr(asset_operator, "tudou_credential", lambda: pytest.fail("status loaded key"))
    status = run(case, "status")
    assert status["mode"] == "observe_only" and status["dispatch_available"] is False
    assert status["asset_design"]["scope_count"] == 0
    assert before == (case.env.runtime.store.root / "state.json").read_bytes()
    assert not case.counter.exists()


def test_factory_disabled_text_no_key_observer_or_task_mutation(asset_control_case):
    case = asset_control_case()
    before = case.env.runtime.store.read_only()
    with factory(case)(loaded(case)) as session:
        assert session.runtime.enabled is False
        assert session.runtime.policy is None
        assert session.runtime.api_key == "SYNTHETIC-ASSET-OPERATOR-KEY"
        assert session.runtime.store.root == case.env.runtime.store.root
        assert digest(session.runtime.store.read_only()) == digest(before)
    assert len(case.env.calls()) == 3 and not case.counter.exists()


def test_actual_cli_quote_grant_reserve_start_fee_restart_and_safe_manifest(asset_control_case, monkeypatch, capsys):
    case = asset_control_case()
    native = copy.deepcopy(case.env.runtime.store.read_only())
    parent = {name: (case.root / name).read_bytes() for name in ("authorization.json", "parent-ledger.json", "begin.json")}
    monkeypatch.setattr(asset_operator, "BACKEND", case.backend)
    original_execute = asset_operator.execute
    monkeypatch.setattr(asset_operator, "execute", lambda control, command, args: original_execute(control, command, args, session_factory=factory(case)))

    def cli(command, *args):
        assert asset_operator.main([command, "--control", str(case.path), *args]) == 0
        raw = capsys.readouterr().out
        for secret in (str(case.root), "PRIVATE-REQUIREMENTS", "SYNTHETIC PRIVATE REASONING", "body_base64", '"asset_input":', "SYNTHETIC-ASSET-OPERATOR-KEY"):
            assert secret not in raw
        return json.loads(raw)

    quote = cli("quote", "--key", "actual-cli-quote-001")
    assert quote["model"] == "gemini-3.8-flash" and quote["max_calls"] == 1
    assert quote["original_budget_cny"] == "100" and quote["opening_risk_cny"] == "75.37"
    assert quote["opening_paid_call_count"] == 47 and quote["automatic_repair_calls"] == quote["image_submissions"] == 0
    grant = cli("grant", "--quote", quote["quote"]["id"], "--disclosure-hash", quote["quote"]["disclosure_hash"], "--consent", "--key", "actual-cli-grant-001")["grant"]
    op = cli("reserve", "--grant", grant["id"], "--key", "actual-cli-reserve-001")["operation"]
    assert not case.counter.exists()
    first = cli("start", "--operation", op["id"])
    assert first["operation"]["status"] == "awaiting_fee" and len(posts(case)) == 1
    waiting = cli("status")
    assert waiting["dispatch_available"] is False and waiting["blocked_reason"] == "PLATFORM_FEE_REQUIRED"
    assert cli("start", "--operation", op["id"])["replayed"] is True
    assert cli("recover", "--operation", op["id"])["replayed"] is True
    path = fee_export(case, operation(case, op["id"]))
    settled = cli("fees-import", "--operation", op["id"], str(path))
    assert settled["operation"]["status"] == "manifest_ready"
    journal = (case.env.runtime.store.root / "state.json").read_bytes()
    assert cli("fees-import", "--operation", op["id"], str(path))["replayed"] is True
    assert (case.env.runtime.store.root / "state.json").read_bytes() == journal
    status = cli("status", "--operation", op["id"])
    assert status["manifest_verified"] is True
    assert status["proof"]["manifest_hash"] == status["manifest_hash"]
    assert "manifest" not in status
    assert status["asset_design"]["paid_call_count"] == 1 and len(posts(case)) == 1
    assert status["control_authority_valid"] is True and status["dispatch_available"] is False
    assert status["blocked_reason"] == "SCOPE_CALL_LIMIT"
    current = case.env.runtime.store.read_only()
    assert {name: current[name] for name in native} == native
    assert all((case.root / name).read_bytes() == raw for name, raw in parent.items())


def test_fee_replay_keeps_original_observed_at(asset_control_case, monkeypatch):
    case = asset_control_case()
    _, grant = authorize(case)
    op = reserve(case, grant)
    run(case, "start", "--operation", op["id"])
    path = fee_export(case, operation(case, op["id"]))
    run(case, "fees-import", "--operation", op["id"], str(path))
    journal = (case.env.runtime.store.root / "state.json").read_bytes()
    monkeypatch.setattr(asset_operator, "utc_now", lambda: pytest.fail("replay generated a new observation time"))
    assert run(case, "fees-import", "--operation", op["id"], str(path))["replayed"] is True
    assert (case.env.runtime.store.root / "state.json").read_bytes() == journal


def test_legacy_export_bridge_is_required_and_does_not_rewrite_old_fees(asset_control_case):
    case = asset_control_case(legacy=True)
    original = copy.deepcopy(case.env.runtime.store.read_only())
    quote = run(case, "quote", "--key", "legacy-quote-001")["quote"]
    with pytest.raises(CreatorError) as error:
        run(case, "grant", "--quote", quote["id"], "--disclosure-hash", quote["disclosure_hash"], "--consent", "--key", "legacy-grant-001")
    assert error.value.code == "MEDIA_LEGACY_FEE_BRIDGE_REQUIRED"
    for op_id, blob in case.old.text_exports.items():
        path = case.root / (op_id + "-fees.json")
        path.write_bytes(blob)
        path.chmod(0o600)
        first = run(case, "legacy-fees-import", "--operation", op_id, str(path))
        assert first["claim_id"].startswith("tudou-request-sha256:")
        journal = (case.env.runtime.store.root / "state.json").read_bytes()
        assert run(case, "legacy-fees-import", "--operation", op_id, str(path))["replayed"] is True
        assert journal == (case.env.runtime.store.root / "state.json").read_bytes()
    grant = run(case, "grant", "--quote", quote["id"], "--disclosure-hash", quote["disclosure_hash"], "--consent", "--key", "legacy-grant-001")["grant"]
    assert grant["id"] and not posts(case)
    current = case.env.runtime.store.read_only()
    assert all(current[name] == values for name, values in original.items())


@pytest.mark.parametrize("command,args", [("quote", ["--key", "observe-quote-001"]),
    ("grant", ["--quote", "0" * 32, "--disclosure-hash", "0" * 64, "--consent", "--key", "observe-grant-001"]),
    ("reserve", ["--grant", "0" * 32, "--key", "observe-reserve-001"]), ("start", ["--operation", "0" * 32]),
    ("recover", ["--operation", "0" * 32]), ("fees-import", ["--operation", "0" * 32, "/not/read"]),
    ("legacy-fees-import", ["--operation", "0" * 32, "/not/read"]), ("revoke", ["--grant", "0" * 32])])
def test_observe_only_rejects_all_writes_before_runtime_or_export(asset_control_case, command, args):
    case = asset_control_case(mode="observe_only")
    before = (case.env.runtime.store.root / "state.json").read_bytes()
    with pytest.raises(CreatorError) as error:
        run(case, command, *args, session_factory=lambda *args: pytest.fail("observe-only opened write runtime"))
    assert error.value.code == "ASSET_CONTROL_UNAVAILABLE"
    assert before == (case.env.runtime.store.root / "state.json").read_bytes()


def test_code_drift_and_expiry_allow_history_and_fees_but_block_new_dispatch(asset_control_case):
    case = asset_control_case()
    _, grant = authorize(case)
    op = reserve(case, grant)
    run(case, "start", "--operation", op["id"])
    path = fee_export(case, operation(case, op["id"]))
    (case.backend / "core/execution/asset_operator.py").write_text("# later public code\n")
    assert run(case, "status")["dispatch_available"] is False
    assert run(case, "recover", "--operation", op["id"])["replayed"] is True
    assert run(case, "fees-import", "--operation", op["id"], str(path))["operation"]["status"] == "manifest_ready"
    assert run(case, "status", "--operation", op["id"])["manifest_verified"] is True
    with pytest.raises(CreatorError) as error:
        run(case, "quote", "--key", "drift-new-quote-001")
    assert error.value.code == "ASSET_CONTROL_UNAVAILABLE" and len(posts(case)) == 1


def test_unknown_keeps_single_call_no_repair_and_revoke_does_not_release(asset_control_case):
    case = asset_control_case(fault="unknown")
    _, grant = authorize(case)
    op = reserve(case, grant)
    result = run(case, "start", "--operation", op["id"])
    assert result["operation"]["status"] == "unknown"
    status = run(case, "status")
    assert status["dispatch_available"] is False and status["blocked_reason"] == "RESPONSE_UNCONFIRMED"
    assert run(case, "start", "--operation", op["id"])["replayed"] is True
    run(case, "recover", "--operation", op["id"])
    run(case, "revoke", "--grant", grant["id"])
    with pytest.raises(CreatorError):
        run(case, "reserve", "--grant", grant["id"], "--key", "unknown-retry-001")
    projection = run(case, "status")["asset_design"]
    assert projection["reserved_risk_cny"] == "1.000000" and projection["paid_call_count"] == 1
    assert projection["risk_released"] is False and len(posts(case)) == 1


@pytest.mark.parametrize("arguments", [["quote", "--key", "x", "--manifest", "{}"], ["quote", "--key", "x", "--model", "other"],
    ["start", "--operation", "0" * 32, "--provider", "other"], ["grant", "--quote", "x", "--disclosure-hash", "y", "--key", "z"],
    ["status", "--private-key", "DO-NOT-ECHO"]])
def test_cli_rejects_arbitrary_provider_manifest_and_missing_consent_safely(arguments, capsys):
    assert asset_operator.main(arguments) == 1
    output = capsys.readouterr()
    assert json.loads(output.out)["error"] == "ASSET_CONTROL_UNAVAILABLE"
    assert "DO-NOT-ECHO" not in output.out and not output.err


def test_wrong_source_task_never_enters_session(asset_control_case):
    case = asset_control_case()
    with pytest.raises(CreatorError) as error:
        run(case, "quote", "--task", "0" * 32, "--key", "wrong-task-001", session_factory=lambda *args: pytest.fail("wrong-task runtime"))
    assert error.value.code == "ASSET_CONTROL_UNAVAILABLE"


def test_expired_configuration_does_not_open_dispatch(asset_control_case):
    case = asset_control_case()
    current = case.data["policy"]
    current["valid_until"] = (now() - timedelta(days=1)).isoformat()
    current["policy_hash"] = digest({key: value for key, value in current.items() if key != "policy_hash"})
    case.request["policy_hash"] = current["policy_hash"]
    case.save()
    assert run(case, "status")["dispatch_available"] is False
    with pytest.raises(CreatorError):
        run(case, "quote", "--key", "expired-quote-001", session_factory=lambda *args: pytest.fail("expired runtime"))


def test_original_text_revoke_preserves_frozen_asset_history_and_local_fee_recovery(asset_control_case):
    case = asset_control_case()
    _, grant = authorize(case)
    op = reserve(case, grant)
    run(case, "start", "--operation", op["id"])
    path = fee_export(case, operation(case, op["id"]))
    payload = case.env.runtime.store.read_only()
    native = payload["tasks"][case.task["id"]]
    native_grant = next(record for record in payload["grants"].values() if record["task_id"] == native["id"])
    response = case.env.client.post(case.env.url(case.task) + "/grants/" + native_grant["id"] + "/revoke",
        json={"client_action_key": "native-revoke-after-asset-001", "expected_execution_revision": native["revision"]})
    assert response.status_code == 200, response.text
    assert loaded(case).verify_authority() is True
    assert run(case, "recover", "--operation", op["id"])["replayed"] is True
    assert run(case, "fees-import", "--operation", op["id"], str(path))["operation"]["status"] == "manifest_ready"
    assert run(case, "status", "--operation", op["id"])["manifest_verified"] is True
    case.data["mode"] = "observe_only"
    case.save()
    assert run(case, "status", "--operation", op["id"])["manifest_verified"] is True
    assert len(posts(case)) == 1


def test_first_asset_quote_uses_full_current_account_handoff_with_another_native_task(asset_control_case):
    from core.execution.media_ledger import native_context
    case = asset_control_case()
    second = case.env.source_task(suffix="002")
    quote = case.env.quote(second, key="second-native-quote-001")
    grant = case.env.grant(second, quote, key="second-native-grant-001")
    assert case.env.action(second, grant["execution"], key="second-native-action-001").status_code == 200
    assert case.env.complete(second)["status"] == "plan_ready"
    context, handoff = native_context(case.env.runtime.store.read_only(), case.task["id"])
    case.context, case.handoff = context, handoff
    case.data["admission"].update(handoff_hash=handoff["handoff_hash"], provenance_hash=handoff["provenance_hash"])
    case.save()
    quote = run(case, "quote", "--key", "multi-task-asset-quote-001")["quote"]
    assert quote["id"] and not posts(case)


def test_later_media_allocation_does_not_revoke_existing_asset_reservation(asset_control_case):
    from core.execution.image_policy import MediaBudgetAnchor
    from core.execution.media_runtime import MediaExecution
    case = asset_control_case()
    _, grant = authorize(case)
    op = reserve(case, grant)
    anchor = MediaBudgetAnchor(account_scope_id=case.anchor["account_scope_id"], parent_authorization_hash=case.anchor["parent_authorization_hash"],
        parent_ledger_hash="1" * 64, parent_allocation_hash="2" * 64, parent_allocation_begin_hash="3" * 64,
        original_budget_cny="100", opening_risk_cny="76.37", opening_paid_call_count=48, scope_risk_cny="2", scope_max_calls=2,
        account_limit_cny="78.37", account_max_calls=50)
    media = MediaExecution(case.env.runtime, policy=case.old.policy, budget_anchor=anchor, manifest=case.old.manifest,
        authority_guard=lambda scope: True, fee_source=case.source, receipt_capture=case.old.settings)
    media.quote(case.task["id"], key="later-media-quote-001")
    assert loaded(case).verify_authority(for_dispatch=True) is True
    assert run(case, "start", "--operation", op["id"])["operation"]["status"] == "awaiting_fee"
    assert len(posts(case)) == 1


def test_fee_overrun_is_retained_and_status_never_advertises_dispatch(asset_control_case):
    case = asset_control_case()
    _, grant = authorize(case)
    op = reserve(case, grant)
    run(case, "start", "--operation", op["id"])
    path = fee_export(case, operation(case, op["id"]), quota=1000000)
    run(case, "fees-import", "--operation", op["id"], str(path))
    status = run(case, "status")
    assert status["control_authority_valid"] is True and status["dispatch_available"] is False
    assert status["blocked_reason"] == "EMPIRICAL_RISK_OVERRUN"
    assert status["asset_design"]["retained_exposure_cny"] == "2.000000"
    assert status["asset_design"]["risk_released"] is False and len(posts(case)) == 1


def test_legacy_fee_bridge_can_target_another_task_in_same_parent_account(asset_control_case):
    case = asset_control_case(legacy=True, second_task=True)
    before = copy.deepcopy(case.env.runtime.store.read_only())
    assert case.task["id"] != case.old.task["id"]
    quote = run(case, "quote", "--key", "cross-task-asset-quote-001")["quote"]
    with pytest.raises(CreatorError) as error:
        run(case, "grant", "--quote", quote["id"], "--disclosure-hash", quote["disclosure_hash"], "--consent", "--key", "cross-task-asset-grant-001")
    assert error.value.code == "MEDIA_LEGACY_FEE_BRIDGE_REQUIRED"
    other_task_count = 0
    for op_id, blob in case.old.text_exports.items():
        original = before["operations"][op_id]
        other_task_count += original["task_id"] != case.task["id"]
        path = case.root / (op_id + "-cross-task.json")
        path.write_bytes(blob)
        path.chmod(0o600)
        assert run(case, "legacy-fees-import", "--operation", op_id, str(path))["replayed"] is False
        journal = (case.env.runtime.store.root / "state.json").read_bytes()
        assert run(case, "legacy-fees-import", "--operation", op_id, str(path))["replayed"] is True
        assert (case.env.runtime.store.root / "state.json").read_bytes() == journal
    assert other_task_count == 3
    assert run(case, "grant", "--quote", quote["id"], "--disclosure-hash", quote["disclosure_hash"], "--consent", "--key", "cross-task-asset-grant-001")["grant"]["id"]
    after = case.env.runtime.store.read_only()
    assert all(after[name] == value for name, value in before.items())
    assert len(case.env.calls()) == 6 and not posts(case)
    with pytest.raises(CreatorError):
        run(case, "quote", "--task", case.old.task["id"], "--key", "cross-task-generation-denied-001")
