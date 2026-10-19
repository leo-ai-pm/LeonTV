"""Formal ASGI routes, real temporary persistence/media, fixture actors; outbound disabled."""

import copy
import hashlib
import json
import socket
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient
from imageio_ffmpeg import count_frames_and_secs

from api.routers import harness
from core.harness.runtime import HarnessRuntime

BASE = "/api/harness/runs"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Harness product tests must not use real network/model HTTP")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)


def body(key="request-key-0001", scenario="success", project_id=None):
    return {"mode": "test", "client_request_key": key, "project_id": project_id,
            "source": {"hash": "a" * 64, "record_ids": ["source-record-1"]},
            "production": {"ratio": "9:16", "duration_ms": 15000},
            "authorization_ref": "test-zero-paid-v1", "scenario": scenario}


def make_app(runtime):
    app = FastAPI()
    app.include_router(harness.router)
    app.dependency_overrides[harness.get_harness_runtime] = lambda: runtime
    return app


@pytest.fixture
def env(tmp_path):
    runtime = HarnessRuntime(tmp_path / "private", step_delay=0.001)
    app = make_app(runtime)
    with TestClient(app) as client:
        yield client, runtime, app
    runtime.close()


def create(client, scenario="success", key="request-key-0001", project_id=None):
    response = client.post(BASE, json=body(key, scenario, project_id))
    assert response.status_code == 201, response.text
    assert response.headers["x-leontv-mode"] == "test"
    return response.json()["run"]


def wait(client, run_id, statuses=("waiting_human_review",), seconds=5):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        response = client.get(f"{BASE}/{run_id}")
        assert response.status_code == 200, response.text
        run = response.json()["run"]
        if run["status"] in statuses:
            return run
        assert run["status"] != "failed", run
        time.sleep(0.005)
    pytest.fail(f"Run did not reach {statuses}: {run}")


def action(run, value, key="action-key-0001", **extra):
    return {"mode": "test", "client_action_key": key, "expected_revision": run["revision"], "action": value, **extra}


def final_review(run, key="review-key-0001", decision="accept"):
    return {"mode": "test", "client_review_key": key, "expected_revision": run["revision"],
            "manifest_version": run["manifest"]["version"], "final_sha256": run["manifest"]["final_sha256"],
            "decision": decision, "feedback": "private test feedback"}


def test_formal_app_registers_contract_and_preserves_legacy_routes():
    from api.app import app

    paths = [route.path for route in app.routes]
    assert BASE in paths
    assert paths.index(BASE + "/by-key/{client_request_key}") < paths.index(BASE + "/{run_id}")
    assert "/api/project/{session_id}/continue" in paths
    assert "/api/studio/projects/{project_id}" in paths


def test_full_create_poll_safe_summaries_events_and_real_media(env, tmp_path):
    client, runtime, _ = env
    run = wait(client, create(client)["id"])
    assert run["test_mode"] is True and run["mode"] == "test" and run["evidence_type"] == "fixture"
    assert run["metrics"]["supplier_submissions"] == 3
    assert run["metrics"]["model_http_calls"] == run["metrics"]["paid_calls"] == 0
    assert run["budget"]["paid_actual"] == "0.00"
    assert all(a["quality_status"] == "accepted" and a["required_scopes"] == ["fixture_integrity", "fixture_content"] for a in run["artifacts"])
    page = client.get(f"{BASE}/{run['id']}/events?limit=3").json()
    assert len(page["events"]) == 3 and page["has_more"]
    remaining = client.get(f"{BASE}/{run['id']}/events?after_sequence={page['next_sequence']}").json()
    events = page["events"] + remaining["events"]
    assert [e["sequence"] for e in events] == list(range(1, run["snapshot_sequence"] + 1))
    assert all(e["mode"] == "test" for e in events)
    assert str(runtime.store.root) not in json.dumps({"run": run, "events": events})
    media_url = next(a["media_url"] for a in run["artifacts"] if a["id"] == "final")
    response = client.get(media_url)
    assert response.status_code == 200 and response.headers["content-type"] == "video/mp4"
    assert response.headers["x-leontv-mode"] == "test"
    assert "test-fixture" in response.headers["content-disposition"]
    assert hashlib.sha256(response.content).hexdigest() == run["manifest"]["final_sha256"]
    video = tmp_path / "decoded-test-fixture.mp4"
    video.write_bytes(response.content)
    assert count_frames_and_secs(str(video))[0] == 360


