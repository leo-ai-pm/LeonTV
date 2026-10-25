"""Independent native fee inspection probes; all receipts are fictional mock HTTP."""
import copy
import hashlib
import json
import socket
from datetime import timedelta
from pathlib import Path

import httpx
import pytest
from asset_operator_fixture import asset_control_case as asset_control_case
from asset_operator_fixture import private_json
from image_operator_fixture import authorize, images_case, posts
from image_tool_fixture import make_transport as original_transport
from test_image_fees import export, observed, row_for
from test_image_receipts import image_bytes

from core.creator.store import CreatorError, digest, utc_now
from core.execution import image_control, image_operator
from core.execution.asset_control import ENVELOPE_FIELDS
from core.execution.control import (
    CODE_CLOSURE_DIRS,
    REQUIRED_CODE_FILES,
    _call_weight,
    _code_closure,
)
from core.execution.fees import FeeInbox
from core.execution.image_control import read_image_control
from core.execution.image_fee_inspection import inspect_native
from core.execution.image_policy import FrozenImageRequest, MediaBudgetAnchor
from core.execution.media_runtime import MediaExecution
from core.execution.policy import money
from core.execution.receipts import ReceiptCaptureSettings, ReceiptStore, timestamp
from core.execution.runtime import ExecutionRuntime
from core.execution.store import ExecutionStore
from core.planner.runtime import PlannerRuntime


def missing_submit_identity_transport(options):
    """Keep the original task and GET identities; omit only the POST header."""
    original = original_transport(options)

    def handle(request):
        response = original.handle_request(request)
        if request.method == "POST":
            return httpx.Response(response.status_code, content=response.content, headers={})
        return response

    return httpx.MockTransport(handle)


def disk_state(case):
    """Inspect only the fictional fixture stores and its mock request counter."""
    result = {}
    for name, root in case.data["roots"].items():
        for path in Path(root).rglob("*"):
            if path.is_file():
                result[name + ":" + path.relative_to(root).as_posix()] = path.read_bytes()
    result["mock_requests"] = case.counter.read_bytes() if case.counter.exists() else None
    return result


def forbid_actions(patch):
    """Any diagnostic attempt to activate execution fails the test."""
    attempted = []

    def forbidden(*args, **kwargs):
        attempted.append("forbidden")
        raise AssertionError("Read-only inspection activated an execution or network path")

    for owner, names in (
        (image_operator, ("build_image_session", "tudou_credential")),
        (ExecutionRuntime, ("__init__", "get", "recover")),
        (PlannerRuntime, ("__init__", "get", "recover")),
        (MediaExecution, ("__init__", "get", "recover")),
        (ExecutionStore, ("transaction",)),
        (ReceiptStore, ("__init__",)),
        (FeeInbox, ("__init__",)),
        (socket, ("create_connection", "getaddrinfo")),
        (socket.socket, ("connect", "connect_ex")),
    ):
        for name in names:
            patch.setattr(owner, name, forbidden)
    return attempted


def assert_missing_native(result):
    assert result["native_status"] == "accepted"
    assert result["receipt_classification"] == "accepted"
    assert result["request_id_status"] == "missing"
    assert result["captured_standard_header_present"] is False
    assert result["body_request_id_present"] is False
    assert result["accepted_task_id_present"] is True
    assert result["fee_match_status"] == "missing_submit_request_id"
    assert result["fee_observed"] is False
    assert result["native_platform_observed_cny"] is None
    assert result["candidate_platform_fee_cny"] is None and result["candidate_overrun"] is None
    assert result["query_count"] == 1 and result["query_status_counts"] == {"completed": 1}
    assert result["delivery_present"] is True and result["quality_status"] == "not_reviewed"
    assert result["scope_risk_cny"] == "2.000000" and result["remaining_call_count"] == 1
    assert result["next_action"] == "await_exact_fee_evidence"
    assert result["read_only"] is True and result["new_dispatch_authorized"] is False
    assert result["import_authorized"] is False and type(result["automatic_followup_calls"]) is int
    assert result["automatic_followup_calls"] == 0
    assert result["upstream_other_request_id_presence"] == "unknown"


