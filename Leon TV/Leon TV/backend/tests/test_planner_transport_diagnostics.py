"""D12 private diagnostics; actual runtime with synthetic HTTP failures only."""
import copy
import json
import threading
from datetime import timedelta

import httpx
import pytest
from fastapi.testclient import TestClient
from test_planner_chat_output import restore_legacy
from test_planner_execution import app_for, change_policy
from test_planner_execution import env as env
from test_planner_execution import no_network as no_network

from core.creator.store import CreatorError, digest
from core.execution.policy import ExecutionPolicy, now
from core.execution.runtime import ExecutionRuntime
from core.execution.store import ExecutionStore
from core.execution.transport import ControlledTransport, ResponseUnconfirmed

PRIVATE_ERROR = "SYNTHETIC-PRIVATE-EXCEPTION https://secret.invalid/?key=synthetic-secret"
TIMEOUTS = [
    (httpx.ConnectTimeout, "CONNECT_TIMEOUT", "connect"),
    (httpx.ReadTimeout, "READ_TIMEOUT", "read"),
    (httpx.WriteTimeout, "WRITE_TIMEOUT", "write"),
    (httpx.PoolTimeout, "POOL_TIMEOUT", "pool"),
    (httpx.ConnectError, "TRANSPORT_ERROR", "unknown"),
    (httpx.TimeoutException, "TRANSPORT_ERROR", "unknown"),
    (AssertionError, "TRANSPORT_ERROR", "unknown"),
]


def private_operation(env):
    with env.runtime.store.transaction() as payload:
        operation = copy.deepcopy(next(iter(payload["operations"].values())))
        policy = copy.deepcopy(payload["quotes"][operation["quote_id"]]["policy"])
    return operation, policy


def start_failure(env, error=httpx.ConnectTimeout):
    calls = []

    def fail(request):
        calls.append(request)
        raise error(PRIVATE_ERROR)

    env.runtime.transport = httpx.MockTransport(fail)
    task = env.source_task()
    grant = env.grant(task)
    assert env.action(task, grant["execution"]).status_code == 200
    final = env.wait(task, {"needs_reconciliation"})
    return task, grant, final, calls


def assert_unknown(env, task, grant, final, calls, code, phase):
    assert final["blocked_reason"] == "RESPONSE_UNCONFIRMED"
    assert final["ledger"]["call_count"] == len(calls) == 1
    assert final["ledger"]["held_cny"] == grant["execution"]["quotes"][0]["per_call_hold_cny"]
    assert final["ledger"]["locally_accounted_cny"] == "0.000000"
    assert final["ledger"]["provider_bill_confirmed"] is False
    assert final["operations"][0]["locally_accounted_cny"] is None
    assert final["operations"][0]["receipt_status"] == "unconfirmed"
    assert "resume" not in final["allowed_actions"] and "reconcile" not in final["allowed_actions"]
    operation, _ = private_operation(env)
    assert operation["transport_diagnostic"] == {"code": code, "phase": phase}
    assert operation["raw"] is None and operation["normalized"] is None
    assert operation["local_units"] is None and operation["status"] == "needs_reconciliation"
    public = [final, env.client.get(env.url(task) + "/events?limit=200").json(), env.runtime.policy_view()]
    assert "transport_diagnostic" not in json.dumps(public)
    assert PRIVATE_ERROR not in (env.runtime.store.root / "state.json").read_text()
    assert "secret.invalid" not in json.dumps(public)


@pytest.mark.parametrize("error,code,phase", TIMEOUTS)
def test_timeout_type_reaches_private_operation_without_changing_unknown_hold(env, error, code, phase):
    task, grant, final, calls = start_failure(env, error)
    assert_unknown(env, task, grant, final, calls, code, phase)


@pytest.mark.parametrize("untrusted", [PRIVATE_ERROR, None, True, [], {"code": PRIVATE_ERROR}])
def test_exception_itself_carries_only_fixed_values(untrusted):
    error = ResponseUnconfirmed(untrusted)
    assert error.diagnostic == {"code": "TRANSPORT_ERROR", "phase": "unknown"}
    assert error.args == ("TRANSPORT_ERROR",) and str(error) == "TRANSPORT_ERROR"


