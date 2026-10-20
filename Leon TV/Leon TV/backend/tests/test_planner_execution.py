"""Execution control is real; only reviewed server policy and HTTP transport are synthetic."""
import copy
import json
import socket
import threading
import time
from dataclasses import dataclass
from datetime import timedelta

import httpx
import pytest
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient

from api.routers import creator, execution, planner
from core.creator.store import CreatorBriefStore, digest
from core.execution.policy import SCOPE, ExecutionPolicy, TrustedInputBounder, now
from core.execution.runtime import ExecutionRuntime
from core.execution.transport import ControlledTransport, ResponseUnconfirmed
from core.planner.actors import OfflineActor
from core.planner.runtime import PlannerRuntime

SCRIPT = "SYNTHETIC-PRIVATE-EXECUTION-SCRIPT Ａ\r\n第二行"
PREFIX = "/api/planner/tasks"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("No real outbound traffic in execution validation")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)


def policy(**changes):
    return ExecutionPolicy(id="synthetic-policy", version="v1", model="synthetic-text-model",
        endpoint="https://synthetic.example/v1/chat/completions", input_cny_per_million="1",
        output_cny_per_million="2", max_input_tokens=100000, max_output_tokens=2000,
        max_input_bytes=50000, max_output_bytes=65536, request_deadline_seconds=2,
        max_calls=6, task_limit_cny="0.700000", account_limit_cny="1.000000", account_max_calls=12,
        account_scope_id="synthetic-account", valid_until=(now() + timedelta(minutes=5)).isoformat(),
        pricing_basis="Synthetic fixed rates; no verified real provider", input_bound_basis="Synthetic provider enforces declared input cap",
        operator_approved=True, billing_bound_verified=True, validation_only=True, **changes)


