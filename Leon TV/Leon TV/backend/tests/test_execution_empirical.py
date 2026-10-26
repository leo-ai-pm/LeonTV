"""Product empirical-risk flow: actual journal/capture, synthetic authority only."""
import base64
import hashlib
import json
import time
from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient
from test_planner_execution import Env, app_for, policy
from test_planner_execution import no_network as no_network

from core.creator.store import CreatorBriefStore, CreatorError, digest, utc_now
from core.execution.empirical import SCOPE, BudgetAnchor, EmpiricalExecutionPolicy, decode
from core.execution.policy import money
from core.execution.receipts import ReceiptCaptureSettings
from core.execution.runtime import ExecutionRuntime
from core.execution.store import validate_state
from core.execution.transport import ControlledTransport, ResponseUnconfirmed
from core.planner.runtime import PlannerRuntime


@dataclass
class EmpiricalEnv(Env):
    counter: object
    guard_state: object

    def grant(self, task, quote=None, key="synthetic-grant-key-001"):
        quote = quote or self.quote(task)
        response = self.client.post(self.url(task) + "/grants", json={"client_request_key": key, "expected_execution_revision": quote["execution"]["revision"],
            "quote_id": quote["quote"]["id"], "accepted_disclosure_hash": quote["quote"]["disclosure_hash"], "accepted_scope": SCOPE, "consent": True})
        assert response.status_code == 201, response.text
        return response.json()

    def calls(self):
        return [json.loads(line) for line in self.counter.read_text().splitlines()] if self.counter.exists() else []

    def evidence(self, operation_id, amount=None, **changes):
        operation = self.runtime.store.read_only()["operations"][operation_id]
        receipt = operation["normalized"] or decode(operation["raw"], self.runtime.policy)[0]
        result = {"schema_version": 1, "operation_id": operation_id, "wire_hash": operation["wire_hash"],
            "raw_body_sha256": hashlib.sha256(base64.b64decode(operation["raw"]["body_base64"])).hexdigest(), "model": self.runtime.policy["model"],
            "provider_response_id": receipt["provider_response_id"], "provider_request_id": receipt["provider_request_id"],
            "prompt_tokens": receipt["input_tokens"], "completion_tokens": receipt["output_tokens"], "platform_log_id": "synthetic-platform-" + operation_id,
            "platform_fee_cny": money(receipt["local_units"]) if amount is None else amount, "source_export_hash": "e" * 64, "observed_at": utc_now()}
        return result | changes

    def fee(self, task, state, amount=None, **changes):
        op_id = state["operations"][-1]["id"]
        return self.runtime.observe_fee(task["id"], op_id, self.evidence(op_id, amount, **changes))["execution"]

    def complete(self, task, seconds=15):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            current = self.observe(task)
            if current["status"] in {"plan_ready", "blocked"}:
                return current
            if current["blocked_reason"] == "PLATFORM_FEE_REQUIRED":
                current = self.fee(task, current)
                if current["status"] in {"plan_ready", "blocked"}:
                    return current
            elif current["status"] == "needs_reconciliation":
                return current
            time.sleep(0.01)
        raise AssertionError("Synthetic empirical flow did not finish")


@pytest.mark.parametrize("terminal_status", ["plan_ready", "blocked"])
def test_complete_consumes_fee_terminal_result_after_poll_window(monkeypatch, terminal_status):
    class Clock:
        value = 0

        def monotonic(self):
            return self.value

        def sleep(self, seconds):
            self.value += seconds

    clock = Clock()
    waiting = {"status": "needs_reconciliation", "blocked_reason": "PLATFORM_FEE_REQUIRED"}
    terminal = {"status": terminal_status,
        "blocked_reason": None if terminal_status == "plan_ready" else "SYNTHETIC_RULE_REJECTED"}
    calls = []

    class FictionalEnv:
        def observe(self, task):
            calls.append("observe")
            return waiting

        def fee(self, task, current):
            assert current is waiting
            calls.append("fee")
            clock.value = 16
            return terminal

    # Replace only this helper's module reference, not stdlib time or a runtime clock.
    monkeypatch.setitem(EmpiricalEnv.complete.__globals__, "time", clock)
    assert EmpiricalEnv.complete(FictionalEnv(), {"id": "fictional-task"}, seconds=15) is terminal
    assert calls == ["observe", "fee"]