@pytest.mark.parametrize("malformation", [
    "operation_none", "missing_permit", "permit_none", "bad_deadline", "bad_issued", "bad_expiry",
    "missing_binding", "bad_protocol", "wire_mismatch", "missing_bounder", "bounder_failure",
])
def test_preflight_parsing_and_binding_errors_are_classified_before_client(env, monkeypatch, malformation):
    start_failure(env)
    operation, policy = private_operation(env)
    bounder = env.runtime.input_bounder
    if malformation == "operation_none":
        operation = None
    elif malformation == "missing_permit":
        operation.pop("permit")
    elif malformation == "permit_none":
        operation["permit"] = None
    elif malformation in {"bad_deadline", "bad_issued", "bad_expiry"}:
        key = {"bad_deadline": "deadline_at", "bad_issued": "issued_at", "bad_expiry": "grant_expires_at"}[malformation]
        operation["permit"][key] = PRIVATE_ERROR
        operation["permit_hash"] = digest(operation["permit"])
    elif malformation == "missing_binding":
        operation["permit"].pop("model")
        operation["permit_hash"] = digest(operation["permit"])
    elif malformation == "bad_protocol":
        policy["wire_api"] = PRIVATE_ERROR
    elif malformation == "wire_mismatch":
        operation["wire"] += " "
    elif malformation == "missing_bounder":
        bounder = None
    elif malformation == "bounder_failure":
        bounder = copy.copy(bounder)

        def bad_bound(*args):
            raise AssertionError(PRIVATE_ERROR)

        object.__setattr__(bounder, "upper_bound", bad_bound)
    monkeypatch.setattr(httpx, "Client", lambda *a, **k: pytest.fail("Preflight constructed an HTTP client"))
    with pytest.raises(ResponseUnconfirmed) as failure:
        ControlledTransport(env.runtime.transport, bounder).perform(operation, policy, "synthetic-secret")
    assert failure.value.diagnostic == {"code": "PERMIT_REJECTED", "phase": "preflight"}
    assert str(failure.value) == "PERMIT_REJECTED" and failure.value.__cause__ is None


def test_expired_permit_has_deadline_classification_and_no_client(env, monkeypatch):
    start_failure(env)
    operation, policy = private_operation(env)
    operation["permit"]["deadline_at"] = (now() - timedelta(seconds=1)).isoformat()
    operation["permit"]["issued_at"] = (now() - timedelta(seconds=2)).isoformat()
    operation["permit_hash"] = digest(operation["permit"])
    monkeypatch.setattr(httpx, "Client", lambda *a, **k: pytest.fail("Expired permit constructed an HTTP client"))
    with pytest.raises(ResponseUnconfirmed) as failure:
        ControlledTransport(env.runtime.transport, env.runtime.input_bounder).perform(operation, policy, "synthetic-secret")
    assert failure.value.diagnostic == {"code": "OVERALL_DEADLINE", "phase": "deadline"}


def test_actual_runtime_persists_preflight_failure_with_intent_hold(env, monkeypatch):
    original = ControlledTransport.perform
    calls = []

    def corrupted_input(self, operation, policy, key):
        operation["permit"]["deadline_at"] = PRIVATE_ERROR
        operation["permit_hash"] = digest(operation["permit"])
        return original(self, operation, policy, key)

    monkeypatch.setattr(ControlledTransport, "perform", corrupted_input)
    task = env.source_task()
    grant = env.grant(task)
    env.action(task, grant["execution"])
    final = env.wait(task, {"needs_reconciliation"})
    calls.append("one persisted intent, zero provider calls")
    assert_unknown(env, task, grant, final, calls, "PERMIT_REJECTED", "preflight")
    assert env.provider.calls == []
    operation, _ = private_operation(env)
    assert operation["permit_hash"] == digest(operation["permit"])
    assert PRIVATE_ERROR not in json.dumps(operation["permit"])


@pytest.mark.parametrize("primary", [True, False])
def test_client_close_error_does_not_override_earlier_classification(env, primary):
    calls = []

    def handle(request):
        calls.append(request)
        if primary:
            raise httpx.ConnectTimeout(PRIVATE_ERROR)
        return env.provider.handle(request)

    class CloseFailure(httpx.MockTransport):
        def close(self):
            raise httpx.ReadTimeout(PRIVATE_ERROR)

    env.runtime.transport = CloseFailure(handle)
    task = env.source_task()
    grant = env.grant(task)
    env.action(task, grant["execution"])
    final = env.wait(task, {"needs_reconciliation"})
    assert_unknown(env, task, grant, final, calls, "CONNECT_TIMEOUT" if primary else "READ_TIMEOUT", "connect" if primary else "read")


