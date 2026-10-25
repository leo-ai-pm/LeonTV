"""Native frame tamper/compatibility checks for optional media records."""
import copy

import pytest
from media_ledger_fixture import authorize, image_fee, media_case, reserve
from test_execution_empirical import empirical_env as empirical_env

from core.creator.store import CreatorError, digest
from core.execution.media_ledger import account_halted, fee_claimed, projection, validate


@pytest.mark.parametrize("change", ["opening_risk", "opening_calls", "account", "source", "manifest", "policy", "grant", "raw", "delete", "tuple", "index", "descriptor", "scope_reuse"])
def test_media_journal_rebuild_rejects_rehashed_or_changed_financial_evidence(empirical_env, change):
    case = media_case(empirical_env)
    _, grant = authorize(case)
    operation = reserve(case, grant)
    before = case.media.store.read_only()
    after = copy.deepcopy(before)
    scope = next(record for record in after["media"].values() if record["kind"] == "scope")
    if change == "opening_risk":
        scope["anchor"]["opening_risk_cny"] = "73.37"
    elif change == "opening_calls":
        scope["anchor"]["opening_paid_call_count"] = 45
    elif change == "account":
        scope["account_hash"] = "9" * 64
    elif change == "source":
        scope["asset_input"]["constraints"]["max_images"] = 99
    elif change == "manifest":
        scope["manifest"]["frames"][0]["first_frame_prompt"] = "changed"
    elif change == "policy":
        scope["policy"]["max_submissions"] = 12
    elif change == "grant":
        after["media"]["grant:" + grant["id"]]["disclosure_hash"] = "9" * 64
    elif change == "raw":
        after["media"]["operation:" + operation["id"]]["raw"] = {}
    elif change == "delete":
        del after["media"]["operation:" + operation["id"]]
    elif change == "tuple":
        scope["manifest"]["frames"] = tuple(scope["manifest"]["frames"])
    elif change == "index":
        after["media"]["operation:" + operation["id"]]["submission_index"] = 2
    elif change == "descriptor":
        after["media"]["operation:" + operation["id"]]["descriptor"]["wire"] = "{}"
    else:
        scope["id"] = "f" * 32
    with pytest.raises(CreatorError):
        validate(after, before)
    assert digest(case.media.store.read_only()) == digest(before)


def test_media_scope_is_optional_and_no_media_projection_does_not_relabel_text(empirical_env):
    case = media_case(empirical_env)
    payload = case.media.store.read_only()
    assert "media" not in payload
    validate(payload)
    assert projection(payload)["status"] == "not_authorized" and projection(payload)["paid_call_count"] == 0


def test_exact_claim_is_global_and_same_operation_replay_does_not_double_count(empirical_env):
    case = media_case(empirical_env)
    _, grant = authorize(case)
    operation = reserve(case, grant)
    operation = case.media.submit(case.task["id"], operation["id"])["operation"]
    image_fee(case, operation)
    payload = case.media.store.read_only()
    fee_id = payload["media"]["operation:" + operation["id"]]["fee_id"]
    assert fee_claimed(payload, fee_id) and not fee_claimed(payload, fee_id, operation["id"])
    assert not account_halted(payload, case.anchor.account_scope_id)


def test_old_account_and_text_financial_authority_remain_identical_after_media(empirical_env):
    case = media_case(empirical_env)
    old = case.media.store.read_only()
    _, grant = authorize(case)
    reserve(case, grant)
    new = case.media.store.read_only()
    for name in ("tasks", "quotes", "grants", "operations", "keys", "accounts"):
        assert digest(old[name]) == digest(new[name])
    assert next(iter(new["accounts"].values()))["limit_units"] == 75370000
