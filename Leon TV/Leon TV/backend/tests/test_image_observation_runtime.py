"""Synthetic original collector GET, delivery, and restart without a new POST."""
import copy
import json
from datetime import timedelta

import pytest
from asset_operator_fixture import asset_control_case as asset_control_case
from image_operator_fixture import posts
from test_image_observation_continuation import accepted_observation_source
from test_image_receipts import image_bytes

from core.creator.store import CreatorError, digest
from core.execution import image_observation_continuation as observation
from core.execution import image_operator
from core.execution.image_observation_control import ImageObservationControl
from core.execution.image_observation_policy import ImageObservationPermit
from core.execution.policy import now
from core.execution.scope_budget import scope_envelope


def test_observation_uses_current_code_and_original_collector_then_restarts_without_resubmitting(asset_control_case, monkeypatch):
    case = accepted_observation_source(asset_control_case)
    store = case.env.runtime.store
    parent = copy.deepcopy(case.accepted_operation)
    source_id = parent["id"]
    before = store.read_only()
    native_before = json.loads(store.path("state.json").read_text())
    parent_budget = case.server_parent_path.read_bytes()
    account = case.control.document.budget_anchor["account_scope_id"]
    envelope = scope_envelope(before, account)
    prior_http = len(posts(case))
    # A new code approval for observation must not rewrite the accepted POST.
    name = next(iter(case.control.document.code_manifest))
    (case.backend / name).write_text("# Explicitly reviewed fictional GET implementation.\n")
    q = observation.quote_observation(case.control, source_id, valid_until=(now() + timedelta(hours=6)).isoformat())["quote"]
    a = observation.activate_observation(case.control, q["id"], disclosure_hash=q["disclosure_hash"], consent=True)["activation"]
    control = ImageObservationControl(case.control, a["id"])
    original_verified = case.control.__class__._verified
    stages, reads = [], []
    original_stage = image_operator.capture_stage

    def checked(self, *args, **kwargs):
        reads.append(kwargs.get("for_dispatch"))
        return original_verified(self, *args, **kwargs)

    def stage(name):
        original_stage(name)
        stages.append((name, len(reads)))

    monkeypatch.setattr(case.control.__class__, "_verified", checked)
    monkeypatch.setattr(image_operator, "capture_stage", stage)
    with image_operator.build_image_session(control, receipt_capture=case.settings) as session:
        query = session.media.query(case.task["id"], source_id, key="explicit-observation-query-one")["query"]
        assert query["status"] == "completed"
        data = query["descriptor"]
        assert data["kind"] == "controlled_image_observation_http_v1" and data["method"] == "GET"
        assert "references" not in data and "reference_bindings" not in data and data["wire"] == ""
        assert data["parent_submit"] == parent["descriptor"] and data["submit_raw"] == parent["raw"]
        assert data["permit"]["paid_calls"] == 0 and data["permit"]["risk_cny"] == "0.000000"
        # Independent fresh reads remain on both sides of Key selection.
        counts = dict(stages)
        assert counts["deadline_before_key"] > counts["control_before_key"]
        assert counts["deadline_after_key"] > counts["credential_check"]
        assert len(reads) > counts["deadline_after_key"]
        for method in (session.media.quote, session.media.grant, session.media.reserve_submit, session.media.submit, session.media.revoke):
            with pytest.raises(CreatorError) as error:
                method()
            assert error.value.code == "IMAGE_OBSERVATION_GET_ONLY"
        session.media.record_delivery(case.task["id"], source_id, query["id"], image_bytes())
    after = store.read_only()
    updated = after["media"]["operation:" + source_id]
    assert updated == {**parent, "delivery_id": updated["delivery_id"]}
    assert updated["delivery_id"] is not None
    assert after["media"]["operation:" + case.unknown["id"]] == case.unknown
    assert all(digest(after[n]) == digest(v) for n, v in before.items() if n != "media")
    assert all(after["media"][k] == v for k, v in before["media"].items() if k != "operation:" + source_id)
    assert json.loads(store.path("state.json").read_text())["frames"][:len(native_before["frames"])] == native_before["frames"]
    assert scope_envelope(after, account) == envelope and case.server_parent_path.read_bytes() == parent_budget
    assert len(posts(case)) == prior_http + 1
    frozen = store.path("state.json").read_bytes()
    with image_operator.build_image_session(control, receipt_capture=case.settings) as session:
        assert session.media.query(case.task["id"], source_id, key="explicit-observation-query-one")["replayed"]
        assert session.media.recover(case.task["id"], query["id"])["replayed"]
        assert session.media.record_delivery(case.task["id"], source_id, query["id"], image_bytes())["replayed"]
    assert store.path("state.json").read_bytes() == frozen and len(posts(case)) == prior_http + 1


@pytest.mark.parametrize("field", ["schema_version", "paid_calls", "query_index", "observation_revision_sequence"])
def test_observation_counts_reject_boolean_normalization(field):
    # The before validator must reject literal normalization even before any
    # missing-field errors can conceal an integer/bool substitution.
    with pytest.raises(CreatorError):
        ImageObservationPermit.strict_counts({field: False})


def test_observation_cli_is_explicit_get_only_and_never_builds_a_submission_session(monkeypatch, capsys):
    monkeypatch.setattr(image_operator, "read_image_control", lambda *args: pytest.fail("must reject absent control"))
    assert image_operator.main(["--observation", "a" * 32, "status"]) == 1
    assert json.loads(capsys.readouterr().out)["automatic_followup_calls"] == 0
