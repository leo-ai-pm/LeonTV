"""D15 distinct empirical authority over the shared one-request capture lane."""
import base64
import copy
import json
import time
from dataclasses import FrozenInstanceError, asdict, replace
from datetime import timedelta

import pytest
from empirical_worker_fixtures import descriptor_for, rebind
from test_planner_execution import env as env
from test_planner_execution import no_network as no_network
from test_planner_receipts import (
    posts,
    prepared_store,
    synthetic_raw,
    tree_bytes,
    wait_process_exit,
)

from core.creator.store import digest
from core.execution.policy import now
from core.execution.receipt_transport import CapturedTransport
from core.execution.receipts import ObservationEnded, ReceiptCaptureSettings, ReceiptStore
from core.execution.transport import ResponseUnconfirmed
from experiments.planner_live.empirical_capture import (
    EmpiricalCaptureAdapter,
    EmpiricalInputRejected,
    FrozenEmpiricalDescriptor,
    empirical_wire_for,
)


def test_formal_actual_seam_refuses_empirical_without_fabricated_permit(env, monkeypatch):
    import subprocess

    import httpx

    descriptor = {"kind": "empirical_planner_text_once", "scope": "empirical_planner_text_once", "risk_fen": 100, "begin_status": "submitting"}
    monkeypatch.setattr(httpx, "Client", lambda *a, **k: pytest.fail("Empirical input constructed formal HTTP client"))
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail("Empirical input spawned formal worker"))
    transport = CapturedTransport(None, None, env.runtime.store, ReceiptCaptureSettings())
    with pytest.raises(ResponseUnconfirmed) as error:
        transport.perform(descriptor, {}, "synthetic-secret")
    assert error.value.code == "PERMIT_REJECTED" and not env.runtime.store.root.exists()


def test_neutral_capture_entry_exists_without_formal_authority_duck_typing():
    import core.execution.receipt_transport as module

    assert callable(getattr(module, "capture_once", None)), "No neutral frozen-request/binding delivery seam"


def test_explicit_binding_inspector_does_not_require_a_fake_formal_operation(tmp_path):
    from core.creator.store import digest

    root, op, legacy = prepared_store(tmp_path)
    binding = {"operation_id": legacy["id"], "task_id": legacy["task_id"], "kind": "empirical_planner_text_once",
        "scope": "empirical_planner_text_once", "deadline_at": legacy["permit"]["deadline_at"],
        "authorization_hash": "a" * 64, "intent_hash": "b" * 64, "risk_allocation_hash": "c" * 64,
        "begin_hash": "d" * 64, "source_snapshot_hash": "e" * 64, "rules_hash": "f" * 64, "context_hash": "1" * 64, "wire_hash": "2" * 64}
    # Synthetic storage uses the actual independently bound claim, no formal permit.
    claim = op.read("claim.json")
    claim["binding"], claim["binding_hash"] = binding, digest(binding)
    claim["claim_hash"] = digest({key: value for key, value in claim.items() if key != "claim_hash"})
    op.path("claim.json").write_text(__import__("json").dumps(claim))
    record_id = op.commit(binding, synthetic_raw(), "SYNTHETIC-PRIVATE-KEY")
    op.notify(binding, record_id)
    before = {p.name: p.read_bytes() for p in op.root.iterdir() if p.is_file()}
    summary = root.inspect_binding(binding["operation_id"], binding)
    assert summary["status"] == "candidate_found" and summary["isolated"] is True and summary["recovery_performed"] is False
    assert before == {p.name: p.read_bytes() for p in op.root.iterdir() if p.is_file()}


def adapter_for(tmp_path, *, observation_wait=None, **options):
    return EmpiricalCaptureAdapter(tmp_path.resolve() / "empirical-receipts", settings=ReceiptCaptureSettings(
        observation_wait_seconds=observation_wait, worker_factory="empirical_worker_fixtures:make_transport",
        worker_options={"counter": str(tmp_path / "posts.jsonl"), "wire_trace": str(tmp_path / "wire-trace.json"), **options}))


def worker_exit(adapter, descriptor, seconds=8):
    op = adapter.receipts.operation(descriptor.intent.capture_id)
    limit = time.monotonic() + 2
    while not op.path("worker.json").exists() and time.monotonic() < limit:
        time.sleep(0.01)
    pid = op.read("worker.json")["worker_pid"]
    wait_process_exit(pid, seconds=seconds)
    return op