@pytest.fixture
def empirical_env(tmp_path):
    resources = []

    def make(flow="repair", fault_role=None, fault=None, **changes):
        root = (tmp_path / str(len(resources))).resolve()
        old = PlannerRuntime(root / "planner", CreatorBriefStore(root / "briefs"))
        anchor = BudgetAnchor(account_scope_id="synthetic-empirical-account", parent_authorization_hash="a" * 64, parent_ledger_hash="b" * 64,
            parent_allocation_hash="c" * 64, original_budget_cny="100", opening_exposure_cny="69.37", opening_call_count=41,
            scope_risk_cny="6", scope_max_calls=6, account_limit_cny="75.37", account_max_calls=47)
        data = policy().model_dump()
        for name in ("max_input_tokens", "input_bound_basis", "operator_approved", "billing_bound_verified"):
            data.pop(name)
        data.update(account_scope_id=anchor.account_scope_id, parent_anchor_hash=anchor.frozen()["parent_anchor_hash"],
                    account_limit_cny="75.37", account_max_calls=47, task_limit_cny="6", chat_output_parameter="max_completion_tokens",
                    operator_risk_approved=True, request_deadline_seconds=10, privacy_scope=SCOPE, **changes)
        current = EmpiricalExecutionPolicy.model_validate(data)
        counter = root / "posts.jsonl"
        guard_state = {"valid": True, "calls": 0}

        def guard(value):
            guard_state["calls"] += 1
            return guard_state["valid"] and digest(value) == digest(anchor.frozen())
        runtime = ExecutionRuntime(root / "execution", old, policy=current, enabled=True, api_key="synthetic-secret", budget_anchor=anchor, anchor_guard=guard,
            fee_verifier=lambda evidence, operation, policy: evidence["source_export_hash"] == "e" * 64,
            receipt_capture=ReceiptCaptureSettings(worker_factory="empirical_product_fixture:make_transport", worker_options={"execution_root": str(root / "execution"), "counter": str(counter), "flow": flow, "fault_role": fault_role, "fault": fault}))
        client = TestClient(app_for(runtime))
        client.__enter__()
        resources.append((client, runtime, old))
        return EmpiricalEnv(client, runtime, None, old, counter, guard_state)
    yield make
    for client, runtime, old in resources:
        client.__exit__(None, None, None)
        runtime.close()
        old.close()


def start(env):
    task = env.source_task()
    grant = env.grant(task)
    assert env.action(task, grant["execution"]).status_code == 200
    return task, env.wait(task, {"needs_reconciliation", "blocked"}, seconds=15)


def test_empirical_receipt_cannot_execute_tool_until_server_fee_observation(empirical_env):
    env = empirical_env()
    task, pending = start(env)
    assert pending["blocked_reason"] == "PLATFORM_FEE_REQUIRED" and pending["operations"][0]["fee_status"] == "awaiting_platform_fee"
    assert pending["ledger"]["call_count"] == 1 and pending["ledger"]["account_call_count"] == 42
    assert pending["ledger"]["risk_allocated_cny"] == "1.000000" and pending["ledger"]["account_risk_allocated_cny"] == "70.370000"
    assert pending["ledger"]["local_estimate_cny"] == "0.000050" and pending["ledger"]["platform_observed_cny"] is None
    assert pending["allowed_actions"] == ["stop"] and len(env.calls()) == 1
    private = env.runtime.store.read_only()["tasks"][task["id"]]
    assert private["read_source"] is False and len(private["messages"]) == 2
    assert env.action(task, pending, "reconcile", "client-cannot-write-fee").status_code == 409
    assert env.action(task, pending, key="client-cannot-bypass-fee").status_code == 409
    final = env.complete(task)
    assert final["status"] == "plan_ready" and final["role_state"]["accepted_plan_version"] == 2
    assert len(env.calls()) == final["ledger"]["call_count"] == 5 and final["ledger"]["account_call_count"] == 46
    assert final["ledger"]["risk_allocated_cny"] == "5.000000" and final["ledger"]["account_risk_allocated_cny"] == "74.370000"
    assert final["ledger"]["held_cny"] == "5.000000" and final["ledger"]["platform_observed_cny"] == "0.000250"
    assert final["ledger"]["provider_bill_confirmed"] is False and final["quality_status"] == "not_reviewed"
    assert "SYNTHETIC PRIVATE REASONING" not in json.dumps(final) and "capture_descriptor" not in json.dumps(final)


