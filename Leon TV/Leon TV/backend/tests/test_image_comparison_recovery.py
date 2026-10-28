"""Crash injection at a real call boundary; no authority/ledger substitution."""

import sys

import pytest
from asset_operator_fixture import asset_control_case as asset_control_case


def crash_before(function, action):
    previous = sys.getprofile()

    def profile(frame, event, arg):
        if event == "call" and frame.f_code is function.__code__:
            sys.setprofile(previous)
            raise SystemExit("SYNTHETIC_PROCESS_LOSS_BEFORE_CALL")

    try:
        sys.setprofile(profile)
        with pytest.raises(SystemExit):
            action()
    finally:
        sys.setprofile(previous)


def late_ready_fixture(capture, record):
    """Synthetic historical collector: timely raw, ready at response cutoff+1s.

    This constructs input evidence, not permission. The unchanged real native
    journal/authority is independently verified by the recovery call under test.
    """
    import base64
    import hashlib
    import json
    import os
    from datetime import timedelta

    from core.creator.store import digest
    from core.execution.image_policy import FrozenImageRequest
    from core.execution.image_response_lifecycle import response_deadline
    from core.execution.policy import now
    from core.execution.receipts import seal, timestamp

    request = FrozenImageRequest.from_frozen(record["descriptor"])
    binding = request.binding()
    cutoff = response_deadline(binding)
    ready_time = cutoff + timedelta(seconds=1)
    assert ready_time <= now(), "Use an already elapsed synthetic permit; do not relax the clock"
    operation = capture.receipts.operation(record["id"])
    if operation.root.exists():
        raw, inspection = capture.ready_raw(request)
        assert raw is not None and inspection["durable_confirmation_at"] == ready_time.isoformat()
        return
    operation.root.mkdir(parents=True, mode=0o700)
    claim_time = timestamp(record["capture_started_at"]) + timedelta(microseconds=1)
    assert claim_time < timestamp(binding["deadline_at"])
    operation.write_new(
        "claim.json",
        seal(
            {
                "version": 2,
                "binding": binding,
                "binding_hash": digest(binding),
                "payload_hash": "a" * 64,
                "capability_hash": "b" * 64,
                "observer_until": cutoff.isoformat(),
                "cleanup_cutoff": (cutoff + timedelta(seconds=5)).isoformat(),
                "observer_pid": os.getpid(),
                "created_at": claim_time.isoformat(),
            },
            "claim_hash",
        ),
    )
    body = json.dumps({"code": 0, "data": {"id": "SYNTHETIC-LATE-TASK"}}).encode()
    raw = {
        "http_status": 200,
        "headers": {"x-request-id": "SYNTHETIC-LATE-POST"},
        "body_base64": base64.b64encode(body).decode(),
        "received_at": (timestamp(binding["deadline_at"]) + timedelta(seconds=1)).isoformat(),
    }
    identity = {
        "binding_hash": digest(binding),
        "body_sha256": hashlib.sha256(body).hexdigest(),
        "http_status": 200,
        "headers": raw["headers"],
    }
    identifier = digest(identity)
    envelope = seal(
        {
            "version": 1,
            "record_id": identifier,
            "binding": binding,
            **identity,
            "raw": raw,
            "commit_started_at": (cutoff - timedelta(seconds=1)).isoformat(),
        },
        "envelope_hash",
    )
    operation.write_new(identifier + ".record.json", envelope)
    commit = seal(
        {
            "version": 1,
            "record_id": identifier,
            "binding_hash": digest(binding),
            "envelope_hash": envelope["envelope_hash"],
            "record_durable_at": cutoff.isoformat(),
        },
        "commit_hash",
    )
    operation.write_new(identifier + ".commit.json", commit)
    operation.write_new(
        identifier + ".ready.json",
        seal(
            {
                "version": 1,
                "record_id": identifier,
                "binding_hash": digest(binding),
                "commit_hash": commit["commit_hash"],
                "durable_confirmation_at": ready_time.isoformat(),
            },
            "ready_hash",
        ),
    )
    raw, inspection = capture.ready_raw(request)
    assert raw is not None and inspection["status"] == "candidate_found"