@pytest.mark.parametrize("field,value", [
    ("capture_id", "../outside"), ("capture_id", "A" * 32), ("intent_id", ""), ("operation_key", "bad//key"),
    ("task_id", None), ("authorization_hash", "x" * 64), ("source_snapshot_hash", "b" * 63),
    ("rules_hash", ""), ("context_hash", 12), ("wire_hash", True), ("version", True), ("version", 2),
    ("kind", "formal"), ("scope", "formal"), ("environment", "production"), ("detailed_tool_feedback", 1),
    ("chat_output_parameter", "max_output_tokens"), ("max_output_tokens", True), ("max_output_tokens", 8193),
    ("request_deadline_seconds", True), ("request_deadline_seconds", 121), ("request_deadline_seconds", 0),
    ("endpoint", "http://synthetic.example/v1/chat/completions"), ("endpoint", "https://user:pass@synthetic.example/v1/chat/completions"),
    ("endpoint", "https://synthetic.example/v1/responses"), ("endpoint", "https://synthetic.example/v1/chat/completions?q=secret"),
    ("model", "bad\nmodel"), ("issued_at", "2026-10-23T01:00:00"), ("deadline_at", "invalid-private-value"),
])
def test_intent_rejects_invalid_authority_and_route_without_io(tmp_path, monkeypatch, field, value):
    import subprocess

    descriptor = descriptor_for()
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail("Invalid intent spawned"))
    before = tree_bytes(tmp_path)
    with pytest.raises(EmpiricalInputRejected, match="^EMPIRICAL_INPUT_REJECTED$"):
        replace(descriptor.intent, **{field: value})
    assert tree_bytes(tmp_path) == before


@pytest.mark.parametrize("field,value", [
    ("allocation_id", "../path"), ("intent_id", ""), ("intent_hash", "bad"), ("operation_key", ""),
    ("authorization_hash", "x" * 64), ("parent_ledger_hash", ""), ("begin_hash", None),
    ("begun_at", "invalid-private-value"), ("risk_fen", True), ("risk_fen", 99), ("risk_fen", 101),
    ("risk_basis", "certified_billing_bound"), ("status", "reserved"), ("status", "settled"), ("version", True), ("kind", "formal"),
])
def test_risk_reference_requires_exact_empirical_begin_contract(field, value):
    with pytest.raises(EmpiricalInputRejected, match="^EMPIRICAL_INPUT_REJECTED$"):
        replace(descriptor_for().allocation, **{field: value})


@pytest.mark.parametrize("field", ["intent_id", "intent_hash", "operation_key", "authorization_hash"])
def test_actual_descriptor_cross_links_reject_another_allocation(field):
    descriptor = descriptor_for()
    value = "f" * 64 if field.endswith("hash") else "another-synthetic-reference"
    allocation = replace(descriptor.allocation, **{field: value})
    with pytest.raises(EmpiricalInputRejected):
        rebind(descriptor, allocation=allocation)


@pytest.mark.parametrize("change", ["extra", "model", "messages", "stream", "n", "temperature", "bool_n", "bool_temperature", "float_tokens", "missing", "formal_wire"])
def test_independent_wire_contract_rejects_semantic_changes_even_with_matching_hash(change):
    descriptor = descriptor_for()
    payload = json.loads(descriptor.wire)
    if change == "extra":
        payload["tools"] = []
    elif change == "model":
        payload["model"] = "another-model"
    elif change == "messages":
        payload["messages"][0]["content"] = "another-context"
    elif change == "stream":
        payload["stream"] = True
    elif change == "n":
        payload["n"] = 2
    elif change == "temperature":
        payload["temperature"] = 1
    elif change == "bool_n":
        payload["n"] = True
    elif change == "bool_temperature":
        payload["temperature"] = False
    elif change == "float_tokens":
        payload["max_tokens"] = 2048.0
    elif change == "missing":
        payload.pop("stream")
    else:
        for field in ("stream", "n", "temperature"):
            payload.pop(field)
    wire = json.dumps(payload, ensure_ascii=False)
    intent = replace(descriptor.intent, wire_hash=digest(wire))
    with pytest.raises(EmpiricalInputRejected):
        rebind(descriptor, intent=intent, wire=wire)


