"""D14 real execution seam and detached workers; synthetic HTTP only."""
import base64
import hashlib
import json
import time

import pytest
from test_planner_execution import change_policy
from test_planner_execution import env as env
from test_planner_execution import no_network as no_network

from core.creator.store import digest
from core.execution.receipts import ReceiptCaptureSettings, ReceiptStore, binding_for


def configure(env, **options):
    counter = env.runtime.store.root.parent / "posts.jsonl"
    wait = options.pop("observation_wait", 0.15)
    grace = options.pop("grace", 2)
    env.runtime.receipt_capture = ReceiptCaptureSettings(observation_wait_seconds=wait, cleanup_grace_seconds=grace,
        worker_factory="receipt_worker_fixtures:make_transport", worker_options={"counter": str(counter), **options})
    return counter


def posts(counter):
    return [json.loads(line) for line in counter.read_text().splitlines()] if counter.exists() else []


def persisted(env):
    payload = env.runtime.store.read_only()
    operation = next(iter(payload["operations"].values()))
    return operation, payload


def inspect_url(env, task, operation):
    return env.url(task) + f"/operations/{operation['id']}/receipts/inspect"


def wait_inspection(env, task, operation, expected, seconds=5, require_notification=False):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        response = env.client.get(inspect_url(env, task, operation))
        assert response.status_code == 200, response.text
        value = response.json()["inspection"]
        if value["status"] in expected and (not require_notification or value["notification_present"]):
            return value
        time.sleep(0.01)
    raise AssertionError("Receipt did not reach expected inspection status")


def unknown_after_deadline(env):
    change_policy(env, request_deadline_seconds=1)
    counter = configure(env, delay=1.3)
    task = env.source_task()
    granted = env.grant(task)
    env.action(task, granted["execution"])
    final = env.wait(task, {"needs_reconciliation"}, seconds=3)
    original = (env.runtime.store.root / "state.json").read_bytes()
    operation, _ = persisted(env)
    summary = wait_inspection(env, task, operation, {"candidate_found"})
    assert len(posts(counter)) == final["ledger"]["call_count"] == 1
    assert final["ledger"]["held_cny"] == granted["execution"]["quotes"][0]["per_call_hold_cny"]
    assert final["operations"][0]["locally_accounted_cny"] is None
    assert env.observe(task) == final and (env.runtime.store.root / "state.json").read_bytes() == original
    return task, final, original, operation, summary, counter


def test_late_complete_response_is_not_lost_after_observer_deadline(env):
    _, _, _, operation, summary, _ = unknown_after_deadline(env)
    store = ReceiptStore(env.runtime.store.root / "receipts").operation(operation["id"])
    records = list(store.root.glob("*.record.json"))
    assert len(records) == 1
    record = store.read(records[0].name)
    body = base64.b64decode(record["raw"]["body_base64"], validate=True)
    assert hashlib.sha256(body).hexdigest() == record["body_sha256"] == summary["body_sha256"]
    assert record["binding"] == binding_for(operation)
    assert record["binding"]["wire_hash"] == digest(operation["wire"])
    assert record["envelope_hash"] == digest({key: value for key, value in record.items() if key != "envelope_hash"})
    assert json.loads(json.loads(body)["choices"][0]["message"]["content"])["tool"] == "read_frozen_brief"
    assert summary["isolated"] is True and summary["recovery_performed"] is False and summary["binding_verified"] is True
    assert operation["raw"] is None and operation["normalized"] is None and operation["local_units"] is None


def test_inspect_endpoint_reads_unknown_evidence_without_mutation_or_post(env):
    task, final, original, operation, _, counter = unknown_after_deadline(env)
    root = env.runtime.store.root
    before = {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()}
    for _ in range(3):
        response = env.client.get(inspect_url(env, task, operation))
        assert response.status_code == 200
        assert "body_base64" not in response.text and "synthetic-secret" not in response.text
        assert response.json()["inspection"]["status"] == "candidate_found"
    after = {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()}
    assert before == after and (root / "state.json").read_bytes() == original and len(posts(counter)) == 1
    assert env.observe(task) == final


def test_existing_target_is_idempotent_even_when_store_is_full(tmp_path, monkeypatch):
    import core.execution.receipts as module

    store = module.ReceiptStore(tmp_path.resolve() / "receipts")
    assert store.write_new("existing.json", {"value": "synthetic"}) is True
    monkeypatch.setattr(module, "MAX_STORE", store.path("existing.json").stat().st_size)
    assert store.write_new("existing.json", {"value": "synthetic"}) is False


@pytest.mark.parametrize("wire", ["chat_completions", "responses"])
def test_timely_complete_protocol_receipts_are_durable_before_normal_processing(env, wire):
    change_policy(env, request_deadline_seconds=5, wire_api=wire,
        endpoint="https://synthetic.example/v1/" + ("responses" if wire == "responses" else "chat/completions"))
    counter = configure(env, observation_wait=None)
    task = env.source_task()
    granted = env.grant(task)
    env.action(task, granted["execution"])
    final = env.wait(task, {"plan_ready"}, seconds=12)
    assert len(posts(counter)) == final["ledger"]["call_count"] == 2
    assert final["ledger"]["locally_accounted_cny"] == "0.000100" and final["ledger"]["held_cny"] == "0.000000"
    for operation in env.runtime.store.read_only()["operations"].values():
        summary = env.client.get(inspect_url(env, task, operation)).json()["inspection"]
        assert summary["status"] == "candidate_found" and summary["notification_present"] is True
        assert summary["isolated"] is True and summary["recovery_performed"] is False
        assert operation["raw"] is not None and operation["status"] == "settled"


