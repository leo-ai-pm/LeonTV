"""Opt-in bounded roles in the existing execution journal and fee account.

Only semantic context/validation is shared with D24. Dispatch, authorization,
holds, receipts, recovery and settlement remain owned by ExecutionRuntime.
"""
import copy
import math
from datetime import datetime

from core.creator.store import CreatorError, digest
from core.execution.policy import ExecutionPolicy, money, policy_hold, policy_scope, units
from core.execution.transport import normalized, wire_for
from core.planner.protocol import execute_tool
from core.planner.recheck import validate_recheck_output
from core.planner.repair import build_repair_task
from core.planner.review import build_review_input, validate_review_output
from core.planner.revision_cycle import (
    BINDING_FIELDS,
    PHASES,
    ROLES,
    TERMINAL,
    candidate_from_receipt,
    independent_messages,
    new_review_binding,
    recheck_input,
    repair_binding,
    repair_input,
    repair_messages,
)
from core.planner.revision_cycle import (
    initial as cycle_initial,
)

WORKFLOW = "bounded_revision_v1"
TAGS = {"role_workflow", "role", "role_call_index", "role_binding_hash"}
SCOPE = {"version": WORKFLOW, "roles": list(ROLES),
         "data": ["frozen_source", "plan_v1", "first_review_report", "repair_task", "candidate_v2", "prior_findings"],
         "max_total_calls": 6, "max_reviewer_calls": 1, "max_repairer_calls": 1, "max_rechecker_calls": 1, "max_plan_version": 2}
FINAL_CODES = {"needs_input": "REVIEW_NEEDS_INPUT", "review_invalid": "REVIEW_INVALID", "repair_invalid": "REPAIR_INVALID",
               "recheck_invalid": "RECHECK_INVALID", "rejected": "RECHECK_REQUIRES_REPAIR", "unknown": "RECHECK_NEEDS_INPUT"}


def require(value):
    if not value:
        raise CreatorError("CORRUPT_STORAGE", "Execution role journal failed integrity checks")


def json_tree(value):
    """Do not normalize tuples, non-string keys, bool/int or non-finite data."""
    kind = type(value)
    if kind is dict:
        require(all(type(key) is str for key in value))
        for item in value.values():
            json_tree(item)
    elif kind is list:
        for item in value:
            json_tree(item)
    else:
        require(value is None or kind in {str, int, bool} or kind is float and math.isfinite(value))


def same(left, right):
    return digest(left) == digest(right)


def initial():
    value = cycle_initial()
    value.pop("evidence_kind")
    value.pop("paid_call_limit")
    value["plans"] = []
    return value


def projection(value):
    result = {key: copy.deepcopy(value[key]) for key in initial()
              if key not in {"parent_plan", "first_report", "repair_task", "recheck_report", "plans"}}
    result.update(first_verdict=value["first_report"]["overall_verdict"] if value["first_report"] else None,
                  repair_task_hash=digest(value["repair_task"]) if value["repair_task"] else None,
                  recheck_verdict=value["recheck_report"]["overall_verdict"] if value["recheck_report"] else None,
                  plans=[{"version": item["version"], "plan_hash": item["plan_hash"]} for item in value["plans"]])
    return result


def adapter(task):
    return {"id": task["id"], "source": task["source"]}, {"revision_cycle": task["role_state"], "plans": task["role_state"]["plans"]}


def next_role(task):
    status = task["role_state"]["status"]
    return {"awaiting_plan": "planner", "review_ready": "reviewer", "repair_ready": "repairer", "recheck_ready": "rechecker"}.get(status)


def gate(task):
    state = task["role_state"]
    if state["status"] in FINAL_CODES:
        return FINAL_CODES[state["status"]]
    role = next_role(task)
    if role is None:
        return "RESPONSE_UNCONFIRMED" if state["status"].endswith("response_unknown") else "ROLE_COMPLETE"
    if role != "planner" and state[f"{role}_call_count"] >= 1:
        return "ROLE_CALL_LIMIT"
    return None


def planner_binding(task, operation_id, index):
    return {"task_id": task["id"], "source_snapshot_hash": task["snapshot_hash"], "intent_id": operation_id,
            "role_workflow": WORKFLOW, "role": "planner", "role_call_index": index}


