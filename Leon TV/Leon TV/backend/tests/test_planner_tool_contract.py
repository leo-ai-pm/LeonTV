"""The model must receive the exact argument envelopes enforced by local tools."""
import copy
import json

from test_brief_planner import brief, create
from test_brief_planner import env as env
from test_brief_planner import no_external as no_external

from core.planner import runtime as module
from core.planner.protocol import ReadArgs, SubmitArgs, execute_tool


def test_prompt_includes_exact_tool_argument_schemas():
    schemas = module.SYSTEM["tool_argument_schemas"]
    assert set(schemas) == {"read_frozen_brief", "submit_shot_plan"}
    assert schemas["read_frozen_brief"] == ReadArgs.model_json_schema()
    assert schemas["submit_shot_plan"] == SubmitArgs.model_json_schema()
    assert schemas["submit_shot_plan"]["required"] == ["plan"]
    assert schemas["submit_shot_plan"]["additionalProperties"] is False


def test_new_task_exposes_submit_envelope_and_real_tool_enforces_it(env):
    client, runtime, _ = env
    task = create(client, brief(client))
    with runtime.store.transaction() as state:
        document = state["tasks"][task["id"]]
        source = copy.deepcopy(document["source"])
        system = document["versions"][-1]["payload"]["messages"][0]["content"]
    assert system["tool_argument_schemas"]["submit_shot_plan"]["required"] == ["plan"]
    plan = {"source_snapshot_hash": source["snapshot_hash"], "duration_ms": 15000, "ratio": "9:16",
            "shots": [{"id": "S01", "duration_ms": 15000, "visual": "SYNTHETIC shot", "camera": "", "audio": "", "depends_on": []}]}
    rejected, output, _ = execute_tool(json.dumps({"tool": "submit_shot_plan", "arguments": plan}), source, True)
    assert rejected == {"ok": False, "code": "INVALID_ACTION"} and output is None
    accepted, output, _ = execute_tool(json.dumps({"tool": "submit_shot_plan", "arguments": {"plan": plan}}), source, True)
    assert accepted["code"] == "STRUCTURE_VALID_NOT_REVIEWED" and output == plan


def test_existing_task_context_and_bytes_are_not_backfilled(env, monkeypatch):
    client, runtime, _ = env
    current = copy.deepcopy(module.SYSTEM)
    legacy = copy.deepcopy(current)
    legacy.pop("tool_argument_schemas", None)
    monkeypatch.setattr(module, "SYSTEM", legacy)
    source = brief(client)
    old = create(client, source)
    original = runtime.store.path("state.json").read_bytes()
    monkeypatch.setattr(module, "SYSTEM", current)
    reread = runtime.get(old["id"])["task"]
    assert reread["source"] == old["source"]
    assert runtime.store.path("state.json").read_bytes() == original
    new = create(client, source, key="planner-new-tool-contract")
    with runtime.store.transaction() as state:
        old_system = state["tasks"][old["id"]]["versions"][-1]["payload"]["messages"][0]["content"]
        new_system = state["tasks"][new["id"]]["versions"][-1]["payload"]["messages"][0]["content"]
        assert old_system == legacy and "tool_argument_schemas" not in old_system
        assert new_system["tool_argument_schemas"] == current["tool_argument_schemas"]