def synthetic_operation():
    from datetime import timedelta

    from core.execution.policy import now

    value = {"id": "a" * 32, "task_id": "b" * 32, "grant_id": "c" * 32, "quote_id": "d" * 32, "account_id": "synthetic-account",
        **{name: "e" * 64 for name in ["snapshot_hash", "context_hash", "wire_hash", "policy_hash", "permit_hash"]},
        "permit": {"deadline_at": (now() + timedelta(seconds=30)).isoformat()}}
    return value


def prepared_store(tmp_path):
    from datetime import timedelta

    from core.execution.policy import now

    operation = synthetic_operation()
    root = ReceiptStore(tmp_path.resolve() / "receipts")
    op, created = root.prepare(binding_for(operation), "f" * 64, "1" * 64, (now() + timedelta(seconds=10)).isoformat(), (now() + timedelta(seconds=35)).isoformat())
    assert created
    return root, op, operation


def synthetic_raw(body=b'{"synthetic":"complete"}'):
    from core.creator.store import utc_now

    return {"http_status": 200, "headers": {"x-request-id": "req_synthetic"}, "body_base64": base64.b64encode(body).decode(), "received_at": utc_now()}


def test_quota_lock_symlink_cannot_create_external_target(tmp_path):
    import core.execution.receipts as module

    root = tmp_path.resolve() / "receipts"
    root.mkdir()
    external = tmp_path.resolve() / "external-must-not-be-created"
    (root / ".quota.lock").symlink_to(external)
    store = module.ReceiptStore(root)
    with pytest.raises(Exception):
        store.prepare(binding_for(synthetic_operation()), "f" * 64, "1" * 64, "2090-01-01T00:00:00+00:00", "2091-01-01T00:00:00+00:00")
    assert not external.exists()


def test_key_escaped_in_inner_action_is_rejected_before_any_body_persistence(tmp_path):
    from core.execution.receipts import ReceiptError
    from core.planner.protocol import execute_tool

    root, op, operation = prepared_store(tmp_path)
    key = "SYNTHETIC-PRIVATE-API-SECRET"
    action = {"tool": "submit_shot_plan", "arguments": {"plan": {"source_snapshot_hash": operation["snapshot_hash"], "duration_ms": 15000, "ratio": "9:16",
        "shots": [{"id": "S01", "duration_ms": 15000, "visual": key, "camera": "", "audio": "", "depends_on": []}]}}}
    inner = json.dumps(action).replace(key, ''.join('\\u%04x' % ord(char) for char in key))
    body = json.dumps({"choices": [{"message": {"content": inner}}]}).encode()
    assert key.encode() not in body
    text = json.loads(body)["choices"][0]["message"]["content"]
    result, plan, _ = execute_tool(text, {"snapshot_hash": operation["snapshot_hash"]}, True)
    assert result["ok"] is True and plan["shots"][0]["visual"] == key
    with pytest.raises(ReceiptError):
        op.commit(binding_for(operation), synthetic_raw(body), key)
    assert not list(op.root.glob("*.record.json"))
    assert root.inspect(operation)["status"] != "candidate_found"


def test_unknown_raw_fields_are_rejected_before_secret_can_be_persisted(tmp_path):
    from core.execution.receipts import ReceiptError

    _, op, operation = prepared_store(tmp_path)
    raw = {**synthetic_raw(), "private_debug": "SYNTHETIC-PRIVATE-API-SECRET"}
    with pytest.raises(ReceiptError):
        op.commit(binding_for(operation), raw, "SYNTHETIC-PRIVATE-API-SECRET")
    assert not list(op.root.glob("*.record.json"))


def tree_bytes(root):
    return {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file() and not path.is_symlink()}


def test_operation_quota_rejects_without_orphan_directory_and_old_markers_still_work(tmp_path, monkeypatch):
    from datetime import timedelta

    import core.execution.receipts as module
    from core.execution.policy import now

    monkeypatch.setattr(module, "MAX_OPERATIONS", 1)
    root, op, operation = prepared_store(tmp_path)
    other = synthetic_operation()
    other["id"] = "2" * 32
    with pytest.raises(module.ReceiptError):
        root.prepare(binding_for(other), "f" * 64, "1" * 64, (now() + timedelta(seconds=10)).isoformat(), (now() + timedelta(seconds=35)).isoformat())
    assert not (root.root / other["id"]).exists()
    op.error(binding_for(operation), "TRANSPORT_ERROR")
    assert op.read("error.json")["code"] == "TRANSPORT_ERROR"


