"""One end-to-end CLI import over fictional native asset and image receipts."""
import copy
import json
import socket
from datetime import timedelta

import pytest
from asset_operator_fixture import asset_control_case as asset_control_case
from asset_operator_fixture import private_json
from image_operator_fixture import authorize, images_case, posts
from test_image_fees import export, row_for
from test_task_image_fees import task_row, task_source_for

from core.creator.store import CreatorError
from core.execution import image_control, image_operator
from core.execution.image_capture import ControlledImageCapture
from core.execution.image_control import read_image_control
from core.execution.image_policy import FrozenImageRequest
from core.execution.media_runtime import MediaExecution
from core.execution.receipts import ReceiptCaptureSettings, ReceiptStore, timestamp
from core.execution.runtime import ExecutionRuntime
from core.planner.runtime import PlannerRuntime


def test_explicit_task_fee_cli_reconciles_missing_post_id_without_executor_and_replays_without_writes(
        asset_control_case, monkeypatch, capsys):
    case = images_case(asset_control_case, profile=False)
    case.settings = ReceiptCaptureSettings(observation_wait_seconds=5,
        worker_factory="test_image_fee_inspection_native:missing_submit_identity_transport",
        worker_options={"counter": str(case.counter), "unique_tasks": True})
    case.media.capture.settings = case.settings
    _, grant = authorize(case)
    operation = case.media.reserve_submit(case.task["id"], grant["id"], "S01", key="task-fee-cli-reserve")["operation"]
    operation = case.media.submit(case.task["id"], operation["id"])["operation"]
    assert operation["status"] == "accepted" and operation["receipt"]["request_id_status"] == "missing"
    assert operation["receipt"]["submit_request_id"] is None and operation["raw"]["headers"] == {}
    request = FrozenImageRequest.from_frozen(operation["descriptor"])
    account_blob = export(row_for(request, operation["raw"], request_id="unrelated-account-witness",
        quota=17, token_name=case.source["token_name"], group=case.source["group"]))
    task_blob = export(task_row(request, operation["raw"], task_id=operation["receipt"]["accepted_task_id"],
        group=case.source["group"]))
    source = task_source_for(request, account_blob, fee_source=case.source)
    task_path, account_path = case.root / "task-observation.json", case.root / "account-witness.json"
    for path, blob in ((task_path, task_blob), (account_path, account_blob)):
        path.write_bytes(blob)
        path.chmod(0o600)
    source_path = case.root / "task-fee-source.json"
    private_json(source_path, source)
    observe_data = {**copy.deepcopy(case.data), "mode": "observe_only"}
    observe_path = case.root / "observe-control.json"
    private_json(observe_path, observe_data)
    original_control = case.path.read_bytes()
    original_execute = image_operator.execute
    attempts = []

    def forbidden(*args, **kwargs):
        attempts.append("forbidden")
        raise AssertionError("Fee import attempted executor, credential, capture, or network")

    def cli(control_path=case.path):
        code = image_operator.main(["task-fees-import", "--control", str(control_path),
            "--operation", operation["id"], "--source", str(source_path),
            "--account-export", str(account_path), str(task_path)])
        text = capsys.readouterr().out
        return code, json.loads(text), text

    # Neither a changed code closure nor expiry can become dispatch permission.
    # The original control remains useful only for importing a historical fact.
    (case.backend / "core/execution/image_operator.py").write_text("# Subsequent fictional code drift.\n")
    expired = timestamp(case.policy["valid_until"]) + timedelta(seconds=1)
    with monkeypatch.context() as patch:
        patch.setattr(image_operator, "BACKEND", case.backend)
        patch.setattr(image_operator, "build_image_session", forbidden)
        patch.setattr(image_operator, "execute", lambda control, command, args:
            original_execute(control, command, args, session_factory=forbidden))
        patch.setattr(image_operator, "tudou_credential", forbidden)
        patch.setattr(image_control, "now", lambda: expired)
        for owner, names in ((PlannerRuntime, ("__init__", "recover")),
                (ExecutionRuntime, ("__init__", "recover")), (MediaExecution, ("__init__", "recover")),
                (ControlledImageCapture, ("__init__", "capture")), (ReceiptStore, ("__init__",)),
                (socket, ("create_connection", "getaddrinfo")), (socket.socket, ("connect", "connect_ex"))):
            for name in names:
                patch.setattr(owner, name, forbidden)
        control = read_image_control(case.path, case.backend)
        assert control.verify_authority(for_dispatch=False) is True
        with pytest.raises(CreatorError):
            control.verify_authority(for_dispatch=True)
        code, result, text = cli()
        assert code == 0 and result["replayed"] is False
        assert result["platform_observed_cny"] == "0.040000"
        assert result["observation_kind"] == "platform_task_quota_observation"
        assert result["provider_bill_confirmed"] is False and result["risk_released"] is False
        assert result["automatic_followup_calls"] == 0
        assert result["operation"]["fee_observed"] is True
        for private in (str(case.root), "https://", "body_base64", "bytes_base64", '"prompt"',
                case.source["token_name"], case.source["group"], operation["receipt"]["accepted_task_id"],
                "unrelated-account-witness"):
            assert private not in text
        state = case.env.runtime.store.path("state.json").read_bytes()
        patch.setattr(image_operator, "utc_now", lambda: (expired + timedelta(days=1)).isoformat())
        code, replay, _ = cli()
        assert code == 0 and replay == {**result, "replayed": True}
        assert case.env.runtime.store.path("state.json").read_bytes() == state

        code, denied, _ = cli(observe_path)
        assert code == 1 and denied["error"] == "IMAGE_CONTROL_UNAVAILABLE"
        assert case.env.runtime.store.path("state.json").read_bytes() == state
        source_path.chmod(0o644)
        code, denied, _ = cli()
        assert code == 1 and "error" in denied
        assert case.env.runtime.store.path("state.json").read_bytes() == state
        assert attempts == []

    assert case.path.read_bytes() == original_control
    current = case.env.runtime.store.read_only()["media"]["operation:" + operation["id"]]
    assert current["status"] == operation["status"] == "accepted"
    assert current["raw"] == operation["raw"] and current["receipt"] == operation["receipt"]
    assert current["descriptor"] == operation["descriptor"]
    assert current["receipt"]["submit_request_id"] is None
    assert [item["method"] for item in posts(case)] == ["POST"]