def context_for(task, role, binding):
    document, payload = adapter(task)
    if role == "planner":
        return copy.deepcopy(task["messages"])
    if role == "reviewer":
        return independent_messages(build_review_input(task["source"], payload["plans"][0]["plan"], binding))
    if role == "repairer":
        return repair_messages(repair_input(document, payload, binding))
    return independent_messages(recheck_input(document, payload, binding), recheck=True)


def prepare(task, operation_id):
    role = next_role(task)
    if role is None:
        raise ValueError("Role stage is terminal or already reserved")
    document, payload = adapter(task)
    index = task["role_state"][f"{role}_call_count"] + 1
    if role == "planner":
        binding = planner_binding(task, operation_id, index)
    elif role == "repairer":
        binding = repair_binding(document, payload, operation_id)
    else:
        binding = new_review_binding(document, payload, operation_id, 1 if role == "reviewer" else 2)
    return role, index, binding, context_for(task, role, binding)


def begin(task, role, binding):
    value = task["role_state"]
    value[f"{role}_call_count"] += 1
    if role != "planner":
        value[BINDING_FIELDS[role]] = copy.deepcopy(binding)
        value["status"] = PHASES[role] + "_running"


def unknown(task, role):
    if role != "planner":
        task["role_state"]["status"] = PHASES[role] + "_response_unknown"


def complete(task, operation, content):
    """Only called after formal receipt normalization and local settlement."""
    value, role = task["role_state"], operation["role"]
    if role == "planner":
        feedback, plan, read = execute_tool(content, task["source"], task["read_source"])
        task["messages"].extend([{"role": "assistant", "content": content}, {"role": "tool", "content": feedback}])
        task["read_source"] = read
        if plan is not None:
            task["plan"] = copy.deepcopy(plan)
            value["plans"].append({"version": 1, "plan": copy.deepcopy(plan), "plan_hash": digest(plan)})
            value.update(parent_plan=copy.deepcopy(plan), status="review_ready")
        elif not feedback["ok"]:
            task["repair_count"] += 1
            if task["repair_count"] >= 2:
                task["status"], task["reason"] = "blocked", "REPAIR_LIMIT"
        return feedback
    document, payload = adapter(task)
    try:
        if role == "reviewer":
            data = build_review_input(task["source"], value["plans"][0]["plan"], value["first_review_binding"])
            report = validate_review_output(content, data)
            proposal = build_repair_task(data, report)
            value.update(first_report=report, first_report_hash=digest(report), repair_task=proposal)
            if report["overall_verdict"] == "accepted":
                value.update(status="accepted", accepted_plan_version=1)
                task["status"], task["reason"] = "plan_ready", None
            elif proposal["kind"] == "needs_input":
                value["status"] = "needs_input"
                task["status"], task["reason"] = "blocked", "REVIEW_NEEDS_INPUT"
            else:
                value["status"] = "repair_ready"
        elif role == "repairer":
            candidate = candidate_from_receipt(document, payload, {"response": content, "error": None})
            value["plans"].append({"version": 2, "plan": candidate, "plan_hash": digest(candidate)})
            task["plan"] = copy.deepcopy(candidate)
            value["status"] = "recheck_ready"
        else:
            report = validate_recheck_output(content, recheck_input(document, payload, value["recheck_binding"]))
            verdict = report["overall_verdict"]
            value.update(recheck_report=report, recheck_report_hash=digest(report), status=verdict,
                         accepted_plan_version=2 if verdict == "accepted" else None)
            task["status"], task["reason"] = ("plan_ready", None) if verdict == "accepted" else ("blocked", FINAL_CODES[verdict])
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
        value["status"] = PHASES[role] + "_invalid"
        task["status"], task["reason"] = "blocked", PHASES[role].upper() + "_INVALID"
        return {"ok": False, "code": task["reason"]}
    return {"ok": True, "code": value["status"].upper()}


