"""Real ASGI/private disk/locks/loop/tools; only model responses are synthetic."""
import copy
import hashlib
import json
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient

from api.routers import creator, planner
from core.creator.store import CreatorBriefStore, CreatorError, digest
from core.planner.actors import (
    ModelUncertain,
    OfflineActor,
    SingleRequestTextAdapter,
    TextCallPermit,
)
from core.planner.protocol import checked_plan
from core.planner.runtime import PlannerRuntime

BASE = "/api/planner/tasks"
OFFLINE = "/api/planner/offline-tasks"
SCRIPT = "SYNTHETIC-PRIVATE-PLANNER-SCRIPT Ａ\r\n第二行\n"


@pytest.fixture(autouse=True)
def no_external(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("No real outbound calls in Planner checks")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)


class TrackingActor:
    def __init__(self):
        self.calls = []
        self.delegate = OfflineActor()

    def respond(self, messages, operation, task):
        self.calls.append({"messages": copy.deepcopy(messages), "operation": copy.deepcopy(operation)})
        return self.delegate.respond(messages, operation, task)


def make_app(runtime):
    app = FastAPI()
    app.include_router(creator.router)
    app.include_router(planner.router)
    app.dependency_overrides[creator.get_brief_store] = lambda: runtime.brief_store
    app.dependency_overrides[planner.get_planner_runtime] = lambda: runtime
    return app


@pytest.fixture
def env(tmp_path):
    source_store = CreatorBriefStore(tmp_path.resolve() / "briefs")
    actor = TrackingActor()
    runtime = PlannerRuntime(tmp_path.resolve() / "planner", source_store, actor)
    with TestClient(make_app(runtime)) as client:
        yield client, runtime, actor
    runtime.close()


def brief(client, **changes):
    r = client.post("/api/creator/briefs", json={"client_request_key": "brief-synthetic-key-001", "script": SCRIPT, "requirements": "PRIVATE-PLANNER-REQUIREMENTS", **changes})
    assert r.status_code == 201, r.text
    return r.json()["brief"]


def body(source, key="planner-synthetic-key-001", **changes):
    return {"client_request_key": key, "brief_id": source["id"], "brief_revision": source["revision"], **changes}


def create(client, source, offline=False, **changes):
    r = client.post(OFFLINE if offline else BASE, json=body(source, **changes))
    assert r.status_code == 201, r.text
    return r.json()["task"]


def action(client, task, name="resume", key="planner-action-key-001"):
    return client.post(f"{BASE}/{task['id']}/actions", json={"client_action_key": key, "expected_revision": task["revision"], "action": name})


def wait(client, task_id, statuses, seconds=10):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        r = client.get(f"{BASE}/{task_id}")
        assert r.status_code == 200, r.text
        task = r.json()["task"]
        if task["status"] in statuses:
            return task
        time.sleep(0.01)
    raise AssertionError("Planner did not reach expected status")


def test_formal_registration_preserves_d06_d07():
    from api.app import app
    paths = [route.path for route in app.routes]
    assert all(path in paths for path in [BASE, OFFLINE, "/api/creator/briefs", "/api/harness/runs"])
    assert paths.index(BASE + "/by-key/{client_request_key}") < paths.index(BASE + "/{task_id}")


def test_normal_budget_never_authorizes_or_constructs_live_client(env, monkeypatch):
    client, runtime, actor = env
    monkeypatch.setattr(SingleRequestTextAdapter, "__init__", lambda *a, **k: pytest.fail("Normal task constructed live adapter"))
    task = create(client, brief(client, budget_cny="100"))
    assert task["status"] == "awaiting_authorization" and task["blocked_reason"] == "AUTHORIZATION_REQUIRED"
    assert task["authorization_status"] == "not_authorized" and not task["execution_enabled"]
    stopped = action(client, task, "stop").json()["task"]
    resumed = action(client, stopped, "resume", "planner-action-key-002").json()["task"]
    assert resumed["status"] == "awaiting_authorization" and resumed["step_count"] == 0
    assert client.get(f"{BASE}/{task['id']}").json()["task"]["step_count"] == 0
    assert actor.calls == [] and (runtime.store.root / "state.json").is_file()


