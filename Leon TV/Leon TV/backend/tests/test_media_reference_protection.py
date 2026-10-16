import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from api.routers import pipelines as pipeline_routes
from api.routers import sandbox, sessions
from api.services.studio_store import StudioStore
from config import settings
from core.orchestrator import WorkflowEngine
from pipelines import storage


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    code = tmp_path / "code"
    result = code / "result"
    temp = tmp_path / "temp"
    for name, value in {"CODE_DIR": code, "RESULT_DIR": result, "TEMP_DIR": temp,
                        "BASE_DIR": tmp_path}.items():
        monkeypatch.setattr(settings, name, str(value))
    monkeypatch.setattr(storage, "TASK_DATA_DIR", str(code / "data" / "tasks"))
    monkeypatch.setattr(storage, "TASK_RESULT_DIR", str(result / "task"))
    monkeypatch.setattr(sandbox, "SANDBOX_DIR", str(result / "sandbox"))
    monkeypatch.setattr(sandbox, "SANDBOX_HISTORY_FILE", str(result / "sandbox" / "history.json"))
    monkeypatch.setattr(sandbox, "SANDBOX_JOBS_DIR", result / "sandbox" / "jobs")
    monkeypatch.setattr(sandbox, "SANDBOX_ACTIVE_TASKS", {})
    engine = WorkflowEngine(session_dir=str(code / "data" / "sessions"))
    monkeypatch.setattr(sessions, "workflow_engine", engine)
    store = StudioStore(code / "data" / "studio_projects")
    app = FastAPI()
    for router in (sessions.router, pipeline_routes.router, sandbox.router):
        app.include_router(router)
    return SimpleNamespace(code=code, result=result, temp=temp, engine=engine, store=store, app=app)


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def source_media(workspace):
    workspace.engine.create_session("source", {})
    media = workspace.result / "image" / "source" / "frame.png"
    media.parent.mkdir(parents=True)
    media.write_bytes(b"retained asset")
    state = workspace.engine.sessions["source"]
    state.artifacts = {"reference_generation": {"scenes": [{"id": "scene", "selected": str(media), "versions": [str(media)]}]}}
    workspace.engine.save_session_to_disk("source")
    return media


def save_canvas_reference(workspace, reference):
    source = workspace.store.create("owner")
    workspace.store.save(source["id"], workspace.engine, {"expected_revision": 1, "title": "owner",
        "nodes": [{"id": "node", "data": {"mediaPath": "", "versions": [reference]}}], "edges": []})
    duplicate = workspace.store.clone(source["id"], workspace.engine)
    # Keep only the independent clone as a consumer, in this test's isolated store.
    workspace.store._path(source["id"]).unlink()
    return duplicate


def add_reference(workspace, owner, reference):
    if owner == "studio":
        save_canvas_reference(workspace, reference)
    elif owner == "session":
        workspace.engine.create_session("consumer", {"image_path": reference})
    elif owner == "task":
        storage.create_task("standard", {"image_path": reference})
    elif owner == "history":
        write_json(Path(sandbox.SANDBOX_HISTORY_FILE), [{"id": "consumer", "files": [reference]}])
    elif owner == "job":
        write_json(sandbox.SANDBOX_JOBS_DIR / "consumer.json", {"input": {"image": reference}})


@pytest.mark.asyncio
@pytest.mark.parametrize("owner", ["studio", "session", "task", "history", "job"])
@pytest.mark.parametrize("form", ["absolute", "code", "public", "relative"])
async def test_session_delete_rejects_other_metadata_references(workspace, owner, form):
    media = source_media(workspace)
    relative = media.relative_to(workspace.code).as_posix()
    reference = {"absolute": str(media), "code": f"code/{relative}", "public": f"/code/{relative}", "relative": relative}[form]
    add_reference(workspace, owner, reference)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=workspace.app), base_url="http://test") as client:
        response = await client.delete("/api/sessions/source")
    assert response.status_code == 409
    assert media.exists()
    assert Path(workspace.engine._session_path("source")).exists()
    assert "source" in workspace.engine.sessions


def test_session_self_reference_does_not_block_unreferenced_delete(workspace):
    media = source_media(workspace)
    assert workspace.engine.delete_session("source") is True
    assert not media.exists()
    assert not Path(workspace.engine._session_path("source")).exists()


