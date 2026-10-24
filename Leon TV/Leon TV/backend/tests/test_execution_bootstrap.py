"""Synthetic server assembly and restart rules; no private inputs or live model calls."""
import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from test_execution_control import bundle as bundle
from test_execution_control import private_json
from test_execution_empirical import EmpiricalEnv
from test_execution_fee_inbox import SOURCE, export, fixture_operation, row_for
from test_planner_execution import app_for
from test_planner_execution import no_network as no_network

from core.creator.store import CreatorError
from core.execution import bootstrap, operator
from core.execution.control import CONTROL_ENV, read_control
from core.execution.fees import FeeInbox
from core.execution.receipts import ReceiptCaptureSettings
from core.execution.runtime import ExecutionRuntime
from core.harness.store import unlock_file


def snapshot(root):
    return {
        path.relative_to(root).as_posix(): (path.is_dir(), None if path.is_dir() else path.read_bytes(), path.stat().st_mtime_ns)
        for path in root.rglob("*")
    }


def forbidden(*args, **kwargs):
    pytest.fail("Synthetic bootstrap validation must not load credentials or call a live model")


@pytest.fixture(autouse=True)
def isolated_control(monkeypatch):
    monkeypatch.delenv(CONTROL_ENV, raising=False)
    bootstrap.reset_control_for_tests()
    yield
    bootstrap.reset_control_for_tests()


@pytest.fixture
def live_factory(bundle, monkeypatch):
    resources = []

    def make():
        source = bundle("enabled")
        source.policy.update(validation_only=True, request_deadline_seconds=10)
        source.save()
        monkeypatch.setattr(bootstrap, "BACKEND", source.backend)
        control = read_control(source.path, source.backend)
        counter = source.root / "synthetic-posts.jsonl"
        capture = ReceiptCaptureSettings(worker_factory="empirical_product_fixture:make_transport", worker_options={
            "execution_root": source.roots["execution"], "counter": str(counter), "flow": "first_accepted",
        })

        def runtime():
            current = bootstrap.OperatorExecutionRuntime(control, source.planner, api_key="synthetic-secret", capture=capture)
            # Receipt binding is exercised by the native runtime; independent inbox
            # proof matching has its own suite and must not require real exports here.
            current.fee_verifier = lambda evidence, operation, policy: evidence["source_export_hash"] == "e" * 64
            client = TestClient(app_for(current))
            client.__enter__()
            resources.append((client, current))
            return EmpiricalEnv(client, current, None, source.planner, counter, {})

        return SimpleNamespace(source=source, control=control, helper=runtime(), restart=runtime,
                               task=source.planner.get(control.document.admission.task_id)["task"])

    yield make
    for client, runtime in reversed(resources):
        client.__exit__(None, None, None)
        runtime.close()


def pending_call(env):
    grant = env.helper.grant(env.task)
    assert env.helper.calls() == []
    assert env.helper.action(env.task, grant["execution"]).status_code == 200
    pending = env.helper.wait(env.task, {"needs_reconciliation", "blocked"}, seconds=15)
    assert pending["blocked_reason"] == "PLATFORM_FEE_REQUIRED"
    assert len(env.helper.calls()) == 1
    return pending


def test_default_off_factories_do_not_create_stores_or_load_credentials(tmp_path, monkeypatch):
    before = snapshot(tmp_path)
    monkeypatch.setattr(bootstrap.OperatorExecutionRuntime, "__init__", forbidden)
    briefs = bootstrap.build_brief_store(tmp_path / "creator")
    planner = bootstrap.build_planner(tmp_path / "planner", briefs)
    runtime = bootstrap.build_execution_runtime(tmp_path / "execution", planner, credential_loader=forbidden)
    try:
        assert runtime.enabled is False and runtime.api_key is None
        assert bootstrap.operator_control() is None
        assert snapshot(tmp_path) == before
    finally:
        runtime.close()
        planner.close()


def test_control_mode_is_frozen_until_explicit_process_reset(bundle, monkeypatch):
    env = bundle("observe_only")
    monkeypatch.setattr(bootstrap, "BACKEND", env.backend)
    assert bootstrap.operator_control() is None
    monkeypatch.setenv(CONTROL_ENV, str(env.path))
    assert bootstrap.operator_control() is None
    bootstrap.reset_control_for_tests()
    assert bootstrap.operator_control().document.mode == "observe_only"


def test_enabled_factory_waits_for_private_route_before_starting_observer(bundle, monkeypatch):
    env = bundle("enabled")
    env.data["observer_enabled"] = True
    env.save()
    monkeypatch.setattr(bootstrap, "BACKEND", env.backend)
    monkeypatch.setenv(CONTROL_ENV, str(env.path))
    monkeypatch.setattr(bootstrap.OperatorExecutionRuntime, "start_observer", forbidden)
    before = snapshot(env.root)
    runtime = bootstrap.build_execution_runtime(Path(env.roots["execution"]), env.planner, credential_loader=lambda: "synthetic-secret")
    try:
        assert runtime.enabled is True and runtime._observer_thread is None
        assert snapshot(env.root) == before
    finally:
        runtime.close()


