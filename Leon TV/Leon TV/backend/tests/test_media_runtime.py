"""Actual native journal and detached mock image transport, never live network."""
import copy
import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from media_ledger_fixture import authorize, image_fee, media_case, posts, reserve
from test_execution_empirical import empirical_env as empirical_env
from test_image_receipts import image_bytes

from core.creator.store import CreatorError, digest
from core.execution.image_policy import FrozenImageRequest
from core.execution.media_runtime import MediaExecution


def test_default_off_read_is_zero_write_and_quote_cannot_create_authority(empirical_env):
    case = media_case(empirical_env)
    before = case.media.store.path("state.json").read_bytes()
    disabled = MediaExecution(case.env.runtime)
    assert disabled.get(case.task["id"])["media"]["status"] == "not_authorized"
    with pytest.raises(CreatorError):
        disabled.quote(case.task["id"], key="disabled-media-quote")
    assert case.media.store.path("state.json").read_bytes() == before and posts(case) == []


def test_quote_grant_and_reservation_preserve_old_financial_records_and_do_not_start(empirical_env):
    case = media_case(empirical_env)
    core = copy.deepcopy(case.media.store.read_only())
    quote, grant = authorize(case)
    operation = reserve(case, grant)
    after = case.media.store.read_only()
    assert all(digest(after[key]) == digest(value) for key, value in core.items())
    assert posts(case) == [] and operation["status"] == "reserved"
    assert case.media.quote(case.task["id"], key="synthetic-media-quote-001")["replayed"]
    assert case.media.grant(case.task["id"], quote["id"], disclosure_hash=quote["disclosure_hash"], consent=True,
        key="synthetic-media-grant-001")["replayed"]
    assert case.media.reserve_submit(case.task["id"], grant["id"], "S01", key="synthetic-media-submit-001")["replayed"]
    view = case.media.get(case.task["id"])["media"]
    assert view["scopes"][0]["opening_risk_cny"] == "75.37" and view["scopes"][0]["opening_paid_call_count"] == 47
    assert view["reserved_risk_cny"] == f"{case.policy.max_submissions}.000000" and view["paid_call_count"] == 1
    assert all(secret not in json.dumps(view) for secret in ("body_base64", "https://", "prompt", "script", "bytes_base64", "wire"))


def test_submit_same_task_query_fee_delivery_and_next_internal_reference(empirical_env):
    case = media_case(empirical_env)
    _, grant = authorize(case)
    operation = reserve(case, grant)
    submitted = case.media.submit(case.task["id"], operation["id"])["operation"]
    assert submitted["status"] == "accepted" and len(posts(case)) == 1
    assert case.media.submit(case.task["id"], operation["id"])["replayed"] and len(posts(case)) == 1
    with pytest.raises(CreatorError) as error:
        reserve(case, grant, "S02", "second-before-fee")
    assert error.value.code == "MEDIA_PLATFORM_FEE_REQUIRED"
    fee = image_fee(case, submitted)
    assert not fee["replayed"]
    assert image_fee(case, submitted)["replayed"]
    query = case.media.query(case.task["id"], operation["id"], key="query-original-001")["query"]
    assert query["status"] == "completed" and len(posts(case)) == 2
    assert case.media.query(case.task["id"], operation["id"], key="query-original-001")["replayed"] and len(posts(case)) == 2
    case.media.record_delivery(case.task["id"], operation["id"], query["id"], image_bytes())
    following = reserve(case, grant, "S02", "synthetic-media-submit-002")
    assert following["descriptor"]["references"][0]["shot_id"] == "S01"
    assert len(posts(case)) == 2 and case.media.get(case.task["id"])["media"]["paid_call_count"] == 2


def test_unknown_never_resubmits_and_survives_replacement_grant_and_restart(empirical_env):
    case = media_case(empirical_env, fault="unknown")
    quote, grant = authorize(case)
    operation = reserve(case, grant)
    assert case.media.submit(case.task["id"], operation["id"])["operation"]["status"] == "unknown"
    case.media.recover(case.task["id"], operation["id"])
    assert case.media.submit(case.task["id"], operation["id"])["replayed"]
    replacement = case.media.grant(case.task["id"], quote["id"], disclosure_hash=quote["disclosure_hash"], consent=True,
        key="replacement-media-grant")["grant"]
    restarted = MediaExecution(case.env.runtime, policy=case.policy, budget_anchor=case.anchor, manifest=case.manifest,
        authority_guard=case.authority, fee_source=case.source, receipt_capture=case.settings)
    with pytest.raises(CreatorError) as error:
        restarted.reserve_submit(case.task["id"], replacement["id"], "S02", key="replacement-post-key")
    assert error.value.code == "MEDIA_RESPONSE_UNCONFIRMED" and len(posts(case)) == 1


@pytest.mark.parametrize("malformed", ["html_502", "bad_json_200", "duplicate_json"])
def test_complete_unusable_http_is_stable_unknown_with_original_receipt_preserved(empirical_env, malformed):
    case = media_case(empirical_env, malformed=malformed)
    _, grant = authorize(case)
    operation = reserve(case, grant)
    result = case.media.submit(case.task["id"], operation["id"])["operation"]
    assert result["status"] == "unknown" and result["raw"] is result["receipt"] is None
    original = case.media.capture.ready_raw(FrozenImageRequest.from_frozen(operation["descriptor"]))[0]
    assert original is not None
    before = case.media.store.path("state.json").read_bytes()
    assert case.media.recover(case.task["id"], operation["id"])["replayed"]
    assert case.media.recover(case.task["id"], operation["id"])["replayed"]
    assert case.media.submit(case.task["id"], operation["id"])["replayed"]
    assert case.media.store.path("state.json").read_bytes() == before and len(posts(case)) == 1


