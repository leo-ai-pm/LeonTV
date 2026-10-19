"""Real ASGI/private-disk draft tests; all content synthetic and outbound disabled."""

import hashlib
import json
import os
import socket
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient

from api.routers import creator
from core.creator.store import CreatorBriefStore, CreatorError
from core.harness.runtime import HarnessRuntime
from core.harness.store import HarnessError, HarnessStore

BASE = "/api/creator/briefs"
SCRIPT = "  SYNTHETIC-PRIVATE-SCRIPT\r\n第二行👩‍🎨\n  "


@pytest.fixture(autouse=True)
def inert_only(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Saving a Brief cannot create a run or use outbound network")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(HarnessRuntime, "create", forbidden)


def app_for(store):
    app = FastAPI()
    app.include_router(creator.router)
    app.dependency_overrides[creator.get_brief_store] = lambda: store
    return app


@pytest.fixture
def env(tmp_path):
    store = CreatorBriefStore(tmp_path.resolve() / "private")
    app = app_for(store)
    with TestClient(app) as client:
        yield client, store, app


def request(key="creator-create-key-001", **changes):
    return {"client_request_key": key, "script": SCRIPT, **changes}


def saved(client, **changes):
    response = client.post(BASE, json=request(**changes))
    assert response.status_code == 201, response.text
    return response.json()["brief"]


def update(brief, key="creator-update-key-001", **changes):
    return {"client_update_key": key, "expected_revision": brief["revision"],
            **{field: brief[field] for field in ["title", "script", "requirements", "production", "budget_cny"]}, **changes}


def test_formal_registration_keeps_d06_and_old_entries():
    from api.app import app

    paths = [route.path for route in app.routes]
    assert BASE in paths and "/api/harness/runs" in paths and "/api/studio/projects" in paths
    assert paths.index(BASE + "/by-key/{client_request_key}") < paths.index(BASE + "/{brief_id}")


def test_create_actual_defaults_source_hash_no_authorization_or_run(env):
    client, store, _ = env
    response = client.post(BASE, json=request())
    assert response.status_code == 201 and response.headers["cache-control"] == "no-store"
    body = response.json()
    brief = body["brief"]
    assert body["applied_revision"] == brief["revision"] == brief["source_version"] == 1
    assert brief["script"] == SCRIPT and brief["source_hash"] == hashlib.sha256(SCRIPT.encode()).hexdigest()
    assert brief["script_characters"] == len(SCRIPT)
    assert brief["title"] == "未命名创作简报" and brief["requirements"] == ""
    assert brief["production"] == {"duration_seconds": 15, "ratio": "9:16", "style": "realistic_cinematic"}
    assert brief["budget_cny"] is None and brief["budget_status"] == "pending"
    assert brief["authorization_status"] == "not_authorized" and brief["execution_enabled"] is False
    assert (store.root / "state.json").is_file()
    assert not (store.root.parent / "product_runs").exists()


def test_list_and_by_key_are_safe_but_explicit_id_returns_full_text(env, caplog):
    client, store, _ = env
    brief = saved(client, requirements="PRIVATE-REQUIREMENTS-SENTINEL")
    for url in [BASE, BASE + "/by-key/creator-create-key-001"]:
        response = client.get(url)
        assert response.status_code == 200
        assert "SYNTHETIC-PRIVATE-SCRIPT" not in response.text and "PRIVATE-REQUIREMENTS" not in response.text
        assert str(store.root) not in response.text and '"script":' not in response.text
    full = client.get(f"{BASE}/{brief['id']}").json()["brief"]
    assert full["script"] == SCRIPT and full["requirements"] == "PRIVATE-REQUIREMENTS-SENTINEL"
    assert "SYNTHETIC-PRIVATE-SCRIPT" not in caplog.text


def test_response_loss_key_lookup_and_normalized_create_replay(env):
    client, _, _ = env
    first = saved(client, budget_cny="100", title="  Synthetic title  ")
    found = client.get(BASE + "/by-key/creator-create-key-001").json()["brief"]
    assert found["id"] == first["id"]
    replay = client.post(BASE, json=request(title="Synthetic title", budget_cny="100.00"))
    assert replay.status_code == 200 and replay.json()["replayed"] is True
    assert replay.json()["brief"]["id"] == first["id"] and len(client.get(BASE).json()["briefs"]) == 1
    wrong = client.post(BASE, json=request(script=SCRIPT + "change"))
    assert wrong.status_code == 409 and wrong.json()["detail"]["code"] == "KEY_CONFLICT"
    assert client.post(BASE, json=request(source_hash=first["source_hash"])).status_code == 422


def test_create_replay_after_update_returns_current_without_rolling_back(env):
    client, _, _ = env
    first = saved(client)
    changed = client.put(f"{BASE}/{first['id']}", json=update(first, script="Second synthetic text")).json()["brief"]
    replay = client.post(BASE, json=request()).json()
    assert replay["replayed"] and replay["applied_revision"] == 1
    assert replay["brief"]["revision"] == 2 and replay["brief"]["script"] == changed["script"]
    assert client.get(BASE + "/by-key/creator-create-key-001").json()["brief"]["revision"] == 2


def test_versions_immutable_and_source_changes_independent_of_other_edits(env):
    client, store, _ = env
    first = saved(client)
    before = json.loads((store.root / "state.json").read_text())["briefs"][first["id"]]["versions"][0]
    second = client.put(f"{BASE}/{first['id']}", json=update(first, requirements="new requirement", budget_cny="0")).json()["brief"]
    assert second["revision"] == 2 and second["source_version"] == 1 and second["source_hash"] == first["source_hash"]
    assert second["budget_status"] == "draft" and second["budget_cny"] == "0.00"
    third = client.put(f"{BASE}/{first['id']}", json=update(second, "creator-update-key-002", script="Third synthetic script")).json()["brief"]
    assert third["revision"] == 3 and third["source_version"] == 2
    fourth = client.put(f"{BASE}/{first['id']}", json=update(third, "creator-update-key-003", script=SCRIPT)).json()["brief"]
    assert fourth["source_version"] == 3 and fourth["source_hash"] == first["source_hash"]
    historical = client.get(f"{BASE}/{first['id']}?revision=1").json()["brief"]
    assert historical["script"] == SCRIPT and historical["revision"] == 1 and historical["current_revision"] == 4 and not historical["is_current"]
    after = json.loads((store.root / "state.json").read_text())["briefs"][first["id"]]["versions"][0]
    assert after == before
    assert client.get(f"{BASE}/{first['id']}?revision=99").status_code == 404


def test_update_idempotency_conflict_and_late_replay(env):
    client, _, _ = env
    first = saved(client)
    payload = update(first, script="Updated once")
    one = client.put(f"{BASE}/{first['id']}", json=payload)
    assert one.status_code == 200
    second = one.json()["brief"]
    replay = client.put(f"{BASE}/{first['id']}", json=payload).json()
    assert replay["replayed"] and replay["applied_revision"] == 2 and len(replay["brief"]["versions"]) == 2
    assert client.put(f"{BASE}/{first['id']}", json={**payload, "script": "different"}).status_code == 409
    stale = client.put(f"{BASE}/{first['id']}", json=update(first, "new-key-but-stale", script="unsaved local text"))
    assert stale.status_code == 409 and stale.json()["detail"]["current_revision"] == 2
    assert "unsaved local text" not in stale.text
    client.put(f"{BASE}/{first['id']}", json=update(second, "creator-update-key-002", title="Latest"))
    late = client.put(f"{BASE}/{first['id']}", json=payload).json()
    assert late["applied_revision"] == 2 and late["brief"]["revision"] == 3 and late["brief"]["title"] == "Latest"


def test_put_requires_complete_content(env):
    client, _, _ = env
    first = saved(client)
    response = client.put(f"{BASE}/{first['id']}", json={"client_update_key": "incomplete-key-1", "expected_revision": 1, "script": "Only script"})
    assert response.status_code == 422
    assert client.get(f"{BASE}/{first['id']}").json()["brief"]["revision"] == 1


def test_concurrent_creates_and_updates_use_real_atomic_store(env):
    client, _, _ = env
    with ThreadPoolExecutor(max_workers=8) as pool:
        responses = list(pool.map(lambda _: client.post(BASE, json=request()), range(8)))
    assert sum(r.status_code == 201 for r in responses) == 1
    assert len({r.json()["brief"]["id"] for r in responses}) == 1
    first = responses[0].json()["brief"]
    payloads = [update(first, f"concurrent-key-{n:04}", script=f"Synthetic variant {n}") for n in range(8)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda p: client.put(f"{BASE}/{first['id']}", json=p), payloads))
    assert sum(r.status_code == 200 for r in results) == 1
    assert sum(r.status_code == 409 for r in results) == 7
    assert client.get(f"{BASE}/{first['id']}").json()["brief"]["revision"] == 2


