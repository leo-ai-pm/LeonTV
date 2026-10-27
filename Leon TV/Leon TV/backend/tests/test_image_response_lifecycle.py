"""Synthetic response lifecycle: no credentials, private state or real network."""
import base64
import copy
import io
import json
import socket
import sys
from datetime import timedelta
from types import SimpleNamespace

import httpx
import pytest

from core.creator.store import CreatorError, digest
from core.execution import image_capture, receipt_transport, receipt_worker, receipts
from core.execution.image_response_lifecycle import (
    IMAGE_KIND,
    make_response_policy,
    response_deadline,
    validate_response_policy,
)
from core.execution.policy import now
from core.execution.receipts import (
    ObservationEnded,
    ReceiptCaptureSettings,
    ReceiptError,
    ReceiptStore,
)
from core.execution.transport import ResponseUnconfirmed


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("No real network is permitted")
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", forbidden)


def binding_at(dispatch, seconds=180, *, legacy=False):
    value = {"kind": "controlled_image_http_v2" if legacy else IMAGE_KIND,
        "operation_id": "a" * 32, "deadline_at": dispatch.isoformat()}
    if not legacy:
        value.update(response_deadline_at=(dispatch + timedelta(seconds=seconds)).isoformat(),
            response_window_seconds=seconds, response_contract_hash=make_response_policy(seconds)["contract_hash"])
    return value


@pytest.mark.parametrize("seconds", [1, 180])
def test_policy_is_exactly_frozen_and_defensive(seconds):
    policy = make_response_policy(seconds)
    assert validate_response_policy(policy) == policy
    assert validate_response_policy(policy) is not policy
    assert policy["dispatch_window_seconds"] == 120 and policy["cleanup_grace_seconds"] == 5
    binding = binding_at(now(), seconds)
    assert response_deadline(binding).isoformat() == binding["response_deadline_at"]


@pytest.mark.parametrize("seconds", [True, False, 0, 181, 1.0, "180", None])
def test_policy_rejects_non_integer_or_unbounded_window(seconds):
    with pytest.raises(CreatorError):
        make_response_policy(seconds)


@pytest.mark.parametrize(("field", "value"), [("schema_version", True), ("dispatch_window_seconds", 121),
    ("cleanup_grace_seconds", 6), ("kind", "alternate_policy"), ("extra", 1)])
def test_resealed_policy_cannot_change_dispatch_or_cleanup(field, value):
    policy = make_response_policy(180)
    policy[field] = value
    policy["contract_hash"] = digest({key: item for key, item in policy.items() if key != "contract_hash"})
    with pytest.raises(CreatorError):
        validate_response_policy(policy)


@pytest.mark.parametrize("field", ["response_deadline_at", "response_window_seconds", "response_contract_hash"])
def test_legacy_bindings_cannot_acquire_any_new_response_field(field):
    current = binding_at(now())
    legacy = binding_at(now(), legacy=True)
    assert response_deadline(legacy).isoformat() == legacy["deadline_at"]
    legacy[field] = current[field]
    with pytest.raises(CreatorError):
        response_deadline(legacy)


@pytest.mark.parametrize("fault", ["longer", "hash", "bool", "missing"])
def test_binding_rejects_response_deadline_or_contract_mismatch(fault):
    binding = binding_at(now())
    if fault == "longer":
        binding["response_deadline_at"] = (response_deadline(binding) + timedelta(seconds=1)).isoformat()
    elif fault == "hash":
        binding["response_contract_hash"] = "f" * 64
    elif fault == "bool":
        binding["response_window_seconds"] = True
    else:
        del binding["response_deadline_at"]
    with pytest.raises(CreatorError):
        response_deadline(binding)


def test_claim_versions_and_cleanup_are_immutable(tmp_path):
    dispatch = now() + timedelta(seconds=120)
    store = ReceiptStore(tmp_path / "receipts")
    binding = binding_at(dispatch)
    response = response_deadline(binding)
    op, created = store.prepare(binding, "b" * 64, "c" * 64, response.isoformat(), (response + timedelta(seconds=5)).isoformat())
    assert created and op.claim(binding)["version"] == 2
    original = op.path("claim.json").read_bytes()
    assert store.prepare(binding, "d" * 64, "e" * 64, response.isoformat(), (response + timedelta(seconds=5)).isoformat())[1] is False
    assert op.path("claim.json").read_bytes() == original
    with pytest.raises(ReceiptError):
        store.prepare(binding, "b" * 64, "c" * 64, response.isoformat(), (response + timedelta(seconds=6)).isoformat())
    corrupt = copy.deepcopy(op.claim(binding))
    corrupt["version"] = 1
    corrupt["claim_hash"] = digest({key: value for key, value in corrupt.items() if key != "claim_hash"})
    op.path("claim.json").write_text(json.dumps(corrupt))
    with pytest.raises(ReceiptError):
        op.claim(binding)
    legacy = binding_at(dispatch, legacy=True)
    legacy["operation_id"] = "f" * 32
    old, _ = store.prepare(legacy, "b" * 64, "c" * 64, dispatch.isoformat(), (dispatch + timedelta(seconds=5)).isoformat())
    assert old.claim(legacy)["version"] == 1


