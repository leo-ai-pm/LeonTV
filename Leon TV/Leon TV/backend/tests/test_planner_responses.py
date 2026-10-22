"""D10 protocol, billing and legacy recovery regression; synthetic inputs only."""
import base64
import copy
import json
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError
from test_planner_execution import change_policy, policy
from test_planner_execution import env as env
from test_planner_execution import no_network as no_network

from core.creator.store import digest
from core.execution.policy import ExecutionPolicy, cost, safe_policy
from core.execution.store import ExecutionStore
from core.execution.transport import ControlledTransport, ResponseUnconfirmed, normalized, wire_for
from core.planner.actors import OfflineActor


def responses_policy():
    value = policy().model_dump()
    value.update(wire_api="responses", endpoint="https://synthetic.example/v1/responses")
    return ExecutionPolicy.model_validate(value).frozen()


def receipt(p, text="SYNTHETIC-TOOL-ACTION"):
    return {"object": "response", "id": "resp_synthetic", "model": p["model"], "status": "completed",
            "output": [{"id": "msg_synthetic", "type": "message", "role": "assistant", "status": "completed",
                        "content": [{"type": "output_text", "text": text, "annotations": [], "logprobs": []}]}],
            "usage": {"input_tokens": 10, "output_tokens": 20, "total_tokens": 30,
                      "input_tokens_details": {"cached_tokens": 4}, "output_tokens_details": {"reasoning_tokens": 15}}}


def raw(value, headers=None):
    return {"http_status": 200, "headers": {"x-request-id": "req_synthetic"} if headers is None else headers,
            "body_base64": base64.b64encode(json.dumps(value).encode()).decode()}


def test_explicit_protocol_hash_and_exact_stateless_wire():
    old = policy().frozen()
    assert "wire_api" not in old
    explicit = ExecutionPolicy.model_validate({**{k: v for k, v in old.items() if k != "policy_hash"}, "wire_api": "chat_completions"}).frozen()
    assert explicit["policy_hash"] != old["policy_hash"]
    assert safe_policy(old)["wire_api"] == "chat_completions" and "wire_api" not in old
    p = responses_policy()
    messages = [{"role": "system", "content": {"rule": "SYNTHETIC"}}, {"role": "tool", "content": {"ok": True}}]
    value = json.loads(wire_for(messages, p))
    assert value == {"model": p["model"], "input": [{"role": "system", "content": json.dumps(messages[0]["content"])},
                     {"role": "user", "content": json.dumps(messages[1]["content"])}],
                     "max_output_tokens": 2000, "stream": False, "store": False, "background": False,
                     "tools": [], "tool_choice": "none", "parallel_tool_calls": False,
                     "truncation": "disabled", "service_tier": "default"}
    assert json.loads(wire_for(messages, old)) == {"model": old["model"], "max_tokens": 2000,
                                                   "messages": value["input"]}


@pytest.mark.parametrize("endpoint", ["https://synthetic.example/v1/chat/completions", "http://synthetic.example/v1/responses",
                                     "https://user@synthetic.example/v1/responses", "https://synthetic.example/v1/responses?x=1",
                                     "https://synthetic.example/v1/responses#x"])
def test_responses_endpoint_cannot_change_protocol_or_credentials(endpoint):
    value = {k: v for k, v in responses_policy().items() if k != "policy_hash"}
    with pytest.raises(ValidationError):
        ExecutionPolicy.model_validate({**value, "endpoint": endpoint})


def test_completed_usage_prices_parent_totals_once_and_keeps_reasoning_private():
    p = responses_policy()
    value = receipt(p)
    value["max_output_tokens"] = p["max_output_tokens"]
    value["output"].insert(0, {"id": "rs_synthetic", "type": "reasoning", "summary": [
        {"type": "summary_text", "text": "SYNTHETIC-PRIVATE-REASONING"}]})
    result, error = normalized(raw(value), p, 104000, 100)
    assert error is None
    assert result["local_units"] == cost(10, 20, p) == 50
    assert result["cached_input_tokens"] == 4 and result["reasoning_output_tokens"] == 15
    assert result["finish_reason"] is None and result["response_status"] == "completed"
    assert result["provider_request_id"] == "req_synthetic" and result["provider_response_id"] == "resp_synthetic"
    assert "SYNTHETIC-PRIVATE-REASONING" not in json.dumps(result)
    value["usage"].pop("input_tokens_details")
    value["usage"].pop("output_tokens_details")
    result, error = normalized(raw(value), p, 104000, 100)
    assert error is None and result["cached_input_tokens"] is None and result["reasoning_output_tokens"] is None


