"""Reconstruct optional asset-design records in the original financial journal."""
import base64
import copy
import hashlib
from decimal import Decimal

from core.creator.store import ID_RE, KEY_RE, CreatorError, digest
from core.execution.empirical import decode
from core.execution.fees import FrozenFeeSource, parse_export
from core.execution.image_policy import MediaBudgetAnchor
from core.execution.media_ledger import _stable_handoff, native_context
from core.execution.policy import money, units
from core.execution.receipts import raw_body, timestamp
from core.planner import product_assets
from core.planner.review import json_copy
from experiments.planner_live.empirical_capture import FrozenEmpiricalDescriptor

COMMON = {"schema_version", "kind", "id", "task_id", "scope_id", "created_at"}
FIELDS = {
    "scope": {"account_id", "account_hash", "ordinal", "policy", "anchor", "asset_input", "handoff", "fee_source"},
    "quote": {"disclosure", "disclosure_hash"}, "grant": {"quote_id", "disclosure_hash", "revoked_at"},
    "operation": {"grant_id", "quote_id", "permit", "descriptor", "status", "capture_started_at", "raw", "receipt", "fee_id", "manifest"},
    "fee": {"operation_id", "candidate", "export_base64"}, "claim": {"operation_id", "candidate_hash"},
    "key": {"verb", "request", "request_hash", "resource_key"},
}


def require(value, code="CORRUPT_STORAGE"):
    if not value:
        raise CreatorError(code, "Asset design journal evidence is unavailable or inconsistent", 409)


def records(payload, kind):
    return [record for record in payload.get("asset_design", {}).values() if record["kind"] == kind]


def reserved_scopes(payload):
    return records(payload, "scope")


def _account_operations(payload, account_id):
    scopes = {scope["id"] for scope in reserved_scopes(payload) if scope["account_id"] == account_id}
    return [operation for operation in records(payload, "operation") if operation["scope_id"] in scopes]


def own_halted(payload, account_id):
    for operation in _account_operations(payload, account_id):
        if (operation["receipt"] or {}).get("local_units", 0) is not None and (operation["receipt"] or {}).get("local_units", 0) > 1000000:
            return True
        if operation["fee_id"] is not None and payload["asset_design"]["fee:" + operation["fee_id"]]["candidate"]["overrun"]:
            return True
    return False


def own_unknown(payload, account_id, exclude_operation_id=None):
    return any(op["id"] != exclude_operation_id and op["status"] in {"submitting", "unknown"} for op in _account_operations(payload, account_id))


def own_pending_fees(payload, account_id, exclude_operation_id=None):
    return any(op["id"] != exclude_operation_id and op["fee_id"] is None for op in _account_operations(payload, account_id))


def own_fee_claimed(payload, key, operation_id=None):
    return any(claim["id"] == key and claim["operation_id"] != operation_id for claim in records(payload, "claim"))


def fee_candidate(scope, operation, export_bytes, observed_at):
    require(operation["raw"] is not None, "ASSET_DESIGN_RECEIPT_REQUIRED")
    source = FrozenFeeSource.model_validate(scope["fee_source"])
    receipt, _ = decode(operation["raw"], scope["policy"])
    require(receipt["receipt_classification"] in {"complete", "truncated"} and receipt["local_units"] is not None, "FEE_EXPORT_PENDING")
    require(digest(receipt) == digest(operation["receipt"]), "FEE_EVIDENCE_CONFLICT")
    issued = timestamp(operation["descriptor"]["intent"]["issued_at"])
    received, observed = timestamp(operation["raw"]["received_at"]), timestamp(observed_at)
    require(issued <= received <= observed, "FEE_EVIDENCE_CONFLICT")
    matches = [(key, row) for key, row in parse_export(export_bytes).items()
        if row["type"] == 2 and row["token_name"] == source.token_name and row["group"] == source.group
        and row["model_name"] == scope["policy"]["model"] and row["prompt_tokens"] == receipt["input_tokens"]
        and row["completion_tokens"] == receipt["output_tokens"]
        and issued.timestamp() - 5 <= row["created_at"] <= received.timestamp() + 5]
    require(bool(matches), "FEE_EXPORT_PENDING")
    require(len(matches) == 1, "FEE_EXPORT_AMBIGUOUS")
    key, row = matches[0]
    fee = Decimal(row["quota"]) / source.quota_per_cny
    candidate = {"schema_version": 1, "kind": "asset_design_fee_observation_v1", "operation_id": operation["id"], "task_id": operation["task_id"],
        "descriptor_hash": operation["descriptor"]["descriptor_hash"], "raw_hash": digest(operation["raw"]),
        "raw_body_sha256": hashlib.sha256(raw_body(operation["raw"])).hexdigest(), "receipt_hash": digest(receipt),
        "platform_log_id": key, "source_export_hash": hashlib.sha256(export_bytes).hexdigest(), "fee_source_hash": digest(scope["fee_source"]),
        "quota": row["quota"], "platform_fee_units": row["quota"] * 2, "platform_fee_cny": format(fee, ".6f"),
        "observed_at": observed_at, "overrun": max(row["quota"] * 2, receipt["local_units"]) > 1000000,
        "risk_released": False, "provider_bill_confirmed": False, "settlement_performed": False}
    return {**candidate, "candidate_hash": digest(candidate)}