def synthetic_capture(monkeypatch, tmp_path, *, legacy=False, start_expired=False, late_response=False,
                      notification_after_response=False):
    """Run real observer, claim, collect and commit with one in-process mock launch."""
    dispatch = now() + timedelta(seconds=1)
    clock = [dispatch + timedelta(seconds=1) if start_expired else dispatch - timedelta(seconds=1)]
    binding = binding_at(dispatch, legacy=legacy)
    for module in (receipt_transport, receipt_worker, receipts):
        monkeypatch.setattr(module, "now", lambda: clock[0])
        monkeypatch.setattr(module, "utc_now", lambda: clock[0].isoformat())
    calls = []

    def handle(request):
        calls.append(request.headers["x-client-operation-id"])
        clock[0] = response_deadline(binding) + timedelta(seconds=1) if late_response else dispatch + timedelta(seconds=1)
        return httpx.Response(200, json={"code": 0, "data": {"id": "synthetic-task"}}, headers={"x-request-id": "synthetic-request"})

    monkeypatch.setitem(sys.modules, "d42_synthetic_transport", SimpleNamespace(make_transport=lambda options: httpx.MockTransport(handle)))
    monkeypatch.setattr(image_capture, "worker_request", lambda job, claim:
        (binding, "https://synthetic.invalid/v1/images/generations/async", "{}", True, "POST", 1024))
    store = ReceiptStore(tmp_path / "receipts")
    if notification_after_response:
        original_notify = ReceiptStore.notify

        def delayed_notification(operation, bound, record_id):
            clock[0] = response_deadline(bound) + timedelta(seconds=4)
            return original_notify(operation, bound, record_id)

        monkeypatch.setattr(ReceiptStore, "notify", delayed_notification)

    class Input(io.BytesIO):
        def close(self):
            envelope = json.loads(self.getvalue())
            operation = store.operation(binding["operation_id"])
            claim = operation.claim(binding)
            receipt_worker.collect(operation, claim, {**envelope["job"], "api_key": envelope["api_key"]})
            super().close()

    class Process:
        def __init__(self, *args, **kwargs):
            self.stdin = Input()

        def wait(self):
            return 0

    class ImmediateThread:
        def __init__(self, target, **kwargs):
            self.target = target

        def start(self):
            self.target()

    monkeypatch.setattr(receipt_transport.subprocess, "Popen", Process)
    monkeypatch.setattr(receipt_transport.threading, "Thread", ImmediateThread)
    settings = ReceiptCaptureSettings(worker_factory="d42_synthetic_transport:make_transport")
    job = {"kind": binding["kind"], "worker_factory": settings.worker_factory, "worker_options": {}}
    return store, binding, calls, lambda: receipt_transport.capture_once(store, binding, job, "SYNTHETIC-ONLY", settings)


def test_preparation_does_not_consume_v3_response_window_and_never_replays(tmp_path, monkeypatch):
    store, binding, calls, capture = synthetic_capture(monkeypatch, tmp_path)
    raw = capture()
    assert receipts.timestamp(raw["received_at"]) > receipts.timestamp(binding["deadline_at"])
    assert b"synthetic-task" in base64.b64decode(raw["body_base64"])
    assert store.operation(binding["operation_id"]).claim()["version"] == 2
    before = {path.name: path.read_bytes() for path in store.operation(binding["operation_id"]).root.iterdir()}
    with pytest.raises(ObservationEnded):
        capture()
    assert calls == [binding["operation_id"]]
    assert {path.name: path.read_bytes() for path in store.operation(binding["operation_id"]).root.iterdir()} == before


def test_same_late_response_remains_unconfirmed_for_legacy(tmp_path, monkeypatch):
    store, binding, calls, capture = synthetic_capture(monkeypatch, tmp_path, legacy=True)
    with pytest.raises(ResponseUnconfirmed, match="OVERALL_DEADLINE"):
        capture()
    assert calls == [binding["operation_id"]]
    assert store.operation(binding["operation_id"]).claim()["version"] == 1


def test_response_extension_never_authorizes_a_late_dispatch(tmp_path, monkeypatch):
    _, _, calls, capture = synthetic_capture(monkeypatch, tmp_path, start_expired=True)
    with pytest.raises(ResponseUnconfirmed, match="OVERALL_DEADLINE"):
        capture()
    assert calls == []


def test_response_after_new_cutoff_remains_unconfirmed(tmp_path, monkeypatch):
    _, binding, calls, capture = synthetic_capture(monkeypatch, tmp_path, late_response=True)
    with pytest.raises(ResponseUnconfirmed, match="OVERALL_DEADLINE"):
        capture()
    assert calls == [binding["operation_id"]]


def test_timely_response_durable_within_cleanup_is_available_read_only(tmp_path, monkeypatch):
    store, binding, calls, capture = synthetic_capture(monkeypatch, tmp_path, notification_after_response=True)
    with pytest.raises(ResponseUnconfirmed, match="OVERALL_DEADLINE"):
        capture()
    operation = store.operation(binding["operation_id"])
    before = {path.name: path.read_bytes() for path in operation.root.iterdir()}
    raw, summary = store.ready_raw_binding(binding["operation_id"], binding)
    assert summary["status"] == "candidate_found" and summary["notification_present"]
    assert receipts.timestamp(raw["received_at"]) < response_deadline(binding)
    assert response_deadline(binding) < receipts.timestamp(summary["durable_confirmation_at"])
    assert receipts.timestamp(summary["durable_confirmation_at"]) < receipts.timestamp(operation.claim()["cleanup_cutoff"])
    with pytest.raises(ObservationEnded):
        capture()
    assert calls == [binding["operation_id"]]
    assert {path.name: path.read_bytes() for path in operation.root.iterdir()} == before