def test_actual_wire_root_contract_hashes_exact_original_bytes_and_frozen_context():
    context = [{"role": "tool", "content": {"ok": True, "中文": "冻结内容"}}]
    descriptor = descriptor_for(context=context)
    payload = {"model": "synthetic-text-model", "max_tokens": 2048,
        "messages": [{"role": "user", "content": json.dumps(context[0]["content"], ensure_ascii=False)}],
        "stream": False, "n": 1, "temperature": 0}
    assert descriptor.wire == json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    spaced = json.dumps(payload, ensure_ascii=False)
    rebuilt = rebind(descriptor, intent=replace(descriptor.intent, wire_hash=digest(spaced)), wire=spaced)
    assert rebuilt.wire == spaced and rebuilt.intent.wire_hash != descriptor.intent.wire_hash
    context[0]["content"]["中文"] = "later mutation"
    assert "later mutation" not in descriptor.context_json
    with pytest.raises(FrozenInstanceError):
        descriptor.wire = "later mutation"
    assert "billing_bound_verified" not in descriptor.frozen()["intent"]
    assert not ({"permit_hash", "grant_id", "quote_id", "policy_hash"} & set(descriptor.binding()))


def test_real_shape_reference_ids_and_nested_context_order_survive_freeze_and_actual_capture(tmp_path):
    context = [
        {"role": "system", "content": {"z_system": "SYNTHETIC", "a_rules": {"z_last": 1, "a_first": 2}}},
        {"role": "tool", "content": {"z_feedback": "SYNTHETIC", "ok": False, "a_issues": {"z": 1, "a": 2}}},
    ]
    original = descriptor_for(context=context)
    operation = "SYNTHETIC-PLANNER-LIVE-20261023/D01"
    intent = replace(original.intent, intent_id=operation, operation_key=operation)
    allocation = replace(original.allocation, intent_id=operation, intent_hash=intent.intent_hash,
        operation_key=operation, allocation_id=operation + "/risk")
    descriptor = rebind(original, intent=intent, allocation=allocation)
    assert descriptor.context_json == json.dumps(context, ensure_ascii=False, allow_nan=False)
    assert descriptor.wire == empirical_wire_for(context, model=intent.model)
    rebuilt = FrozenEmpiricalDescriptor.from_frozen(json.loads(json.dumps(descriptor.frozen(), ensure_ascii=False)))
    assert rebuilt == descriptor and rebuilt.intent.context_hash == digest(context)
    adapter = adapter_for(tmp_path)
    adapter.capture(rebuilt, "SYNTHETIC-PRIVATE-KEY")
    worker_exit(adapter, rebuilt)
    trace = json.loads((tmp_path / "wire-trace.json").read_text())
    assert trace["wire"] == original.wire and len(posts(tmp_path / "posts.jsonl")) == 1
    assert sorted(p.name for p in adapter.receipts.root.iterdir()) == [".quota.lock", intent.capture_id]


@pytest.mark.parametrize("where", ["descriptor_hash", "intent_hash", "risk_allocation_hash", "wire", "extra", "formal_field", "missing_hash", "missing_kind", "missing_risk_version", "asdict"])
def test_frozen_roundtrip_requires_exact_hashes_and_rejects_formal_fields(where):
    descriptor = descriptor_for()
    assert FrozenEmpiricalDescriptor.from_frozen(descriptor.frozen()) == descriptor
    value = copy.deepcopy(descriptor.frozen())
    if where == "intent_hash":
        value["intent"]["intent_hash"] = "0" * 64
    elif where == "risk_allocation_hash":
        value["allocation"]["risk_allocation_hash"] = "0" * 64
    elif where == "formal_field":
        value["intent"]["billing_bound_verified"] = True
    elif where == "extra":
        value["permit"] = {}
    elif where == "missing_hash":
        value["intent"].pop("intent_hash")
    elif where == "missing_kind":
        value["intent"].pop("kind")
    elif where == "missing_risk_version":
        value["allocation"].pop("version")
    elif where == "asdict":
        value = asdict(descriptor)
    else:
        value[where] = "SYNTHETIC-PRIVATE-INVALID"
    with pytest.raises(EmpiricalInputRejected, match="^EMPIRICAL_INPUT_REJECTED$"):
        FrozenEmpiricalDescriptor.from_frozen(value)


