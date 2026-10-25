"""Asset-design native journal/worker/fee integration, completely synthetic."""
import copy
import json

import pytest
from asset_design_fixture import authorize, design_case, fee, posts, reserve
from test_execution_empirical import empirical_env as empirical_env

from core.creator.store import CreatorError, digest
from core.execution.asset_design import AssetDesignExecution
from core.execution.asset_design_ledger import own_fee_claimed, own_halted


def test_default_off_and_quote_grant_do_not_dispatch_or_load_credentials(empirical_env):
    case = design_case(empirical_env)
    calls = []
    disabled = AssetDesignExecution(case.env.runtime, credential_loader=lambda: calls.append(True))
    original = case.design.store.path("state.json").read_bytes()
    assert disabled.get(case.task["id"])["asset_design"]["status"] == "not_authorized"
    with pytest.raises(CreatorError):
        disabled.quote(case.task["id"], key="disabled-asset-quote")
    assert case.design.store.path("state.json").read_bytes() == original and calls == []
    core = copy.deepcopy(case.design.store.read_only())
    quote, grant = authorize(case)
    operation = reserve(case, grant)
    assert posts(case) == [] and operation["status"] == "reserved"
    assert all(digest(case.design.store.read_only()[name]) == digest(value) for name, value in core.items())
    assert quote["disclosure"]["image_submissions"] == 0 and quote["disclosure"]["max_calls"] == 1
    assert quote["disclosure"]["budget_anchor"]["opening_risk_cny"] == "75.37"
    assert quote["disclosure"]["budget_anchor"]["opening_paid_call_count"] == 47


def test_one_fresh_asset_call_requires_fee_before_manifest_and_preserves_original_namespaces(empirical_env):
    case = design_case(empirical_env)
    core = copy.deepcopy(case.design.store.read_only())
    _, grant = authorize(case)
    operation = reserve(case, grant)
    started = case.design.start(case.task["id"], operation["id"])["operation"]
    assert started["status"] == "awaiting_fee" and started["manifest"] is not None and len(posts(case)) == 1
    with pytest.raises(CreatorError):
        case.design.verified_manifest(case.task["id"], operation["id"])
    assert case.design.start(case.task["id"], operation["id"])["replayed"] and len(posts(case)) == 1
    result = fee(case, started)
    assert result["operation"]["status"] == "manifest_ready" and not result["replayed"]
    assert fee(case, started)["replayed"]
    verified = case.design.verified_manifest(case.task["id"], operation["id"])
    assert verified["manifest"] == started["manifest"] and verified["proof"]["manifest_hash"] == started["manifest"]["manifest_hash"]
    assert verified["proof"]["kind"] == "asset_design_manifest_proof_v1"
    payload = case.design.store.read_only()
    assert all(digest(payload[name]) == digest(value) for name, value in core.items())
    assert own_fee_claimed(payload, result["operation"]["fee_id"]) and not own_fee_claimed(payload, result["operation"]["fee_id"], operation["id"])
    public = json.dumps(case.design.get(case.task["id"]))
    assert all(forbidden not in public for forbidden in ("body_base64", "wire", "prompt", "script", "https://", "PRIVATE"))


@pytest.mark.parametrize("fault,status", [("invalid_json", "invalid"), ("invalid_schema", "invalid"), ("length", "truncated")])
def test_invalid_or_truncated_output_stops_but_valid_billing_receipt_is_recorded(empirical_env, fault, status):
    case = design_case(empirical_env, fault=fault)
    _, grant = authorize(case)
    operation = reserve(case, grant)
    result = case.design.start(case.task["id"], operation["id"])["operation"]
    assert result["status"] == status and result["manifest"] is None
    assert fee(case, result)["operation"]["status"] == status
    with pytest.raises(CreatorError):
        case.design.verified_manifest(case.task["id"], operation["id"])
    with pytest.raises(CreatorError):
        case.design.reserve(case.task["id"], grant["id"], key="no-automatic-protocol-repair")
    assert len(posts(case)) == 1


@pytest.mark.parametrize("fault", ["unknown", "html"])
def test_unknown_does_not_retry_after_restart_or_replacement_grant(empirical_env, fault):
    case = design_case(empirical_env, fault=fault)
    quote, grant = authorize(case)
    operation = reserve(case, grant)
    assert case.design.start(case.task["id"], operation["id"])["operation"]["status"] == "unknown"
    case.design.recover(case.task["id"], operation["id"])
    assert case.design.start(case.task["id"], operation["id"])["replayed"]
    replacement = case.design.grant(case.task["id"], quote["id"], disclosure_hash=quote["disclosure_hash"], consent=True,
        key="replacement-asset-grant")["grant"]
    restarted = AssetDesignExecution(case.env.runtime, policy=case.policy, budget_anchor=case.anchor, authority_guard=case.authority,
        fee_source=case.source, receipt_capture=case.settings)
    with pytest.raises(CreatorError):
        restarted.reserve(case.task["id"], replacement["id"], key="replacement-asset-intent")
    assert len(posts(case)) == 1


def test_overrun_fee_is_retained_in_full_and_does_not_publish_manifest(empirical_env):
    case = design_case(empirical_env)
    _, grant = authorize(case)
    operation = reserve(case, grant)
    started = case.design.start(case.task["id"], operation["id"])["operation"]
    result = fee(case, started, quota=750000)
    assert result["operation"]["status"] == "blocked"
    assert own_halted(case.design.store.read_only(), case.anchor.account_scope_id)
    view = case.design.get(case.task["id"])["asset_design"]
    assert view["platform_observed_cny"] == "1.500000" and view["reserved_risk_cny"] == "1.000000" and not view["risk_released"]
    with pytest.raises(CreatorError):
        case.design.verified_manifest(case.task["id"], operation["id"])


def test_bad_key_replay_cannot_replace_consent_or_original_intent(empirical_env):
    case = design_case(empirical_env)
    quote, grant = authorize(case)
    assert case.design.quote(case.task["id"], key="asset-design-quote-001")["replayed"]
    assert case.design.grant(case.task["id"], quote["id"], disclosure_hash=quote["disclosure_hash"], consent=True,
        key="asset-design-grant-001")["replayed"]
    with pytest.raises(CreatorError):
        case.design.grant(case.task["id"], quote["id"], disclosure_hash=quote["disclosure_hash"], consent=1, key="asset-grant-integer-consent")
    operation = reserve(case, grant)
    assert case.design.reserve(case.task["id"], grant["id"], key="asset-design-reserve-001")["operation"]["id"] == operation["id"]
    with pytest.raises(CreatorError):
        case.design.reserve(case.task["id"], grant["id"], key="new-key-cannot-reset-call")


def test_old_text_grant_revoke_and_current_guard_change_preserve_historical_manifest(empirical_env):
    case = design_case(empirical_env)
    _, grant = authorize(case)
    operation = reserve(case, grant)
    started = case.design.start(case.task["id"], operation["id"])["operation"]
    fee(case, started)
    verified = case.design.verified_manifest(case.task["id"], operation["id"])
    task = case.design.store.read_only()["tasks"][case.task["id"]]
    case.env.runtime.revoke(case.task["id"], task["active_grant_id"],
        {"client_action_key": "revoke-old-text-after-assets", "expected_execution_revision": task["revision"]})
    quote = case.design.quote(case.task["id"], key="new-quote-same-frozen-scope")["quote"]
    assert quote["scope_id"] == operation["scope_id"]
    case.guard["allowed"] = False
    assert case.design.verified_manifest(case.task["id"], operation["id"]) == verified