def _validate(payload, previous):
    from core.execution.asset_design import (
        AssetDesignPolicy,
        classify,
        descriptor_for,
        disclosure_for,
        permit_for,
    )
    from core.execution.media_ledger import (
        account_halted,
        fee_claimed,
        has_pending_fees,
        has_pending_unknown,
        unresolved_text_claims,
    )
    from core.execution.scope_budget import scope_envelope
    data = payload.get("asset_design", {})
    require(type(data) is dict)
    json_copy(data, limit=48 * 1048576)
    other_ids = set(payload["operations"]) | {record["id"] for record in payload.get("media", {}).values() if record["kind"] in {"operation", "query"}}
    for key, record in data.items():
        require(type(record) is dict and record.get("kind") in FIELDS and set(record) == COMMON | FIELDS[record["kind"]])
        require(type(record["schema_version"]) is int and record["schema_version"] == 1)
        require(key == record["kind"] + ":" + record["id"] and record["task_id"] in payload["tasks"])
        timestamp(record["created_at"])
        if record["kind"] not in {"fee", "claim", "key"}:
            require(ID_RE.fullmatch(record["id"]) is not None)
        if record["kind"] == "operation":
            require(record["id"] not in other_ids)
    scopes = sorted(reserved_scopes(payload), key=lambda scope: scope["ordinal"])
    used_allocations = {scope["anchor"]["parent_allocation_hash"] for scope in payload.get("media", {}).values() if scope["kind"] == "scope"}
    for ordinal, scope in enumerate(scopes, 1):
        require(type(scope["ordinal"]) is int and scope["ordinal"] == ordinal and scope["id"] == scope["scope_id"])
        policy = AssetDesignPolicy.model_validate({k: v for k, v in scope["policy"].items() if k != "policy_hash"}).frozen()
        anchor = MediaBudgetAnchor.model_validate({k: v for k, v in scope["anchor"].items() if k != "budget_anchor_hash"}).frozen()
        require(digest(policy) == digest(scope["policy"]) and digest(anchor) == digest(scope["anchor"]))
        require(anchor["scope_max_calls"] == 1 and units(anchor["scope_risk_cny"]) == 1000000)
        account = payload["accounts"][scope["account_id"]]
        require(account.get("accounting_kind") == "empirical_risk_v1" and scope["account_hash"] == digest(account))
        require(anchor["account_scope_id"] == account["id"] and anchor["parent_authorization_hash"] == account["anchor"]["parent_authorization_hash"])
        require(units(anchor["opening_risk_cny"]) >= account["limit_units"] and anchor["opening_paid_call_count"] >= account["max_calls"])
        require(anchor["parent_allocation_hash"] not in used_allocations)
        used_allocations.add(anchor["parent_allocation_hash"])
        context, handoff = native_context(payload, scope["task_id"], historical=True)
        require(digest(context) == digest(scope["asset_input"]) and digest(_stable_handoff(handoff)) == digest(_stable_handoff(scope["handoff"])))
        require(scope["handoff"]["handoff_hash"] == digest({k: v for k, v in scope["handoff"].items() if k != "handoff_hash"}))
        require(policy["validation_only"] is (scope["handoff"]["evidence_kind"] == "synthetic"))
        require(scope["handoff"]["evidence_kind"] in {"synthetic", "empirical_receipts"})
        require(FrozenFeeSource.model_validate(scope["fee_source"]).account_namespace == account["id"])
        identity = {"task_id": scope["task_id"], "policy_hash": policy["policy_hash"], "budget_anchor_hash": anchor["budget_anchor_hash"], "handoff_hash": scope["handoff"]["handoff_hash"]}
        require(scope["id"] == digest(identity)[:32])
        require(sum(op["scope_id"] == scope["id"] for op in records(payload, "operation")) <= 1)
    for record in data.values():
        kind = record["kind"]
        if kind == "scope":
            continue
        scope = data["scope:" + record["scope_id"]]
        require(record["task_id"] == scope["task_id"])
        if kind == "quote":
            require(record["disclosure_hash"] == digest({k: v for k, v in record.items() if k != "disclosure_hash"}))
            require(digest(record["disclosure"]) == digest(disclosure_for(scope)))
            require(timestamp(scope["created_at"]) <= timestamp(record["created_at"]) < timestamp(scope["policy"]["valid_until"]))
        elif kind == "grant":
            quote = data["quote:" + record["quote_id"]]
            require(quote["scope_id"] == scope["id"] and quote["disclosure_hash"] == record["disclosure_hash"])
            require(timestamp(quote["created_at"]) <= timestamp(record["created_at"]) < timestamp(scope["policy"]["valid_until"]))
            if record["revoked_at"] is not None:
                require(timestamp(record["revoked_at"]) >= timestamp(record["created_at"]))
        elif kind == "operation":
            grant = data["grant:" + record["grant_id"]]
            require(grant["scope_id"] == scope["id"] and record["quote_id"] == grant["quote_id"])
            descriptor = FrozenEmpiricalDescriptor.from_frozen(record["descriptor"])
            expected = descriptor_for(scope, grant, record["id"], descriptor.intent.issued_at)
            require(digest(expected.frozen()) == digest(record["descriptor"]))
            require(digest(record["permit"]) == digest(permit_for(scope, grant, descriptor)))
            require(timestamp(grant["created_at"]) <= timestamp(descriptor.intent.issued_at) <= timestamp(record["created_at"]) <= timestamp(descriptor.intent.deadline_at))
            require(record["capture_started_at"] is not None or record["status"] == "reserved")
            if record["capture_started_at"] is not None:
                require(timestamp(record["created_at"]) <= timestamp(record["capture_started_at"]) <= timestamp(descriptor.intent.deadline_at))
            fee = data["fee:" + record["fee_id"]]["candidate"] if record["fee_id"] is not None else None
            if record["raw"] is None:
                require(record["receipt"] is record["manifest"] is fee is None and record["status"] in {"reserved", "submitting", "unknown"})
            else:
                raw_body(record["raw"])
                receipt, manifest, status = classify(scope, record, record["raw"], fee)
                require(digest(receipt) == digest(record["receipt"]) and digest(manifest) == digest(record["manifest"]) and record["status"] == status)
        elif kind == "fee":
            operation = data["operation:" + record["operation_id"]]
            require(operation["scope_id"] == scope["id"] and operation["fee_id"] == record["id"])
            candidate = fee_candidate(scope, operation, base64.b64decode(record["export_base64"], validate=True), record["candidate"]["observed_at"])
            require(digest(candidate) == digest(record["candidate"]) and candidate["platform_log_id"] == record["id"])
            require("claim:" + record["id"] in data)
            require(not fee_claimed(payload, record["id"], operation["id"]))
        elif kind == "claim":
            fee = data["fee:" + record["id"]]
            require(fee["operation_id"] == record["operation_id"] and fee["scope_id"] == scope["id"] and record["candidate_hash"] == digest(fee["candidate"]))
        elif kind == "key":
            require(record["request_hash"] == digest(record["request"]) and record["resource_key"] in data)
            verb, _, token = record["id"].partition(":")
            require(verb == record["verb"] and verb in {"quote", "grant", "reserve"} and KEY_RE.fullmatch(token) is not None)
            resource = data[record["resource_key"]]
            require(resource["kind"] == {"quote": "quote", "grant": "grant", "reserve": "operation"}[verb])
            require(resource["task_id"] == record["task_id"] and resource["scope_id"] == scope["id"])
            expected = {"task_id": record["task_id"]}
            if verb == "grant":
                expected.update(quote_id=resource["quote_id"], disclosure_hash=resource["disclosure_hash"], consent=True)
                require(type(record["request"].get("consent")) is bool)
            elif verb == "reserve":
                expected.update(grant_id=resource["grant_id"])
            require(digest(expected) == digest(record["request"]))
    for record in data.values():
        if record["kind"] in {"quote", "grant", "operation"}:
            require(sum(key["resource_key"] == record["kind"] + ":" + record["id"] for key in records(payload, "key")) == 1)
    if previous is not None:
        old = previous.get("asset_design", {})
        require(set(old) <= set(data))
        for key, before in old.items():
            after = data[key]
            if before is after or digest(before) == digest(after):
                continue
            mutable = {"status", "capture_started_at", "raw", "receipt", "fee_id", "manifest"} if before["kind"] == "operation" else {"revoked_at"} if before["kind"] == "grant" else set()
            require(digest({k: v for k, v in before.items() if k not in mutable}) == digest({k: v for k, v in after.items() if k not in mutable}))
            for field in mutable - {"status"}:
                require(before[field] is None or digest(before[field]) == digest(after[field]))
            if before["kind"] == "operation":
                allowed = {"reserved": {"reserved", "submitting"}, "submitting": {"submitting", "unknown", "awaiting_fee", "invalid", "truncated"},
                    "unknown": {"unknown", "awaiting_fee", "invalid", "truncated"}, "awaiting_fee": {"awaiting_fee", "manifest_ready", "blocked"}}
                require(after["status"] in allowed.get(before["status"], {before["status"]}))
        new = [record for key, record in data.items() if key not in old]
        for record in new:
            kind = record["kind"]
            if kind in {"quote", "grant", "operation"}:
                require(sum(key["kind"] == "key" and key["resource_key"] == kind + ":" + record["id"] for key in new) == 1)
            if kind == "scope":
                context, handoff = native_context(previous, record["task_id"])
                require(digest(context) == digest(record["asset_input"]) and digest(handoff) == digest(record["handoff"]))
                envelope = scope_envelope(previous, record["account_id"])
                require(units(record["anchor"]["opening_risk_cny"]) >= envelope["risk_units"] and record["anchor"]["opening_paid_call_count"] >= envelope["max_calls"])
            elif kind == "grant":
                require("quote:" + record["quote_id"] in old and record["revoked_at"] is None and not unresolved_text_claims(previous))
            elif kind == "operation":
                require(record["status"] == "reserved" and all(record[field] is None for field in ("capture_started_at", "raw", "receipt", "fee_id", "manifest")))
                require("scope:" + record["scope_id"] in old and "grant:" + record["grant_id"] in old)
                require(old["grant:" + record["grant_id"]]["revoked_at"] is None and data["grant:" + record["grant_id"]]["revoked_at"] is None)
                scope = data["scope:" + record["scope_id"]]
                account_id = scope["account_id"]
                require(not unresolved_text_claims(previous) and not has_pending_unknown(previous, account_id) and not has_pending_fees(previous, account_id))
                require(not any(op["account_id"] == account_id and op["status"] != "settled" for op in previous["operations"].values()))
            elif kind == "fee":
                require(old["operation:" + record["operation_id"]]["raw"] is not None)
        for account_id in payload["accounts"]:
            new_text = [op for op_id, op in payload["operations"].items() if op_id not in previous["operations"] and op["account_id"] == account_id]
            new_images = [op for key, op in payload.get("media", {}).items() if key not in previous.get("media", {}) and op["kind"] == "operation"
                and payload["media"]["scope:" + op["scope_id"]]["account_id"] == account_id]
            new_assets = [op for op in new if op["kind"] == "operation" and data["scope:" + op["scope_id"]]["account_id"] == account_id]
            if new_text or new_images or new_assets:
                require(len(new_text) + len(new_images) + len(new_assets) <= 1)
                require(not account_halted(previous, account_id) and not account_halted(payload, account_id))
                if new_images and new_images[0]["descriptor"]["kind"] in {"controlled_image_http_v2", "controlled_image_http_v3"}:
                    from core.execution.media_continuation_gate import candidate_gate
                    candidate_gate(previous, new_images[0])
                    candidate_gate(payload, new_images[0], exclude_candidate=True)
                else:
                    require(not has_pending_unknown(previous, account_id) and not has_pending_unknown(payload, account_id))
                    require(not has_pending_fees(previous, account_id))


