"""Synthetic worker factory for the actual detached empirical capture path."""
import copy
import json
import os
from pathlib import Path

import httpx

from core.execution.store import ExecutionStore
from core.planner.actors import OfflineActor
from core.planner.collaboration import SyntheticReviewer
from core.planner.revision_cycle import SyntheticRevisionRole


def make_transport(options):
    def handle(request):
        operation = ExecutionStore(Path(options["execution_root"]).resolve()).read_only()["operations"][request.headers["x-client-operation-id"]]
        assert operation["status"] == "submitting" and operation["hold_units"] == 1000000
        body = json.loads(request.content)
        messages = []
        for message in body["messages"]:
            value = json.loads(message["content"])
            messages.append({"role": "tool" if isinstance(value, dict) and "ok" in value else message["role"], "content": value})
        role = operation["role"]
        fd = os.open(options["counter"], os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
        try:
            os.write(fd, (json.dumps({"id": operation["id"], "role": role, "step": operation["step"], "wire_hash": operation["wire_hash"]}) + "\n").encode())
        finally:
            os.close(fd)
        fault = options.get("fault") if role == options.get("fault_role") else None
        if fault == "timeout":
            raise httpx.ReadTimeout("SYNTHETIC PRIVATE NETWORK ERROR")
        if role == "planner":
            content = OfflineActor().respond(messages, operation, {"scenario": "success"})
        elif role == "reviewer":
            scenario = {"first_accepted": "review_accepted", "first_unknown": "review_unknown"}.get(options.get("flow"), "review_rejected")
            content = SyntheticReviewer().respond(messages, operation, {"scenario": scenario})
        else:
            scenario = {"recheck_rejected": "revision_rejected", "recheck_unknown": "recheck_unknown"}.get(options.get("flow"), "revision_accepted")
            content = SyntheticRevisionRole().respond(messages, operation, {"scenario": scenario})
        if fault == "invalid":
            content = '{"invalid":true}'
        elif fault == "binding":
            value = json.loads(content)
            value["binding"]["intent_id"] = "f" * 32
            content = json.dumps(value)
        elif fault == "deep_json":
            content = "[" * 10000 + "0" + "]" * 10000
        output = 600000 if fault == "estimate_overrun" else 20
        if fault == "over_requested":
            output = body[next(key for key in ("max_tokens", "max_completion_tokens") if key in body)] + 1
        value = {"id": "synthetic-response-" + operation["id"], "model": body["model"],
                 "choices": [{"index": 0, "message": {"role": "assistant", "content": content, "reasoning_content": "SYNTHETIC PRIVATE REASONING"}, "finish_reason": "length" if fault == "truncated" else "stop"}],
                 "usage": {"prompt_tokens": 10, "completion_tokens": output, "total_tokens": output + 10,
                           "completion_tokens_details": {"reasoning_tokens": 4}}}
        if fault == "usage":
            value.pop("usage")
        if fault == "model":
            value["model"] = "synthetic-other-model"
        if fault == "secret":
            value["choices"][0]["message"]["content"] = "synthetic-secret"
        return httpx.Response(200, json=copy.deepcopy(value), headers={})
    return httpx.MockTransport(handle)
