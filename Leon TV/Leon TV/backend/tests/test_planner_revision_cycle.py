"""D24 real ASGI, durable role receipts and versioned repair with synthetic actors."""

import copy
import json
import socket
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient
from test_planner_collaboration import (
    TrackingPlanner,
    WorkerCrash,
    act,
    application,
    create,
    document,
    wait,
)
from test_planner_recheck import revision_case

from core.creator.store import CreatorBriefStore, digest, source_hash
from core.planner import revision_cycle
from core.planner.actors import ModelUncertain
from core.planner.collaboration import SyntheticReviewer
from core.planner.protocol import REVISION_SCENARIOS
from core.planner.recheck import recheck_messages
from core.planner.revision_cycle import DETAIL_FIELDS, SyntheticRevisionRole
from core.planner.runtime import PlannerRuntime
from core.planner.store import PlannerError, frame_hash, validate_state


@pytest.fixture(autouse=True)
def no_external(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Revision checks must not make outbound calls")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)


class TrackingRole:
    def __init__(self):
        self.calls = []

    def respond(self, messages, operation, task):
        self.calls.append(copy.deepcopy((messages, operation, task)))
        return SyntheticRevisionRole().respond(messages, operation, task)


def runtime_at(root, brief_store=None):
    return PlannerRuntime(
        root / "planner",
        brief_store or CreatorBriefStore(root / "briefs"),
        TrackingPlanner(),
        reviewer=TrackingRole(),
        repairer=TrackingRole(),
        rechecker=TrackingRole(),
    )


@pytest.fixture
def env(tmp_path):
    runtime = runtime_at(tmp_path)
    with TestClient(application(runtime)) as client:
        yield client, runtime
    runtime.close()


def start(client, scenario="revision_accepted"):
    task, _ = create(client, scenario)
    assert "collaboration" not in task and task["revision_cycle"]["status"] == "awaiting_plan"
    response = act(client, task)
    assert response.status_code == 200, response.text
    return task


@pytest.mark.parametrize(
    "scenario,status,version,calls,code",
    [
        ("revision_accepted", "accepted", 2, 5, None),
        ("revision_rejected", "rejected", 2, 5, "RECHECK_REQUIRES_REPAIR"),
        ("revision_needs_input", "needs_input", 1, 3, "REVIEW_NEEDS_INPUT"),
        ("revision_invalid", "repair_invalid", 1, 4, "REPAIR_INVALID"),
        (
            "revision_response_unknown",
            "repair_response_unknown",
            1,
            4,
            "MODEL_RESPONSE_UNCONFIRMED",
        ),
        ("recheck_unknown", "unknown", 2, 5, "RECHECK_NEEDS_INPUT"),
        (
            "recheck_response_unknown",
            "recheck_response_unknown",
            2,
            5,
            "MODEL_RESPONSE_UNCONFIRMED",
        ),
        ("recheck_invalid", "recheck_invalid", 2, 5, "RECHECK_INVALID"),
    ],
)
def test_all_frozen_scenarios_advance_once_or_stop_truthfully(
    env, scenario, status, version, calls, code
):
    client, runtime = env
    task = start(client, scenario)
    final = wait(client, task["id"], {"plan_ready", "blocked", "response_unknown"})
    value = final["revision_cycle"]
    assert value["status"] == status and final["blocked_reason"] == code
    assert final["plan_version"] == version and final["step_count"] == calls
    assert final["quality_status"] == "not_reviewed" and final["execution_enabled"] is False
    assert value["paid_call_limit"] == 0 and value["max_total_calls"] == 6
    assert sum(value[f"{role}_call_count"] for role in value["roles"]) == calls
    assert value["accepted_plan_version"] == (version if status == "accepted" else None)
    assert (
        value["parent_plan"]
        == document(runtime, task["id"])["versions"][-1]["payload"]["plans"][0]["plan"]
    )
    assert value["first_report_hash"] == digest(value["first_report"])
    assert value["first_review_binding"]["plan_version"] == 1
    if version == 2:
        assert value["first_report"]["overall_verdict"] == "rejected"
        assert value["recheck_binding"]["plan_version"] == 2
        assert value["recheck_binding"]["plan_hash"] == digest(final["plan"])
        assert final["plan"] != value["parent_plan"] and final["repair_count"] == 0
    assert len(runtime.revision_reviewer.calls) == 1
    assert len(runtime.repairer.calls) <= 1 and len(runtime.rechecker.calls) <= 1


def test_full_chain_persists_actual_v1_then_receipted_bounded_v2_and_fresh_independent_recheck(env):
    client, runtime = env
    task = start(client)
    final = wait(client, task["id"], {"plan_ready"})
    value = final["revision_cycle"]
    persisted = document(runtime, task["id"])
    payload = persisted["versions"][-1]["payload"]
    assert [operation["role"] for operation in payload["operations"]] == [
        "planner",
        "planner",
        "reviewer",
        "repairer",
        "rechecker",
    ]
    assert all(operation["status"] == "processed" for operation in payload["operations"])
    assert value["repair_binding"]["repair_task_hash"] == digest(value["repair_task"])
    assert value["repair_binding"]["report_hash"] == value["first_report_hash"]
    assert value["repair_binding"]["intent_id"] == payload["operations"][3]["id"]
    assert value["first_review_binding"]["review_id"] != value["recheck_binding"]["review_id"]
    before, after = value["parent_plan"], final["plan"]
    changed = [
        (old["id"], key)
        for old, new in zip(before["shots"], after["shots"])
        for key in old
        if old[key] != new[key]
    ]
    assert changed == [("S01", "camera")]
    for role_actor in (runtime.revision_reviewer, runtime.repairer, runtime.rechecker):
        context = role_actor.calls[0][0]
        assert [message["role"] for message in context] == ["system", "user"]
        assert not {"history", "messages", "operations", "reasoning", "self_evaluation"} & set(
            context[1]["content"]
        )
    recheck_context = runtime.rechecker.calls[0][0]
    assert recheck_context == recheck_messages(recheck_context[1]["content"])
    assert recheck_context[1]["content"]["parent"]["report"] == value["first_report"]
    assert recheck_context[1]["content"]["plan"]["artifact"] == final["plan"]
    assert value["recheck_report"]["resolutions"][0]["status"] == "resolved"
    assert value["recheck_report_hash"] == digest(value["recheck_report"])
    repair_intent = next(
        frame
        for frame in persisted["versions"]
        if frame["payload"]["events"][-1]["type"] == "repair_call_intent"
    )
    assert repair_intent["payload"]["operations"][-2]["status"] == "processed"
    assert len(repair_intent["payload"]["plans"]) == 1
    recheck_intent = next(
        frame
        for frame in persisted["versions"]
        if frame["payload"]["events"][-1]["type"] == "recheck_call_intent"
    )
    assert recheck_intent["payload"]["operations"][-2]["status"] == "processed"
    assert len(recheck_intent["payload"]["plans"]) == 2
    assert len(payload["actions"]) == 1


@pytest.mark.parametrize("first", ["accepted", "invalid", "unknown_response", "no_editable_scope"])
def test_first_review_does_not_manufacture_defects_revisions_or_missing_facts(env, first):
    client, runtime = env

    def respond(messages, operation, task):
        if first == "unknown_response":
            raise ModelUncertain("Synthetic first review interrupted")
        if first == "invalid":
            return "{}"
        raw = SyntheticReviewer().respond(
            messages,
            operation,
            {**task, "scenario": "review_accepted" if first == "accepted" else "review_rejected"},
        )
        if first == "no_editable_scope":
            report = json.loads(raw)
            report["rules"][3]["findings"][0]["repair_scope"] = []
            raw = json.dumps(report)
        return raw

    runtime.revision_reviewer.respond = respond
    task = start(client)
    final = wait(client, task["id"], {"plan_ready", "blocked", "response_unknown"})
    value = final["revision_cycle"]
    assert final["plan_version"] == 1 and final["step_count"] == 3
    assert not runtime.repairer.calls and not runtime.rechecker.calls
    assert (
        value["status"]
        == {
            "accepted": "accepted",
            "invalid": "review_invalid",
            "unknown_response": "review_response_unknown",
            "no_editable_scope": "needs_input",
        }[first]
    )
    assert value["accepted_plan_version"] == (1 if first == "accepted" else None)


@pytest.mark.parametrize(
    "case",
    [
        "intent",
        "source",
        "parent_hash",
        "report",
        "scope_hash",
        "version",
        "bool_version",
        "extra_binding",
        "bare_plan",
        "extra_wire",
        "unchanged",
        "out_of_scope",
        "preserved",
        "order",
        "source_plan",
    ],
)
def test_bad_repair_echo_or_candidate_cannot_save_v2_or_dispatch_rechecker(env, case):
    client, runtime = env

    def bad(messages, operation, task):
        wire = json.loads(SyntheticRevisionRole().respond(messages, operation, task))
        if case == "intent":
            wire["binding"]["intent_id"] = "different-intent"
        elif case == "source":
            wire["binding"]["source_snapshot_hash"] = "0" * 64
        elif case == "parent_hash":
            wire["binding"]["parent_plan_hash"] = "0" * 64
        elif case == "report":
            wire["binding"]["report_hash"] = "0" * 64
        elif case == "scope_hash":
            wire["binding"]["repair_task_hash"] = "0" * 64
        elif case == "version":
            wire["binding"]["next_plan_version"] = 3
        elif case == "bool_version":
            wire["binding"]["parent_plan_version"] = True
        elif case == "extra_binding":
            wire["binding"]["permission"] = "live"
        elif case == "bare_plan":
            wire = wire["plan"]
        elif case == "extra_wire":
            wire["self_evaluation"] = "accepted"
        elif case == "unchanged":
            wire["plan"] = copy.deepcopy(messages[1]["content"]["plan"]["artifact"])
        elif case == "out_of_scope":
            wire["plan"]["shots"][1]["camera"] = "outside scope"
        elif case == "preserved":
            wire["plan"]["shots"][0]["visual"] = "changed preserved field"
        elif case == "order":
            wire["plan"]["shots"].reverse()
        else:
            wire["plan"]["source_snapshot_hash"] = "0" * 64
        return json.dumps(wire)

    runtime.repairer.respond = bad
    task = start(client)
    final = wait(client, task["id"], {"blocked"})
    assert (
        final["blocked_reason"] == "REPAIR_INVALID"
        and final["revision_cycle"]["status"] == "repair_invalid"
    )
    assert final["plan_version"] == 1 and final["step_count"] == 4 and not runtime.rechecker.calls


@pytest.mark.parametrize(
    "planner_calls,version,rechecker", [(3, 2, 1), (4, 2, 0), (5, 1, 0), (6, 1, 0)]
)
def test_all_roles_share_the_original_six_intents_without_reset_on_v2(
    env, planner_calls, version, rechecker
):
    client, runtime = env
    original = runtime.actor.respond

    def delayed(messages, operation, task):
        if operation["step"] < planner_calls:
            return '{"tool":"read_frozen_brief","arguments":{}}'
        return original(messages, operation, task)

    runtime.actor.respond = delayed
    task = start(client)
    final = wait(client, task["id"], {"plan_ready", "blocked"})
    assert final["step_count"] == 6 and final["plan_version"] == version
    assert final["revision_cycle"]["rechecker_call_count"] == rechecker
    assert final["blocked_reason"] == (None if rechecker else "STEP_LIMIT")
    assert final["revision_cycle"]["planner_call_count"] == planner_calls


ACTOR_ATTRIBUTES = {
    "reviewer": "revision_reviewer",
    "repairer": "repairer",
    "rechecker": "rechecker",
}
PHASES = {"reviewer": "review", "repairer": "repair", "rechecker": "recheck"}


@pytest.mark.parametrize("role", ["reviewer", "repairer", "rechecker"])
@pytest.mark.parametrize("checkpoint", ["intent", "receipt"])
def test_each_role_crash_recovers_durable_receipt_or_stops_unknown_without_resubmission(
    env, monkeypatch, role, checkpoint
):
    client, runtime = env
    crashed = threading.Event()
    if checkpoint == "intent":

        def crash(*args):
            crashed.set()
            raise WorkerCrash()

        getattr(runtime, ACTOR_ATTRIBUTES[role]).respond = crash
    else:
        original = runtime.store.transaction

        @contextmanager
        def crash_after_receipt():
            with original() as state:
                before = {key: value["head_revision"] for key, value in state["tasks"].items()}
                yield state
                crash_now = not crashed.is_set() and any(
                    value["head_revision"] != before.get(key)
                    and value["versions"][-1]["payload"]["events"][-1]["type"]
                    == PHASES[role] + "_call_received"
                    for key, value in state["tasks"].items()
                )
            if crash_now:
                crashed.set()
                raise WorkerCrash()

        monkeypatch.setattr(runtime.store, "transaction", crash_after_receipt)
    task = start(client)
    assert crashed.wait(15)
    runtime.close()
    restarted = runtime_at(runtime.store.root.parent, runtime.brief_store)
    try:
        with TestClient(application(restarted)) as recovered_client:
            response = recovered_client.get(f"/api/planner/tasks/{task['id']}")
            assert response.status_code == 200, response.text
            recovered = response.json()["task"]
            old_count = recovered["step_count"]
            assert recovered["revision_cycle"][f"{role}_call_count"] == 1
            if checkpoint == "intent":
                assert recovered["status"] == "response_unknown"
                assert recovered["revision_cycle"]["status"] == PHASES[role] + "_response_unknown"
                stopped = act(recovered_client, recovered, "stop", "revision-crash-stop").json()[
                    "task"
                ]
                assert stopped["allowed_actions"] == []
                assert (
                    act(recovered_client, stopped, key="revision-crash-forbidden").status_code
                    == 409
                )
                assert recovered["plan_version"] == (2 if role == "rechecker" else 1)
                assert all(
                    not getattr(restarted, attribute).calls
                    for attribute in ACTOR_ATTRIBUTES.values()
                )
            else:
                assert (
                    recovered["status"] == "stopped"
                    and recovered["pending_call"]["status"] == "received"
                )
                assert (
                    act(recovered_client, recovered, key="revision-crash-resume").status_code == 200
                )
                final = wait(recovered_client, task["id"], {"plan_ready"})
                assert (
                    final["step_count"] == 5
                    and final["revision_cycle"]["accepted_plan_version"] == 2
                )
                assert not getattr(restarted, ACTOR_ATTRIBUTES[role]).calls
                assert final["step_count"] >= old_count
            assert restarted.actor.calls == []
    finally:
        restarted.close()


@pytest.mark.parametrize("role", ["reviewer", "repairer", "rechecker"])
def test_stop_during_each_role_retains_late_receipt_until_explicit_resume(env, role):
    client, runtime = env
    actor = getattr(runtime, ACTOR_ATTRIBUTES[role])
    original = actor.respond
    entered, release = threading.Event(), threading.Event()

    def blocked(messages, operation, task):
        raw = original(messages, operation, task)
        entered.set()
        assert release.wait(15)
        return raw

    actor.respond = blocked
    task = start(client)
    try:
        assert entered.wait(15)
        current = client.get(f"/api/planner/tasks/{task['id']}").json()["task"]
        response = act(client, current, "stop", "revision-role-stop")
        assert response.status_code == 200, response.text
    finally:
        release.set()
    runtime.close()
    stopped = client.get(f"/api/planner/tasks/{task['id']}").json()["task"]
    assert stopped["status"] == "stopped" and stopped["pending_call"]["status"] == "received"
    assert stopped["revision_cycle"]["accepted_plan_version"] is None
    restarted = runtime_at(runtime.store.root.parent, runtime.brief_store)
    try:
        with TestClient(application(restarted)) as resumed:
            assert act(resumed, stopped, key="revision-role-resume").status_code == 200
            final = wait(resumed, task["id"], {"plan_ready"})
            assert (
                final["step_count"] == 5 and final["revision_cycle"]["accepted_plan_version"] == 2
            )
            assert (
                not getattr(restarted, ACTOR_ATTRIBUTES[role]).calls and not restarted.actor.calls
            )
    finally:
        restarted.close()


def test_stop_after_v2_acceptance_prevents_recheck_delegation_until_resume(env, monkeypatch):
    client, runtime = env
    entered, release = threading.Event(), threading.Event()
    original = runtime.store.transaction

    @contextmanager
    def pause_after_v2():
        with original() as state:
            before = {key: value["head_revision"] for key, value in state["tasks"].items()}
            yield state
            pause = not entered.is_set() and any(
                value["head_revision"] != before.get(key)
                and value["versions"][-1]["payload"].get("revision_cycle", {}).get("status")
                == "recheck_ready"
                for key, value in state["tasks"].items()
            )
        if pause:
            entered.set()
            assert release.wait(15)

    monkeypatch.setattr(runtime.store, "transaction", pause_after_v2)
    task = start(client)
    try:
        assert entered.wait(15)
        current = client.get(f"/api/planner/tasks/{task['id']}").json()["task"]
        assert current["plan_version"] == 2 and current["revision_cycle"]["recheck_binding"] is None
        assert current["revision_cycle"]["first_report"]["overall_verdict"] == "rejected"
        stopped = act(client, current, "stop", "revision-v2-stop").json()["task"]
    finally:
        release.set()
    runtime.close()
    assert not runtime.rechecker.calls
    restarted = runtime_at(runtime.store.root.parent, runtime.brief_store)
    try:
        with TestClient(application(restarted)) as resumed:
            assert act(resumed, stopped, key="revision-v2-resume").status_code == 200
            final = wait(resumed, task["id"], {"plan_ready"})
            assert final["step_count"] == 5 and len(restarted.rechecker.calls) == 1
            assert (
                not restarted.actor.calls
                and not restarted.revision_reviewer.calls
                and not restarted.repairer.calls
            )
    finally:
        restarted.close()


@pytest.mark.parametrize(
    "case",
    [
        "context",
        "repair_binding",
        "first_report",
        "scope",
        "v1",
        "v2",
        "first_binding",
        "recheck_binding",
        "old_report_as_current",
        "resolution",
        "accepted_version",
        "receipt",
        "counts",
        "extra",
        "second_repair",
    ],
)
def test_rehashed_storage_still_rejects_wrong_versions_scopes_receipts_and_old_judgements(
    env, case
):
    client, runtime = env
    task = start(client)
    final = wait(client, task["id"], {"plan_ready"})
    with runtime.store.transaction() as state:
        state = copy.deepcopy(state)
    persisted = state["tasks"][task["id"]]
    frame = persisted["versions"][-1]
    payload, value = frame["payload"], frame["payload"]["revision_cycle"]
    if case == "context":
        payload["operations"][-1]["context"][1]["content"]["parent"]["report_hash"] = "0" * 64
        payload["operations"][-1]["context_hash"] = digest(payload["operations"][-1]["context"])
    elif case == "repair_binding":
        value["repair_binding"]["repair_task_hash"] = "0" * 64
    elif case == "first_report":
        value["first_report"]["rules"][0]["checked_scope"] = "forged first report"
    elif case == "scope":
        value["repair_task"]["repair_targets"].append({"shot_id": "S02", "field": "visual"})
    elif case == "v1":
        payload["plans"][0]["plan"]["shots"][0]["visual"] = "overwritten old version"
    elif case == "v2":
        payload["plans"][1]["plan"]["shots"][1]["visual"] = "unscoped candidate"
    elif case == "first_binding":
        value["first_review_binding"]["plan_version"] = 2
    elif case == "recheck_binding":
        value["recheck_binding"]["plan_version"] = 1
    elif case == "old_report_as_current":
        value["recheck_report"] = copy.deepcopy(value["first_report"])
    elif case == "resolution":
        value["recheck_report"]["resolutions"] = []
    elif case == "accepted_version":
        value["accepted_plan_version"] = 1
    elif case == "receipt":
        payload["operations"][3]["status"] = "received"
    elif case == "counts":
        value["repairer_call_count"] = 0
    elif case == "extra":
        value["authorization"] = "live"
    else:
        second = copy.deepcopy(payload["operations"][3])
        second.update(id="f" * 32, step=6)
        payload["operations"].append(second)
        payload["step_count"] = 6
        value["repairer_call_count"] = 2
    for plan in payload["plans"]:
        plan["plan_hash"] = digest(plan["plan"])
    value["first_report_hash"] = digest(value["first_report"])
    value["recheck_report_hash"] = digest(value["recheck_report"])
    frame["hash"] = frame_hash(persisted, frame)
    persisted["head_hash"] = frame["hash"]
    with pytest.raises((PlannerError, ValueError, TypeError, KeyError)):
        validate_state(state)
    assert final["revision_cycle"]["accepted_plan_version"] == 2


def test_safe_summary_idempotence_and_frozen_scenarios_do_not_enable_normal_execution(env):
    client, runtime = env
    task, body = create(client, "revision_accepted")
    act(client, task)
    final = wait(client, task["id"], {"plan_ready"})
    replay = client.post("/api/planner/offline-tasks", json=body)
    assert replay.status_code == 200 and replay.json()["replayed"] is True
    summary = client.get("/api/planner/tasks").json()["tasks"][0]
    lookup = client.get(f"/api/planner/tasks/by-key/{body['client_request_key']}").json()["task"]
    assert summary == lookup and not DETAIL_FIELDS & set(summary["revision_cycle"])
    assert (
        "collaboration" not in summary and summary["revision_cycle"]["accepted_plan_version"] == 2
    )
    assert "SYNTHETIC SOURCE" not in json.dumps(summary) and str(
        runtime.store.root
    ) not in json.dumps(summary)
    assert set(REVISION_SCENARIOS) == {
        "revision_accepted",
        "revision_rejected",
        "revision_needs_input",
        "revision_invalid",
        "revision_response_unknown",
        "recheck_unknown",
        "recheck_response_unknown",
        "recheck_invalid",
    }
    for scenario in REVISION_SCENARIOS:
        request = {**body, "scenario": scenario}
        assert client.post("/api/planner/tasks", json=request).status_code == 422
    assert (
        final["step_count"] == 5
        and len(runtime.repairer.calls) == len(runtime.rechecker.calls) == 1
    )


def test_recheck_rejection_accounts_for_multiple_old_findings_in_different_rules(env):
    client, runtime = env
    original = runtime.revision_reviewer.respond

    def multiple(messages, operation, task):
        report = json.loads(original(messages, operation, task))
        first = report["rules"][3]["findings"][0]
        second = copy.deepcopy(first)
        second.update(
            finding_id="SYNTHETIC-SECOND-F02",
            rule_id="RV-F",
            targets=[{"shot_id": "S02", "field": "camera"}],
            repair_scope=[{"shot_id": "S02", "field": "camera"}],
        )
        report["rules"][0].update(verdict="rejected", findings=[second])
        return json.dumps(report)

    runtime.revision_reviewer.respond = multiple
    task = start(client, "revision_rejected")
    final = wait(client, task["id"], {"blocked"})
    value = final["revision_cycle"]
    assert final["blocked_reason"] == "RECHECK_REQUIRES_REPAIR" and final["plan_version"] == 2
    prior = {
        finding["finding_id"]
        for rule in value["first_report"]["rules"]
        for finding in rule["findings"]
    }
    resolutions = value["recheck_report"]["resolutions"]
    linked = {
        finding["prior_finding_id"]
        for rule in value["recheck_report"]["rules"]
        for finding in rule["findings"]
    }
    assert (
        len(resolutions) == 2
        and {resolution["prior_finding_id"] for resolution in resolutions} == prior == linked
    )
    assert all(resolution["status"] == "unresolved" for resolution in resolutions)


def test_deeply_nested_repair_json_is_processed_invalid_without_v2_retry_or_recheck(env):
    client, runtime = env
    calls = []

    def deep(messages, operation, task):
        calls.append(operation["id"])
        return "[" * 10000 + "0" + "]" * 10000

    runtime.repairer.respond = deep
    task = start(client)
    final = wait(client, task["id"], {"blocked", "stopped", "response_unknown"})
    assert final["status"] == "blocked" and final["blocked_reason"] == "REPAIR_INVALID"
    assert final["revision_cycle"]["status"] == "repair_invalid" and final["plan_version"] == 1
    assert (
        final["step_count"] == 4
        and final["pending_call"] is None
        and final["allowed_actions"] == []
    )
    payload = document(runtime, task["id"])["versions"][-1]["payload"]
    assert (
        payload["operations"][-1]["role"] == "repairer"
        and payload["operations"][-1]["status"] == "processed"
    )
    assert len(calls) == 1 and not runtime.rechecker.calls
    refreshed = client.get(f"/api/planner/tasks/{task['id']}").json()["task"]
    assert act(client, refreshed, key="revision-deep-forbidden").status_code == 409
    assert len(calls) == 1 and refreshed["plan_version"] == 1


def cache_payloads(marker):
    args = revision_case()
    source, parent, binding, report = args[:4]
    source["script"] = marker + " 合成纸船放在桌面，灯光熄灭。"
    source["source_hash"] = source_hash(source["script"])
    source["snapshot_hash"] = digest(
        {key: value for key, value in source.items() if key != "snapshot_hash"}
    )
    parent["source_snapshot_hash"] = source["snapshot_hash"]
    binding.update(source_snapshot_hash=source["snapshot_hash"], plan_hash=digest(parent))
    report["binding"] = copy.deepcopy(binding)
    first = revision_cycle.build_review_input(source, parent, binding)
    args[4] = revision_cycle.build_repair_task(first, report)
    args[5]["source_snapshot_hash"] = source["snapshot_hash"]
    args[6].update(source_snapshot_hash=source["snapshot_hash"], plan_hash=digest(args[5]))
    return first, revision_cycle.build_recheck_input(*args)


def test_cached_public_contexts_preserve_exact_wire_and_hash_for_distinct_sources():
    for marker in ("SYNTHETIC-PRIVATE-ALPHA", "SYNTHETIC-PRIVATE-BETA"):
        first, recheck = cache_payloads(marker)
        for payload, flag, builder in (
            (first, False, revision_cycle.review_messages),
            (recheck, True, recheck_messages),
        ):
            before = copy.deepcopy(payload)
            original = builder(payload)
            cached = revision_cycle.independent_messages(payload, recheck=flag)
            assert cached == original and digest(cached) == digest(original)
            assert (
                payload == before and marker in cached[1]["content"]["source"]["snapshot"]["script"]
            )
    assert all(
        "SYNTHETIC-PRIVATE-" not in template
        for template in revision_cycle._SYSTEM_TEMPLATES.values()
    )


def test_cached_public_templates_are_thread_safe_and_do_not_rebuild_schema_per_frame(monkeypatch):
    monkeypatch.setattr(revision_cycle, "_SYSTEM_TEMPLATES", {})
    original_review, original_recheck = (
        revision_cycle.review_messages,
        revision_cycle.recheck_messages,
    )
    calls = {"review": 0, "recheck": 0}

    def review(payload):
        calls["review"] += 1
        return original_review(payload)

    def recheck(payload):
        calls["recheck"] += 1
        return original_recheck(payload)

    monkeypatch.setattr(revision_cycle, "review_messages", review)
    monkeypatch.setattr(revision_cycle, "recheck_messages", recheck)
    payloads = [cache_payloads(f"SYNTHETIC-PRIVATE-CONCURRENT-{number}") for number in range(8)]

    def build(pair):
        first, second = pair
        return revision_cycle.independent_messages(first), revision_cycle.independent_messages(
            second, recheck=True
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(build, payloads))
    assert calls == {"review": 1, "recheck": 1}
    for number, contexts in enumerate(results):
        assert all(
            f"SYNTHETIC-PRIVATE-CONCURRENT-{number}"
            in context[1]["content"]["source"]["snapshot"]["script"]
            for context in contexts
        )
    assert all(
        "SYNTHETIC-PRIVATE-" not in template
        for template in revision_cycle._SYSTEM_TEMPLATES.values()
    )


def test_returned_template_mutation_cannot_poison_cache_or_skip_current_payload_validation():
    first, recheck = cache_payloads("SYNTHETIC-PRIVATE-MUTATION")
    for payload, flag, builder in (
        (first, False, revision_cycle.review_messages),
        (recheck, True, recheck_messages),
    ):
        original = builder(payload)
        context = revision_cycle.independent_messages(payload, recheck=flag)
        context[0]["content"]["instruction"] = "forged public instructions"
        context[0]["content"]["output_schema"].clear()
        context[1]["content"]["source"]["snapshot"]["script"] = "mutated returned user source"
        assert revision_cycle.independent_messages(payload, recheck=flag) == original
        broken = copy.deepcopy(payload)
        broken["source"]["segments"][0]["text"] = "forged source summary"
        with pytest.raises(ValueError):
            revision_cycle.independent_messages(broken, recheck=flag)
    data = {"binding": {"synthetic_binding": "one"}}
    original = revision_cycle.repair_messages(data)
    context = revision_cycle.repair_messages(data)
    context[0]["content"]["output_schema"]["properties"]["plan"].clear()
    context[0]["content"]["output_schema"]["properties"]["binding"]["const"].clear()
    assert revision_cycle.repair_messages(data) == original and data["binding"] == {
        "synthetic_binding": "one"
    }
    assert (
        original[0]["content"]["output_schema"]["properties"]["plan"]
        == revision_cycle.ShotPlan.model_json_schema()
    )


def test_unchanged_read_still_validates_all_history_once_and_does_not_replace_file(
    env, monkeypatch
):
    client, runtime = env
    task, _ = create(client, "revision_accepted")
    path = runtime.store.root / "state.json"
    before = path.read_bytes()
    import core.planner.store as store_module

    original = store_module.validate_state
    checked = []

    def validate(state):
        checked.append(sum(len(document["versions"]) for document in state["tasks"].values()))
        return original(state)

    def forbid_write(*args):
        pytest.fail("A net-unchanged read must not replace the existing journal")

    monkeypatch.setattr(store_module, "validate_state", validate)
    monkeypatch.setattr(store_module.os, "replace", forbid_write)
    with runtime.store.transaction() as state:
        assert state["tasks"][task["id"]]["head_revision"] == 1
    assert checked == [1] and path.read_bytes() == before
    checked.clear()
    with runtime.store.transaction() as state:
        state["schema_version"] = 2
        state["schema_version"] = 1
    assert checked == [1] and path.read_bytes() == before


def test_real_net_modification_still_requires_exit_full_validation(env, monkeypatch):
    client, runtime = env
    with runtime.store.transaction():
        pass
    import core.planner.store as store_module

    original, checked = store_module.validate_state, []

    def validate(state):
        checked.append(sum(len(document["versions"]) for document in state["tasks"].values()))
        return original(state)

    monkeypatch.setattr(store_module, "validate_state", validate)
    create(client, "revision_accepted")
    assert checked == [0, 1]


@pytest.mark.parametrize("bad", [True, 1.0])
def test_python_equal_numeric_type_change_is_detected_rejected_and_never_written(env, bad):
    _, runtime = env
    with runtime.store.transaction():
        pass
    path = runtime.store.root / "state.json"
    before = path.read_bytes()
    with pytest.raises(PlannerError) as error:
        with runtime.store.transaction() as state:
            state["schema_version"] = bad
    assert error.value.code == "CORRUPT_STORAGE" and path.read_bytes() == before


@pytest.mark.parametrize(
    "case", ["tuple", "nonfinite", "infinity", "object", "cycle", "utf8", "nonstring_key"]
)
def test_exit_fingerprint_rejects_non_json_or_recursive_values_without_writing(env, case):
    client, runtime = env
    task, _ = create(client, "revision_accepted")
    path = runtime.store.root / "state.json"
    before = path.read_bytes()
    with pytest.raises(PlannerError) as error:
        with runtime.store.transaction() as state:
            if case == "tuple":
                payload = state["tasks"][task["id"]]["versions"][-1]["payload"]
                payload["messages"] = tuple(payload["messages"])
            elif case == "nonfinite":
                state["invalid"] = float("nan")
            elif case == "infinity":
                state["invalid"] = float("inf")
            elif case == "object":
                state["invalid"] = object()
            elif case == "cycle":
                state["invalid"] = state
            elif case == "utf8":
                state["invalid"] = "\ud800"
            else:
                state[1] = "non-JSON object key"
    assert (
        error.value.code == "CORRUPT_STORAGE"
        and error.value.__cause__ is None
        and path.read_bytes() == before
    )


def test_read_fingerprint_never_bypasses_corrupt_history_or_repairs_the_file(env):
    client, runtime = env
    task, _ = create(client, "revision_accepted")
    path = runtime.store.root / "state.json"
    state = json.loads(path.read_text(encoding="utf-8"))
    state["tasks"][task["id"]]["versions"][0]["payload"]["messages"][0]["content"][
        "instruction"
    ] = "corrupted historical frame"
    corrupt = json.dumps(state).encode("utf-8")
    path.write_bytes(corrupt)
    with pytest.raises(PlannerError) as error:
        with runtime.store.transaction():
            pytest.fail("Damaged history must fail before returning a state")
    assert error.value.code == "CORRUPT_STORAGE" and path.read_bytes() == corrupt