@pytest.mark.parametrize("fault,status,notification", [
    ("before_commit", "incomplete", False), ("persist_fail", "capture_error", False), ("after_commit", "candidate_found", False),
])
def test_commit_crashes_separate_complete_candidate_from_notification(env, fault, status, notification):
    counter = configure(env, fault=fault)
    task = env.source_task()
    granted = env.grant(task)
    env.action(task, granted["execution"])
    final = env.wait(task, {"needs_reconciliation"})
    operation, _ = persisted(env)
    op = ReceiptStore(env.runtime.store.root / "receipts").operation(operation["id"])
    end = time.monotonic() + 4
    while not op.path("worker.json").exists() and time.monotonic() < end:
        time.sleep(0.01)
    worker_pid = op.read("worker.json")["worker_pid"]
    wait_process_exit(worker_pid)
    summary = env.runtime.inspect_receipts(task["id"], operation["id"])["inspection"]
    assert summary["status"] == status and summary["notification_present"] is notification
    assert len(posts(counter)) == final["ledger"]["call_count"] == 1
    assert final["ledger"]["held_cny"] == granted["execution"]["quotes"][0]["per_call_hold_cny"]
    assert operation["raw"] is None and operation["local_units"] is None and operation["tool_result"] is None
    before = tree_bytes(env.runtime.store.root)
    for _ in range(2):
        assert env.runtime.inspect_receipts(task["id"], operation["id"])["inspection"] == summary
    assert before == tree_bytes(env.runtime.store.root)


def process_alive(pid):
    import subprocess

    result = subprocess.run(["ps", "-p", str(pid), "-o", "stat="], capture_output=True, text=True, timeout=2)
    return result.returncode == 0 and bool(result.stdout.strip()) and not result.stdout.strip().startswith("Z")


def wait_process_exit(pid, seconds=5):
    end = time.monotonic() + seconds
    while process_alive(pid) and time.monotonic() < end:
        time.sleep(0.02)
    assert not process_alive(pid), "Synthetic detached worker leaked after cutoff"


def launch_observer(root, options):
    import os
    import subprocess
    import sys
    from pathlib import Path

    root.mkdir()
    script = Path(__file__).with_name("receipt_observer_fixture.py")
    process = subprocess.Popen([sys.executable, str(script), str(root), json.dumps(options)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    end = time.monotonic() + 6
    ready = root / "observer.json"
    while not ready.exists() and process.poll() is None and time.monotonic() < end:
        time.sleep(0.01)
    assert ready.exists(), "Synthetic observer did not establish original intent"
    return process, json.loads(ready.read_text())


def inspect_subprocess_state(root, ids):
    from core.execution.store import ExecutionStore

    store = ExecutionStore(root / "execution")
    payload = store.read_only()
    operation = payload["operations"][ids["operation_id"]]
    receipt_store = ReceiptStore(store.root / "receipts")
    return store, operation, receipt_store, receipt_store.inspect(operation)


@pytest.mark.parametrize("kill_while_observing", [False, True])
def test_worker_collects_after_observer_sigkill_without_new_post_or_journal_mutation(tmp_path, kill_while_observing):
    import os

    from core.creator.store import utc_now
    from core.execution.policy import TrustedInputBounder
    from core.execution.receipt_transport import CapturedTransport
    from core.execution.receipts import ObservationEnded, timestamp

    root = tmp_path.resolve() / "detached"
    release = root / "release-response"
    process, ids = launch_observer(root, {"release": str(release), "deadline_seconds": 5, "observation_wait": 4 if kill_while_observing else 0.2,
        "kill_while_observing": kill_while_observing})
    try:
        store, operation, receipt_store, _ = inspect_subprocess_state(root, ids)
        original = store.path("state.json").read_bytes()
        waiting = time.monotonic() + 2
        while len(posts(root / "posts.jsonl")) != 1 and time.monotonic() < waiting:
            time.sleep(0.01)
        assert len(posts(root / "posts.jsonl")) == 1 and not release.exists()
        assert not list(receipt_store.operation(operation["id"]).root.glob("*.record.json"))
        process.kill()
        process.wait(timeout=3)
        assert process.returncode is not None and not process_alive(ids["observer_pid"])
        released_at = utc_now()
        release.write_text("observer-confirmed-exited")
        end = time.monotonic() + 5
        while time.monotonic() < end:
            summary = receipt_store.inspect(operation)
            if summary["status"] == "candidate_found":
                break
            time.sleep(0.02)
        assert summary["status"] == "candidate_found" and summary["isolated"] is True and summary["recovery_performed"] is False
        assert timestamp(released_at) <= timestamp(summary["response_received_at"]) < timestamp(operation["permit"]["deadline_at"])
        worker_pid = receipt_store.operation(operation["id"]).read("worker.json")["worker_pid"]
        assert worker_pid != ids["observer_pid"] != os.getpid()
        wait_process_exit(worker_pid)
        assert hashlib.sha256(original).hexdigest() == ids["journal_sha256"]
        assert store.path("state.json").read_bytes() == original and len(posts(root / "posts.jsonl")) == 1
        if kill_while_observing:
            payload = store.read_only()
            p = payload["quotes"][operation["quote_id"]]["policy"]
            bounder = TrustedInputBounder(p["policy_hash"], operation["permit"]["bounder_ref"], lambda wire, policy: len(wire) + 1024)
            transport = CapturedTransport(None, bounder, store, ReceiptCaptureSettings(observation_wait_seconds=100))
            with pytest.raises(ObservationEnded):
                transport.perform(operation, p, "synthetic-secret")
            assert store.path("state.json").read_bytes() == original and len(posts(root / "posts.jsonl")) == 1
        before = tree_bytes(root)
        for _ in range(3):
            assert receipt_store.inspect(operation)["status"] == "candidate_found"
        assert tree_bytes(root) == before
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=3)


def test_independent_watchdog_exits_blocked_worker_after_observer_sigkill(tmp_path):
    root = tmp_path.resolve() / "cutoff"
    process, ids = launch_observer(root, {"fault": "blocks", "deadline_seconds": 1, "observation_wait": 0.2, "grace": 0.5})
    try:
        store, operation, receipt_store, _ = inspect_subprocess_state(root, ids)
        op = receipt_store.operation(operation["id"])
        end = time.monotonic() + 1
        while not op.path("started.json").exists() and time.monotonic() < end:
            time.sleep(0.01)
        worker_pid = op.read("worker.json")["worker_pid"]
        assert len(posts(root / "posts.jsonl")) == 1
        original = store.path("state.json").read_bytes()
        process.kill()
        process.wait(timeout=3)
        wait_process_exit(worker_pid, seconds=4)
        assert receipt_store.inspect(operation)["status"] != "candidate_found"
        assert store.path("state.json").read_bytes() == original and len(posts(root / "posts.jsonl")) == 1
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=3)


