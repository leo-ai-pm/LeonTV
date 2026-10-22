"""D11 server-only Chat parameter binding; no real provider or production policy."""
import base64
import copy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError
from test_planner_execution import change_policy, policy
from test_planner_execution import env as env
from test_planner_execution import no_network as no_network
from test_planner_responses import install_provider, responses_policy

from core.creator.store import CreatorError, digest
from core.execution.policy import ExecutionPolicy, TrustedInputBounder, cost, safe_policy
from core.execution.transport import ControlledTransport, ResponseUnconfirmed, normalized, wire_for

PARAMETER = "chat_output_parameter"
CHOICES = ["max_tokens", "max_completion_tokens"]
FIXTURE = Path(__file__).parent / "fixtures" / "d09_legacy_execution.json"


def selected_policy(base, selected):
    return ExecutionPolicy.model_validate({**{key: value for key, value in base.items() if key != "policy_hash"}, PARAMETER: selected}).frozen()


def test_legacy_export_hash_and_wire_bytes_remain_identical():
    captured = json.loads(FIXTURE.read_text())
    old = captured["policy"]
    current = ExecutionPolicy.model_validate({key: value for key, value in old.items() if key != "policy_hash"})
    assert current.frozen() == old and PARAMETER not in current.model_dump()
    assert old["policy_hash"] == "3263f5e8a0d0481aeb45d05d5b2cd57f560efb9890dc4b5c24aa96b33c14a379"
    messages = [{"role": "system", "content": {"x": "SYNTHETIC Ａ\r\n"}}, {"role": "tool", "content": {"ok": True}}]
    expected = {"model": old["model"], "max_tokens": old["max_output_tokens"], "messages": [
        {"role": "system", "content": json.dumps(messages[0]["content"], ensure_ascii=False)},
        {"role": "user", "content": json.dumps(messages[1]["content"], ensure_ascii=False)}]}
    assert wire_for(messages, old).encode() == json.dumps(expected, ensure_ascii=False).encode()
    assert safe_policy(old)[PARAMETER] == "max_tokens" and PARAMETER not in old


@pytest.mark.parametrize("selected", CHOICES)
def test_explicit_selection_is_hashed_and_only_one_parameter_is_sent(selected):
    old = policy().frozen()
    frozen = selected_policy(old, selected)
    assert frozen["policy_hash"] != old["policy_hash"] and frozen[PARAMETER] == selected
    assert ExecutionPolicy.model_validate({k: v for k, v in frozen.items() if k != "policy_hash"}).model_dump()[PARAMETER] == selected
    body = json.loads(wire_for([{"role": "user", "content": "Synthetic"}], frozen))
    assert set(body) == {"model", "messages", selected}
    assert body[selected] == frozen["max_output_tokens"] and "max_output_tokens" not in body
    assert set(body).intersection(CHOICES) == {selected}


@pytest.mark.parametrize("selected", ["max_tokens", "max_completion_tokens", None, "max_output_tokens", True])
def test_responses_rejects_any_explicit_chat_selector(selected):
    p = {key: value for key, value in responses_policy().items() if key != "policy_hash"}
    with pytest.raises(ValidationError):
        ExecutionPolicy.model_validate({**p, PARAMETER: selected})
    assert PARAMETER not in safe_policy(responses_policy())


@pytest.mark.parametrize("selected", [None, "max_output_tokens", "MAX_TOKENS", True, 1])
def test_invalid_chat_selector_is_rejected(selected):
    with pytest.raises(ValidationError):
        ExecutionPolicy.model_validate({**policy().model_dump(), PARAMETER: selected})


@pytest.mark.parametrize("selected", CHOICES)
def test_actual_loop_binds_quote_operation_permit_and_summary(env, selected):
    change_policy(env, chat_output_parameter=selected)
    task = env.source_task()
    quote = env.quote(task)
    grant = env.grant(task, quote)
    assert quote["quote"]["policy"][PARAMETER] == selected and env.provider.calls == []
    assert env.action(task, grant["execution"]).status_code == 200
    final = env.wait(task, {"plan_ready"})
    assert len(env.provider.calls) == final["ledger"]["call_count"] == 2
    assert all(op[PARAMETER] == selected for op in final["operations"])
    assert final["ledger"]["locally_accounted_cny"] == "0.000100" and final["ledger"]["provider_bill_confirmed"] is False
    for request in env.provider.calls:
        body = json.loads(request.content)
        assert set(body).intersection(CHOICES) == {selected}
        assert body[selected] == 2000 and "max_output_tokens" not in body
    with env.runtime.store.transaction() as payload:
        for op in payload["operations"].values():
            p = payload["quotes"][op["quote_id"]]["policy"]
            assert p[PARAMETER] == op[PARAMETER] == op["permit"][PARAMETER] == selected
            assert op["policy_hash"] == op["permit"]["policy_hash"] == p["policy_hash"]
            assert op["wire_hash"] == digest(op["wire"]) and op["permit_hash"] == digest(op["permit"])


