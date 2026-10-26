"""Retained continuation risk includes unused historical allocations exactly once."""
import copy
import sys
from types import SimpleNamespace

import pytest

from core.creator.store import CreatorError
from core.execution import scope_budget as budget


@pytest.fixture
def arithmetic_case(monkeypatch):
    # This isolates shared-envelope arithmetic. Full journal provenance is
    # exercised by the continuation/store integration and independent audits.
    account = "account"
    asset = {"kind": "scope", "id": "asset", "account_id": account,
        "anchor": {"scope_risk_cny": "1.000000", "scope_max_calls": 1,
            "account_limit_cny": "76.370000", "account_max_calls": 48, "parent_allocation_hash": "asset-hash"}}
    image = {"kind": "scope", "id": "image", "account_id": account,
        "anchor": {"scope_risk_cny": "4.000000", "scope_max_calls": 4,
            "account_limit_cny": "81.370000", "account_max_calls": 53, "parent_allocation_hash": "image-hash"}}
    quote = {"kind": "validity_continuation_quote", "id": "quote", "scope_id": "image",
        "proposal": {"parent_allocation": {"operation_key": "extension", "request": {"kind": "proposal"}, "quote_ref": "quote"},
            "basis": {"budget": {"proposed_retained_risk_cny": "82.370000", "proposed_max_calls": 54}}}}
    intent = {"kind": "validity_continuation_intent", "id": "intent", "scope_id": "image", "quote_id": "quote",
        "additional_risk_cny": "1.000000", "additional_paid_calls": 1}
    payload = {"accounts": {account: {"limit_units": 75370000, "max_calls": 47}}, "operations": {},
        "asset_design": {"scope:asset": asset}, "media": {"scope:image": image, "validity_continuation_quote:quote": quote,
            "validity_continuation_intent:intent": intent}}

    def extensions(value, account_id):
        return [r for r in value["media"].values() if r["kind"] == "validity_continuation_intent"
                and value["media"]["scope:" + r["scope_id"]]["account_id"] == account_id]

    monkeypatch.setitem(sys.modules, "core.execution.image_validity_continuation", SimpleNamespace(retained_extensions=extensions))
    return SimpleNamespace(payload=payload, account=account)


def test_pending_intent_retains_the_unused_parent_allocation_without_activation(arithmetic_case):
    case = arithmetic_case
    before = copy.deepcopy(case.payload)
    del before["media"]["validity_continuation_intent:intent"]
    assert budget.scope_envelope(before, case.account) == {"risk_units": 81370000, "max_calls": 53}
    assert budget.scope_envelope(case.payload, case.account) == {"risk_units": 82370000, "max_calls": 54}
    budget.validate_shared_scopes(case.payload, before)


def test_later_scope_inherits_extension_without_counting_it_twice(arithmetic_case):
    case = arithmetic_case
    before = copy.deepcopy(case.payload)
    case.payload["asset_design"]["scope:later"] = {"kind": "scope", "id": "later", "account_id": case.account,
        "anchor": {"scope_risk_cny": "1.000000", "scope_max_calls": 1, "account_limit_cny": "83.370000",
            "account_max_calls": 55, "parent_allocation_hash": "later-hash", "opening_risk_cny": "82.370000",
            "opening_paid_call_count": 54}}
    assert budget.scope_envelope(case.payload, case.account) == {"risk_units": 83370000, "max_calls": 55}
    budget.validate_shared_scopes(case.payload, before)
    case.payload["asset_design"]["scope:later"]["anchor"]["opening_risk_cny"] = "81.370000"
    with pytest.raises(CreatorError):
        budget.validate_shared_scopes(case.payload, before)


def test_extension_parent_allocation_cannot_be_reused_by_another_scope(arithmetic_case):
    case = arithmetic_case
    allocation_hash = budget._extensions(case.payload, case.account)[0]["allocation_hash"]
    case.payload["asset_design"]["scope:asset"]["anchor"]["parent_allocation_hash"] = allocation_hash
    with pytest.raises(CreatorError):
        budget.validate_shared_scopes(case.payload)


def test_new_scope_and_extension_cannot_share_one_opening_transaction(arithmetic_case):
    case = arithmetic_case
    before = copy.deepcopy(case.payload)
    del before["media"]["validity_continuation_intent:intent"]
    del before["asset_design"]["scope:asset"]
    with pytest.raises(CreatorError):
        budget.validate_shared_scopes(case.payload, before)


def test_pending_extension_still_enforces_original_total_limit(arithmetic_case):
    case = arithmetic_case
    quote = case.payload["media"]["validity_continuation_quote:quote"]
    quote["proposal"]["basis"]["budget"]["proposed_retained_risk_cny"] = "101.000000"
    with pytest.raises(CreatorError):
        budget.validate_shared_scopes(case.payload)
