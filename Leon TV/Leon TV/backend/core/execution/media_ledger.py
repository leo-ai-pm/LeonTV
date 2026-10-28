"""Optional media records in the original execution journal; no second ledger."""
import base64
import copy
import hashlib
from types import SimpleNamespace

from core.creator.store import ID_RE, KEY_RE, CreatorError, digest
from core.execution.fees import FrozenFeeSource, operation_basis, parse_export
from core.execution.image_comparison_schema import FIELDS as COMPARISON_FIELDS
from core.execution.image_fees import candidate_for
from core.execution.image_policy import FrozenImageRequest, ImageToolPolicy, MediaBudgetAnchor
from core.execution.image_receipts import decode_query, decode_submit, verify_delivery
from core.execution.policy import money, units
from core.execution.receipts import timestamp
from core.execution.reference_derivation import (
    checked_profile,
    encoder_identity,
    references_from_proofs,
)
from core.execution.task_image_fees import candidate_for_task, claim_keys
from core.planner.product_assets import image_jobs
from core.planner.review import json_copy

CORE = ("tasks", "quotes", "grants", "operations", "keys", "accounts")
FIELDS = {
    "scope": {"account_id", "account_hash", "ordinal", "policy", "anchor", "asset_input", "manifest", "handoff", "fee_source"},
    "quote": {"disclosure", "disclosure_hash"},
    "grant": {"quote_id", "disclosure_hash", "revoked_at"},
    "operation": {"descriptor", "grant_id", "quote_id", "shot_id", "submission_index", "status", "capture_started_at", "raw", "receipt", "fee_id", "delivery_id"},
    "query": {"descriptor", "operation_id", "query_index", "status", "capture_started_at", "raw", "receipt"},
    "fee": {"operation_id", "candidate", "export_base64"},
    "claim": {"owner", "operation_id", "evidence_hash", "export_base64", "fee_source"},
    "delivery": {"operation_id", "query_id", "delivery", "bytes_base64"},
    "reference_derivation": {"operation_id", "proof"},
    "code_continuation_quote": {"disclosure", "disclosure_hash"},
    "code_continuation_activation": {"quote_id", "disclosure_hash", "previous_activation_id", "activation_sequence", "consent"},
    "validity_continuation_quote": {"proposal", "parent_evidence_ref", "dispatch_ready", "runtime_contract_pending"},
    "validity_continuation_intent": {"quote_id", "proposal_hash", "consent", "additional_risk_cny", "additional_paid_calls", "dispatch_ready", "runtime_contract_pending"},
    "validity_continuation_activation": {"quote_id", "intent_id", "proposal_hash", "consent", "parent_receipt", "dispatch_ready", "runtime_contract_pending"},
    "runtime_continuation_quote": {"disclosure", "disclosure_hash"},
    "runtime_continuation_activation": {"quote_id", "disclosure_hash", "previous_activation_id", "activation_sequence", "consent"},
    "image_observation_quote": {"disclosure", "disclosure_hash"},
    "image_observation_activation": {"source_operation_id", "quote_id", "disclosure_hash", "previous_activation_id", "activation_sequence", "consent"},
    "key": {"verb", "request", "request_hash", "resource_key"},
}
FIELDS.update(COMPARISON_FIELDS)
COMMON = {"schema_version", "kind", "id", "scope_id", "task_id", "created_at"}
STATES = {"reserved", "submitting", "unknown", "accepted", "rejected", "pending", "completed", "failed"}


def require(value, code="CORRUPT_STORAGE"):
    if not value:
        raise CreatorError(code, "Media execution evidence is unavailable or inconsistent", 409)


def records(payload, kind):
    return [record for record in payload.get("media", {}).values() if record["kind"] == kind]


def native_context(payload, task_id, *, historical=False):
    from core.execution.asset_handoff import _prepare
    core = {name: copy.deepcopy(payload[name]) for name in CORE}
    if historical:
        # Other tasks may later settle or overrun. They cannot invalidate this
        # task's already accepted immutable source/plan/reviewer receipts.
        for name in ("tasks", "quotes", "grants", "operations", "keys"):
            core[name] = {key: record for key, record in core[name].items()
                          if (key == task_id if name == "tasks" else record["task_id"] == task_id)}
    return _prepare(SimpleNamespace(store=SimpleNamespace(read_only=lambda: core)), task_id)


def _stable_handoff(value):
    value = copy.deepcopy(value)
    value.pop("handoff_hash")
    value.pop("execution_revision")
    value.pop("provenance_hash")
    value["inherited_budget"].pop("execution_risk_cny")
    value["inherited_budget"].pop("account_call_count")
    return value