def test_complete_version_freeze_and_idempotent_original_timestamp(env):
    client, _, _ = env
    source = brief(client, budget_cny="12.30")
    first = create(client, source)
    before = client.get(f"{BASE}/{first['id']}/source").json()["source"]
    update = {"client_update_key": "brief-change-key-001", "expected_revision": 1, **{k: source[k] for k in ["title", "script", "requirements", "production", "budget_cny"]}}
    update.update(script="New private source", requirements="Changed requirements", budget_cny="99")
    assert client.put(f"/api/creator/briefs/{source['id']}", json=update).status_code == 200
    assert client.get(f"{BASE}/{first['id']}/source").json()["source"] == before
    assert before["script"] == SCRIPT and before["requirements"] == source["requirements"] and before["budget_cny"] == "12.30"
    assert before["source_hash"] == hashlib.sha256(SCRIPT.encode()).hexdigest()
    assert before["snapshot_hash"] == digest({k: v for k, v in before.items() if k != "snapshot_hash"})
    replay = client.post(BASE, json=body(source))
    assert replay.status_code == 200 and replay.json()["replayed"] and replay.json()["task"]["source"] == first["source"]
    assert client.post(BASE, json=body(source, brief_revision=2)).status_code == 409
    older = create(client, source, key="second-planner-key-001")
    assert client.get(f"{BASE}/{older['id']}/source").json()["source"]["script"] == SCRIPT


def test_summary_lookup_and_event_privacy(env, caplog):
    client, runtime, _ = env
    task = create(client, brief(client), offline=True)
    for url in [BASE, BASE + "/by-key/planner-synthetic-key-001", f"{BASE}/{task['id']}/events"]:
        r = client.get(url)
        assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
        assert "SYNTHETIC-PRIVATE" not in r.text and "PRIVATE-PLANNER-REQUIREMENTS" not in r.text and str(runtime.store.root) not in r.text
    assert client.get(BASE + "/by-key/planner-synthetic-key-001").json()["task"]["id"] == task["id"]
    assert client.get(f"{BASE}/{task['id']}/source").json()["source"]["script"] == SCRIPT
    assert "SYNTHETIC-PRIVATE" not in caplog.text


@pytest.mark.parametrize("scenario,status,steps,repairs,reason", [
    ("success", "plan_ready", 2, 0, None), ("repair", "plan_ready", 3, 1, None),
    ("invalid_tool", "blocked", 2, 2, "REPAIR_LIMIT"), ("step_limit", "blocked", 6, 0, "STEP_LIMIT"),
    ("response_unknown", "response_unknown", 1, 0, "MODEL_RESPONSE_UNCONFIRMED")])
def test_real_loop_feedback_and_limits(env, scenario, status, steps, repairs, reason):
    client, runtime, actor = env
    task = create(client, brief(client), offline=True, scenario=scenario)
    assert task["status"] == "ready" and actor.calls == []
    assert action(client, task).status_code == 200
    final = wait(client, task["id"], {status})
    assert final["step_count"] == len(actor.calls) == steps and final["repair_count"] == repairs
    assert final["blocked_reason"] == reason and final["mode"] == "offline_test"
    assert final["authorization_status"] == "offline_only" and final["quality_status"] == "not_reviewed" and not final["execution_enabled"]
    if status == "plan_ready":
        assert sum(shot["duration_ms"] for shot in final["plan"]["shots"]) == 15000
        assert final["plan"]["source_snapshot_hash"] == final["source"]["snapshot_hash"]
        assert final["plan_version"] == len(final["plan_versions"]) == 1
        assert actor.calls[1]["messages"][-1]["content"]["source"]["script"] == SCRIPT
    if scenario == "repair":
        assert actor.calls[2]["messages"][-1]["content"]["code"] == "DURATION_TOTAL"
    events = client.get(f"{BASE}/{task['id']}/events?limit=200").json()["events"]
    assert [e["sequence"] for e in events] == list(range(1, len(events) + 1))
    assert sum(e["type"] == "call_intent" for e in events) == steps and "SYNTHETIC-PRIVATE" not in json.dumps(events)
    with runtime.store.transaction() as state:
        operations = state["tasks"][task["id"]]["versions"][-1]["payload"]["operations"]
        assert len({op["id"] for op in operations}) == steps
        assert all(op["context_hash"] == digest(op["context"]) for op in operations)


