"""One original native history; supplier only is synthetic. No private inputs."""
import copy
import json
import os
import sys
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest
from asset_operator_fixture import asset_control_case as asset_control_case
from test_image_comparison_native import original_five_case

from core.creator.store import CreatorError, digest
from core.execution import image_comparison as base
from core.execution import image_comparison_observation as contract
from core.execution.image_comparison_observation_runtime import ComparisonObservationExecution
from core.execution.image_comparison_runtime import ComparisonExecution
from core.execution.image_control import read_image_control
from core.execution.image_policy import FrozenImageRequest, timestamp
from core.execution.media_ledger import records
from core.execution.policy import now
from core.execution.receipts import ReceiptCaptureSettings
from core.execution.scope_budget import scope_envelope
from core.execution.store import ExecutionStore, validate_state


def accepted_case(asset_control_case, monkeypatch):
    cached = os.environ.get("D46_SYNTHETIC_SOURCE")
    if cached:
        path = Path(cached).resolve()
        assert "synthetic-fixtures" in path.parts
        control = read_image_control(path, path.parent / "public-backend")
        assert control.document.policy["validation_only"] is True and control.document.policy["origin"] == "https://synthetic.invalid"
        return control
    case = original_five_case(asset_control_case, monkeypatch)
    q = base.quote_comparison(case.control, valid_until=(now() + timedelta(hours=1)).isoformat())["quote"]
    intent = base.prepare_comparison(case.control, q["id"], proposal_hash=q["proposal"]["proposal_hash"], consent=True)["intent"]
    a = base.activate_comparison(case.control, intent["id"], proposal_hash=q["proposal"]["proposal_hash"], consent=True)["activation"]
    settings = replace(case.settings, worker_factory="image_comparison_observation_fixture:make_transport")
    execution = ComparisonExecution(case.control, a["id"], receipt_capture=settings, credential_loader=lambda: "SYNTHETIC")
    op = execution.reserve(key="D46-historical-A")["operation"]
    op = execution.submit(op["id"])["operation"]
    assert op["status"] == "accepted" and op["receipt"]["request_id_status"] == "missing"
    return case.control


