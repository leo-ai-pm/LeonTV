"""A fresh original-store snapshot per v2 decision, never per nested check."""
import copy

import pytest
from asset_operator_fixture import asset_control_case as asset_control_case
from image_operator_fixture import posts
from test_image_runtime_continuation import prepared_runtime_case

from core.creator.store import CreatorError
from core.execution import image_capture, image_operator
from core.execution import image_runtime_continuation as continuation
from core.execution.image_runtime_control import RuntimeContinuedImageControl
from core.execution.store import ExecutionStore


def test_v2_capture_reads_once_for_each_independent_credential_boundary(asset_control_case, monkeypatch):
    case = prepared_runtime_case(asset_control_case)
    control = RuntimeContinuedImageControl(case.control, case.runtime_activation["id"])
    target = case.runtime_authority["remaining_jobs"][0]
    original_read = ExecutionStore.read_only
    original_verify = type(case.control)._verified
    original_operator_stage = image_operator.capture_stage
    original_capture_stage = image_capture.capture_stage
    reads, verifies, stages = [], [], {}

    def read(store, *args, **kwargs):
        if store.root == case.env.runtime.store.root:
            reads.append(1)
        return original_read(store, *args, **kwargs)

    def verify(base, *args, **kwargs):
        verifies.append(kwargs.get("for_dispatch"))
        return original_verify(base, *args, **kwargs)

    def stage(name, original):
        original(name)
        stages[name] = (len(reads), len(verifies))

    parent = case.server_parent_path.read_bytes()
    prior_http = len(posts(case))
    with image_operator.build_image_session(control, receipt_capture=case.settings) as session:
        operation = session.media.reserve_submit(case.task["id"], case.grant["id"], target["shot_id"],
            key="snapshot-boundary-submit")["operation"]
        monkeypatch.setattr(ExecutionStore, "read_only", read)
        monkeypatch.setattr(type(case.control), "_verified", verify)
        monkeypatch.setattr(image_operator, "capture_stage", lambda name: stage(name, original_operator_stage))
        monkeypatch.setattr(image_capture, "capture_stage", lambda name: stage(name, original_capture_stage))
        result = session.media.submit(case.task["id"], operation["id"])["operation"]
        assert result["status"] == "accepted"
        measured = {name: tuple(end - start for start, end in zip(stages[first], stages[last], strict=True))
            for name, first, last in (
                ("before_key", "control_before_key", "deadline_before_key"),
                ("after_key", "control_after_key", "deadline_after_key"),
                ("collector", "image_authority", "image_credential"))}
        assert measured == {name: (1, 1) for name in ("before_key", "after_key", "collector")}
        assert verifies and all(value is False for value in verifies)
        frozen = session.media.store.path("state.json").read_bytes()
        assert session.media.submit(case.task["id"], operation["id"])["replayed"]
        assert session.media.store.path("state.json").read_bytes() == frozen
    assert len(posts(case)) == prior_http + 1
    assert case.server_parent_path.read_bytes() == parent


def test_current_snapshot_is_fresh_and_supplied_snapshot_cannot_replace_actual_state(asset_control_case, monkeypatch):
    case = prepared_runtime_case(asset_control_case)
    control = RuntimeContinuedImageControl(case.control, case.runtime_activation["id"])
    original = type(case.control)._verified
    calls = []

    def checked(base, *args, **kwargs):
        calls.append(kwargs.get("for_dispatch"))
        return original(base, *args, **kwargs)

    monkeypatch.setattr(type(case.control), "_verified", checked)
    payload, authority = control.verified_current()
    assert calls == [False] and authority == case.runtime_authority
    forged = copy.deepcopy(payload)
    forged["media"].pop("operation:" + case.unknown["id"])
    with pytest.raises(CreatorError):
        continuation.verify_current(case.control, forged, case.runtime_activation["id"])
    assert calls == [False, False]
    # Returned mutable dictionaries are data, never cached current authority.
    payload["media"].clear()
    authority["additional_paid_calls"] = 1
    actual, current = control.verified_current()
    assert calls == [False, False, False]
    assert actual["media"]["operation:" + case.unknown["id"]] == case.unknown
    assert current == case.runtime_authority