def test_separate_processes_share_create_key_lock(tmp_path):
    code = """
import json,sys
from core.creator.store import CreatorBriefStore,PRODUCTION
store=CreatorBriefStore(sys.argv[1])
r=store.create('process-shared-key',{'title':'Synthetic','script':'Fixture','requirements':'','production':PRODUCTION,'budget_cny':None})
print(json.dumps({'id':r['brief']['id'],'replayed':r['replayed']}))
"""
    root = tmp_path.resolve() / "shared-process-store"
    processes = [subprocess.Popen([sys.executable, "-c", code, str(root)], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}) for _ in range(4)]
    outputs = []
    for process in processes:
        stdout, stderr = process.communicate(timeout=15)
        assert process.returncode == 0, stderr.decode()
        outputs.append(json.loads(stdout))
    assert len({r["id"] for r in outputs}) == 1 and sum(not r["replayed"] for r in outputs) == 1


def test_app_recreation_restores_actual_private_history(env):
    client, store, _ = env
    first = saved(client)
    client.put(f"{BASE}/{first['id']}", json=update(first, script="Persisted new source"))
    restored = CreatorBriefStore(store.root)
    with TestClient(app_for(restored)) as other:
        current = other.get(BASE + "/by-key/creator-create-key-001").json()["brief"]
        assert current["id"] == first["id"] and current["revision"] == current["source_version"] == 2
        assert other.get(f"{BASE}/{first['id']}?revision=1").json()["brief"]["script"] == SCRIPT