def test_formal_transport_refuses_empirical_authority_without_a_fake_bounder(empirical_env):
    env = empirical_env()
    task, state = start(env)
    op = env.runtime.store.read_only()["operations"][state["operations"][0]["id"]]
    assert "input_token_upper" not in op["permit"] and "bounder_ref" not in op["permit"]
    with pytest.raises(ResponseUnconfirmed) as error:
        ControlledTransport().perform(op, env.runtime.policy, "synthetic-secret")
    assert error.value.code == "PERMIT_REJECTED" and len(env.calls()) == 1


@pytest.mark.parametrize(("role", "fault", "reason"), [("planner", "truncated", "RECEIPT_TRUNCATED"), ("reviewer", "invalid", "REVIEW_INVALID"), ("repairer", "invalid", "REPAIR_INVALID"), ("rechecker", "binding", "RECHECK_INVALID")])
def test_unusable_content_stops_with_risk_retained_even_after_fee(empirical_env, role, fault, reason):
    env = empirical_env(fault_role=role, fault=fault)
    task, _ = start(env)
    current = env.complete(task)
    if current["blocked_reason"] == "RECEIPT_TRUNCATED":
        current = env.fee(task, current)
    assert current["blocked_reason"] == reason
    assert current["ledger"]["held_cny"] == money(len(env.calls()) * 1000000)
    assert "resume" not in current["allowed_actions"] and "reconcile" not in current["allowed_actions"]


@pytest.mark.parametrize("fault", ["timeout", "usage", "model", "secret"])
def test_unknown_receipt_has_no_fee_or_retry_permission(empirical_env, fault):
    env = empirical_env(fault_role="planner", fault=fault)
    task, current = start(env)
    assert current["status"] == "needs_reconciliation" and current["blocked_reason"] != "PLATFORM_FEE_REQUIRED"
    assert current["ledger"]["local_estimate_cny"] is None and current["ledger"]["platform_observed_cny"] is None
    assert env.action(task, current, key="unknown-cannot-retry").status_code == 409
    assert len(env.calls()) == 1 and current["ledger"]["risk_allocated_cny"] == "1.000000"


def test_platform_overrun_evidence_persists_without_clipping_to_hold(empirical_env):
    env = empirical_env()
    task, pending = start(env)
    current = env.fee(task, pending, "9.000000")
    assert current["status"] == "blocked" and current["blocked_reason"] == "EMPIRICAL_RISK_OVERRUN"
    assert current["operations"][0]["platform_observed_cny"] == "9.000000" and current["operations"][0]["fee_status"] == "overrun"
    assert current["ledger"]["risk_allocated_cny"] == "1.000000" and current["ledger"]["account_risk_allocated_cny"] == "70.370000"
    assert len(env.calls()) == 1 and "resume" not in current["allowed_actions"]
    assert env.runtime.store.read_only()["operations"][pending["operations"][0]["id"]]["empirical"]["platform_units"] == 9000000


def test_receipt_estimate_overrun_is_saved_as_a_fact(empirical_env):
    env = empirical_env(fault_role="planner", fault="estimate_overrun")
    task, current = start(env)
    assert current["blocked_reason"] == "EMPIRICAL_RISK_OVERRUN" and current["operations"][0]["local_estimate_cny"] == "1.200010"
    assert current["ledger"]["platform_observed_cny"] is None and len(env.calls()) == 1


def test_parent_anchor_drift_blocks_before_any_risk_or_request(empirical_env):
    env = empirical_env()
    env.guard_state["valid"] = False
    task = env.source_task()
    grant = env.grant(task)
    assert env.action(task, grant["execution"]).status_code == 200
    current = env.wait(task, {"blocked"})
    assert current["blocked_reason"] == "PARENT_ANCHOR_CHANGED" and current["ledger"]["call_count"] == 0
    assert env.calls() == [] and current["ledger"]["account_call_count"] == 41


@pytest.mark.parametrize("field", ["wire_hash", "raw_body_sha256", "model", "provider_response_id", "provider_request_id", "prompt_tokens", "completion_tokens", "source_export_hash"])
def test_server_fee_requires_exact_receipt_binding_and_independent_verifier(empirical_env, field):
    env = empirical_env()
    task, pending = start(env)
    path = env.runtime.store.path("state.json")
    before = path.read_bytes()
    evidence = env.evidence(pending["operations"][-1]["id"])
    evidence[field] = 11 if field == "prompt_tokens" else True if field == "completion_tokens" else "f" * 64
    with pytest.raises(CreatorError) as error:
        env.runtime.observe_fee(task["id"], evidence["operation_id"], evidence)
    assert error.value.code == "FEE_EVIDENCE_REJECTED" and path.read_bytes() == before
    assert len(env.calls()) == 1 and env.observe(task)["blocked_reason"] == "PLATFORM_FEE_REQUIRED"


