"""Independent public image-operator probes over the original journal and mock HTTP."""
import copy

import pytest
from asset_design_fixture import design_case
from asset_design_fixture import fee as asset_fee
from media_ledger_fixture import authorize, image_fee, media_case, posts, reserve
from test_asset_media_handoff import image_settings, ready
from test_execution_empirical import empirical_env as empirical_env
from test_image_receipts import image_bytes

from core.creator.store import CreatorError, digest
from core.execution.asset_media_handoff import check_manifest_origin, media_from_asset_design
from core.execution.image_policy import MediaBudgetAnchor
from core.execution.media_ledger import records
from core.execution.media_runtime import MediaExecution
from core.execution.policy import money
from core.execution.reference_derivation import ReferenceProfile


def restart(case, **changes):
    settings = {"policy": case.policy, "budget_anchor": case.anchor, "manifest": case.manifest,
        "authority_guard": case.authority, "fee_source": case.source, "receipt_capture": case.settings}
    settings.update(changes)
    return MediaExecution(case.env.runtime, **settings)


def revoke_original_text(case):
    task = case.env.runtime.store.read_only()["tasks"][case.task["id"]]
    case.env.runtime.revoke(case.task["id"], task["active_grant_id"],
        {"client_action_key": "audit-revoke-original-text", "expected_execution_revision": task["revision"]})


def test_requote_new_key_reuses_frozen_image_scope_after_original_text_revoke(empirical_env):
    case = media_case(empirical_env)
    quote, _ = authorize(case)
    original = copy.deepcopy(case.media.store.read_only()["media"]["scope:" + quote["scope_id"]])
    revoke_original_text(case)
    reopened = restart(case, reference_profile=None, manifest_origin=None)
    replacement = reopened.quote(case.task["id"], key="requote-after-original-text-revoke")["quote"]
    assert replacement["id"] != quote["id"] and replacement["scope_id"] == quote["scope_id"]
    payload = reopened.store.read_only()
    assert len(records(payload, "scope")) == 1
    assert payload["media"]["scope:" + quote["scope_id"]] == original
    assert not posts(case)


def test_requote_new_key_does_not_import_later_native_account_totals_into_scope_identity(empirical_env):
    case = media_case(empirical_env)
    quote, _ = authorize(case)
    original = copy.deepcopy(case.media.store.read_only()["media"]["scope:" + quote["scope_id"]])
    other = case.env.source_task("audit-later-native-task")
    native_quote = case.env.quote(other, key="audit-later-native-quote")
    native_grant = case.env.grant(other, native_quote, key="audit-later-native-grant")
    assert case.env.action(other, native_grant["execution"], key="audit-later-native-start").status_code == 200
    assert case.env.complete(other)["status"] == "plan_ready"
    replacement = restart(case).quote(case.task["id"], key="requote-after-native-account-change")["quote"]
    payload = case.media.store.read_only()
    assert replacement["scope_id"] == original["id"] and len(records(payload, "scope")) == 1
    assert payload["media"]["scope:" + original["id"]] == original and not posts(case)


def test_later_separate_media_allocation_keeps_prior_scope_and_frozen_budget(empirical_env):
    case = media_case(empirical_env)
    quote, _ = authorize(case)
    original = copy.deepcopy(case.media.store.read_only()["media"]["scope:" + quote["scope_id"]])
    count = case.policy.max_submissions
    anchor = MediaBudgetAnchor.model_validate({**case.anchor.model_dump(),
        "opening_risk_cny": money(75370000 + count * 1000000), "opening_paid_call_count": 47 + count,
        "account_limit_cny": money(75370000 + 2 * count * 1000000), "account_max_calls": 47 + 2 * count,
        "parent_ledger_hash": "a" * 64, "parent_allocation_hash": "b" * 64,
        "parent_allocation_begin_hash": "c" * 64})
    later = restart(case, budget_anchor=anchor, authority_guard=lambda scope: scope["anchor"] == anchor.frozen())
    later.quote(case.task["id"], key="audit-later-separate-media-allocation")
    replacement = restart(case).quote(case.task["id"], key="audit-prior-scope-after-later-allocation")["quote"]
    payload = case.media.store.read_only()
    assert replacement["scope_id"] == original["id"] and len(records(payload, "scope")) == 2
    assert payload["media"]["scope:" + original["id"]] == original and not posts(case)


@pytest.mark.parametrize("changed", ["profile", "origin", "manifest", "fee_source"])
def test_existing_scope_reuse_never_upgrades_immutable_configuration(empirical_env, changed):
    case = media_case(empirical_env)
    quote, _ = authorize(case)
    changes = {}
    if changed == "profile":
        changes["reference_profile"] = ReferenceProfile(allow_lossy_jpeg=False).frozen()
    elif changed == "origin":
        changes["manifest_origin"] = {"kind": "unverified-operator-origin"}
    elif changed == "manifest":
        manifest = copy.deepcopy(case.manifest)
        manifest["frames"][0]["first_frame_prompt"] += " unauthorized replacement"
        changes["manifest"] = manifest
    else:
        changes["fee_source"] = {**case.source, "token_name": "different-server-token"}
    before = case.media.store.path("state.json").read_bytes()
    with pytest.raises(CreatorError):
        restart(case, **changes).quote(case.task["id"], key="audit-immutable-scope-" + changed)
    assert case.media.store.path("state.json").read_bytes() == before
    assert case.media.get(case.task["id"])["media"]["scope_count"] == 1
    assert case.media.store.read_only()["media"]["scope:" + quote["scope_id"]]
    assert not posts(case)