def disclosure_for(scope):
    result = {"scope": "accepted_plan_image_tools_v1", "scope_id": scope["id"], "account_hash": scope["account_hash"],
        "policy": copy.deepcopy(scope["policy"]), "budget_anchor": copy.deepcopy(scope["anchor"]),
        "handoff_hash": scope["handoff"]["handoff_hash"], "provenance_hash": scope["handoff"]["provenance_hash"],
        "asset_input_hash": digest(scope["asset_input"]), "manifest_hash": scope["manifest"]["manifest_hash"],
        "fee_source_hash": digest(scope["fee_source"]), "data_categories": ["accepted_source_and_plan", "compiled_image_prompt", "internal_predecessor_images"],
        "risk_basis": "empirical_not_hard_billing_bound", "risk_released_after_fee": False, "grant_starts_generation": False}
    if "reference_profile" in scope:
        result["reference_profile"] = copy.deepcopy(scope["reference_profile"])
    if "manifest_origin" in scope:
        result["manifest_origin"] = copy.deepcopy(scope["manifest_origin"])
    return result


def reference_sources(payload, scope, shot_id):
    """Original same-scope delivery bytes, never an Agent-supplied path or URL."""
    job = next(job for job in image_jobs(scope["asset_input"], scope["manifest"]) if job["shot_id"] == shot_id)
    result = []
    for previous_shot in job["reference_shot_ids"]:
        matches = [op for op in records(payload, "operation") if op["scope_id"] == scope["id"] and op["shot_id"] == previous_shot and op["delivery_id"] is not None]
        require(len(matches) == 1, "MEDIA_REFERENCE_REQUIRED")
        operation = matches[0]
        delivery = payload["media"]["delivery:" + operation["delivery_id"]]
        result.append({"operation_id": operation["id"], "query_id": delivery["query_id"], "delivery_id": delivery["id"],
            "shot_id": previous_shot, "first_frame_version": 1, "delivery": copy.deepcopy(delivery["delivery"]),
            "bytes_base64": delivery["bytes_base64"]})
    return result


def internal_references(payload, scope, shot_id):
    job = next(job for job in image_jobs(scope["asset_input"], scope["manifest"]) if job["shot_id"] == shot_id)
    result = []
    for previous_shot in job["reference_shot_ids"]:
        matches = [op for op in records(payload, "operation") if op["scope_id"] == scope["id"] and op["shot_id"] == previous_shot and op["delivery_id"] is not None]
        require(len(matches) == 1, "MEDIA_REFERENCE_REQUIRED")
        delivery = payload["media"]["delivery:" + matches[0]["delivery_id"]]
        format_name = delivery["delivery"]["format"]
        require(format_name in {"PNG", "JPEG"}, "MEDIA_REFERENCE_FORMAT_REQUIRED")
        raw = base64.b64decode(delivery["bytes_base64"], validate=True)
        result.append({"shot_id": previous_shot, "first_frame_version": 1,
            "mime_type": "image/png" if format_name == "PNG" else "image/jpeg", "bytes_base64": delivery["bytes_base64"],
            "sha256": hashlib.sha256(raw).hexdigest()})
    return result


def operation_references(payload, scope, operation, *, source_payload=None, reencode=False):
    if "reference_profile" not in scope:
        return internal_references(payload if source_payload is None else source_payload, scope, operation["shot_id"])
    require(operation["reference_profile_hash"] == scope["reference_profile"]["profile_hash"])
    entries = operation["reference_derivations"]
    require(type(entries) is list and all(type(entry) is dict and set(entry) == {"id", "derivation_hash"} for entry in entries))
    require(len({entry["id"] for entry in entries}) == len(entries))
    proofs = []
    for entry in entries:
        record = payload["media"]["reference_derivation:" + entry["id"]]
        require(record["operation_id"] == operation["id"] and record["proof"]["derivation_hash"] == entry["derivation_hash"])
        proofs.append(record["proof"])
    source_payload = payload if source_payload is None else source_payload
    job = operation["descriptor"]["job"]
    return references_from_proofs(profile=scope["reference_profile"], sources=reference_sources(source_payload, scope, operation["shot_id"]),
        target_job=job, policy=scope["policy"], scope_id=scope["id"], task_id=scope["task_id"], operation_id=operation["id"],
        proofs=proofs, reencode=reencode)


def _record_fields(record, media):
    """Explicit optional profiles; old exact field sets remain unchanged."""
    fields = COMMON | FIELDS[record["kind"]]
    if record["kind"] == "scope" and "reference_profile" in record:
        fields = fields | {"reference_profile"}
    if record["kind"] == "scope" and "manifest_origin" in record:
        fields = fields | {"manifest_origin"}
    if record["kind"] == "operation" and "reference_profile" in media.get("scope:" + str(record.get("scope_id")), {}):
        fields = fields | {"reference_profile_hash", "reference_derivations"}
    if record["kind"] in {"fee", "image_comparison_fee"} and record.get("candidate", {}).get("kind") == "image_task_fee_candidate_v1":
        fields = fields | {"task_fee_source", "account_export_base64"}
    return fields


def account_halted(payload, account_id):
    from core.execution.asset_design_ledger import own_halted
    if own_halted(payload, account_id):
        return True
    if any(op["account_id"] == account_id and op.get("empirical", {}).get("fee_status") == "overrun" for op in payload["operations"].values()):
        return True
    scopes = {scope["id"] for scope in records(payload, "scope") if scope["account_id"] == account_id}
    return any(fee["scope_id"] in scopes and fee["candidate"]["overrun"] for fee in records(payload, "fee") + records(payload, "image_comparison_fee"))


