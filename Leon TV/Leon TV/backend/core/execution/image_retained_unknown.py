"""Read-only retained-risk plans over an existing funded image scope.

Pure reconstruction is not authorization. The caller must supply the validated
native snapshot and freshly verified original funding; live collector evidence
is checked again at every dispatch boundary, never cached across decisions.
"""
import copy
import hashlib
import re
from datetime import timedelta

from core.creator.store import CreatorError, digest
from core.execution.control import _read
from core.execution.image_policy import FrozenImageRequest, timestamp
from core.execution.image_response_lifecycle import response_deadline
from core.execution.media_ledger import account_halted, records, unresolved_text_claims
from core.execution.policy import money, now, units
from core.execution.receipts import ReceiptStore
from core.planner.product_assets import image_jobs
from core.planner.review import json_copy

KIND = "image_retained_unknown_plan_v1"
FIELDS = {"schema_version", "kind", "scope_id", "scope_hash", "grant_id", "grant_hash", "account_id",
    "base_runtime_activation_id", "base_authority_hash", "funding_hash", "max_cumulative_submissions",
    "used_submissions", "remaining_funded_slots", "operations", "retained_unknowns", "new_unknown_ids",
    "funded_targets", "deferred_targets", "retained_risk_released_cny", "call_slots_released",
    "old_request_retry_authorized", "new_unknown_stops_continuation", "original_records_must_remain_unchanged", "plan_hash"}
TARGET_FIELDS = {"shot_id", "job_hash", "first_frame_version", "attempt_version", "replacement_of", "new_operation_id", "new_nonce",
    "planned_submission_index", "reference_shot_ids", "references_require_exact_delivered_operation"}
DEFERRED_FIELDS = TARGET_FIELDS - {"planned_submission_index"} | {"reason", "required_paid_calls", "required_risk_cny"}
UNKNOWN_FIELDS = {"operation_id", "operation_hash", "descriptor_hash", "permit_hash", "shot_id", "submission_index",
    "original_deadline_at", "original_response_deadline_at", "retained_risk_cny", "consumed_call_slots",
    "zero_http_proven", "zero_fee_proven", "original_record_must_remain_unchanged", "collector"}
COLLECTOR_FIELDS = {"inspection", "files", "claim_hash", "cleanup_cutoff", "observer_closed_hash", "collector_hash"}
INSPECTION_FIELDS = {"operation_id", "status", "candidate_count", "binding_verified", "isolated", "recovery_performed",
    "notification_present", "body_sha256", "response_received_at", "durable_confirmation_at", "original_deadline_at", "observer_until"}
ALLOWED_FILES = {"claim.json", "worker.json", "started.json", "observer-closed.json"}


def require(value):
    if not value:
        raise CreatorError("IMAGE_RETAINED_UNKNOWN_UNAVAILABLE", "The exact retained-risk image plan is unavailable", 409)


def _hash(value):
    return type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _id(value):
    return type(value) is str and re.fullmatch(r"[0-9a-f]{32}", value) is not None


def _same(left, right):
    return digest(left) == digest(right)


def _seal(value, field):
    return {**value, field: digest(value)}