def test_response_loss_same_key_lookup_and_normalized_defaults(env):
    client, _, _ = env
    first = create(client)
    found = client.get(f"{BASE}/by-key/request-key-0001")
    assert found.status_code == 200 and found.json()["run"]["id"] == first["id"]
    request = body()
    del request["project_id"]
    del request["scenario"]
    duplicate = client.post(BASE, json=request)
    assert duplicate.status_code == 200 and duplicate.json()["replayed"] is True
    run = wait(client, first["id"])
    assert run["metrics"]["supplier_submissions"] == 3
    assert len(client.get(BASE).json()["runs"]) == 1


def test_same_claimed_source_hash_does_not_hide_real_parameter_change(env):
    client, _, _ = env
    create(client)
    changed = body()
    changed["source"]["record_ids"] = ["changed-record-with-same-claimed-hash"]
    response = client.post(BASE, json=changed)
    assert response.status_code == 409 and response.json()["detail"]["code"] == "KEY_CONFLICT"
    changed = body()
    changed["semantic_hash"] = "client-cannot-supply-this"
    assert client.post(BASE, json=changed).status_code == 422


def test_concurrent_creates_atomically_share_key_and_one_run(env):
    client, _, _ = env
    with ThreadPoolExecutor(max_workers=8) as pool:
        responses = list(pool.map(lambda _: client.post(BASE, json=body()), range(8)))
    assert sum(r.status_code == 201 for r in responses) == 1
    assert all(r.status_code in {200, 201} for r in responses)
    ids = {r.json()["run"]["id"] for r in responses}
    assert len(ids) == 1
    run = wait(client, ids.pop())
    assert run["metrics"]["supplier_submissions"] == 3


def test_created_queued_run_is_not_misclassified_before_worker_starts(tmp_path):
    runtime = HarnessRuntime(tmp_path / "private", step_delay=0.05)
    try:
        with TestClient(make_app(runtime)) as client:
            run = create(client)
            immediate = client.get(f"{BASE}/{run['id']}").json()["run"]
            assert immediate["status"] in {"queued", "running"}
            assert immediate["blocked_reason"] is None
            wait(client, run["id"])
    finally:
        runtime.close()


@pytest.mark.parametrize("scenario,reason", [("review_unknown", "QUALITY_UNKNOWN"), ("review_rejected", "QUALITY_REJECTED"), ("zero_budget", "BUDGET_EXHAUSTED")])
def test_unknown_rejected_and_zero_budget_block_dependents(env, scenario, reason):
    client, _, _ = env
    run = wait(client, create(client, scenario)["id"], ("blocked",))
    assert run["blocked_reason"] == reason and run["manifest"] is None
    before = copy.deepcopy(run["metrics"])
    response = client.post(f"{BASE}/{run['id']}/actions", json=action(run, "resume"))
    assert response.status_code == 409
    after = client.get(f"{BASE}/{run['id']}").json()["run"]
    assert after["metrics"] == before
    assert not any(a["id"] == "final" for a in after["artifacts"])
    if scenario == "zero_budget":
        assert before["supplier_submissions"] == 0 and after["budget"]["fixture_used"] == 0


@pytest.mark.parametrize("malformed", [{"accepted": True, "private_judge": "PRIVATE-JUDGE"}, ["accepted"], True, None, "false"])
def test_malformed_judge_becomes_unknown_not_success_or_regeneration(env, malformed):
    client, runtime, _ = env
    runtime.actors.review = lambda *args: malformed
    run = wait(client, create(client)["id"], ("blocked",))
    assert run["blocked_reason"] == "QUALITY_UNKNOWN"
    assert run["metrics"]["supplier_submissions"] == 1 and run["metrics"]["review_calls"] == 1
    assert "PRIVATE-JUDGE" not in json.dumps(run)