def test_idempotency_conflict_and_late_replay(env):
    client, _, _ = env
    source = brief(client)
    task = create(client, source)
    assert client.post(OFFLINE, json=body(source)).status_code == 409
    request = {"client_action_key": "persist-stop-key-001", "expected_revision": 1, "action": "stop"}
    first = client.post(f"{BASE}/{task['id']}/actions", json=request).json()
    assert first["applied_revision"] == 2
    stale = action(client, task, "stop", "new-stale-key-001")
    assert stale.status_code == 409 and stale.json()["detail"]["current_revision"] == 2
    resumed = action(client, first["task"], "resume", "new-resume-key-001").json()["task"]
    replay = client.post(f"{BASE}/{task['id']}/actions", json=request).json()
    assert replay["replayed"] and replay["applied_revision"] == 2 and replay["task"]["revision"] == resumed["revision"]
    assert client.post(f"{BASE}/{task['id']}/actions", json={**request, "action": "resume"}).status_code == 409
    late_create = client.post(BASE, json=body(source)).json()
    assert late_create["replayed"] and late_create["applied_revision"] == 1 and late_create["task"]["revision"] == resumed["revision"]


def test_concurrent_create_and_actions_atomic(env):
    client, _, _ = env
    source = brief(client)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: client.post(BASE, json=body(source)), range(8)))
    assert sum(r.status_code == 201 for r in results) == 1 and len({r.json()["task"]["id"] for r in results}) == 1
    task = results[0].json()["task"]
    with ThreadPoolExecutor(max_workers=8) as pool:
        actions = list(pool.map(lambda n: action(client, task, "stop", f"parallel-action-{n:03}"), range(8)))
    assert sum(r.status_code == 200 for r in actions) == 1 and sum(r.status_code == 409 for r in actions) == 7


def test_unknown_after_new_application_and_stop_never_retries(env):
    client, runtime, actor = env
    task = create(client, brief(client), offline=True, scenario="response_unknown")
    action(client, task)
    final = wait(client, task["id"], {"response_unknown"})
    other = PlannerRuntime(runtime.store.root, runtime.brief_store, actor)
    try:
        with TestClient(make_app(other)) as fresh:
            loaded = fresh.get(f"{BASE}/{task['id']}").json()["task"]
            assert action(fresh, loaded, "resume", "forbidden-resume-key").status_code == 409
            stopped = action(fresh, loaded, "stop", "stop-unknown-key-001").json()["task"]
            assert stopped["pending_call"] == final["pending_call"] and stopped["allowed_actions"] == []
            assert stopped["blocked_reason"] == "MODEL_RESPONSE_UNCONFIRMED"
            refreshed = fresh.get(f"{BASE}/{task['id']}").json()["task"]
            assert refreshed["blocked_reason"] == "MODEL_RESPONSE_UNCONFIRMED" and refreshed["allowed_actions"] == []
            assert action(fresh, refreshed, "resume", "still-no-resume-key").status_code == 409
            assert len(actor.calls) == 1
    finally:
        other.close()


def test_intent_interruption_recovers_unknown_without_second_call(env):
    client, runtime, actor = env
    def crash(messages, operation, task):
        actor.calls.append(operation["id"])
        raise SystemExit("Synthetic interruption")
    runtime.actor = type("CrashActor", (), {"respond": staticmethod(crash)})()
    task = create(client, brief(client), offline=True)
    action(client, task)
    recovered = wait(client, task["id"], {"response_unknown"})
    assert recovered["blocked_reason"] == "MODEL_RESPONSE_UNCONFIRMED" and len(actor.calls) == 1
    assert action(client, recovered, "resume", "no-retry-unknown-key").status_code == 409