def test_native_observation_same_account_fee_GET_artifact_and_guards(asset_control_case, monkeypatch, tmp_path):
    control = accepted_case(asset_control_case, monkeypatch)
    assert control.document.policy["validation_only"] is True and control.document.policy["origin"] == "https://synthetic.invalid"
    store = ExecutionStore(control.roots["execution"])
    before = store.read_only()
    op = records(before, base.OPERATION)[0]
    old_frames = json.loads(store.path("state.json").read_text())["frames"]
    from core.execution.image_continuation_parent_io import server_parent_path

    parent_path = server_parent_path(control)
    parent = parent_path.read_bytes()
    envelope = scope_envelope(before, control.document.budget_anchor["account_scope_id"])
    checkpoint = tmp_path / "accepted-synthetic-source.json"
    checkpoint.write_text(json.dumps({"control": str(control.path), "operation_id": op["id"], "native_sha": base.sha(store.path("state.json").read_bytes())}))
    if os.environ.get("D46_PREPARE_ONLY") == "1":
        return
    # Expired old authority rejects without any write; only its clock seam is
    # synthetic. The subsequent native collector still uses real wall time.
    with monkeypatch.context() as clock:
        clock.setattr(base, "now", lambda: timestamp(op["descriptor"]["policy"]["valid_until"]) + timedelta(seconds=1))
        with pytest.raises(CreatorError):
            ComparisonExecution(control, op["activation_id"])._fresh()
    stable = store.path("state.json").read_bytes()
    assert before == store.read_only() and stable == store.path("state.json").read_bytes()
    # Current-source drift invalidates old approval; new disclosure must freeze
    # this changed synthetic code, without editing its old quote/activation.
    code = control.backend_root / next(iter(control.document.code_manifest))
    code.write_bytes(code.read_bytes() + b"\n# SYNTHETIC D46 source version\n")
    with pytest.raises(CreatorError):
        ComparisonExecution(control, op["activation_id"])._fresh()
    until = (now() + timedelta(hours=2)).isoformat()
    q = contract.quote_observation(control, op["id"], valid_until=until)["quote"]
    saved = store.path("state.json").read_bytes()
    for consent in (False, 1, "true", None):
        with pytest.raises(CreatorError):
            contract.activate_observation(control, q["id"], disclosure_hash=q["disclosure_hash"], consent=consent)
    assert saved == store.path("state.json").read_bytes()
    a = contract.activate_observation(control, q["id"], disclosure_hash=q["disclosure_hash"], consent=True)["activation"]
    settings = ReceiptCaptureSettings(worker_factory="image_comparison_observation_fixture:make_transport",
        worker_options={"counter": str(tmp_path / "observer-http.jsonl"), "unique_tasks": True})
    observer = ComparisonObservationExecution(control, a["id"], receipt_capture=settings, credential_loader=lambda: "SYNTHETIC")
    with pytest.raises(CreatorError):
        observer.query(op["id"], key="unfunded-fee-GET")
    assert not (tmp_path / "observer-http.jsonl").exists()
    from test_image_fees import export, observed, row_for
    from test_task_image_fees import task_row, task_source_for

    request = FrozenImageRequest.from_frozen(op["descriptor"])
    source = before["media"]["scope:" + op["scope_id"]]["fee_source"]
    account = export(row_for(request, op["raw"], request_id="SYNTHETIC-ACCOUNT-ONLY"))
    pinned = task_source_for(request, account, fee_source=source)
    tasks = export(task_row(request, op["raw"], task_id=op["receipt"]["accepted_task_id"]))
    for bad in (export(task_row(request, op["raw"], task_id="SYNTHETIC-WRONG-TASK")),
                export(task_row(request, op["raw"], user_id=2, task_id=op["receipt"]["accepted_task_id"]))):
        with pytest.raises(CreatorError):
            observer.observe_task_fee(op["id"], bad, account, pinned, observed_at=observed(op["raw"]))
    fee = observer.observe_task_fee(op["id"], tasks, account, pinned, observed_at=observed(op["raw"]))["fee"]
    assert fee["candidate"]["platform_fee_cny"] == "0.040000" and fee["candidate"]["risk_released"] is False
    stable = store.path("state.json").read_bytes()
    assert observer.observe_task_fee(op["id"], tasks, account, pinned,
                                    observed_at=(timestamp(observed(op["raw"])) + timedelta(seconds=3)).isoformat())["replayed"]
    assert store.path("state.json").read_bytes() == stable
    # The existing whole-account owner gate sees the same identity across
    # ordinary and diagnostic fee namespaces; local IDs do not grant ownership.
    from core.execution.scope_budget import _validate_fee_owners

    duplicate = copy.deepcopy(store.read_only())
    forged_fee = copy.deepcopy(fee)
    forged_fee["kind"] = "fee"
    forged_fee["operation_id"] = "0" * 32
    duplicate["media"]["fee:" + forged_fee["id"]] = forged_fee
    with pytest.raises(CreatorError):
        _validate_fee_owners(duplicate)
    # Independent negative branches share only the exact synthetic baseline.
    # Save each resulting journal before restoring the lab snapshot. This is
    # never available for private/live inputs or a product recovery operation.
    branch_base = store.path("state.json").read_bytes()
    assert control.document.policy["validation_only"] is True
    code_bytes = code.read_bytes()
    # A newly added original collector file invalidates even an otherwise
    # unchanged observation. The exact original files are never edited.
    collector_extra = store.root / "media-receipts" / op["id"] / ("f" * 64 + ".record.json")
    assert not collector_extra.exists()
    collector_extra.write_text('{"SYNTHETIC":"incomplete"}')
    collector_extra.chmod(0o600)
    try:
        with pytest.raises(CreatorError):
            contract.verified_current(control, a["id"])
    finally:
        collector_extra.unlink()
    assert store.path("state.json").read_bytes() == branch_base

    def drift_during_key():
        code.write_bytes(code_bytes + b"\n# SYNTHETIC post-Key source drift\n")
        return "SYNTHETIC"

    drifting = ComparisonObservationExecution(control, a["id"], receipt_capture=settings, credential_loader=drift_during_key)
    with pytest.raises(CreatorError):
        drifting.query(op["id"], key="stale-post-Key-branch")
    code.write_bytes(code_bytes)
    stale_query = records(store.read_only(), base.QUERY)[0]
    assert not (tmp_path / "observer-http.jsonl").exists()
    assert drifting.recover(stale_query["id"])["query"]["status"] == "unknown"
    stopped = store.path("state.json").read_bytes()
    with pytest.raises(CreatorError):
        drifting.query(op["id"], key="new-key-cannot-clear-unknown")
    assert stopped == store.path("state.json").read_bytes()
    (tmp_path / "stale-post-Key-branch-state.json").write_bytes(stopped)
    store.path("state.json").write_bytes(branch_base)
    from test_image_comparison_recovery import crash_before

    from core.execution.image_capture import ControlledImageCapture

    crash_before(ControlledImageCapture.capture, lambda: observer.query(op["id"], key="before-claim-branch"))
    abandoned = records(store.read_only(), base.QUERY)[0]
    assert abandoned["status"] == "submitting" and not (store.root / "media-receipts" / abandoned["id"]).exists()
    assert observer.recover(abandoned["id"])["query"]["status"] == "unknown"
    stopped = store.path("state.json").read_bytes()
    assert observer.recover(abandoned["id"])["replayed"]
    assert stopped == store.path("state.json").read_bytes()
    (tmp_path / "before-claim-branch-state.json").write_bytes(stopped)
    store.path("state.json").write_bytes(branch_base)
    with pytest.raises(CreatorError):
        observer.query("0" * 32, key="wrong-local-A")
    boundaries = []
    original_profile = sys.getprofile()

    def profile(frame, event, arg):
        if event == "call" and frame.f_code is ComparisonObservationExecution._capture_guard.__code__:
            boundaries.append(1)

    sys.setprofile(profile)
    from datetime import datetime, timezone

    entry = datetime.now(timezone.utc)
    try:
        query = observer.query(op["id"], key="D46-only-GET")["query"]
    finally:
        sys.setprofile(original_profile)
    assert query["status"] == "completed" and query["query_index"] == 1
    assert len(boundaries) == 3
    from core.execution.receipts import ReceiptStore

    request_get = FrozenImageRequest.from_frozen(query["descriptor"])
    claim = ReceiptStore(store.root / "media-receipts").operation(query["id"]).claim(request_get.binding())
    (tmp_path / "synthetic-query-timing.json").write_text(json.dumps({
        "ENTRY_to_claim_seconds": (timestamp(claim["created_at"]) - entry).total_seconds(),
        "ISSUE_to_claim_seconds": (timestamp(claim["created_at"]) - timestamp(request_get.frozen()["permit"]["issued_at"])).total_seconds(),
        "fresh_credential_boundaries": 3, "synthetic_not_real_SLA": True,
    }))
    assert len((tmp_path / "observer-http.jsonl").read_text().splitlines()) == 1
    stable = store.path("state.json").read_bytes()
    assert observer.query(op["id"], key="D46-only-GET")["replayed"]
    assert observer.recover(query["id"])["replayed"]
    with pytest.raises(CreatorError):
        observer.query(op["id"], key="different-key-no-second-GET")
    assert store.path("state.json").read_bytes() == stable
    from test_image_receipts import image_bytes

    artifact = observer.record_artifact(op["id"], query["id"], image_bytes())["artifact"]
    stable = store.path("state.json").read_bytes()
    assert observer.record_artifact(op["id"], query["id"], image_bytes())["replayed"]
    assert store.path("state.json").read_bytes() == stable
    assert artifact["purpose"] == base.PURPOSE and artifact["artifact"]["visual_quality"] == "not_reviewed"
    after = store.read_only()
    assert parent_path.read_bytes() == parent and scope_envelope(after, control.document.budget_anchor["account_scope_id"]) == envelope
    assert json.loads(stable)["frames"][:len(old_frames)] == old_frames
    assert all(after[n] == v for n, v in before.items() if n != "media")
    assert all(after["media"][k] == v for k, v in before["media"].items() if k != base.OPERATION + ":" + op["id"])
    assert contract.immutable_operation(after["media"][base.OPERATION + ":" + op["id"]]) == contract.immutable_operation(op)
    assert len(records(after, "delivery")) == len(records(before, "delivery")) == 2
    assert sum(r["status"] == "unknown" for r in records(after, "operation")) == 3
    assert records(after, base.OPERATION)[0]["status"] == "accepted"
    status = contract.status_summary(control, a["id"])
    assert status["accepted"] and status["completed"] and status["received"]
    assert status["artifact"] == "recorded" and status["platform_observed_cny"] == "0.040000"
    assert not status["final_bill_confirmed"] and status["quality"] == "not_reviewed" and status["human_acceptance"] == "pending"
    assert status["formal_delivered"] == 2 and status["post_cap"] == 0
    stable = store.path("state.json").read_bytes()
    with monkeypatch.context() as expired:
        expired.setattr(contract, "now", lambda: timestamp(until) + timedelta(seconds=1))
        for action in (
            lambda: observer.observe_task_fee(op["id"], tasks, account, pinned, observed_at=observed(op["raw"])),
            lambda: observer.query(op["id"], key="expired-no-new-GET"),
            lambda: observer.recover(query["id"]),
            lambda: observer.record_artifact(op["id"], query["id"], image_bytes()),
        ):
            with pytest.raises(CreatorError):
                action()
        assert not contract.status_summary(control, a["id"])["current_authority_valid"]
    assert stable == store.path("state.json").read_bytes()
    # A fully rehashed late activation / altered immutable source is rejected by
    # original validate_state, not a private verifier or patched permission.
    forged = copy.deepcopy(json.loads(stable))
    frame = next(f for f in forged["frames"] if any(r["kind"] == contract.ACTIVATION for r in f["delta"].get("media", {}).values()))
    r = next(r for r in frame["delta"]["media"].values() if r["kind"] == contract.ACTIVATION)
    r["consent"] = 1
    previous = None
    for f in forged["frames"]:
        f["previous_hash"] = previous
        f["hash"] = digest({k: v for k, v in f.items() if k != "hash"})
        previous = f["hash"]
    forged["head_hash"] = previous
    with pytest.raises(CreatorError):
        validate_state(forged)
