"""Real execution controller with synthetic policy, provider and source only."""
import copy
import json
import threading
import time

import httpx
import pytest
from fastapi.testclient import TestClient
from test_planner_execution import Env, app_for, policy
from test_planner_execution import no_network as no_network

from core.creator.store import CreatorBriefStore, CreatorError, digest
from core.execution import roles
from core.execution.policy import ExecutionPolicy, TrustedInputBounder
from core.execution.runtime import ExecutionRuntime
from core.execution.store import validate_state
from core.execution.transport import ControlledTransport
from core.planner.actors import OfflineActor
from core.planner.collaboration import SyntheticReviewer
from core.planner.revision_cycle import SyntheticRevisionRole
from core.planner.runtime import PlannerRuntime


class RoleProvider:
    def __init__(self, flow="repair", fault_role=None, fault=None):
        self.flow, self.fault_role, self.fault = flow, fault_role, fault
        self.calls, self.operations = [], []
        self.entered, self.release = threading.Event(), threading.Event()
        self.runtime = None
        self.planner_invalid = 0

    def handle(self, request):
        operation = self.runtime.store.read_only()["operations"][request.headers["x-client-operation-id"]]
        assert operation["status"] == "submitting" and operation["raw"] is None and operation["hold_units"] > 0
        self.calls.append(request)
        self.operations.append(operation)
        role = operation.get("role", "planner")
        body = json.loads(request.content)
        messages = []
        for message in body["messages"]:
            value = json.loads(message["content"])
            messages.append({"role": "tool" if isinstance(value, dict) and "ok" in value else message["role"], "content": value})
        if role == self.fault_role:
            self.entered.set()
            if self.fault == "blocking":
                assert self.release.wait(10)
            if self.fault == "timeout":
                raise httpx.ReadTimeout("SYNTHETIC PRIVATE ERROR")
        if role == "planner":
            content = OfflineActor().respond(messages, operation, {"scenario": "success"})
            if sum(op.get("role", "planner") == "planner" for op in self.operations) <= self.planner_invalid:
                content = '{"invalid_planner_tool":true}'
        elif role == "reviewer":
            scenario = {"first_accepted": "review_accepted", "first_unknown": "review_unknown"}.get(self.flow, "review_rejected")
            content = SyntheticReviewer().respond(messages, operation, {"scenario": scenario})
        else:
            scenario = {"recheck_rejected": "revision_rejected", "recheck_unknown": "recheck_unknown"}.get(self.flow, "revision_accepted")
            content = SyntheticRevisionRole().respond(messages, operation, {"scenario": scenario})
        if role == self.fault_role:
            if self.fault == "invalid":
                content = '{"invalid":true}'
            elif self.fault == "deep_json":
                content = "[" * 10000 + "0" + "]" * 10000
            elif self.fault == "binding":
                wire = json.loads(content)
                wire["binding"]["intent_id"] = "f" * 32
                content = json.dumps(wire)
            elif self.fault == "no_diff":
                wire = json.loads(content)
                wire["plan"] = copy.deepcopy(messages[1]["content"]["plan"]["artifact"])
                content = json.dumps(wire)
            elif self.fault == "out_of_scope":
                wire = json.loads(content)
                wire["plan"]["shots"][0]["visual"] += " SYNTHETIC outside repair scope"
                content = json.dumps(wire)
        value = {"id": "synthetic-response-" + operation["id"], "model": body["model"],
                 "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
                 "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30}}
        if role == self.fault_role and self.fault == "usage":
            value.pop("usage")
        return httpx.Response(200, json=value, headers={"x-request-id": "synthetic-request-" + operation["id"]})


@pytest.fixture
def role_env(tmp_path):
    resources = []

    def make(flow="repair", fault_role=None, fault=None, **changes):
        path = tmp_path.resolve() / str(len(resources))
        planner = PlannerRuntime(path / "planner", CreatorBriefStore(path / "briefs"))
        provider = RoleProvider(flow, fault_role, fault)
        data = {key: value for key, value in policy().frozen().items() if key != "policy_hash"}
        data.update(role_workflow=roles.WORKFLOW, **changes)
        current = ExecutionPolicy.model_validate(data)
        bounder = TrustedInputBounder(current.frozen()["policy_hash"], "synthetic-role-bound", lambda wire, p: len(wire) + 1024)
        runtime = ExecutionRuntime(path / "execution", planner, policy=current, enabled=True, api_key="synthetic-secret", transport=httpx.MockTransport(provider.handle), input_bounder=bounder)
        provider.runtime = runtime
        client = TestClient(app_for(runtime))
        client.__enter__()
        resources.append((client, runtime, planner))
        return Env(client, runtime, provider, planner)
    yield make
    for client, runtime, planner in resources:
        client.__exit__(None, None, None)
        runtime.close()
        planner.close()


def run(env):
    task = env.source_task()
    grant = env.grant(task)
    assert env.action(task, grant["execution"]).status_code == 200
    return task, env.wait(task, {"plan_ready", "blocked", "needs_reconciliation"}, seconds=15)


def test_one_resume_runs_all_four_roles_with_shared_holds_and_fee_records(role_env):
    env = role_env()
    task, final = run(env)
    assert final["status"] == "plan_ready", final
    state = final["role_state"]
    assert state["status"] == "accepted" and state["accepted_plan_version"] == 2
    assert [op["role"] for op in final["operations"]] == ["planner", "planner", "reviewer", "repairer", "rechecker"]
    assert [op["role_call_index"] for op in final["operations"]] == [1, 2, 1, 1, 1]
    assert final["ledger"]["call_count"] == final["ledger"]["account_call_count"] == len(env.provider.calls) == 5
    assert final["ledger"]["locally_accounted_cny"] == "0.000250" and final["ledger"]["held_cny"] == "0.000000"
    assert final["quality_status"] == "not_reviewed" and final["ledger"]["provider_bill_confirmed"] is False
    assert state["plans"][-1]["version"] == 2 and len(state["plans"]) == 2
    assert final["plan"]["source_snapshot_hash"] == task["source"]["snapshot_hash"]
    assert all(op["receipt_status"] == "confirmed_local" for op in final["operations"])
    assert all(key not in json.dumps(state) for key in ("SYNTHETIC-PRIVATE", "PRIVATE-REQUIREMENTS", '"content"', '"first_report":', '"parent_plan":'))
    contexts = [op["context"] for op in env.provider.operations if op["role"] != "planner"]
    assert all([message["role"] for message in context] == ["system", "user"] for context in contexts)
    assert all("SYNTHETIC-PRIVATE" in json.dumps(context[1]) for context in contexts)
    with env.runtime.store.transaction() as payload:
        assert len(payload["tasks"][task["id"]]["messages"]) == 6
        assert payload["tasks"][task["id"]]["source"]["snapshot_hash"] == task["source"]["snapshot_hash"]


@pytest.mark.parametrize(("flow", "status", "version", "calls"), [
    ("first_accepted", "accepted", 1, 3), ("first_unknown", "needs_input", 1, 3),
    ("recheck_rejected", "rejected", 2, 5), ("recheck_unknown", "unknown", 2, 5)])
def test_first_accept_or_unknown_never_manufactures_repair_and_recheck_is_terminal(role_env, flow, status, version, calls):
    env = role_env(flow)
    task, final = run(env)
    assert final["role_state"]["status"] == status and final["role_state"]["plans"][-1]["version"] == version
    assert len(env.provider.calls) == calls
    assert final["allowed_actions"] == []
    assert env.action(task, final, key="no-second-cycle-action").status_code == 409


@pytest.mark.parametrize(("role", "fault", "code", "calls"), [
    ("reviewer", "invalid", "REVIEW_INVALID", 3), ("reviewer", "binding", "REVIEW_INVALID", 3),
    ("repairer", "invalid", "REPAIR_INVALID", 4), ("repairer", "binding", "REPAIR_INVALID", 4),
    ("repairer", "deep_json", "REPAIR_INVALID", 4), ("repairer", "no_diff", "REPAIR_INVALID", 4),
    ("repairer", "out_of_scope", "REPAIR_INVALID", 4), ("rechecker", "invalid", "RECHECK_INVALID", 5),
    ("rechecker", "binding", "RECHECK_INVALID", 5)])
def test_confirmed_cost_settles_even_when_role_content_invalid_without_retry(role_env, role, fault, code, calls):
    env = role_env(fault_role=role, fault=fault)
    task, final = run(env)
    assert final["status"] == "blocked" and final["blocked_reason"] == code, final
    assert final["ledger"]["call_count"] == len(env.provider.calls) == calls
    assert final["ledger"]["held_cny"] == "0.000000"
    assert final["operations"][-1]["status"] == "settled" and final["operations"][-1]["locally_accounted_cny"] == "0.000050"
    assert final["allowed_actions"] == [] and env.action(task, final, key="invalid-content-resume").status_code == 409
    assert final["role_state"]["plans"][-1]["version"] == (2 if role == "rechecker" else 1)


@pytest.mark.parametrize("role", ["reviewer", "repairer", "rechecker"])
@pytest.mark.parametrize("fault", ["timeout", "usage"])
def test_unknown_receipt_holds_shared_funds_and_never_redispatches(role_env, role, fault):
    env = role_env(fault_role=role, fault=fault)
    task, final = run(env)
    calls = len(env.provider.calls)
    assert final["status"] == "needs_reconciliation"
    assert final["ledger"]["held_cny"] != "0.000000" and final["operations"][-1]["locally_accounted_cny"] is None
    assert final["role_state"]["status"] == roles.PHASES[role] + "_response_unknown"
    assert env.action(task, final, key="unknown-role-resume").status_code == 409
    if fault == "usage":
        response = env.action(task, final, "reconcile", "unknown-role-reconcile")
        assert response.status_code == 200
    else:
        assert env.action(task, final, "reconcile", "unknown-role-reconcile").status_code == 409
    assert len(env.provider.calls) == calls


def append_delta(path, delta):
    state = json.loads(path.read_text())
    frame = {"revision": state["head_revision"] + 1, "saved_at": state["frames"][-1]["saved_at"], "previous_hash": state["head_hash"], "delta": delta}
    frame["hash"] = digest(frame)
    state["frames"].append(frame)
    state["head_revision"], state["head_hash"] = frame["revision"], frame["hash"]
    return state


@pytest.mark.parametrize("field", ["role", "role_call_index", "role_binding", "context", "wire", "permit", "hold_units", "raw", "normalized", "local_units", "status"])
def test_rehashed_history_cannot_rewrite_reserved_or_settled_operation(role_env, field):
    env = role_env("first_accepted")
    task, _ = run(env)
    path = env.runtime.store.path("state.json")
    operation = copy.deepcopy(env.provider.operations[-1])
    with env.runtime.store.transaction() as payload:
        operation = copy.deepcopy(payload["operations"][operation["id"]])
    changes = {"role": "rechecker", "role_call_index": 2, "role_binding": {}, "context": [], "wire": "{}", "permit": {},
               "hold_units": operation["hold_units"] + 1, "raw": None, "normalized": {}, "local_units": operation["local_units"] + 1, "status": "received"}
    operation[field] = changes[field]
    for name in ("context", "wire", "permit"):
        operation[name + "_hash"] = digest(operation[name])
    operation["role_binding_hash"] = digest(operation["role_binding"])
    corrupt = append_delta(path, {"operations": {operation["id"]: operation}})
    with pytest.raises(CreatorError, match="integrity"):
        validate_state(corrupt)


def test_policy_default_hash_and_profile_are_opt_in_without_changing_legacy_fields():
    old = policy()
    assert "role_workflow" not in old.model_dump() and "role_workflow" not in old.frozen()
    current = ExecutionPolicy.model_validate({**old.model_dump(), "role_workflow": roles.WORKFLOW})
    assert current.frozen()["policy_hash"] != old.frozen()["policy_hash"]


def test_scope_confirmation_and_frozen_environment_metadata(role_env):
    env = role_env("first_accepted")
    task = env.source_task()
    quote = env.quote(task)
    assert quote["quote"]["role_scope"] == roles.SCOPE and quote["quote"]["role_workflow"] == roles.WORKFLOW
    grant = env.grant(task, quote)
    assert grant["grant"]["role_scope_hash"] == digest(roles.SCOPE)
    env.runtime.enabled = False
    env.runtime.policy = None
    current = env.observe(task)
    assert current["environment"] == "synthetic_test" and current["role_workflow"] == roles.WORKFLOW


def test_stop_during_role_keeps_received_receipt_and_reconcile_does_not_recall(role_env):
    env = role_env(fault_role="reviewer", fault="blocking", request_deadline_seconds=10)
    task = env.source_task()
    grant = env.grant(task)
    assert env.action(task, grant["execution"]).status_code == 200
    assert env.provider.entered.wait(10)
    running = env.observe(task)
    stopped = env.action(task, running, "stop", "stop-reviewer-inflight").json()["execution"]
    assert stopped["status"] == "stopped"
    env.provider.release.set()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        current = env.observe(task)
        if current["operations"][-1]["status"] == "received":
            break
        time.sleep(0.01)
    assert current["status"] == "stopped" and len(env.provider.calls) == 3
    reconciled = env.action(task, current, "reconcile", "reconcile-stopped-reviewer").json()["execution"]
    assert reconciled["role_state"]["status"] == "repair_ready" and len(env.provider.calls) == 3
    assert env.action(task, reconciled, key="resume-after-reconciled-reviewer").status_code == 200
    assert env.wait(task, {"plan_ready"}, seconds=15)["role_state"]["accepted_plan_version"] == 2
    assert len(env.provider.calls) == 5


def set_policy(env, **changes):
    data = {key: copy.deepcopy(value) for key, value in env.runtime.policy.items() if key != "policy_hash"}
    for key, value in changes.items():
        if value is None:
            data.pop(key, None)
        else:
            data[key] = value
    current = ExecutionPolicy.model_validate(data)
    env.runtime.policy = current.frozen()
    env.runtime.input_bounder = TrustedInputBounder(current.frozen()["policy_hash"], "synthetic-role-bound", lambda wire, p: len(wire) + 1024)


def test_roles_use_original_shared_six_call_cap_including_planner_protocol_repair(role_env):
    env = role_env()
    env.provider.planner_invalid = 1
    task, final = run(env)
    assert final["status"] == "plan_ready" and final["role_state"]["accepted_plan_version"] == 2
    assert final["ledger"]["call_count"] == len(env.provider.calls) == 6
    assert final["role_state"]["planner_call_count"] == 3
    assert all(final["role_state"][f"{role}_call_count"] == 1 for role in roles.ROLES[1:])
    assert final["ledger"]["locally_accounted_cny"] == "0.000300"
    assert env.action(task, final, key="no-seventh-request-ever").status_code == 409


def test_new_grant_and_higher_policy_cannot_reset_initial_task_calls(role_env):
    env = role_env(max_calls=5)
    env.provider.planner_invalid = 1
    task, final = run(env)
    assert final["blocked_reason"] == "CALL_LIMIT" and len(env.provider.calls) == 5
    assert final["role_state"]["status"] == "recheck_ready" and len(final["role_state"]["plans"]) == 2
    set_policy(env, max_calls=6)
    quote = env.quote(task, final["revision"], "renewed-role-quote-key")
    renewed = env.grant(task, quote, "renewed-role-grant-key")["execution"]
    assert renewed["ledger"]["call_count"] == renewed["ledger"]["max_calls"] == 5
    assert renewed["blocked_reason"] == "CALL_LIMIT" and renewed["allowed_actions"] == []
    assert env.action(task, renewed, key="renewed-cannot-reset-calls").status_code == 409
    assert len(env.provider.calls) == 5


def test_different_tasks_roles_inherit_same_account_count_and_money(role_env):
    env = role_env("first_accepted", account_max_calls=6)
    for index in range(2):
        task = env.source_task(str(index))
        grant = env.grant(task, env.quote(task, key=f"shared-quote-{index:03d}"), key=f"shared-grant-{index:03d}")
        assert env.action(task, grant["execution"], key=f"shared-action-{index:03d}").status_code == 200
        final = env.wait(task, {"plan_ready"}, seconds=15)
        assert final["ledger"]["account_call_count"] == (index + 1) * 3
    task = env.source_task("third")
    grant = env.grant(task, env.quote(task, key="shared-third-quote"), key="shared-third-grant")
    final = grant["execution"]
    assert final["blocked_reason"] == "CALL_LIMIT" and final["ledger"]["call_count"] == 0
    assert final["ledger"]["account_locally_accounted_cny"] == "0.000300"
    assert env.action(task, final, key="shared-third-resume").status_code == 409
    assert len(env.provider.calls) == 6


def test_next_role_requires_full_conservative_hold_not_empirical_average(role_env):
    env = role_env(task_limit_cny="0.104050", account_limit_cny="0.104050")
    task, final = run(env)
    assert final["blocked_reason"] == "BUDGET_EXHAUSTED" and final["role_state"]["status"] == "review_ready"
    assert final["ledger"]["call_count"] == len(env.provider.calls) == 2
    assert final["ledger"]["locally_accounted_cny"] == "0.000100" and final["ledger"]["held_cny"] == "0.000000"
    assert final["role_state"]["accepted_plan_version"] is None


@pytest.mark.parametrize("initial_profile", [False, True])
def test_existing_task_cannot_change_workflow_by_requote(role_env, initial_profile):
    env = role_env()
    if not initial_profile:
        set_policy(env, role_workflow=None)
    task = env.source_task()
    first = env.quote(task)
    set_policy(env, role_workflow=None if initial_profile else roles.WORKFLOW)
    response = env.client.post(env.url(task) + "/quotes", json={"client_request_key": "no-workflow-upgrade", "expected_execution_revision": first["execution"]["revision"], "expected_task_revision": task["revision"]})
    assert response.status_code == 409 and response.json()["detail"]["code"] == "POLICY_STALE"
    assert env.provider.calls == []
    current = env.observe(task)
    assert ("role_workflow" in current) == initial_profile


def test_legacy_environment_comes_from_its_own_quotes_without_rewriting(role_env):
    env = role_env()
    set_policy(env, role_workflow=None, validation_only=False)
    task = env.source_task()
    env.quote(task)
    before = env.runtime.store.path("state.json").read_bytes()
    set_policy(env, validation_only=True)
    env.runtime.enabled = False
    current = env.observe(task)
    assert current["environment"] == "live" and "role_state" not in current
    assert env.runtime.store.path("state.json").read_bytes() == before


@pytest.mark.parametrize("field", ["scope", "policy_hash", "disclosure_hash", "total_limit_cny", "max_calls", "expires_at"])
def test_first_grant_record_is_bound_to_its_quote_not_just_frozen_afterward(role_env, field):
    env = role_env()
    task = env.source_task()
    env.grant(task)
    state = json.loads(env.runtime.store.path("state.json").read_text())
    for frame in state["frames"]:
        for grant in frame["delta"].get("grants", {}).values():
            grant[field] = {"scope": "forged", "policy_hash": "0" * 64, "disclosure_hash": "0" * 64,
                            "total_limit_cny": "0.000001", "max_calls": 1, "expires_at": "2026-01-01T00:00:00+00:00"}[field]
        frame["previous_hash"] = None if frame["revision"] == 1 else state["frames"][frame["revision"] - 2]["hash"]
        frame["hash"] = digest({key: value for key, value in frame.items() if key != "hash"})
    state["head_hash"] = state["frames"][-1]["hash"]
    with pytest.raises((CreatorError, ValueError)):
        validate_state(state)


@pytest.mark.parametrize(("namespace", "field"), [("tasks", "role_state"), ("operations", "role"), ("quotes", "role_scope"), ("grants", "role_scope_hash"), ("permit", "role_binding_hash")])
def test_partial_role_tags_in_legacy_journal_fail_closed(role_env, namespace, field):
    env = role_env()
    set_policy(env, role_workflow=None)
    task, final = run(env)
    assert final["status"] == "plan_ready" and "role_state" not in final
    with env.runtime.store.transaction() as payload:
        if namespace == "permit":
            operation = next(iter(payload["operations"].values()))
            operation["permit"][field] = "forged"
            operation["permit_hash"] = digest(operation["permit"])
        else:
            record = next(iter(payload[namespace].values()))
            record[field] = {}
        with pytest.raises(CreatorError):
            # Explicitly validate, then restore so the fixture teardown is clean.
            from core.execution.store import validate_payload
            validate_payload(payload)
        if namespace == "permit":
            operation["permit"].pop(field)
            operation["permit_hash"] = digest(operation["permit"])
        else:
            record.pop(field)


@pytest.mark.parametrize("value", [True, 1.0, (), float("nan")])
def test_transaction_rejects_alias_and_non_json_role_mutation_without_writing(role_env, value):
    env = role_env()
    task = env.source_task()
    env.grant(task)
    path = env.runtime.store.path("state.json")
    before = path.read_bytes()
    with pytest.raises(CreatorError) as error:
        with env.runtime.store.transaction() as payload:
            payload["tasks"][task["id"]]["role_state"]["schema_version"] = value
    assert error.value.code == "CORRUPT_STORAGE" and path.read_bytes() == before


@pytest.mark.parametrize("role", ["reviewer", "repairer", "rechecker"])
def test_crash_after_role_receipt_recovers_and_processes_exactly_once(role_env, monkeypatch, role):
    env = role_env()
    original = env.runtime.process_receipt
    crashed = threading.Event()

    def once(payload, task, operation):
        if operation["role"] == role and not crashed.is_set():
            crashed.set()
            raise RuntimeError("SYNTHETIC crash after durable receipt")
        return original(payload, task, operation)
    monkeypatch.setattr(env.runtime, "process_receipt", once)
    task = env.source_task()
    grant = env.grant(task)
    assert env.action(task, grant["execution"]).status_code == 200
    assert crashed.wait(10)
    recovered = env.wait(task, {"stopped"}, seconds=10)
    calls = len(env.provider.calls)
    assert recovered["blocked_reason"] == "RECOVERY_REQUIRES_RESUME" and recovered["operations"][-1]["status"] == "received"
    response = env.action(task, recovered, "reconcile", "recover-durable-role-receipt")
    assert response.status_code == 200, response.text
    reconciled = response.json()["execution"]
    assert len(env.provider.calls) == calls and reconciled["operations"][-1]["status"] == "settled"
    assert reconciled["role_state"][f"{role}_call_count"] == 1
    if role != "rechecker":
        assert env.action(task, reconciled, key="continue-after-receipt-recovery").status_code == 200
    final = env.wait(task, {"plan_ready"}, seconds=15)
    assert final["role_state"]["accepted_plan_version"] == 2 and len(env.provider.calls) == 5
    assert env.action(task, final, "reconcile", "cannot-reprocess-settled-role").status_code == 409


@pytest.mark.parametrize("role", ["reviewer", "repairer", "rechecker"])
def test_crash_after_intent_retains_hold_and_cannot_recall(role_env, monkeypatch, role):
    env = role_env()
    original = ControlledTransport.perform
    crashed = threading.Event()

    def die(transport, operation, policy, api_key):
        if operation["role"] == role:
            crashed.set()
            raise SystemExit("SYNTHETIC process exit after intent, before HTTP")
        return original(transport, operation, policy, api_key)
    monkeypatch.setattr(ControlledTransport, "perform", die)
    task = env.source_task()
    grant = env.grant(task)
    assert env.action(task, grant["execution"]).status_code == 200
    assert crashed.wait(10)
    final = env.wait(task, {"needs_reconciliation"}, seconds=10)
    calls = len(env.provider.calls)
    assert final["ledger"]["call_count"] == calls + 1 and final["ledger"]["held_cny"] != "0.000000"
    assert final["operations"][-1]["status"] == "needs_reconciliation" and final["operations"][-1]["receipt_status"] == "unconfirmed"
    assert env.action(task, final, key="intent-recovery-cannot-resume").status_code == 409
    assert env.action(task, final, "reconcile", "intent-no-raw-cannot-reconcile").status_code == 409
    assert len(env.provider.calls) == calls


def test_revoke_during_review_settles_inflight_cost_and_blocks_next_role(role_env):
    env = role_env(fault_role="reviewer", fault="blocking", request_deadline_seconds=10)
    task = env.source_task()
    grant = env.grant(task)
    assert env.action(task, grant["execution"]).status_code == 200
    assert env.provider.entered.wait(10)
    current = env.observe(task)
    response = env.client.post(env.url(task) + f"/grants/{grant['grant']['id']}/revoke", json={"client_action_key": "revoke-inflight-reviewer", "expected_execution_revision": current["revision"]})
    assert response.status_code == 200, response.text
    env.provider.release.set()
    final = env.wait(task, {"blocked"}, seconds=15)
    assert final["blocked_reason"] == "GRANT_REVOKED" and final["role_state"]["status"] == "repair_ready"
    assert len(env.provider.calls) == 3 and final["ledger"]["locally_accounted_cny"] == "0.000150"
    assert final["ledger"]["held_cny"] == "0.000000" and env.action(task, final, key="revoked-no-next-role").status_code == 409


def test_new_grant_cannot_resume_or_release_unknown_role_hold(role_env):
    env = role_env(fault_role="repairer", fault="timeout")
    task, first = run(env)
    held = first["ledger"]["held_cny"]
    quote = env.quote(task, first["revision"], "unknown-renewed-quote")
    second = env.grant(task, quote, "unknown-renewed-grant")["execution"]
    assert second["ledger"]["call_count"] == 4 and second["ledger"]["held_cny"] == held
    assert second["role_state"]["repairer_call_count"] == 1
    assert env.action(task, second, key="unknown-new-grant-still-no-resume").status_code == 409
    assert len(env.provider.calls) == 4