def test_overrun_is_recorded_whole_and_halts_account_without_releasing_scope(empirical_env):
    case = media_case(empirical_env)
    _, grant = authorize(case)
    operation = reserve(case, grant)
    submitted = case.media.submit(case.task["id"], operation["id"])["operation"]
    image_fee(case, submitted, quota=750000)
    view = case.media.get(case.task["id"])["media"]
    assert view["status"] == "halted" and view["platform_observed_cny"] == "1.500000"
    assert view["reserved_risk_cny"] == f"{case.policy.max_submissions}.000000" and view["risk_released"] is False
    with pytest.raises(CreatorError) as error:
        reserve(case, grant, "S02", "after-overrun-submit")
    assert error.value.code == "EMPIRICAL_RISK_OVERRUN" and len(posts(case)) == 1


def test_historical_ordinal_fee_requires_verified_export_bridge(empirical_env):
    case = media_case(empirical_env, legacy=True)
    quote = case.media.quote(case.task["id"], key="legacy-bridge-quote")["quote"]
    with pytest.raises(CreatorError) as error:
        case.media.grant(case.task["id"], quote["id"], disclosure_hash=quote["disclosure_hash"], consent=True, key="legacy-bridge-grant")
    assert error.value.code == "MEDIA_LEGACY_FEE_BRIDGE_REQUIRED"
    before = copy.deepcopy(case.media.store.read_only()["operations"])
    for operation_id, blob in case.text_exports.items():
        assert not case.media.register_text_claim(operation_id, blob)["replayed"]
        assert case.media.register_text_claim(operation_id, blob)["replayed"]
    assert digest(before) == digest(case.media.store.read_only()["operations"])
    case.media.grant(case.task["id"], quote["id"], disclosure_hash=quote["disclosure_hash"], consent=True, key="legacy-bridge-grant")


def test_concurrent_same_reservation_has_one_financial_intent(empirical_env):
    case = media_case(empirical_env)
    _, grant = authorize(case)
    def reserve_once():
        try:
            return reserve(case, grant)["id"]
        except CreatorError as error:
            assert error.code == "ACTION_NOT_ALLOWED"
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: reserve_once(), range(2)))
    assert len(set(result for result in results if result)) == 1
    assert case.media.get(case.task["id"])["media"]["paid_call_count"] == 1 and posts(case) == []


def test_other_already_inflight_text_overrun_is_recorded_without_corrupting_frozen_media_scope(empirical_env):
    case = media_case(empirical_env)
    other = case.env.source_task("inflight-overrun")
    quote = case.env.quote(other, key="inflight-text-quote")
    grant = case.env.grant(other, quote, key="inflight-text-grant")
    assert case.env.action(other, grant["execution"], key="inflight-text-start").status_code == 200
    pending = case.env.wait(other, {"needs_reconciliation"})
    scope_quote = case.media.quote(case.task["id"], key="scope-while-text-pending")["quote"]
    frozen = case.media.store.read_only()["media"]["scope:" + scope_quote["scope_id"]]
    operation_id = pending["operations"][-1]["id"]
    evidence = case.env.evidence(operation_id)
    evidence["platform_fee_cny"] = "9.000000"
    result = case.env.runtime.observe_fee(other["id"], operation_id, evidence)["execution"]
    assert result["operations"][-1]["fee_status"] == "overrun"
    assert case.media.get(case.task["id"])["media"]["status"] == "halted"
    assert digest(frozen) == digest(case.media.store.read_only()["media"]["scope:" + scope_quote["scope_id"]])


def test_old_text_grant_revoke_does_not_invalidate_accepted_media_provenance(empirical_env):
    case = media_case(empirical_env)
    quote, _ = authorize(case)
    before = case.media.store.read_only()
    task = before["tasks"][case.task["id"]]
    case.env.runtime.revoke(case.task["id"], task["active_grant_id"],
        {"client_action_key": "revoke-original-text-grant", "expected_execution_revision": task["revision"]})
    assert case.media.get(case.task["id"])["media"]["scope_count"] == 1
    assert digest(before["media"]["scope:" + quote["scope_id"]]) == digest(case.media.store.read_only()["media"]["scope:" + quote["scope_id"]])


def test_synthetic_text_acceptance_cannot_be_promoted_to_live_image_policy(empirical_env):
    case = media_case(empirical_env)
    policy = {**case.policy.model_dump(), "validation_only": False, "origin": "https://api.ai-tudou.net"}
    case.media.policy = type(case.policy).model_validate(policy).frozen()
    with pytest.raises(CreatorError) as error:
        case.media.quote(case.task["id"], key="fake-live-image-quote")
    assert error.value.code == "MEDIA_ENVIRONMENT_MISMATCH" and posts(case) == []


def test_direct_image_request_id_cannot_reuse_a_native_text_fee_even_with_another_model_row(empirical_env):
    case = media_case(empirical_env)
    first_text_id = next(iter(case.media.store.read_only()["operations"]))
    case.media.capture.settings.worker_options["force_request_id"] = "text-" + first_text_id
    _, grant = authorize(case)
    operation = reserve(case, grant)
    operation = case.media.submit(case.task["id"], operation["id"])["operation"]
    before = case.media.store.path("state.json").read_bytes()
    with pytest.raises(CreatorError) as error:
        image_fee(case, operation)
    assert error.value.code == "FEE_EVIDENCE_CONFLICT"
    assert case.media.store.path("state.json").read_bytes() == before
