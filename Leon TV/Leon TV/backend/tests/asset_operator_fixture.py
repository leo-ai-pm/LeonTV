"""Fictional parent proofs and a real native accepted journal, entirely offline."""
import hashlib
import json
from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from media_ledger_fixture import media_case
from test_execution_empirical import EmpiricalEnv
from test_planner_execution import app_for, policy

from core.creator.store import CreatorBriefStore, digest, utc_now
from core.execution.asset_control import ENVELOPE_FIELDS, EXTRA_CODE, KIND
from core.execution.asset_design import AssetDesignPolicy
from core.execution.control import REQUIRED_CODE_FILES
from core.execution.empirical import SCOPE, BudgetAnchor, EmpiricalExecutionPolicy
from core.execution.image_policy import MediaBudgetAnchor
from core.execution.media_ledger import native_context
from core.execution.policy import now
from core.execution.receipts import ReceiptCaptureSettings
from core.execution.runtime import ExecutionRuntime
from core.planner.runtime import PlannerRuntime


def private_json(path, value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
    path.write_bytes(raw)
    path.chmod(0o600)
    return hashlib.sha256(raw).hexdigest()


@pytest.fixture
def asset_control_case(tmp_path):
    resources = []

    def make(*, mode="enabled", legacy=False, fault=None, delay=0, second_task=False):
        root = (tmp_path / str(len(resources))).resolve()
        root.mkdir(mode=0o700)
        authorization = {"task_id": "synthetic-parent", "status": "approved_bounded_live_api_trial",
            "budget": {"currency": "CNY", "hard_total": 100}, "provider_origin": "https://synthetic.invalid"}

        def empirical_factory(flow, **changes):
            planner = PlannerRuntime(root / "planner", CreatorBriefStore(root / "creator"))
            anchor = BudgetAnchor(account_scope_id="synthetic-empirical-account", parent_authorization_hash=digest(authorization),
                parent_ledger_hash="b" * 64, parent_allocation_hash="c" * 64, original_budget_cny="100", opening_exposure_cny="69.37",
                opening_call_count=41, scope_risk_cny="6", scope_max_calls=6, account_limit_cny="75.37", account_max_calls=47)
            values = policy().model_dump()
            for name in ("max_input_tokens", "input_bound_basis", "operator_approved", "billing_bound_verified"):
                values.pop(name)
            values.update(account_scope_id=anchor.account_scope_id, parent_anchor_hash=anchor.frozen()["parent_anchor_hash"],
                account_limit_cny="75.37", account_max_calls=47, task_limit_cny="6", chat_output_parameter="max_completion_tokens",
                operator_risk_approved=True, request_deadline_seconds=10, privacy_scope=SCOPE, **changes)
            counter = root / "text-posts.jsonl"
            guard = {"valid": True, "calls": 0}
            runtime = ExecutionRuntime(root / "execution", planner, policy=EmpiricalExecutionPolicy.model_validate(values),
                enabled=True, api_key="synthetic-secret", budget_anchor=anchor,
                anchor_guard=lambda value: guard["valid"] and digest(value) == digest(anchor.frozen()), fee_verifier=lambda *args: True,
                receipt_capture=ReceiptCaptureSettings(worker_factory="empirical_product_fixture:make_transport",
                    worker_options={"execution_root": str(root / "execution"), "counter": str(counter), "flow": flow}))
            client = TestClient(app_for(runtime))
            client.__enter__()
            resources.append((client, runtime, planner))
            return EmpiricalEnv(client, runtime, None, planner, counter, guard)

        old = media_case(empirical_factory, legacy=legacy)
        task = old.task
        if second_task:
            task = old.env.source_task(suffix="002")
            quote = old.env.quote(task, key="fixture-second-quote-001")
            grant = old.env.grant(task, quote, key="fixture-second-grant-001")
            assert old.env.action(task, grant["execution"], key="fixture-second-action-001").status_code == 200
            assert old.env.complete(task)["status"] == "plan_ready"
        payload = old.env.runtime.store.read_only()
        task_id = task["id"]
        context, handoff = native_context(payload, task_id)
        task = payload["tasks"][task_id]
        source = task["source"]
        admission = {"task_id": task_id, "planner_task_revision": task["planner_task_revision"],
            **{name: source[name] for name in ("snapshot_hash", "brief_id", "brief_revision", "source_hash")},
            "plan_version": handoff["plan"]["version"], "plan_hash": handoff["plan"]["plan_hash"],
            "acceptance_input_hash": handoff["acceptance"]["input_hash"], "acceptance_report_hash": handoff["acceptance"]["report_hash"],
            "handoff_hash": handoff["handoff_hash"], "provenance_hash": handoff["provenance_hash"]}
        current = AssetDesignPolicy(id="synthetic-asset-operator", version="1", origin="https://synthetic.invalid",
            input_cny_per_million="0.75", output_cny_per_million="3.75", valid_until=(now() + timedelta(hours=1)).isoformat(),
            operator_risk_approved=True, validation_only=True)
        anchor = MediaBudgetAnchor(account_scope_id=old.anchor.account_scope_id, parent_authorization_hash=digest(authorization),
            parent_ledger_hash="8" * 64, parent_allocation_hash="9" * 64, parent_allocation_begin_hash="a" * 64,
            original_budget_cny="100", opening_risk_cny="75.37", opening_paid_call_count=47,
            scope_risk_cny="1", scope_max_calls=1, account_limit_cny="76.37", account_max_calls=48).frozen()
        backend = root / "public-backend"
        manifest = {}
        for name in sorted(set(REQUIRED_CODE_FILES) | EXTRA_CODE):
            path = backend / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('# Independent frozen synthetic public module.\n')
            manifest[name] = hashlib.sha256(path.read_bytes()).hexdigest()
        envelope = {"kind": "asset_design_scope_envelope_v1", **{name: anchor[name] for name in ENVELOPE_FIELDS}}
        envelope["envelope_hash"] = digest(envelope)
        request = {"kind": "asset_design_scope_v1", "model": current.model, "endpoint": current.origin + "/v1/chat/completions",
            "chat_output_parameter": current.chat_output_parameter, "source_hash": source["source_hash"], "admission": admission,
            "policy_hash": current.frozen()["policy_hash"], "max_new_calls": 1, "per_call_risk_cny": "1.000000",
            "scope_risk_cny": "1.000000", "risk_basis": current.risk_basis, "scope_envelope_hash": envelope["envelope_hash"],
            "backend_manifest_hash": digest(manifest), "fee_source_hash": digest(old.source),
            "account_scope_id": anchor["account_scope_id"], "scope_status": "open"}
        selected = {"operation_key": "synthetic-asset-allocation", "request": request, "request_sha256": digest(request),
            "upper_fen": 100, "status": "reserved", "provider_task_id": None, "actual_charge_fen": None}
        operations = {"historical-" + str(index): {"request": {"kind": "historical-one-call"}, "upper_fen": 6937 if index == 0 else 0}
                      for index in range(41)}
        operations["old-native-envelope"] = {"request": {"kind": "empirical_product_text_role_scope_envelope", "max_new_calls": 6}, "upper_fen": 600}
        operations[selected["operation_key"]] = selected
        ledger = {"schema_version": 1, "task_id": authorization["task_id"], "authorization_hash": digest(authorization),
            "currency": "CNY", "limit_fen": 10000, "operations": operations}
        begin = {"schema_version": 1, "kind": "asset_design_allocation_begin_v1", "operation_key": selected["operation_key"],
            "allocation_hash": digest(selected), "request_sha256": selected["request_sha256"], "status": "begun", "begun_at": utc_now()}
        data = {"schema_version": 1, "kind": KIND, "mode": mode,
            "roots": {"creator": str(root / "creator"), "planner": str(root / "planner"), "execution": str(root / "execution")},
            "admission": admission, "policy": current.frozen(), "budget_anchor": anchor, "scope_envelope": envelope,
            "fee_source": old.source, "code_manifest": manifest,
            "authority": {"authorization_path": str(root / "authorization.json"), "authorization_sha256": "0" * 64,
                "parent_ledger_path": str(root / "parent-ledger.json"), "allocation_key": selected["operation_key"],
                "allocation_begin_path": str(root / "begin.json"), "allocation_begin_sha256": "0" * 64}}
        path = root / "asset-control.json"

        def save(*, close_request=True):
            if close_request:
                selected["request_sha256"] = digest(request)
            begin["allocation_hash"] = digest(selected)
            begin["request_sha256"] = selected["request_sha256"]
            data["authority"]["authorization_sha256"] = private_json(root / "authorization.json", authorization)
            anchor["parent_ledger_hash"] = private_json(root / "parent-ledger.json", ledger)
            anchor["parent_allocation_hash"] = digest(selected)
            data["authority"]["allocation_begin_sha256"] = private_json(root / "begin.json", begin)
            anchor["parent_allocation_begin_hash"] = digest(begin)
            anchor["budget_anchor_hash"] = digest({key: value for key, value in anchor.items() if key != "budget_anchor_hash"})
            private_json(path, data)

        save()
        counter = root / "asset-posts.jsonl"
        settings = ReceiptCaptureSettings(observation_wait_seconds=5, worker_factory="asset_design_fixture:make_transport",
            worker_options={"counter": str(counter), "fault": fault, "delay": delay})
        return SimpleNamespace(path=path, root=root, backend=backend, data=data, anchor=anchor, request=request, selected=selected,
            authorization=authorization, ledger=ledger, begin=begin, old=old, env=old.env, task=task, source=old.source,
            context=context, handoff=handoff, save=save, settings=settings, counter=counter)

    yield make
    for client, runtime, planner in reversed(resources):
        client.__exit__(None, None, None)
        runtime.close()
        planner.close()