@pytest.mark.parametrize("selected", CHOICES)
def test_normalizer_keeps_strict_output_bound_regardless_of_key(env, selected):
    change_policy(env, chat_output_parameter=selected, max_output_tokens=1)
    task = env.source_task()
    grant = env.grant(task)
    env.action(task, grant["execution"])
    final = env.wait(task, {"needs_reconciliation"})
    assert final["blocked_reason"] == "RECEIPT_OUT_OF_BOUNDS"
    assert final["operations"][0]["locally_accounted_cny"] is None and final["ledger"]["locally_accounted_cny"] == "0.000000"
    assert final["ledger"]["held_cny"] == grant["execution"]["quotes"][0]["per_call_hold_cny"]
    assert len(env.provider.calls) == final["ledger"]["call_count"] == 1
    body = json.loads(env.provider.calls[0].content)
    assert body[selected] == 1 and set(body).intersection(CHOICES) == {selected}


@pytest.mark.parametrize("selected", CHOICES)
def test_client_cannot_choose_parameter(env, selected):
    task = env.source_task()
    r = env.client.post(env.url(task) + "/quotes", json={"client_request_key": "client-selector-key", "expected_execution_revision": 0, "expected_task_revision": 1, PARAMETER: selected})
    assert r.status_code == 422 and env.provider.calls == []


def test_sink_rejects_missing_wrong_or_forged_wire_before_request(env):
    change_policy(env, chat_output_parameter="max_completion_tokens")
    task = env.source_task()
    granted = env.grant(task)
    env.action(task, granted["execution"])
    env.wait(task, {"plan_ready"})
    with env.runtime.store.transaction() as payload:
        original = copy.deepcopy(next(iter(payload["operations"].values())))
        p = copy.deepcopy(payload["quotes"][original["quote_id"]]["policy"])
    attempts = []
    for target in ["operation", "permit"]:
        for value in [None, "max_tokens"]:
            changed = copy.deepcopy(original)
            record = changed if target == "operation" else changed["permit"]
            if value is None:
                record.pop(PARAMETER)
            else:
                record[PARAMETER] = value
            changed["permit_hash"] = digest(changed["permit"])
            attempts.append(changed)
    for mode in ["wrong_key", "both_keys", "wrong_value"]:
        changed = copy.deepcopy(original)
        body = json.loads(changed["wire"])
        if mode == "wrong_key":
            body["max_tokens"] = body.pop("max_completion_tokens")
        elif mode == "both_keys":
            body["max_tokens"] = body["max_completion_tokens"]
        else:
            body["max_completion_tokens"] += 1
        changed["wire"] = json.dumps(body, ensure_ascii=False)
        changed["wire_hash"] = digest(changed["wire"])
        changed["permit"]["wire_hash"] = changed["wire_hash"]
        changed["permit_hash"] = digest(changed["permit"])
        attempts.append(changed)
    before = len(env.provider.calls)
    for changed in attempts:
        with pytest.raises(ResponseUnconfirmed):
            ControlledTransport(env.runtime.transport, env.runtime.input_bounder).perform(changed, p, "synthetic-secret")
    assert len(env.provider.calls) == before


def test_private_selector_mismatch_rejected_without_overwriting_history(env):
    change_policy(env, chat_output_parameter="max_completion_tokens")
    task = env.source_task()
    grant = env.grant(task)
    env.action(task, grant["execution"])
    env.wait(task, {"plan_ready"})
    path = env.runtime.store.root / "state.json"
    original = path.read_bytes()
    with pytest.raises(CreatorError) as error:
        with env.runtime.store.transaction() as payload:
            op = next(iter(payload["operations"].values()))
            op[PARAMETER] = "max_tokens"
    assert error.value.code == "CORRUPT_STORAGE"
    assert path.read_bytes() == original and len(env.provider.calls) == 2


def restore_legacy(env):
    fixture = json.loads(FIXTURE.read_text())
    assert fixture["synthetic_only"] is True
    original = base64.b64decode(fixture["journal_base64"])
    path = env.runtime.store.root / "state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(original)
    env.runtime.policy = ExecutionPolicy.model_validate({k: v for k, v in fixture["policy"].items() if k != "policy_hash"}).frozen()
    env.runtime.input_bounder = TrustedInputBounder(env.runtime.policy["policy_hash"], "synthetic-provider-cap-fixture", lambda wire, p: len(wire) + 1024)
    return fixture, path, original


def test_old_journal_read_projects_default_without_rewriting_any_byte(env):
    fixture, path, original = restore_legacy(env)
    state = env.observe(fixture["success_task"])
    assert state["status"] == "plan_ready"
    assert all(op[PARAMETER] == "max_tokens" for op in state["operations"])
    assert all(q["policy"][PARAMETER] == "max_tokens" for q in state["quotes"])
    assert path.read_bytes() == original and env.provider.calls == []
    with env.runtime.store.transaction() as payload:
        for op in payload["operations"].values():
            p = payload["quotes"][op["quote_id"]]["policy"]
            assert all(PARAMETER not in value for value in [p, op, op["permit"]])
            assert wire_for(op["context"], p).encode() == op["wire"].encode()
    assert path.read_bytes() == original