def test_confirmed_receipt_reused_after_interruption(env, monkeypatch):
    import core.planner.runtime as module
    client, runtime, actor = env
    real_tool = module.execute_tool
    def crash(*args):
        raise SystemExit("Synthetic interruption after persisted receipt")
    monkeypatch.setattr(module, "execute_tool", crash)
    task = create(client, brief(client), offline=True)
    action(client, task)
    recovered = wait(client, task["id"], {"stopped"})
    assert recovered["pending_call"]["status"] == "received" and len(actor.calls) == 1
    original = actor.calls[0]["operation"]["id"]
    monkeypatch.setattr(module, "execute_tool", real_tool)
    other = PlannerRuntime(runtime.store.root, runtime.brief_store, actor)
    try:
        with TestClient(make_app(other)) as fresh:
            assert action(fresh, recovered, "resume", "resume-confirmed-key").status_code == 200
            assert wait(fresh, task["id"], {"plan_ready"})["step_count"] == len(actor.calls) == 2
            assert sum(c["operation"]["id"] == original for c in actor.calls) == 1
    finally:
        other.close()


def test_stop_preserves_inflight_intent_and_late_receipt(env):
    client, runtime, actor = env
    entered, release = threading.Event(), threading.Event()
    def respond(messages, operation, task):
        if not entered.is_set():
            entered.set()
            assert release.wait(5)
        return actor.respond(messages, operation, task)
    runtime.actor = type("BlockingActor", (), {"respond": staticmethod(respond)})()
    task = create(client, brief(client), offline=True)
    action(client, task)
    try:
        assert entered.wait(3)
        running = client.get(f"{BASE}/{task['id']}").json()["task"]
        stopped = action(client, running, "stop", "stop-pending-key-001").json()["task"]
        assert stopped["pending_call"]["status"] == "intent" and stopped["allowed_actions"] == []
    finally:
        release.set()
    runtime.close()
    stopped = client.get(f"{BASE}/{task['id']}").json()["task"]
    assert stopped["status"] == "stopped" and stopped["pending_call"]["status"] == "received"
    assert stopped["blocked_reason"] == "STOPPED"
    other = PlannerRuntime(runtime.store.root, runtime.brief_store, actor)
    try:
        with TestClient(make_app(other)) as fresh:
            assert action(fresh, stopped, "resume", "resume-late-receipt-key").status_code == 200
            assert wait(fresh, task["id"], {"plan_ready"})["step_count"] == len(actor.calls) == 2
    finally:
        other.close()


@pytest.mark.parametrize("extra", [{"mode": "live"}, {"script": "PRIVATE-INPUT"}, {"snapshot_hash": "f" * 64}, {"authorization_ref": "old-100-cny"}, {"execution_enabled": True}, {"budget_cny": "100"}, {"model": "gpt-test"}, {"file_path": "/config.yaml"}, {"scenario": "success"}, {"brief_revision": True}, {"brief_revision": 1.0}])
def test_forged_input_rejected_without_echo(env, extra):
    client, _, actor = env
    source = brief(client)
    r = client.post(BASE, json=body(source, **extra))
    assert r.status_code == 422 and "PRIVATE-INPUT" not in r.text and "input" not in r.json()["detail"]
    assert client.get(BASE).json()["tasks"] == [] and actor.calls == []