def test_watchdog_bounds_incomplete_stdin_without_model_submission(tmp_path):
    import os
    import subprocess
    import sys
    from datetime import timedelta
    from pathlib import Path

    from core.execution.policy import now

    operation = synthetic_operation()
    operation["permit"]["deadline_at"] = (now() + timedelta(seconds=1)).isoformat()
    root = ReceiptStore(tmp_path.resolve() / "ipc")
    op, _ = root.prepare(binding_for(operation), "f" * 64, "1" * 64, (now() + timedelta(seconds=0.2)).isoformat(), (now() + timedelta(seconds=1.4)).isoformat())
    backend = Path(__file__).resolve().parents[1]
    environment = {"PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": os.pathsep.join([str(backend), str(backend / "tests")])}
    if "LTV_TEST_NETWORK_LOG" in os.environ:
        environment["LTV_TEST_NETWORK_LOG"] = os.environ["LTV_TEST_NETWORK_LOG"]
    process = subprocess.Popen([sys.executable, "-m", "core.execution.receipt_worker", str(root.root), operation["id"]],
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=environment, start_new_session=True)
    try:
        assert process.wait(timeout=4) == 70
        assert op.path("worker.json").exists() and not op.path("started.json").exists()
        assert root.inspect(operation)["status"] == "incomplete"
    finally:
        process.stdin.close()
        if process.poll() is None:
            process.kill()
            process.wait(timeout=3)


@pytest.mark.parametrize("options,late_field", [
    ({"delay": 1.2}, "response_received_at"), ({"commit_delay": 1.2}, "record_durable_at"),
    ({"notify_delay": 1.2}, "durable_confirmation_at"),
])
def test_arrival_or_durable_confirmation_crossing_deadline_stays_isolated(env, options, late_field):
    from core.execution.receipts import timestamp

    change_policy(env, request_deadline_seconds=1)
    counter = configure(env, observation_wait=None, **options)
    task = env.source_task()
    granted = env.grant(task)
    env.action(task, granted["execution"])
    final = env.wait(task, {"needs_reconciliation"})
    operation, _ = persisted(env)
    original = env.runtime.store.path("state.json").read_bytes()
    summary = wait_inspection(env, task, operation, {"candidate_found"}, require_notification=True)
    op = ReceiptStore(env.runtime.store.root / "receipts").operation(operation["id"])
    record_id = next(op.root.glob("*.record.json")).name.removesuffix(".record.json")
    value = op.read(record_id + ".commit.json")[late_field] if late_field == "record_durable_at" else summary[late_field]
    assert timestamp(value) >= timestamp(operation["permit"]["deadline_at"])
    assert operation["transport_diagnostic"] == {"code": "OVERALL_DEADLINE", "phase": "deadline"}
    assert operation["raw"] is None and operation["tool_result"] is None and operation["local_units"] is None
    assert len(posts(counter)) == 1 and env.runtime.store.path("state.json").read_bytes() == original
    assert env.observe(task) == final and summary["isolated"] is True and summary["recovery_performed"] is False


def test_late_worker_error_cannot_overwrite_fixed_deadline_diagnostic(env):
    change_policy(env, request_deadline_seconds=1)
    counter = configure(env, observation_wait=None, delay=1.2, fault="read_error")
    task = env.source_task()
    granted = env.grant(task)
    env.action(task, granted["execution"])
    env.wait(task, {"needs_reconciliation"})
    operation, _ = persisted(env)
    original = env.runtime.store.path("state.json").read_bytes()
    assert operation["transport_diagnostic"]["code"] == "OVERALL_DEADLINE"
    assert wait_inspection(env, task, operation, {"capture_error"})["recovery_performed"] is False
    op = ReceiptStore(env.runtime.store.root / "receipts").operation(operation["id"])
    assert op.read("error.json")["code"] == "READ_TIMEOUT"
    assert env.runtime.store.path("state.json").read_bytes() == original and len(posts(counter)) == 1


