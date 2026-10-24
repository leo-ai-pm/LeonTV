"""Native execution-to-assets seam; authority stays closed and network is forbidden."""
import copy
import json
from pathlib import Path

import pytest
from fastapi.staticfiles import StaticFiles
from test_execution_empirical import empirical_env as empirical_env
from test_execution_empirical import start
from test_execution_roles import role_env as role_env
from test_execution_roles import run
from test_planner_execution import env as env
from test_planner_execution import no_network as no_network

from core.creator.store import CreatorError, digest
from core.execution.asset_handoff import PERMISSIONS, build_asset_context, build_handoff
from core.planner.actors import OfflineActor
from core.planner.product_assets import checked_asset_payload


def private_bytes(runtime):
    return {str(path): path.read_bytes()
            for root in (runtime.store.root, runtime.planner.store.root, runtime.planner.brief_store.root)
            for path in Path(root).rglob("*") if path.is_file()}


def assert_unavailable(env, task):
    before = private_bytes(env.runtime)
    with pytest.raises(CreatorError) as error:
        build_handoff(env.runtime, task["id"])
    assert error.value.code == "ASSET_HANDOFF_UNAVAILABLE" and error.value.status == 409
    response = env.client.get(env.url(task) + "/asset-handoff")
    assert response.status_code == 409
    assert response.json() == {"detail": {"code": "ASSET_HANDOFF_UNAVAILABLE",
        "message": "Accepted execution evidence is required for asset handoff"}}
    assert private_bytes(env.runtime) == before


def assert_handoff(env, task, version, kind):
    before = private_bytes(env.runtime)
    result = build_handoff(env.runtime, task["id"])
    assert set(result) == {"handoff"}
    handoff = result["handoff"]
    assert set(handoff) == {"schema_version", "contract_version", "status", "task_id", "execution_revision", "source",
        "plan", "acceptance", "asset_contract_version", "asset_task_id", "asset_input_hash", "jobs", "inherited_budget",
        "permissions", "quality_status", "media_status", "evidence_kind", "provenance_hash", "handoff_hash"}
    assert handoff["schema_version"] == 1 and handoff["contract_version"] == "execution-asset-handoff-v1"
    assert handoff["status"] == "awaiting_asset_authorization"
    assert handoff["task_id"] == task["id"] and handoff["source"] == task["source"]
    assert "script" not in handoff["source"] and "requirements" not in handoff["source"]
    assert handoff["plan"]["version"] == version
    assert handoff["plan"]["plan_hash"] == digest(handoff["plan"]["artifact"])
    assert handoff["acceptance"]["kind"] == kind
    assert handoff["asset_contract_version"] == "product-assets-v1"
    assert handoff["permissions"] == PERMISSIONS == {"asset_text_calls": 0, "image_submissions": 0,
        "visual_review_calls": 0, "repair_submissions": 0, "paid_calls": 0}
    assert handoff["quality_status"] == "not_reviewed" and handoff["media_status"] == "not_started"
    assert handoff["evidence_kind"] == "synthetic"
    assert handoff["handoff_hash"] == digest({key: value for key, value in handoff.items() if key != "handoff_hash"})
    context = build_asset_context(env.runtime, task["id"])
    assert context == checked_asset_payload(context)
    assert handoff["asset_input_hash"] == digest(context)
    assert context["binding"]["asset_task_id"] == handoff["asset_task_id"]
    assert context["binding"]["acceptance_kind"] == kind
    assert context["binding"]["acceptance_input_hash"] == handoff["acceptance"]["input_hash"]
    assert context["binding"]["acceptance_report_hash"] == handoff["acceptance"]["report_hash"]
    assert context["binding"]["plan_hash"] == handoff["plan"]["plan_hash"]
    shots = handoff["plan"]["artifact"]["shots"]
    assert handoff["jobs"] == [{"shot_id": shot["id"], "image_order": index, "duration_ms": shot["duration_ms"],
        "depends_on": shot["depends_on"], "first_frame_version": 1, "status": "awaiting_manifest"}
        for index, shot in enumerate(shots, 1)]
    public = copy.deepcopy(handoff)
    public.pop("plan")  # The accepted shot text itself is intentionally readable.
    serialized = json.dumps(public)
    for forbidden in ("PRIVATE-REQUIREMENTS", "SYNTHETIC-PRIVATE", "synthetic-secret", "reasoning_content", "body_base64",
                      "provider_request_id", "provider_response_id", "platform_log_id", "source_export_hash", "https://", str(env.runtime.store.root)):
        assert forbidden not in serialized
    assert not {"asset_payload", "acceptance_payload", "acceptance_report", "prompt", "raw", "normalized", "context", "wire"} & set(handoff)
    response = env.client.get(env.url(task) + "/asset-handoff")
    assert response.status_code == 200 and response.json() == result
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert private_bytes(env.runtime) == before
    return handoff


