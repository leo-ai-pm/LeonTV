"""Public fictional native text history and media scopes; no live credentials."""
import copy
import hashlib
import json
from datetime import timedelta
from types import SimpleNamespace

from test_execution_empirical import start
from test_image_fees import SOURCE, export, observed, row_for
from test_product_assets import asset_case

from core.creator.store import digest
from core.execution.fees import request_key
from core.execution.image_policy import FrozenImageRequest, ImageToolPolicy, MediaBudgetAnchor
from core.execution.media_ledger import native_context
from core.execution.media_runtime import MediaExecution
from core.execution.policy import now
from core.execution.receipts import ReceiptCaptureSettings, timestamp
from core.planner import product_assets


def media_case(empirical_env, *, fault=None, legacy=False, **worker_options):
    current = empirical_env("first_accepted")
    original_evidence, text_exports = current.evidence, {}

    def evidence(operation_id, amount=None, **changes):
        result = original_evidence(operation_id, "0.040000", **changes)
        op = current.runtime.store.read_only()["operations"][operation_id]
        row = {"id": 1, "user_id": 1, "created_at": int(timestamp(op["raw"]["received_at"]).timestamp()), "type": 2,
            "content": "SYNTHETIC TEXT", "username": "synthetic", "token_name": SOURCE["token_name"], "model_name": result["model"],
            "quota": 20000, "prompt_tokens": result["prompt_tokens"], "completion_tokens": result["completion_tokens"], "use_time": 1,
            "is_stream": False, "channel": 1, "channel_name": "synthetic", "token_id": 1, "group": SOURCE["group"], "ip": "127.0.0.1",
            "request_id": "text-" + operation_id, "other": "{}"}
        blob = export(row)
        text_exports[operation_id] = blob
        result.update(source_export_hash=hashlib.sha256(blob).hexdigest(),
            platform_log_id="tudou:1:" + operation_id if legacy else request_key(row["request_id"]))
        return result
    current.evidence = evidence
    current.runtime.fee_verifier = lambda *values: True
    task, _ = start(current)
    assert current.complete(task)["status"] == "plan_ready"
    payload = current.runtime.store.read_only()
    context, _ = native_context(payload, task["id"])
    _, candidate = asset_case(len(context["acceptance_payload"]["plan"]["artifact"]["shots"]))
    candidate["binding"] = copy.deepcopy(context["binding"])
    manifest = product_assets.validate_asset_output(json.dumps(candidate, ensure_ascii=False), context)
    count = len(manifest["frames"])
    account = next(iter(payload["accounts"].values()))
    policy = ImageToolPolicy(id="synthetic-native-media", version="1", origin="https://synthetic.invalid", max_submissions=count,
        max_queries=3, max_request_bytes=1048576, max_response_bytes=1048576, request_deadline_seconds=120,
        valid_until=(now() + timedelta(hours=1)).isoformat(), per_submission_risk_cny="1.000000", operator_risk_approved=True, validation_only=True)
    anchor = MediaBudgetAnchor(account_scope_id=account["id"], parent_authorization_hash=account["anchor"]["parent_authorization_hash"],
        parent_ledger_hash="5" * 64, parent_allocation_hash="6" * 64, parent_allocation_begin_hash="7" * 64,
        original_budget_cny="100", opening_risk_cny="75.37", opening_paid_call_count=47, scope_risk_cny=str(count),
        scope_max_calls=count, account_limit_cny=str(75.37 + count), account_max_calls=47 + count)
    counter = current.runtime.store.root.parent / "image-posts.jsonl"
    guard = {"allowed": True, "calls": 0}

    def authority(scope):
        guard["calls"] += 1
        return guard["allowed"] and digest(scope["policy"]) == digest(policy.frozen()) and digest(scope["anchor"]) == digest(anchor.frozen())
    settings = ReceiptCaptureSettings(observation_wait_seconds=5, worker_factory="media_tool_fixture:make_transport",
        worker_options={"counter": str(counter), "fault": fault, "unique_tasks": True, **worker_options})
    source = {**SOURCE, "account_namespace": account["id"]}
    media = MediaExecution(current.runtime, policy=policy, budget_anchor=anchor, manifest=manifest, authority_guard=authority,
        fee_source=source, receipt_capture=settings)
    return SimpleNamespace(env=current, task=task, media=media, policy=policy, anchor=anchor, manifest=manifest,
        counter=counter, guard=guard, source=source, settings=settings, authority=authority, text_exports=text_exports)


def authorize(case):
    quote = case.media.quote(case.task["id"], key="synthetic-media-quote-001")["quote"]
    grant = case.media.grant(case.task["id"], quote["id"], disclosure_hash=quote["disclosure_hash"], consent=True,
        key="synthetic-media-grant-001")["grant"]
    return quote, grant


def reserve(case, grant, shot="S01", key="synthetic-media-submit-001"):
    return case.media.reserve_submit(case.task["id"], grant["id"], shot, key=key)["operation"]


def image_fee(case, operation, *, quota=20000):
    descriptor = FrozenImageRequest.from_frozen(operation["descriptor"])
    raw = operation["raw"]
    request_id = operation["receipt"]["submit_request_id"]
    row = row_for(descriptor, raw, request_id=request_id, quota=quota)
    return case.media.observe_fee(case.task["id"], operation["id"], export(row), observed_at=observed(raw))


def posts(case):
    return [json.loads(line) for line in case.counter.read_text().splitlines()] if case.counter.exists() else []