# Each mutation leaves otherwise-valid completed text and ordinary usage in place.
# In particular, top-level cache charges must never be silently priced as input.
BAD_FIELDS = [
    (("cache_write_tokens",), 1), (("cache_creation_input_tokens",), 1),
    (("billing",), {"extra": 1}), (("service_tier",), "priority"), (("tools",), [{"type": "web_search"}]),
    (("store",), True), (("background",), True), (("parallel_tool_calls",), True),
    (("previous_response_id",), "resp_previous"), (("conversation",), "conv_previous"),
    (("truncation",), "auto"), (("tool_choice",), "auto"), (("max_output_tokens",), True),
    (("max_output_tokens",), "2000"), (("max_output_tokens",), None), (("max_output_tokens",), 1999),
    (("status",), "incomplete"), (("status",), "failed"), (("error",), {"code": "synthetic"}),
    (("incomplete_details",), {"reason": "max_output_tokens"}), (("model",), "different-model"),
    (("id",), "invalid id"), (("output_text",), "different-text"),
    (("usage",), None), (("usage", "input_tokens"), True), (("usage", "input_tokens"), -1),
    (("usage", "total_tokens"), 31), (("usage", "output_tokens"), 2001),
    (("usage", "billable_tokens"), 1), (("usage", "input_tokens_details", "cached_tokens"), 11),
    (("usage", "input_tokens_details", "cache_write_tokens"), 1),
    (("usage", "input_tokens_details", "audio_tokens"), 1),
    (("usage", "output_tokens_details", "reasoning_tokens"), 21),
    (("usage", "output_tokens_details", "reasoning_tokens"), True),
    (("output", 0, "status"), "incomplete"), (("output", 0, "role"), "user"),
    (("output", 0, "content", 0, "type"), "refusal"),
    (("output", 0, "content", 0, "annotations"), [{"type": "url_citation"}]),
    (("output", 0, "content", 0, "text"), " "),
]


@pytest.mark.parametrize("path,value", BAD_FIELDS, ids=["-".join(map(str, path)) + "-" + str(i) for i, (path, _) in enumerate(BAD_FIELDS)])
def test_ambiguous_receipt_cannot_settle(path, value):
    p = responses_policy()
    body = receipt(p)
    parent = body
    for key in path[:-1]:
        parent = parent[key]
    parent[path[-1]] = value
    result, error = normalized(raw(body), p, 104000, 100)
    assert error is not None and result["local_units"] is None and result["content"] is None


@pytest.mark.parametrize("fault", ["mixed_tool", "two_messages", "two_texts", "duplicate_id", "missing_request_id", "duplicate_json", "nonfinite", "actual_input_bound", "hold_bound"])
def test_envelope_and_input_bounds_cannot_settle(fault):
    p = responses_policy()
    body = receipt(p)
    if fault == "mixed_tool":
        body["output"].append({"id": "tool_synthetic", "type": "function_call", "name": "unsafe"})
    elif fault in {"two_messages", "duplicate_id"}:
        other = copy.deepcopy(body["output"][0])
        if fault == "two_messages":
            other["id"] = "msg_other"
        body["output"].append(other)
    elif fault == "two_texts":
        body["output"][0]["content"].append(copy.deepcopy(body["output"][0]["content"][0]))
    incoming = raw(body, {} if fault == "missing_request_id" else None)
    if fault in {"duplicate_json", "nonfinite"}:
        wire = json.dumps(body).replace('"input_tokens": 10', '"input_tokens": 10, "input_tokens": 10' if fault == "duplicate_json" else '"input_tokens": NaN')
        incoming["body_base64"] = base64.b64encode(wire.encode()).decode()
    result, error = normalized(incoming, p, 49 if fault == "hold_bound" else 104000, 9 if fault == "actual_input_bound" else 100)
    assert error is not None and result["local_units"] is None


def install_provider(env, fault=False, scenario="success"):
    change_policy(env, wire_api="responses", endpoint="https://synthetic.example/v1/responses")
    calls = []

    def handle(request):
        calls.append(request)
        body = json.loads(request.content)
        assert body["store"] is False and body["service_tier"] == "default" and body["tools"] == []
        assert "messages" not in body and "previous_response_id" not in body
        messages = []
        for message in body["input"]:
            content = json.loads(message["content"])
            messages.append({"role": "tool" if isinstance(content, dict) and "ok" in content else message["role"], "content": content})
        text = OfflineActor().respond(messages, {"id": request.headers["x-client-operation-id"]}, {"scenario": scenario})
        value = receipt(env.runtime.policy, text)
        if fault:
            value["cache_write_tokens"] = 1
        return httpx.Response(200, json=value, headers={"x-request-id": "req_" + request.headers["x-client-operation-id"]})

    env.runtime.transport = httpx.MockTransport(handle)
    return calls


