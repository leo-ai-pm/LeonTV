"""Explicit retained-unknown plan uses the last funded slot in the same journal."""
import copy
import json
import time
from dataclasses import replace

import pytest
from asset_operator_fixture import asset_control_case as asset_control_case
from image_operator_fixture import posts
from media_ledger_fixture import image_fee
from test_image_receipts import image_bytes
from test_image_runtime_continuation import prepared_runtime_case

from core.creator.store import CreatorError
from core.execution import image_operator, image_runtime_operator
from core.execution import image_runtime_continuation as continuation
from core.execution.image_policy import FrozenImageRequest
from core.execution.image_response_lifecycle import response_deadline
from core.execution.image_runtime_control import RuntimeContinuedImageControl
from core.execution.media_ledger import records
from core.execution.policy import now
from core.execution.scope_budget import scope_envelope


def two_unknown_case(asset_control_case, monkeypatch):
    import asset_operator_fixture

    settings = asset_operator_fixture.ReceiptCaptureSettings

    def four_shots(*args, **kwargs):
        if kwargs.get("worker_factory") == "empirical_product_fixture:make_transport":
            kwargs["worker_factory"] = "four_shot_runtime_fixture:make_transport"
        return settings(*args, **kwargs)

    monkeypatch.setattr(asset_operator_fixture, "ReceiptCaptureSettings", four_shots)
    case = prepared_runtime_case(asset_control_case)
    case.settings = replace(case.settings, observation_wait_seconds=None)
    control = RuntimeContinuedImageControl(case.control, case.runtime_activation["id"])
    with image_operator.build_image_session(control, receipt_capture=case.settings,
            credential_loader=lambda: pytest.fail("real key")) as session:
        case.media = session.media
        second = session.media.reserve_submit(case.task["id"], case.grant["id"], case.runtime_authority["remaining_jobs"][0]["shot_id"],
            key="retained-prepare-second")["operation"]
        accepted = session.media.submit(case.task["id"], second["id"])["operation"]
        assert accepted["status"] == "accepted"
        image_fee(case, accepted)
        query = session.media.query(case.task["id"], second["id"], key="retained-prepare-query")["query"]
        assert query["status"] == "completed"
        session.media.record_delivery(case.task["id"], second["id"], query["id"], image_bytes())
        third = session.media.reserve_submit(case.task["id"], case.grant["id"], case.runtime_authority["remaining_jobs"][1]["shot_id"],
            key="retained-prepare-unknown")["operation"]

        def missing(*args, **kwargs):
            raise CreatorError("MEDIA_RESPONSE_UNCONFIRMED", "Synthetic missing response", 409)

        monkeypatch.setattr(session.media.capture, "capture", missing)
        case.new_unknown = session.media.submit(case.task["id"], third["id"])["operation"]
        assert case.new_unknown["status"] == "unknown"
        stable = case.env.runtime.store.path("state.json").read_bytes()
        with pytest.raises(CreatorError):
            session.media.reserve_submit(case.task["id"], case.grant["id"], third["shot_id"], key="implicit-retry-denied")
        assert case.env.runtime.store.path("state.json").read_bytes() == stable
    # The prior synthetic permit really expires; do not relax production clocks
    # or fabricate a historical cleanup timestamp to make the new quote pass.
    cutoff = response_deadline(FrozenImageRequest.from_frozen(case.new_unknown["descriptor"]).binding())
    while (cutoff - now()).total_seconds() + 5 >= 0:
        time.sleep(0.1)
    return case


def test_retention_flag_is_explicit_quote_only(monkeypatch, capsys):
    monkeypatch.setattr(image_runtime_operator, "read_image_control", lambda *args: pytest.fail("implicit control"))
    for args in (["--retain-unknown-risk"], ["status", "--retain-unknown-risk"], ["activate", "--retain-unknown-risk"]):
        assert image_runtime_operator.main(args) == 1
        assert json.loads(capsys.readouterr().out)["paid_calls"] == 0