@pytest.mark.parametrize("damage", ["truncated", "duplicate", "source", "frame", "create_key", "action_key", "context", "receipt"])
def test_corrupt_storage_fails_closed_preserving_bytes(env, damage):
    client, runtime, _ = env
    source = brief(client)
    task = create(client, source, offline=True)
    action(client, task)
    task = wait(client, task["id"], {"plan_ready"})
    path = runtime.store.root / "state.json"
    state = json.loads(path.read_text())
    document = state["tasks"][task["id"]]
    if damage == "truncated":
        path.write_text('{"PRIVATE-DAMAGE":')
    elif damage == "duplicate":
        path.write_text('{"schema_version":1,"schema_version":1,"keys":{},"tasks":{}}')
    else:
        if damage == "source":
            document["source"]["requirements"] += "PRIVATE-DAMAGE"
        elif damage == "frame":
            document["versions"][0]["payload"]["status"] = "plan_ready"
        elif damage == "create_key":
            state["keys"]["planner-synthetic-key-002"] = state["keys"].pop("planner-synthetic-key-001")
        elif damage == "action_key":
            receipts = document["versions"][-1]["payload"]["actions"]
            receipts["planner-action-key-002"] = receipts.pop("planner-action-key-001")
        elif damage == "context":
            document["versions"][-1]["payload"]["operations"][0]["context_hash"] = "f" * 64
        else:
            document["versions"][-1]["payload"]["operations"][0]["response"] = "PRIVATE-DAMAGE"
        path.write_text(json.dumps(state))
    damaged = path.read_bytes()
    for url in [BASE, BASE + "/by-key/planner-synthetic-key-001", f"{BASE}/{task['id']}", f"{BASE}/{task['id']}/source", f"{BASE}/{task['id']}/events"]:
        r = client.get(url)
        assert r.status_code == 503 and r.json()["detail"]["code"] == "CORRUPT_STORAGE"
        assert "PRIVATE-DAMAGE" not in r.text and path.read_bytes() == damaged
    assert client.post(BASE, json=body(source, key="new-on-corruption-key")).status_code == 503
    assert action(client, task, "stop", "action-on-corrupt-key").status_code == 503
    assert path.read_bytes() == damaged


@pytest.mark.parametrize("relation", ["parent", "same", "child"])
@pytest.mark.parametrize("nested", [False, True])
def test_recursive_static_bidirectional_guard(env, relation, nested):
    client, runtime, _ = env
    source = brief(client)
    root = runtime.store.root
    static = {"parent": root.parent, "same": root, "child": root / "child"}[relation]
    app = make_app(runtime)
    target = FastAPI() if nested else app
    target.mount("/static", StaticFiles(directory=static, check_dir=False))
    if nested:
        app.mount("/nested", target)
    with TestClient(app) as exposed:
        assert exposed.post(BASE, json=body(source)).status_code == 503
    assert not root.exists()


@pytest.mark.parametrize("target", ["state.json", ".state.lock", "execution"])
def test_symlinks_rejected_without_touching_target(env, tmp_path, target):
    client, runtime, _ = env
    task = create(client, brief(client), offline=True)
    outside = tmp_path / "outside-file"
    outside.write_text("PRIVATE-OUTSIDE")
    path = runtime.store.root / (f".{task['id']}.execution.lock" if target == "execution" else target)
    path.unlink(missing_ok=True)
    path.symlink_to(outside)
    r = action(client, task) if target == "execution" else client.get(BASE)
    assert r.status_code == 503 and "PRIVATE-OUTSIDE" not in r.text and outside.read_text() == "PRIVATE-OUTSIDE"


def test_root_symlink_and_unknown_query(env, tmp_path):
    client, runtime, _ = env
    actual = tmp_path.resolve() / "actual"
    actual.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(actual, target_is_directory=True)
    with pytest.raises(CreatorError):
        PlannerRuntime(alias / "child", runtime.brief_store)
    r = client.get(BASE + "?script=PRIVATE-QUERY")
    assert r.status_code == 422 and "PRIVATE-QUERY" not in r.text


def plan_value():
    return {"source_snapshot_hash": "a" * 64, "duration_ms": 15000, "ratio": "9:16", "shots": [
        {"id": "S01", "duration_ms": 7500, "visual": "Synthetic one", "camera": "", "audio": "", "depends_on": []},
        {"id": "S02", "duration_ms": 7500, "visual": "Synthetic two", "camera": "", "audio": "", "depends_on": ["S01"]}]}


@pytest.mark.parametrize("fault,code", [("duration", "DURATION_TOTAL"), ("duplicate", "DUPLICATE_SHOT_ID"), ("self", "INVALID_DEPENDENCY"), ("dangling", "INVALID_DEPENDENCY"), ("cycle", "DEPENDENCY_CYCLE"), ("source", "SOURCE_BINDING"), ("extra", "INVALID_PLAN"), ("float", "INVALID_PLAN"), ("blank", "INVALID_PLAN")])
def test_strict_plan_structure(fault, code):
    plan = plan_value()
    if fault == "duration":
        plan["shots"][0]["duration_ms"] = 7000
    elif fault == "duplicate":
        plan["shots"][1]["id"] = "S01"
    elif fault == "self":
        plan["shots"][0]["depends_on"] = ["S01"]
    elif fault == "dangling":
        plan["shots"][0]["depends_on"] = ["MISSING"]
    elif fault == "cycle":
        plan["shots"][0]["depends_on"] = ["S02"]
    elif fault == "source":
        plan["source_snapshot_hash"] = "b" * 64
    elif fault == "extra":
        plan["execute_url"] = "https://invalid.example"
    elif fault == "float":
        plan["shots"][0]["duration_ms"] = 7500.0
    else:
        plan["shots"][0]["visual"] = " "
    checked, error = checked_plan(plan, "a" * 64)
    assert checked is None and error == code


