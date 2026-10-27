"""Native same-journal response contract approval, execution and fail-closed stop."""
import copy
import json
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
from core.execution.image_runtime_control import RuntimeContinuedImageControl
from core.execution.media_ledger import records
from core.execution.scope_budget import scope_envelope


def test_response_arguments_are_explicit_and_quote_only(monkeypatch, capsys):
    monkeypatch.setattr(image_runtime_operator, "read_image_control", lambda *a: pytest.fail("implicit control"))
    for args in (["--response-window-seconds", "180"], ["status", "--response-window-seconds", "0"],
                 ["quote", "--response-window-seconds", "invalid"]):
        assert image_runtime_operator.main(args) == 1
        assert json.loads(capsys.readouterr().out)["paid_calls"] == 0


def test_native_response_contract_reuses_funding_and_unknown_still_stops_next_shot(asset_control_case, monkeypatch, capsys):
    import asset_operator_fixture

    settings = asset_operator_fixture.ReceiptCaptureSettings

    def four_shots(*args, **kwargs):
        if kwargs.get("worker_factory") == "empirical_product_fixture:make_transport":
            kwargs["worker_factory"] = "four_shot_runtime_fixture:make_transport"
        return settings(*args, **kwargs)

    monkeypatch.setattr(asset_operator_fixture, "ReceiptCaptureSettings", four_shots)
    case = prepared_runtime_case(asset_control_case)
    case.settings = replace(case.settings, observation_wait_seconds=None)
    store = case.env.runtime.store
    before = store.read_only()
    old_authority = copy.deepcopy(case.runtime_authority)
    parent_before = case.server_parent_path.read_bytes()
    account = case.control.document.budget_anchor["account_scope_id"]
    envelope = scope_envelope(before, account)
    state_before = json.loads(store.path("state.json").read_text())
    monkeypatch.setattr(image_runtime_operator, "BACKEND", case.backend)
    common = ["--control", str(case.path)]
    assert image_runtime_operator.main(["quote", *common, "--valid-until", case.runtime_valid_until,
        "--response-window-seconds", "180"]) == 0
    disclosure = json.loads(capsys.readouterr().out)
    assert disclosure["response_policy"]["response_window_seconds"] == 180
    assert disclosure["dispatch_available"] is False and disclosure["additional_paid_calls"] == 0
    new_quote = continuation.quote_runtime_continuation(case.control, valid_until=case.runtime_valid_until,
        response_window_seconds=180)
    assert new_quote["replayed"] and new_quote["quote"]["id"] != case.runtime_quote["id"]
    q = new_quote["quote"]
    assert q["disclosure"]["schema_version"] == 2
    frozen = store.path("state.json").read_bytes()
    with pytest.raises(CreatorError):
        continuation.activate_runtime_continuation(case.control, q["id"], disclosure_hash=q["disclosure_hash"], consent=1)
    assert store.path("state.json").read_bytes() == frozen
    activation = continuation.activate_runtime_continuation(case.control, q["id"],
        disclosure_hash=q["disclosure_hash"], consent=True)["activation"]
    authority = continuation.authority_for(store.read_only(), activation["id"])
    assert authority["schema_version"] == 3 and authority["allocations"] == old_authority["allocations"]
    assert authority["intent_id"] == old_authority["intent_id"]
    assert continuation.authority_for(store.read_only(), case.runtime_activation["id"]) == old_authority
    frozen = store.path("state.json").read_bytes()
    assert continuation.quote_runtime_continuation(case.control, valid_until=case.runtime_valid_until,
        response_window_seconds=180)["replayed"]
    assert continuation.activate_runtime_continuation(case.control, q["id"],
        disclosure_hash=q["disclosure_hash"], consent=True)["replayed"]
    assert store.path("state.json").read_bytes() == frozen
    control = RuntimeContinuedImageControl(case.control, activation["id"])
    http_before = len(posts(case))

    def no_credential():
        pytest.fail("Synthetic contract requested a real credential")

    with image_operator.build_image_session(control, receipt_capture=case.settings, credential_loader=no_credential) as session:
        case.media = session.media
        target = authority["remaining_jobs"][0]
        operation = session.media.reserve_submit(case.task["id"], case.grant["id"], target["shot_id"],
            key="response-contract-first")["operation"]
        assert operation["descriptor"]["kind"] == "controlled_image_http_v3"
        assert operation["descriptor"]["permit"]["schema_version"] == 4
        accepted = session.media.submit(case.task["id"], operation["id"])["operation"]
        assert accepted["status"] == "accepted"
        claim = session.media.capture.receipts.operation(operation["id"]).claim()
        assert claim["version"] == 2
        assert claim["binding"]["response_contract_hash"] == authority["response_policy"]["contract_hash"]
        assert claim["binding"]["response_deadline_at"] == operation["descriptor"]["permit"]["response_deadline_at"]
        assert claim["observer_until"] == claim["binding"]["response_deadline_at"]
        image_fee(case, accepted)
        query = session.media.query(case.task["id"], operation["id"], key="response-contract-query")["query"]
        assert query["status"] == "completed" and query["descriptor"]["kind"] == "controlled_image_http_v3"
        session.media.record_delivery(case.task["id"], operation["id"], query["id"], image_bytes())
        stable = store.path("state.json").read_bytes()
        assert session.media.submit(case.task["id"], operation["id"])["replayed"]
        assert session.media.recover(case.task["id"], operation["id"])["replayed"]
        assert store.path("state.json").read_bytes() == stable
        assert len(posts(case)) == http_before + 2
        next_target = authority["remaining_jobs"][1]
        pending = session.media.reserve_submit(case.task["id"], case.grant["id"], next_target["shot_id"],
            key="response-contract-unknown")["operation"]

        def unknown_response(*args, **kwargs):
            raise CreatorError("MEDIA_RESPONSE_UNCONFIRMED", "Synthetic missing response", 409)

        monkeypatch.setattr(session.media.capture, "capture", unknown_response)
        unknown = session.media.submit(case.task["id"], pending["id"])["operation"]
        assert unknown["status"] == "unknown" and unknown["raw"] is None
        stable = store.path("state.json").read_bytes()
        with pytest.raises(CreatorError):
            session.media.reserve_submit(case.task["id"], case.grant["id"], authority["remaining_jobs"][2]["shot_id"],
                key="response-contract-must-stop")
        assert store.path("state.json").read_bytes() == stable
        assert image_operator._status(control)["blocked_reason"] == "MEDIA_RESPONSE_UNCONFIRMED"
        assert len(posts(case)) == http_before + 2
    # A newly approved response window must not erase or exempt the new unknown.
    changed_quote = continuation.quote_runtime_continuation(case.control, valid_until=case.runtime_valid_until,
        response_window_seconds=60)["quote"]
    assert changed_quote["id"] != q["id"]
    changed_activation = continuation.activate_runtime_continuation(case.control, changed_quote["id"],
        disclosure_hash=changed_quote["disclosure_hash"], consent=True)["activation"]
    changed_control = RuntimeContinuedImageControl(case.control, changed_activation["id"])
    stable = store.path("state.json").read_bytes()
    with image_operator.build_image_session(changed_control, receipt_capture=case.settings, credential_loader=no_credential) as session:
        with pytest.raises(CreatorError):
            session.media.reserve_submit(case.task["id"], case.grant["id"], authority["remaining_jobs"][2]["shot_id"],
                key="response-contract-renewal-must-stop")
    assert image_operator._status(changed_control)["blocked_reason"] == "MEDIA_RESPONSE_UNCONFIRMED"
    assert store.path("state.json").read_bytes() == stable and len(posts(case)) == http_before + 2
    after = store.read_only()
    assert after["media"]["operation:" + pending["id"]] == unknown
    assert scope_envelope(after, account) == envelope and case.server_parent_path.read_bytes() == parent_before
    assert after["media"]["operation:" + case.unknown["id"]] == case.unknown
    assert all(after["media"][key] == value for key, value in before["media"].items())
    assert json.loads(store.path("state.json").read_text())["frames"][:len(state_before["frames"])] == state_before["frames"]
    assert len(records(after, "operation")) == len(records(before, "operation")) + 2