def has_pending_unknown(payload, account_id, exclude_operation_id=None):
    from core.execution.asset_design_ledger import own_unknown
    if own_unknown(payload, account_id, exclude_operation_id):
        return True
    scopes = {scope["id"] for scope in records(payload, "scope") if scope["account_id"] == account_id}
    return any(op["scope_id"] in scopes and op["id"] != exclude_operation_id and op["status"] in {"submitting", "unknown"}
               for op in records(payload, "operation") + records(payload, "image_comparison_operation") + records(payload, "image_comparison_query"))


def has_pending_fees(payload, account_id, exclude_operation_id=None):
    from core.execution.asset_design_ledger import own_pending_fees
    if own_pending_fees(payload, account_id, exclude_operation_id):
        return True
    scopes = {scope["id"] for scope in records(payload, "scope") if scope["account_id"] == account_id}
    return any(op["scope_id"] in scopes and op["id"] != exclude_operation_id and op["fee_id"] is None
               for op in records(payload, "operation") + records(payload, "image_comparison_operation"))


def fee_claimed(payload, key, operation_id=None):
    from core.execution.asset_design_ledger import own_fee_claimed
    if own_fee_claimed(payload, key, operation_id):
        return True
    for operation in payload["operations"].values():
        evidence = operation.get("empirical", {}).get("fee_evidence")
        if evidence and evidence["platform_log_id"] == key and operation["id"] != operation_id:
            return True
    return (any(claim["id"] == key and claim["operation_id"] != operation_id for claim in records(payload, "claim"))
            or any(key in claim_keys(fee["candidate"]) and fee["operation_id"] != operation_id
                   for fee in records(payload, "fee") + records(payload, "image_comparison_fee")))


def candidate_claimed(payload, candidate, operation_id=None):
    """A native task and its actual POST ID identify one fee owner."""
    return any(fee_claimed(payload, key, operation_id) for key in claim_keys(candidate))


def unresolved_text_claims(payload):
    bridged = {claim["operation_id"] for claim in records(payload, "claim") if claim["owner"] == "text"}
    return [op["id"] for op in payload["operations"].values()
            if op.get("empirical", {}).get("fee_evidence") and op["id"] not in bridged
            and not op["empirical"]["fee_evidence"]["platform_log_id"].startswith("tudou-request-sha256:")]


def text_claim_key(payload, operation_id, export_bytes, fee_source):
    """Resolve only the exact original export; never infer a log ID from text IDs."""
    operation = payload["operations"][operation_id]
    policy = payload["quotes"][operation["quote_id"]]["policy"]
    _, receipt, _, issued, received, _ = operation_basis(operation, policy)
    evidence = operation["empirical"]["fee_evidence"]
    require(evidence is not None and hashlib.sha256(export_bytes).hexdigest() == evidence["source_export_hash"])
    source = FrozenFeeSource.model_validate(fee_source)
    rows = parse_export(export_bytes)
    matches = [(key, row) for key, row in rows.items() if row["type"] == 2 and row["token_name"] == source.token_name
               and row["group"] == source.group and row["model_name"] == policy["model"]
               and row["prompt_tokens"] == receipt["input_tokens"] and row["completion_tokens"] == receipt["output_tokens"]
               and issued - 5 <= row["created_at"] <= received + 5]
    require(len(matches) == 1)
    key, row = matches[0]
    require(units(evidence["platform_fee_cny"]) == row["quota"] * 2)
    if evidence["platform_log_id"].startswith("tudou-request-sha256:"):
        require(key == evidence["platform_log_id"])
    return key


