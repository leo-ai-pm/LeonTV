"""Offline role handoff inside the existing Planner journal and execution loop."""
import copy
import json
import uuid

from core.creator.store import digest
from core.planner.actors import ModelUncertain
from core.planner.protocol import LIMITS, execute_tool
from core.planner.repair import build_repair_task
from core.planner.review import (
    RULE_HASH,
    RULE_IDS,
    RULE_VERSION,
    ReviewValidationError,
    build_review_input,
    review_messages,
    validate_review_output,
)

FIELDS = {"schema_version", "evidence_kind", "roles", "status", "planner_call_count", "reviewer_call_count",
          "max_total_calls", "max_reviewer_calls", "paid_call_limit", "binding", "report_hash", "report", "repair_task"}
TERMINAL = {"accepted", "rejected", "unknown", "invalid"}


def initial():
    return {"schema_version": 1, "evidence_kind": "synthetic", "roles": ["planner", "reviewer"],
            "status": "awaiting_plan", "planner_call_count": 0, "reviewer_call_count": 0,
            "max_total_calls": LIMITS["max_steps"], "max_reviewer_calls": 1, "paid_call_limit": 0,
            "binding": None, "report_hash": None, "report": None, "repair_task": None}


def projection(value, *, full=False):
    return copy.deepcopy({key: item for key, item in value.items() if full or key not in {"report", "repair_task"}})


def prepare_review(document, payload, operation_id):
    plan = payload["plans"][-1]
    binding = {"review_id": uuid.uuid4().hex, "run_id": document["id"], "intent_id": operation_id,
               "task_id": document["id"], "plan_version": plan["version"], "plan_hash": plan["plan_hash"],
               "source_snapshot_hash": document["source"]["snapshot_hash"], "rule_version": RULE_VERSION, "rule_hash": RULE_HASH}
    data = build_review_input(document["source"], plan["plan"], binding)
    return binding, review_messages(data)


def review_input(document, payload):
    return build_review_input(document["source"], payload["plans"][-1]["plan"], payload["collaboration"]["binding"])


def complete_review(document, payload, operation):
    """Interpret a durable receipt only; the caller commits the resulting frame."""
    collaboration = payload["collaboration"]
    try:
        if operation["error"] is not None:
            raise ReviewValidationError()
        data = review_input(document, payload)
        report = validate_review_output(operation["response"], data)
        repair = build_repair_task(data, report)
    except ReviewValidationError:
        collaboration["status"] = "invalid"
        payload["status"], payload["blocked_reason"] = "blocked", "REVIEW_INVALID"
    else:
        verdict = report["overall_verdict"]
        collaboration.update(status=verdict, report=report, report_hash=digest(report), repair_task=repair)
        payload["status"] = "plan_ready" if verdict == "accepted" else "blocked"
        payload["blocked_reason"] = {"accepted": None, "rejected": "REVIEW_REQUIRES_REPAIR", "unknown": "REVIEW_NEEDS_INPUT"}[verdict]
    operation["status"], payload["pending"] = "processed", None


class SyntheticReviewer:
    """Deterministic test judgement. Never constructs a model transport."""

    def respond(self, messages, operation, task):
        if task["scenario"] == "review_response_unknown":
            raise ModelUncertain("Synthetic Reviewer response not confirmed")
        if task["scenario"] == "review_invalid":
            return '{"synthetic_invalid_review":true}'
        payload = messages[1]["content"]
        verdict = {"review_rejected": "rejected", "review_unknown": "unknown"}.get(task["scenario"], "accepted")
        report = {"binding": copy.deepcopy(payload["binding"]), "rules": [], "overall_verdict": verdict}
        segment = payload["source"]["segments"][0]["segment_id"]
        shot_id = payload["plan"]["artifact"]["shots"][0]["id"]
        for rule_id in RULE_IDS:
            rule = {"rule_id": rule_id, "verdict": "accepted", "checked_scope": "合成场景，仅核验独立审阅控制流。",
                    "evidence": [{"source_segment_ids": [segment], "targets": [{"shot_id": "*", "field": "$"}],
                                  "note": "合成证据，不构成真实内容判断。"}], "findings": []}
            if rule_id == "RV-A" and verdict != "accepted":
                rule["verdict"] = verdict
                rule["findings"] = [{"finding_id": "SYNTHETIC-F01", "prior_finding_id": None, "rule_id": rule_id,
                                     "targets": [{"shot_id": shot_id, "field": "camera"}], "source_segment_ids": [segment],
                                     "problem": "合成运镜问题或缺证。", "impact": "仅用于验证有界返工提案。",
                                     "repair_scope": [{"shot_id": shot_id, "field": "camera"}],
                                     "preserve": [{"shot_id": shot_id, "field": "visual"}],
                                     "recheck_condition": "合成运镜问题消除后另行复核。"}]
            report["rules"].append(rule)
        return json.dumps(report, ensure_ascii=False)