@pytest.mark.parametrize("primary", ["read_timeout", "oversized"])
def test_response_close_error_does_not_override_iteration_failure(env, primary):
    calls = []

    class BrokenStream(httpx.SyncByteStream):
        def __iter__(self):
            if primary == "read_timeout":
                raise httpx.ReadTimeout(PRIVATE_ERROR)
            yield b"x" * 1048577

        def close(self):
            raise httpx.WriteTimeout(PRIVATE_ERROR)

    def handle(request):
        calls.append(request)
        return httpx.Response(200, stream=BrokenStream())

    env.runtime.transport = httpx.MockTransport(handle)
    task = env.source_task()
    grant = env.grant(task)
    env.action(task, grant["execution"])
    final = env.wait(task, {"needs_reconciliation"})
    assert_unknown(env, task, grant, final, calls, "READ_TIMEOUT" if primary == "read_timeout" else "RECEIPT_TOO_LARGE", "read" if primary == "read_timeout" else "receipt")


def test_oversized_receipt_keeps_original_hold_without_raw_body(env):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(200, content=b"x" * 1048577)

    env.runtime.transport = httpx.MockTransport(handle)
    task = env.source_task()
    grant = env.grant(task)
    env.action(task, grant["execution"])
    final = env.wait(task, {"needs_reconciliation"})
    assert_unknown(env, task, grant, final, calls, "RECEIPT_TOO_LARGE", "receipt")


@pytest.mark.parametrize("outcome", ["response", "read_timeout"])
def test_overall_deadline_wins_and_late_thread_cannot_change_journal(env, outcome):
    change_policy(env, request_deadline_seconds=1)
    release, closed = threading.Event(), threading.Event()
    calls = []

    def handle(request):
        calls.append(request)
        assert release.wait(5)
        if outcome == "read_timeout":
            raise httpx.ReadTimeout(PRIVATE_ERROR)
        return env.provider.handle(request)

    class ObservedTransport(httpx.MockTransport):
        def close(self):
            closed.set()

    env.runtime.transport = ObservedTransport(handle)
    task = env.source_task()
    grant = env.grant(task)
    try:
        env.action(task, grant["execution"])
        final = env.wait(task, {"needs_reconciliation"}, seconds=3)
        assert_unknown(env, task, grant, final, calls, "OVERALL_DEADLINE", "deadline")
        path = env.runtime.store.root / "state.json"
        original = path.read_bytes()
    finally:
        release.set()
    assert closed.wait(2)
    assert env.observe(task) == final and path.read_bytes() == original
    assert len(calls) == 1


def test_deadline_precedes_already_completed_read_error(env, monkeypatch):
    import core.execution.transport as module

    calls = []
    clock = [now()]
    monkeypatch.setattr(module, "now", lambda: clock[0])

    def handle(request):
        calls.append(request)
        clock[0] += timedelta(seconds=3)
        raise httpx.ReadTimeout(PRIVATE_ERROR)

    env.runtime.transport = httpx.MockTransport(handle)
    task = env.source_task()
    grant = env.grant(task)
    env.action(task, grant["execution"])
    final = env.wait(task, {"needs_reconciliation"})
    assert_unknown(env, task, grant, final, calls, "OVERALL_DEADLINE", "deadline")