def validate_operation_tags(operation, policy, *, verify_context=False):
    """Transport and storage both reject partial or mismatched role authority."""
    active = policy.get("role_workflow") == WORKFLOW
    require((set(operation) & TAGS) == (TAGS if active else set()))
    require((set(operation["permit"]) & TAGS) == (TAGS if active else set()))
    require(("role_binding" in operation) == active)
    if active:
        require(operation["role_workflow"] == WORKFLOW and operation["role"] in ROLES)
        require(type(operation["role_call_index"]) is int and 1 <= operation["role_call_index"] <= (6 if operation["role"] == "planner" else 1))
        require(operation["role_binding_hash"] == digest(operation["role_binding"]))
        require(all(same(operation[key], operation["permit"][key]) for key in TAGS))
        binding, role = operation["role_binding"], operation["role"]
        require(binding["task_id"] == operation["task_id"] and binding["intent_id"] == operation["id"] and binding["source_snapshot_hash"] == operation["snapshot_hash"])
        if role == "planner":
            require(same(binding, planner_binding({"id": operation["task_id"], "snapshot_hash": operation["snapshot_hash"]}, operation["id"], operation["role_call_index"])))
        else:
            require(binding["run_id"] == operation["task_id"])
            require(type(operation["context"]) is list and len(operation["context"]) == 2
                    and [message["role"] for message in operation["context"]] == ["system", "user"])
            require(same(binding, operation["context"][1]["content"]["binding"]))
            if role in {"reviewer", "rechecker"}:
                require(type(binding["plan_version"]) is int and binding["plan_version"] == (1 if role == "reviewer" else 2))
                if verify_context:
                    require(same(operation["context"], independent_messages(operation["context"][1]["content"], recheck=role == "rechecker")))
            elif verify_context:
                require(same(operation["context"], repair_messages(operation["context"][1]["content"])))


def validate_authority(op, task, quote, grant):
    policy, permit = quote["policy"], op["permit"]
    require(op["task_id"] == task["id"] == quote["task_id"] == grant["task_id"])
    require(op["quote_id"] == grant["quote_id"] == quote["id"] and op["grant_id"] == grant["id"])
    require(op["account_id"] == task["account_id"] == policy["account_scope_id"])
    require(op["policy_hash"] == grant["policy_hash"] == policy["policy_hash"])
    require(op["snapshot_hash"] == task["snapshot_hash"] == grant["snapshot_hash"] == quote["snapshot_hash"])
    require(grant["disclosure_hash"] == quote["disclosure_hash"] and grant["scope"] == policy_scope(policy))
    require(quote["role_workflow"] == grant["role_workflow"] == WORKFLOW)
    require(grant["role_scope_hash"] == digest(SCOPE) and same(quote["role_scope"], SCOPE))
    require(type(op["hold_units"]) is int and op["hold_units"] == units(quote["per_call_hold_cny"]) == policy_hold(policy))
    pairs = {"operation_id": "id", "task_id": "task_id", "snapshot_hash": "snapshot_hash", "context_hash": "context_hash", "wire_hash": "wire_hash", "grant_id": "grant_id", "quote_id": "quote_id", "account_id": "account_id", "policy_hash": "policy_hash", "hold_units": "hold_units"}
    require(all(same(permit[key], op[field]) for key, field in pairs.items()))
    require(permit["scope"] == policy_scope(policy) and permit["model"] == policy["model"] and permit["endpoint"] == policy["endpoint"])
    require(all(same(permit[key], policy[key]) for key in ("currency", "max_output_tokens", "max_input_bytes", "max_output_bytes")))
    if policy.get("kind") == "empirical_risk_v1":
        from core.execution.empirical import validate_operation
        validate_operation(op, task, quote, grant)
    else:
        require(permit["max_input_tokens"] == policy["max_input_tokens"])
        require(type(permit["input_token_upper"]) is int and 0 < permit["input_token_upper"] <= policy["max_input_tokens"])
        require(type(permit["bounder_ref"]) is str and bool(permit["bounder_ref"]))
    require(permit["grant_expires_at"] == grant["expires_at"] and permit["quote_expires_at"] == quote["expires_at"])
    issued, deadline = datetime.fromisoformat(permit["issued_at"]), datetime.fromisoformat(permit["deadline_at"])
    require(0 < (deadline - issued).total_seconds() <= policy["request_deadline_seconds"])
    require(deadline <= min(datetime.fromisoformat(policy["valid_until"]), datetime.fromisoformat(grant["expires_at"]), datetime.fromisoformat(quote["expires_at"])))
    require(issued >= datetime.fromisoformat(grant["created_at"]))
    require(len(op["wire"].encode("utf-8")) <= policy["max_input_bytes"])


def checked_receipt(operation, policy):
    if policy.get("kind") == "empirical_risk_v1":
        from core.execution.empirical import decode
        require(operation["empirical"]["fee_status"] == "matched" and operation["empirical"]["fee_evidence"] is not None)
        return decode(operation["raw"], policy)
    return normalized(operation["raw"], policy, operation["hold_units"], operation["permit"]["input_token_upper"])


