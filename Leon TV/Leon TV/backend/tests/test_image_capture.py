"""Independent actual detached capture tests; all HTTP is synthetic transport."""
import base64
import copy
import hashlib
import json
import os
import socket
import subprocess
import sys
import time
from datetime import timedelta
from pathlib import Path

import httpx
import pytest
from test_image_policy import inputs, make_submit

from core.creator.store import CreatorError, digest
from core.execution.image_capture import ControlledImageCapture, worker_request
from core.execution.image_policy import (
    ImagePolicyError,
    ImageToolPolicy,
    build_image_query,
    build_image_submit,
)
from core.execution.image_receipts import decode_query, decode_submit
from core.execution.policy import now
from core.execution.receipts import ObservationEnded, ReceiptCaptureSettings
from core.execution.transport import ResponseUnconfirmed

KEY = "SYNTHETIC-IMAGE-PRIVATE-KEY"
BACKEND = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def no_real_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Image capture tests attempted real network")
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", forbidden)


def tree(root):
    return {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def requests(root):
    path = root / "requests.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def adapter_for(root, *, observation_wait=3, verifier=lambda value: True, **options):
    return ControlledImageCapture(root / "receipts", authority_verifier=verifier,
        settings=ReceiptCaptureSettings(observation_wait_seconds=observation_wait,
            worker_factory="image_tool_fixture:make_transport", worker_options={"counter": str(root / "requests.jsonl"), **options}))


def wait_for(predicate, seconds=5):
    limit = time.monotonic() + seconds
    while time.monotonic() < limit:
        value = predicate()
        if value:
            return value
        time.sleep(0.01)
    raise AssertionError("Synthetic image worker did not reach the expected durable state")


def wait_worker(adapter, request):
    operation = adapter.receipts.operation(request.binding()["operation_id"])
    wait_for(lambda: operation.path("worker.json").exists())
    pid = operation.read("worker.json")["worker_pid"]

    def exited():
        try:
            os.kill(pid, 0)
            return False
        except ProcessLookupError:
            return True
    wait_for(exited)
    return operation


def query_for(submit, actual_raw):
    parent = submit.frozen()["permit"]
    changed = {"permit_hash", "operation_id", "capture_id", "nonce", "action", "query_index", "paid_calls", "risk_cny", "issued_at", "deadline_at"}
    permit = {key: value for key, value in parent.items() if key not in changed}
    issued = now()
    permit.update(operation_id="5" * 32, capture_id="5" * 32, nonce="6" * 32, action="query", query_index=1,
        paid_calls=0, risk_cny="0.000000", issued_at=issued.isoformat(), deadline_at=(issued + timedelta(seconds=120)).isoformat())
    return build_image_query(submit=submit, submit_raw=actual_raw, permit=permit, query_index=1)


def test_post_then_same_task_get_uses_the_shared_detached_worker_once_each(tmp_path, monkeypatch):
    monkeypatch.setenv("SYNTHETIC_IMAGE_KEY", KEY)
    submit = make_submit()
    adapter = adapter_for(tmp_path)
    submit_raw = adapter.capture(submit, KEY)
    submit_op = wait_worker(adapter, submit)
    submit_receipt = decode_submit(submit, submit_raw)
    assert submit_receipt["classification"] == "accepted" and submit_receipt["accepted_task_id"] == "synthetic-image-task-1"
    assert adapter.ready_raw(submit)[0] == submit_raw
    record = submit_op.read(next(submit_op.root.glob("*.record.json")).name)
    assert record["binding"] == submit.binding() and record["binding"]["wire_hash"] == digest(submit.wire)
    assert record["body_sha256"] == hashlib.sha256(base64.b64decode(submit_raw["body_base64"])).hexdigest()
    query = query_for(submit, submit_raw)
    query_raw = adapter.capture(query, KEY)
    wait_worker(adapter, query)
    completed = decode_query(query, query_raw)
    assert completed["classification"] == "completed" and completed["accepted_task_id"] == submit_receipt["accepted_task_id"]
    assert completed["submit_request_id"] == submit_receipt["submit_request_id"]
    assert completed["submit_raw_hash"] == digest(submit_raw)
    assert [(item["method"], item["url"], item["wire"]) for item in requests(tmp_path)] == [
        ("POST", submit.endpoint, submit.wire), ("GET", query.endpoint, "")]
    assert [item["operation_id"] for item in requests(tmp_path)] == [submit.binding()["operation_id"], query.binding()["operation_id"]]
    assert all(not item["credential_in_environment"] for item in requests(tmp_path))
    before = tree(tmp_path)
    for request in (submit, query):
        with pytest.raises(ObservationEnded):
            adapter.capture(request, KEY)
        summary = adapter.inspect(request)["inspection"]
        assert summary["binding_verified"] and summary["notification_present"] and not summary["recovery_performed"]
    assert tree(tmp_path) == before and len(requests(tmp_path)) == 2
    assert KEY not in json.dumps({name: value.decode("utf-8") for name, value in before.items()})


@pytest.mark.parametrize("fault", ["unknown", "broken_stream", "oversize", "credential_echo"])
def test_unknown_and_unusable_http_never_create_ready_raw_or_resubmit(tmp_path, fault):
    request = make_submit()
    adapter = adapter_for(tmp_path, fault=fault)
    with pytest.raises(ResponseUnconfirmed):
        adapter.capture(request, KEY)
    wait_worker(adapter, request)
    assert adapter.ready_raw(request)[0] is None
    assert adapter.inspect(request)["inspection"]["status"] == "capture_error"
    before = tree(tmp_path)
    with pytest.raises(ObservationEnded):
        adapter.capture(request, KEY)
    assert tree(tmp_path) == before and len(requests(tmp_path)) == 1


def test_worker_enforces_the_explicit_policy_response_limit_below_the_global_cap(tmp_path):
    arguments = inputs()
    policy = dict(arguments["policy"].model_dump(), max_response_bytes=128)
    arguments["policy"] = policy
    arguments["permit"] = dict(arguments["permit"].model_dump(), policy_hash=ImageToolPolicy.model_validate(policy).frozen()["policy_hash"])
    request = build_image_submit(**arguments)
    adapter = adapter_for(tmp_path, fault="oversize", response_bytes=129)
    with pytest.raises(ResponseUnconfirmed) as error:
        adapter.capture(request, KEY)
    wait_worker(adapter, request)
    assert error.value.code == "RECEIPT_TOO_LARGE"
    assert adapter.ready_raw(request)[0] is None and len(requests(tmp_path)) == 1


@pytest.mark.parametrize(("fault", "status", "records"), [("before_commit", "incomplete", 0),
    ("after_commit", "candidate_found", 1), ("before_seal", "incomplete", 1),
    ("partial_ready", "corrupt", 1), ("persist_fail", "capture_error", 0)])
def test_commit_boundary_crashes_retain_original_evidence_without_adoption_or_retry(tmp_path, fault, status, records):
    request = make_submit()
    adapter = adapter_for(tmp_path, observation_wait=0.4, fault=fault)
    with pytest.raises((ObservationEnded, ResponseUnconfirmed)):
        adapter.capture(request, KEY)
    operation = wait_worker(adapter, request)
    before = tree(tmp_path)
    assert len(list(operation.root.glob("*.record.json"))) == records
    assert adapter.inspect(request)["inspection"]["status"] == status
    assert adapter.ready_raw(request)[0] is None
    with pytest.raises(ObservationEnded):
        adapter.capture(request, KEY)
    assert tree(tmp_path) == before and len(requests(tmp_path)) == 1


def test_observation_end_retains_late_complete_receipt_but_does_not_resubmit(tmp_path):
    request = make_submit()
    adapter = adapter_for(tmp_path, observation_wait=0.01, factory_delay=0.1)
    with pytest.raises(ObservationEnded):
        adapter.capture(request, KEY)
    operation = wait_worker(adapter, request)
    before = tree(tmp_path)
    assert operation.path("observer-closed.json").exists()
    summary = adapter.inspect(request)["inspection"]
    assert summary["status"] == "candidate_found" and summary["notification_present"] and not summary["recovery_performed"]
    with pytest.raises(ObservationEnded):
        adapter.capture(request, KEY)
    assert tree(tmp_path) == before and len(requests(tmp_path)) == 1


def test_detached_worker_survives_observer_process_exit_with_exact_original_claim(tmp_path):
    request = make_submit()
    descriptor = tmp_path / "descriptor.json"
    descriptor.write_text(json.dumps(request.frozen()))
    release = tmp_path / "release-worker"
    environment = {key: value for key, value in os.environ.items() if key in {"PATH", "LANG", "LC_ALL", "TMPDIR", "SYSTEMROOT", "LTV_TEST_NETWORK_LOG"}}
    environment.update(PYTHONDONTWRITEBYTECODE="1", PYTHONPATH=os.pathsep.join((str(BACKEND), str(BACKEND / "tests"))))
    process = subprocess.Popen([sys.executable, str(BACKEND / "tests/image_tool_fixture.py"), str(tmp_path), str(descriptor), json.dumps({"release": str(release)})],
        cwd=BACKEND, env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        wait_for(lambda: requests(tmp_path))
        operation = adapter_for(tmp_path).receipts.operation(request.binding()["operation_id"])
        claim = operation.path("claim.json").read_bytes()
        assert requests(tmp_path)[0]["pid"] != process.pid
        process.kill()
        process.wait(timeout=3)
        release.write_text("release synthetic transport")
        adapter = adapter_for(tmp_path)
        wait_for(lambda: adapter.inspect(request)["inspection"]["notification_present"])
        before = tree(tmp_path)
        assert operation.path("claim.json").read_bytes() == claim
        assert adapter.ready_raw(request)[0] is not None
        with pytest.raises(ObservationEnded):
            adapter.capture(request, KEY)
        assert tree(tmp_path) == before and len(requests(tmp_path)) == 1
    finally:
        release.touch(exist_ok=True)
        if process.poll() is None:
            process.kill()
        process.wait(timeout=3)


def test_wrong_query_task_is_preserved_as_raw_and_cannot_become_completed(tmp_path):
    submit = make_submit()
    adapter = adapter_for(tmp_path)
    raw = adapter.capture(submit, KEY)
    wait_worker(adapter, submit)
    query = query_for(submit, raw)
    wrong = adapter_for(tmp_path, fault="wrong_task")
    query_raw = wrong.capture(query, KEY)
    wait_worker(wrong, query)
    result = decode_query(query, query_raw)
    assert result["classification"] == "unconfirmed" and result["result_url"] is None
    assert result["accepted_task_id"] == "synthetic-image-task-1"
    assert wrong.ready_raw(query)[0] == query_raw and len(requests(tmp_path)) == 2


@pytest.mark.parametrize("behavior", ["absent", "false", "one", "error", "mutated", "non_json_mutation"])
def test_only_strict_unchanged_server_verification_can_create_a_capture(tmp_path, behavior, monkeypatch):
    def verifier(candidate):
        if behavior == "error":
            raise RuntimeError("SYNTHETIC PRIVATE AUTHORITY ERROR")
        if behavior == "mutated":
            candidate["permit"]["grant_id"] = "9" * 32
            return True
        if behavior == "non_json_mutation":
            candidate["references"] = tuple(candidate["references"])
            return True
        return {"false": False, "one": 1}.get(behavior, True)
    adapter = adapter_for(tmp_path, verifier=None if behavior == "absent" else verifier)
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: pytest.fail("Rejected authority launched HTTP worker"))
    with pytest.raises(CreatorError) as error:
        adapter.capture(make_submit(), KEY)
    assert error.value.code == "MEDIA_TOOL_UNAVAILABLE" and tree(tmp_path) == {}


def test_synthetic_factory_is_required_and_live_factory_or_options_are_forbidden(tmp_path, monkeypatch):
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: pytest.fail("Invalid execution environment launched worker"))
    request = make_submit()
    with pytest.raises(CreatorError):
        ControlledImageCapture(tmp_path / "synthetic", authority_verifier=lambda candidate: True).capture(request, KEY)
    arguments = inputs()
    policy = dict(arguments["policy"].model_dump(), origin="https://api.ai-tudou.net", validation_only=False)
    arguments["policy"] = policy
    arguments["permit"] = dict(arguments["permit"].model_dump(), policy_hash=ImageToolPolicy.model_validate(policy).frozen()["policy_hash"])
    live = build_image_submit(**arguments)
    for settings in (ReceiptCaptureSettings(worker_factory="image_tool_fixture:make_transport"), ReceiptCaptureSettings(worker_options={"unexpected": True})):
        with pytest.raises(CreatorError):
            ControlledImageCapture(tmp_path / "live", authority_verifier=lambda candidate: True, settings=settings).capture(live, KEY)
    assert tree(tmp_path) == {}