def permit_for(messages, operation, task):
    return TextCallPermit(task["id"], operation["id"], task["snapshot_hash"], digest(messages), "openai_compatible", "exact-test-model", "https://text.example/v1/chat/completions", datetime.now(timezone.utc) + timedelta(minutes=1), True, 1, 10000, 65536, 1000, "0.10", "0.20")


def adapter_fixture(failure=None, change="valid"):
    messages = [{"role": "user", "content": SCRIPT}]
    operation, task = {"id": "a" * 32}, {"id": "b" * 32, "snapshot_hash": "c" * 64}
    calls = []
    permit = permit_for(messages, operation, task)
    if change != "valid":
        permit = replace(permit, **change) if change is not None else None
    def transport(request):
        calls.append(request)
        payload = json.loads(request.content)
        assert payload["model"] == "exact-test-model" and set(payload) == {"model", "messages", "max_tokens"}
        assert json.loads(payload["messages"][0]["content"]) == SCRIPT
        if failure == "timeout":
            raise httpx.ReadTimeout("PRIVATE-PROVIDER-ERROR")
        if failure == "http":
            return httpx.Response(500, text="PRIVATE-PROVIDER-ERROR")
        if failure == "malformed":
            return httpx.Response(200, json={"PRIVATE-PROVIDER-ERROR": True})
        content = "x" * 65537 if failure == "oversized" else '{"tool":"read_frozen_brief","arguments":{}}'
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})
    adapter = SingleRequestTextAdapter(endpoint="https://text.example/v1/chat/completions", model="exact-test-model", api_key="synthetic-key", permit_provider=lambda *args: permit, transport=httpx.MockTransport(transport))
    return adapter, messages, operation, task, calls


def test_adapter_single_exact_request_no_normalization_logging_or_retry(caplog):
    adapter, messages, operation, task, calls = adapter_fixture()
    assert "read_frozen_brief" in adapter.respond(messages, operation, task)
    with pytest.raises(ModelUncertain):
        adapter.respond(messages, operation, task)
    assert len(calls) == 1 and "SYNTHETIC-PRIVATE" not in caplog.text and "synthetic-key" not in caplog.text


@pytest.mark.parametrize("failure", ["timeout", "http", "malformed", "oversized"])
def test_adapter_error_never_retries_or_echoes(failure, caplog):
    adapter, messages, operation, task, calls = adapter_fixture(failure)
    for _ in range(2):
        with pytest.raises(ModelUncertain) as error:
            adapter.respond(messages, operation, task)
        assert "PRIVATE-PROVIDER" not in str(error.value)
    assert len(calls) == 1 and "PRIVATE-PROVIDER" not in caplog.text


@pytest.mark.parametrize("change", [None, {"allow_text_export": False}, {"max_calls": 2}, {"context_hash": "f" * 64}, {"snapshot_hash": "f" * 64}, {"model": "another"}, {"per_call_upper_cny": "0"}, {"total_ceiling_cny": "0.01"}, {"max_output_tokens": 8193}, {"expires_at": datetime(2000, 1, 1, tzinfo=timezone.utc)}])
def test_adapter_gate_precedes_transport(change):
    adapter, messages, operation, task, calls = adapter_fixture(change=change)
    with pytest.raises(PermissionError):
        adapter.respond(messages, operation, task)
    assert calls == []


