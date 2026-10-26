"""Same-store continuation execution, real mock collector, original funding."""
import copy
import json

import pytest
from asset_operator_fixture import asset_control_case as asset_control_case
from image_operator_fixture import posts
from media_ledger_fixture import image_fee
from test_image_receipts import image_bytes
from test_image_runtime_continuation import prepared_runtime_case

from core.creator.store import CreatorError, digest
from core.execution import image_operator, image_runtime_operator
from core.execution.image_runtime_control import RuntimeContinuedImageControl
from core.execution.media_ledger import has_pending_fees, has_pending_unknown, records
from core.execution.scope_budget import scope_envelope


def test_runtime_review_is_off_and_rejects_implicit_or_dispatch_arguments(monkeypatch, capsys):
    monkeypatch.setenv("LEONTV_IMAGE_CONTROL_FILE", "/private/fictional-implicit-control")
    monkeypatch.setattr(image_runtime_operator, "read_image_control", lambda *a, **k: pytest.fail("implicit control"))
    assert image_runtime_operator.main([]) == 0
    assert json.loads(capsys.readouterr().out)["mode"] == "off"
    for args in [["start"], ["activate", "--quote", "a" * 32], ["status", "--parent-ledger", "/private/secret"]]:
        assert image_runtime_operator.main(args) == 1
        value = capsys.readouterr()
        assert value.err == "" and "/private/secret" not in value.out
        assert json.loads(value.out)["paid_calls"] == 0


def test_same_original_four_shot_run_completes_three_new_attempts_without_refunding_unknown(asset_control_case, monkeypatch, capsys):
    import asset_operator_fixture

    settings = asset_operator_fixture.ReceiptCaptureSettings

    def four_shot_settings(*args, **kwargs):
        if kwargs.get("worker_factory") == "empirical_product_fixture:make_transport":
            kwargs["worker_factory"] = "four_shot_runtime_fixture:make_transport"
        return settings(*args, **kwargs)

    monkeypatch.setattr(asset_operator_fixture, "ReceiptCaptureSettings", four_shot_settings)
    case = prepared_runtime_case(asset_control_case)
    assert len(case.manifest["frames"]) == 4
    monkeypatch.setattr(image_runtime_operator, "BACKEND", case.backend)
    native_before_cli = case.env.runtime.store.path("state.json").read_bytes()
    common = ["--control", str(case.path)]
    assert image_runtime_operator.main(["quote", *common, "--valid-until", case.runtime_valid_until]) == 0
    metadata = json.loads(capsys.readouterr().out)
    assert metadata["replayed"] is True and metadata["dispatch_available"] is False
    assert image_runtime_operator.main(["activate", *common, "--quote", case.runtime_quote["id"],
        "--disclosure-hash", case.runtime_quote["disclosure_hash"], "--consent"]) == 0
    metadata = json.loads(capsys.readouterr().out)
    assert metadata["replayed"] is True and metadata["current_authority_valid"] is True
    assert str(case.root) not in json.dumps(metadata) and case.unknown["id"] not in json.dumps(metadata)
    assert case.env.runtime.store.path("state.json").read_bytes() == native_before_cli
    before = case.env.runtime.store.read_only()
    state_path = case.env.runtime.store.path("state.json")
    state_before = json.loads(state_path.read_text())
    unknown = copy.deepcopy(case.unknown)
    account = case.control.document.budget_anchor["account_scope_id"]
    envelope = scope_envelope(before, account)
    parent_before = case.server_parent_path.read_bytes()
    http_before = len(posts(case))
    control = RuntimeContinuedImageControl(case.control, case.runtime_activation["id"])
    targets = case.runtime_authority["remaining_jobs"]
    assert [target["planned_submission_index"] for target in targets] == [3, 4, 5]
    completed = []

    def no_real_credential():
        pytest.fail("Synthetic continuation requested a real credential")

    with image_operator.build_image_session(control, receipt_capture=case.settings, credential_loader=no_real_credential) as session:
        case.media = session.media
        for target in targets:
            operation = session.media.reserve_submit(case.task["id"], case.grant["id"], target["shot_id"],
                key="four-shot-runtime-" + str(target["planned_submission_index"]))["operation"]
            assert operation["descriptor"]["kind"] == "controlled_image_http_v2"
            assert operation["submission_index"] == target["planned_submission_index"]
            assert operation["descriptor"]["permit"]["replacement_of"] == target["replacement_of"]
            started = session.media.submit(case.task["id"], operation["id"])["operation"]
            assert started["status"] == "accepted"
            if target["planned_submission_index"] == 3:
                status = image_operator._status(control, operation["id"])
                assert status["dispatch_available"] is False and status["global_unknown_retained"] is True
                assert status["blocked_reason"] == "MEDIA_PLATFORM_FEE_REQUIRED"
            image_fee(case, started)
            query = session.media.query(case.task["id"], operation["id"], key="four-shot-query-" + operation["id"])["query"]
            assert query["status"] == "completed"
            session.media.record_delivery(case.task["id"], operation["id"], query["id"], image_bytes())
            completed.append((operation, query))
        with pytest.raises(CreatorError):
            session.media.reserve_submit(case.task["id"], case.grant["id"], targets[-1]["shot_id"], key="forbidden-sixth-call")
    after = case.env.runtime.store.read_only()
    assert len(records(after, "operation")) == 5 and len(records(after, "delivery")) == 4
    assert after["media"]["operation:" + unknown["id"]] == unknown
    assert has_pending_unknown(after, account) and has_pending_fees(after, account)
    assert scope_envelope(after, account) == envelope and case.server_parent_path.read_bytes() == parent_before
    assert all(digest(after[n]) == digest(v) for n, v in before.items() if n != "media")
    assert all(digest(after["media"][k]) == digest(v) for k, v in before["media"].items())
    assert json.loads(state_path.read_text())["frames"][:len(state_before["frames"])] == state_before["frames"]
    assert len(posts(case)) == http_before + 6
    third = completed[1][0]["descriptor"]["reference_bindings"]
    assert any(binding["source_operation_id"] == completed[0][0]["id"] for binding in third)
    frozen = state_path.read_bytes()
    assert image_operator._status(control)["blocked_reason"] == "SCOPE_FINISHED"
    with image_operator.build_image_session(control, receipt_capture=case.settings, credential_loader=no_real_credential) as session:
        for operation, query in completed:
            assert session.media.submit(case.task["id"], operation["id"])["replayed"]
            assert session.media.query(case.task["id"], operation["id"], key="four-shot-query-" + operation["id"])["replayed"]
            assert session.media.recover(case.task["id"], query["id"])["replayed"]
    assert state_path.read_bytes() == frozen and len(posts(case)) == http_before + 6
    assert case.server_parent_path.read_bytes() == parent_before