def test_stopped_fee_match_is_idempotent_and_resume_reuses_received_operation(empirical_env):
    env = empirical_env("first_accepted")
    task, pending = start(env)
    stopped = env.action(task, pending, "stop", "pause-before-fee-proof").json()["execution"]
    op_id = stopped["operations"][-1]["id"]
    evidence = env.evidence(op_id)
    matched = env.runtime.observe_fee(task["id"], op_id, evidence)["execution"]
    assert matched["status"] == "stopped" and matched["operations"][-1]["status"] == "received"
    assert matched["operations"][-1]["fee_status"] == "matched" and matched["allowed_actions"] == ["resume"]
    assert env.runtime.store.read_only()["tasks"][task["id"]]["read_source"] is False
    before = env.runtime.store.path("state.json").read_bytes()
    assert env.runtime.observe_fee(task["id"], op_id, evidence)["replayed"] is True
    assert env.runtime.store.path("state.json").read_bytes() == before and len(env.calls()) == 1
    changed = dict(evidence, platform_fee_cny="0.000051")
    with pytest.raises(CreatorError) as error:
        env.runtime.observe_fee(task["id"], op_id, changed)
    assert error.value.code == "FEE_EVIDENCE_CONFLICT"
    assert env.action(task, matched, key="resume-verified-durable-receipt").status_code == 200
    final = env.complete(task)
    assert final["role_state"]["accepted_plan_version"] == 1 and len(env.calls()) == 3


def test_revoked_grant_records_actual_fee_without_executing_tool_or_auto_continue(empirical_env):
    env = empirical_env()
    task, pending = start(env)
    grant_id = pending["active_grant_id"]
    response = env.client.post(env.url(task) + f"/grants/{grant_id}/revoke", json={"client_action_key": "revoke-before-empirical-fee", "expected_execution_revision": pending["revision"]})
    assert response.status_code == 200
    matched = env.fee(task, response.json()["execution"])
    assert matched["operations"][-1]["fee_status"] == "matched" and matched["operations"][-1]["status"] == "received"
    assert len(env.calls()) == 1 and "resume" not in matched["allowed_actions"]
    assert env.runtime.store.read_only()["tasks"][task["id"]]["read_source"] is False


def test_platform_log_cannot_be_reused_for_another_operation(empirical_env):
    env = empirical_env()
    task, pending = start(env)
    first = env.evidence(pending["operations"][-1]["id"])
    env.runtime.observe_fee(task["id"], first["operation_id"], first)
    second = env.wait(task, {"needs_reconciliation"}, seconds=15)
    evidence = env.evidence(second["operations"][-1]["id"], platform_log_id=first["platform_log_id"])
    with pytest.raises(CreatorError) as error:
        env.runtime.observe_fee(task["id"], evidence["operation_id"], evidence)
    assert error.value.code == "FEE_EVIDENCE_CONFLICT" and len(env.calls()) == 2


def test_parent_anchor_drift_after_fee_blocks_before_next_reservation(empirical_env):
    env = empirical_env()
    task, pending = start(env)
    env.guard_state["valid"] = False
    env.fee(task, pending)
    current = env.wait(task, {"blocked"})
    assert current["blocked_reason"] == "PARENT_ANCHOR_CHANGED" and current["ledger"]["call_count"] == 1
    assert len(env.calls()) == 1 and current["ledger"]["account_risk_allocated_cny"] == "70.370000"


def test_requested_output_tokens_are_not_claimed_as_a_hard_empirical_bound(empirical_env):
    env = empirical_env("first_accepted", fault_role="planner", fault="over_requested")
    task, pending = start(env)
    assert pending["operations"][-1]["output_limit_observed"] is False and pending["blocked_reason"] == "PLATFORM_FEE_REQUIRED"
    final = env.complete(task)
    assert final["status"] == "plan_ready" and len(env.calls()) == 3
    assert all("input_token_upper" not in op["permit"] for op in env.runtime.store.read_only()["operations"].values())


