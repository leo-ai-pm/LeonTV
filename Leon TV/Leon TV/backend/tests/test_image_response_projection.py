"""Safe native response summaries; no new receipt, permission, key or HTTP."""
import copy
import json
import socket
from types import SimpleNamespace

import pytest
from test_image_policy import accepted_raw, make_query, make_submit
from test_image_receipts import query_raw, raw_for

from core.creator.store import CreatorError
from core.execution import (
    image_observation_continuation,
    image_operator,
    image_policy,
    image_receipts,
    image_runtime_control,
    media_ledger,
    store,
)
from core.execution.media_runtime import MediaExecution


@pytest.fixture(autouse=True)
def no_network_or_executor(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Read-only response projection must not dispatch or load credentials")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(image_operator, "tudou_credential", forbidden)
    monkeypatch.setattr(image_operator, "build_image_session", forbidden)
    monkeypatch.setattr(MediaExecution, "submit", forbidden)
    monkeypatch.setattr(MediaExecution, "query", forbidden)
    monkeypatch.setattr(MediaExecution, "reserve_submit", forbidden)


def started_record(request, *, kind="operation"):
    permit = request.frozen()["permit"]
    value = {"kind": kind, "id": permit["operation_id"], "scope_id": "a" * 32,
        "task_id": permit["task_id"], "status": "submitting", "capture_started_at": permit["issued_at"],
        "descriptor": request.frozen(), "raw": None, "receipt": None}
    if kind == "operation":
        value.update(shot_id=permit["shot_id"], grant_id=permit["grant_id"], fee_id=None, delivery_id=None)
    else:
        value.update(operation_id=request.frozen()["parent_submit"]["permit"]["operation_id"], query_index=1)
    return value


def diagnostic(record):
    before = copy.deepcopy(record)
    projected = image_operator._resource(record)
    assert record == before
    assert set(projected["response_diagnostic"]) == {
        "raw_captured", "http_status", "receipt_classification", "accepted_task_present", "retry_authorized"}
    assert projected["response_diagnostic"]["retry_authorized"] is False
    assert "fee_outcome" not in projected["response_diagnostic"]
    assert "authentication" not in projected["response_diagnostic"]
    return projected["response_diagnostic"]


def test_native_403_is_distinct_from_missing_response_without_changing_unknown():
    request = make_submit()
    captured = started_record(request)
    MediaExecution._adopt(captured, raw_for(request,
        {"code": "fail_to_fetch_task", "message": "", "data": None}, status=403))
    missing = {**started_record(request), "status": "unknown"}
    assert captured["status"] == missing["status"] == "unknown"
    assert diagnostic(captured) == {"raw_captured": True, "http_status": 403,
        "receipt_classification": "unconfirmed", "accepted_task_present": False, "retry_authorized": False}
    assert diagnostic(missing) == {"raw_captured": False, "http_status": None,
        "receipt_classification": None, "accepted_task_present": False, "retry_authorized": False}


@pytest.mark.parametrize("status", ["reserved", "submitting", "unknown"])
def test_no_native_raw_cannot_claim_http_response_or_cause(status):
    record = started_record(make_submit())
    record["status"] = status
    if status == "reserved":
        record["capture_started_at"] = None
    assert diagnostic(record) == {"raw_captured": False, "http_status": None,
        "receipt_classification": None, "accepted_task_present": False, "retry_authorized": False}


@pytest.mark.parametrize("http_status,code,classification", [
    (403, "fail_to_fetch_task", "unconfirmed"),
    (502, 502, "unconfirmed"),
    (200, "200", "unconfirmed"),
    (403, 403, "rejected"),
])
def test_diagnostic_preserves_decoder_classification_without_promoting_embedded_task(http_status, code, classification):
    request = make_submit()
    record = started_record(request)
    MediaExecution._adopt(record, raw_for(request,
        {"code": code, "data": {"id": "untrusted-embedded-task"}}, status=http_status))
    value = diagnostic(record)
    assert value["raw_captured"] is True and value["http_status"] == http_status
    assert value["receipt_classification"] == classification and value["accepted_task_present"] is False
    assert record["fee_id"] is None and record["delivery_id"] is None


def test_accepted_task_is_not_billing_confirmation_and_summary_does_not_share_mutable_data():
    request = make_submit()
    record = started_record(request)
    MediaExecution._adopt(record, accepted_raw(request))
    before = copy.deepcopy(record)
    projected = image_operator._resource(record)
    assert projected["status"] == "accepted" and projected["fee_observed"] is False
    assert projected["response_diagnostic"] == {"raw_captured": True, "http_status": 200,
        "receipt_classification": "accepted", "accepted_task_present": True, "retry_authorized": False}
    projected["response_diagnostic"]["receipt_classification"] = "changed-display-only"
    assert record == before


@pytest.mark.parametrize("status,classification", [("completed", "completed"), ("processing", "pending"),
    ("failed", "failed"), ("unrecognized", "unconfirmed")])
def test_query_summary_preserves_its_own_classification_and_parent_task_presence(status, classification):
    request = make_query()
    record = started_record(request, kind="query")
    MediaExecution._adopt(record, query_raw(request, status=status))
    value = diagnostic(record)
    assert value["raw_captured"] is True and value["http_status"] == 200
    assert value["receipt_classification"] == classification and value["accepted_task_present"] is True
    assert "result_url" not in json.dumps(image_operator._resource(record))


def test_projection_exposes_no_body_messages_task_ids_or_credentials_and_does_not_redecode(monkeypatch):
    request = make_submit()
    record = started_record(request)
    body = {"code": "fail_to_fetch_task", "message": "PRIVATE-KEY https://private.invalid/signed?secret=VALUE",
        "data": {"id": "UPSTREAM-TASK-SENTINEL", "input": "PRIVATE-SCRIPT"}}
    MediaExecution._adopt(record, raw_for(request, body, status=403))

    def no_redecode(*args, **kwargs):
        raise AssertionError("Projection must use already verified normalized fields")

    monkeypatch.setattr(MediaExecution, "_adopt", no_redecode)
    monkeypatch.setattr(image_policy.FrozenImageRequest, "from_frozen", no_redecode)
    monkeypatch.setattr(image_receipts, "decode_submit", no_redecode)
    monkeypatch.setattr(image_receipts, "decode_query", no_redecode)
    projected = json.dumps(image_operator._resource(record))
    assert all(value not in projected for value in (
        "PRIVATE-KEY", "https://", "UPSTREAM-TASK-SENTINEL", "PRIVATE-SCRIPT", "body_base64", "descriptor",
        "raw_hash", "receipt_hash", "received_at", "message", "provider_error_code"))


@pytest.mark.parametrize("kind", ["quote", "grant"])
def test_non_execution_resources_keep_existing_shape(kind):
    record = {"kind": kind, "id": "a" * 32, "scope_id": "b" * 32, "task_id": "c" * 32,
        "status": "granted", "disclosure_hash": "d" * 64}
    assert image_operator._resource(record) == {key: value for key, value in record.items() if key != "kind"}


@pytest.mark.parametrize("mode", ["base", "runtime", "observation"])
def test_existing_status_routes_share_response_summary_without_runtime_or_authorization_change(tmp_path, monkeypatch, mode):
    """Exercise all projection call sites with prevalidated-input stand-ins, not an authority fixture."""
    submit = make_submit()
    operation = started_record(submit)
    MediaExecution._adopt(operation, accepted_raw(submit))
    query_request = make_query(submit)
    query = started_record(query_request, kind="query")
    MediaExecution._adopt(query, raw_for(query_request,
        {"code": "fail_to_fetch_task", "message": "", "data": None}, status=403))
    scope = {"kind": "scope", "id": operation["scope_id"], "task_id": operation["task_id"], "account_id": "synthetic-account"}
    grant = {"kind": "grant", "id": operation["grant_id"], "scope_id": scope["id"], "revoked_at": None}
    payload = {"media": {"scope:" + scope["id"]: scope, "grant:" + grant["id"]: grant,
        "operation:" + operation["id"]: operation, "query:" + query["id"]: query}, "operations": {}}
    frozen = copy.deepcopy(payload)
    authority = {"schema_version": 2, "scope_id": scope["id"], "source_operation_id": operation["id"],
        "remaining_jobs": [{"shot_id": operation["shot_id"]}], "max_cumulative_submissions": 2,
        "effective_policy": {"max_queries": 2}}
    reads = []

    def readonly(root):
        reads.append(root)
        return SimpleNamespace(read_only=lambda: copy.deepcopy(payload))

    def unavailable(*args, **kwargs):
        raise CreatorError("SYNTHETIC_STALE_AUTHORITY", "Synthetic read-only authority", 409)

    monkeypatch.setattr(store, "ExecutionStore", readonly)
    monkeypatch.setattr(image_operator, "ExecutionStore", readonly)
    monkeypatch.setattr(image_runtime_control, "ExecutionStore", readonly)
    monkeypatch.setattr(image_operator, "_matching_scope", lambda *args: True)
    monkeypatch.setattr(image_operator, "failure_projection", lambda *args: {"status": "missing"})
    monkeypatch.setattr(image_operator, "projection", lambda *args: {"quality_status": "not_reviewed"})
    monkeypatch.setattr(media_ledger, "projection", lambda *args: {"quality_status": "not_reviewed"})
    monkeypatch.setattr(media_ledger, "has_pending_unknown", lambda *args: True)
    monkeypatch.setattr(media_ledger, "has_pending_fees", lambda *args: True)
    monkeypatch.setattr(image_runtime_control, "authority_for", lambda *args: authority)
    monkeypatch.setattr(image_runtime_control, "verify_current", unavailable)
    monkeypatch.setattr(image_observation_continuation, "authority_for", lambda *args: authority)
    control = SimpleNamespace(roots={"execution": tmp_path / "must-not-be-created"}, base=None,
        document=SimpleNamespace(mode="enabled", admission=SimpleNamespace(task_id=operation["task_id"]),
            policy={"model": "gpt-image-2-all", "max_queries": 2, "max_submissions": 2},
            budget_anchor={"account_scope_id": "synthetic-account"}),
        verify_authority=unavailable, verified_current=unavailable,
        runtime_activation_id="e" * 32 if mode == "runtime" else None,
        observation_activation_id="f" * 32 if mode == "observation" else None)
    result = image_operator._status(control, operation["id"])
    assert result["operation"]["response_diagnostic"]["receipt_classification"] == "accepted"
    assert result["queries"][0]["response_diagnostic"] == {"raw_captured": True, "http_status": 403,
        "receipt_classification": "unconfirmed", "accepted_task_present": True, "retry_authorized": False}
    assert result["dispatch_available"] is result["query_available"] is False
    assert result["automatic_followup_calls"] == 0
    assert payload == frozen and reads and not control.roots["execution"].exists()