def test_fresh_task_needs_quote_grant_resume_then_handoffs_without_more_resumes(live_factory):
    env = live_factory()
    assert env.helper.calls() == []
    quote = env.helper.quote(env.task)
    denied = env.helper.action(env.task, quote["execution"], key="resume-without-explicit-grant")
    assert denied.status_code == 409 and env.helper.calls() == []
    grant = env.helper.grant(env.task, quote)
    assert env.helper.calls() == []
    assert env.helper.action(env.task, grant["execution"]).status_code == 200
    assert env.helper.wait(env.task, {"needs_reconciliation"}, seconds=15)["blocked_reason"] == "PLATFORM_FEE_REQUIRED"
    final = env.helper.complete(env.task)
    assert final["status"] == "plan_ready"
    assert len(env.helper.calls()) == final["ledger"]["call_count"] == 3
    actions = env.helper.runtime.store.read_only()["keys"]
    assert sum(key.startswith("action:") for key in actions) == 1


def test_restart_fee_observation_records_only_until_this_process_resumes(live_factory):
    env = live_factory()
    pending = pending_call(env)
    env.helper.runtime.close()
    env.helper = env.restart()
    operation_id = pending["operations"][-1]["id"]
    evidence = env.helper.evidence(operation_id)
    result = env.helper.runtime.observe_fee(env.task["id"], operation_id, evidence)
    assert result["replayed"] is False
    matched = result["execution"]
    assert matched["operations"][-1]["fee_status"] == "matched"
    assert matched["operations"][-1]["status"] == "received"
    assert matched["allowed_actions"] == ["stop", "resume"]
    native = env.helper.runtime.store.read_only()["tasks"][env.task["id"]]
    assert native["read_source"] is False and len(env.helper.calls()) == 1
    replay = env.helper.runtime.observe_fee(env.task["id"], operation_id, evidence)
    assert replay["replayed"] is True and len(env.helper.calls()) == 1
    assert env.helper.action(env.task, matched, key="explicit-resume-after-service-restart").status_code == 200
    final = env.helper.complete(env.task)
    assert final["status"] == "plan_ready" and len(env.helper.calls()) == 3


def test_explicit_no_continue_is_preserved_by_operator_wrapper(live_factory):
    env = live_factory()
    pending = pending_call(env)
    operation_id = pending["operations"][-1]["id"]
    matched = env.helper.runtime.observe_fee(env.task["id"], operation_id, env.helper.evidence(operation_id), auto_continue=False)["execution"]
    assert matched["operations"][-1]["fee_status"] == "matched"
    assert matched["operations"][-1]["status"] == "received"
    assert env.helper.runtime.store.read_only()["tasks"][env.task["id"]]["read_source"] is False
    assert len(env.helper.calls()) == 1


def test_resume_gate_is_visible_before_concurrent_fee_delivery(live_factory, monkeypatch):
    env = live_factory()
    granted = env.helper.grant(env.task)["execution"]
    env.helper.runtime.close()
    env.helper = env.restart()
    runtime = env.helper.runtime
    assert env.task["id"] in runtime._startup_tasks
    submitted, release, entering_fee, observed = (threading.Event() for _ in range(4))
    results, errors, flags = [], [], []

    def submit(worker, task_id, lock):
        submitted.set()
        try:
            assert release.wait(5)
        finally:
            unlock_file(lock)

    def fee(self, task_id, operation_id, evidence, **kwargs):
        flags.append(kwargs["auto_continue"])
        observed.set()
        return {"replayed": False}

    def resume():
        try:
            results.append(runtime.action(env.task["id"], {"client_action_key": "concurrent-resume-gate", "expected_execution_revision": granted["revision"], "action": "resume"}))
        except BaseException as error:
            errors.append(error)

    def deliver():
        entering_fee.set()
        runtime.observe_fee(env.task["id"], "f" * 32, {})

    monkeypatch.setattr(runtime.pool, "submit", submit)
    monkeypatch.setattr(ExecutionRuntime, "observe_fee", fee)
    action_thread = threading.Thread(target=resume)
    fee_thread = threading.Thread(target=deliver)
    action_thread.start()
    try:
        assert submitted.wait(5)
        fee_thread.start()
        assert entering_fee.wait(1)
        assert not observed.wait(0.05)
    finally:
        release.set()
        action_thread.join(5)
        if fee_thread.ident is not None:
            fee_thread.join(5)
    assert not action_thread.is_alive() and not fee_thread.is_alive()
    assert errors == [] and results[0]["replayed"] is False
    assert flags == [True] and env.task["id"] not in runtime._startup_tasks
    assert env.helper.calls() == []