@pytest.mark.parametrize("fault,status", [
    ("fsync_fail", "capture_error"), ("crash_before_link", "corrupt"), ("crash_before_seal", "incomplete"), ("partial_ready", "corrupt"),
    ("oversize", "capture_error"), ("truncated_stream", "capture_error"),
])
def test_uncommitted_partial_or_excessive_receipts_never_become_candidates(env, fault, status):
    counter = configure(env, fault=fault)
    task = env.source_task()
    granted = env.grant(task)
    env.action(task, granted["execution"])
    final = env.wait(task, {"needs_reconciliation"})
    operation, _ = persisted(env)
    op = ReceiptStore(env.runtime.store.root / "receipts").operation(operation["id"])
    end = time.monotonic() + 3
    while not op.path("worker.json").exists() and time.monotonic() < end:
        time.sleep(0.01)
    wait_process_exit(op.read("worker.json")["worker_pid"])
    assert env.runtime.inspect_receipts(task["id"], operation["id"])["inspection"]["status"] == status
    assert len(posts(counter)) == final["ledger"]["call_count"] == 1 and final["ledger"]["held_cny"] == granted["execution"]["quotes"][0]["per_call_hold_cny"]
    assert operation["raw"] is None and operation["local_units"] is None and operation["tool_result"] is None


@pytest.mark.parametrize("fault", ["key_body", "key_escaped", "key_header", "key_inner_action"])
def test_detached_worker_never_persists_plain_or_semantically_decoded_key_echo(env, monkeypatch, fault):
    monkeypatch.setenv("SYNTHETIC_UPSTREAM_KEY", "synthetic-secret")
    counter = configure(env, fault=fault, observation_wait=None)
    task = env.source_task()
    granted = env.grant(task)
    env.action(task, granted["execution"])
    final = env.wait(task, {"needs_reconciliation"})
    operation, _ = persisted(env)
    summary = wait_inspection(env, task, operation, {"capture_error"})
    op = ReceiptStore(env.runtime.store.root / "receipts").operation(operation["id"])
    assert op.read("error.json")["code"] == "CREDENTIAL_ECHO"
    assert not list(op.root.glob("*.record.json")) and summary["body_sha256"] is None
    assert b"synthetic-secret" not in b"".join(tree_bytes(env.runtime.store.root).values())
    assert posts(counter)[0]["credential_in_environment"] is False
    assert len(posts(counter)) == final["ledger"]["call_count"] == 1 and operation["raw"] is None


def committed_candidate(tmp_path):
    root, op, operation = prepared_store(tmp_path)
    raw = synthetic_raw()
    record_id = op.commit(binding_for(operation), raw, "SYNTHETIC-PRIVATE-KEY")
    op.notify(binding_for(operation), record_id)
    assert root.inspect(operation)["status"] == "candidate_found"
    return root, op, operation, record_id, raw


def test_duplicate_is_idempotent_and_different_body_is_ambiguous(tmp_path):
    root, op, operation, record_id, raw = committed_candidate(tmp_path)
    original = tree_bytes(root.root)
    assert op.commit(binding_for(operation), raw, "SYNTHETIC-PRIVATE-KEY") == record_id
    op.notify(binding_for(operation), record_id)
    assert tree_bytes(root.root) == original
    other = op.commit(binding_for(operation), synthetic_raw(b'{"synthetic":"different"}'), "SYNTHETIC-PRIVATE-KEY")
    op.notify(binding_for(operation), other)
    before = tree_bytes(root.root)
    summary = root.inspect(operation)
    assert summary["status"] == "ambiguous" and summary["candidate_count"] == 2
    assert summary["binding_verified"] is False and summary["recovery_performed"] is False
    assert tree_bytes(root.root) == before


@pytest.mark.parametrize("bad", ["first_record", "last_record", "orphan_ready", "bad_ready", "unsealed_candidate", "temporary_fragment"])
def test_any_bad_or_incomplete_candidate_prevents_confirmation_beside_good_one(tmp_path, bad):
    root, op, operation, record_id, _ = committed_candidate(tmp_path)
    if bad in {"first_record", "last_record"}:
        op.path(("0" if bad == "first_record" else "f") * 64 + ".record.json").write_text('{"bad":"SYNTHETIC-SECRET"}')
    elif bad == "orphan_ready":
        op.path("f" * 64 + ".ready.json").write_text("{}")
    elif bad == "bad_ready":
        op.path(record_id + ".ready.json").write_text("{")
    elif bad == "temporary_fragment":
        op.path(".receipt-fragment").write_text("{")
    else:
        other = op.commit(binding_for(operation), synthetic_raw(b'{"different":true}'), "SYNTHETIC-PRIVATE-KEY")
        op.path(other + ".commit.json").unlink()
    before = tree_bytes(root.root)
    summary = root.inspect(operation)
    assert summary["status"] in {"corrupt", "incomplete"} and summary["binding_verified"] is False
    assert summary["body_sha256"] is None and summary["recovery_performed"] is False
    assert root.ready_raw(operation)[0] is None and tree_bytes(root.root) == before