def test_adapter_wire_bytes_bound_applies_after_message_encoding():
    messages = [{"role": "user", "content": SCRIPT}]
    old_size = len(json.dumps(messages, ensure_ascii=False).encode("utf-8"))
    adapter, current, operation, task, calls = adapter_fixture(change={"max_input_bytes": old_size})
    with pytest.raises(PermissionError):
        adapter.respond(current, operation, task)
    assert calls == []
    wire_payload = {"model": "exact-test-model", "max_tokens": 1000, "messages": [{"role": "user", "content": json.dumps(SCRIPT, ensure_ascii=False)}]}
    exact_size = len(json.dumps(wire_payload, ensure_ascii=False).encode("utf-8"))
    assert exact_size > old_size
    adapter, current, operation, task, calls = adapter_fixture(change={"max_input_bytes": exact_size})
    adapter.respond(current, operation, task)
    assert len(calls) == 1 and len(calls[0].content) == exact_size


@pytest.mark.parametrize("raw,code", [
    ("[" * 10000 + "0" + "]" * 10000, "INVALID_ACTION"),
    ("not JSON", "INVALID_ACTION"),
    ('{"tool":"read_frozen_brief","tool":"read_frozen_brief","arguments":{}}', "INVALID_ACTION"),
    ('[{"tool":"read_frozen_brief","arguments":{}}]', "INVALID_ACTION"),
    ("x" * 65537, "OUTPUT_LIMIT"),
    ("\ud800", "INVALID_OUTPUT"),
])
def test_malformed_model_outputs_feed_back_and_exhaust_repairs_in_real_loop(env, raw, code):
    client, runtime, actor = env
    def respond(messages, operation, task):
        actor.calls.append(copy.deepcopy(messages))
        return raw
    runtime.actor = type("MalformedActor", (), {"respond": staticmethod(respond)})()
    task = create(client, brief(client), offline=True)
    assert action(client, task).status_code == 200
    final = wait(client, task["id"], {"blocked"})
    assert final["blocked_reason"] == "REPAIR_LIMIT" and final["repair_count"] == final["step_count"] == 2
    assert final["pending_call"] is None and len(actor.calls) == 2
    assert actor.calls[1][-1]["content"] == {"ok": False, "code": code}


def test_context_byte_limit_uses_real_repeated_tool_results(env):
    client, runtime, actor = env
    task = create(client, brief(client, script="字" * 190000), offline=True, scenario="step_limit")
    assert action(client, task).status_code == 200
    final = wait(client, task["id"], {"blocked"}, seconds=20)
    assert final["blocked_reason"] == "CONTEXT_LIMIT" and final["step_count"] == len(actor.calls) == 2
    assert final["repair_count"] == 0
    assert all(len(json.dumps(call["messages"], ensure_ascii=False).encode("utf-8")) <= 1048576 for call in actor.calls)


def test_atomic_replace_failure_preserves_task_and_action_key_retry(env, monkeypatch):
    client, runtime, _ = env
    task = create(client, brief(client))
    path = runtime.store.root / "state.json"
    original = path.read_bytes()
    with monkeypatch.context() as patcher:
        def fail(*args):
            raise OSError("PRIVATE-ATOMIC-ERROR")
        patcher.setattr("core.planner.store.os.replace", fail)
        response = action(client, task, "stop")
    assert response.status_code == 503 and "PRIVATE-ATOMIC" not in response.text
    assert path.read_bytes() == original and list(runtime.store.root.glob(".planner-*")) == []
    retry = action(client, task, "stop")
    assert retry.status_code == 200 and not retry.json()["replayed"] and retry.json()["task"]["revision"] == 2


def test_deeply_nested_corrupt_json_is_not_overwritten(env):
    client, runtime, _ = env
    source = brief(client)
    create(client, source)
    path = runtime.store.root / "state.json"
    damaged = ("[" * 10000 + "0" + "]" * 10000).encode()
    path.write_bytes(damaged)
    for method in ["GET", "POST"]:
        r = client.get(BASE) if method == "GET" else client.post(BASE, json=body(source, key="fresh-on-deep-json"))
        assert r.status_code == 503 and r.json()["detail"]["code"] == "CORRUPT_STORAGE"
        assert path.read_bytes() == damaged