class Provider:
    def __init__(self, fault=None, scenario="success"):
        self.calls, self.fault, self.scenario = [], fault, scenario
        self.entered, self.release = threading.Event(), threading.Event()

    def handle(self, request):
        self.calls.append(request)
        self.entered.set()
        if self.fault == "blocking":
            assert self.release.wait(6)
        if self.fault == "timeout":
            raise httpx.ReadTimeout("PRIVATE-UPSTREAM-ERROR")
        if self.fault == "deadline":
            time.sleep(2.3)
        body = json.loads(request.content)
        messages = []
        for message in body["messages"]:
            value = json.loads(message["content"])
            role = "tool" if isinstance(value, dict) and "ok" in value else message["role"]
            messages.append({"role": role, "content": value})
        content = OfflineActor().respond(messages, {"id": request.headers["x-client-operation-id"]}, {"scenario": self.scenario})
        value = {"id": "response-" + request.headers["x-client-operation-id"], "model": body["model"], "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
                 "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30}}
        if self.fault == "missing_usage":
            value.pop("usage")
        elif self.fault == "bool_usage":
            value["usage"] = {"prompt_tokens": True, "completion_tokens": 20, "total_tokens": 21}
        elif self.fault == "negative_usage":
            value["usage"] = {"prompt_tokens": -1, "completion_tokens": 20, "total_tokens": 19}
        elif self.fault == "over_input":
            value["usage"] = {"prompt_tokens": 100001, "completion_tokens": 20, "total_tokens": 100021}
        elif self.fault == "over_actual_bound":
            value["usage"] = {"prompt_tokens": 90000, "completion_tokens": 20, "total_tokens": 90020}
        elif self.fault == "over_output":
            value["usage"] = {"prompt_tokens": 10, "completion_tokens": 2001, "total_tokens": 2011}
        elif self.fault == "inconsistent_usage":
            value["usage"]["total_tokens"] = 1
        elif self.fault == "unknown_usage":
            value["usage"]["billed_extra_tokens"] = 100
        elif self.fault == "length":
            value["choices"][0]["finish_reason"] = "length"
        elif self.fault == "finish_unknown":
            value["choices"][0]["finish_reason"] = "private-model-finish"
        elif self.fault == "model":
            value["model"] = "other-channel-model"
        elif self.fault == "choices":
            value["choices"].append(copy.deepcopy(value["choices"][0]))
        elif self.fault == "oversized":
            value["choices"][0]["message"]["content"] = "x" * 65537
        elif self.fault == "secret_echo":
            value["id"] = "synthetic-secret"
            value["choices"][0]["message"]["content"] = "synthetic-secret"
        headers = {} if self.fault == "request_id" else {"x-request-id": "request-" + request.headers["x-client-operation-id"]}
        if self.fault == "duplicate_usage":
            wire = json.dumps(value).replace('"prompt_tokens": 10', '"prompt_tokens": 99999999, "prompt_tokens": 1').replace('"total_tokens": 30', '"total_tokens": 21')
            return httpx.Response(200, content=wire, headers=headers)
        if self.fault == "nonfinite":
            wire = json.dumps(value).replace('"prompt_tokens": 10', '"prompt_tokens": NaN')
            return httpx.Response(200, content=wire, headers=headers)
        return httpx.Response(500 if self.fault == "http" else 200, json=value, headers=headers)


def app_for(runtime):
    app = FastAPI()
    for router in [creator.router, planner.router, execution.router]:
        app.include_router(router)
    app.dependency_overrides[creator.get_brief_store] = lambda: runtime.planner.brief_store
    app.dependency_overrides[planner.get_planner_runtime] = lambda: runtime.planner
    app.dependency_overrides[execution.get_execution_runtime] = lambda: runtime
    return app


@dataclass
class Env:
    client: object
    runtime: object
    provider: object
    planner: object

    def source_task(self, suffix="001", offline=False):
        r = self.client.post("/api/creator/briefs", json={"client_request_key": "synthetic-brief-key-" + suffix, "script": SCRIPT, "requirements": "PRIVATE-REQUIREMENTS", "budget_cny": "100"})
        assert r.status_code == 201, r.text
        source = r.json()["brief"]
        r = self.client.post("/api/planner/offline-tasks" if offline else PREFIX, json={"client_request_key": "synthetic-task-key-" + suffix, "brief_id": source["id"], "brief_revision": 1})
        assert r.status_code == 201, r.text
        return r.json()["task"]

    def url(self, task):
        return f"{PREFIX}/{task['id']}/execution"

    def observe(self, task):
        r = self.client.get(self.url(task))
        assert r.status_code == 200, r.text
        return r.json()["execution"]

    def quote(self, task, revision=0, key="synthetic-quote-key-001"):
        r = self.client.post(self.url(task) + "/quotes", json={"client_request_key": key, "expected_execution_revision": revision, "expected_task_revision": task["revision"]})
        assert r.status_code == 201, r.text
        return r.json()

    def grant(self, task, quote=None, key="synthetic-grant-key-001"):
        quote = quote or self.quote(task)
        r = self.client.post(self.url(task) + "/grants", json={"client_request_key": key, "expected_execution_revision": quote["execution"]["revision"], "quote_id": quote["quote"]["id"],
            "accepted_disclosure_hash": quote["quote"]["disclosure_hash"], "accepted_scope": SCOPE, "consent": True})
        assert r.status_code == 201, r.text
        return r.json()

    def action(self, task, state, name="resume", key="synthetic-action-key-001"):
        return self.client.post(self.url(task) + "/actions", json={"client_action_key": key, "expected_execution_revision": state["revision"], "action": name})

    def wait(self, task, statuses, seconds=10):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            current = self.observe(task)
            if current["status"] in statuses:
                return current
            time.sleep(0.01)
        raise AssertionError("Execution did not reach expected state")


@pytest.fixture
def env(tmp_path):
    source_store = CreatorBriefStore(tmp_path.resolve() / "briefs")
    old = PlannerRuntime(tmp_path.resolve() / "planner", source_store)
    provider = Provider()
    test_policy = policy()
    bounder = TrustedInputBounder(test_policy.frozen()["policy_hash"], "synthetic-provider-cap-fixture", lambda wire, p: len(wire) + 1024)
    runtime = ExecutionRuntime(tmp_path.resolve() / "execution", old, policy=test_policy, enabled=True, api_key="synthetic-secret", transport=httpx.MockTransport(provider.handle), input_bounder=bounder)
    with TestClient(app_for(runtime)) as client:
        yield Env(client, runtime, provider, old)
    runtime.close()
    old.close()


def test_formal_registration_and_default_disabled_no_client(env, monkeypatch):
    from api.app import app
    assert any(route.path == "/api/planner/execution/policy" for route in app.routes)
    env.runtime.enabled = False
    task = env.source_task()
    monkeypatch.setattr(httpx, "Client", lambda *a, **k: pytest.fail("Disabled execution constructed a network client"))
    assert env.client.get("/api/planner/execution/policy").json()["blocked_reason"] == "LIVE_DISABLED"
    current = env.observe(task)
    assert current["revision"] == 0 and current["status"] == "not_authorized"
    r = env.client.post(env.url(task) + "/quotes", json={"client_request_key": "disabled-quote-key", "expected_execution_revision": 0, "expected_task_revision": 1})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "LIVE_DISABLED"
    assert env.provider.calls == []


def test_real_quote_grant_loop_ledger_and_legacy_unchanged(env):
    task = env.source_task()
    quote = env.quote(task)
    assert quote["quote"]["policy"]["model"] == "synthetic-text-model" and env.provider.calls == []
    grant = env.grant(task, quote)
    assert grant["execution"]["status"] == "ready" and env.provider.calls == []
    assert env.action(task, grant["execution"]).status_code == 200
    final = env.wait(task, {"plan_ready"})
    assert final["environment"] == "synthetic_test" and final["quality_status"] == "not_reviewed"
    assert final["ledger"]["call_count"] == len(env.provider.calls) == 2

    assert final["ledger"]["held_cny"] == "0.000000" and final["ledger"]["locally_accounted_cny"] == "0.000100"
    assert final["ledger"]["provider_bill_confirmed"] is False and final["plan"]["source_snapshot_hash"] == task["source"]["snapshot_hash"]
    assert env.client.get(f"{PREFIX}/{task['id']}").json()["task"]["status"] == "awaiting_authorization"
    assert all(op["receipt_status"] == "confirmed_local" and op["provider_request_id"] and op["provider_response_id"] for op in final["operations"])
    assert all(key not in final for key in ["raw", "messages", "context", "wire", "normalized"])
    assert "synthetic-secret" not in json.dumps(final)
    events = env.client.get(env.url(task) + "/events?limit=200").json()["events"]
    assert [event["sequence"] for event in events] == list(range(1, len(events) + 1))
    assert "SYNTHETIC-PRIVATE" not in json.dumps(events)


def change_policy(env, **changes):
    value = {key: copy.deepcopy(value) for key, value in env.runtime.policy.items() if key != "policy_hash"}
    value.update(changes)
    current = ExecutionPolicy.model_validate(value)
    env.runtime.policy = current.frozen()
    env.runtime.input_bounder = TrustedInputBounder(current.frozen()["policy_hash"], "synthetic-provider-cap-fixture", lambda wire, p: len(wire) + 1024)


def test_actual_input_bound_checked_before_hold_intent_or_http(env):
    change_policy(env, max_input_tokens=1)
    task = env.source_task()
    grant = env.grant(task)
    assert env.action(task, grant["execution"]).status_code == 200
    final = env.wait(task, {"blocked"})
    assert final["blocked_reason"] == "CONTEXT_LIMIT"
    assert final["ledger"]["call_count"] == 0 and final["ledger"]["held_cny"] == "0.000000"
    assert final["operations"] == [] and env.provider.calls == []


@pytest.mark.parametrize("result", [None, True, -1, 0, 100001])
def test_invalid_or_over_token_bounder_result_never_dispatches(env, result):
    env.runtime.input_bounder = TrustedInputBounder(env.runtime.policy["policy_hash"], "synthetic-bad-bound", lambda wire, p: result)
    task = env.source_task()
    grant = env.grant(task)
    env.action(task, grant["execution"])
    final = env.wait(task, {"blocked"})
    assert final["ledger"]["call_count"] == 0 and final["operations"] == [] and env.provider.calls == []


def test_missing_trusted_bounder_is_policy_unavailable(env):
    env.runtime.input_bounder = None
    task = env.source_task()
    assert env.client.get("/api/planner/execution/policy").json()["blocked_reason"] == "POLICY_UNAVAILABLE"
    response = env.client.post(env.url(task) + "/quotes", json={"client_request_key": "unbound-quote-key", "expected_execution_revision": 0, "expected_task_revision": 1})
    assert response.status_code == 409 and response.json()["detail"]["code"] == "POLICY_UNAVAILABLE"
    assert env.provider.calls == []


def test_policy_scope_identity_cannot_reset_global_budget_or_projection(env):
    env.provider.fault = "timeout"
    task = env.source_task()
    grant = env.grant(task)
    env.action(task, grant["execution"])
    previous = env.wait(task, {"needs_reconciliation"})
    change_policy(env, version="v2", account_scope_id="new-unapproved-account")
    observed = env.observe(task)
    assert observed["ledger"] == previous["ledger"] and observed["policy_available"] is False
    other = env.source_task("002")
    for selected in [task, other]:
        state = env.observe(selected)
        r = env.client.post(env.url(selected) + "/quotes", json={"client_request_key": "new-account-key-" + selected["id"], "expected_execution_revision": state["revision"], "expected_task_revision": selected["revision"]})
        assert r.status_code == 409 and r.json()["detail"]["code"] == "POLICY_UNAVAILABLE"
    assert len(env.provider.calls) == 1


def test_new_grant_does_not_erase_unknown_hold_or_reset_calls(env):
    env.provider.fault = "timeout"
    task = env.source_task()
    grant = env.grant(task)
    env.action(task, grant["execution"])
    unknown = env.wait(task, {"needs_reconciliation"})
    quote = env.quote(task, unknown["revision"], key="replacement-quote-key")
    replacement = env.grant(task, quote, key="replacement-grant-key")
    state = replacement["execution"]
    assert state["ledger"] == unknown["ledger"]
    assert state["status"] == "needs_reconciliation" and "resume" not in state["allowed_actions"]
    assert env.action(task, state, key="no-reset-resume-key").status_code == 409 and len(env.provider.calls) == 1


@pytest.mark.parametrize("scenario,calls,status", [("repair", 3, "plan_ready"), ("invalid_tool", 2, "blocked"), ("step_limit", 6, "blocked")])
def test_bounded_feedback_loop_preserves_real_cost_and_call_limits(env, scenario, calls, status):
    env.provider.scenario = scenario
    task = env.source_task()
    grant = env.grant(task)
    env.action(task, grant["execution"])
    final = env.wait(task, {status})
    assert final["ledger"]["call_count"] == len(env.provider.calls) == calls
    assert final["ledger"]["held_cny"] == "0.000000"
    assert final["ledger"]["locally_accounted_cny"] == f"{calls * 50 / 1000000:.6f}"
    assert final["quality_status"] == "not_reviewed"


def test_absolute_deadline_keeps_unknown_hold_even_after_late_response(env):
    change_policy(env, request_deadline_seconds=1)
    env.provider.fault = "deadline"
    task = env.source_task()
    grant = env.grant(task)
    started = time.monotonic()
    env.action(task, grant["execution"])
    final = env.wait(task, {"needs_reconciliation"}, seconds=3)
    assert time.monotonic() - started < 1.6
    assert final["ledger"]["held_cny"] == "0.104000" and final["ledger"]["call_count"] == 1
    time.sleep(1.5)
    assert env.observe(task)["ledger"] == final["ledger"] and len(env.provider.calls) == 1


def test_expiry_blocks_new_calls_without_consuming_budget(env, monkeypatch):
    import core.execution.runtime as module
    task = env.source_task()
    env.grant(task)
    future = now() + timedelta(minutes=10)
    monkeypatch.setattr(module, "now", lambda: future)
    state = env.observe(task)
    assert state["grants"][0]["status"] == "expired"
    assert env.action(task, state).status_code == 409
    assert state["ledger"]["call_count"] == 0 and env.provider.calls == []


def test_account_hold_limit_across_two_runtime_instances(env):
    from concurrent.futures import ThreadPoolExecutor
    change_policy(env, task_limit_cny="0.104000", account_limit_cny="0.104000")
    env.provider.fault = "timeout"
    first, second = env.source_task(), env.source_task("002")
    one = env.grant(first)
    two = env.grant(second, env.quote(second, key="quote-second-task-key"), key="grant-second-task-key")
    twin = ExecutionRuntime(env.runtime.store.root, env.planner, policy=ExecutionPolicy.model_validate({k: v for k, v in env.runtime.policy.items() if k != "policy_hash"}),
        enabled=True, api_key="synthetic-secret", transport=env.runtime.transport, input_bounder=env.runtime.input_bounder)
    try:
        def submit_twin():
            from core.creator.store import CreatorError
            try:
                return twin.action(second["id"], {"client_action_key": "second-resume-key", "expected_execution_revision": two["execution"]["revision"], "action": "resume"})
            except CreatorError as error:
                assert error.code == "ACTION_NOT_ALLOWED"
                return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            a = pool.submit(env.action, first, one["execution"])
            b = pool.submit(submit_twin)
            assert a.result().status_code in {200, 409}
            b.result()
        first_state = env.wait(first, {"needs_reconciliation", "blocked"})
        second_state = env.wait(second, {"needs_reconciliation", "blocked"})
        assert len(env.provider.calls) == 1
        assert first_state["ledger"]["account_held_cny"] == second_state["ledger"]["account_held_cny"] == "0.104000"
        assert first_state["ledger"]["account_call_count"] == 1
        assert first_state["ledger"]["call_count"] + second_state["ledger"]["call_count"] == 1
        untouched = env.observe(env.source_task("003"))
        assert untouched["revision"] == 0 and untouched["ledger"]["call_count"] == 0
        assert untouched["ledger"]["account_held_cny"] == "0.104000" and untouched["ledger"]["account_call_count"] == 1
    finally:
        twin.close()


@pytest.mark.parametrize("target", ["state.json", ".state.lock", "execution"])
def test_execution_symlinks_stop_without_modifying_outside(env, tmp_path, target):
    task = env.source_task()
    grant = env.grant(task)
    outside = tmp_path / "outside"
    outside.write_text("PRIVATE-OUTSIDE")
    path = env.runtime.store.root / (f".{task['id']}.execution.lock" if target == "execution" else target)
    path.unlink(missing_ok=True)
    path.symlink_to(outside)
    r = env.action(task, grant["execution"]) if target == "execution" else env.client.get(env.url(task))
    assert r.status_code == 503 and "PRIVATE-OUTSIDE" not in r.text and outside.read_text() == "PRIVATE-OUTSIDE"


def test_atomic_write_failure_keeps_quote_key_available_for_same_retry(env, monkeypatch):
    task = env.source_task()
    env.observe(task)
    path = env.runtime.store.root / "state.json"
    before = path.read_bytes()
    request = {"client_request_key": "atomic-quote-key", "expected_execution_revision": 0, "expected_task_revision": 1}
    with monkeypatch.context() as patcher:
        def fail(*args):
            raise OSError("PRIVATE-WRITE-FAILURE")
        patcher.setattr("core.execution.store.os.replace", fail)
        r = env.client.post(env.url(task) + "/quotes", json=request)
    assert r.status_code == 503 and "PRIVATE-WRITE" not in r.text and path.read_bytes() == before
    assert list(env.runtime.store.root.glob(".execution-*")) == []
    retry = env.client.post(env.url(task) + "/quotes", json=request)
    assert retry.status_code == 201 and not retry.json()["replayed"]


@pytest.mark.parametrize("change", [{"consent": False}, {"consent": 1}, {"accepted_scope": "all-files"}, {"accepted_disclosure_hash": "f" * 64}, {"total_limit_cny": "0"}, {"usage": {"input_tokens": 0}}])
def test_grant_scope_and_disclosure_cannot_be_forged(env, change):
    task = env.source_task()
    quote = env.quote(task)
    request = {"client_request_key": "forged-consent-key", "expected_execution_revision": quote["execution"]["revision"],
        "quote_id": quote["quote"]["id"], "accepted_disclosure_hash": quote["quote"]["disclosure_hash"], "accepted_scope": SCOPE, "consent": True, **change}
    r = env.client.post(env.url(task) + "/grants", json=request)
    assert r.status_code in {409, 422} and env.observe(task)["grants"] == [] and env.provider.calls == []


@pytest.mark.parametrize("fault", ["timeout", "http", "missing_usage", "bool_usage", "negative_usage", "over_input", "over_actual_bound", "over_output", "inconsistent_usage", "unknown_usage", "length", "finish_unknown", "model", "choices", "oversized", "request_id", "duplicate_usage", "nonfinite", "secret_echo"])
def test_unconfirmed_receipts_never_release_hold_or_retry(env, fault):
    env.provider.fault = fault
    task = env.source_task()
    grant = env.grant(task)
    env.action(task, grant["execution"])
    final = env.wait(task, {"needs_reconciliation"})
    assert final["ledger"]["call_count"] == len(env.provider.calls) == 1
    assert final["ledger"]["held_cny"] == "0.104000" and final["ledger"]["locally_accounted_cny"] == "0.000000"
    assert final["operations"][0]["locally_accounted_cny"] is None and "resume" not in final["allowed_actions"]
    stopped = env.action(task, final, "stop", "stop-unknown-key-001")
    assert stopped.status_code == 200
    state = stopped.json()["execution"]
    assert state["blocked_reason"] == "RESPONSE_UNCONFIRMED" and state["ledger"]["held_cny"] == "0.104000"
    assert env.action(task, state, "resume", "no-resend-key-001").status_code == 409
    if "reconcile" in state["allowed_actions"]:
        replay = env.action(task, state, "reconcile", "local-reconcile-key-001")
        assert replay.status_code == 200 and replay.json()["execution"]["ledger"]["held_cny"] == "0.104000"
    assert len(env.provider.calls) == 1
    assert "synthetic-secret" not in json.dumps(env.observe(task))


@pytest.mark.parametrize("extra", [{"model": "bypass"}, {"endpoint": "https://attacker.example"}, {"price": "0"}, {"budget_cny": "100"}, {"source": SCRIPT}, {"authorization_status": "authorized"}, {"expected_task_revision": True}])
def test_quote_rejects_client_policy_or_source(env, extra):
    task = env.source_task()
    r = env.client.post(env.url(task) + "/quotes", json={"client_request_key": "forged-quote-key-001", "expected_execution_revision": 0, "expected_task_revision": 1, **extra})
    assert r.status_code == 422 and "SYNTHETIC-PRIVATE" not in r.text and env.provider.calls == []


def test_offline_never_upgrades(env):
    task = env.source_task(offline=True)
    for path in ["", "/quotes/by-key/missing-valid-key", "/grants/by-key/missing-valid-key"]:
        r = env.client.get(env.url(task) + path)
        assert r.status_code == 409 and r.json()["detail"]["code"] == "TASK_NOT_ELIGIBLE"
    assert env.provider.calls == []


def test_quote_grant_revoke_idempotency_and_no_automatic_execution(env):
    task = env.source_task()
    q = env.quote(task)
    request = {"client_request_key": "synthetic-quote-key-001", "expected_execution_revision": 0, "expected_task_revision": 1}
    replay = env.client.post(env.url(task) + "/quotes", json=request)
    assert replay.status_code == 200 and replay.json()["replayed"] and replay.json()["quote"]["id"] == q["quote"]["id"]
    assert env.client.post(env.url(task) + "/quotes", json={**request, "expected_task_revision": 2}).status_code == 409
    assert env.client.get(env.url(task) + "/quotes/by-key/synthetic-quote-key-001").json()["quote"]["id"] == q["quote"]["id"]
    grant = env.grant(task, q)
    revoke = {"client_action_key": "revoke-key-001", "expected_execution_revision": grant["execution"]["revision"]}
    url = env.url(task) + f"/grants/{grant['grant']['id']}/revoke"
    result = env.client.post(url, json=revoke)
    assert result.status_code == 200 and result.json()["grant"]["status"] == "revoked"
    again = env.client.post(url, json=revoke)
    assert again.json()["replayed"] and again.json()["applied_revision"] == result.json()["applied_revision"]
    state = env.observe(task)
    assert env.action(task, state).status_code == 409 and env.provider.calls == []


def test_sink_binds_actual_bytes_context_and_every_permit_dimension(env):
    task = env.source_task()
    grant = env.grant(task)
    env.action(task, grant["execution"])
    env.wait(task, {"plan_ready"})
    with env.runtime.store.transaction() as payload:
        original = copy.deepcopy(next(iter(payload["operations"].values())))
        p = copy.deepcopy(next(iter(payload["quotes"].values()))["policy"])
    count = len(env.provider.calls)
    for field in ["wire", "context", "task_id", "snapshot_hash", "grant_id", "quote_id", "account_id"]:
        changed = copy.deepcopy(original)
        changed[field] = changed[field] + "x" if isinstance(changed[field], str) else changed[field] + [{"role": "user", "content": "PRIVATE-ALTERED"}]
        with pytest.raises(ResponseUnconfirmed):
            ControlledTransport(env.runtime.transport, env.runtime.input_bounder).perform(changed, p, "synthetic-secret")
    for field in ["max_input_bytes", "max_output_tokens", "currency", "deadline_at"]:
        changed = copy.deepcopy(original)
        changed["permit"][field] = 1 if field in {"max_input_bytes", "max_output_tokens"} else "USD" if field == "currency" else (now() + timedelta(days=2)).isoformat()
        changed["permit_hash"] = digest(changed["permit"])
        with pytest.raises(ResponseUnconfirmed):
            ControlledTransport(env.runtime.transport, env.runtime.input_bounder).perform(changed, p, "synthetic-secret")
    assert len(env.provider.calls) == count


def test_revoke_during_submitted_call_blocks_only_next_submit_and_keeps_receipt(env):
    env.provider.fault = "blocking"
    task = env.source_task()
    grant = env.grant(task)
    env.action(task, grant["execution"])
    try:
        assert env.provider.entered.wait(2)
        during = env.observe(task)
        assert during["ledger"]["held_cny"] == "0.104000" and during["ledger"]["call_count"] == 1
        r = env.client.post(env.url(task) + f"/grants/{grant['grant']['id']}/revoke", json={"client_action_key": "revoke-inflight-key", "expected_execution_revision": during["revision"]})
        assert r.status_code == 200
    finally:
        env.provider.release.set()
    final = env.wait(task, {"blocked"})
    assert final["blocked_reason"] == "GRANT_REVOKED" and len(env.provider.calls) == 1
    assert final["ledger"]["held_cny"] == "0.000000" and final["ledger"]["locally_accounted_cny"] == "0.000050"


def test_journal_damage_and_single_tail_delete_fail_closed(env):
    task = env.source_task()
    grant = env.grant(task)
    path = env.runtime.store.root / "state.json"
    state = json.loads(path.read_text())
    state["frames"].pop()
    path.write_text(json.dumps(state))
    damaged = path.read_bytes()
    r = env.client.get(env.url(task))
    assert r.status_code == 503 and r.json()["detail"]["code"] == "CORRUPT_STORAGE"
    assert env.action(task, grant["execution"]).status_code == 503 and path.read_bytes() == damaged


@pytest.mark.parametrize("relation", ["parent", "same", "child"])
def test_execution_private_tree_static_guard(env, relation):
    task = env.source_task()
    root = env.runtime.store.root
    target = {"parent": root.parent, "same": root, "child": root / "child"}[relation]
    app = app_for(env.runtime)
    nested = FastAPI()
    nested.mount("/files", StaticFiles(directory=target, check_dir=False))
    app.mount("/nested", nested)
    with TestClient(app) as exposed:
        r = exposed.get(env.url(task))
        assert r.status_code == 503
    assert env.provider.calls == []


def test_receipt_persisted_before_tools_and_recovery_does_not_repeat_http(env, monkeypatch):
    import core.execution.runtime as module
    real_tool = module.execute_tool
    def interrupted(*args):
        raise SystemExit("Synthetic crash after durable receipt")
    monkeypatch.setattr(module, "execute_tool", interrupted)
    task = env.source_task()
    grant = env.grant(task)
    env.action(task, grant["execution"])
    stopped = env.wait(task, {"stopped"})
    assert stopped["ledger"]["held_cny"] == "0.104000" and len(env.provider.calls) == 1
    monkeypatch.setattr(module, "execute_tool", real_tool)
    reconciled = env.action(task, stopped, "reconcile", "reuse-receipt-key-001")
    assert reconciled.status_code == 200
    state = reconciled.json()["execution"]
    assert state["ledger"]["held_cny"] == "0.000000" and state["ledger"]["call_count"] == 1
    assert env.action(task, state, "resume", "resume-confirmed-key").status_code == 200
    final = env.wait(task, {"plan_ready"})
    assert final["ledger"]["call_count"] == len(env.provider.calls) == 2


CHILD_CODE = """
import json,os,sys,time,httpx
from core.creator.store import CreatorBriefStore
from core.planner.runtime import PlannerRuntime
from core.execution.policy import ExecutionPolicy,TrustedInputBounder
from core.execution.runtime import ExecutionRuntime
from core.execution.transport import ControlledTransport
from core.creator.store import CreatorError
root,planner_root,brief_root,policy_text,task_id,revision,mode,trace=sys.argv[1:]
p=ExecutionPolicy.model_validate(json.loads(policy_text))
bound=TrustedInputBounder(p.frozen()['policy_hash'],'synthetic-provider-cap-fixture',lambda wire,policy:len(wire)+1024)
def send(request):
 with open(trace,'a') as stream:
  stream.write(request.headers['x-client-operation-id']+'\\n')
 if mode=='race':
  raise httpx.ReadTimeout('synthetic unknown')
 body=json.loads(request.content)
 value={'id':'child-response','model':body['model'],'choices':[{'message':{'content':'{"tool":"read_frozen_brief","arguments":{}}'},'finish_reason':'stop'}],
        'usage':{'prompt_tokens':10,'completion_tokens':20,'total_tokens':30}}
 return httpx.Response(200,json=value,headers={'x-request-id':'child-request'})
old=PlannerRuntime(planner_root,CreatorBriefStore(brief_root))
runtime=ExecutionRuntime(root,old,policy=p,enabled=True,api_key='synthetic-secret',transport=httpx.MockTransport(send),input_bounder=bound)
if mode=='intent':
 ControlledTransport.perform=lambda *args:os._exit(71)
elif mode=='received':
 runtime.process_receipt=lambda *args:os._exit(71)
try:
 try:
  runtime.action(task_id,{'client_action_key':'child-action-key-'+task_id,'expected_execution_revision':int(revision),'action':'resume'})
 except CreatorError as error:
  assert error.code=='ACTION_NOT_ALLOWED'
 deadline=time.monotonic()+5
 while time.monotonic()<deadline:
  state=runtime.get(task_id)['execution']
  if state['status'] not in {'queued','running'}:
   print(json.dumps({'status':state['status'],'calls':state['ledger']['account_call_count']}))
   break
  time.sleep(.01)
finally:
 runtime.close()
 old.close()
"""


def child_args(env, task, state, mode, trace):
    import sys
    return [sys.executable, "-c", CHILD_CODE, str(env.runtime.store.root), str(env.planner.store.root), str(env.planner.brief_store.root),
        json.dumps({key: value for key, value in env.runtime.policy.items() if key != "policy_hash"}), task["id"], str(state["revision"]), mode, str(trace)]


@pytest.mark.parametrize("mode", ["intent", "received"])
def test_real_process_exit_after_durable_phase_does_not_repeat_http(env, tmp_path, mode):
    import subprocess
    if mode == "intent":
        change_policy(env, task_limit_cny="0.104000", account_limit_cny="0.104000")
    task = env.source_task()
    grant = env.grant(task)
    trace = tmp_path / "http-call-trace"
    child = subprocess.run(child_args(env, task, grant["execution"], mode, trace), capture_output=True, text=True, timeout=12)
    assert child.returncode == 71, child.stdout + child.stderr
    state = env.observe(task)
    assert state["ledger"]["call_count"] == 1 and state["ledger"]["held_cny"] == "0.104000" and env.provider.calls == []
    if mode == "intent":
        assert state["status"] == "needs_reconciliation" and not trace.exists()
        assert state["operations"][0]["status"] == "needs_reconciliation" and state["operations"][0]["receipt_status"] == "unconfirmed"
        assert env.action(task, state, "resume", "no-retry-after-exit").status_code == 409
        other = env.source_task("after-crash")
        next_grant = env.grant(other, env.quote(other, key="new-quote-after-crash"), key="new-grant-after-crash")
        assert env.action(other, next_grant["execution"], key="new-task-no-reset").status_code == 409
        assert env.provider.calls == []
    else:
        assert state["status"] == "stopped" and len(trace.read_text().splitlines()) == 1
        reconciled = env.action(task, state, "reconcile", "process-receipt-reuse").json()["execution"]
        assert reconciled["ledger"]["held_cny"] == "0.000000" and reconciled["ledger"]["locally_accounted_cny"] == "0.000050"
        assert env.action(task, reconciled, "resume", "resume-after-process").status_code == 200
        final = env.wait(task, {"plan_ready"})
        assert final["ledger"]["call_count"] == 2 and len(env.provider.calls) == 1
        assert len({op["id"] for op in final["operations"]}) == 2


def test_cross_process_account_gate_allows_only_one_hold_and_one_transport(env, tmp_path):
    import subprocess
    change_policy(env, task_limit_cny="0.104000", account_limit_cny="0.104000")
    first, second = env.source_task(), env.source_task("002")
    first_grant = env.grant(first)
    second_grant = env.grant(second, env.quote(second, key="race-quote-second"), key="race-grant-second")
    trace = tmp_path / "race-transport-trace"
    processes = [subprocess.Popen(child_args(env, task, grant["execution"], "race", trace), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        for task, grant in [(first, first_grant), (second, second_grant)]]
    for process in processes:
        stdout, stderr = process.communicate(timeout=12)
        assert process.returncode == 0, stdout + stderr
    records = trace.read_text().splitlines()
    assert len(records) == len(set(records)) == 1
    one, two = env.observe(first), env.observe(second)
    assert one["ledger"]["account_call_count"] == two["ledger"]["account_call_count"] == 1
    assert one["ledger"]["account_held_cny"] == two["ledger"]["account_held_cny"] == "0.104000"
    assert one["ledger"]["call_count"] + two["ledger"]["call_count"] == 1
    assert env.provider.calls == []


def test_delta_journal_twelve_task_growth_and_rebuild(env, record_testsuite_property):
    change_policy(env, account_limit_cny="10.000000", account_max_calls=100)
    snapshots = []
    half_size = 0
    for index in range(12):
        task = env.source_task(f"growth-{index:02}")
        grant = env.grant(task, env.quote(task, key=f"growth-quote-{index:02}"), key=f"growth-grant-{index:02}")
        assert env.action(task, grant["execution"], key=f"growth-resume-{index:02}").status_code == 200
        final = env.wait(task, {"plan_ready"})
        snapshots.append((task, final))
        if index == 5:
            half_size = (env.runtime.store.root / "state.json").stat().st_size
    path = env.runtime.store.root / "state.json"
    total_size = path.stat().st_size
    assert total_size < 4_000_000 and total_size < half_size * 2.6
    raw = json.loads(path.read_text())
    assert raw["schema_version"] == 2 and all("payload" not in frame and "delta" in frame for frame in raw["frames"])
    started = time.perf_counter()
    assert env.observe(snapshots[-1][0]) == snapshots[-1][1]
    elapsed = time.perf_counter() - started
    assert elapsed < 0.75
    env.runtime.close()
    restored = ExecutionRuntime(env.runtime.store.root, env.planner,
        policy=ExecutionPolicy.model_validate({key: value for key, value in env.runtime.policy.items() if key != "policy_hash"}), enabled=True,
        api_key="synthetic-secret", transport=env.runtime.transport, input_bounder=env.runtime.input_bounder)
    try:
        for task, expected in snapshots:
            # Account totals are shared and increase after each saved snapshot.
            current = restored.get(task["id"])["execution"]
            assert current["operations"] == expected["operations"] and current["plan"] == expected["plan"]
            assert current["revision"] == expected["revision"] and current["ledger"]["call_count"] == 2
        assert len(env.provider.calls) == 24
    finally:
        restored.close()
    for name, value in {"growth_tasks": 12, "growth_operations": 24, "growth_six_task_bytes": half_size,
                        "growth_twelve_task_bytes": total_size, "growth_get_seconds": elapsed, "growth_frames": len(raw["frames"])}.items():
        record_testsuite_property(name, str(value))


@pytest.mark.parametrize("damage", ["schema1", "source", "operation", "grant", "key", "duplicate", "deep_json", "invalid_utf8"])
def test_delta_history_corruption_stops_read_and_write_without_migration(env, damage):
    task = env.source_task()
    grant = env.grant(task)
    env.action(task, grant["execution"])
    final = env.wait(task, {"plan_ready"})
    path = env.runtime.store.root / "state.json"
    state = json.loads(path.read_text())
    if damage == "schema1":
        state["schema_version"] = 1
    elif damage in {"source", "operation", "grant", "key"}:
        namespace = {"source": "tasks", "operation": "operations", "grant": "grants", "key": "keys"}[damage]
        records = next(frame["delta"][namespace] for frame in state["frames"] if namespace in frame["delta"])
        record = next(iter(records.values()))
        if damage == "source":
            record["source"]["script"] += "PRIVATE-CORRUPT"
        elif damage == "operation":
            record["hold_units"] = 0
        elif damage == "grant":
            record["total_limit_cny"] = "999.000000"
        else:
            record["applied_revision"] = 999
    if damage == "duplicate":
        path.write_text('{"schema_version":2,"schema_version":2,"head_revision":0,"head_hash":null,"frames":[]}')
    elif damage == "deep_json":
        path.write_text("[" * 10000 + "0" + "]" * 10000)
    elif damage == "invalid_utf8":
        path.write_bytes(b"\xffPRIVATE-CORRUPT")
    else:
        path.write_text(json.dumps(state))
    damaged = path.read_bytes()
    for url in [env.url(task), env.url(task) + "/events", env.url(task) + "/quotes/by-key/synthetic-quote-key-001"]:
        result = env.client.get(url)
        assert result.status_code == 503 and result.json()["detail"]["code"] == "CORRUPT_STORAGE"
        assert "PRIVATE-CORRUPT" not in result.text and path.read_bytes() == damaged
    assert env.action(task, final).status_code == 503 and path.read_bytes() == damaged


def test_policy_amount_projection_is_six_decimal_and_disclosure_roundtrip_stable(env):
    change_policy(env, task_limit_cny="0.5", account_limit_cny="1")
    visible = env.client.get("/api/planner/execution/policy").json()["policy"]
    assert visible["task_limit_cny"] == "0.500000" and visible["account_limit_cny"] == "1.000000"
    task = env.source_task()
    quote = env.quote(task)
    assert quote["quote"]["policy"]["task_limit_cny"] == "0.500000"
    stored_hash = env.runtime.policy["policy_hash"]
    assert quote["quote"]["policy"]["policy_hash"] == stored_hash
    assert env.grant(task, quote)["grant"]["disclosure_hash"] == quote["quote"]["disclosure_hash"]
    assert env.provider.calls == []


def test_decimal_local_cost_rounds_up_to_micro_cny(env):
    change_policy(env, input_cny_per_million="0.000001", output_cny_per_million="0.000001")
    task = env.source_task()
    grant = env.grant(task)
    env.action(task, grant["execution"])
    final = env.wait(task, {"plan_ready"})
    assert final["ledger"]["locally_accounted_cny"] == "0.000002"
    assert all(op["hold_cny"] == op["locally_accounted_cny"] == "0.000001" for op in final["operations"])