def test_runtime_unclassified_exception_is_fixed_and_private(env, monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError(PRIVATE_ERROR)

    monkeypatch.setattr(ControlledTransport, "perform", fail)
    task = env.source_task()
    grant = env.grant(task)
    env.action(task, grant["execution"])
    final = env.wait(task, {"needs_reconciliation"})
    assert_unknown(env, task, grant, final, ["persisted intent"], "TRANSPORT_ERROR", "unknown")
    assert env.provider.calls == []


def test_restart_stop_and_new_grant_preserve_diagnostic_hold_and_no_resend(env):
    task, grant, final, calls = start_failure(env)
    original_operation, _ = private_operation(env)
    path = env.runtime.store.root / "state.json"
    original = path.read_bytes()
    reopened = ExecutionStore(env.runtime.store.root)
    with reopened.transaction() as payload:
        assert payload["operations"][original_operation["id"]] == original_operation
    twin = ExecutionRuntime(env.runtime.store.root, env.planner,
        policy=ExecutionPolicy.model_validate({k: v for k, v in env.runtime.policy.items() if k != "policy_hash"}),
        enabled=True, api_key="synthetic-secret", transport=env.runtime.transport, input_bounder=env.runtime.input_bounder)
    try:
        with TestClient(app_for(twin)) as client:
            assert client.get(env.url(task)).json()["execution"] == final
            assert path.read_bytes() == original
    finally:
        twin.close()
    for name in ["resume", "reconcile"]:
        assert env.action(task, final, name, "d12-no-resend-" + name).status_code == 409
    assert path.read_bytes() == original and len(calls) == 1
    quote = env.quote(task, final["revision"], "d12-new-quote-preserves-unknown")
    replacement = env.grant(task, quote, "d12-new-grant-preserves-unknown")
    state = replacement["execution"]
    assert state["ledger"] == final["ledger"] and state["status"] == "needs_reconciliation"
    assert env.action(task, state, key="d12-new-grant-no-resend").status_code == 409
    stopped = env.action(task, state, "stop", "d12-stop-with-unknown")
    assert stopped.status_code == 200 and stopped.json()["execution"]["status"] == "stopped"
    assert stopped.json()["execution"]["ledger"] == final["ledger"]
    assert private_operation(env)[0] == original_operation and len(calls) == 1


@pytest.mark.parametrize("diagnostic", [
    None, [], {}, {"code": "CONNECT_TIMEOUT"}, {"code": [], "phase": "connect"},
    {"code": PRIVATE_ERROR, "phase": "unknown"}, {"code": "CONNECT_TIMEOUT", "phase": "read"},
    {"code": "CONNECT_TIMEOUT", "phase": "connect", "error": PRIVATE_ERROR},
])
def test_invalid_diagnostic_is_rejected_without_overwriting_history(env, diagnostic):
    start_failure(env)
    path = env.runtime.store.root / "state.json"
    original = path.read_bytes()
    with pytest.raises(CreatorError) as error:
        with env.runtime.store.transaction() as payload:
            next(iter(payload["operations"].values()))["transport_diagnostic"] = diagnostic
    assert error.value.code == "CORRUPT_STORAGE" and path.read_bytes() == original


@pytest.mark.parametrize("change", ["received", "settled", "raw", "normalized", "local_units"])
def test_diagnostic_is_only_valid_on_unconfirmed_transport_operation(env, change):
    start_failure(env)
    path = env.runtime.store.root / "state.json"
    original = path.read_bytes()
    with pytest.raises(CreatorError) as error:
        with env.runtime.store.transaction() as payload:
            operation = next(iter(payload["operations"].values()))
            if change in {"received", "settled"}:
                operation["status"] = change
                if change == "settled":
                    operation["local_units"] = 0
            else:
                operation[change] = 0 if change == "local_units" else {}
    assert error.value.code == "CORRUPT_STORAGE" and path.read_bytes() == original


@pytest.mark.parametrize("fault", [None, "http", "missing_usage"])
def test_received_receipts_and_success_never_gain_transport_diagnostic(env, fault):
    env.provider.fault = fault
    task = env.source_task()
    grant = env.grant(task)
    env.action(task, grant["execution"])
    env.wait(task, {"plan_ready"} if fault is None else {"needs_reconciliation"})
    with env.runtime.store.transaction() as payload:
        assert all("transport_diagnostic" not in op for op in payload["operations"].values())
        assert all(op["raw"] is not None for op in payload["operations"].values())


def test_legacy_journal_is_not_backfilled_or_rewritten(env):
    fixture, path, original = restore_legacy(env)
    for task in [fixture["success_task"], fixture["received_task"]]:
        assert env.observe(task)["operations"]
    with env.runtime.store.transaction() as payload:
        assert all("transport_diagnostic" not in op for op in payload["operations"].values())
    assert path.read_bytes() == original and env.provider.calls == []


def test_stop_while_inflight_remains_stopped_after_classified_failure(env):
    entered, release = threading.Event(), threading.Event()
    calls = []

    def handle(request):
        calls.append(request)
        entered.set()
        assert release.wait(5)
        raise httpx.ReadTimeout(PRIVATE_ERROR)

    env.runtime.transport = httpx.MockTransport(handle)
    task = env.source_task()
    grant = env.grant(task)
    try:
        env.action(task, grant["execution"])
        assert entered.wait(1)
        running = env.observe(task)
        stopped = env.action(task, running, "stop", "d12-stop-during-transport")
        assert stopped.status_code == 200 and stopped.json()["execution"]["status"] == "stopped"
    finally:
        release.set()
    env.runtime.close()
    final = env.observe(task)
    assert final["status"] == "stopped"
    assert_unknown(env, task, grant, final, calls, "READ_TIMEOUT", "read")