@pytest.mark.parametrize("money,expected", [(None, None), ("0", "0.00"), ("12.3", "12.30"), ("1000000", "1000000.00")])
def test_budget_is_only_a_draft_never_an_authorization(env, money, expected):
    client, _, _ = env
    first = saved(client, budget_cny=money)
    assert first["budget_cny"] == expected and first["authorization_status"] == "not_authorized" and first["execution_enabled"] is False


@pytest.mark.parametrize("money", [0, 1.5, "", " 1 ", "1e2", "NaN", "Infinity", "-1", "+1", "00.1", "1.001", "1000000.01"])
def test_invalid_money_is_rejected_without_echo_or_write(env, money):
    client, _, _ = env
    response = client.post(BASE, json=request(budget_cny=money))
    assert response.status_code == 422 and "SYNTHETIC-PRIVATE-SCRIPT" not in response.text
    assert client.get(BASE).json()["briefs"] == []


@pytest.mark.parametrize("extra", [{"source_hash": "a" * 64}, {"authorization_status": "authorized"}, {"execution_enabled": True}, {"file_path": "/config.yaml"}, {"run_id": "anything"}, {"project_id": "old-project"}, {"source_version": 9}])
def test_forged_state_or_paths_are_not_input(env, extra):
    client, _, _ = env
    response = client.post(BASE, json=request(**extra))
    assert response.status_code == 422 and "SYNTHETIC-PRIVATE-SCRIPT" not in response.text


