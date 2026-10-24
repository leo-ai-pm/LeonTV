"""Read-only handoff from accepted execution evidence; never a media permit."""
import copy

from core.creator.store import CreatorError, digest
from core.execution.policy import money
from core.execution.roles import WORKFLOW, adapter, checked_receipt
from core.execution.store import totals, validate_payload
from core.planner.review import build_review_input
from core.planner.revision_cycle import recheck_input
from core.planner.runtime import PlannerRuntime

CONTRACT_VERSION = "execution-asset-handoff-v1"
PERMISSIONS = {"asset_text_calls": 0, "image_submissions": 0, "visual_review_calls": 0,
               "repair_submissions": 0, "paid_calls": 0}


def require(value):
    if not value:
        raise CreatorError("ASSET_HANDOFF_UNAVAILABLE", "Accepted execution evidence is required for asset handoff", 409)


def _prepare(runtime, task_id):
    from core.planner import product_assets

    # read_only reconstructs the original journal and never calls get/recover.
    payload = runtime.store.read_only()
    if task_id not in payload["tasks"]:
        raise CreatorError("NOT_FOUND", "Execution record not found", 404)
    validate_payload(payload)
    task = payload["tasks"][task_id]
    require(task.get("role_workflow") == WORKFLOW and task["status"] == "plan_ready")
    state = task["role_state"]
    require(state["status"] == "accepted" and type(state["accepted_plan_version"]) is int)
    version = state["accepted_plan_version"]
    require(version in {1, 2} and len(state["plans"]) == version)
    current = state["plans"][-1]
    require(current["version"] == version and digest(current["plan"]) == current["plan_hash"] == digest(task["plan"]))
    require(all(op.get("empirical", {}).get("fee_status") != "overrun"
                for op in payload["operations"].values() if op["account_id"] == task["account_id"]))
    operations = sorted((op for op in payload["operations"].values() if op["task_id"] == task_id), key=lambda op: op["step"])
    require(bool(operations))
    for operation in operations:
        require(operation["status"] == "settled" and operation["raw"] is not None and operation["normalized"] is not None)
        policy = payload["quotes"][operation["quote_id"]]["policy"]
        receipt, reason = checked_receipt(operation, policy)
        require(reason is None and digest(receipt) == digest(operation["normalized"]))
    if version == 1:
        kind, role = "first_review", "reviewer"
        accepted = build_review_input(task["source"], current["plan"], state["first_review_binding"])
        report = state["first_report"]
    else:
        kind, role = "recheck", "rechecker"
        accepted = recheck_input(*adapter(task), state["recheck_binding"])
        report = state["recheck_report"]
    matches = [op for op in operations if op["role"] == role and digest(op["role_binding"]) == digest(accepted["binding"])]
    require(len(matches) == 1 and report["overall_verdict"] == "accepted")
    operation = matches[0]
    policy = payload["quotes"][operation["quote_id"]]["policy"]
    receipt, reason = checked_receipt(operation, policy)
    require(reason is None)
    # The selected report must be the accepted operation's actual parsed body.
    from core.planner.recheck import validate_recheck_output
    from core.planner.review import validate_review_output
    checker = validate_review_output if version == 1 else validate_recheck_output
    require(digest(checker(receipt["content"], accepted)) == digest(report))
    identity = {"task_id": task_id, "snapshot_hash": task["snapshot_hash"], "plan_hash": current["plan_hash"],
                "plan_version": version, "acceptance_report_hash": digest(report)}
    asset_id = "ASSET-" + digest(identity)[:48]
    context = product_assets.build_asset_input(kind, accepted, report, {"asset_task_id": asset_id, "manifest_version": 1})
    account = payload["accounts"][task["account_id"]]
    parent = account.get("anchor", {})
    usage = totals(payload, account_id=task["account_id"])
    empirical = policy.get("kind") == "empirical_risk_v1"
    budget = {"accounting_kind": policy.get("kind", "formal_local_accounting"), "currency": "CNY",
              "original_budget_cny": parent.get("original_budget_cny"), "opening_risk_cny": parent.get("opening_exposure_cny"),
              "opening_call_count": parent.get("opening_call_count", 0),
              "frozen_parent_reserved_risk_cny": money(account["limit_units"]) if empirical else None,
              "scope_risk_cny": parent.get("scope_risk_cny"), "scope_max_calls": parent.get("scope_max_calls"),
              "execution_risk_cny": money(usage["accounted"] + usage["held"]) if empirical else None,
              "account_call_count": usage["calls"], "account_max_calls": account["max_calls"],
              "parent_anchor_hash": parent.get("parent_anchor_hash"),
              "source": "frozen_execution_journal", "is_current_balance": False}
    basis = {"task": identity, "execution_revision": task["revision"], "operations": [digest(op) for op in operations],
             "quote": digest(payload["quotes"][operation["quote_id"]]), "grant": digest(payload["grants"][operation["grant_id"]]),
             "account": digest(account)}
    result = {"schema_version": 1, "contract_version": CONTRACT_VERSION, "status": "awaiting_asset_authorization",
              "task_id": task_id, "execution_revision": task["revision"], "source": PlannerRuntime.source_summary(task["source"]),
              "plan": {"version": version, "plan_hash": current["plan_hash"], "artifact": copy.deepcopy(current["plan"])},
              "acceptance": {"kind": kind, "review_id": accepted["binding"]["review_id"], "input_hash": digest(accepted),
                             "report_hash": digest(report), "operation_id": operation["id"]},
              "asset_contract_version": product_assets.ASSET_CONTRACT_VERSION, "asset_task_id": asset_id,
              "asset_input_hash": digest(context),
              "jobs": [{"shot_id": shot["id"], "image_order": index, "duration_ms": shot["duration_ms"],
                        "depends_on": copy.deepcopy(shot["depends_on"]), "first_frame_version": 1, "status": "awaiting_manifest"}
                       for index, shot in enumerate(current["plan"]["shots"], 1)],
              "inherited_budget": budget, "permissions": copy.deepcopy(PERMISSIONS), "quality_status": "not_reviewed",
              "media_status": "not_started", "evidence_kind": "synthetic" if policy["validation_only"] else "empirical_receipts" if empirical else "formal_receipts",
              "provenance_hash": digest(basis)}
    return context, {**result, "handoff_hash": digest(result)}


def build_asset_context(runtime, task_id):
    """Private future actor input, verified from the same authoritative snapshot."""
    return _checked(runtime, task_id)[0]


def build_handoff(runtime, task_id):
    """Safe product projection; no full source, raw receipt or generation authority."""
    return {"handoff": _checked(runtime, task_id)[1]}


def _checked(runtime, task_id):
    try:
        return _prepare(runtime, task_id)
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
        raise CreatorError("ASSET_HANDOFF_UNAVAILABLE", "Accepted execution evidence is required for asset handoff", 409) from None