@pytest.mark.parametrize(("flow", "version", "kind", "calls"), [
    ("first_accepted", 1, "first_review", 3), ("repair", 2, "recheck", 5)])
def test_native_accepted_formal_history_yields_closed_safe_handoff(role_env, flow, version, kind, calls):
    current = role_env(flow)
    task, _ = run(current)
    result = assert_handoff(current, task, version, kind)
    assert len(current.provider.calls) == calls
    budget = result["inherited_budget"]
    assert budget["accounting_kind"] == "formal_local_accounting"
    assert budget["account_call_count"] == calls and budget["account_max_calls"] == 12
    assert budget["source"] == "frozen_execution_journal" and budget["is_current_balance"] is False
    assert budget["original_budget_cny"] is None and budget["parent_anchor_hash"] is None


@pytest.mark.parametrize(("flow", "version", "kind", "calls"), [
    ("first_accepted", 1, "first_review", 3), ("repair", 2, "recheck", 5)])
def test_empirical_matched_receipts_preserve_original_budget_history(empirical_env, flow, version, kind, calls):
    current = empirical_env(flow)
    task, _ = start(current)
    final = current.complete(task)
    assert final["status"] == "plan_ready"
    result = assert_handoff(current, task, version, kind)
    budget = result["inherited_budget"]
    assert budget["accounting_kind"] == "empirical_risk_v1"
    assert budget["original_budget_cny"] == "100"
    assert budget["opening_risk_cny"] == "69.37" and budget["opening_call_count"] == 41
    assert budget["scope_risk_cny"] == "6" and budget["scope_max_calls"] == 6
    assert budget["frozen_parent_reserved_risk_cny"] == "75.370000"
    assert budget["execution_risk_cny"] == final["ledger"]["account_risk_allocated_cny"]
    assert budget["account_call_count"] == 41 + calls and budget["account_max_calls"] == 47
    assert len(current.calls()) == calls


@pytest.mark.parametrize(("flow", "version", "kind", "count"), [
    ("first_accepted", 1, "first_review", 4), ("first_accepted", 1, "first_review", 12),
    ("repair", 2, "recheck", 4)])
def test_full_four_and_twelve_shot_plans_keep_order_duration_and_dependencies(role_env, monkeypatch, flow, version, kind, count):
    original = OfflineActor.respond

    def respond(actor, messages, operation, task):
        data = json.loads(original(actor, messages, operation, task))
        if data.get("tool") == "submit_shot_plan":
            plan = data["arguments"]["plan"]
            plan["shots"] = [{"id": f"S{index:02}", "duration_ms": 15000 // count,
                "visual": f"Synthetic shot {index}", "camera": "Static", "audio": "Not generated",
                "depends_on": [f"S{index - 1:02}"] if index > 1 else []} for index in range(1, count + 1)]
        return json.dumps(data)
    monkeypatch.setattr(OfflineActor, "respond", respond)
    current = role_env(flow)
    task, final = run(current)
    assert final["status"] == "plan_ready", final
    result = assert_handoff(current, task, version, kind)
    assert len(result["jobs"]) == count and sum(job["duration_ms"] for job in result["jobs"]) == 15000


@pytest.mark.parametrize("flow", ["first_unknown", "recheck_unknown", "recheck_rejected"])
def test_unknown_or_rejected_review_never_becomes_asset_authority(role_env, flow):
    current = role_env(flow)
    task, _ = run(current)
    assert_unavailable(current, task)


def test_structure_only_plan_without_independent_roles_is_not_an_asset_handoff(env):
    task = env.source_task()
    grant = env.grant(task)
    assert env.action(task, grant["execution"]).status_code == 200
    assert env.wait(task, {"plan_ready"})["plan"] is not None
    assert_unavailable(env, task)


def test_unmatched_empirical_fee_and_unknown_response_are_not_handoffs(empirical_env):
    for fault in (None, "timeout"):
        current = empirical_env("first_accepted", fault_role="planner", fault=fault)
        task, pending = start(current)
        assert pending["operations"]
        assert_unavailable(current, task)
        assert len(current.calls()) == 1


def test_actual_platform_overrun_prevents_handoff(empirical_env):
    current = empirical_env("first_accepted")
    task, pending = start(current)
    final = current.fee(task, pending, "9.000000")
    assert final["operations"][-1]["fee_status"] == "overrun"
    assert_unavailable(current, task)
    assert len(current.calls()) == 1