def test_paging_missing_source_and_private_brief_mount_guard(env):
    client, runtime, actor = env
    source = brief(client)
    first = create(client, source)
    second = create(client, source, key="second-paging-key-001")
    page = client.get(BASE + "?limit=1&brief_id=" + source["id"]).json()
    assert page["tasks"][0]["id"] == second["id"] and page["has_more"] and page["next_offset"] == 1
    assert client.get(BASE + "?limit=1&offset=1").json()["tasks"][0]["id"] == first["id"]
    assert client.post(BASE, json=body(source, key="missing-version-key", brief_revision=99)).status_code == 404
    assert client.post(BASE, json=body(source, key="missing-brief-key", brief_id="f" * 32)).status_code == 404
    app = make_app(runtime)
    app.mount("/private-briefs", StaticFiles(directory=runtime.brief_store.root))
    with TestClient(app) as exposed:
        assert exposed.post(BASE, json=body(source, key="must-not-freeze-exposed")).status_code == 503
    assert actor.calls == []


def test_cross_process_creation_lock_and_new_process_recovery(env):
    import os
    import subprocess
    import sys
    client, runtime, _ = env
    source = brief(client)
    code = """
import json,sys
from core.creator.store import CreatorBriefStore
from core.planner.runtime import PlannerRuntime
runtime=PlannerRuntime(sys.argv[1],CreatorBriefStore(sys.argv[2]))
try:
 r=runtime.create({'client_request_key':'process-shared-task-key','brief_id':sys.argv[3],'brief_revision':1})
 print(json.dumps({'id':r['task']['id'],'replayed':r['replayed'],'snapshot_hash':r['task']['source']['snapshot_hash']}))
finally:
 runtime.close()
"""
    arguments = [sys.executable, "-c", code, str(runtime.store.root), str(runtime.brief_store.root), source["id"]]
    processes = [subprocess.Popen(arguments, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}) for _ in range(4)]
    outputs = []
    for process in processes:
        stdout, stderr = process.communicate(timeout=15)
        assert process.returncode == 0, stderr.decode()
        outputs.append(json.loads(stdout))
    assert len({r["id"] for r in outputs}) == len({r["snapshot_hash"] for r in outputs}) == 1
    assert sum(not r["replayed"] for r in outputs) == 1
    fresh = subprocess.run(arguments, capture_output=True, text=True, timeout=15)
    assert fresh.returncode == 0, fresh.stderr
    assert json.loads(fresh.stdout)["replayed"]
    assert client.get(BASE + "/by-key/process-shared-task-key").json()["task"]["id"] == outputs[0]["id"]


def test_single_tail_deletion_is_corruption_and_cannot_reconsume_successful_action(env):
    client, runtime, _ = env
    source = brief(client)
    task = create(client, source)
    request = {"client_action_key": "successful-stop-key", "expected_revision": task["revision"], "action": "stop"}
    written = client.post(f"{BASE}/{task['id']}/actions", json=request)
    assert written.status_code == 200 and not written.json()["replayed"]
    assert written.json()["task"]["revision"] == 2
    path = runtime.store.root / "state.json"
    state = json.loads(path.read_text())
    document = state["tasks"][task["id"]]
    assert document["versions"][-1]["revision"] == 2
    document["versions"].pop()  # One mutation, no receipt/index/hash/head edits.
    path.write_text(json.dumps(state))
    damaged = path.read_bytes()
    for url in [f"{BASE}/{task['id']}", BASE, BASE + "/by-key/planner-synthetic-key-001", f"{BASE}/{task['id']}/source", f"{BASE}/{task['id']}/events"]:
        r = client.get(url)
        assert r.status_code == 503 and r.json()["detail"]["code"] == "CORRUPT_STORAGE"
        assert path.read_bytes() == damaged
    replay = client.post(f"{BASE}/{task['id']}/actions", json=request)
    assert replay.status_code == 503 and replay.json()["detail"]["code"] == "CORRUPT_STORAGE"
    assert path.read_bytes() == damaged
    assert client.post(BASE, json=body(source)).status_code == 503
    assert client.post(BASE, json=body(source, key="fresh-after-tail-delete")).status_code == 503
    assert path.read_bytes() == damaged