@pytest.mark.parametrize("case", ["expired", "future_begin", "long_interval", "reversed_interval", "before_issue", "invalid_key", "formal_object", "live_mock"])
def test_pre_dispatch_checks_create_no_receipt_or_process(tmp_path, monkeypatch, case):
    import subprocess

    descriptor = descriptor_for(environment="empirical_live" if case == "live_mock" else "synthetic")
    adapter = adapter_for(tmp_path)
    key = "SYNTHETIC-PRIVATE-KEY"
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail("Rejected descriptor spawned"))
    with pytest.raises(EmpiricalInputRejected):
        if case == "expired":
            old = now() - timedelta(seconds=10)
            intent = replace(descriptor.intent, issued_at=old.isoformat(), deadline_at=(old + timedelta(seconds=5)).isoformat())
            allocation = replace(descriptor.allocation, intent_hash=intent.intent_hash, begun_at=old.isoformat())
            descriptor = rebind(descriptor, intent=intent, allocation=allocation)
        elif case == "future_begin":
            descriptor = rebind(descriptor, allocation=replace(descriptor.allocation, begun_at=(now() + timedelta(seconds=1)).isoformat()))
        elif case in {"long_interval", "reversed_interval"}:
            intent = replace(descriptor.intent, deadline_at=(now() + timedelta(seconds=121 if case == "long_interval" else -2)).isoformat())
            descriptor = rebind(descriptor, intent=intent)
        elif case == "before_issue":
            descriptor = rebind(descriptor, allocation=replace(descriptor.allocation, begun_at=(now() - timedelta(seconds=2)).isoformat()))
        elif case == "invalid_key":
            key = ""
        elif case == "formal_object":
            descriptor = {"grant_id": "synthetic", "permit": {}}
        adapter.capture(descriptor, key)
    assert not adapter.receipts.root.exists()


@pytest.mark.parametrize("grace", [0.1, 4.9, 5.1, 30])
def test_empirical_cleanup_cannot_be_extended_or_reinterpreted(tmp_path, grace):
    with pytest.raises(EmpiricalInputRejected):
        EmpiricalCaptureAdapter(tmp_path / "receipts", settings=ReceiptCaptureSettings(cleanup_grace_seconds=grace))
    assert not (tmp_path / "receipts").exists()


@pytest.mark.parametrize("parameter", ["max_tokens", "max_completion_tokens"])
def test_actual_single_http_seam_preserves_wire_and_never_runs_normalizer_or_tools(tmp_path, monkeypatch, parameter):
    import os
    import subprocess

    import core.execution.transport as transport
    import core.planner.protocol as protocol

    descriptor = descriptor_for(parameter=parameter)
    adapter = adapter_for(tmp_path)
    key = "SYNTHETIC-PRIVATE-KEY"
    monkeypatch.setenv("SYNTHETIC_UPSTREAM_KEY", key)
    original_popen = subprocess.Popen

    def popen(*args, **kwargs):
        assert key not in repr(args) and key not in repr(kwargs)
        assert "SYNTHETIC_UPSTREAM_KEY" not in kwargs.get("env", os.environ)
        return original_popen(*args, **kwargs)

    with monkeypatch.context() as capture_patch:
        capture_patch.setattr(subprocess, "Popen", popen)
        capture_patch.setattr(transport, "normalized", lambda *a, **k: pytest.fail("Empirical capture normalized formal policy"))
        capture_patch.setattr(protocol, "execute_tool", lambda *a, **k: pytest.fail("Capture ran a tool"))
        raw = adapter.capture(descriptor, key)
    worker_exit(adapter, descriptor)
    assert len(posts(tmp_path / "posts.jsonl")) == 1
    wire_trace = json.loads((tmp_path / "wire-trace.json").read_text())
    assert wire_trace["wire"] == descriptor.wire and wire_trace["wire_hash"] == descriptor.intent.wire_hash
    assert wire_trace["method"] == "POST" and wire_trace["url"] == descriptor.intent.endpoint
    receipt = json.loads(base64.b64decode(raw["body_base64"]))
    assert raw["http_status"] == 200 and raw["headers"]["x-request-id"] == "req_" + descriptor.intent.capture_id
    assert receipt["id"] == "resp_" + descriptor.intent.capture_id and receipt["model"] == descriptor.intent.model
    assert receipt["choices"][0]["finish_reason"] == "stop" and receipt["usage"] == {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30}
    before = tree_bytes(tmp_path)
    for _ in range(2):
        summary = adapter.inspect(descriptor)["inspection"]
        assert summary["status"] == "candidate_found" and summary["notification_present"] and summary["isolated"] and not summary["recovery_performed"]
    assert before == tree_bytes(tmp_path)
    with pytest.raises(ObservationEnded, match="EXISTING_CAPTURE_REQUIRES_INSPECTION"):
        adapter.capture(descriptor, key)
    assert len(posts(tmp_path / "posts.jsonl")) == 1 and before == tree_bytes(tmp_path)
    assert all(key.encode() not in data for data in tree_bytes(adapter.receipts.root).values())