@pytest.mark.parametrize("field", ["operation_id", "wire_hash", "deadline_at", "missing_binding"])
def test_claim_binding_mismatch_or_missing_fields_cannot_borrow_good_body(tmp_path, field):
    from core.execution.receipts import seal

    root, op, operation, _, _ = committed_candidate(tmp_path)
    claim = op.read("claim.json")
    if field == "missing_binding":
        claim.pop("binding")
    else:
        claim["binding"][field] = {"operation_id": "3" * 32, "wire_hash": "3" * 64, "deadline_at": "2090-01-01T00:00:00+00:00"}[field]
        claim["binding_hash"] = digest(claim["binding"])
    claim = seal({key: value for key, value in claim.items() if key != "claim_hash"}, "claim_hash")
    op.path("claim.json").write_text(json.dumps(claim))
    before = tree_bytes(root.root)
    summary = root.inspect(operation)
    assert summary["status"] == ("corrupt" if field == "missing_binding" else "binding_mismatch")
    assert summary["binding_verified"] is False and tree_bytes(root.root) == before


@pytest.mark.parametrize("fault", ["body_hash", "record_truncated", "commit_hash", "ready_hash", "future_ready", "backwards_received"])
def test_hash_corruption_and_impossible_timestamps_are_not_used(tmp_path, fault):
    from core.execution.receipts import seal

    root, op, operation, record_id, _ = committed_candidate(tmp_path)
    if fault in {"body_hash", "backwards_received"}:
        value = op.read(record_id + ".record.json")
        if fault == "body_hash":
            value["raw"]["body_base64"] = base64.b64encode(b"changed body").decode()
        else:
            value["raw"]["received_at"] = "2000-01-01T00:00:00+00:00"
        value = seal({key: item for key, item in value.items() if key != "envelope_hash"}, "envelope_hash")
        op.path(record_id + ".record.json").write_text(json.dumps(value))
    elif fault == "record_truncated":
        op.path(record_id + ".record.json").write_bytes(b"{")
    elif fault == "commit_hash":
        op.path(record_id + ".commit.json").write_text("{}")
    else:
        value = op.read(record_id + ".ready.json")
        if fault == "future_ready":
            value["durable_confirmation_at"] = "2090-01-01T00:00:00+00:00"
            value = seal({key: item for key, item in value.items() if key != "ready_hash"}, "ready_hash")
        else:
            value["ready_hash"] = "0" * 64
        op.path(record_id + ".ready.json").write_text(json.dumps(value))
    before = tree_bytes(root.root)
    assert root.inspect(operation)["status"] == "corrupt" and root.ready_raw(operation)[0] is None
    assert tree_bytes(root.root) == before


def tree_snapshot(root):
    import os
    import stat

    values = {"root_exists": root.exists()}
    if root.exists():
        for path in root.rglob("*"):
            info = path.lstat()
            name = str(path.relative_to(root))
            if path.is_symlink():
                values[name] = ("link", os.readlink(path))
            elif stat.S_ISREG(info.st_mode):
                values[name] = ("file", stat.S_IMODE(info.st_mode), info.st_mtime_ns, path.read_bytes())
            else:
                values[name] = ("directory_or_special", info.st_mode, info.st_mtime_ns)
    return values


def test_missing_and_invalid_inspection_requests_create_no_directories_or_files(env):
    root = env.runtime.store.root.parent
    before = tree_snapshot(root)
    url = "/api/planner/tasks/" + "0" * 32 + "/execution/operations/" + "1" * 32 + "/receipts/inspect"
    assert env.client.get(url).status_code == 404
    assert env.client.get(url + "?raw=SYNTHETIC-SECRET").status_code == 422
    assert env.client.request("GET", url, content=b'{"raw":"SYNTHETIC-SECRET"}').status_code == 422
    assert env.client.get(url.replace("1" * 32, "not-an-id")).status_code == 422
    assert env.client.post(url, json={}).status_code == 405
    assert tree_snapshot(root) == before and not env.runtime.store.root.exists()


def test_inspect_bypasses_recover_transaction_normalizer_and_tools(env, monkeypatch):
    env.provider.fault = "timeout"
    task = env.source_task()
    granted = env.grant(task)
    env.action(task, granted["execution"])
    env.wait(task, {"needs_reconciliation"})
    operation, _ = persisted(env)
    before = tree_snapshot(env.runtime.store.root.parent)

    def forbidden(*args, **kwargs):
        pytest.fail("Read-only inspection invoked a recovery/execution/write seam")

    monkeypatch.setattr(env.runtime.store, "transaction", forbidden)
    monkeypatch.setattr(env.runtime, "get", forbidden)
    monkeypatch.setattr(env.runtime, "process_receipt", forbidden)
    monkeypatch.setattr(env.runtime, "events", forbidden)
    response = env.client.get(inspect_url(env, task, operation))
    assert response.status_code == 200 and response.json()["inspection"]["status"] == "missing"
    assert tree_snapshot(env.runtime.store.root.parent) == before and len(env.provider.calls) == 1


def cli_inspect(root, task_id, operation_id):
    import os
    import subprocess
    import sys
    from pathlib import Path

    backend = Path(__file__).resolve().parents[1]
    environment = {"PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": str(backend)}
    if "LTV_TEST_NETWORK_LOG" in os.environ:
        environment["LTV_TEST_NETWORK_LOG"] = os.environ["LTV_TEST_NETWORK_LOG"]
    return subprocess.run([sys.executable, "-m", "core.execution.receipts", "inspect", "--execution-root", str(root),
        "--task-id", task_id, "--operation-id", operation_id], capture_output=True, text=True, env=environment, timeout=5)