def test_judge_exception_becomes_unknown_with_no_paid_regeneration(env):
    client, runtime, _ = env

    def broken(*args):
        raise TimeoutError("PRIVATE-ERROR-CONTENT")

    runtime.actors.review = broken
    run = wait(client, create(client)["id"], ("blocked",))
    assert run["blocked_reason"] == "QUALITY_UNKNOWN" and run["metrics"]["supplier_submissions"] == 1
    assert "PRIVATE-ERROR" not in json.dumps(run)


def test_rejected_fixture_repairs_only_affected_shot(env):
    client, runtime, _ = env
    run = wait(client, create(client, "repair")["id"])
    artifacts = {a["id"]: a for a in run["artifacts"]}
    assert artifacts["reference"]["version"] == artifacts["final"]["version"] == 1
    assert artifacts["shot"]["version"] == 2
    assert run["metrics"]["supplier_submissions"] == 4 and run["metrics"]["repair_count"] == 1
    with runtime.store.transaction() as state:
        versions = state["runs"][run["id"]]["artifacts"]["shot"]
        assert len(versions) == 2
        assert runtime.store.media_path(run["id"], "shot", 1).exists()
    assert any(r["status"] == "rejected" and not r["valid"] for r in run["reviews"])


def test_unknown_acceptance_reconciles_original_operation_without_resubmission(env):
    client, _, _ = env
    run = wait(client, create(client, "unknown_acceptance")["id"], ("needs_reconciliation",))
    assert run["metrics"]["supplier_submissions"] == 1
    assert run["budget"]["unresolved_operations"] == 1
    assert client.post(f"{BASE}/{run['id']}/actions", json=action(run, "resume")).status_code == 409
    request = action(run, "reconcile")
    result = client.post(f"{BASE}/{run['id']}/actions", json=request)
    assert result.status_code == 200
    assert client.post(f"{BASE}/{run['id']}/actions", json=request).json()["replayed"] is True
    final = wait(client, run["id"])
    assert final["metrics"]["supplier_submissions"] == 3 and final["metrics"]["query_calls"] == 1
    assert final["budget"]["fixture_used"] == 3 and final["budget"]["unresolved_operations"] == 0


@pytest.mark.parametrize("stop_first", [False, True])
def test_unknown_operation_cannot_be_hidden_by_stop_revise_or_resume(env, stop_first):
    client, _, _ = env
    run = wait(client, create(client, "unknown_acceptance")["id"], ("needs_reconciliation",))
    if stop_first:
        response = client.post(f"{BASE}/{run['id']}/actions", json=action(run, "stop"))
        assert response.status_code == 200
        run = response.json()["run"]
    assert "revise" not in run["allowed_actions"] and "resume" not in run["allowed_actions"]
    for value in ["revise", "resume"]:
        response = client.post(f"{BASE}/{run['id']}/actions", json=action(run, value, f"blocked-{value}-0001", artifact_id="reference" if value == "revise" else None))
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "REMOTE_OUTCOME_UNKNOWN"
    current = client.get(f"{BASE}/{run['id']}").json()["run"]
    assert current["metrics"]["supplier_submissions"] == 1 and current["budget"]["unresolved_operations"] == 1
    assert client.post(f"{BASE}/{run['id']}/actions", json=action(current, "reconcile", "reconcile-key-0001")).status_code == 200
    assert wait(client, run["id"])["metrics"]["supplier_submissions"] == 3


def test_new_application_restores_checkpoint_and_does_not_redo_reference(tmp_path):
    root = tmp_path / "private"
    first = HarnessRuntime(root, step_delay=0.001)
    with TestClient(make_app(first)) as client:
        paused = wait(client, create(client, "resume_checkpoint")["id"], ("stopped",))
    first.close()
    reference = next(a for a in paused["artifacts"] if a["id"] == "reference")
    second = HarnessRuntime(root, step_delay=0.001)
    try:
        with TestClient(make_app(second)) as client:
            restored = client.get(f"{BASE}/by-key/request-key-0001").json()["run"]
            assert restored["id"] == paused["id"]
            result = client.post(f"{BASE}/{restored['id']}/actions", json=action(restored, "resume"))
            assert result.status_code == 200, result.text
            final = wait(client, restored["id"])
            assert next(a for a in final["artifacts"] if a["id"] == "reference") == reference
            assert final["metrics"]["supplier_submissions"] == 3
    finally:
        second.close()