@pytest.mark.parametrize("outcome", ["accepted", "unknown"])
def test_retained_plan_last_slot_keeps_old_unknowns_and_defers_fourth(asset_control_case, monkeypatch, capsys, outcome):
    case = two_unknown_case(asset_control_case, monkeypatch)
    store = case.env.runtime.store
    before = store.read_only()
    parent_before = case.server_parent_path.read_bytes()
    state_before = json.loads(store.path("state.json").read_text())
    account = case.control.document.budget_anchor["account_scope_id"]
    envelope = scope_envelope(before, account)
    old_authority = copy.deepcopy(case.runtime_authority)
    monkeypatch.setattr(image_runtime_operator, "BACKEND", case.backend)
    assert image_runtime_operator.main(["quote", "--control", str(case.path), "--valid-until", case.runtime_valid_until,
        "--response-window-seconds", "180", "--retain-unknown-risk"]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["retained_unknown_plan"]["retained_unknown_count"] == 2
    assert len(summary["retained_unknown_plan"]["funded_jobs"]) == len(summary["retained_unknown_plan"]["deferred_jobs"]) == 1
    q = continuation.quote_runtime_continuation(case.control, valid_until=case.runtime_valid_until,
        response_window_seconds=180, retain_unknown_risk=True)
    assert q["replayed"]
    q = q["quote"]
    plan = q["disclosure"]["retained_unknown_plan"]
    assert plan["used_submissions"] == 4 and plan["remaining_funded_slots"] == 1
    assert plan["funded_targets"][0]["replacement_of"] == case.new_unknown["id"]
    assert plan["funded_targets"][0]["planned_submission_index"] == 5
    assert "planned_submission_index" not in plan["deferred_targets"][0]
    frozen = store.path("state.json").read_bytes()
    with pytest.raises(CreatorError):
        continuation.activate_runtime_continuation(case.control, q["id"], disclosure_hash=q["disclosure_hash"], consent=1)
    assert store.path("state.json").read_bytes() == frozen
    a = continuation.activate_runtime_continuation(case.control, q["id"], disclosure_hash=q["disclosure_hash"], consent=True)["activation"]
    authority = continuation.authority_for(store.read_only(), a["id"])
    assert authority["schema_version"] == 4 and authority["allocations"] == old_authority["allocations"]
    assert continuation.authority_for(store.read_only(), case.runtime_activation["id"]) == old_authority
    assert authority["remaining_jobs"] == plan["funded_targets"]
    control = RuntimeContinuedImageControl(case.control, a["id"])
    status = image_operator._status(control)
    assert status["funded_remaining_jobs"] == 1 and status["deferred_jobs"] == 1 and status["remaining_jobs"] == 2
    http_before = len(posts(case))
    with image_operator.build_image_session(control, receipt_capture=case.settings,
            credential_loader=lambda: pytest.fail("real key")) as session:
        case.media = session.media
        stable = store.path("state.json").read_bytes()
        with pytest.raises(CreatorError):
            session.media.reserve_submit(case.task["id"], case.grant["id"], plan["deferred_targets"][0]["shot_id"], key="deferred-cannot-start")
        assert store.path("state.json").read_bytes() == stable
        operation = session.media.reserve_submit(case.task["id"], case.grant["id"], plan["funded_targets"][0]["shot_id"],
            key="retained-exact-replacement")["operation"]
        assert operation["submission_index"] == 5 and operation["descriptor"]["kind"] == "controlled_image_http_v3"
        assert operation["descriptor"]["permit"]["replacement_of"] == case.new_unknown["id"]
        assert operation["descriptor"]["reference_bindings"][0]["source_operation_id"] != case.unknown["id"]
        if outcome == "unknown":
            def missing(*args, **kwargs):
                raise CreatorError("MEDIA_RESPONSE_UNCONFIRMED", "Synthetic second missing response", 409)
            monkeypatch.setattr(session.media.capture, "capture", missing)
        result = session.media.submit(case.task["id"], operation["id"])["operation"]
        assert result["status"] == outcome
        if outcome == "accepted":
            image_fee(case, result)
            query = session.media.query(case.task["id"], operation["id"], key="retained-exact-query")["query"]
            assert query["status"] == "completed"
            session.media.record_delivery(case.task["id"], operation["id"], query["id"], image_bytes())
            status = image_operator._status(control)
            assert status["remaining_jobs"] == status["deferred_jobs"] == 1 and status["funded_remaining_jobs"] == 0
            assert status["blocked_reason"] == "NEEDS_FUNDING_OR_NEW_PLAN"
        else:
            assert image_operator._status(control)["blocked_reason"] == "MEDIA_RESPONSE_UNCONFIRMED"
        stable = store.path("state.json").read_bytes()
        assert session.media.submit(case.task["id"], operation["id"])["replayed"]
        for target in (plan["funded_targets"][0], plan["deferred_targets"][0]):
            with pytest.raises(CreatorError):
                session.media.reserve_submit(case.task["id"], case.grant["id"], target["shot_id"], key="sixth-denied-" + target["shot_id"])
        assert store.path("state.json").read_bytes() == stable
        assert len(posts(case)) == http_before + (2 if outcome == "accepted" else 0)
    after = store.read_only()
    assert len(records(after, "operation")) == 5 and scope_envelope(after, account) == envelope
    assert case.server_parent_path.read_bytes() == parent_before
    assert all(after["media"][key] == value for key, value in before["media"].items())
    assert after["media"]["operation:" + case.unknown["id"]] == case.unknown
    assert after["media"]["operation:" + case.new_unknown["id"]] == case.new_unknown
    assert json.loads(store.path("state.json").read_text())["frames"][:len(state_before["frames"])] == state_before["frames"]