def _validate(payload, previous, *, historical=False):
    media = payload.get("media", {})
    require(type(media) is dict)
    json_copy(media, limit=48 * 1048576)
    for key, record in media.items():
        require(type(record) is dict and record.get("kind") in FIELDS)
        require(set(record) == _record_fields(record, media))
        require(type(record["schema_version"]) is int and record["schema_version"] == 1)
        require(key == record["kind"] + ":" + record["id"] and record["task_id"] in payload["tasks"])
        timestamp(record["created_at"])
        if record["kind"] not in {"claim", "fee", "key"}:
            require(type(record["id"]) is str and ID_RE.fullmatch(record["id"]) is not None)
        if record["kind"] in {"operation", "query", "image_comparison_operation", "image_comparison_query"}:
            require(record["id"] not in payload["operations"])
    capture_ids = [record["id"] for record in media.values() if record["kind"] in {"operation", "query", "image_comparison_operation", "image_comparison_query"}]
    require(len(capture_ids) == len(set(capture_ids)))
    scopes = sorted(records(payload, "scope"), key=lambda scope: scope["ordinal"])
    last_risk, last_calls, allocations = {}, {}, set()
    for ordinal, scope in enumerate(scopes, 1):
        require(scope["scope_id"] == scope["id"] and type(scope["ordinal"]) is int and scope["ordinal"] == ordinal)
        identity = {"task_id": scope["task_id"], "policy_hash": scope["policy"]["policy_hash"], "budget_anchor_hash": scope["anchor"]["budget_anchor_hash"],
            "manifest_hash": scope["manifest"]["manifest_hash"], "handoff_hash": scope["handoff"]["handoff_hash"]}
        if "reference_profile" in scope:
            require(digest(checked_profile(scope["reference_profile"])) == digest(scope["reference_profile"]))
            identity["reference_profile_hash"] = scope["reference_profile"]["profile_hash"]
        if "manifest_origin" in scope:
            from core.execution.asset_media_handoff import check_manifest_origin
            check_manifest_origin(payload, scope)
            identity["manifest_origin_hash"] = digest(scope["manifest_origin"])
        require(scope["id"] == digest(identity)[:32])
        account = payload["accounts"][scope["account_id"]]
        require(account.get("accounting_kind") == "empirical_risk_v1" and scope["account_hash"] == digest(account))
        anchor = MediaBudgetAnchor.model_validate({key: value for key, value in scope["anchor"].items() if key != "budget_anchor_hash"}).frozen()
        policy = ImageToolPolicy.model_validate({key: value for key, value in scope["policy"].items() if key != "policy_hash"}).frozen()
        require(digest(anchor) == digest(scope["anchor"]) and digest(policy) == digest(scope["policy"]))
        require(anchor["account_scope_id"] == account["id"] and anchor["parent_authorization_hash"] == account["anchor"]["parent_authorization_hash"])
        require(units(anchor["opening_risk_cny"]) >= last_risk.get(account["id"], account["limit_units"]))
        require(anchor["opening_paid_call_count"] >= last_calls.get(account["id"], account["max_calls"]))
        require(anchor["scope_max_calls"] == policy["max_submissions"] and units(anchor["scope_risk_cny"]) == units(policy["per_submission_risk_cny"]) * policy["max_submissions"])
        require(anchor["parent_allocation_hash"] not in allocations)
        allocations.add(anchor["parent_allocation_hash"])
        last_risk[account["id"]], last_calls[account["id"]] = units(anchor["account_limit_cny"]), anchor["account_max_calls"]
        context, handoff = native_context(payload, scope["task_id"], historical=True)
        require(digest(scope["asset_input"]) == digest(context) and digest(_stable_handoff(scope["handoff"])) == digest(_stable_handoff(handoff)))
        require(scope["handoff"]["handoff_hash"] == digest({key: value for key, value in scope["handoff"].items() if key != "handoff_hash"}))
        require(policy["validation_only"] is (scope["handoff"]["evidence_kind"] == "synthetic"))
        require(scope["handoff"]["evidence_kind"] in {"synthetic", "empirical_receipts"})
        image_jobs(context, scope["manifest"], expected_manifest_hash=scope["manifest"]["manifest_hash"])
        require(FrozenFeeSource.model_validate(scope["fee_source"]).account_namespace == account["id"])
    used_requests = {}
    for op in payload["operations"].values():
        evidence = op.get("empirical", {}).get("fee_evidence")
        if evidence and evidence["platform_log_id"].startswith("tudou-request-sha256:"):
            require(evidence["platform_log_id"] not in used_requests)
            used_requests[evidence["platform_log_id"]] = op["id"]
    for record in media.values():
        kind = record["kind"]
        if kind == "scope":
            continue
        if kind == "claim" and record["owner"] == "text":
            require(record["scope_id"] is None)
            original = payload["operations"][record["operation_id"]]
            require(original["task_id"] == record["task_id"])
            require(record["evidence_hash"] == digest(original["empirical"]["fee_evidence"]))
            claim_key = text_claim_key(payload, original["id"], base64.b64decode(record["export_base64"], validate=True), record["fee_source"])
            require(claim_key == record["id"])
            require(claim_key not in used_requests or used_requests[claim_key] == original["id"])
            used_requests[claim_key] = original["id"]
            continue
        scope = media["scope:" + record["scope_id"]]
        require(record["task_id"] == scope["task_id"])
        if kind in COMPARISON_FIELDS:
            from core.execution.image_comparison import validate_record
            validate_record(payload, record)
            continue
        if kind in {"code_continuation_quote", "code_continuation_activation"}:
            from core.execution.image_code_continuation import validate_record
            validate_record(payload, record)
            continue
        if kind in {"validity_continuation_quote", "validity_continuation_intent", "validity_continuation_activation"}:
            from core.execution.image_validity_continuation import validate_record
            validate_record(payload, record)
            continue
        if kind in {"runtime_continuation_quote", "runtime_continuation_activation"}:
            from core.execution.image_runtime_continuation import validate_record
            validate_record(payload, record)
            continue
        if kind in {"image_observation_quote", "image_observation_activation"}:
            from core.execution.image_observation_continuation import validate_record
            validate_record(payload, record)
            continue
        if kind == "quote":
            require(record["disclosure_hash"] == digest({key: value for key, value in record.items() if key != "disclosure_hash"}))
            require(digest(record["disclosure"]) == digest(disclosure_for(scope)))
            require(timestamp(scope["created_at"]) <= timestamp(record["created_at"]) < timestamp(scope["policy"]["valid_until"]))
        elif kind == "grant":
            quote = media["quote:" + record["quote_id"]]
            require(quote["scope_id"] == record["scope_id"] and record["disclosure_hash"] == quote["disclosure_hash"])
            require(timestamp(quote["created_at"]) <= timestamp(record["created_at"]) < timestamp(scope["policy"]["valid_until"]))
            if record["revoked_at"] is not None:
                require(timestamp(record["revoked_at"]) >= timestamp(record["created_at"]))
        elif kind in {"operation", "query"}:
            descriptor = FrozenImageRequest.from_frozen(record["descriptor"])
            data, permit = descriptor.frozen(), descriptor.frozen()["permit"]
            require(record["id"] == permit["operation_id"] and record["task_id"] == permit["task_id"])
            if data["kind"] == "controlled_image_observation_http_v1":
                from core.execution.image_observation_continuation import ACTIVATION, authority_for
                authority = authority_for(payload, permit["observation_activation_id"])
                require(kind == "query" and digest(data["observation_authority"]) == digest(authority)
                    and authority["scope_id"] == scope["id"] and authority["scope_hash"] == digest(scope))
                require(digest(data["policy"]) == digest(authority["effective_policy"])
                    and digest(data["budget_anchor"]) == digest(scope["anchor"]))
                activation = media[ACTIVATION + ":" + authority["activation_id"]]
                require(timestamp(activation["created_at"]) <= timestamp(permit["issued_at"]))
            elif data["kind"] in {"controlled_image_http_v2", "controlled_image_http_v3"}:
                from core.execution.image_runtime_continuation import authority_for
                authority = authority_for(payload, permit["runtime_activation_id"])
                require(digest(data["continuation_authority"]) == digest(authority)
                        and authority["scope_id"] == scope["id"] and authority["scope_hash"] == digest(scope))
                require(digest(data["policy"]) == digest(authority["effective_policy"])
                        and digest(data["budget_anchor"]) == digest(scope["anchor"]))
                activation = media["runtime_continuation_activation:" + authority["activation_id"]]
                require(timestamp(activation["created_at"]) <= timestamp(permit["issued_at"]))
            else:
                require(digest(data["policy"]) == digest(scope["policy"]) and digest(data["budget_anchor"]) == digest(scope["anchor"]))
            require(digest(data["asset_input"]) == digest(scope["asset_input"]) and digest(data["manifest"]) == digest(scope["manifest"]))
            require(permit["handoff_hash"] == scope["handoff"]["handoff_hash"] and permit["provenance_hash"] == scope["handoff"]["provenance_hash"])
            grant = media["grant:" + permit["grant_id"]]
            require(grant["scope_id"] == scope["id"] and permit["disclosure_hash"] == grant["disclosure_hash"])
            require(timestamp(grant["created_at"]) <= timestamp(permit["issued_at"]) <= timestamp(record["created_at"]))
            require(timestamp(record["created_at"]) <= timestamp(permit["deadline_at"]))
            require(record["status"] in STATES)
            require(record["capture_started_at"] is not None or record["status"] == "reserved")
            if record["capture_started_at"] is not None:
                require(timestamp(record["created_at"]) <= timestamp(record["capture_started_at"]) <= timestamp(permit["deadline_at"]))
            if record["raw"] is not None:
                receipt = decode_submit(descriptor, record["raw"]) if kind == "operation" else decode_query(descriptor, record["raw"])
                require(digest(receipt) == digest(record["receipt"]))
                expected = receipt["classification"]
                require(record["status"] == ("unknown" if expected == "unconfirmed" else expected))
            else:
                require(record["receipt"] is None and record["status"] in {"reserved", "submitting", "unknown"})
            if kind == "operation":
                require(data["method"] == "POST" and record["grant_id"] == grant["id"] and record["quote_id"] == grant["quote_id"])
                require(record["shot_id"] == permit["shot_id"] and record["submission_index"] == permit["scope_submission_index"])
                require(digest(data["references"]) == digest(operation_references(payload, scope, record)))
                sources = reference_sources(payload, scope, record["shot_id"])
                if data["kind"] in {"controlled_image_http_v2", "controlled_image_http_v3"}:
                    from core.execution.image_continuation_policy import bind_reference_sources
                    require(digest(data["reference_bindings"]) == digest(bind_reference_sources(sources, data["references"])))
                for source in sources:
                    preceding = media["operation:" + source["operation_id"]]
                    require(preceding["submission_index"] < record["submission_index"])
                if record["fee_id"] is not None:
                    require(media["fee:" + record["fee_id"]]["operation_id"] == record["id"])
                if record["delivery_id"] is not None:
                    require(media["delivery:" + record["delivery_id"]]["operation_id"] == record["id"])
            else:
                original = media["operation:" + record["operation_id"]]
                require(data["method"] == "GET" and record["query_index"] == data["query_index"])
                require(digest(data["parent_submit"]) == digest(original["descriptor"]) and digest(data["submit_raw"]) == digest(original["raw"]))
        elif kind == "fee":
            original = media["operation:" + record["operation_id"]]
            descriptor = FrozenImageRequest.from_frozen(original["descriptor"])
            export_bytes = base64.b64decode(record["export_base64"], validate=True)
            if record["candidate"].get("kind") == "image_task_fee_candidate_v1":
                candidate = candidate_for_task(descriptor, original["raw"], export_bytes,
                    base64.b64decode(record["account_export_base64"], validate=True), scope["fee_source"],
                    record["task_fee_source"], observed_at=record["candidate"]["observed_at"])
            else:
                candidate = candidate_for(descriptor, original["raw"], export_bytes,
                    scope["fee_source"], observed_at=record["candidate"]["observed_at"])
            require(digest(candidate) == digest(record["candidate"]) and record["id"] == candidate["platform_log_id"])
            require(original["fee_id"] == record["id"])
            require("claim:" + record["id"] in media and media["claim:" + record["id"]]["owner"] == "media")
            require(not candidate_claimed(payload, candidate, original["id"]))
        elif kind == "claim":
            require(record["owner"] == "media" and digest(record["fee_source"]) == digest(scope["fee_source"]))
            fee = media["fee:" + record["id"]]
            require(fee["operation_id"] == record["operation_id"] and record["evidence_hash"] == digest(fee["candidate"]))
            require(record["export_base64"] == fee["export_base64"])
            require(record["id"] not in used_requests)
            used_requests[record["id"]] = record["operation_id"]
        elif kind == "delivery":
            raw_image = base64.b64decode(record["bytes_base64"], validate=True)
            require(len(raw_image) <= 2 * 1048576)
            query = media["query:" + record["query_id"]]
            require(query["operation_id"] == record["operation_id"])
            result = verify_delivery(FrozenImageRequest.from_frozen(query["descriptor"]), query["raw"], raw_image)
            require(digest(result) == digest(record["delivery"]))
        elif kind == "reference_derivation":
            require("reference_profile" in scope)
            operation = media["operation:" + record["operation_id"]]
            require(operation["scope_id"] == scope["id"] and record["proof"]["derivation_id"] == record["id"])
            require(sum(entry == {"id": record["id"], "derivation_hash": record["proof"]["derivation_hash"]}
                        for entry in operation["reference_derivations"]) == 1)
        elif kind == "key":
            require(record["request_hash"] == digest(record["request"]) and record["resource_key"] in media)
            verb = record["verb"]
            target = media[record["resource_key"]]
            require(verb in {"quote", "grant", "submit", "query"})
            prefix, _, token = record["id"].partition(":")
            require(prefix == verb and KEY_RE.fullmatch(token) is not None)
            require(target["kind"] == {"quote": "quote", "grant": "grant", "submit": "operation", "query": "query"}[verb])
            require(record["task_id"] == target["task_id"] and record["scope_id"] == target["scope_id"])
            request = record["request"]
            expected = {"task_id": target["task_id"]}
            if verb == "grant":
                expected.update(quote_id=target["quote_id"], disclosure_hash=target["disclosure_hash"], consent=True)
                require(type(request.get("consent")) is bool)
            elif verb == "submit":
                expected.update(grant_id=target["grant_id"], shot_id=target["shot_id"], references=request.get("references"))
                require(type(request.get("references")) is list)
                require(request["references"] == [] or digest(request["references"]) == digest(target["descriptor"]["references"]))
            elif verb == "query":
                expected.update(operation_id=target["operation_id"])
            if verb in {"submit", "query"} and target["descriptor"]["kind"] in {"controlled_image_http_v2", "controlled_image_http_v3"}:
                expected["runtime_activation_id"] = target["descriptor"]["permit"]["runtime_activation_id"]
            if verb == "query" and target["descriptor"]["kind"] == "controlled_image_observation_http_v1":
                expected["observation_activation_id"] = target["descriptor"]["permit"]["observation_activation_id"]
            require(digest(request) == digest(expected))
    for record in media.values():
        if record["kind"] in {"quote", "grant", "operation", "query"}:
            target_key = record["kind"] + ":" + record["id"]
            require(sum(key["resource_key"] == target_key for key in records(payload, "key")) == 1)
    for scope in scopes:
        operations = sorted((op for op in records(payload, "operation") if op["scope_id"] == scope["id"]), key=lambda op: op["submission_index"])
        require([op["submission_index"] for op in operations] == list(range(1, len(operations) + 1)))
        continued = [op for op in operations if op["descriptor"]["kind"] in {"controlled_image_http_v2", "controlled_image_http_v3"}]
        require(len(operations) <= scope["policy"]["max_submissions"] + bool(continued))
        if continued:
            for shot_id in {op["shot_id"] for op in operations}:
                same_shot = [op for op in operations if op["shot_id"] == shot_id]
                if len(same_shot) > 1:
                    require(len(same_shot) == 2)
                    old, replacement = same_shot
                    require(old["status"] == "unknown" and replacement["descriptor"]["kind"] in {"controlled_image_http_v2", "controlled_image_http_v3"})
                    require(replacement["descriptor"]["permit"]["replacement_of"] == old["id"]
                            and replacement["descriptor"]["permit"]["attempt_version"] == 2)
        else:
            require(len({op["shot_id"] for op in operations}) == len(operations))
        for operation in operations:
            queries = sorted((q for q in records(payload, "query") if q["operation_id"] == operation["id"]), key=lambda q: q["query_index"])
            require([q["query_index"] for q in queries] == list(range(1, len(queries) + 1)))
            require(len(queries) <= scope["policy"]["max_queries"])
    if previous is not None:
        old = previous.get("media", {})
        require(set(old) <= set(media))
        for key, before in old.items():
            after = media[key]
            if before is after or digest(before) == digest(after):
                continue
            kind = before["kind"]
            if kind in COMPARISON_FIELDS:
                continue
            mutable = {"status", "capture_started_at", "raw", "receipt", "fee_id", "delivery_id"} if kind == "operation" else {"status", "capture_started_at", "raw", "receipt"} if kind == "query" else {"revoked_at"} if kind == "grant" else set()
            require(digest({k: v for k, v in before.items() if k not in mutable}) == digest({k: v for k, v in after.items() if k not in mutable}))
            for field in mutable - {"status"}:
                require(before.get(field) is None or digest(before[field]) == digest(after[field]))
            if kind in {"operation", "query"}:
                allowed = {"reserved": {"reserved", "submitting"}, "submitting": STATES - {"reserved"}, "unknown": STATES - {"reserved", "submitting"}}
                require(after["status"] in allowed.get(before["status"], {before["status"]}))
        new_records = [record for key, record in media.items() if key not in old]
        for record in new_records:
            kind = record["kind"]
            if kind in {"quote", "grant", "operation", "query"}:
                target_key = kind + ":" + record["id"]
                require(sum(key["kind"] == "key" and key["resource_key"] == target_key for key in new_records) == 1)
            if kind == "scope":
                context, handoff = native_context(previous, record["task_id"])
                require(digest(record["asset_input"]) == digest(context) and digest(record["handoff"]) == digest(handoff))
            elif kind in {"operation", "query"}:
                require(record["status"] == "reserved" and all(record.get(field) is None for field in ("capture_started_at", "raw", "receipt", "fee_id", "delivery_id")))
                scope = old["scope:" + record["scope_id"]]
                grant_id = record["descriptor"]["permit"]["grant_id"]
                grant = old["grant:" + grant_id]
                require(grant["revoked_at"] is None and "quote:" + grant["quote_id"] in old)
                require(media["grant:" + grant_id]["revoked_at"] is None)
                if kind == "operation":
                    require(not unresolved_text_claims(previous))
                    prior = [op for op in records(previous, "operation") if old["scope:" + op["scope_id"]]["account_id"] == scope["account_id"]]
                    if record["descriptor"]["kind"] in {"controlled_image_http_v2", "controlled_image_http_v3"}:
                        from core.execution.media_continuation_gate import candidate_gate
                        candidate_gate(previous, record)
                        candidate_gate(payload, record, exclude_candidate=True)
                    else:
                        require(all(op["fee_id"] is not None for op in prior))
                    reencode = not historical or (scope.get("reference_profile", {}).get("encoder") == encoder_identity())
                    require(digest(record["descriptor"]["references"]) == digest(operation_references(payload, scope, record,
                        source_payload=previous, reencode=reencode)))
                else:
                    require(old["operation:" + record["operation_id"]]["status"] == "accepted")
                    if record["descriptor"]["kind"] == "controlled_image_observation_http_v1":
                        from core.execution.image_observation_continuation import candidate_gate
                        candidate_gate(previous, record)
                        candidate_gate(payload, record, exclude_candidate=True)
            elif kind == "grant":
                require("quote:" + record["quote_id"] in old and record["revoked_at"] is None)
                require(not unresolved_text_claims(previous))
            elif kind == "fee":
                require(old["operation:" + record["operation_id"]]["raw"] is not None)
            elif kind == "delivery":
                require(old["query:" + record["query_id"]]["raw"] is not None)
            elif kind == "reference_derivation":
                require("operation:" + record["operation_id"] not in old)
                require(any(new["kind"] == "operation" and new["id"] == record["operation_id"] for new in new_records))
                require("delivery:" + record["proof"]["source_delivery_id"] in old)
        for account_id in payload["accounts"]:
            halted = account_halted(previous, account_id) or account_halted(payload, account_id)
            unknown = has_pending_unknown(previous, account_id) or has_pending_unknown(payload, account_id)
            new_text = [op for op_id, op in payload["operations"].items() if op_id not in previous["operations"] and op["account_id"] == account_id]
            new_images = [op for op in new_records if op["kind"] == "operation" and media["scope:" + op["scope_id"]]["account_id"] == account_id]
            require(len(new_text) + len(new_images) <= 1)
            if new_text:
                require(all(op["fee_id"] is not None for op in records(previous, "operation")
                            if old["scope:" + op["scope_id"]]["account_id"] == account_id))
            if new_images:
                require(not any(op["account_id"] == account_id and op["status"] != "settled" for op in previous["operations"].values()))
            if halted or unknown:
                require(all(op_id in previous["operations"] for op_id, op in payload["operations"].items() if op["account_id"] == account_id))
                require(not halted or not new_images)
                require(all(op["descriptor"]["kind"] in {"controlled_image_http_v2", "controlled_image_http_v3"} for op in new_images))