def test_separate_fresh_synthetic_intents_allow_original_tool_contract_acceptance_only_after_capture(tmp_path):
    from core.planner.protocol import execute_tool

    source = {"snapshot_hash": "b" * 64, "script": "SYNTHETIC two shots of a sea cliff", "requirements": "SYNTHETIC"}
    adapter = adapter_for(tmp_path)
    first = descriptor_for()
    raw = adapter.capture(first, "SYNTHETIC-PRIVATE-KEY")
    worker_exit(adapter, first)
    content = json.loads(base64.b64decode(raw["body_base64"]))["choices"][0]["message"]["content"]
    feedback, plan, read = execute_tool(content, source, False, detailed_feedback=True)
    assert feedback["ok"] and read and plan is None
    context = json.loads(first.context_json) + [{"role": "tool", "content": feedback}]
    second = descriptor_for(context=context)
    assert first.intent.intent_hash != second.intent.intent_hash and first.allocation.allocation_id != second.allocation.allocation_id
    raw = adapter.capture(second, "SYNTHETIC-PRIVATE-KEY")
    worker_exit(adapter, second)
    content = json.loads(base64.b64decode(raw["body_base64"]))["choices"][0]["message"]["content"]
    feedback, plan, read = execute_tool(content, source, read, detailed_feedback=True)
    assert feedback["code"] == "STRUCTURE_VALID_NOT_REVIEWED" and plan["duration_ms"] == sum(s["duration_ms"] for s in plan["shots"]) == 15000
    assert plan["source_snapshot_hash"] == source["snapshot_hash"] and len(posts(tmp_path / "posts.jsonl")) == 2


@pytest.mark.parametrize("fault,status,notification", [
    ("before_commit", "incomplete", False), ("after_commit", "candidate_found", False),
    ("persist_fail", "capture_error", False), ("fsync_fail", "capture_error", False),
    ("crash_before_link", "corrupt", False), ("crash_before_seal", "incomplete", False),
    ("partial_ready", "corrupt", False), ("read_error", "capture_error", False),
    ("oversize", "capture_error", False), ("truncated_stream", "capture_error", False),
    ("key_body", "capture_error", False), ("key_escaped", "capture_error", False),
    ("key_inner_action", "capture_error", False), ("key_header", "capture_error", False),
])
def test_empirical_lane_reuses_commit_fault_and_key_protection(tmp_path, fault, status, notification):
    descriptor = descriptor_for()
    adapter = adapter_for(tmp_path, observation_wait=0.4, fault=fault)
    with pytest.raises((ResponseUnconfirmed, ObservationEnded)):
        adapter.capture(descriptor, "SYNTHETIC-PRIVATE-KEY")
    op = worker_exit(adapter, descriptor)
    summary = adapter.inspect(descriptor)["inspection"]
    assert summary["status"] == status and summary["notification_present"] is notification
    assert summary["isolated"] and not summary["recovery_performed"] and len(posts(tmp_path / "posts.jsonl")) == 1
    if fault.startswith("key_"):
        assert op.read("error.json")["code"] == "CREDENTIAL_ECHO" and not list(op.root.glob("*.record.json"))
    assert all(b"SYNTHETIC-PRIVATE-KEY" not in value for value in tree_bytes(adapter.receipts.root).values())


@pytest.mark.parametrize("options,late_field", [({"delay": 1.1}, "response_received_at"), ({"notify_delay": 1.1}, "durable_confirmation_at")])
def test_original_deadline_blocks_late_body_or_ready_even_when_complete(tmp_path, options, late_field):
    descriptor = descriptor_for(seconds=0.8)
    adapter = adapter_for(tmp_path, **options)
    with pytest.raises(ResponseUnconfirmed) as error:
        adapter.capture(descriptor, "SYNTHETIC-PRIVATE-KEY")
    assert error.value.code == "OVERALL_DEADLINE"
    worker_exit(adapter, descriptor)
    summary = adapter.inspect(descriptor)["inspection"]
    assert summary["status"] == "candidate_found" and summary["notification_present"] and summary[late_field] > descriptor.intent.deadline_at
    before = tree_bytes(tmp_path)
    with pytest.raises(EmpiricalInputRejected):
        adapter.capture(descriptor, "SYNTHETIC-PRIVATE-KEY")
    assert tree_bytes(tmp_path) == before and len(posts(tmp_path / "posts.jsonl")) == 1