def test_missing_post_identity_stays_unbilled_after_native_delivery_and_historical_control_changes(asset_control_case, monkeypatch):
    case = images_case(asset_control_case)
    case.settings = ReceiptCaptureSettings(observation_wait_seconds=5,
        worker_factory="test_image_fee_inspection_native:missing_submit_identity_transport",
        worker_options={"counter": str(case.counter)})
    case.media.capture.settings = case.settings
    quote, grant = authorize(case)
    operation = case.media.reserve_submit(case.task["id"], grant["id"], "S01", key="inspection-native-reserve")["operation"]
    submitted = case.media.submit(case.task["id"], operation["id"])["operation"]
    assert submitted["status"] == "accepted" and submitted["receipt"]["request_id_status"] == "missing"
    assert submitted["receipt"]["submit_request_id"] is None and submitted["raw"]["headers"] == {}
    query = case.media.query(case.task["id"], operation["id"], key="inspection-native-query")["query"]
    assert query["status"] == "completed" and query["receipt"]["query_request_id"] is not None
    case.media.record_delivery(case.task["id"], operation["id"], query["id"], image_bytes())
    native = case.env.runtime.store.read_only()["media"]["operation:" + operation["id"]]
    assert native["fee_id"] is None and native["delivery_id"] is not None
    assert [request["method"] for request in posts(case)] == ["POST", "GET"]
    before = disk_state(case)
    control = read_image_control(case.path, case.backend)

    with monkeypatch.context() as patch:
        attempted = forbid_actions(patch)
        current = inspect_native(control, operation["id"])
        assert_missing_native(current)
        serialized = json.dumps(current, ensure_ascii=False)
        for private in (str(case.root), "https://", "body_base64", "bytes_base64", '"manifest":',
                        '"prompt":', '"wire":', "synthetic-image-task-1", query["receipt"]["query_request_id"]):
            assert private not in serialized

        # A historical reader remains useful in observe_only, after code drift
        # and after the generation policy expires; it never renews authority.
        case.data["mode"] = "observe_only"
        case.save()
        control = read_image_control(case.path, case.backend)
        (case.backend / "core/execution/image_control.py").write_text("# Fictional subsequent code drift.\n")
        expired_now = timestamp(case.policy["valid_until"]) + timedelta(seconds=1)
        patch.setattr(image_control, "now", lambda: expired_now)
        historical = inspect_native(control, operation["id"])
        assert_missing_native(historical)
        assert historical == current
        assert not attempted

    assert disk_state(case) == before
    payload = case.env.runtime.store.read_only()
    assert payload["media"]["operation:" + operation["id"]]["fee_id"] is None
    assert not any(record["kind"] == "fee" or record["kind"] == "claim" and record.get("owner") == "media"
        for record in payload["media"].values())
    assert payload["media"]["scope:" + quote["scope_id"]]["manifest_origin"] == case.origin


def foreign_scope_control(case):
    """Create a valid separate control allocation, without a second image call."""
    root = case.root / "separate-control"
    root.mkdir(mode=0o700)
    data, ledger = copy.deepcopy(case.data), copy.deepcopy(case.ledger)
    request = copy.deepcopy(case.request)
    count = case.policy["max_submissions"]
    opening = sum(record["upper_fen"] for record in ledger["operations"].values()) * 10000
    calls = sum(_call_weight(record) for record in ledger["operations"].values())
    anchor = {**data["budget_anchor"], "opening_risk_cny": money(opening), "opening_paid_call_count": calls,
        "account_limit_cny": money(opening + count * 1000000), "account_max_calls": calls + count}
    envelope = {"kind": "image_execution_scope_envelope_v1", **{name: anchor[name] for name in ENVELOPE_FIELDS}}
    envelope["envelope_hash"] = digest(envelope)
    request["scope_envelope_hash"] = envelope["envelope_hash"]
    selected = {"operation_key": "inspection-separate-image-allocation", "request": request,
        "request_sha256": digest(request), "upper_fen": count * 100, "status": "reserved",
        "provider_task_id": None, "actual_charge_fen": None}
    ledger["operations"][selected["operation_key"]] = selected
    if "reserved_upper_fen" in ledger:
        ledger["reserved_upper_fen"] = sum(record["upper_fen"] for record in ledger["operations"].values())
    begin = {"schema_version": 1, "kind": "image_execution_allocation_begin_v1",
        "operation_key": selected["operation_key"], "allocation_hash": digest(selected),
        "request_sha256": selected["request_sha256"], "status": "begun", "begun_at": utc_now()}
    anchor.update(parent_ledger_hash=private_json(root / "parent-ledger.json", ledger),
        parent_allocation_hash=digest(selected), parent_allocation_begin_hash=digest(begin))
    data["budget_anchor"] = MediaBudgetAnchor.model_validate({name: value for name, value in anchor.items()
        if name != "budget_anchor_hash"}).frozen()
    data["scope_envelope"] = envelope
    data["authority"].update(parent_ledger_path=str(root / "parent-ledger.json"), allocation_key=selected["operation_key"],
        allocation_begin_path=str(root / "begin.json"), allocation_begin_sha256=private_json(root / "begin.json", begin))
    path = root / "image-control.json"
    private_json(path, data)
    return read_image_control(path, case.backend)


