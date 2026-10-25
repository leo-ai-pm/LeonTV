"""Native asset evidence stops original text and independently granted images."""
import pytest
from asset_design_fixture import authorize, design_case, fee, posts, reserve
from media_ledger_fixture import authorize as image_authorize
from test_asset_media_handoff import image_settings
from test_execution_empirical import empirical_env as empirical_env

from core.creator.store import CreatorError
from core.execution.asset_design import AssetDesignExecution
from core.execution.image_policy import MediaBudgetAnchor
from core.execution.media_ledger import (
    account_halted,
    fee_claimed,
    has_pending_fees,
    has_pending_unknown,
)
from core.execution.media_runtime import MediaExecution
from core.execution.policy import money
from core.execution.scope_budget import scope_envelope


@pytest.mark.parametrize("state,reason,image_reason", [
    ("unknown", "RESPONSE_UNCONFIRMED", "MEDIA_RESPONSE_UNCONFIRMED"),
    ("awaiting_fee", "PLATFORM_FEE_REQUIRED", "MEDIA_PLATFORM_FEE_REQUIRED"),
    ("overrun", "EMPIRICAL_RISK_OVERRUN", "EMPIRICAL_RISK_OVERRUN"),
])
def test_asset_state_blocks_original_text_and_already_granted_image_without_new_intent(empirical_env, state, reason, image_reason):
    case = design_case(empirical_env, fault="unknown" if state == "unknown" else None)
    _, grant = authorize(case)
    media = MediaExecution(case.env.runtime, manifest=case.old.manifest, **image_settings(case))
    quote = media.quote(case.task["id"], key="shared-gates-image-quote")["quote"]
    image_grant = media.grant(case.task["id"], quote["id"], disclosure_hash=quote["disclosure_hash"], consent=True,
                              key="shared-gates-image-grant")["grant"]
    operation = reserve(case, grant)
    result = case.design.start(case.task["id"], operation["id"])["operation"]
    if state == "overrun":
        fee(case, result, quota=750000)
    payload = case.env.runtime.store.read_only()
    task = payload["tasks"][case.task["id"]]
    assert case.env.runtime.gate(payload, task) == reason
    assert account_halted(payload, task["account_id"]) is (state == "overrun")
    assert has_pending_unknown(payload, task["account_id"]) is (state == "unknown")
    assert has_pending_fees(payload, task["account_id"]) is (state != "overrun")
    if state == "overrun":
        assert fee_claimed(payload, payload["asset_design"]["operation:" + operation["id"]]["fee_id"])
    before = case.env.runtime.store.path("state.json").read_bytes()
    with pytest.raises(CreatorError) as error:
        media.reserve_submit(case.task["id"], image_grant["id"], "S01", key="blocked-shared-image")
    assert error.value.code == image_reason
    assert case.env.runtime.store.path("state.json").read_bytes() == before
    assert len(posts(case)) == 1 and not case.old.counter.exists()


def test_asset_quote_after_image_scope_inherits_full_frozen_media_envelope(empirical_env):
    case = design_case(empirical_env)
    image_authorize(case.old)
    count = case.old.policy.max_submissions
    data = case.anchor.model_dump()
    data.update(opening_risk_cny=money(75370000 + count * 1000000), opening_paid_call_count=47 + count,
                account_limit_cny=money(76370000 + count * 1000000), account_max_calls=48 + count)
    anchor = MediaBudgetAnchor.model_validate(data)
    design = AssetDesignExecution(case.env.runtime, policy=case.policy, budget_anchor=anchor,
        authority_guard=lambda scope: scope["anchor"] == anchor.frozen(), fee_source=case.source, receipt_capture=case.settings)
    quote = design.quote(case.task["id"], key="asset-after-image-quote")["quote"]
    payload = case.env.runtime.store.read_only()
    scope = payload["asset_design"]["scope:" + quote["scope_id"]]
    assert scope["anchor"]["opening_paid_call_count"] == 47 + count
    assert scope_envelope(payload, anchor.account_scope_id) == {
        "risk_units": 76370000 + count * 1000000, "max_calls": 48 + count}
    assert posts(case) == [] and not case.old.counter.exists()
