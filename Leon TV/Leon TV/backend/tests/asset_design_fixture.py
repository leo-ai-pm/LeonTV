"""Native fictional acceptance and one asset-design MockTransport response."""
import json
import time
from datetime import timedelta
from types import SimpleNamespace

import httpx
from media_ledger_fixture import media_case
from test_image_fees import export
from test_product_assets import asset_case

from core.creator.store import digest, utc_now
from core.execution.asset_design import AssetDesignExecution, AssetDesignPolicy
from core.execution.image_policy import MediaBudgetAnchor
from core.execution.policy import now
from core.execution.receipts import ReceiptCaptureSettings, timestamp


def make_transport(options):
    from pathlib import Path

    def handle(request):
        assert request.url.host == "synthetic.invalid" and request.url.path == "/v1/chat/completions" and request.method == "POST"
        wire = json.loads(request.content)
        assert wire["model"] == "gemini-3.8-flash" and wire["max_completion_tokens"] <= 8192
        assert [message["role"] for message in wire["messages"]] == ["system", "user"]
        with Path(options["counter"]).open("a") as stream:
            stream.write(json.dumps({"operation_id": request.headers["x-client-operation-id"], "method": request.method,
                "wire_hash": digest(request.content.decode()), "created_at": utc_now()}) + "\n")
        time.sleep(options.get("delay", 0))
        if options.get("fault") == "unknown":
            raise httpx.ReadTimeout("synthetic asset design timeout")
        if options.get("fault") == "html":
            return httpx.Response(502, content=b"<html>synthetic gateway error</html>")
        asset_input = json.loads(wire["messages"][1]["content"])
        shots = asset_input["acceptance_payload"]["plan"]["artifact"]["shots"]
        _, candidate = asset_case(len(shots))
        candidate["binding"] = asset_input["binding"]
        for index, (frame, shot) in enumerate(zip(candidate["frames"], shots, strict=True)):
            frame["shot_id"] = shot["id"]
            frame["reference_shot_ids"] = [] if index == 0 else [shots[index - 1]["id"]]
        content = json.dumps(candidate, ensure_ascii=False)
        if options.get("fault") == "invalid_json":
            content = '{"binding":'
        elif options.get("fault") == "invalid_schema":
            content = json.dumps({"binding": asset_input["binding"], "frames": []})
        finish = "length" if options.get("fault") == "length" else "stop"
        body = {"id": "chat-" + request.headers["x-client-operation-id"], "object": "chat.completion", "created": int(time.time()),
            "model": "gemini-3.8-flash", "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": finish}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 300, "total_tokens": 400}}
        return httpx.Response(200, json=body, headers={"x-request-id": "asset-http-" + request.headers["x-client-operation-id"]})
    return httpx.MockTransport(handle)


def design_case(empirical_env, *, fault=None, delay=0):
    old = media_case(empirical_env)
    policy = AssetDesignPolicy(id="synthetic-asset-design", version="1", origin="https://synthetic.invalid", input_cny_per_million="0.75",
        output_cny_per_million="3.75", valid_until=(now() + timedelta(hours=1)).isoformat(), operator_risk_approved=True, validation_only=True)
    anchor = MediaBudgetAnchor(account_scope_id=old.anchor.account_scope_id, parent_authorization_hash=old.anchor.parent_authorization_hash,
        parent_ledger_hash="8" * 64, parent_allocation_hash="9" * 64, parent_allocation_begin_hash="a" * 64, original_budget_cny="100",
        opening_risk_cny="75.37", opening_paid_call_count=47, scope_risk_cny="1", scope_max_calls=1, account_limit_cny="76.37", account_max_calls=48)
    counter = old.env.runtime.store.root.parent / "asset-design-posts.jsonl"
    guard = {"allowed": True, "calls": 0}

    def authority(scope):
        guard["calls"] += 1
        return guard["allowed"] and digest(scope["policy"]) == digest(policy.frozen()) and digest(scope["anchor"]) == digest(anchor.frozen())
    settings = ReceiptCaptureSettings(observation_wait_seconds=5, worker_factory="asset_design_fixture:make_transport",
        worker_options={"counter": str(counter), "fault": fault, "delay": delay})
    design = AssetDesignExecution(old.env.runtime, policy=policy, budget_anchor=anchor, authority_guard=authority,
        fee_source=old.source, receipt_capture=settings)
    return SimpleNamespace(old=old, env=old.env, task=old.task, design=design, policy=policy, anchor=anchor,
        source=old.source, settings=settings, guard=guard, authority=authority, counter=counter)


def authorize(case):
    quote = case.design.quote(case.task["id"], key="asset-design-quote-001")["quote"]
    grant = case.design.grant(case.task["id"], quote["id"], disclosure_hash=quote["disclosure_hash"], consent=True,
        key="asset-design-grant-001")["grant"]
    return quote, grant


def reserve(case, grant):
    return case.design.reserve(case.task["id"], grant["id"], key="asset-design-reserve-001")["operation"]


def fee(case, operation, *, quota=20000, request_id=None):
    receipt, raw = operation["receipt"], operation["raw"]
    row = {"id": 1, "user_id": 1, "created_at": int(timestamp(raw["received_at"]).timestamp()), "type": 2, "content": "SYNTHETIC ASSET DESIGN",
        "username": "synthetic", "token_name": case.source["token_name"], "model_name": "gemini-3.8-flash", "quota": quota,
        "prompt_tokens": receipt["input_tokens"], "completion_tokens": receipt["output_tokens"], "use_time": 1, "is_stream": False,
        "channel": 1, "channel_name": "synthetic", "token_id": 1, "group": case.source["group"], "ip": "127.0.0.1",
        "request_id": request_id or "actual-platform-asset-" + operation["id"], "other": "{}"}
    observed = (timestamp(raw["received_at"]) + timedelta(seconds=1)).isoformat()
    return case.design.observe_fee(case.task["id"], operation["id"], export(row), observed_at=observed)


def posts(case):
    return [json.loads(line) for line in case.counter.read_text().splitlines()] if case.counter.exists() else []