def test_second_runtime_cannot_steal_active_execution_lock(tmp_path):
    first = HarnessRuntime(tmp_path / "private", step_delay=0.05)
    second = HarnessRuntime(tmp_path / "private", step_delay=0.001)
    try:
        with TestClient(make_app(first)) as client, TestClient(make_app(second)) as other:
            created = create(client)
            run = other.get(f"{BASE}/{created['id']}").json()["run"]
            assert run["status"] in {"queued", "running"}
            assert other.post(f"{BASE}/{run['id']}/actions", json=action(run, "resume")).status_code == 409
            assert wait(client, created["id"])["metrics"]["supplier_submissions"] == 3
    finally:
        first.close()
        second.close()


def test_explicit_stop_then_resume_is_persistent_and_action_key_idempotent(tmp_path):
    runtime = HarnessRuntime(tmp_path / "private", step_delay=0.05)
    try:
        with TestClient(make_app(runtime)) as client:
            run = create(client)
            stop = action(run, "stop")
            response = client.post(f"{BASE}/{run['id']}/actions", json=stop)
            assert response.status_code == 200
            assert client.post(f"{BASE}/{run['id']}/actions", json=stop).json()["replayed"] is True
            time.sleep(0.06)  # Bounded wait for the current fixture worker to release its OS lock.
            stopped = client.get(f"{BASE}/{run['id']}").json()["run"]
            assert stopped["status"] == "stopped" and stopped["metrics"]["supplier_submissions"] == 0
            resume = action(stopped, "resume", "action-key-0002")
            assert client.post(f"{BASE}/{run['id']}/actions", json=resume).status_code == 200
            assert client.post(f"{BASE}/{run['id']}/actions", json=resume).json()["replayed"] is True
            changed = {**resume, "action": "stop"}
            assert client.post(f"{BASE}/{run['id']}/actions", json=changed).status_code == 409
            assert wait(client, run["id"])["metrics"]["supplier_submissions"] == 3
    finally:
        runtime.close()


def test_recovery_of_orphaned_submit_stays_unknown_and_never_resubmits(env):
    client, runtime, _ = env
    run = wait(client, create(client, "resume_checkpoint")["id"], ("stopped",))
    with runtime.store.transaction() as state:
        raw = state["runs"][run["id"]]
        raw["status"] = "running"  # Controlled crash-injection on real persisted state.
        artifact = raw["artifacts"]["reference"][-1]
        artifact["generation_status"] = "submitting"
        raw["operations"][artifact["operation_id"]]["status"] = "submitting"
    recovered = client.get(f"{BASE}/{run['id']}").json()["run"]
    assert recovered["status"] == "needs_reconciliation"
    assert client.post(f"{BASE}/{run['id']}/actions", json=action(recovered, "resume")).status_code == 409
    assert client.get(f"{BASE}/{run['id']}").json()["run"]["metrics"]["supplier_submissions"] == 1


@pytest.mark.parametrize("decision", ["accept", "return"])
def test_test_final_review_is_atomic_idempotent_and_cannot_change_decision(env, decision):
    client, _, _ = env
    run = wait(client, create(client)["id"])
    request = final_review(run, decision=decision)
    first = client.post(f"{BASE}/{run['id']}/reviews", json=request)
    assert first.status_code == 200, first.text
    final = first.json()["run"]
    assert final["status"] == ("test_accepted" if decision == "accept" else "test_returned")
    assert final["final_review"]["test_mode"] is True
    assert "private test feedback" not in first.text
    replay = client.post(f"{BASE}/{run['id']}/reviews", json=request)
    assert replay.status_code == 200 and replay.json()["replayed"] is True
    assert replay.json()["run"]["metrics"]["final_decision_count"] == 1
    assert replay.json()["run"]["snapshot_sequence"] == final["snapshot_sequence"]
    wrong = {**request, "decision": "return" if decision == "accept" else "accept"}
    assert client.post(f"{BASE}/{run['id']}/reviews", json=wrong).status_code == 409
    assert client.post(f"{BASE}/{run['id']}/actions", json=action(final, "stop")).status_code == 409