def validate(document, payload, previous=None):
    """Rebuild role context and receipt-derived results on every storage read/write."""
    # Local import keeps the old review -> store dependency acyclic.
    from core.planner.store import require

    value = payload["collaboration"]
    require(type(value) is dict and set(value) == FIELDS)
    constants = initial()
    for field in ("schema_version", "evidence_kind", "roles", "max_total_calls", "max_reviewer_calls", "paid_call_limit"):
        require(type(value[field]) is type(constants[field]) and value[field] == constants[field])
    require(value["status"] in {"awaiting_plan", "review_ready", "review_running", "response_unknown"} | TERMINAL)
    for field in ("planner_call_count", "reviewer_call_count"):
        require(type(value[field]) is int and value[field] >= 0)
    operations = payload["operations"]
    planner = [operation for operation in operations if operation["role"] == "planner"]
    reviewers = [operation for operation in operations if operation["role"] == "reviewer"]
    require(value["planner_call_count"] == len(planner) and value["reviewer_call_count"] == len(reviewers) <= 1)
    require(len(planner) + len(reviewers) == payload["step_count"] <= value["max_total_calls"])
    require(len(payload["plans"]) <= 1)
    for operation in operations:
        if operation["status"] == "intent":
            require(operation["response"] is None and operation["error"] is None)
        elif operation["status"] == "unknown":
            require(operation["response"] is None and operation["error"] == "MODEL_RESPONSE_UNCONFIRMED")
        else:
            require(operation["error"] is None and type(operation["response"]) is str
                    or operation["error"] in {"OUTPUT_LIMIT", "INVALID_OUTPUT"} and operation["response"] is None)
    unfinished = [operation["id"] for operation in operations if operation["status"] != "processed"]
    require(unfinished == ([] if payload["pending"] is None else [payload["pending"]]))
    # The review may only follow a plan actually received by the local submit tool.
    read, accepted = False, []
    for operation in planner:
        require(not accepted)
        if operation["status"] == "processed":
            if operation["error"] is not None:
                continue
            _, plan, read = execute_tool(operation["response"], document["source"], read)
            if plan is not None:
                accepted.append(plan)
    require(payload["read_source"] == read)
    require([plan["plan"] for plan in payload["plans"]] == accepted)
    if previous is None:
        require(value == initial() and payload["status"] == "ready" and not operations and not payload["plans"])
    else:
        require(payload["plans"][:len(previous["plans"])] == previous["plans"])
        require(len(previous["operations"]) <= len(operations) <= len(previous["operations"]) + 1)
        if len(operations) > len(previous["operations"]):
            require(operations[-1]["status"] == "intent")
        transitions = {"intent": {"intent", "received", "unknown"}, "received": {"received", "processed"}, "processed": {"processed"}, "unknown": {"unknown"}}
        for before, after in zip(previous["operations"], operations):
            require(all(before[key] == after[key] for key in ("id", "step", "role", "context", "context_hash")))
            require(after["status"] in transitions[before["status"]])
            if before["status"] != "intent":
                require(all(before[key] == after[key] for key in ("response", "error")))
        old = previous["collaboration"]
        if old["binding"] is not None:
            require(value["binding"] == old["binding"])
        if old["status"] in TERMINAL:
            require(value == old)
    if not reviewers:
        require(value["binding"] is None and value["report"] is None and value["report_hash"] is None and value["repair_task"] is None)
        require(value["status"] == ("review_ready" if accepted else "awaiting_plan"))
        require(payload["status"] != "plan_ready")
        return
    require(accepted and operations[-1] == reviewers[0] and all(op["status"] == "processed" for op in planner))
    operation = reviewers[0]
    binding = value["binding"]
    require(type(binding) is dict)
    require(binding["run_id"] == binding["task_id"] == document["id"] and binding["intent_id"] == operation["id"])
    require(type(binding["plan_version"]) is int and binding["plan_version"] == payload["plans"][-1]["version"])
    data = review_input(document, payload)
    require(operation["context"] == review_messages(data))
    if operation["status"] != "processed":
        require(value["report"] is None and value["report_hash"] is None and value["repair_task"] is None)
        require(value["status"] == ("response_unknown" if operation["status"] == "unknown" else "review_running"))
        require(payload["status"] in {"running", "queued", "stopped", "response_unknown"})
        if operation["status"] == "unknown":
            require(payload["status"] in {"stopped", "response_unknown"})
        return
    try:
        if operation["error"] is not None:
            raise ReviewValidationError()
        report = validate_review_output(operation["response"], data)
    except ReviewValidationError:
        require(value["status"] == "invalid" and value["report"] is None and value["report_hash"] is None and value["repair_task"] is None)
        require(payload["status"] == "blocked" and payload["blocked_reason"] == "REVIEW_INVALID")
    else:
        verdict = report["overall_verdict"]
        require(value["status"] == verdict and value["report"] == report and value["report_hash"] == digest(report))
        require(value["repair_task"] == build_repair_task(data, report))
        require(payload["status"] == ("plan_ready" if verdict == "accepted" else "blocked"))
        require(payload["blocked_reason"] == {"accepted": None, "rejected": "REVIEW_REQUIRES_REPAIR", "unknown": "REVIEW_NEEDS_INPUT"}[verdict])
