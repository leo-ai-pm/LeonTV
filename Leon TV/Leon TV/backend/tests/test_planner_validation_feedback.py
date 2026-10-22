"""D13 safe feedback: real controlled execution with synthetic format failures."""
import copy
import json

import httpx
import pytest
import test_brief_planner
from test_brief_planner import action, brief, create, wait
from test_planner_chat_output import restore_legacy
from test_planner_execution import env as env
from test_planner_execution import no_network as no_network

from core.planner.actors import OfflineActor
from core.planner.protocol import execute_tool

planner_env = test_brief_planner.env


@pytest.mark.parametrize("case,path,code", [
    ("json_string", "$", "OBJECT_REQUIRED"),
    ("missing_plan_wrapper", "$.arguments.plan", "REQUIRED"),
])
def test_format_failure_detail_reaches_next_private_tool_context(env, monkeypatch, case, path, code):
    # Explicit opt-in is required; the default three-argument contract stays generic.
    probe = json.dumps("synthetic action encoded as a JSON string")
    assert execute_tool(probe, {}, False) == ({"ok": False, "code": "INVALID_ACTION"}, None, False)
    detailed, _, _ = execute_tool(probe, {}, False, detailed_feedback=True)
    assert detailed["validation_issues"] == [{"path": "$", "code": "OBJECT_REQUIRED"}]
    import core.execution.runtime as module

    def opted_in(raw, source, read):
        return execute_tool(raw, source, read, detailed_feedback=True)

    # Test-only binding models the root pilot's explicit opt-in; no production caller changes.
    monkeypatch.setattr(module, "execute_tool", opted_in)
    calls = []

    def handle(request):
        calls.append(request)
        body = json.loads(request.content)
        messages = []
        for message in body["messages"]:
            value = json.loads(message["content"])
            role = "tool" if isinstance(value, dict) and "ok" in value else message["role"]
            messages.append({"role": role, "content": value})
        result = OfflineActor().respond(messages, {"id": request.headers["x-client-operation-id"]}, {"scenario": "success"})
        if case == "json_string" and len(calls) == 1:
            result = json.dumps(result)
        elif case == "missing_plan_wrapper" and len(calls) == 2:
            action = json.loads(result)
            action["arguments"] = action["arguments"]["plan"]
            result = json.dumps(action)
        return httpx.Response(200, headers={"x-request-id": "request-" + request.headers["x-client-operation-id"]}, json={
            "id": "response-" + request.headers["x-client-operation-id"], "model": body["model"],
            "choices": [{"message": {"content": result}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30}})

    env.runtime.transport = httpx.MockTransport(handle)
    task = env.source_task()
    grant = env.grant(task)
    env.action(task, grant["execution"])
    final = env.wait(task, {"plan_ready"})
    assert final["ledger"]["call_count"] == len(calls) == 3
    assert final["ledger"]["locally_accounted_cny"] == "0.000150"
    assert final["ledger"]["held_cny"] == "0.000000" and final["quality_status"] == "not_reviewed"
    assert "validation_issues" not in json.dumps(final)
    correction_request = json.loads(calls[1 if case == "json_string" else 2].content)
    feedback = json.loads(correction_request["messages"][-1]["content"])
    assert feedback["ok"] is False and feedback["code"] == "INVALID_ACTION"
    assert {"path": path, "code": code} in feedback["validation_issues"]


def source_and_plan():
    source = {"snapshot_hash": "a" * 64, "script": "SYNTHETIC-PRIVATE-SOURCE"}
    plan = {"source_snapshot_hash": source["snapshot_hash"], "duration_ms": 15000, "ratio": "9:16", "shots": [
        {"id": "S01", "duration_ms": 15000, "visual": "SYNTHETIC visual", "camera": "", "audio": "", "depends_on": []}]}
    return source, plan


def detailed(value, read=True):
    source, _ = source_and_plan()
    return execute_tool(json.dumps(value), source, read, detailed_feedback=True)


@pytest.mark.parametrize("value,path,code", [
    ("JSON string", "$", "OBJECT_REQUIRED"),
    ([], "$", "OBJECT_REQUIRED"),
    ({}, "$.tool", "REQUIRED"),
    ({"tool": "SECRET-UNKNOWN-TOOL", "arguments": {}}, "$.tool", "UNSUPPORTED_VALUE"),
    ({"tool": "read_frozen_brief", "arguments": []}, "$.arguments", "OBJECT_REQUIRED"),
    ({"tool": "submit_shot_plan", "arguments": {}}, "$.arguments.plan", "REQUIRED"),
    ({"tool": "submit_shot_plan", "arguments": {"plan": []}}, "$.arguments.plan", "OBJECT_REQUIRED"),
])
def test_optin_explains_format_error_without_changing_legacy_result(value, path, code):
    source, _ = source_and_plan()
    raw = json.dumps(value)
    assert execute_tool(raw, source, True) == ({"ok": False, "code": "INVALID_ACTION"}, None, True)
    feedback, plan, read = execute_tool(raw, source, True, detailed_feedback=True)
    assert feedback["ok"] is False and feedback["code"] == "INVALID_ACTION" and plan is None and read is True
    assert {"path": path, "code": code} in feedback["validation_issues"]


@pytest.mark.parametrize("field,value,code", [
    ("source_snapshot_hash", "SECRET-INVALID-HASH", "INVALID_FORMAT"),
    ("duration_ms", "15000", "INTEGER_REQUIRED"),
    ("duration_ms", True, "INTEGER_REQUIRED"),
    ("duration_ms", 14999, "NUMERIC_BOUND"),
    ("ratio", "16:9-PRIVATE", "UNSUPPORTED_VALUE"),
    ("shots", {}, "ARRAY_REQUIRED"),
    ("shots", [], "LENGTH_BOUND"),
])
def test_nested_plan_schema_error_has_fixed_full_path(field, value, code):
    _, plan = source_and_plan()
    plan[field] = value
    feedback, output, _ = detailed({"tool": "submit_shot_plan", "arguments": {"plan": plan}})
    assert output is None and feedback["validation_issues"] == [{"path": "$.arguments.plan." + field, "code": code}]
    assert "SECRET-INVALID-HASH" not in json.dumps(feedback) and "16:9-PRIVATE" not in json.dumps(feedback)


@pytest.mark.parametrize("field,value,code", [
    ("id", "SECRET INVALID ID", "INVALID_FORMAT"),
    ("duration_ms", "15000", "INTEGER_REQUIRED"),
    ("duration_ms", 0, "NUMERIC_BOUND"),
    ("visual", 123, "STRING_REQUIRED"),
    ("visual", "", "LENGTH_BOUND"),
    ("visual", "  ", "INVALID_VALUE"),
    ("camera", "x" * 501, "LENGTH_BOUND"),
    ("audio", False, "STRING_REQUIRED"),
    ("depends_on", {}, "ARRAY_REQUIRED"),
    ("depends_on", [123], "STRING_REQUIRED"),
    ("depends_on", ["SECRET INVALID ID"], "INVALID_FORMAT"),
])
def test_shot_schema_paths_use_fixed_wildcard_never_input_ids(field, value, code):
    _, plan = source_and_plan()
    plan["shots"][0][field] = value
    feedback, output, _ = detailed({"tool": "submit_shot_plan", "arguments": {"plan": plan}})
    suffix = "[*]" if field == "depends_on" and isinstance(value, list) else ""
    assert feedback["validation_issues"] == [{"path": "$.arguments.plan.shots[*]." + field + suffix, "code": code}]
    assert output is None and "SECRET INVALID ID" not in json.dumps(feedback)


@pytest.mark.parametrize("level", ["action", "read", "submit", "plan", "shot"])
@pytest.mark.parametrize("name", ["SECRET-EXTRA-KEY/?token=PRIVATE", "visual", "source_snapshot_hash", "plan"])
def test_extra_names_never_echo_or_jump_to_a_familiar_field_in_another_schema(level, name):
    _, plan = source_and_plan()
    action = {"tool": "submit_shot_plan", "arguments": {"plan": plan}}
    if level == "action":
        target, path = action, "$"
    elif level == "read":
        action = {"tool": "read_frozen_brief", "arguments": {}}
        target, path = action["arguments"], "$.arguments"
    elif level == "submit":
        target, path = action["arguments"], "$.arguments"
    elif level == "plan":
        target, path = plan, "$.arguments.plan"
    else:
        target, path = plan["shots"][0], "$.arguments.plan.shots[*]"
    # If a name is real at this exact level, use a different familiar name there.
    extra = name if name not in target else "arguments"
    target[extra] = {"private_value": "PRIVATE-INPUT-VALUE"}
    feedback, output, _ = detailed(action)
    assert feedback["validation_issues"] == [{"path": path, "code": "UNEXPECTED_FIELD"}]
    assert output is None and "SECRET-EXTRA-KEY" not in json.dumps(feedback) and "PRIVATE-INPUT-VALUE" not in json.dumps(feedback)


@pytest.mark.parametrize("raw", [
    "not JSON SECRET-INPUT", '{"tool":"read_frozen_brief","tool":"SECRET-INPUT","arguments":{}}',
    '{"tool":"read_frozen_brief","arguments":{"SECRET-INPUT":NaN}}',
    "[" * 10000 + "0" + "]" * 10000,
])
def test_json_failure_is_fixed_bounded_and_default_remains_exact(raw):
    assert execute_tool(raw, {}, False) == ({"ok": False, "code": "INVALID_ACTION"}, None, False)
    assert execute_tool(raw, {}, False, detailed_feedback=True) == (
        {"ok": False, "code": "INVALID_ACTION", "validation_issues": [{"path": "$", "code": "JSON_INVALID"}]}, None, False)


def test_error_projection_ignores_msg_input_ctx_url_and_unknown_deep_names():
    from core.planner.protocol import _SUBMIT_PATHS, _schema_issues

    class Errors:
        def errors(self, **options):
            assert options == {"include_url": False, "include_context": False, "include_input": False}
            return [
                {"type": "unknown-PRIVATE-CATEGORY", "loc": ("plan", "shots", 777, "SECRET", "visual"),
                 "msg": "SECRET MESSAGE", "input": "SECRET INPUT", "ctx": {"secret": "SECRET"}, "url": "https://secret.invalid/"},
                {"type": "extra_forbidden", "loc": ("plan", "shots", 0, "source_snapshot_hash"), "msg": "SECRET"},
            ]

    assert _schema_issues(Errors(), _SUBMIT_PATHS) == [
        {"path": "$.arguments.plan.shots[*]", "code": "INVALID_STRUCTURE"},
        {"path": "$.arguments.plan.shots[*]", "code": "UNEXPECTED_FIELD"}]


def test_issue_list_is_deduplicated_sorted_and_capped_with_permuted_input():
    _, plan = source_and_plan()
    plan["shots"] = [{"id": 3, "duration_ms": "1", "visual": 4, "camera": 5, "audio": 6, "depends_on": [7]} for _ in range(2)]
    plan.update(source_snapshot_hash="secret", duration_ms=True, ratio="secret")
    first, _, _ = detailed({"tool": "submit_shot_plan", "arguments": {"plan": plan}})
    reordered = dict(reversed(list(plan.items())))
    reordered["shots"] = [dict(reversed(list(shot.items()))) for shot in reversed(plan["shots"])]
    second, _, _ = detailed({"arguments": {"plan": reordered}, "tool": "submit_shot_plan"})
    issues = first["validation_issues"]
    pairs = [(row["path"], row["code"]) for row in issues]
    assert len(issues) == 8 and len(set(pairs)) == 8 and pairs == sorted(pairs)
    assert second == first and all(set(row) == {"path", "code"} for row in issues)


@pytest.mark.parametrize("flag", [False, None, 1, "true", {}, []])
def test_only_boolean_true_can_opt_in(flag):
    assert execute_tool(json.dumps("bad"), {}, False, detailed_feedback=flag) == ({"ok": False, "code": "INVALID_ACTION"}, None, False)


@pytest.mark.parametrize("error", [None, "SOURCE_NOT_READ", "SOURCE_BINDING", "DURATION_TOTAL", "DUPLICATE_SHOT_ID", "INVALID_DEPENDENCY", "DEPENDENCY_CYCLE"])
def test_optin_does_not_change_success_or_original_semantic_verdicts(error):
    source, plan = source_and_plan()
    read = error != "SOURCE_NOT_READ"
    if error == "SOURCE_BINDING":
        plan["source_snapshot_hash"] = "b" * 64
    elif error == "DURATION_TOTAL":
        plan["shots"][0]["duration_ms"] = 14999
    elif error == "DUPLICATE_SHOT_ID":
        plan["shots"][0]["duration_ms"] = 7500
        plan["shots"].append(copy.deepcopy(plan["shots"][0]))
    elif error == "INVALID_DEPENDENCY":
        plan["shots"][0]["depends_on"] = ["MISSING"]
    elif error == "DEPENDENCY_CYCLE":
        plan["shots"][0]["duration_ms"] = 7500
        second = copy.deepcopy(plan["shots"][0])
        second["id"], second["depends_on"] = "S02", ["S01"]
        plan["shots"][0]["depends_on"] = ["S02"]
        plan["shots"].append(second)
    raw = json.dumps({"tool": "submit_shot_plan", "arguments": {"plan": plan}})
    legacy = execute_tool(raw, source, read)
    assert execute_tool(raw, source, read, detailed_feedback=True) == legacy
    assert legacy[0]["code"] == (error or "STRUCTURE_VALID_NOT_REVIEWED")
    assert "validation_issues" not in legacy[0]
    read_raw = json.dumps({"tool": "read_frozen_brief", "arguments": {}})
    assert execute_tool(read_raw, source, False, detailed_feedback=True) == execute_tool(read_raw, source, False)


@pytest.mark.parametrize("optin", [True, False])
def test_controlled_model_can_only_repair_wrapper_from_safe_feedback(env, monkeypatch, optin):
    import core.execution.runtime as module

    if optin:
        monkeypatch.setattr(module, "execute_tool", lambda raw, source, read: execute_tool(raw, source, read, detailed_feedback=True))
    calls = []

    def handle(request):
        calls.append(request)
        body = json.loads(request.content)
        messages = []
        for message in body["messages"]:
            value = json.loads(message["content"])
            role = "tool" if isinstance(value, dict) and "ok" in value else message["role"]
            messages.append({"role": role, "content": value})
        raw = OfflineActor().respond(messages, {"id": "synthetic"}, {"scenario": "success"})
        value = json.loads(raw)
        if value["tool"] == "submit_shot_plan":
            has_fix = any(message["role"] == "tool" and
                {"path": "$.arguments.plan", "code": "REQUIRED"} in message["content"].get("validation_issues", []) for message in messages)
            if not has_fix:
                value["arguments"] = value["arguments"]["plan"]
        return httpx.Response(200, headers={"x-request-id": "request-" + request.headers["x-client-operation-id"]}, json={
            "id": "response-" + request.headers["x-client-operation-id"], "model": body["model"],
            "choices": [{"message": {"content": json.dumps(value)}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30}})

    env.runtime.transport = httpx.MockTransport(handle)
    task = env.source_task()
    granted = env.grant(task)
    env.action(task, granted["execution"])
    final = env.wait(task, {"plan_ready" if optin else "blocked"})
    assert len(calls) == final["ledger"]["call_count"] == 3
    assert final["ledger"]["locally_accounted_cny"] == "0.000150" and final["ledger"]["held_cny"] == "0.000000"
    assert final["blocked_reason"] == (None if optin else "REPAIR_LIMIT")
    assert "validation_issues" not in json.dumps([final, env.client.get(env.url(task) + "/events").json()])
    with env.runtime.store.transaction() as payload:
        assert payload["tasks"][task["id"]]["repair_count"] == (1 if optin else 2)
        private = list(payload["operations"].values())[1]["tool_result"]
        assert ("validation_issues" in private) is optin


@pytest.mark.parametrize("optin", [True, False])
def test_offline_planner_repair_depends_on_explicit_private_feedback(planner_env, monkeypatch, optin):
    import core.planner.runtime as module

    client, runtime, actor = planner_env
    if optin:
        monkeypatch.setattr(module, "execute_tool", lambda raw, source, read: execute_tool(raw, source, read, detailed_feedback=True))
    calls = []

    def respond(messages, operation, task):
        calls.append(copy.deepcopy(messages))
        has_fix = any(message["role"] == "tool" and
            {"path": "$", "code": "OBJECT_REQUIRED"} in message["content"].get("validation_issues", []) for message in messages)
        raw = OfflineActor().respond(messages, operation, task)
        return raw if has_fix else json.dumps(raw)

    monkeypatch.setattr(actor, "respond", respond)
    task = create(client, brief(client), offline=True)
    action(client, task)
    final = wait(client, task["id"], {"plan_ready" if optin else "blocked"})
    assert final["step_count"] == len(calls) == (3 if optin else 2)
    assert final["repair_count"] == (1 if optin else 2)
    assert final["blocked_reason"] == (None if optin else "REPAIR_LIMIT")
    assert final["quality_status"] == "not_reviewed" and final["authorization_status"] == "offline_only"
    assert "validation_issues" not in json.dumps([final, client.get(f"/api/planner/tasks/{task['id']}/events").json()])
    feedback = calls[1][-1]["content"]
    if optin:
        assert feedback["validation_issues"] == [{"path": "$", "code": "OBJECT_REQUIRED"}]
    else:
        assert feedback == {"ok": False, "code": "INVALID_ACTION"}


def test_unicode_validator_exception_does_not_leak_or_change_source():
    source, plan = source_and_plan()
    original = copy.deepcopy(source)
    plan["shots"][0]["visual"] = "\ud800"
    raw = json.dumps({"tool": "submit_shot_plan", "arguments": {"plan": plan}})
    assert execute_tool(raw, source, True) == ({"ok": False, "code": "INVALID_ACTION"}, None, True)
    feedback, result, read = execute_tool(raw, source, True, detailed_feedback=True)
    assert feedback == {"ok": False, "code": "INVALID_ACTION", "validation_issues": [{"path": "$.arguments.plan.shots[*].visual", "code": "INVALID_STRUCTURE"}]}
    assert source == original and result is None and read is True


def test_existing_journal_is_not_backfilled_when_optin_capability_is_available(env):
    fixture, path, original = restore_legacy(env)
    for task in [fixture["success_task"], fixture["received_task"]]:
        assert "validation_issues" not in json.dumps(env.observe(task))
    execute_tool(json.dumps("bad"), {}, False, detailed_feedback=True)
    assert path.read_bytes() == original and env.provider.calls == []



@pytest.mark.parametrize("scope,location,kind,path", [
    ("submit", ("plan", "shots", 0, "visual", "SECRET-UNKNOWN"), "unknown", "$.arguments.plan.shots[*]"),
    ("submit", ("plan", "shots", 0, "visual", "SECRET-UNKNOWN"), "extra_forbidden", "$.arguments.plan.shots[*]"),
    ("submit", ("plan", "source_snapshot_hash", "SECRET-UNKNOWN"), "unknown", "$.arguments.plan"),
    ("submit", ("plan", "shots", 0, "depends_on", 999, "SECRET-UNKNOWN"), "unknown", "$.arguments.plan.shots[*].depends_on"),
    ("action", ("tool", "SECRET-UNKNOWN"), "unknown", "$"),
    ("action", ("arguments", "SECRET-UNKNOWN"), "unknown", "$.arguments"),
    ("read", ("plan", "visual", "SECRET-UNKNOWN"), "unknown", "$.arguments"),
])
def test_unknown_deeper_location_falls_to_container_not_a_known_scalar(scope, location, kind, path):
    from core.planner.protocol import _ACTION_PATHS, _READ_PATHS, _SUBMIT_PATHS, _schema_issues

    class Errors:
        def errors(self, **options):
            return [{"type": kind, "loc": location}]

    paths = {"action": _ACTION_PATHS, "read": _READ_PATHS, "submit": _SUBMIT_PATHS}[scope]
    assert _schema_issues(Errors(), paths) == [{"path": path, "code": "UNEXPECTED_FIELD" if kind == "extra_forbidden" else "INVALID_STRUCTURE"}]