def validate(payload, previous=None, *, historical=False):
    try:
        _validate(payload, previous, historical=historical)
        if any(r["kind"] in COMPARISON_FIELDS for r in payload.get("media", {}).values()):
            from core.execution.image_comparison import validate_transition
            validate_transition(payload, previous)
        from core.execution.scope_budget import _validate_fee_owners
        _validate_fee_owners(payload)
        if any(record["kind"] in {"code_continuation_quote", "code_continuation_activation"}
               for record in payload.get("media", {}).values()):
            from core.execution.image_code_continuation import validate_transition
            validate_transition(payload, previous)
        if any(record["kind"] in {"validity_continuation_quote", "validity_continuation_intent", "validity_continuation_activation"}
               for record in payload.get("media", {}).values()):
            from core.execution.image_validity_continuation import validate_transition
            validate_transition(payload, previous)
        if any(record["kind"] in {"runtime_continuation_quote", "runtime_continuation_activation"}
               for record in payload.get("media", {}).values()):
            from core.execution.image_runtime_continuation import validate_transition
            validate_transition(payload, previous)
        if any(record["kind"] in {"image_observation_quote", "image_observation_activation"}
               for record in payload.get("media", {}).values()):
            from core.execution.image_observation_continuation import validate_transition
            validate_transition(payload, previous)
    except Exception:
        raise CreatorError("CORRUPT_STORAGE", "Media execution journal failed integrity checks") from None


