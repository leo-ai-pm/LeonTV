"""Direct transaction and fully rehashed asset-record attacks are rejected."""
import copy
import uuid

import pytest
from asset_design_fixture import authorize, design_case, fee, reserve
from test_execution_empirical import empirical_env as empirical_env

from core.creator.store import CreatorError, digest
from core.execution.asset_design_ledger import validate, verified_manifest
from core.execution.image_policy import MediaBudgetAnchor


def test_reconstructed_asset_history_rejects_mutated_proofs_keys_and_bypass_birth(empirical_env):
    case = design_case(empirical_env)
    _, grant = authorize(case)
    operation = reserve(case, grant)
    before = case.design.store.read_only()
    scope_key = "scope:" + operation["scope_id"]
    variants = []
    for field, value in (("account_hash", "0" * 64), ("ordinal", True)):
        changed = copy.deepcopy(before)
        changed["asset_design"][scope_key][field] = value
        variants.append(changed)
    for field in ("opening_risk_cny", "opening_paid_call_count"):
        changed = copy.deepcopy(before)
        scope = changed["asset_design"][scope_key]
        anchor = {k: v for k, v in scope["anchor"].items() if k != "budget_anchor_hash"}
        if field == "opening_risk_cny":
            anchor.update(opening_risk_cny="73.37", account_limit_cny="74.37")
        else:
            anchor.update(opening_paid_call_count=45, account_max_calls=46)
        scope["anchor"] = MediaBudgetAnchor.model_validate(anchor).frozen()
        variants.append(changed)
    for field in ("wire", "context_json"):
        changed = copy.deepcopy(before)
        changed["asset_design"]["operation:" + operation["id"]]["descriptor"][field] = "{}"
        variants.append(changed)
    for field, value in (("grant_id", "1" * 32), ("status", "manifest_ready"), ("fee_id", "unproved-fee"), ("raw", {})):
        changed = copy.deepcopy(before)
        changed["asset_design"]["operation:" + operation["id"]][field] = value
        variants.append(changed)
    changed = copy.deepcopy(before)
    changed["asset_design"][scope_key]["asset_input"]["constraints"]["max_images"] = 99
    variants.append(changed)
    changed = copy.deepcopy(before)
    orphan = copy.deepcopy(changed["asset_design"]["grant:" + grant["id"]])
    orphan["id"] = uuid.uuid4().hex
    changed["asset_design"]["grant:" + orphan["id"]] = orphan
    variants.append(changed)
    changed = copy.deepcopy(before)
    key = next(record for record in changed["asset_design"].values() if record["kind"] == "key" and record["verb"] == "grant")
    key["request"]["consent"] = 1
    key["request_hash"] = digest(key["request"])
    variants.append(changed)
    changed = copy.deepcopy(before)
    del changed["asset_design"]["operation:" + operation["id"]]
    variants.append(changed)
    for changed in variants:
        with pytest.raises(CreatorError):
            validate(changed, before)
    assert case.design.store.read_only() == before


def test_new_operation_cannot_be_born_with_received_raw_or_same_frame_new_grant(empirical_env):
    case = design_case(empirical_env)
    _, grant = authorize(case)
    before = case.design.store.read_only()
    operation = reserve(case, grant)
    after = case.design.store.read_only()
    changed = copy.deepcopy(after)
    changed["asset_design"]["operation:" + operation["id"]]["status"] = "submitting"
    changed["asset_design"]["operation:" + operation["id"]]["capture_started_at"] = operation["created_at"]
    with pytest.raises(CreatorError):
        validate(changed, before)
    original = copy.deepcopy(before)
    for key in tuple(original["asset_design"]):
        if original["asset_design"][key]["kind"] == "grant" or original["asset_design"][key]["kind"] == "key" and original["asset_design"][key]["verb"] == "grant":
            del original["asset_design"][key]
    with pytest.raises(CreatorError):
        validate(after, original)


def test_fee_raw_and_manifest_cannot_be_replaced_or_unclaimed(empirical_env):
    case = design_case(empirical_env)
    _, grant = authorize(case)
    operation = reserve(case, grant)
    started = case.design.start(case.task["id"], operation["id"])["operation"]
    result = fee(case, started)["operation"]
    before = case.design.store.read_only()
    for field in ("raw", "receipt", "manifest", "fee_id"):
        changed = copy.deepcopy(before)
        changed["asset_design"]["operation:" + operation["id"]][field] = None
        with pytest.raises(CreatorError):
            validate(changed, before)
    changed = copy.deepcopy(before)
    del changed["asset_design"]["claim:" + result["fee_id"]]
    with pytest.raises(CreatorError):
        validate(changed, before)
    for part in ("descriptor", "fee", "claim", "manifest"):
        changed = copy.deepcopy(before)
        if part == "descriptor":
            changed["asset_design"]["operation:" + operation["id"]]["descriptor"]["wire"] = "{}"
        elif part == "fee":
            candidate = changed["asset_design"]["fee:" + result["fee_id"]]["candidate"]
            candidate["platform_fee_cny"] = "0.000000"
            candidate["candidate_hash"] = digest({k: v for k, v in candidate.items() if k != "candidate_hash"})
            changed["asset_design"]["claim:" + result["fee_id"]]["candidate_hash"] = digest(candidate)
        elif part == "claim":
            changed["asset_design"]["claim:" + result["fee_id"]]["operation_id"] = "f" * 32
        else:
            changed["asset_design"]["operation:" + operation["id"]]["manifest"]["frames"][0]["first_frame_prompt"] = "forged"
        with pytest.raises(CreatorError):
            verified_manifest(changed, case.task["id"], operation["id"])


def test_global_text_fee_request_cannot_be_reclaimed_by_asset_design(empirical_env):
    case = design_case(empirical_env)
    _, grant = authorize(case)
    operation = reserve(case, grant)
    started = case.design.start(case.task["id"], operation["id"])["operation"]
    native_operation_id = next(iter(case.design.store.read_only()["operations"]))
    original = case.design.store.path("state.json").read_bytes()
    with pytest.raises(CreatorError) as error:
        fee(case, started, request_id="text-" + native_operation_id)
    assert error.value.code == "FEE_EVIDENCE_CONFLICT"
    assert case.design.store.path("state.json").read_bytes() == original