def test_concurrent_final_decisions_write_only_one_record(env):
    client, _, _ = env
    run = wait(client, create(client)["id"])
    requests = [final_review(run, f"review-key-{n:04}") for n in range(6)]
    with ThreadPoolExecutor(max_workers=6) as pool:
        responses = list(pool.map(lambda r: client.post(f"{BASE}/{run['id']}/reviews", json=r), requests))
    assert sum(r.status_code == 200 for r in responses) == 1
    assert all(r.status_code in {200, 409} for r in responses)
    assert client.get(f"{BASE}/{run['id']}").json()["run"]["metrics"]["final_decision_count"] == 1


@pytest.mark.parametrize("field,value", [("expected_revision", 999), ("manifest_version", 999), ("final_sha256", "b" * 64), ("mode", "production")])
def test_stale_or_non_test_review_rejected(env, field, value):
    client, _, _ = env
    run = wait(client, create(client)["id"])
    request = {**final_review(run), field: value}
    response = client.post(f"{BASE}/{run['id']}/reviews", json=request)
    assert response.status_code == (422 if field == "mode" else 409)


@pytest.mark.parametrize("fault", ["rule", "receipt", "review", "dependency", "file"])
def test_same_final_hash_cannot_override_invalid_quality_evidence(env, fault):
    client, runtime, _ = env
    run = wait(client, create(client)["id"])
    request = final_review(run)
    with runtime.store.transaction() as state:
        raw = state["runs"][run["id"]]
        if fault == "rule":
            raw["rule_version"] = "fixture-v2"
        elif fault == "receipt":
            raw["receipts"].pop(raw["reviews"][-1]["receipt_id"])
        elif fault == "review":
            raw["reviews"][-1]["status"] = "unknown"
        elif fault == "dependency":
            raw["artifacts"]["shot"][-1]["stale"] = True
        else:
            runtime.store.media_path(run["id"], "final", 1).write_bytes(b"changed")
    response = client.post(f"{BASE}/{run['id']}/reviews", json=request)
    assert response.status_code == 409
    safe = client.get(f"{BASE}/{run['id']}").json()["run"]
    assert safe["status"] == "blocked" and safe["final_review"] is None


def test_upstream_revision_invalidates_downstream_and_old_review_key(env):
    client, _, _ = env
    run = wait(client, create(client)["id"])
    old_request = final_review(run)
    accepted = client.post(f"{BASE}/{run['id']}/reviews", json=old_request).json()["run"]
    response = client.post(f"{BASE}/{run['id']}/actions", json=action(accepted, "revise", artifact_id="shot"))
    assert response.status_code == 200
    blocked = response.json()["run"]
    assert blocked["manifest"]["valid"] is False and blocked["final_review"] is None
    assert all(a["quality_status"] == "unknown" for a in blocked["artifacts"] if a["id"] in {"shot", "final"})
    assert client.post(f"{BASE}/{run['id']}/reviews", json=old_request).status_code == 409
    assert client.post(f"{BASE}/{run['id']}/actions", json=action(blocked, "resume", "action-key-0002")).status_code == 200
    final = wait(client, run["id"])
    assert final["manifest"]["version"] == 2 and final["manifest"]["final_sha256"] == run["manifest"]["final_sha256"]
    assert next(a for a in final["artifacts"] if a["id"] == "reference")["version"] == 1
    assert final["metrics"]["supplier_submissions"] == 5


