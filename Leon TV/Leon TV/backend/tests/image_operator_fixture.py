"""Fictional image authority after one actual detached, fee-verified asset call."""
import copy
import hashlib
import json
from types import SimpleNamespace

from asset_design_fixture import fee as asset_fee
from asset_operator_fixture import private_json

from core.creator.store import digest, utc_now
from core.execution.asset_control import ENVELOPE_FIELDS, read_asset_control
from core.execution.asset_media_handoff import manifest_origin, media_from_asset_design
from core.execution.asset_operator import build_asset_session
from core.execution.control import REQUIRED_CODE_FILES
from core.execution.image_control import EXTRA_CODE, KIND, read_image_control
from core.execution.image_policy import ImageToolPolicy, MediaBudgetAnchor
from core.execution.media_ledger import native_context
from core.execution.policy import money, units
from core.execution.receipts import ReceiptCaptureSettings
from core.execution.reference_derivation import ReferenceProfile, checked_profile


def images_case(asset_control_case, *, mode="enabled", profile=None, fault=None, legacy=False, retained_unused=False):
    asset_case = asset_control_case(legacy=legacy)
    if retained_unused:
        unused = copy.deepcopy(asset_case.selected)
        unused["operation_key"] = "synthetic-retained-unused-asset-allocation"
        asset_case.ledger["operations"][unused["operation_key"]] = unused
        asset_case.anchor.update(opening_risk_cny="76.37", opening_paid_call_count=48,
            account_limit_cny="77.37", account_max_calls=49)
        previous = {"kind": "asset_design_scope_envelope_v1", **{key: asset_case.anchor[key] for key in ENVELOPE_FIELDS}}
        previous["envelope_hash"] = digest(previous)
        asset_case.data["scope_envelope"] = previous
        asset_case.request["scope_envelope_hash"] = previous["envelope_hash"]
        asset_case.save()
    if legacy:
        for operation_id, blob in asset_case.old.text_exports.items():
            asset_case.old.media.register_text_claim(operation_id, blob)
    with build_asset_session(read_asset_control(asset_case.path, asset_case.backend), receipt_capture=asset_case.settings) as session:
        quote = session.asset.quote(asset_case.task["id"], key="image-fixture-asset-quote")["quote"]
        grant = session.asset.grant(asset_case.task["id"], quote["id"], disclosure_hash=quote["disclosure_hash"], consent=True,
            key="image-fixture-asset-grant")["grant"]
        operation = session.asset.reserve(asset_case.task["id"], grant["id"], key="image-fixture-asset-reserve")["operation"]
        received = session.asset.start(asset_case.task["id"], operation["id"])["operation"]
        asset_fee(SimpleNamespace(design=session.asset, task=asset_case.task, source=asset_case.source), received)
    root = asset_case.root / "image-authority"
    root.mkdir(mode=0o700)
    payload = asset_case.env.runtime.store.read_only()
    task_id = asset_case.task["id"]
    ready = manifest_origin(payload, task_id, operation["id"])
    context, handoff = native_context(payload, task_id)
    task = payload["tasks"][task_id]
    admission = {"task_id": task_id, "planner_task_revision": task["planner_task_revision"],
        **{key: task["source"][key] for key in ("snapshot_hash", "brief_id", "brief_revision", "source_hash")},
        "plan_version": handoff["plan"]["version"], "plan_hash": handoff["plan"]["plan_hash"],
        "acceptance_input_hash": handoff["acceptance"]["input_hash"], "acceptance_report_hash": handoff["acceptance"]["report_hash"],
        "handoff_hash": handoff["handoff_hash"], "provenance_hash": handoff["provenance_hash"],
        "asset_operation_id": operation["id"], "asset_manifest_hash": ready["manifest"]["manifest_hash"],
        "asset_proof_hash": ready["origin"]["asset_proof"]["proof_hash"]}
    policy = asset_case.old.policy.model_dump()
    policy["id"] = "synthetic-image-operator"
    policy = ImageToolPolicy.model_validate(policy).frozen()
    count = policy["max_submissions"]
    opening_risk = asset_case.anchor["account_limit_cny"]
    opening_calls = asset_case.anchor["account_max_calls"]
    anchor = MediaBudgetAnchor(account_scope_id=asset_case.anchor["account_scope_id"],
        parent_authorization_hash=asset_case.anchor["parent_authorization_hash"], parent_ledger_hash="1" * 64,
        parent_allocation_hash="2" * 64, parent_allocation_begin_hash="3" * 64, original_budget_cny="100",
        opening_risk_cny=opening_risk, opening_paid_call_count=opening_calls, scope_risk_cny=money(count * 1000000), scope_max_calls=count,
        account_limit_cny=money(units(opening_risk) + count * 1000000), account_max_calls=opening_calls + count).frozen()
    backend = root / "public-backend"
    manifest = {}
    for name in sorted(set(REQUIRED_CODE_FILES) | EXTRA_CODE):
        path = backend / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# Independent frozen image operator module.\n")
        manifest[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    envelope = {"kind": "image_execution_scope_envelope_v1", **{key: anchor[key] for key in ENVELOPE_FIELDS}}
    envelope["envelope_hash"] = digest(envelope)
    profile = None if profile is None else ReferenceProfile(allow_lossy_jpeg=profile).frozen() if type(profile) is bool else checked_profile(profile)
    request = {"kind": "image_execution_scope_v1", "model": policy["model"], "endpoint": policy["origin"] + "/v1/images/generations/async",
        "query_endpoint_prefix": policy["origin"] + "/v1/tasks/", "source_hash": admission["source_hash"], "admission": admission,
        "policy_hash": policy["policy_hash"], "max_new_calls": policy["max_submissions"], "max_queries": policy["max_queries"],
        "per_submission_risk_cny": policy["per_submission_risk_cny"], "scope_risk_cny": anchor["scope_risk_cny"], "risk_basis": policy["risk_basis"],
        "scope_envelope_hash": envelope["envelope_hash"], "backend_manifest_hash": digest(manifest), "fee_source_hash": digest(asset_case.source),
        "account_scope_id": anchor["account_scope_id"], "asset_operation_id": admission["asset_operation_id"],
        "asset_manifest_hash": admission["asset_manifest_hash"], "asset_proof_hash": admission["asset_proof_hash"],
        "manifest_origin_hash": ready["origin"]["origin_hash"], "reference_profile_hash": None if profile is None else profile["profile_hash"], "scope_status": "open"}
    selected = {"operation_key": "synthetic-image-allocation", "request": request, "request_sha256": digest(request),
        "upper_fen": count * 100, "status": "reserved", "provider_task_id": None, "actual_charge_fen": None}
    ledger = copy.deepcopy(asset_case.ledger)
    ledger["operations"][selected["operation_key"]] = selected
    begin = {"schema_version": 1, "kind": "image_execution_allocation_begin_v1", "operation_key": selected["operation_key"],
        "allocation_hash": digest(selected), "request_sha256": digest(request), "status": "begun", "begun_at": utc_now()}
    data = {"schema_version": 1, "kind": KIND, "mode": mode, "roots": asset_case.data["roots"].copy(), "admission": admission,
        "policy": policy, "budget_anchor": anchor, "scope_envelope": envelope, "fee_source": asset_case.source,
        "authority": {"authorization_path": asset_case.data["authority"]["authorization_path"],
            "authorization_sha256": asset_case.data["authority"]["authorization_sha256"], "parent_ledger_path": str(root / "parent-ledger.json"),
            "allocation_key": selected["operation_key"], "allocation_begin_path": str(root / "begin.json"), "allocation_begin_sha256": "0" * 64},
        "code_manifest": manifest}
    if profile is not None:
        data["reference_profile"] = profile
    path = root / "image-control.json"

    def save(*, close_request=True):
        if close_request:
            selected["request_sha256"] = digest(request)
        begin["allocation_hash"] = digest(selected)
        begin["request_sha256"] = selected["request_sha256"]
        anchor["parent_ledger_hash"] = private_json(root / "parent-ledger.json", ledger)
        anchor["parent_allocation_hash"] = digest(selected)
        data["authority"]["allocation_begin_sha256"] = private_json(root / "begin.json", begin)
        anchor["parent_allocation_begin_hash"] = digest(begin)
        anchor["budget_anchor_hash"] = digest({key: value for key, value in anchor.items() if key != "budget_anchor_hash"})
        private_json(path, data)

    save()
    counter = root / "image-http.jsonl"
    settings = ReceiptCaptureSettings(observation_wait_seconds=5, worker_factory="image_tool_fixture:make_transport",
        worker_options={"counter": str(counter), "fault": fault})
    case = SimpleNamespace(path=path, backend=backend, root=root, data=data, anchor=anchor, policy=policy, request=request,
        selected=selected, ledger=ledger, begin=begin, source=asset_case.source, manifest=ready["manifest"], origin=ready["origin"],
        context=context, handoff=handoff, asset_case=asset_case, asset_operation=operation, asset_received=received,
        env=asset_case.env, task=asset_case.task, settings=settings, counter=counter, save=save,
        text_exports=asset_case.old.text_exports, profile=profile)
    case.media = make_media(case)
    return case


def make_media(case):
    control = read_image_control(case.path, case.backend)
    return media_from_asset_design(case.env.runtime, case.task["id"], control.document.admission.asset_operation_id,
        policy=control.document.policy, budget_anchor=control.document.budget_anchor, authority_guard=control.guard,
        fee_source=control.document.fee_source.model_dump(), receipt_capture=case.settings, reference_profile=control.document.reference_profile)


def authorize(case):
    quote = case.media.quote(case.task["id"], key="image-operator-fixture-quote")["quote"]
    grant = case.media.grant(case.task["id"], quote["id"], disclosure_hash=quote["disclosure_hash"], consent=True,
        key="image-operator-fixture-grant")["grant"]
    return quote, grant


def posts(case):
    return [json.loads(line) for line in case.counter.read_text().splitlines()] if case.counter.exists() else []