def validate(payload, previous=None):
    try:
        _validate(payload, previous)
    except Exception:
        raise CreatorError("CORRUPT_STORAGE", "Asset design journal failed integrity checks") from None


def verified_manifest(payload, task_id, operation_id):
    from core.execution.asset_design import classify
    # This public pure seam is also called while an image scope is being built;
    # independently reconstruct our namespace rather than trusting its caller.
    # Our validator scans global financial helpers but never validates media.
    validate(payload)
    operation = payload.get("asset_design", {}).get("operation:" + operation_id)
    require(operation is not None and operation["task_id"] == task_id and operation["status"] == "manifest_ready", "ASSET_DESIGN_MANIFEST_UNAVAILABLE")
    scope = payload["asset_design"]["scope:" + operation["scope_id"]]
    fee = payload["asset_design"]["fee:" + operation["fee_id"]]
    claim = payload["asset_design"]["claim:" + operation["fee_id"]]
    receipt, manifest, status = classify(scope, operation, operation["raw"], fee["candidate"])
    candidate = fee_candidate(scope, operation, base64.b64decode(fee["export_base64"], validate=True), fee["candidate"]["observed_at"])
    require(status == "manifest_ready" and digest(manifest) == digest(operation["manifest"])
        and digest(candidate) == digest(fee["candidate"]) and claim["candidate_hash"] == digest(fee["candidate"]), "ASSET_DESIGN_MANIFEST_UNAVAILABLE")
    product_assets.image_jobs(scope["asset_input"], manifest, expected_manifest_hash=manifest["manifest_hash"])
    proof = {"schema_version": 1, "kind": "asset_design_manifest_proof_v1", "task_id": task_id, "operation_id": operation_id,
        "scope_id": scope["id"], "policy_hash": scope["policy"]["policy_hash"], "budget_anchor_hash": scope["anchor"]["budget_anchor_hash"],
        "asset_input_hash": digest(scope["asset_input"]), "handoff_hash": scope["handoff"]["handoff_hash"], "provenance_hash": scope["handoff"]["provenance_hash"],
        "descriptor_hash": operation["descriptor"]["descriptor_hash"], "raw_hash": digest(operation["raw"]), "receipt_hash": digest(receipt),
        "fee_claim_id": operation["fee_id"], "fee_candidate_hash": digest(fee["candidate"]), "manifest_hash": manifest["manifest_hash"]}
    return {"manifest": copy.deepcopy(manifest), "proof": {**proof, "proof_hash": digest(proof)}}