def test_committed_query_recovers_and_imports_delivery_without_current_dispatch_authority(empirical_env, monkeypatch):
    case = media_case(empirical_env)
    _, grant = authorize(case)
    operation = reserve(case, grant)
    submitted = case.media.submit(case.task["id"], operation["id"])["operation"]
    image_fee(case, submitted)
    adopt = case.media._adopt

    def fail_adopt(record, raw):
        raise RuntimeError("synthetic crash after original collector committed")

    monkeypatch.setattr(case.media, "_adopt", fail_adopt)
    with pytest.raises(RuntimeError, match="synthetic crash"):
        case.media.query(case.task["id"], operation["id"], key="audit-captured-query-before-crash")
    monkeypatch.setattr(case.media, "_adopt", adopt)
    payload = case.media.store.read_only()
    query = records(payload, "query")[0]
    assert query["status"] == "submitting" and query["raw"] is None
    case.media.revoke(case.task["id"], grant["id"])
    revoke_original_text(case)
    case.guard["allowed"] = False
    disabled = MediaExecution(case.env.runtime, receipt_capture=case.settings)
    recovered = disabled.recover(case.task["id"], query["id"])["query"]
    assert recovered["status"] == "completed"
    assert disabled.query(case.task["id"], operation["id"], key="audit-captured-query-before-crash")["replayed"]
    delivery = disabled.record_delivery(case.task["id"], operation["id"], query["id"], image_bytes())
    assert delivery["delivery_hash"] and len(posts(case)) == 2
    assert disabled.record_delivery(case.task["id"], operation["id"], query["id"], image_bytes())["replayed"]
    before = disabled.store.path("state.json").read_bytes()
    with pytest.raises(CreatorError):
        disabled.record_delivery(case.task["id"], operation["id"], query["id"], b"not an image")
    assert disabled.store.path("state.json").read_bytes() == before and len(posts(case)) == 2


def test_new_query_retains_explicit_read_permit_after_image_grant_revocation(empirical_env):
    case = media_case(empirical_env)
    _, grant = authorize(case)
    operation = reserve(case, grant)
    submitted = case.media.submit(case.task["id"], operation["id"])["operation"]
    image_fee(case, submitted)
    case.media.revoke(case.task["id"], grant["id"])
    before = case.media.store.path("state.json").read_bytes()
    with pytest.raises(CreatorError) as error:
        case.media.query(case.task["id"], operation["id"], key="audit-new-query-after-grant-revoke")
    assert error.value.code == "MEDIA_GRANT_REVOKED"
    assert case.media.store.path("state.json").read_bytes() == before and len(posts(case)) == 1


def test_native_manifest_origin_and_delivery_remain_readable_after_historical_revokes(empirical_env):
    case = design_case(empirical_env)
    asset_operation, received = ready(case)
    asset_fee(case, received)
    media = media_from_asset_design(case.env.runtime, case.task["id"], asset_operation["id"], **image_settings(case))
    quote = media.quote(case.task["id"], key="audit-native-origin-image-quote")["quote"]
    grant = media.grant(case.task["id"], quote["id"], disclosure_hash=quote["disclosure_hash"], consent=True,
        key="audit-native-origin-image-grant")["grant"]
    operation = media.reserve_submit(case.task["id"], grant["id"], "S01", key="audit-native-origin-image-submit")["operation"]
    submitted = media.submit(case.task["id"], operation["id"])["operation"]
    linked = copy.copy(case.old)
    linked.media = media
    image_fee(linked, submitted)
    query = media.query(case.task["id"], operation["id"], key="audit-native-origin-image-query")["query"]
    before = media.store.read_only()["media"]["scope:" + quote["scope_id"]]
    media.revoke(case.task["id"], grant["id"])
    revoke_original_text(case)
    case.guard["allowed"] = False
    disabled = MediaExecution(case.env.runtime)
    assert disabled.get(case.task["id"])["media"]["scope_count"] == 1
    assert disabled.recover(case.task["id"], query["id"])["replayed"]
    disabled.record_delivery(case.task["id"], operation["id"], query["id"], image_bytes())
    payload = disabled.store.read_only()
    assert check_manifest_origin(payload, payload["media"]["scope:" + quote["scope_id"]])
    assert digest(before) == digest(payload["media"]["scope:" + quote["scope_id"]])
    assert len(posts(linked)) == 2
