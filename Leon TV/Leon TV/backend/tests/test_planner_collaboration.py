"""Real Planner API/journal/worker handoff; only role responses are synthetic."""
import copy
import json
import socket
import threading
import time
from contextlib import contextmanager

import pytest
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient

from api.routers import creator, planner
from core.creator.store import CreatorBriefStore, digest
from core.planner.actors import OfflineActor
from core.planner.collaboration import SyntheticReviewer
from core.planner.protocol import REVIEW_SCENARIOS
from core.planner.review import review_messages
from core.planner.runtime import PlannerRuntime
from core.planner.store import PAYLOAD_FIELDS, PlannerError, frame_hash, validate_state

BASE = "/api/planner/tasks"
OFFLINE = "/api/planner/offline-tasks"


@pytest.fixture(autouse=True)
def no_external(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Collaboration must never make a real outbound call")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)


class TrackingPlanner:
    def __init__(self):
        self.calls = []

    def respond(self, messages, operation, task):
        self.calls.append(copy.deepcopy((messages, operation, task)))
        return OfflineActor().respond(messages, operation, task)


class TrackingReviewer:
    def __init__(self):
        self.calls = []

    def respond(self, messages, operation, task):
        self.calls.append(copy.deepcopy((messages, operation, task)))
        return SyntheticReviewer().respond(messages, operation, task)


def application(runtime):
    app = FastAPI()
    app.include_router(creator.router)
    app.include_router(planner.router)
    app.dependency_overrides[creator.get_brief_store] = lambda: runtime.brief_store
    app.dependency_overrides[planner.get_planner_runtime] = lambda: runtime
    return app


@pytest.fixture
def env(tmp_path):
    runtime = PlannerRuntime(tmp_path / "planner", CreatorBriefStore(tmp_path / "briefs"), TrackingPlanner(), reviewer=TrackingReviewer())
    with TestClient(application(runtime)) as client:
        yield client, runtime
    runtime.close()


def create(client, scenario="review_accepted", key="collaboration-create-001", offline=True):
    response = client.post("/api/creator/briefs", json={"client_request_key": "collaboration-brief-001",
        "script": "SYNTHETIC SOURCE: a paper boat is placed on the table. 灯光熄灭。", "requirements": "Synthetic test only.", "budget_cny": "100.00"})
    assert response.status_code in {200, 201}, response.text
    source = response.json()["brief"]
    body = {"client_request_key": key, "brief_id": source["id"], "brief_revision": source["revision"]}
    if offline:
        body["scenario"] = scenario
    response = client.post(OFFLINE if offline else BASE, json=body)
    assert response.status_code == 201, response.text
    return response.json()["task"], body


def act(client, task, action="resume", key="collaboration-resume-001"):
    return client.post(f"{BASE}/{task['id']}/actions", json={"client_action_key": key, "expected_revision": task["revision"], "action": action})


def wait(client, task_id, statuses):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        response = client.get(f"{BASE}/{task_id}")
        assert response.status_code == 200, response.text
        task = response.json()["task"]
        if task["status"] in statuses:
            return task
        time.sleep(0.01)
    raise AssertionError("Collaboration did not reach expected state")


def document(runtime, task_id):
    with runtime.store.transaction() as state:
        return copy.deepcopy(state["tasks"][task_id])


def test_one_resume_automatically_hands_actual_accepted_plan_to_independent_reviewer(env):
    client, runtime = env
    task, body = create(client)
    initial = task["collaboration"]
    assert initial["status"] == "awaiting_plan" and initial["binding"] is None
    assert initial["report"] is None and initial["repair_task"] is None
    assert act(client, task).status_code == 200
    task = wait(client, task["id"], {"plan_ready", "blocked", "response_unknown"})
    assert task["status"] == "plan_ready" and task["collaboration"]["status"] == "accepted"
    assert task["quality_status"] == "not_reviewed" and task["execution_enabled"] is False
    value = task["collaboration"]
    assert value["planner_call_count"] == 2 and value["reviewer_call_count"] == 1 and task["step_count"] == 3
    assert value["paid_call_limit"] == 0 and value["max_total_calls"] == 6 and value["max_reviewer_calls"] == 1
    assert value["report_hash"] == digest(value["report"]) and value["repair_task"] is None
    binding = value["binding"]
    assert binding["run_id"] == binding["task_id"] == task["id"]
    assert binding["plan_hash"] == digest(task["plan"]) and binding["plan_version"] == task["plan_version"] == 1
    persisted = document(runtime, task["id"])
    payload = persisted["versions"][-1]["payload"]
    assert [operation["role"] for operation in payload["operations"]] == ["planner", "planner", "reviewer"]
    assert all(operation["status"] == "processed" for operation in payload["operations"])
    assert binding["intent_id"] == payload["operations"][-1]["id"]
    review_frame = next(frame for frame in persisted["versions"] if frame["payload"]["events"][-1]["type"] == "review_call_intent")
    assert review_frame["payload"]["plans"][0]["plan"] == task["plan"]
    assert review_frame["payload"]["operations"][-2]["status"] == "processed" and review_frame["payload"]["read_source"] is True
    calls = runtime.reviewer.calls
    assert len(calls) == 1 and [message["role"] for message in calls[0][0]] == ["system", "user"]
    review_data = calls[0][0][1]["content"]
    assert calls[0][0] == review_messages(review_data) and review_data["prior_findings"] == []
    assert not {"messages", "history", "operations", "self_evaluation", "reasoning"} & set(review_data)
    assert review_data["source"]["snapshot"] == persisted["source"] and review_data["plan"]["artifact"] == task["plan"]
    assert all(message["role"] != "tool" for message in calls[0][0])
    assert [message["role"] for message in payload["messages"]][-2:] == ["assistant", "tool"]
    replay = client.post(OFFLINE, json=body)
    assert replay.status_code == 200 and replay.json()["replayed"] is True and len(runtime.reviewer.calls) == 1


@pytest.mark.parametrize("scenario,status,code,kind", [
    ("review_rejected", "rejected", "REVIEW_REQUIRES_REPAIR", "content_repair_ready"),
    ("review_unknown", "unknown", "REVIEW_NEEDS_INPUT", "needs_input"),
    ("review_invalid", "invalid", "REVIEW_INVALID", None),
])
def test_review_branches_save_truthful_bounded_proposals_without_executing_repair(env, scenario, status, code, kind):
    client, runtime = env
    task, _ = create(client, scenario)
    assert act(client, task).status_code == 200
    task = wait(client, task["id"], {"blocked"})
    value = task["collaboration"]
    assert value["status"] == status and task["blocked_reason"] == code and task["allowed_actions"] == []
    assert task["plan_version"] == 1 and task["step_count"] == 3 and len(runtime.reviewer.calls) == 1
    if kind is None:
        assert value["report"] is None and value["report_hash"] is None and value["repair_task"] is None
    else:
        assert value["report"]["overall_verdict"] == status and value["report_hash"] == digest(value["report"])
        repair = value["repair_task"]
        assert repair["kind"] == kind and repair["binding"]["parent_plan_hash"] == digest(task["plan"])
        assert repair["repair_targets"] == [{"shot_id": "S01", "field": "camera"}]
        assert {"shot_id": "S01", "field": "visual"} in repair["preserve_targets"] and repair["affected_shot_ids"] == ["S01", "S02"]


@pytest.mark.parametrize("field,value", [("plan_version", 2), ("plan_hash", "0" * 64), ("source_snapshot_hash", "1" * 64), ("intent_id", "wrong-intent"), ("task_id", "other-task"), ("rule_hash", "2" * 64)])
def test_wrong_output_binding_is_invalid_not_content_rejected(env, field, value):
    client, runtime = env
    original = runtime.reviewer.respond
    def wrong(messages, operation, task):
        report = json.loads(original(messages, operation, task))
        report["binding"][field] = value
        return json.dumps(report)
    runtime.reviewer.respond = wrong
    task, _ = create(client)
    act(client, task)
    task = wait(client, task["id"], {"blocked"})
    assert task["blocked_reason"] == "REVIEW_INVALID" and task["collaboration"]["report"] is None
    assert task["collaboration"]["status"] == "invalid" and task["collaboration"]["reviewer_call_count"] == 1


def test_unknown_reviewer_never_resubmits_through_get_stop_resume_or_new_runtime(env):
    client, runtime = env
    task, _ = create(client, "review_response_unknown")
    act(client, task)
    task = wait(client, task["id"], {"response_unknown"})
    assert task["collaboration"]["status"] == "response_unknown" and task["collaboration"]["report"] is None
    operation_id = task["pending_call"]["operation_id"]
    stopped = act(client, task, "stop", "collaboration-stop-001").json()["task"]
    assert stopped["allowed_actions"] == [] and stopped["step_count"] == 3
    assert act(client, stopped, key="collaboration-forbidden-001").status_code == 409
    restarted = PlannerRuntime(runtime.store.root, runtime.brief_store, TrackingPlanner(), reviewer=TrackingReviewer())
    try:
        recovered = restarted.get(task["id"])["task"]
        assert recovered["pending_call"]["operation_id"] == operation_id and recovered["allowed_actions"] == []
        assert recovered["collaboration"]["binding"] == task["collaboration"]["binding"]
        assert not restarted.actor.calls and not restarted.reviewer.calls
    finally:
        restarted.close()
    assert len(runtime.reviewer.calls) == 1


class BlockingRole:
    def __init__(self, delegate, *, submit_only=False):
        self.delegate, self.submit_only = delegate, submit_only
        self.entered, self.release = threading.Event(), threading.Event()
        self.calls = []

    def respond(self, messages, operation, task):
        self.calls.append(copy.deepcopy((messages, operation, task)))
        result = self.delegate.respond(messages, operation, task)
        if not self.submit_only or json.loads(result)["tool"] == "submit_shot_plan":
            self.entered.set()
            assert self.release.wait(10), "Test did not release blocked role"
        return result


@pytest.mark.parametrize("stop_role", ["planner", "reviewer"])
def test_stop_between_roles_or_during_review_preserves_receipt_and_requires_resume(env, stop_role):
    client, runtime = env
    role = BlockingRole(OfflineActor() if stop_role == "planner" else SyntheticReviewer(), submit_only=stop_role == "planner")
    if stop_role == "planner":
        runtime.actor = role
    else:
        runtime.reviewer = role
    task, _ = create(client)
    act(client, task)
    try:
        assert role.entered.wait(10)
        current = client.get(f"{BASE}/{task['id']}").json()["task"]
        response = act(client, current, "stop", "collaboration-stop-race-001")
        assert response.status_code == 200, response.text
    finally:
        role.release.set()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        current = client.get(f"{BASE}/{task['id']}").json()["task"]
        if current["pending_call"] and current["pending_call"]["status"] == "received":
            break
        time.sleep(0.01)
    assert current["status"] == "stopped" and current["allowed_actions"] == ["resume"]
    assert current["collaboration"]["report"] is None
    if stop_role == "planner":
        assert current["plan_version"] == 0 and current["collaboration"]["reviewer_call_count"] == 0
    else:
        assert current["plan_version"] == 1 and len(role.calls) == 1
    resumed = act(client, current, key="collaboration-resume-race-002")
    assert resumed.status_code == 200, resumed.text
    final = wait(client, task["id"], {"plan_ready"})
    assert final["collaboration"]["status"] == "accepted" and final["step_count"] == 3
    assert final["collaboration"]["reviewer_call_count"] == 1


class WorkerCrash(BaseException):
    pass


@pytest.mark.parametrize("checkpoint", ["intent", "receipt"])
def test_reviewer_crash_recovery_reuses_receipt_or_blocks_unconfirmed_intent(env, monkeypatch, checkpoint):
    client, runtime = env
    crashed = threading.Event()
    if checkpoint == "intent":
        def crash(*args):
            crashed.set()
            raise WorkerCrash()
        runtime.reviewer.respond = crash
    else:
        original = runtime.store.transaction
        @contextmanager
        def crash_after_receipt():
            with original() as state:
                before = {key: value["head_revision"] for key, value in state["tasks"].items()}
                yield state
                crash_now = not crashed.is_set() and any(value["head_revision"] != before.get(key) and
                    value["versions"][-1]["payload"]["events"][-1]["type"] == "review_call_received" for key, value in state["tasks"].items())
            if crash_now:
                crashed.set()
                raise WorkerCrash()
        monkeypatch.setattr(runtime.store, "transaction", crash_after_receipt)
    task, _ = create(client)
    act(client, task)
    assert crashed.wait(10)
    runtime.close()
    restarted = PlannerRuntime(runtime.store.root, runtime.brief_store, TrackingPlanner(), reviewer=TrackingReviewer())
    try:
        with TestClient(application(restarted)) as recovered_client:
            recovered = recovered_client.get(f"{BASE}/{task['id']}").json()["task"]
            assert recovered["step_count"] == 3 and recovered["collaboration"]["reviewer_call_count"] == 1
            if checkpoint == "intent":
                assert recovered["status"] == "response_unknown" and recovered["collaboration"]["status"] == "response_unknown"
                assert recovered["allowed_actions"] == ["stop"]
                assert act(recovered_client, recovered, key="collaboration-crash-resume").status_code == 409
            else:
                assert recovered["status"] == "stopped" and recovered["pending_call"]["status"] == "received"
                assert act(recovered_client, recovered, key="collaboration-crash-resume").status_code == 200
                final = wait(recovered_client, task["id"], {"plan_ready"})
                assert final["collaboration"]["report_hash"] is not None and final["step_count"] == 3
            assert restarted.actor.calls == [] and restarted.reviewer.calls == []
    finally:
        restarted.close()


@pytest.mark.parametrize("planner_calls,expected", [(5, "plan_ready"), (6, "blocked")])
def test_total_six_call_limit_is_shared_by_both_roles(env, planner_calls, expected):
    client, runtime = env
    original = runtime.actor.respond
    def delayed_plan(messages, operation, task):
        if operation["step"] < planner_calls:
            return json.dumps({"tool": "read_frozen_brief", "arguments": {}})
        return original(messages, operation, task)
    runtime.actor.respond = delayed_plan
    task, _ = create(client)
    act(client, task)
    task = wait(client, task["id"], {"plan_ready", "blocked"})
    assert task["status"] == expected and task["step_count"] == 6
    assert task["collaboration"]["planner_call_count"] == planner_calls
    assert task["collaboration"]["reviewer_call_count"] == (1 if planner_calls == 5 else 0)
    if planner_calls == 6:
        assert task["blocked_reason"] == "STEP_LIMIT" and task["collaboration"]["binding"] is None and runtime.reviewer.calls == []


def test_summary_events_and_lookup_hide_context_raw_report_and_source_body(env):
    client, runtime = env
    task, body = create(client)
    act(client, task)
    task = wait(client, task["id"], {"plan_ready"})
    summary = client.get(BASE).json()["tasks"][0]
    lookup = client.get(f"{BASE}/by-key/{body['client_request_key']}").json()["task"]
    assert summary == lookup
    assert "report" not in summary["collaboration"] and "repair_task" not in summary["collaboration"]
    assert "report_hash" in summary["collaboration"] and summary["collaboration"]["binding"] == task["collaboration"]["binding"]
    for result in (summary, client.get(f"{BASE}/{task['id']}/events").json()):
        encoded = json.dumps(result)
        assert "SYNTHETIC SOURCE" not in encoded and "Synthetic test only" not in encoded
        assert not {"context", "response", "messages", "script", "requirements", "report", "repair_task"} & set(result)
        assert str(runtime.store.root) not in encoded


@pytest.mark.parametrize("scenario", ["success", "repair", "invalid_tool", "step_limit", "response_unknown", None])
def test_old_scenarios_and_normal_keep_exact_legacy_shape_and_no_reviewer(env, scenario):
    client, runtime = env
    task, _ = create(client, scenario, offline=scenario is not None)
    assert "collaboration" not in task
    act(client, task)
    task = wait(client, task["id"], {"awaiting_authorization", "plan_ready", "blocked", "response_unknown"})
    persisted = document(runtime, task["id"])
    for frame in persisted["versions"]:
        assert set(frame["payload"]) == PAYLOAD_FIELDS
        assert all("role" not in operation for operation in frame["payload"]["operations"])
        assert frame["hash"] == frame_hash(persisted, frame)
    assert runtime.reviewer.calls == [] and task["quality_status"] == "not_reviewed"
    if scenario is None:
        assert task["step_count"] == 0 and runtime.actor.calls == []


@pytest.mark.parametrize("case", ["context", "binding_version", "binding_hash", "report", "repair", "counts", "extra", "unprocessed_plan", "second_review"])
def test_storage_rebuild_rejects_forged_role_context_or_results_even_with_rehashed_frames(env, case):
    client, runtime = env
    task, _ = create(client, "review_rejected")
    act(client, task)
    task = wait(client, task["id"], {"blocked"})
    with runtime.store.transaction() as state:
        state = copy.deepcopy(state)
    persisted = state["tasks"][task["id"]]
    payload = persisted["versions"][-1]["payload"]
    value, operation = payload["collaboration"], payload["operations"][-1]
    if case == "context":
        operation["context"][1]["content"]["source"]["segments"][0]["text"] = "forged source summary"
        operation["context_hash"] = digest(operation["context"])
    elif case == "binding_version":
        value["binding"]["plan_version"] = 2
    elif case == "binding_hash":
        value["binding"]["plan_hash"] = "0" * 64
    elif case == "report":
        value["report"]["rules"][0]["checked_scope"] = "forged accepted scope"
        value["report_hash"] = digest(value["report"])
    elif case == "repair":
        value["repair_task"]["repair_targets"].append({"shot_id": "S01", "field": "visual"})
    elif case == "counts":
        value["reviewer_call_count"] = 0
    elif case == "extra":
        value["authorization"] = "live"
    elif case == "unprocessed_plan":
        payload["operations"][-2]["status"] = "received"
    else:
        second = copy.deepcopy(operation)
        second.update(id="f" * 32, step=4)
        payload["operations"].append(second)
        payload["step_count"] = 4
        value["reviewer_call_count"] = 2
    persisted["versions"][-1]["hash"] = frame_hash(persisted, persisted["versions"][-1])
    persisted["head_hash"] = persisted["versions"][-1]["hash"]
    with pytest.raises((PlannerError, ValueError, TypeError, KeyError)):
        validate_state(state)


def test_new_scenarios_cannot_enable_normal_execution_or_static_private_access(env):
    client, runtime = env
    task, body = create(client)
    assert set(REVIEW_SCENARIOS) == {"review_accepted", "review_rejected", "review_unknown", "review_response_unknown", "review_invalid"}
    for field, value in (("scenario", "review_accepted"), ("execution_enabled", True), ("mode", "offline_test"), ("reviewer", "live")):
        request = {key: item for key, item in body.items() if key != "scenario"}
        request[field] = value
        assert client.post(BASE, json=request).status_code == 422
    app = application(runtime)
    app.mount("/unsafe", StaticFiles(directory=runtime.store.root.parent), name="unsafe")
    with TestClient(app) as unsafe:
        response = unsafe.get(f"{BASE}/{task['id']}")
        assert response.status_code == 503 and response.json()["detail"]["code"] == "PRIVATE_STORAGE_UNAVAILABLE"


def test_stop_after_plan_acceptance_prevents_reviewer_intent_until_resume(env, monkeypatch):
    client, runtime = env
    entered, release = threading.Event(), threading.Event()
    original = runtime.store.transaction

    @contextmanager
    def pause_after_plan():
        with original() as state:
            before = {key: value["head_revision"] for key, value in state["tasks"].items()}
            yield state
            pause = not entered.is_set() and any(value["head_revision"] != before.get(key) and
                value["versions"][-1]["payload"].get("collaboration", {}).get("status") == "review_ready"
                for key, value in state["tasks"].items())
        if pause:
            entered.set()
            assert release.wait(10)

    monkeypatch.setattr(runtime.store, "transaction", pause_after_plan)
    task, _ = create(client)
    act(client, task)
    try:
        assert entered.wait(10)
        current = client.get(f"{BASE}/{task['id']}").json()["task"]
        assert current["plan_version"] == 1 and current["collaboration"]["status"] == "review_ready"
        stopped = act(client, current, "stop", "collaboration-plan-stop").json()["task"]
        assert stopped["collaboration"]["binding"] is None and stopped["collaboration"]["reviewer_call_count"] == 0
    finally:
        release.set()
    runtime.pool.shutdown(wait=True)
    assert runtime.reviewer.calls == []
    # A rebuilt runtime resumes the same accepted plan; there is no new Planner call.
    restarted = PlannerRuntime(runtime.store.root, runtime.brief_store, TrackingPlanner(), reviewer=TrackingReviewer())
    try:
        with TestClient(application(restarted)) as resumed_client:
            stopped = resumed_client.get(f"{BASE}/{task['id']}").json()["task"]
            assert act(resumed_client, stopped, key="collaboration-plan-resume").status_code == 200
            final = wait(resumed_client, task["id"], {"plan_ready"})
            assert final["step_count"] == 3 and final["collaboration"]["reviewer_call_count"] == 1
            assert restarted.actor.calls == [] and len(restarted.reviewer.calls) == 1
    finally:
        restarted.close()


@pytest.mark.parametrize("response", [None, "x" * 65537, "\ud800"])
def test_reviewer_nontext_oversized_or_invalid_utf8_output_fails_closed(env, response):
    client, runtime = env
    runtime.reviewer.respond = lambda *args: response
    task, _ = create(client)
    act(client, task)
    task = wait(client, task["id"], {"blocked"})
    assert task["blocked_reason"] == "REVIEW_INVALID" and task["collaboration"]["status"] == "invalid"
    assert task["collaboration"]["report"] is None and task["step_count"] == 3