def test_native_inspection_rejects_unknown_text_task_and_separate_image_scope_without_writes(asset_control_case, monkeypatch):
    case = images_case(asset_control_case)
    _, grant = authorize(case)
    operation = case.media.reserve_submit(case.task["id"], grant["id"], "S01", key="inspection-denial-reserve")["operation"]
    control = read_image_control(case.path, case.backend)
    separate = foreign_scope_control(case)
    assert separate.verify_authority(for_dispatch=True) is True
    native_text = next(iter(case.env.runtime.store.read_only()["operations"]))
    before = disk_state(case)

    with monkeypatch.context() as patch:
        attempted = forbid_actions(patch)
        for admitted_control, operation_id in ((control, "0" * 32), (control, native_text),
                (control, case.task["id"]), (separate, operation["id"])):
            with pytest.raises(CreatorError) as caught:
                inspect_native(admitted_control, operation_id)
            assert caught.value.code == "IMAGE_CONTROL_UNAVAILABLE"
            assert str(case.root) not in str(caught.value)
        assert not attempted

    assert disk_state(case) == before and posts(case) == []


def test_package_closure_cannot_dispatch_with_the_new_readonly_inspector_omitted():
    backend = Path(__file__).resolve().parents[1]
    names = set(REQUIRED_CODE_FILES)
    for directory in CODE_CLOSURE_DIRS:
        names.update(path.relative_to(backend).as_posix() for path in (backend / directory).rglob("*.py"))
    inspector = "core/execution/image_fee_inspection.py"
    assert inspector in names
    manifest = {name: hashlib.sha256((backend / name).read_bytes()).hexdigest() for name in names}
    _code_closure(backend, manifest)
    del manifest[inspector]
    with pytest.raises(CreatorError) as caught:
        _code_closure(backend, manifest)
    assert caught.value.code == "CONTROL_UNAVAILABLE"


def test_existing_fee_and_conflicting_direct_candidate_remain_separate_readonly_facts(asset_control_case, monkeypatch):
    case = images_case(asset_control_case)
    _, grant = authorize(case)
    operation = case.media.reserve_submit(case.task["id"], grant["id"], "S01", key="inspection-existing-fee-reserve")["operation"]
    submitted = case.media.submit(case.task["id"], operation["id"])["operation"]
    assert submitted["status"] == "accepted" and submitted["receipt"]["request_id_status"] == "direct"
    request = FrozenImageRequest.from_frozen(submitted["descriptor"])
    row = row_for(request, submitted["raw"], request_id=submitted["receipt"]["submit_request_id"], quota=20000,
        token_name=case.source["token_name"], group=case.source["group"])
    original_export = export(row)
    recorded = case.media.observe_fee(case.task["id"], operation["id"], original_export,
        observed_at=observed(submitted["raw"]))["operation"]
    payload = case.env.runtime.store.read_only()
    original_fee = copy.deepcopy(payload["media"]["fee:" + recorded["fee_id"]])
    assert original_fee["candidate"]["platform_fee_cny"] == "0.040000"
    different_export = export({**row, "quota": 1250000})
    control = read_image_control(case.path, case.backend)
    before = disk_state(case)

    with monkeypatch.context() as patch:
        attempted = forbid_actions(patch)
        same = inspect_native(control, operation["id"], export_bytes=original_export)
        assert same["native_status"] == "accepted" and same["fee_observed"] is True
        assert same["fee_match_status"] == "direct_match" and same["candidate_matches"] is True
        assert same["candidate_hash"] == original_fee["candidate"]["candidate_hash"]
        assert same["candidate_platform_fee_cny"] == same["native_platform_observed_cny"] == "0.040000"
        assert same["native_fee_candidate_conflict"] is False

        conflict = inspect_native(control, operation["id"], export_bytes=different_export)
        assert conflict["export_status"] == "valid" and conflict["candidate_matches"] is True
        assert conflict["fee_match_status"] == "evidence_conflict"
        assert conflict["candidate_platform_fee_cny"] == "2.500000" and conflict["candidate_overrun"] is True
        assert conflict["native_platform_observed_cny"] == "0.040000" and conflict["fee_observed"] is True
        assert conflict["candidate_hash"] != original_fee["candidate"]["candidate_hash"]
        assert conflict["native_fee_candidate_conflict"] is True
        assert conflict["next_action"] == "await_exact_fee_evidence"
        for report in (same, conflict):
            assert report["read_only"] is True and report["import_authorized"] is False
            assert report["new_dispatch_authorized"] is False and report["automatic_followup_calls"] == 0
        assert not attempted

    assert disk_state(case) == before
    after = case.env.runtime.store.read_only()
    assert after["media"]["operation:" + operation["id"]] == recorded
    assert after["media"]["fee:" + recorded["fee_id"]] == original_fee
    assert original_fee["candidate"]["overrun"] is False
    assert sum(record["kind"] == "fee" for record in after["media"].values()) == 1
    assert sum(record["kind"] == "claim" and record.get("owner") == "media" for record in after["media"].values()) == 1
    assert [request["method"] for request in posts(case)] == ["POST"]