def test_legacy_confirmed_receipt_uses_own_parameter_and_rates_after_current_policy_change(env):
    fixture, path, original = restore_legacy(env)
    old_state = json.loads(original)
    task = fixture["received_task"]
    change_policy(env, chat_output_parameter="max_completion_tokens", input_cny_per_million="3", output_cny_per_million="5")
    before = env.observe(task)
    assert before["operations"][0][PARAMETER] == "max_tokens"
    response = env.action(task, before, "reconcile", "d11-legacy-local-reconcile")
    assert response.status_code == 200
    after = response.json()["execution"]
    assert after["ledger"]["locally_accounted_cny"] == "0.000050" and after["ledger"]["held_cny"] == "0.000000"
    assert after["operations"][0][PARAMETER] == "max_tokens" and after["blocked_reason"] == "POLICY_STALE"
    assert env.action(task, after, "resume", "d11-no-silent-new-parameter").status_code == 409
    assert env.provider.calls == []
    current = json.loads(path.read_text())
    assert current["frames"][:len(old_state["frames"])] == old_state["frames"]


@pytest.mark.parametrize("selected", CHOICES)
def test_confirmed_receipt_recovery_reuses_http_and_keeps_selected_parameter(env, monkeypatch, selected):
    import core.execution.runtime as module
    change_policy(env, chat_output_parameter=selected)
    actual = module.execute_tool

    def interrupted(*args):
        raise SystemExit("Synthetic interruption after durable response")

    monkeypatch.setattr(module, "execute_tool", interrupted)
    task = env.source_task()
    granted = env.grant(task)
    env.action(task, granted["execution"])
    stopped = env.wait(task, {"stopped"})
    assert stopped["operations"][0][PARAMETER] == selected and len(env.provider.calls) == 1
    monkeypatch.setattr(module, "execute_tool", actual)
    reconciled = env.action(task, stopped, "reconcile", "d11-confirmed-reconcile")
    assert reconciled.status_code == 200
    current = reconciled.json()["execution"]
    assert current["ledger"]["call_count"] == 1
    assert env.action(task, current, "resume", "d11-confirmed-resume").status_code == 200
    final = env.wait(task, {"plan_ready"})
    assert final["ledger"]["call_count"] == len(env.provider.calls) == 2
    assert all(op[PARAMETER] == selected for op in final["operations"])


def test_response_loop_stays_unchanged_and_has_no_chat_parameter(env):
    calls = install_provider(env)
    task = env.source_task()
    granted = env.grant(task)
    assert PARAMETER not in granted["execution"]["quotes"][0]["policy"]
    env.action(task, granted["execution"])
    final = env.wait(task, {"plan_ready"})
    assert len(calls) == final["ledger"]["call_count"] == 2
    assert all(PARAMETER not in op for op in final["operations"])
    for request in calls:
        body = json.loads(request.content)
        assert body["max_output_tokens"] == 2000 and not set(body).intersection(CHOICES)


@pytest.mark.parametrize("selected", CHOICES)
def test_changed_parameter_cannot_reuse_old_grant_or_old_bounder(env, selected):
    previous_bounder = env.runtime.input_bounder
    task = env.source_task()
    env.grant(task)
    change_policy(env, chat_output_parameter=selected)
    state = env.observe(task)
    assert state["grants"][0]["status"] == "policy_stale"
    assert env.action(task, state, "resume", "d11-no-old-grant").status_code == 409
    env.runtime.input_bounder = previous_bounder
    assert env.client.get("/api/planner/execution/policy").json()["blocked_reason"] == "POLICY_UNAVAILABLE"
    assert env.provider.calls == [] and env.observe(task)["ledger"]["call_count"] == 0


@pytest.mark.parametrize("selected", CHOICES)
@pytest.mark.parametrize("cap", [1, 128])
def test_over_limit_usage_is_never_normalized_as_free_or_given_an_offset(selected, cap):
    legacy = {key: value for key, value in policy().frozen().items() if key != "policy_hash"}
    p = selected_policy({**legacy, "max_output_tokens": cap}, selected)
    body = {"id": "synthetic-response", "model": p["model"], "choices": [{"message": {"content": "SYNTHETIC"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": cap + 1, "total_tokens": cap + 11}}
    raw = {"http_status": 200, "headers": {"x-request-id": "synthetic-request"}, "body_base64": base64.b64encode(json.dumps(body).encode()).decode()}
    result, error = normalized(raw, p, cost(p["max_input_tokens"], p["max_output_tokens"], p), 100)
    assert error == "RECEIPT_OUT_OF_BOUNDS" and result["local_units"] is None