def _file_inventory(root):
    if not root.exists():
        return []
    require(root.is_dir() and not root.is_symlink() and root.resolve() == root)
    entries = sorted(root.iterdir(), key=lambda path: path.name)
    require(0 < len(entries) <= len(ALLOWED_FILES) and {path.name for path in entries} <= ALLOWED_FILES)
    result = []
    for path in entries:
        data = _read(path, maximum=65536)
        result.append({"name": path.name, "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)})
    return result


def _collector(control, operation):
    descriptor = FrozenImageRequest.from_frozen(operation["descriptor"])
    binding = descriptor.binding()
    store = ReceiptStore(control.roots["execution"] / "media-receipts")
    store.check_private((control.backend_root / "code", control.backend_root.parent / "frontend/public", *control.static_roots))
    root = store.operation(operation["id"]).root
    before = _file_inventory(root)
    inspection = store.inspect_binding(operation["id"], binding)
    require(inspection["status"] in {"missing", "incomplete"} and inspection["candidate_count"] == 0)
    require(inspection["notification_present"] is False and inspection["body_sha256"] is None)
    # A missing directory is not evidence that no HTTP happened. Retain risk.
    require(now() >= response_deadline(binding) + timedelta(seconds=5))
    claim_hash = cutoff = closed_hash = None
    if inspection["status"] == "missing":
        require(before == [] and not root.exists())
    else:
        require({item["name"] for item in before} >= {"claim.json", "observer-closed.json"})
        claim = store.operation(operation["id"]).claim(binding)
        closed = store.operation(operation["id"]).read("observer-closed.json")
        cutoff = claim["cleanup_cutoff"]
        require(now() >= timestamp(cutoff))
        claim_hash, closed_hash = claim["claim_hash"], closed["closed_hash"]
    require(_file_inventory(root) == before)
    return _seal({"inspection": inspection, "files": before, "claim_hash": claim_hash,
        "cleanup_cutoff": cutoff, "observer_closed_hash": closed_hash}, "collector_hash")


def _unknown(operation, collector):
    descriptor = FrozenImageRequest.from_frozen(operation["descriptor"])
    binding = descriptor.binding()
    require(operation["status"] == "unknown" and operation["capture_started_at"] is not None
        and all(operation[field] is None for field in ("raw", "receipt", "fee_id", "delivery_id")))
    permit = descriptor.frozen()["permit"]
    require(descriptor.method == "POST" and binding["operation_id"] == operation["id"]
        and binding["task_id"] == operation["task_id"] and permit["grant_id"] == operation["grant_id"]
        and permit["scope_submission_index"] == operation["submission_index"] and permit["shot_id"] == operation["shot_id"])
    return {"operation_id": operation["id"], "operation_hash": digest(operation), "descriptor_hash": descriptor.descriptor_hash,
        "permit_hash": operation["descriptor"]["permit"]["permit_hash"], "shot_id": operation["shot_id"],
        "submission_index": operation["submission_index"], "original_deadline_at": descriptor.deadline_at,
        "original_response_deadline_at": response_deadline(binding).isoformat(),
        "retained_risk_cny": operation["descriptor"]["permit"]["risk_cny"], "consumed_call_slots": 1,
        "zero_http_proven": False, "zero_fee_proven": False, "original_record_must_remain_unchanged": True,
        "collector": collector}


def _selected_operations(payload, scope, grant):
    account = scope["account_id"]
    require(grant["revoked_at"] is None and not account_halted(payload, account) and not unresolved_text_claims(payload))
    scopes = {item["id"] for item in records(payload, "scope") if item["account_id"] == account}
    account_ops = [item for item in records(payload, "operation") if item["scope_id"] in scopes]
    for operation in account_ops:
        if operation["status"] == "unknown":
            require(operation["scope_id"] == scope["id"] and operation["grant_id"] == grant["id"])
            require(operation["capture_started_at"] is not None
                and all(operation[field] is None for field in ("raw", "receipt", "fee_id", "delivery_id")))
        else:
            require(operation["status"] not in {"reserved", "submitting"} and operation["fee_id"] is not None)
    require(all(item["status"] not in {"reserved", "submitting", "unknown"}
        for item in records(payload, "query") if item["scope_id"] in scopes))
    require(all(item["status"] == "settled" for item in payload["operations"].values() if item["account_id"] == account))
    asset_scopes = {item["id"] for item in payload.get("asset_design", {}).values()
        if item["kind"] == "scope" and item["account_id"] == account}
    require(all(item["status"] not in {"reserved", "submitting", "unknown"} and item["fee_id"] is not None
        for item in payload.get("asset_design", {}).values() if item["kind"] == "operation" and item["scope_id"] in asset_scopes))
    own = sorted((item for item in account_ops if item["scope_id"] == scope["id"]), key=lambda item: item["submission_index"])
    require(own and all(item["grant_id"] == grant["id"] for item in own))
    require([item["submission_index"] for item in own] == list(range(1, len(own) + 1)))
    require(all(item["status"] == "unknown" or (item["status"] == "accepted" and item["delivery_id"] is not None) for item in own))
    return own


def _targets(scope, operations):
    result = []
    for job in image_jobs(scope["asset_input"], scope["manifest"]):
        prior = [item for item in operations if item["shot_id"] == job["shot_id"]]
        delivered = [item for item in prior if item["delivery_id"] is not None]
        require(len(delivered) <= 1)
        if delivered:
            continue
        require(not prior or (len(prior) == 1 and prior[0]["status"] == "unknown"))
        replacement = prior[0]["id"] if prior else None
        result.append({"shot_id": job["shot_id"], "job_hash": digest(job), "first_frame_version": 1,
            "attempt_version": 2 if replacement else 1, "replacement_of": replacement,
            "new_operation_id": None, "new_nonce": None, "planned_submission_index": len(operations) + len(result) + 1,
            "reference_shot_ids": copy.deepcopy(job["reference_shot_ids"]), "references_require_exact_delivered_operation": True})
    return result


def _context(scope, grant, base_authority, funding):
    require(type(base_authority["schema_version"]) is int and base_authority["schema_version"] in {2, 3}
        and base_authority["kind"] == ("image_continuation_authority_v2" if base_authority["schema_version"] == 2 else "image_continuation_authority_v3")
        and base_authority["authority_hash"] == digest({k: v for k, v in base_authority.items() if k != "authority_hash"})
        and base_authority["scope_id"] == scope["id"] and base_authority["scope_hash"] == digest(scope)
        and base_authority["grant_id"] == grant["id"] and base_authority["grant_hash"] == digest({**grant, "revoked_at": None}))
    require(grant["scope_id"] == scope["id"] and grant["task_id"] == scope["task_id"])
    require(funding["scope_id"] == scope["id"] and funding["intent_id"] == base_authority["intent_id"]
        and funding["validity_activation_id"] == base_authority["validity_activation_id"]
        and funding["parent_receipt_hash"] == base_authority["parent_receipt_hash"]
        and _same(funding["allocations"], base_authority["allocations"]))
    require(funding["funding_hash"] == digest({key: value for key, value in funding.items() if key != "funding_hash"}))
    require(sum(item["max_calls"] for item in funding["allocations"]) == base_authority["max_cumulative_submissions"])


def _facts(scope, grant, base_authority, funding, operations, unknowns):
    _context(scope, grant, base_authority, funding)
    used, maximum = len(operations), base_authority["max_cumulative_submissions"]
    remaining = maximum - used
    require(1 <= remaining <= 12)
    base_unknown = base_authority["unknown_disposition"]
    matches = [item for item in unknowns if item["operation_id"] == base_unknown["operation_id"]]
    require(len(matches) == 1 and matches[0]["operation_hash"] == base_unknown["operation_hash"])
    new_unknown_ids = [item["operation_id"] for item in unknowns if item["operation_id"] != base_unknown["operation_id"]]
    require(bool(new_unknown_ids))
    targets = _targets(scope, operations)
    require(bool(targets) and any(item["replacement_of"] in new_unknown_ids for item in targets[:remaining]))
    deferred = [{**{k: v for k, v in item.items() if k != "planned_submission_index"},
        "reason": "funding_exhausted", "required_paid_calls": 1,
        "required_risk_cny": scope["policy"]["per_submission_risk_cny"]} for item in targets[remaining:]]
    return {"schema_version": 1, "kind": KIND, "scope_id": scope["id"], "scope_hash": digest(scope),
        "grant_id": grant["id"], "grant_hash": digest({**grant, "revoked_at": None}), "account_id": scope["account_id"],
        "base_runtime_activation_id": base_authority["activation_id"], "base_authority_hash": base_authority["authority_hash"],
        "funding_hash": funding["funding_hash"], "max_cumulative_submissions": maximum, "used_submissions": used,
        "remaining_funded_slots": remaining,
        "operations": [{"operation_id": item["id"], "operation_hash": digest(item), "submission_index": item["submission_index"]} for item in operations],
        "retained_unknowns": unknowns, "new_unknown_ids": new_unknown_ids,
        "funded_targets": targets[:remaining], "deferred_targets": deferred,
        "retained_risk_released_cny": "0.000000", "call_slots_released": 0, "old_request_retry_authorized": False,
        "new_unknown_stops_continuation": True, "original_records_must_remain_unchanged": True}


def _checked_collector(value, unknown):
    require(type(value) is dict and set(value) == COLLECTOR_FIELDS
        and value["collector_hash"] == digest({k: v for k, v in value.items() if k != "collector_hash"}))
    inspection = value["inspection"]
    require(type(inspection) is dict and set(inspection) == INSPECTION_FIELDS)
    require(inspection["operation_id"] == unknown["operation_id"] and inspection["status"] in {"missing", "incomplete"}
        and type(inspection["candidate_count"]) is int and inspection["candidate_count"] == 0
        and inspection["binding_verified"] is False and inspection["isolated"] is True
        and inspection["recovery_performed"] is False and inspection["notification_present"] is False
        and all(inspection[key] is None for key in ("body_sha256", "response_received_at", "durable_confirmation_at"))
        and inspection["original_deadline_at"] == unknown["original_deadline_at"])
    files = value["files"]
    require(type(files) is list and len(files) <= len(ALLOWED_FILES))
    for item in files:
        require(type(item) is dict and set(item) == {"name", "sha256", "size"} and item["name"] in ALLOWED_FILES
            and _hash(item["sha256"]) and type(item["size"]) is int and 0 < item["size"] <= 65536)
    names = [item["name"] for item in files]
    require(names == sorted(set(names)))
    if inspection["status"] == "missing":
        require(files == [] and inspection["observer_until"] is None
            and value["claim_hash"] is value["cleanup_cutoff"] is value["observer_closed_hash"] is None)
    else:
        require(set(names) >= {"claim.json", "observer-closed.json"} and _hash(value["claim_hash"])
            and _hash(value["observer_closed_hash"]))
        require(timestamp(inspection["observer_until"]) <= timestamp(unknown["original_response_deadline_at"])
            < timestamp(value["cleanup_cutoff"]))


def validate_plan(value):
    """Pure strict value validation. Neither seals nor this function grant dispatch."""
    try:
        value = json_copy(value, limit=1024 * 1024)
        require(type(value) is dict and set(value) == FIELDS and type(value["schema_version"]) is int
            and value["schema_version"] == 1 and value["kind"] == KIND)
        require(value["plan_hash"] == digest({k: v for k, v in value.items() if k != "plan_hash"}))
        require(all(_id(value[k]) for k in ("scope_id", "grant_id", "base_runtime_activation_id")))
        require(all(_hash(value[k]) for k in ("scope_hash", "grant_hash", "base_authority_hash", "funding_hash")))
        require(type(value["account_id"]) is str and 0 < len(value["account_id"]) <= 128)
        require(all(type(value[k]) is int for k in ("max_cumulative_submissions", "used_submissions", "remaining_funded_slots", "call_slots_released")))
        require(1 <= value["used_submissions"] < value["max_cumulative_submissions"] <= 12
            and value["remaining_funded_slots"] == value["max_cumulative_submissions"] - value["used_submissions"])
        require(value["retained_risk_released_cny"] == "0.000000" and value["call_slots_released"] == 0
            and value["old_request_retry_authorized"] is False and value["new_unknown_stops_continuation"] is True
            and value["original_records_must_remain_unchanged"] is True)
        operations = value["operations"]
        require(type(operations) is list and len(operations) == value["used_submissions"])
        for i, item in enumerate(operations, 1):
            require(type(item) is dict and set(item) == {"operation_id", "operation_hash", "submission_index"}
                and _id(item["operation_id"]) and _hash(item["operation_hash"])
                and type(item["submission_index"]) is int and item["submission_index"] == i)
        ids = [item["operation_id"] for item in operations]
        require(len(ids) == len(set(ids)))
        unknowns = value["retained_unknowns"]
        require(type(unknowns) is list and 2 <= len(unknowns) <= len(operations))
        for item in unknowns:
            require(type(item) is dict and set(item) == UNKNOWN_FIELDS)
            require(item["operation_id"] in ids and all(_hash(item[k]) for k in ("operation_hash", "descriptor_hash", "permit_hash")))
            require(type(item["submission_index"]) is int and 1 <= item["submission_index"] <= len(operations))
            require(operations[item["submission_index"] - 1] == {k: item[k] for k in ("operation_id", "operation_hash", "submission_index")})
            require(type(item["shot_id"]) is str and re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,31}", item["shot_id"]) is not None)
            require(type(item["consumed_call_slots"]) is int and item["consumed_call_slots"] == 1
                and item["zero_http_proven"] is False and item["zero_fee_proven"] is False and item["original_record_must_remain_unchanged"] is True)
            require(item["retained_risk_cny"] == money(units(item["retained_risk_cny"])) and units(item["retained_risk_cny"]) > 0)
            require(timestamp(item["original_deadline_at"]) <= timestamp(item["original_response_deadline_at"]))
            _checked_collector(item["collector"], item)
        unknown_ids = [item["operation_id"] for item in unknowns]
        require(len(unknown_ids) == len(set(unknown_ids)) and unknown_ids == [item for item in ids if item in unknown_ids])
        new = value["new_unknown_ids"]
        require(type(new) is list and 1 <= len(new) < len(unknown_ids) and new == [item for item in unknown_ids if item in new])
        funded, deferred = value["funded_targets"], value["deferred_targets"]
        require(type(funded) is list and 1 <= len(funded) <= value["remaining_funded_slots"]
            and type(deferred) is list and len(funded) + len(deferred) <= 12)
        if deferred:
            require(len(funded) == value["remaining_funded_slots"])
        seen = set()
        for i, item in enumerate(funded + deferred):
            is_funded = i < len(funded)
            require(type(item) is dict and set(item) == (TARGET_FIELDS if is_funded else DEFERRED_FIELDS))
            require(type(item["shot_id"]) is str and re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,31}", item["shot_id"]) is not None
                and item["shot_id"] not in seen and _hash(item["job_hash"]))
            seen.add(item["shot_id"])
            require(type(item["first_frame_version"]) is int and item["first_frame_version"] == 1
                and type(item["attempt_version"]) is int and item["attempt_version"] in {1, 2}
                and item["new_operation_id"] is None and item["new_nonce"] is None
                and item["references_require_exact_delivered_operation"] is True and type(item["reference_shot_ids"]) is list)
            require(all(type(shot) is str and re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,31}", shot) is not None for shot in item["reference_shot_ids"]))
            require(len(item["reference_shot_ids"]) <= 11 and len(item["reference_shot_ids"]) == len(set(item["reference_shot_ids"]))
                and item["shot_id"] not in item["reference_shot_ids"])
            require((item["attempt_version"] == 1 and item["replacement_of"] is None)
                or (item["attempt_version"] == 2 and item["replacement_of"] in unknown_ids
                    and next(u["shot_id"] for u in unknowns if u["operation_id"] == item["replacement_of"]) == item["shot_id"]))
            if is_funded:
                require(type(item["planned_submission_index"]) is int and item["planned_submission_index"] == value["used_submissions"] + i + 1)
            else:
                require(item["reason"] == "funding_exhausted" and type(item["required_paid_calls"]) is int and item["required_paid_calls"] == 1
                    and item["required_risk_cny"] == money(units(item["required_risk_cny"])) and units(item["required_risk_cny"]) > 0)
        require(any(item["replacement_of"] in new for item in funded))
        return value
    except Exception:
        require(False)


def validate_plan_history(payload, scope, grant, plan, base_authority, funding, *, birth=False):
    """Rebuild old facts; birth additionally requires the complete then-current head."""
    try:
        require(type(birth) is bool)
        plan = validate_plan(plan)
        operations = [payload["media"]["operation:" + item["operation_id"]] for item in plan["operations"]]
        require(all(item["scope_id"] == scope["id"] and item["grant_id"] == grant["id"] for item in operations))
        require(all(digest(item) == frozen["operation_hash"] for item, frozen in zip(operations, plan["operations"], strict=True)))
        by_id = {item["id"]: item for item in operations}
        unknowns = [_unknown(by_id[item["operation_id"]], item["collector"]) for item in plan["retained_unknowns"]]
        require([item["id"] for item in operations if item["status"] == "unknown"] == [item["operation_id"] for item in unknowns])
        expected = _seal(_facts(scope, grant, base_authority, funding, operations, unknowns), "plan_hash")
        require(_same(plan, expected))
        if birth:
            require(_same(_selected_operations(payload, scope, grant), operations))
        return plan
    except Exception:
        require(False)


def build_plan(control, payload, scope, grant, base_authority, funding):
    """Read a complete proposal over existing funds; no state or ledger writes."""
    try:
        operations = _selected_operations(payload, scope, grant)
        unknowns = [_unknown(item, _collector(control, item)) for item in operations if item["status"] == "unknown"]
        plan = _seal(_facts(scope, grant, base_authority, funding, operations, unknowns), "plan_hash")
        return validate_plan_history(payload, scope, grant, plan, base_authority, funding, birth=True)
    except Exception:
        require(False)


def verify_plan_current(control, payload, plan):
    """Check frozen old facts and exact live evidence only, not candidate state."""
    try:
        plan = validate_plan(plan)
        for frozen in plan["operations"]:
            operation = payload["media"]["operation:" + frozen["operation_id"]]
            require(digest(operation) == frozen["operation_hash"])
        for frozen in plan["retained_unknowns"]:
            operation = payload["media"]["operation:" + frozen["operation_id"]]
            require(_same(_unknown(operation, _collector(control, operation)), frozen))
        return True
    except Exception:
        require(False)