def validate(payload, previous=None):
    """Validate every delta transition; fully rebuild on the final snapshot.

    A validated previous frame is an induction basis, never a cross-request
    cache. Immutable operations need no repeated schema/report reconstruction.
    """
    from core.planner.runtime import SYSTEM

    tasks = payload["tasks"]
    for operation in payload["operations"].values():
        if previous is None or previous["operations"].get(operation["id"]) is not operation:
            validate_operation_tags(operation, payload["quotes"][operation["quote_id"]]["policy"])
    changed_operation_tasks = {op["task_id"] for op in payload["operations"].values()
                               if previous is None or previous["operations"].get(op["id"]) is not op}
    for task_id, task in tasks.items():
        active = task.get("role_workflow") == WORKFLOW
        require(("role_workflow" in task) == active and ("role_state" in task) == active and ("environment" in task) == active)
        before = previous["tasks"].get(task_id) if previous else None
        if before is not None:
            require(before.get("role_workflow") == task.get("role_workflow"))
        if not active:
            continue
        if before is task and task_id not in changed_operation_tasks:
            continue
        json_tree(task)
        empirical = task.get("accounting_kind") == "empirical_risk_v1"
        require(task["environment"] in ({"empirical_live", "synthetic_empirical_test"} if empirical else {"live", "synthetic_test"}) and task["source_mode"] == "normal")
        if before is not None:
            for name in ("id", "source", "snapshot_hash", "planner_task_revision", "account_id", "task_limit_units", "max_calls", "created_at", "role_workflow", "environment", "source_mode"):
                require(same(task[name], before[name]))
            require(task.get("accounting_kind") == before.get("accounting_kind") and task.get("parent_anchor_hash") == before.get("parent_anchor_hash"))
            require(same(task["events"][:len(before["events"])], before["events"]))
            transitions = {"not_authorized": {"not_authorized", "ready"}, "ready": {"ready", "queued", "blocked"},
                           "queued": {"queued", "running", "stopped", "blocked"},
                           "running": {"running", "stopped", "blocked", "plan_ready", "needs_reconciliation"},
                           "stopped": {"stopped", "queued", "ready", "blocked", "plan_ready", "needs_reconciliation"},
                           "blocked": {"blocked", "queued", "ready"},
                           "needs_reconciliation": {"needs_reconciliation", "stopped", "ready", "blocked", "plan_ready"},
                           "plan_ready": {"plan_ready"}}
            if empirical:
                transitions["needs_reconciliation"].update({"queued", "running"})
                transitions["queued"].update({"plan_ready", "needs_reconciliation"})
            require(task["status"] in transitions[before["status"]])
        elif previous is not None:
            require(task["status"] == "not_authorized" and same(task["role_state"], initial()))
        operations = [op for op in payload["operations"].values() if op["task_id"] == task_id]
        operations.sort(key=lambda op: op["step"])
        old_ops = [op for op in previous["operations"].values() if op["task_id"] == task_id] if previous else []
        if previous is not None:
            require(len(old_ops) <= len(operations) <= len(old_ops) + 1)
            if len(operations) > len(old_ops):
                require(before is not None and before["status"] == "running" and operations[-1]["status"] == "submitting")
                reserved = operations[-1]
                require(before["active_grant_id"] == reserved["grant_id"] and not any(op["status"] != "settled" for op in old_ops))
                require(previous["grants"][reserved["grant_id"]]["revoked_at"] is None)
                if empirical:
                    from core.execution.store import totals
                    grant = previous["grants"][reserved["grant_id"]]
                    account = previous["accounts"][task["account_id"]]
                    own, shared = totals(previous, task_id=task_id), totals(previous, account_id=task["account_id"])
                    require(own["calls"] < min(grant["max_calls"], task["max_calls"], 6) and shared["calls"] < account["max_calls"])
                    require(own["accounted"] + own["held"] + reserved["hold_units"] <= min(task["task_limit_units"], units(grant["total_limit_cny"])))
                    require(shared["accounted"] + shared["held"] + reserved["hold_units"] <= account["limit_units"])
                    require(not any(op["account_id"] == task["account_id"] and op.get("empirical", {}).get("fee_status") == "overrun" for op in previous["operations"].values()))
        scratch = {"id": task_id, "source": task["source"], "snapshot_hash": task["snapshot_hash"], "role_state": initial(),
                   "messages": [{"role": "system", "content": copy.deepcopy(SYSTEM)}, {"role": "user", "content": {"task_id": task_id, "source_snapshot_hash": task["snapshot_hash"]}}],
                   "read_source": False, "repair_count": 0, "plan": None, "status": "running", "reason": None}
        incremental = before is not None
        if incremental:
            scratch = copy.deepcopy(before)
            if scratch["role_state"]["status"] not in TERMINAL:
                scratch["status"], scratch["reason"] = "running", None
        for index, op in enumerate(operations, 1):
            old = previous["operations"].get(op["id"]) if previous else None
            if old is op:
                continue
            json_tree(op)
            require(type(op["step"]) is int and op["step"] == index <= min(task["max_calls"], 6))
            if old is not None:
                mutable = {"status", "raw", "normalized", "local_units", "tool_result", "transport_diagnostic"} | ({"empirical"} if empirical else set())
                require(same({key: value for key, value in op.items() if key not in mutable}, {key: value for key, value in old.items() if key not in mutable}))
                allowed = {"submitting": {"submitting", "received", "needs_reconciliation"}, "received": {"received", "settled", "needs_reconciliation"},
                           "needs_reconciliation": {"needs_reconciliation", "settled"}, "settled": {"settled"}}
                require(op["status"] in allowed[old["status"]])
                if old["raw"] is not None or old["status"] != "submitting":
                    require(same(op["raw"], old["raw"]))
                if old["normalized"] is not None:
                    require(same(op["normalized"], old["normalized"]))
                if empirical:
                    from core.execution.empirical import validate_transition
                    validate_transition(op, old)
                if old["status"] == "settled":
                    require(same(op, old))
                if old["status"] == "needs_reconciliation" and old["raw"] is None:
                    require(same(op, old))
            if op["status"] == "submitting":
                require(all(op[key] is None for key in ("raw", "normalized", "local_units", "tool_result")))
            elif op["status"] == "received":
                require(op["raw"] is not None)
                if not empirical:
                    require(all(op[key] is None for key in ("normalized", "local_units", "tool_result")))
            if incremental and old is not None:
                if old["status"] != "settled" and op["status"] == "settled":
                    quote = payload["quotes"][op["quote_id"]]
                    receipt, reason = checked_receipt(op, quote["policy"])
                    require(reason is None and same(receipt, op["normalized"]) and type(op["local_units"]) is int and op["local_units"] == receipt["local_units"])
                    require(same(complete(scratch, op, receipt["content"]), op["tool_result"]))
                elif op["status"] == "needs_reconciliation":
                    unknown(scratch, op["role"])
                continue
            require(next_role(scratch) == op["role"] and scratch["status"] == "running")
            require(type(op["role_call_index"]) is int and op["role_call_index"] == scratch["role_state"][f"{op['role']}_call_count"] + 1)
            quote = payload["quotes"][op["quote_id"]]
            grant = payload["grants"][op["grant_id"]]
            validate_operation_tags(op, quote["policy"])
            validate_authority(op, task, quote, grant)
            binding = op["role_binding"]
            if op["role"] == "planner":
                require(same(binding, planner_binding(scratch, op["id"], op["role_call_index"])))
            else:
                require(binding["task_id"] == binding["run_id"] == task_id and binding["intent_id"] == op["id"])
                if op["role"] == "repairer":
                    require(same(binding, repair_binding(*adapter(scratch), op["id"])))
                else:
                    require(type(binding["plan_version"]) is int and binding["plan_version"] == (1 if op["role"] == "reviewer" else 2))
            require(same(op["context"], context_for(scratch, op["role"], binding)))
            require(op["wire"] == wire_for(op["context"], quote["policy"]))
            begin(scratch, op["role"], binding)
            if op["status"] == "settled":
                receipt, reason = checked_receipt(op, quote["policy"])
                require(reason is None and same(receipt, op["normalized"]) and type(op["local_units"]) is int and op["local_units"] == receipt["local_units"])
                feedback = complete(scratch, op, receipt["content"])
                require(same(feedback, op["tool_result"]))
            else:
                require(index == len(operations) and (empirical or op["local_units"] is None) and op["tool_result"] is None)
                if op["status"] == "needs_reconciliation":
                    unknown(scratch, op["role"])
        require(same(task["role_state"], scratch["role_state"]) and same(task["messages"], scratch["messages"]))
        require(type(task["read_source"]) is bool and task["read_source"] == scratch["read_source"])
        require(type(task["repair_count"]) is int and task["repair_count"] == scratch["repair_count"] and same(task["plan"], scratch["plan"]))
        if scratch["role_state"]["status"] in TERMINAL:
            require(task["status"] == scratch["status"] and task["reason"] == scratch["reason"])
        else:
            require(task["status"] in {"not_authorized", "ready", "queued", "running", "stopped", "blocked", "needs_reconciliation"})
        require(all(scratch["role_state"][f"{role}_call_count"] <= 1 for role in ROLES[1:]))
    for quote_id, quote in payload["quotes"].items():
        task = tasks[quote["task_id"]]
        active = task.get("role_workflow") == WORKFLOW
        require(("role_workflow" in quote) == active and ("role_scope" in quote) == active)
        require(("role_workflow" in quote["policy"]) == active)
        if active:
            if previous and previous["quotes"].get(quote_id) is quote:
                continue
            json_tree(quote)
            require(quote["policy"]["role_workflow"] == quote["role_workflow"] == WORKFLOW and same(quote["role_scope"], SCOPE))
            empirical = quote["policy"].get("kind") == "empirical_risk_v1"
            require((("synthetic_empirical_test" if quote["policy"]["validation_only"] else "empirical_live") if empirical else ("synthetic_test" if quote["policy"]["validation_only"] else "live")) == task["environment"])
            policy = quote["policy"]
            if empirical:
                from core.execution.empirical import EmpiricalExecutionPolicy
                model = EmpiricalExecutionPolicy
            else:
                model = ExecutionPolicy
            checked = model.model_validate({key: value for key, value in policy.items() if key != "policy_hash"}).frozen()
            require(same(checked, policy))
            require(quote["snapshot_hash"] == task["snapshot_hash"] and quote["planner_task_revision"] == task["planner_task_revision"])
            require(quote["expires_at"] == policy["valid_until"] and datetime.fromisoformat(quote["created_at"]) < datetime.fromisoformat(quote["expires_at"]))
            require(quote["per_call_hold_cny"] == money(policy_hold(policy)))
            require(type(quote["max_calls"]) is int and quote["max_calls"] == min(task["max_calls"], policy["max_calls"]))
            require(quote["total_limit_cny"] == money(min(task["task_limit_units"], units(policy["task_limit_cny"]))))
            account = payload["accounts"][task["account_id"]]
            require(task["account_id"] == policy["account_scope_id"] == account["id"])
            require(type(account["limit_units"]) is int and account["limit_units"] == units(policy["account_limit_cny"]))
            require(type(account["max_calls"]) is int and account["max_calls"] == policy["account_max_calls"])
            if previous and quote_id in previous["quotes"]:
                require(same(quote, previous["quotes"][quote_id]))
    for grant_id, grant in payload["grants"].items():
        active = tasks[grant["task_id"]].get("role_workflow") == WORKFLOW
        require(("role_workflow" in grant) == active and ("role_scope_hash" in grant) == active)
        if active:
            if previous and previous["grants"].get(grant_id) is grant:
                continue
            json_tree(grant)
            require(grant["role_workflow"] == WORKFLOW and grant["role_scope_hash"] == digest(SCOPE))
            quote = payload["quotes"][grant["quote_id"]]
            task = tasks[grant["task_id"]]
            require(grant["task_id"] == quote["task_id"] and grant["snapshot_hash"] == task["snapshot_hash"] == quote["snapshot_hash"])
            require(grant["policy_hash"] == quote["policy"]["policy_hash"] and grant["disclosure_hash"] == quote["disclosure_hash"])
            require(grant["scope"] == policy_scope(quote["policy"]) and grant["total_limit_cny"] == quote["total_limit_cny"])
            require(type(grant["max_calls"]) is int and grant["max_calls"] == quote["max_calls"] and grant["expires_at"] == quote["expires_at"])
            require(datetime.fromisoformat(quote["created_at"]) <= datetime.fromisoformat(grant["created_at"]) < datetime.fromisoformat(grant["expires_at"]))
            if previous and grant_id in previous["grants"]:
                old = previous["grants"][grant_id]
                require(same({key: value for key, value in grant.items() if key != "revoked_at"}, {key: value for key, value in old.items() if key != "revoked_at"}))
                require(old["revoked_at"] is None or grant["revoked_at"] == old["revoked_at"])
    if previous and any(task.get("role_workflow") == WORKFLOW for task in tasks.values()):
        for account_id, account in previous["accounts"].items():
            require(account is payload["accounts"][account_id] or same(account, payload["accounts"][account_id]))
    from core.execution.empirical import validate_payload
    validate_payload(payload, previous)