def test_private_summary_media_and_validation_never_expose_private_payloads(env):
    client, runtime, _ = env
    run = wait(client, create(client)["id"])
    with runtime.store.transaction() as state:
        raw = state["runs"][run["id"]]
        raw["raw_source"] = "PRIVATE-NOVEL-SENTINEL"
        raw["secret"] = "PRIVATE-KEY-SENTINEL"
        raw["receipts"]["private-unrelated"] = {"raw_judge": "PRIVATE-JUDGE-SENTINEL", "url": "https://private.example?sig=SECRET"}
    for suffix in ["", "/events"]:
        response = client.get(f"{BASE}/{run['id']}{suffix}")
        assert response.status_code == 200
        assert all(token not in response.text for token in ["PRIVATE-", "sig=SECRET", str(runtime.store.root)])
    request = {**body("other-request-key"), "raw_document": "SECRET-IN-INVALID-REQUEST"}
    response = client.post(BASE, json=request)
    assert response.status_code == 422 and "SECRET-IN-INVALID-REQUEST" not in response.text
    assert client.get(f"{BASE}/{run['id']}/artifacts/final/media?version=1&path=config.yaml").status_code == 422


def test_private_static_overlap_and_symlink_media_are_rejected(env, tmp_path):
    client, runtime, app = env
    run = wait(client, create(client)["id"])
    outside = tmp_path / "secret.txt"
    outside.write_text("DO-NOT-EXPOSE-THIS")
    media = runtime.store.media_path(run["id"], "final", 1)
    media.unlink()
    media.symlink_to(outside)
    response = client.get(f"{BASE}/{run['id']}/artifacts/final/media?version=1")
    assert response.status_code == 503 and "DO-NOT-EXPOSE" not in response.text
    app.mount("/unsafe", StaticFiles(directory=runtime.store.root))
    response = client.get(f"{BASE}/{run['id']}")
    assert response.status_code == 503 and str(runtime.store.root) not in response.text


def test_state_symlink_is_not_read(env, tmp_path):
    client, runtime, _ = env
    private = tmp_path / "other-private.json"
    private.write_text('{"secret":"PRIVATE-STATE"}')
    runtime.store.root.mkdir()
    (runtime.store.root / "state.json").symlink_to(private)
    response = client.get(BASE)
    assert response.status_code == 503 and "PRIVATE-STATE" not in response.text


def test_nested_static_mount_blocks_private_storage_before_creation(tmp_path):
    root = tmp_path / "not-yet-created"
    runtime = HarnessRuntime(root)
    app = make_app(runtime)
    nested = FastAPI()
    nested.mount("/raw", StaticFiles(directory=root, check_dir=False))
    app.mount("/nested", nested)
    try:
        with TestClient(app) as client:
            response = client.post(BASE, json=body())
            assert response.status_code == 503 and str(root) not in response.text
            assert not root.exists()
    finally:
        runtime.close()


def test_project_association_is_separate_and_does_not_mutate_legacy_link(tmp_path):
    from types import SimpleNamespace

    from api.services.studio_store import StudioStore

    store = StudioStore(tmp_path / "projects", tmp_path / "exports")
    project = store.create("Synthetic linked project")
    project["source_session_id"] = "old-workflow-session"
    store._write(project)
    original = store._path(project["id"]).read_bytes()
    engine = SimpleNamespace(get_status_snapshot=lambda _: None)
    runtime = HarnessRuntime(tmp_path / "private", project_reader=lambda pid: store.get(pid, engine), step_delay=0.001)
    try:
        with TestClient(make_app(runtime)) as client:
            run = create(client, project_id=project["id"])
            assert client.get(f"{BASE}?project_id={project['id']}").json()["runs"][0]["id"] == run["id"]
            assert client.get(f"{BASE}?project_id=old-workflow-session").json()["runs"] == []
            response = client.post(BASE, json=body("request-missing-project", project_id="missing"))
            assert response.status_code == 404
            assert store._path(project["id"]).read_bytes() == original
    finally:
        runtime.close()


@pytest.mark.parametrize("patch", [{"mode": "live"}, {"authorization_ref": "old-CNY-100"}, {"production": {"ratio": "16:9", "duration_ms": 15000}}, {"production": {"duration_ms": 15000}}, {"source": {"hash": "bad", "record_ids": ["source"]}}])
def test_invalid_live_budget_source_or_ratio_never_creates_run(env, patch):
    client, _, _ = env
    response = client.post(BASE, json={**body(), **patch})
    assert response.status_code == 422
    assert client.get(BASE).json()["runs"] == []