def projection(payload, task_id=None):
    scopes = [scope for scope in reserved_scopes(payload) if task_id is None or scope["task_id"] == task_id]
    selected = {scope["id"] for scope in scopes}
    operations = [op for op in records(payload, "operation") if op["scope_id"] in selected]
    fees = [fee for fee in records(payload, "fee") if fee["scope_id"] in selected]
    extra = 0
    for operation in operations:
        local = (operation["receipt"] or {}).get("local_units") or 0
        platform = payload["asset_design"]["fee:" + operation["fee_id"]]["candidate"]["platform_fee_units"] if operation["fee_id"] else 0
        extra += max(0, max(local, platform) - 1000000)
    return {"schema_version": 1, "status": "not_authorized" if not scopes else "halted" if any(own_halted(payload, scope["account_id"]) for scope in scopes) else "configured",
        "scope_count": len(scopes), "paid_call_count": len(operations), "reserved_risk_cny": money(1000000 * len(scopes)),
        "extra_exposure_cny": money(extra), "retained_exposure_cny": money(1000000 * len(scopes) + extra),
        "platform_observed_cny": money(sum(fee["candidate"]["platform_fee_units"] for fee in fees)) if fees else None,
        "risk_released": False, "provider_bill_confirmed": False, "quality_status": "not_reviewed", "allowed_actions": [],
        "operations": [{"id": op["id"], "status": op["status"], "fee_observed": op["fee_id"] is not None,
            "manifest_ready": op["status"] == "manifest_ready", "manifest_hash": op["manifest"]["manifest_hash"] if op["status"] == "manifest_ready" else None} for op in operations]}