def test_late_ready_recovery_preserves_evidence_and_stops(asset_control_case, monkeypatch):
    """Full native authority; optional previously built synthetic history speeds reruns."""
    import json
    import os
    import time
    from pathlib import Path
    from types import SimpleNamespace

    from core.execution import image_comparison as c
    from core.execution.image_capture import ControlledImageCapture
    from core.execution.image_comparison_runtime import ComparisonExecution
    from core.execution.image_control import read_image_control
    from core.execution.image_policy import FrozenImageRequest
    from core.execution.image_response_lifecycle import response_deadline
    from core.execution.policy import now
    from core.execution.receipts import ReceiptCaptureSettings
    from core.execution.store import validate_state

    source = os.environ.get("D45_LATE_SOURCE")
    if source:
        path = Path(source).resolve()
        assert "synthetic-fixtures" in path.parts
        control = read_image_control(path, path.parent / "public-backend")
        assert control.document.policy["validation_only"] is True
        assert control.document.policy["origin"] == "https://synthetic.invalid"
        state_path = control.roots["execution"] / "state.json"
        state = json.loads(state_path.read_text())
        stop = next(
            i
            for i, frame in enumerate(state["frames"])
            if any(
                r["kind"] == c.OPERATION and r["status"] == "submitting"
                for r in frame["delta"].get("media", {}).values()
            )
        )
        state["frames"] = state["frames"][: stop + 1]
        state.update(head_revision=len(state["frames"]), head_hash=state["frames"][-1]["hash"])
        payload = validate_state(state)
        state_path.write_text(json.dumps(state))
        record = c.records(payload, c.OPERATION)[0]
        case = SimpleNamespace(
            control=control,
            activation_id=record["activation_id"],
            settings=ReceiptCaptureSettings(worker_factory="image_tool_fixture:make_transport"),
        )
        execution = ComparisonExecution(
            case.control,
            case.activation_id,
            receipt_capture=case.settings,
            credential_loader=lambda: pytest.fail("late recovery loaded key"),
        )
    else:
        from test_image_comparison_native import original_five_case

        original = original_five_case(asset_control_case, monkeypatch)
        q = c.quote_comparison(original.control)["quote"]
        h = q["proposal"]["proposal_hash"]
        intent = c.prepare_comparison(original.control, q["id"], proposal_hash=h, consent=True)[
            "intent"
        ]
        activation = c.activate_comparison(
            original.control, intent["id"], proposal_hash=h, consent=True
        )["activation"]
        execution = ComparisonExecution(
            original.control,
            activation["id"],
            receipt_capture=original.settings,
            credential_loader=lambda: "SYNTHETIC-KEY",
        )
        record = execution.reserve(key="late-ready-single-A")["operation"]
        crash_before(ControlledImageCapture.capture, lambda: execution.submit(record["id"]))
        record = execution.store.read_only()["media"][c.OPERATION + ":" + record["id"]]
    cutoff = response_deadline(FrozenImageRequest.from_frozen(record["descriptor"]).binding())
    while (cutoff - now()).total_seconds() + 1 >= 0:
        time.sleep(0.1)
    late_ready_fixture(execution.capture, record)
    import hashlib

    root = execution.store.root / "media-receipts" / record["id"]

    def inventory():
        return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in root.iterdir()}

    before = inventory()
    result = execution.recover(record["id"])["operation"]
    assert result["status"] == "unknown" and result["raw"] is result["receipt"] is None
    stable = execution.store.path("state.json").read_bytes()
    assert execution.recover(record["id"])["replayed"]
    assert execution.submit(record["id"])["replayed"]
    assert execution.store.path("state.json").read_bytes() == stable and inventory() == before