@pytest.mark.parametrize("changes", [{"title": " "}, {"title": "a" * 121}, {"script": "\n\t "}, {"script": "a" * 200001}, {"requirements": "a" * 20001}, {"production": {"ratio": "16:9"}}, {"production": {"duration_seconds": 15.0}}, {"production": {"style": "anime"}}, {"production": {"execution_enabled": True}}])
def test_lengths_specs_and_unknown_nested_fields(env, changes):
    client, _, _ = env
    assert client.post(BASE, json=request(**changes)).status_code == 422


@pytest.mark.parametrize("damage", ["truncated", "wrong_shape", "source_hash", "history", "index", "update_index", "duplicate", "invalid_utf8"])
def test_corrupt_storage_is_never_cleared_or_overwritten(env, damage):
    client, store, _ = env
    first = saved(client)
    client.put(f"{BASE}/{first['id']}", json=update(first, script="Second source"))
    path = store.root / "state.json"
    state = json.loads(path.read_text())
    if damage == "truncated":
        path.write_text('{"script":"PRIVATE-CORRUPT')
    elif damage == "duplicate":
        path.write_text('{"schema_version":1,"schema_version":1,"keys":{},"briefs":{}}')
    elif damage == "invalid_utf8":
        path.write_bytes(b"\xffPRIVATE-CORRUPT")
    else:
        doc = state["briefs"][first["id"]]
        if damage == "wrong_shape":
            state["briefs"] = []
        elif damage == "source_hash":
            doc["versions"][0]["source_hash"] = "b" * 64
        elif damage == "history":
            doc["versions"][0]["content"]["script"] = "PRIVATE-CORRUPT-HISTORY"
        elif damage == "index":
            state["keys"]["creator-create-key-001"]["request_hash"] = "b" * 64
        else:
            doc["update_keys"] = {}
        path.write_text(json.dumps(state))
    corrupted = path.read_bytes()
    for method, url, payload in [("GET", BASE, None), ("GET", f"{BASE}/{first['id']}", None), ("POST", BASE, request("new-key-on-corruption")), ("PUT", f"{BASE}/{first['id']}", update(first))]:
        response = client.request(method, url, json=payload) if payload is not None else client.get(url)
        assert response.status_code == 503 and response.json()["detail"]["code"] == "CORRUPT_STORAGE"
        assert "PRIVATE-CORRUPT" not in response.text and path.read_bytes() == corrupted


def test_failed_atomic_replace_preserves_history_and_allows_same_update_retry(env, monkeypatch):
    client, store, _ = env
    first = saved(client)
    path = store.root / "state.json"
    before = path.read_bytes()
    payload = update(first, script="Uncommitted source")
    with monkeypatch.context() as patcher:
        patcher.setattr("core.creator.store.os.replace", lambda *args: (_ for _ in ()).throw(OSError("PRIVATE-PATH-ERROR")))
        response = client.put(f"{BASE}/{first['id']}", json=payload)
    assert response.status_code == 503 and "PRIVATE-PATH" not in response.text
    assert path.read_bytes() == before and list(store.root.glob(".brief-*")) == []
    assert client.put(f"{BASE}/{first['id']}", json=payload).json()["brief"]["revision"] == 2


def test_legal_single_character_update_key_rename_is_corruption_not_a_new_operation(env):
    client, store, _ = env
    first = saved(client)
    original_key = "creator-update-key-001"
    renamed_key = "creator-update-key-002"
    payload = update(first, original_key, script="Synthetic source after one update")
    saved_update = client.put(f"{BASE}/{first['id']}", json=payload)
    assert saved_update.status_code == 200
    second = saved_update.json()["brief"]
    assert second["revision"] == 2

    # Alter exactly one valid key character in the real private file; leave the
    # receipt, snapshot, revision and original request completely untouched.
    path = store.root / "state.json"
    before = path.read_bytes()
    assert before.count(original_key.encode()) == 1
    corrupted = before.replace(original_key.encode(), renamed_key.encode(), 1)
    assert sum(left != right for left, right in zip(before, corrupted)) == 1
    path.write_bytes(corrupted)

    attempts = [
        ("GET", f"{BASE}/{first['id']}", None),
        ("GET", f"{BASE}/{first['id']}?revision=1", None),
        ("GET", BASE, None),
        ("GET", BASE + "/by-key/creator-create-key-001", None),
        ("PUT", f"{BASE}/{first['id']}", payload),
        ("PUT", f"{BASE}/{first['id']}", {**payload, "client_update_key": renamed_key}),
        ("PUT", f"{BASE}/{first['id']}", update(second, "fresh-update-key-003", script="Must not become revision three")),
        ("POST", BASE, request("fresh-create-key-002")),
    ]
    for method, url, body in attempts:
        response = client.request(method, url, json=body) if body is not None else client.get(url)
        assert response.status_code == 503
        assert response.json()["detail"]["code"] == "CORRUPT_STORAGE"
        assert "Synthetic source after one update" not in response.text
        assert path.read_bytes() == corrupted


