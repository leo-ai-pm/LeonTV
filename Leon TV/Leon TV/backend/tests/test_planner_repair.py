"""D18 pure bounded content-repair proposals; no actual repair execution."""
import copy
import importlib

import pytest
from test_planner_review import synthetic_review_case


def repair_module():
    return importlib.import_module("core.planner.repair")


def test_accepted_report_needs_no_manufactured_repair():
    payload, report = synthetic_review_case()
    assert repair_module().build_repair_task(payload, report) is None


def test_rejected_report_builds_minimal_scope_and_dependency_recheck_closure():
    payload, report = synthetic_review_case("rejected")
    before = copy.deepcopy((payload, report))
    task = repair_module().build_repair_task(payload, report)
    assert task["kind"] == "content_repair_ready" and task["binding"]["next_plan_version"] == 2
    assert task["repair_targets"] == [{"shot_id": "S01", "field": "camera"}]
    assert task["affected_shot_ids"] == ["S01", "S02", "S03"]
    assert {"shot_id": "S02", "field": "camera"} in task["preserve_targets"]
    assert {"shot_id": "S01", "field": "id"} in task["preserve_targets"]
    assert (payload, report) == before


def test_candidate_only_changes_explicit_field_and_remains_structure_not_quality_acceptance():
    payload, report = synthetic_review_case("rejected")
    repair = repair_module()
    task = repair.build_repair_task(payload, report)
    candidate = copy.deepcopy(payload["plan"]["artifact"])
    candidate["shots"][0]["camera"] = "SYNTHETIC slow push"
    result = repair.validate_repair_candidate(candidate, payload, task, report)
    assert result == candidate and "accepted" not in result


@pytest.mark.parametrize("case", ["unknown", "outside_scope", "id", "order", "source", "total", "ratio", "noop", "stale_task", "scope_tamper", "report_tamper", "preserve"])
def test_repair_never_overrides_scope_version_source_or_preservation(case):
    payload, report = synthetic_review_case("unknown" if case == "unknown" else "rejected")
    repair = repair_module()
    if case == "preserve":
        report["rules"][3]["findings"][0]["preserve"].append({"shot_id": "S01", "field": "camera"})
    task = repair.build_repair_task(payload, report)
    candidate = copy.deepcopy(payload["plan"]["artifact"])
    candidate["shots"][0]["camera"] = "SYNTHETIC repaired camera"
    if case == "outside_scope":
        candidate["shots"][1]["visual"] = "out of scope downstream change"
    elif case == "id":
        candidate["shots"][0]["id"] = "NEW"
        candidate["shots"][1]["depends_on"] = ["NEW"]
    elif case == "order":
        candidate["shots"].reverse()
    elif case == "source":
        candidate["source_snapshot_hash"] = "0" * 64
    elif case == "total":
        candidate["shots"][0]["duration_ms"] -= 1
    elif case == "ratio":
        candidate["ratio"] = "16:9"
    elif case == "noop":
        candidate = copy.deepcopy(payload["plan"]["artifact"])
    elif case == "stale_task":
        task["binding"]["parent_plan_version"] = 2
    elif case == "scope_tamper":
        task["repair_targets"].append({"shot_id": "S02", "field": "visual"})
    elif case == "report_tamper":
        report["rules"][3]["findings"][0]["problem"] = "changed report after task freeze"
    with pytest.raises(repair.RepairValidationError, match="^REPAIR_INVALID$"):
        repair.validate_repair_candidate(candidate, payload, task, report)


def test_unknown_produces_input_request_and_new_version_cannot_inherit_old_review():
    payload, report = synthetic_review_case("unknown")
    task = repair_module().build_repair_task(payload, report)
    assert task["kind"] == "needs_input" and task["binding"]["parent_plan_hash"] == payload["binding"]["plan_hash"]
    assert task["binding"]["next_plan_version"] == 2 and not {"permission", "max_calls", "accepted"} & set(task)


def test_global_scope_keeps_ids_and_all_invariants_while_preserve_wins():
    payload, report = synthetic_review_case("rejected")
    finding = report["rules"][3]["findings"][0]
    finding["repair_scope"] = [{"shot_id": "*", "field": "$"}]
    task = repair_module().build_repair_task(payload, report)
    assert {"shot_id": "S01", "field": "visual"} not in task["repair_targets"]
    assert all(target["field"] != "id" for target in task["repair_targets"])
    assert task["invariants"]["shot_ids"] == ["S01", "S02", "S03"]


@pytest.mark.parametrize("case", ["empty_scope", "all_preserved"])
def test_no_effective_edit_targets_requests_input_without_changing_review_verdict(case):
    payload, report = synthetic_review_case("rejected")
    finding = report["rules"][3]["findings"][0]
    if case == "empty_scope":
        finding["repair_scope"] = []
    else:
        finding["preserve"] = copy.deepcopy(finding["repair_scope"])
    before = copy.deepcopy(report)
    task = repair_module().build_repair_task(payload, report)
    assert task["kind"] == "needs_input" and task["repair_targets"] == []
    assert report == before and report["overall_verdict"] == "rejected"


def test_review_and_repair_apis_are_pure_and_do_not_execute_original_tools(monkeypatch):
    import builtins
    import os
    import subprocess

    from test_planner_review import encoded, review_module

    import core.planner.protocol as protocol

    payload, report = synthetic_review_case("rejected")
    source, plan, binding = payload["source"]["snapshot"], payload["plan"]["artifact"], payload["binding"]
    candidate = copy.deepcopy(plan)
    candidate["shots"][0]["camera"] = "SYNTHETIC repair"
    review, repair = review_module(), repair_module()

    def forbidden(*args, **kwargs):
        pytest.fail("Pure reviewer contracts attempted external state or tool execution")

    with monkeypatch.context() as guard:
        guard.setattr(builtins, "open", forbidden)
        guard.setattr(os, "getenv", forbidden)
        guard.setattr(subprocess, "Popen", forbidden)
        guard.setattr(protocol, "execute_tool", forbidden)
        rebuilt = review.build_review_input(source, plan, binding)
        review.review_messages(rebuilt)
        validated = review.validate_review_output(encoded(report), rebuilt)
        task = repair.build_repair_task(rebuilt, validated)
        result = repair.validate_repair_candidate(candidate, rebuilt, task, validated)
    assert result == candidate


def test_candidate_copy_does_not_repair_strict_schema_container_types():
    payload, report = synthetic_review_case("rejected")
    repair = repair_module()
    task = repair.build_repair_task(payload, report)
    candidate = copy.deepcopy(payload["plan"]["artifact"])
    candidate["shots"][0]["camera"] = "SYNTHETIC repair"
    candidate["shots"][1]["depends_on"] = tuple(candidate["shots"][1]["depends_on"])
    with pytest.raises(repair.RepairValidationError):
        repair.validate_repair_candidate(candidate, payload, task, report)
