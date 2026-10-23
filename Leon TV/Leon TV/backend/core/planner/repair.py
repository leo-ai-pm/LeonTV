"""Pure proposals and structural checks for explicit, version-bound repair."""
import json

from core.creator.store import digest
from core.planner.protocol import checked_plan
from core.planner.review import SHOT_FIELDS, checked_payload, json_copy, validate_review_output


class RepairValidationError(ValueError):
    def __init__(self):
        self.code = "REPAIR_INVALID"
        super().__init__(self.code)


def require(value):
    if not value:
        raise RepairValidationError() from None


def target_pairs(targets, shot_ids):
    result = set()
    for target in targets:
        if target["shot_id"] == "*":
            result.update((shot_id, field) for shot_id in shot_ids for field in SHOT_FIELDS)
        else:
            result.add((target["shot_id"], target["field"]))
    return result


def targets(pairs, shot_ids):
    return [{"shot_id": shot_id, "field": field} for shot_id in shot_ids for field in SHOT_FIELDS if (shot_id, field) in pairs]


def checked_report(payload, report):
    require(type(report) is dict)
    return validate_review_output(json.dumps(report, ensure_ascii=False, allow_nan=False), payload)


def build_repair_task(payload: dict, report: dict) -> dict | None:
    """Propose a minimum scope; no model call, version write or permissions."""
    try:
        payload = checked_payload(payload)
        report = checked_report(payload, json_copy(report))
        if report["overall_verdict"] == "accepted":
            return None
        plan, binding = payload["plan"]["artifact"], payload["binding"]
        shot_ids = [shot["id"] for shot in plan["shots"]]
        findings = sorted((finding for rule in report["rules"] if rule["verdict"] != "accepted" for finding in rule["findings"]),
            key=lambda finding: (finding["rule_id"], finding["finding_id"]))
        scope = target_pairs([target for finding in findings for target in finding["repair_scope"]], shot_ids)
        keep = target_pairs([target for finding in findings for target in finding["preserve"]], shot_ids)
        keep.update((shot_id, "id") for shot_id in shot_ids)
        editable = scope - keep
        all_fields = {(shot_id, field) for shot_id in shot_ids for field in SHOT_FIELDS}
        preserve = all_fields - editable
        # Dependency closure informs recheck only, never expands edit authority.
        affected = {shot_id for shot_id, _ in editable}
        while True:
            extended = affected | {shot["id"] for shot in plan["shots"] if set(shot["depends_on"]) & affected}
            if extended == affected:
                break
            affected = extended
        return {"kind": "needs_input" if not editable or any(rule["verdict"] == "unknown" for rule in report["rules"]) else "content_repair_ready",
            "binding": {"source_snapshot_hash": binding["source_snapshot_hash"], "parent_plan_version": binding["plan_version"],
                "parent_plan_hash": binding["plan_hash"], "review_id": binding["review_id"], "report_hash": digest(report),
                "rule_version": binding["rule_version"], "rule_hash": binding["rule_hash"], "next_plan_version": binding["plan_version"] + 1},
            "finding_refs": [{"rule_id": finding["rule_id"], "finding_id": finding["finding_id"]} for finding in findings],
            "repair_targets": targets(editable, shot_ids), "affected_shot_ids": [shot_id for shot_id in shot_ids if shot_id in affected],
            "preserve_targets": targets(preserve, shot_ids),
            "invariants": {"shot_ids": shot_ids, "duration_ms": plan["duration_ms"], "ratio": plan["ratio"],
                "source_snapshot_hash": binding["source_snapshot_hash"], "rule_version": binding["rule_version"], "rule_hash": binding["rule_hash"]}}
    except RepairValidationError:
        raise
    except Exception:
        raise RepairValidationError() from None


def validate_repair_candidate(candidate: dict, payload: dict, task: dict, report: dict) -> dict:
    """Check a proposed structure; a new version still needs actual tool/review."""
    try:
        payload = checked_payload(payload)
        fresh = build_repair_task(payload, json_copy(report))
        require(fresh is not None and fresh["kind"] == "content_repair_ready")
        require(type(task) is dict and digest(json_copy(task)) == digest(fresh))
        candidate = json_copy(candidate)
        checked, error = checked_plan(candidate, payload["binding"]["source_snapshot_hash"])
        require(error is None and checked == candidate)
        original = payload["plan"]["artifact"]
        shot_ids = [shot["id"] for shot in original["shots"]]
        require([shot["id"] for shot in candidate["shots"]] == shot_ids)
        require(all(candidate[name] == original[name] for name in ("source_snapshot_hash", "duration_ms", "ratio")))
        allowed = target_pairs(fresh["repair_targets"], shot_ids)
        changed = set()
        for before, after in zip(original["shots"], candidate["shots"], strict=True):
            for field in SHOT_FIELDS:
                if before[field] != after[field]:
                    changed.add((before["id"], field))
        require(bool(changed) and changed <= allowed)
        return checked
    except RepairValidationError:
        raise
    except Exception:
        raise RepairValidationError() from None