def test_orphan_cleanup_keeps_referenced_media_and_deletes_unused_media(workspace):
    media = source_media(workspace)
    save_canvas_reference(workspace, f"/code/{media.relative_to(workspace.code).as_posix()}")
    Path(workspace.engine._session_path("source")).unlink()
    workspace.engine.sessions.pop("source")
    unused = workspace.result / "image" / "unused" / "frame.png"
    unused.parent.mkdir(parents=True)
    unused.write_bytes(b"unreferenced")
    outcome = workspace.engine.cleanup_orphan_results()
    assert media.exists()
    assert not unused.exists()
    assert "unused" in outcome["cleaned"]["images"]
    assert "source" in outcome["preserved"]["images"]


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["session", "orphans", "pipeline", "history"])
async def test_corrupt_index_blocks_deletion_before_any_data_changes(workspace, operation):
    media = source_media(workspace)
    task = storage.create_task("standard", {})
    task_media = Path(task["output_dir"]) / "frame.png"
    task_media.write_bytes(b"task")
    write_json(Path(sandbox.SANDBOX_HISTORY_FILE), [{"id": "record", "files": [str(media)]}])
    corrupt = workspace.code / "data" / "studio_projects" / "corrupt.json"
    corrupt.write_text("{broken", encoding="utf-8")
    if operation == "orphans":
        Path(workspace.engine._session_path("source")).unlink()
        workspace.engine.sessions.pop("source")
    path = {"session": "/api/sessions/source", "orphans": "/api/sessions",
            "pipeline": f"/api/tasks/{task['task_id']}", "history": "/api/sandbox/history/record"}[operation]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=workspace.app), base_url="http://test") as client:
        response = await client.delete(path)
    assert response.status_code == 409
    assert media.exists() and task_media.exists()
    assert json.loads(Path(sandbox.SANDBOX_HISTORY_FILE).read_text())[0]["id"] == "record"


@pytest.mark.asyncio
async def test_pipeline_delete_rejects_canvas_reference(workspace):
    task = storage.create_task("standard", {})
    media = Path(task["output_dir"]) / "frame.png"
    media.write_bytes(b"task")
    storage.append_artifact(task["task_id"], {"kind": "image", "path": str(media)})
    save_canvas_reference(workspace, f"code/{media.relative_to(workspace.code).as_posix()}")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=workspace.app), base_url="http://test") as client:
        response = await client.delete(f"/api/tasks/{task['task_id']}")
    assert response.status_code == 409
    assert media.exists() and storage.load_task(task["task_id"])


def test_pipeline_self_reference_does_not_block_unreferenced_delete(workspace):
    task = storage.create_task("standard", {})
    media = Path(task["output_dir"]) / "frame.png"
    media.write_bytes(b"task")
    storage.append_artifact(task["task_id"], {"kind": "image", "path": str(media)})
    assert storage.delete_task(task["task_id"]) is True
    assert not media.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("other_record", [False, True])
async def test_history_delete_keeps_media_referenced_by_canvas_or_another_record(workspace, other_record):
    media = workspace.result / "sandbox" / "frame.png"
    media.parent.mkdir(parents=True)
    media.write_bytes(b"history")
    history = [{"id": "record", "files": [str(media)]}]
    if other_record:
        history.append({"id": "other", "files": [f"/code/{media.relative_to(workspace.code).as_posix()}"]})
    else:
        save_canvas_reference(workspace, str(media))
    write_json(Path(sandbox.SANDBOX_HISTORY_FILE), history)
    response = await sandbox.sandbox_delete_record("record")
    assert response["success"] is True
    assert media.exists()
    assert response["preserved"]
    assert not any(item["id"] == "record" for item in json.loads(Path(sandbox.SANDBOX_HISTORY_FILE).read_text()))


@pytest.mark.asyncio
async def test_unreferenced_history_media_is_still_deleted(workspace):
    media = workspace.result / "sandbox" / "frame.png"
    media.parent.mkdir(parents=True)
    media.write_bytes(b"history")
    write_json(Path(sandbox.SANDBOX_HISTORY_FILE), [{"id": "record", "files": [str(media)]}])
    result = await sandbox.sandbox_delete_record("record")
    assert result["success"] is True
    assert not media.exists()