def test_account_overrun_from_another_task_prevents_existing_accepted_plan(empirical_env):
    current = empirical_env("first_accepted")
    task, _ = start(current)
    assert current.complete(task)["status"] == "plan_ready"
    assert_handoff(current, task, 1, "first_review")
    second = current.source_task("second")
    grant = current.grant(second, current.quote(second, key="second-handoff-quote"), key="second-handoff-grant")
    assert current.action(second, grant["execution"], key="second-handoff-start").status_code == 200
    pending = current.wait(second, {"needs_reconciliation"})
    current.fee(second, pending, "9.000000")
    assert_unavailable(current, task)
    assert len(current.calls()) == 4


def test_historical_read_does_not_use_current_policy_keys_recovery_or_write_transactions(role_env, monkeypatch):
    current = role_env("first_accepted")
    task, _ = run(current)
    expected = build_handoff(current.runtime, task["id"])
    before = private_bytes(current.runtime)
    calls = len(current.provider.calls)

    def forbidden(*args, **kwargs):
        pytest.fail("Asset inspection invoked mutable execution or planner work")
    for target in (current.runtime, current.planner):
        monkeypatch.setattr(target, "get", forbidden)
        monkeypatch.setattr(target, "recover", forbidden)
        monkeypatch.setattr(target.store, "transaction", forbidden)
    monkeypatch.setattr(current.planner.brief_store, "transaction", forbidden)
    current.runtime.enabled = False
    current.runtime.policy = None
    current.runtime.api_key = None
    assert build_handoff(current.runtime, task["id"]) == expected
    response = current.client.get(current.url(task) + "/asset-handoff")
    assert response.status_code == 200 and response.json() == expected
    assert private_bytes(current.runtime) == before and len(current.provider.calls) == calls


def test_absent_record_returns_fixed_not_found_without_creating_execution(role_env):
    current = role_env("first_accepted")
    task = current.source_task()
    before = private_bytes(current.runtime)
    for task_id in (task["id"], "f" * 32):
        with pytest.raises(CreatorError) as error:
            build_handoff(current.runtime, task_id)
        assert error.value.code == "NOT_FOUND" and error.value.status == 404
        response = current.client.get(f"/api/planner/tasks/{task_id}/execution/asset-handoff")
        assert response.status_code == 404 and response.json()["detail"]["code"] == "NOT_FOUND"
    assert private_bytes(current.runtime) == before and current.provider.calls == []


@pytest.mark.parametrize("mutation", ["source", "plan", "report", "binding", "raw", "normalized", "quote", "grant"])
def test_forged_references_cannot_pass_native_validation(role_env, monkeypatch, mutation):
    current = role_env("first_accepted")
    task, _ = run(current)
    payload = current.runtime.store.read_only()
    private = payload["tasks"][task["id"]]
    operation = next(op for op in payload["operations"].values() if op["role"] == "reviewer")
    if mutation == "source":
        private["source"]["script"] += " SYNTHETIC forged source"
    elif mutation == "plan":
        private["plan"]["shots"][0]["visual"] += " SYNTHETIC forged plan"
    elif mutation == "report":
        private["role_state"]["first_report_hash"] = "f" * 64
    elif mutation == "binding":
        private["role_state"]["first_review_binding"]["intent_id"] = "f" * 32
    elif mutation == "raw":
        operation["raw"]["body_base64"] = "e30="
    elif mutation == "normalized":
        operation["normalized"]["content"] = "{}"
    elif mutation == "quote":
        operation["quote_id"] = "f" * 32
    else:
        operation["grant_id"] = "f" * 32
    monkeypatch.setattr(current.runtime.store, "read_only", lambda: copy.deepcopy(payload))
    with pytest.raises(CreatorError) as error:
        build_handoff(current.runtime, task["id"])
    assert error.value.code == "CORRUPT_STORAGE"
    response = current.client.get(current.url(task) + "/asset-handoff")
    assert response.status_code == 503 and response.json()["detail"]["code"] == "CORRUPT_STORAGE"
    assert "SYNTHETIC forged" not in response.text


def test_inspection_rejects_query_body_and_public_static_mount(role_env):
    current = role_env("first_accepted")
    task, _ = run(current)
    before = private_bytes(current.runtime)
    url = current.url(task) + "/asset-handoff"
    assert current.client.get(url + "?resume=true").status_code == 422
    assert current.client.request("GET", url, json={"paid_calls": 1}).status_code == 422
    assert current.client.post(url, json={"paid_calls": 1}).status_code == 405
    current.client.app.mount("/unsafe", StaticFiles(directory=current.runtime.store.root), name="unsafe")
    response = current.client.get(url)
    assert response.status_code == 503 and response.json()["detail"]["code"] == "PRIVATE_STORAGE_UNAVAILABLE"
    assert str(current.runtime.store.root) not in response.text
    assert private_bytes(current.runtime) == before