def test_observe_only_reads_historical_running_journal_without_recovery_or_writes(live_factory, monkeypatch):
    env = live_factory()
    monkeypatch.setattr(env.helper.runtime, "process_empirical_receipt", lambda *args: (_ for _ in ()).throw(RuntimeError("SYNTHETIC crash before receipt decode")))
    grant = env.helper.grant(env.task)
    assert env.helper.action(env.task, grant["execution"]).status_code == 200
    env.helper.runtime.close()
    native = env.helper.runtime.store.read_only()
    assert native["tasks"][env.task["id"]]["status"] == "running"
    assert next(iter(native["operations"].values()))["raw"] is not None
    env.source.data["mode"] = "observe_only"
    env.source.data["observer_enabled"] = True
    private_json(env.source.path, env.source.data)
    monkeypatch.setenv(CONTROL_ENV, str(env.source.path))
    bootstrap.reset_control_for_tests()
    before = snapshot(env.source.root)
    briefs = bootstrap.build_brief_store(env.source.root / "unused-creator")
    planner = bootstrap.build_planner(env.source.root / "unused-planner", briefs)
    runtime = bootstrap.build_execution_runtime(env.source.root / "unused-execution", planner, credential_loader=forbidden)
    monkeypatch.setattr(ExecutionRuntime, "recover", forbidden)
    try:
        with TestClient(app_for(runtime)) as client:
            base = f"/api/planner/tasks/{env.task['id']}"
            for path in ["/api/creator/briefs", f"/api/creator/briefs/{env.task['source']['brief_id']}", "/api/planner/tasks", base, base + "/source", base + "/events", base + "/execution", base + "/execution/events", "/api/planner/execution/policy", base + "/execution/quotes/by-key/synthetic-quote-key-001", base + "/execution/grants/by-key/synthetic-grant-key-001"]:
                response = client.get(path)
                assert response.status_code == 200, response.text
            assert client.get(base).json()["task"]["allowed_actions"] == []
            observed = client.get(base + "/execution").json()["execution"]
            assert observed["status"] == "running" and observed["allowed_actions"] == []
            response = client.post(base + "/execution/actions", json={"client_action_key": "read-only-cannot-resume", "expected_execution_revision": observed["revision"], "action": "resume"})
            assert response.status_code == 409 and response.json()["detail"]["code"] == "CONTROL_READ_ONLY"
            with pytest.raises(CreatorError) as error:
                briefs.create("read-only-cannot-create", {"title": "Synthetic", "script": "Synthetic", "requirements": "", "production": env.task["source"]["production"], "budget_cny": None})
            assert error.value.code == "CONTROL_READ_ONLY"
        assert runtime._observer_thread is None
        assert snapshot(env.source.root) == before
        assert len(env.helper.calls()) == 1
    finally:
        runtime.close()
        planner.close()


def test_empty_fee_inbox_drain_does_not_create_directory_or_read_runtime(tmp_path):
    inbox = FeeInbox(tmp_path / "absent-fees", SOURCE)
    assert inbox.drain(None, observe_only=False) == {"prepared": 0, "applied": 0, "pending": 0, "conflicts": 0}
    assert list(tmp_path.iterdir()) == []


def test_cli_import_and_default_status_do_not_import_runtime_or_create_directories():
    backend = Path(__file__).resolve().parents[1]
    script = """
import builtins
import pathlib
import socket
import sys
original = builtins.__import__
def checked(name, globals=None, locals=None, fromlist=(), level=0):
    if level == 0 and (name in {'config', 'core.execution.runtime', 'core.execution.bootstrap', 'core.planner.runtime'} or name == 'models' or name.startswith('models.')):
        raise AssertionError('Runtime/config/model import forbidden')
    return original(name, globals, locals, fromlist, level)
def forbidden(*args, **kwargs):
    raise AssertionError('Directory creation or network forbidden')
builtins.__import__ = checked
pathlib.Path.mkdir = forbidden
socket.socket.connect = forbidden
socket.socket.connect_ex = forbidden
from core.execution.operator import main
assert main(['status']) == 0
assert 'core.execution.runtime' not in sys.modules
assert 'config' not in sys.modules and 'models' not in sys.modules
"""
    environment = {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(backend), "PYTHONDONTWRITEBYTECODE": "1"}
    result = subprocess.run([sys.executable, "-c", script], cwd=backend, env=environment, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"mode": "off", "generation_posts": 0}


def test_cli_status_is_readonly_and_fee_import_only_changes_inbox(bundle, monkeypatch, capsys):
    env = bundle("observe_only")
    monkeypatch.setattr(operator, "BACKEND", env.backend)
    monkeypatch.setattr(operator, "STATIC", (env.backend / "code", env.backend.parent / "frontend/public"))
    monkeypatch.setattr(ExecutionRuntime, "__init__", forbidden)
    before = snapshot(env.root)
    assert operator.main(["status", "--control", str(env.path)]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["mode"] == "observe_only" and status["generation_posts"] == 0
    assert snapshot(env.root) == before and not Path(env.roots["fees"]).exists()
    operation, policy = fixture_operation()
    raw = export(row_for(operation, policy))
    path = env.root / "synthetic-export.json"
    path.write_bytes(raw)
    path.chmod(0o600)
    before = snapshot(env.root)
    assert operator.main(["fees-import", str(path), "--control", str(env.path)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["generation_posts"] == 0
    after = snapshot(env.root)
    assert {key: value for key, value in after.items() if key != "fees" and not key.startswith("fees/")} == before
    assert FeeInbox(env.roots["fees"], env.data["fee_source"]).status() == {"exports": 1, "prepared": 0, "applied": 0}