def test_formal_responses_repair_loop_protocol_binding_and_replay(env):
    calls = install_provider(env, scenario="repair")
    task = env.source_task()
    quote = env.quote(task)
    grant = env.grant(task, quote)
    assert quote["quote"]["policy"]["wire_api"] == "responses" and calls == []
    assert env.action(task, grant["execution"]).status_code == 200
    final = env.wait(task, {"plan_ready"})
    assert len(calls) == final["ledger"]["call_count"] == 3
    assert final["ledger"]["locally_accounted_cny"] == "0.000150" and final["ledger"]["held_cny"] == "0.000000"
    assert final["ledger"]["provider_bill_confirmed"] is False and final["quality_status"] == "not_reviewed"
    assert all(op["wire_api"] == "responses" and op["finish_reason"] is None and op["response_status"] == "completed" for op in final["operations"])
    assert env.action(task, grant["execution"]).json()["replayed"] and len(calls) == 3
    with env.runtime.store.transaction() as payload:
        op = copy.deepcopy(next(iter(payload["operations"].values())))
        frozen = copy.deepcopy(next(iter(payload["quotes"].values()))["policy"])
    for target in ["operation", "permit", "wire"]:
        changed = copy.deepcopy(op)
        if target == "operation":
            changed["wire_api"] = "chat_completions"
        elif target == "permit":
            changed["permit"].pop("wire_api")
            changed["permit_hash"] = digest(changed["permit"])
        else:
            body = json.loads(changed["wire"])
            body["store"] = True
            changed["wire"] = json.dumps(body)
            changed["wire_hash"] = digest(changed["wire"])
            changed["permit"]["wire_hash"] = changed["wire_hash"]
            changed["permit_hash"] = digest(changed["permit"])
        with pytest.raises(ResponseUnconfirmed):
            ControlledTransport(env.runtime.transport, env.runtime.input_bounder).perform(changed, frozen, "synthetic-secret")
    assert len(calls) == 3
    assert env.client.get("/api/planner/tasks/" + task["id"]).json()["task"]["status"] == "awaiting_authorization"


def test_top_level_cache_charge_keeps_hold_through_reconcile_stop_and_regrant(env):
    calls = install_provider(env, fault=True)
    task = env.source_task()
    grant = env.grant(task)
    env.action(task, grant["execution"])
    state = env.wait(task, {"needs_reconciliation"})
    ledger = state["ledger"]
    assert ledger["call_count"] == len(calls) == 1 and ledger["held_cny"] == "0.104000"
    assert ledger["locally_accounted_cny"] == "0.000000" and "resume" not in state["allowed_actions"]
    assert env.action(task, state, "reconcile", "synthetic-reconcile-key").status_code == 200
    state = env.wait(task, {"needs_reconciliation"})
    assert state["ledger"] == ledger and len(calls) == 1
    assert env.action(task, state, "stop", "synthetic-stop-key").status_code == 200
    state = env.observe(task)
    replacement = env.grant(task, env.quote(task, state["revision"], "synthetic-new-quote"), "synthetic-new-grant")
    assert replacement["execution"]["ledger"] == ledger and len(calls) == 1
    assert "resume" not in replacement["execution"]["allowed_actions"]


def test_d09_journal_hashes_and_raw_receipts_stay_legacy_without_rewrite(tmp_path):
    fixture = json.loads((Path(__file__).parent / "fixtures/d09_legacy_execution.json").read_text())
    assert fixture["synthetic_only"] is True
    old_policy = fixture["policy"]
    assert "wire_api" not in old_policy
    assert ExecutionPolicy.model_validate({k: v for k, v in old_policy.items() if k != "policy_hash"}).frozen() == old_policy
    store = ExecutionStore(tmp_path / "legacy")
    store.root.mkdir()
    path = store.root / "state.json"
    original = base64.b64decode(fixture["journal_base64"])
    path.write_bytes(original)
    with store.transaction() as payload:
        operations = list(payload["operations"].values())
        assert len(operations) == 3
        assert {op["status"] for op in operations} == {"settled", "received"}
        for op in operations:
            frozen = payload["quotes"][op["quote_id"]]["policy"]
            assert all("wire_api" not in value for value in [op, op["permit"], frozen])
            result, error = normalized(op["raw"], frozen, op["hold_units"], op["permit"]["input_token_upper"])
            assert error is None and result["local_units"] == 50
            assert result["finish_reason"] == "stop" and "response_status" not in result
            assert safe_policy(frozen)["wire_api"] == "chat_completions"
    assert path.read_bytes() == original