def test_opening_offsets_are_counted_once_across_tasks_and_cannot_reset_scope(empirical_env):
    env = empirical_env("first_accepted")
    for index in range(2):
        task = env.source_task(str(index))
        grant = env.grant(task, env.quote(task, key=f"empirical-shared-quote-{index}"), key=f"empirical-shared-grant-{index}")
        assert env.action(task, grant["execution"], key=f"empirical-shared-start-{index}").status_code == 200
        final = env.complete(task)
        assert final["ledger"]["account_call_count"] == 41 + (index + 1) * 3
    task = env.source_task("third")
    grant = env.grant(task, env.quote(task, key="empirical-third-quote"), key="empirical-third-grant")
    assert grant["execution"]["blocked_reason"] == "CALL_LIMIT" and grant["execution"]["ledger"]["account_call_count"] == 47
    assert grant["execution"]["ledger"]["account_risk_allocated_cny"] == "75.370000" and len(env.calls()) == 6


def test_received_crash_can_be_fee_verified_without_resending_or_running_tool(empirical_env, monkeypatch):
    env = empirical_env("first_accepted")
    original = env.runtime.process_empirical_receipt
    crashed = []

    def once(payload, task, operation, policy):
        if not crashed:
            crashed.append(True)
            raise RuntimeError("SYNTHETIC crash before receipt decode")
        return original(payload, task, operation, policy)
    monkeypatch.setattr(env.runtime, "process_empirical_receipt", once)
    task = env.source_task()
    grant = env.grant(task)
    assert env.action(task, grant["execution"]).status_code == 200
    current = env.wait(task, {"stopped"}, seconds=15)
    assert current["operations"][-1]["receipt_classification"] is None and len(env.calls()) == 1
    matched = env.fee(task, current)
    assert matched["status"] == "stopped" and matched["operations"][-1]["fee_status"] == "matched"
    assert len(env.calls()) == 1 and env.runtime.store.read_only()["tasks"][task["id"]]["read_source"] is False
    assert env.action(task, matched, key="resume-after-raw-fee-proof").status_code == 200
    assert env.complete(task)["status"] == "plan_ready" and len(env.calls()) == 3


def test_one_account_overrun_halts_all_tasks_even_when_total_budget_remains(empirical_env):
    env = empirical_env()
    first, pending = start(env)
    first = env.fee(first, pending, "1.010000")
    assert first["blocked_reason"] == "EMPIRICAL_RISK_OVERRUN"
    second = env.source_task("second")
    granted = env.grant(second, env.quote(second, key="scope-halt-second-quote"), key="scope-halt-second-grant")["execution"]
    assert granted["blocked_reason"] == "EMPIRICAL_RISK_OVERRUN" and granted["allowed_actions"] == []
    assert granted["ledger"]["call_count"] == 0 and granted["ledger"]["account_call_count"] == 42
    assert env.action(second, granted, key="scope-halt-cannot-change-task").status_code == 409 and len(env.calls()) == 1


def test_replacing_revoked_grant_cannot_restore_automatic_fee_continuation(empirical_env):
    env = empirical_env("first_accepted")
    task, pending = start(env)
    response = env.client.post(env.url(task) + f"/grants/{pending['active_grant_id']}/revoke", json={"client_action_key": "revoke-before-replacement", "expected_execution_revision": pending["revision"]})
    assert response.status_code == 200
    revoked = response.json()["execution"]
    renewed = env.grant(task, env.quote(task, revoked["revision"], "replacement-risk-quote"), "replacement-risk-grant")["execution"]
    matched = env.fee(task, renewed)
    assert matched["status"] == "stopped" and matched["blocked_reason"] == "RECOVERY_REQUIRES_RESUME"
    assert matched["operations"][-1]["status"] == "received" and matched["operations"][-1]["fee_status"] == "matched"
    assert len(env.calls()) == 1 and env.runtime.store.read_only()["tasks"][task["id"]]["read_source"] is False
    assert matched["allowed_actions"] == ["resume"]
    assert env.action(task, matched, key="explicit-start-under-new-grant").status_code == 200
    final = env.complete(task)
    assert final["status"] == "plan_ready" and len(env.calls()) == 3


def test_received_frame_requires_actual_raw_even_with_valid_rehashed_history(empirical_env):
    env = empirical_env()
    _, _ = start(env)
    state = json.loads(env.runtime.store.path("state.json").read_text())
    for index, frame in enumerate(state["frames"]):
        records = frame["delta"].get("operations", {})
        if any(operation["status"] == "received" for operation in records.values()):
            state["frames"] = state["frames"][:index + 1]
            for operation in records.values():
                operation["raw"] = None
            frame["hash"] = digest({key: value for key, value in frame.items() if key != "hash"})
            state["head_hash"], state["head_revision"] = frame["hash"], index + 1
            break
    else:
        raise AssertionError("No actual received frame")
    with pytest.raises(CreatorError):
        validate_state(state)