@pytest.mark.parametrize("relation", ["parent", "same", "child"])
@pytest.mark.parametrize("nested", [False, True])
def test_all_static_roots_must_be_bidirectionally_disjoint(tmp_path, relation, nested):
    root = tmp_path.resolve() / "private" / "drafts"
    store = CreatorBriefStore(root)
    static = {"parent": root.parent, "same": root, "child": root / "snapshots"}[relation]
    app = app_for(store)
    target = FastAPI() if nested else app
    target.mount("/static", StaticFiles(directory=static, check_dir=False))
    if nested:
        app.mount("/nested", target)
    with TestClient(app) as client:
        assert client.post(BASE, json=request()).status_code == 503
    assert not root.exists()
    with pytest.raises(HarnessError):
        HarnessStore(root).check_private([static])


@pytest.mark.parametrize("target", ["state.json", ".state.lock"])
def test_private_state_and_lock_symlinks_rejected(env, tmp_path, target):
    client, store, _ = env
    first = saved(client)
    outside = tmp_path / "private-outside"
    outside.write_text("PRIVATE-OUTSIDE-CONTENT")
    path = store.root / target
    path.unlink(missing_ok=True)
    path.symlink_to(outside)
    response = client.get(f"{BASE}/{first['id']}")
    assert response.status_code == 503 and "PRIVATE-OUTSIDE" not in response.text
    assert outside.read_text() == "PRIVATE-OUTSIDE-CONTENT"


def test_root_or_parent_symlink_is_rejected(tmp_path):
    actual = tmp_path.resolve() / "actual"
    actual.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(actual, target_is_directory=True)
    for root in [alias, alias / "child"]:
        with pytest.raises(CreatorError):
            CreatorBriefStore(root)
    assert not (actual / "child").exists()


def test_lists_paginate_and_reflect_latest_revision(env):
    client, _, _ = env
    first = saved(client)
    saved(client, key="second-create-key-001", title="Second")
    page = client.get(BASE + "?limit=1").json()
    assert len(page["briefs"]) == 1 and page["has_more"] and page["next_offset"] == 1
    assert client.get(BASE + "?offset=1&limit=1").json()["briefs"][0]["id"] == first["id"]
    client.put(f"{BASE}/{first['id']}", json=update(first, title="Latest"))
    assert client.get(BASE).json()["briefs"][0]["title"] == "Latest"


def test_paths_queries_errors_and_logs_never_echo_script(env, caplog):
    client, _, _ = env
    brief = saved(client)
    for url in [f"{BASE}/{brief['id']}?revision=0", BASE + "?script=PRIVATE-QUERY", BASE + "/by-key/missing-valid-key", f"{BASE}/{('f' * 32)}", f"{BASE}/{brief['id']}?revision=99"]:
        response = client.get(url)
        assert response.status_code in {404, 422} and "SYNTHETIC-PRIVATE-SCRIPT" not in response.text
    response = client.post(BASE, content=b'{"script":"\\ud800","client_request_key":"invalid-unicode-key"}', headers={"Content-Type": "application/json"})
    assert response.status_code == 422 and "input" not in response.json()["detail"]
    assert "SYNTHETIC-PRIVATE-SCRIPT" not in caplog.text