@pytest.mark.parametrize("mutation", ["kind", "binding", "wire", "cleanup"])
def test_worker_rejects_forged_job_before_post(tmp_path, mutation):
    from core.execution.receipt_transport import capture_once

    descriptor = descriptor_for()
    adapter = adapter_for(tmp_path, observation_wait=1)
    binding = descriptor.binding()
    job = {"kind": descriptor.intent.kind, "binding": binding, "descriptor": descriptor.frozen(),
        "worker_factory": adapter.settings.worker_factory, "worker_options": adapter.settings.worker_options}
    settings = adapter.settings
    if mutation == "kind":
        job["kind"] = "unknown-job-kind"
    elif mutation == "binding":
        job["binding"] = {**binding, "begin_hash": "f" * 64}
    elif mutation == "wire":
        job["descriptor"]["wire"] += "tampered"
    else:
        settings = replace(settings, cleanup_grace_seconds=6)
    with pytest.raises(ResponseUnconfirmed):
        capture_once(adapter.receipts, binding, job, "SYNTHETIC-PRIVATE-KEY", settings)
    op = worker_exit(adapter, descriptor)
    assert op.read("error.json")["code"] == "WORKER_INPUT_REJECTED" and not posts(tmp_path / "posts.jsonl")


def test_missing_and_expired_inspect_are_read_only_and_never_load_config_or_ledger(tmp_path, monkeypatch):
    import subprocess

    from core.execution.store import ExecutionStore

    descriptor = descriptor_for()
    adapter = EmpiricalCaptureAdapter(tmp_path / "missing")
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail("inspect spawned"))
    monkeypatch.setattr(ExecutionStore, "read_only", lambda *a: pytest.fail("inspect read formal state"))
    before = tree_bytes(tmp_path)
    assert adapter.inspect(descriptor)["inspection"]["status"] == "missing"
    assert before == tree_bytes(tmp_path) and not adapter.receipts.root.exists()


@pytest.mark.parametrize("kill_while_observing", [False, True])
def test_experimental_worker_survives_observer_sigkill_without_ledger_writes(tmp_path, kill_while_observing):
    import os
    import subprocess
    import sys
    from pathlib import Path

    root = tmp_path / "detached"
    root.mkdir()
    release = root / "release"
    script = Path(__file__).with_name("empirical_observer_fixture.py")
    process = subprocess.Popen([sys.executable, str(script), str(root), json.dumps({"release": str(release),
        "observation_wait": 4 if kill_while_observing else 0.15})], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    try:
        end = time.monotonic() + 4
        while (not posts(root / "posts.jsonl") or (not kill_while_observing and not (root / "observer-ended").exists())) and time.monotonic() < end:
            time.sleep(0.01)
        assert len(posts(root / "posts.jsonl")) == 1
        descriptor = FrozenEmpiricalDescriptor.from_frozen(json.loads((root / "descriptor.json").read_text()))
        original = (root / "parent-ledger.json").read_bytes()
        process.kill()
        process.wait(timeout=3)
        assert process.returncode is not None
        released = now()
        release.write_text("observer confirmed exited")
        adapter = adapter_for(root)
        # Actual detached process uses the same root but no authority is reissued.
        adapter.receipts = ReceiptStore(root / "receipts")
        worker_exit(adapter, descriptor)
        summary = adapter.inspect(descriptor)["inspection"]
        assert summary["status"] == "candidate_found" and summary["isolated"] and not summary["recovery_performed"]
        assert summary["response_received_at"] >= released.isoformat()
        assert (root / "parent-ledger.json").read_bytes() == original and len(posts(root / "posts.jsonl")) == 1
        before = tree_bytes(root)
        with pytest.raises(ObservationEnded):
            adapter.capture(descriptor, "SYNTHETIC-PRIVATE-KEY")
        assert before == tree_bytes(root)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=3)


def test_experimental_watchdog_retains_fixed_five_second_cleanup_bound(tmp_path):
    descriptor = descriptor_for(seconds=0.8)
    adapter = adapter_for(tmp_path, observation_wait=0.15, fault="blocks")
    started = time.monotonic()
    with pytest.raises(ObservationEnded):
        adapter.capture(descriptor, "SYNTHETIC-PRIVATE-KEY")
    worker_exit(adapter, descriptor, seconds=7)
    assert time.monotonic() - started < 7 and len(posts(tmp_path / "posts.jsonl")) == 1
    assert adapter.inspect(descriptor)["inspection"]["status"] != "candidate_found"