def projection(payload, task_id=None):
    scopes = [scope for scope in records(payload, "scope") if task_id is None or scope["task_id"] == task_id]
    selected = {scope["id"] for scope in scopes}
    operations = [op for op in records(payload, "operation") if op["scope_id"] in selected]
    fees = [fee for fee in records(payload, "fee") if fee["scope_id"] in selected]
    extra = sum(max(0, units(fee["candidate"]["platform_fee_cny"]) - units(fee["candidate"]["per_submission_risk_cny"])) for fee in fees)
    result = {"schema_version": 1, "status": "not_authorized" if not scopes else "halted" if any(account_halted(payload, scope["account_id"]) for scope in scopes) else "configured",
        "scope_count": len(scopes), "paid_call_count": len(operations), "query_count": sum(q["scope_id"] in selected for q in records(payload, "query")),
        "reserved_risk_cny": money(sum(units(scope["anchor"]["scope_risk_cny"]) for scope in scopes)),
        "retained_exposure_cny": money(sum(units(scope["anchor"]["scope_risk_cny"]) for scope in scopes) + extra),
        "extra_exposure_cny": money(extra), "reservation_stage": "quote_scope", "fees_settled": False,
        "platform_observed_cny": money(sum(units(fee["candidate"]["platform_fee_cny"]) for fee in fees)) if fees else None,
        "risk_released": False, "provider_bill_confirmed": False, "quality_status": "not_reviewed", "allowed_actions": [],
        "scopes": [{"id": scope["id"], "task_id": scope["task_id"], "opening_risk_cny": scope["anchor"]["opening_risk_cny"],
            "opening_paid_call_count": scope["anchor"]["opening_paid_call_count"], "account_limit_cny": scope["anchor"]["account_limit_cny"],
            "account_max_calls": scope["anchor"]["account_max_calls"], "scope_max_calls": scope["anchor"]["scope_max_calls"]} for scope in scopes],
        "operations": [{"id": op["id"], "shot_id": op["shot_id"], "status": op["status"], "fee_observed": op["fee_id"] is not None,
            "delivered": op["delivery_id"] is not None} for op in operations]}
    extensions = [record for record in records(payload, "validity_continuation_intent") if record["scope_id"] in selected]
    if extensions:
        from core.execution.scope_budget import scope_envelope

        additional = sum(units(record["additional_risk_cny"]) for record in extensions)
        result["reserved_risk_cny"] = money(units(result["reserved_risk_cny"]) + additional)
        result["retained_exposure_cny"] = money(units(result["retained_exposure_cny"]) + additional)
        result["reservation_stage"] = "scope_and_continuation_intent"
        accounts = []
        for account_id in sorted({scope["account_id"] for scope in scopes}):
            envelope = scope_envelope(payload, account_id)
            accounts.append({"account_id": account_id, "retained_risk_cny": money(envelope["risk_units"]),
                "retained_max_calls": envelope["max_calls"]})
        result["continuation"] = {"reservation_intent_count": len(extensions),
            "activation_count": sum(record["scope_id"] in selected for record in records(payload, "validity_continuation_activation")),
            "additional_reserved_risk_cny": money(additional),
            "additional_reserved_call_slots": sum(record["additional_paid_calls"] for record in extensions),
            "accounts": accounts, "dispatch_ready": False, "runtime_contract_pending": True}
    return result