def test_old_journal_http_and_reopened_cli_remain_byte_for_byte_read_only(env):
    from test_planner_chat_output import restore_legacy

    fixture, path, original = restore_legacy(env)
    payload = env.runtime.store.read_only()
    operation = next(op for op in payload["operations"].values() if op["task_id"] == fixture["received_task"]["id"])
    task = fixture["received_task"]
    before = tree_snapshot(env.runtime.store.root.parent)
    for _ in range(2):
        response = env.client.get(inspect_url(env, task, operation))
        assert response.status_code == 200 and response.json()["inspection"]["status"] == "missing"
        result = cli_inspect(env.runtime.store.root, task["id"], operation["id"])
        assert result.returncode == 0 and json.loads(result.stdout) == response.json()
    wrong = cli_inspect(env.runtime.store.root, "0" * 32, operation["id"])
    assert wrong.returncode == 1 and json.loads(wrong.stdout) == {"error": "INSPECTION_UNAVAILABLE"}
    assert path.read_bytes() == original and tree_snapshot(env.runtime.store.root.parent) == before and env.provider.calls == []


def test_corrupt_execution_journal_error_branch_is_read_only_and_redacted(env):
    env.runtime.store.root.mkdir()
    env.runtime.store.path("state.json").write_text('{"SYNTHETIC-PRIVATE-CORRUPTION":')
    before = tree_snapshot(env.runtime.store.root.parent)
    url = "/api/planner/tasks/" + "0" * 32 + "/execution/operations/" + "1" * 32 + "/receipts/inspect"
    response = env.client.get(url)
    assert response.status_code == 503 and "SYNTHETIC-PRIVATE-CORRUPTION" not in response.text
    assert tree_snapshot(env.runtime.store.root.parent) == before


@pytest.mark.parametrize("scope", ["parent", "same", "child"])
def test_public_static_parent_same_child_blocks_inspect_before_reading(env, scope):
    from fastapi.staticfiles import StaticFiles

    roots = {"parent": env.runtime.store.root.parent, "same": env.runtime.store.root, "child": env.runtime.store.root / "receipts"}
    directory = roots[scope]
    directory.mkdir(parents=True, exist_ok=True)
    env.client.app.mount("/unsafe-static", StaticFiles(directory=directory))
    before = tree_snapshot(env.runtime.store.root.parent)
    response = env.client.get("/api/planner/tasks/" + "0" * 32 + "/execution/operations/" + "1" * 32 + "/receipts/inspect")
    assert response.status_code == 503 and str(directory) not in response.text
    assert tree_snapshot(env.runtime.store.root.parent) == before


def test_read_paths_reject_symlinks_hardlinks_and_fifo_without_external_mutation(tmp_path):
    import os

    from core.execution.receipts import read_json

    external = tmp_path.resolve() / "external.json"
    external.write_text('{"SYNTHETIC-PRIVATE":"data"}')
    linked = tmp_path.resolve() / "linked.json"
    linked.symlink_to(external)
    with pytest.raises(Exception):
        read_json(linked)
    hard = tmp_path.resolve() / "hard.json"
    os.link(external, hard)
    with pytest.raises(Exception):
        read_json(hard)
    fifo = tmp_path.resolve() / "fifo.json"
    os.mkfifo(fifo)
    started = time.monotonic()
    with pytest.raises(Exception):
        read_json(fifo)
    assert time.monotonic() - started < 1
    assert external.read_text() == '{"SYNTHETIC-PRIVATE":"data"}'


@pytest.mark.parametrize("filename", ["../escape.json", "/tmp/escaped-receipt.json", "a/b.json"])
def test_writer_rejects_path_escape_before_creating_files(tmp_path, filename):
    store = ReceiptStore(tmp_path.resolve() / "receipts")
    before = tree_snapshot(tmp_path.resolve())
    with pytest.raises(Exception):
        store.write_new(filename, {"value": "SYNTHETIC"})
    assert tree_snapshot(tmp_path.resolve()) == before


def test_receipt_modes_body_boundary_and_capacity_rejection_markers_are_bounded(tmp_path, monkeypatch):
    import stat

    import core.execution.receipts as module

    root, op, operation, _, _ = committed_candidate(tmp_path)
    assert stat.S_IMODE(root.root.stat().st_mode) == stat.S_IMODE(op.root.stat().st_mode) == 0o700
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in op.root.iterdir())
    monkeypatch.setattr(module, "MAX_CANDIDATES", 1)
    before = tree_bytes(root.root)
    with pytest.raises(module.ReceiptError):
        op.commit(binding_for(operation), synthetic_raw(b'{"different":true}'), "SYNTHETIC-PRIVATE-KEY")
    assert tree_bytes(root.root) == before
    op.error(binding_for(operation), "CAPACITY_LIMIT")
    op.error(binding_for(operation), "TRANSPORT_ERROR")
    assert len(list(op.root.glob("error.json"))) == 1 and op.read("error.json")["code"] == "CAPACITY_LIMIT"
    assert root.inspect(operation)["status"] == "capture_error"