def test_expired_request_and_legacy_text_scope_are_rejected_before_claim_creation(tmp_path, monkeypatch):
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: pytest.fail("Expired or legacy permit launched worker"))
    arguments = inputs()
    issued = now() - timedelta(seconds=60)
    permit = dict(arguments["permit"].model_dump(), issued_at=issued.isoformat(), deadline_at=(issued + timedelta(seconds=30)).isoformat())
    arguments["permit"] = permit
    expired = build_image_submit(**arguments)
    adapter = adapter_for(tmp_path)
    with pytest.raises(CreatorError):
        adapter.capture(expired, KEY)
    arguments["permit"] = dict(permit, scope="frozen_brief_and_bounded_roles_empirical_risk_to_named_provider")
    with pytest.raises(ImagePolicyError):
        build_image_submit(**arguments)
    with pytest.raises(CreatorError):
        adapter.capture({"grant_id": "4" * 32, "scope": "frozen_brief_and_bounded_roles_empirical_risk_to_named_provider"}, KEY)
    assert tree(tmp_path) == {}


def test_inspection_is_read_only_and_static_mounted_receipts_are_rejected(tmp_path):
    request = make_submit()
    adapter = adapter_for(tmp_path)
    assert adapter.inspect(request)["inspection"]["status"] == "missing"
    assert adapter.ready_raw(request)[0] is None and tree(tmp_path) == {}
    with pytest.raises(CreatorError):
        ControlledImageCapture(tmp_path / "receipts", static_roots=(tmp_path,))
    assert tree(tmp_path) == {}


def test_worker_checks_exact_binding_and_method_before_http_factory(tmp_path):
    request = make_submit()
    binding = request.binding()
    job = {"kind": "controlled_image_http_v1", "descriptor": request.frozen(), "binding": binding,
           "worker_factory": "image_tool_fixture:make_transport", "worker_options": {}, "api_key": KEY}
    claim = {"binding": copy.deepcopy(binding), "cleanup_cutoff": (now() + timedelta(seconds=125)).isoformat(), "created_at": now().isoformat()}
    from core.execution.receipts import timestamp
    claim["cleanup_cutoff"] = (timestamp(binding["deadline_at"]) + timedelta(seconds=5)).isoformat()
    assert worker_request(job, claim)[4] == "POST"
    for key in ("task_id", "descriptor_hash", "wire_hash", "operation_id"):
        changed = copy.deepcopy(job)
        changed["binding"][key] = "f" * (32 if key.endswith("_id") else 64)
        with pytest.raises(CreatorError):
            worker_request(changed, claim)
    forged = copy.deepcopy(job)
    forged["descriptor"]["method"] = "DELETE"
    with pytest.raises(CreatorError):
        worker_request(forged, claim)
    assert tree(tmp_path) == {}