def test_full_store_reserves_space_for_fixed_rejection_markers(tmp_path, monkeypatch):
    import core.execution.receipts as module

    root, op, operation = prepared_store(tmp_path)
    total = sum(path.stat().st_size for path in root.root.rglob("*") if path.is_file())
    monkeypatch.setattr(module, "MAX_STORE", total + 4096)
    assert root.write_new("padding.json", {"padding": "x" * 2000})
    with pytest.raises(module.ReceiptError):
        root.write_new("more.json", {"padding": "x" * 100})
    op.error(binding_for(operation), "CAPACITY_LIMIT")
    assert op.read("error.json")["code"] == "CAPACITY_LIMIT"
    assert sum(path.stat().st_size for path in root.root.rglob("*") if path.is_file()) <= module.MAX_STORE


@pytest.mark.parametrize("body_length", [1048576, 1048577])
def test_body_capacity_is_strict_before_candidate_persistence(tmp_path, body_length):
    from core.execution.receipts import ReceiptError

    root, op, operation = prepared_store(tmp_path)
    raw = synthetic_raw(b"x" * body_length)
    if body_length == 1048576:
        record_id = op.commit(binding_for(operation), raw, "SYNTHETIC-PRIVATE-KEY")
        op.notify(binding_for(operation), record_id)
        assert root.inspect(operation)["status"] == "candidate_found"
    else:
        before = tree_bytes(root.root)
        with pytest.raises(ReceiptError):
            op.commit(binding_for(operation), raw, "SYNTHETIC-PRIVATE-KEY")
        assert tree_bytes(root.root) == before


@pytest.mark.parametrize("kind", ["platform-error", "bill-only", "unverified-query"])
def test_external_records_never_synthesize_response_or_free_billing(tmp_path, monkeypatch, kind):
    import socket

    root, op, operation = prepared_store(tmp_path)
    op.path(kind + ".json").write_text(json.dumps({"status": 524, "charge": "0", "SYNTHETIC-PRIVATE": "not a bound HTTP receipt"}))
    monkeypatch.setattr(socket.socket, "connect", lambda *a, **k: pytest.fail("Inspection queried an unverified provider endpoint"))
    before = tree_snapshot(root.root)
    summary = root.inspect(operation)
    assert summary["status"] != "candidate_found" and summary["recovery_performed"] is False and summary["body_sha256"] is None
    assert "charge" not in json.dumps(summary) and "free" not in json.dumps(summary)
    assert tree_snapshot(root.root) == before


def test_worker_cannot_start_post_if_bootstrap_crosses_original_deadline(env):
    change_policy(env, request_deadline_seconds=1)
    counter = configure(env, factory_delay=1.2)
    task = env.source_task()
    granted = env.grant(task)
    env.action(task, granted["execution"])
    final = env.wait(task, {"needs_reconciliation"})
    operation, _ = persisted(env)
    original = env.runtime.store.path("state.json").read_bytes()
    summary = wait_inspection(env, task, operation, {"capture_error"})
    op = ReceiptStore(env.runtime.store.root / "receipts").operation(operation["id"])
    assert op.read("error.json")["code"] == "OVERALL_DEADLINE"
    assert not op.path("started.json").exists() and posts(counter) == []
    assert final["ledger"]["call_count"] == 1 and final["ledger"]["held_cny"] == granted["execution"]["quotes"][0]["per_call_hold_cny"]
    assert summary["recovery_performed"] is False and env.runtime.store.path("state.json").read_bytes() == original


@pytest.mark.parametrize("fault,deadline,grace,exit_seconds", [
    ("clock_forward", 5, 1, 1.5), ("clock_backward", 1, 0.5, 3),
])
def test_watchdog_clock_shift_keeps_absolute_and_elapsed_resource_bounds(env, fault, deadline, grace, exit_seconds):
    import os
    import signal

    change_policy(env, request_deadline_seconds=deadline)
    counter = configure(env, fault=fault, grace=grace)
    task = env.source_task()
    granted = env.grant(task)
    env.action(task, granted["execution"])
    env.wait(task, {"needs_reconciliation"})
    operation, _ = persisted(env)
    original = env.runtime.store.path("state.json").read_bytes()
    op = ReceiptStore(env.runtime.store.root / "receipts").operation(operation["id"])
    end = time.monotonic() + 2
    while not counter.exists() and time.monotonic() < end:
        time.sleep(0.01)
    assert len(posts(counter)) == 1
    worker_pid = op.read("worker.json")["worker_pid"]
    trace_path = counter.with_name(counter.name + ".clock")
    end = time.monotonic() + 2
    while not trace_path.exists() and time.monotonic() < end:
        time.sleep(0.01)
    assert json.loads(trace_path.read_text()) == {"main_module": "core.execution.receipt_worker", "shift_seconds": 3600 if fault == "clock_forward" else -3600}
    try:
        wait_process_exit(worker_pid, seconds=exit_seconds)
        assert env.runtime.store.path("state.json").read_bytes() == original
        assert env.runtime.inspect_receipts(task["id"], operation["id"])["inspection"]["status"] != "candidate_found"
    finally:
        if process_alive(worker_pid):
            os.kill(worker_pid, signal.SIGKILL)
            wait_process_exit(worker_pid)
