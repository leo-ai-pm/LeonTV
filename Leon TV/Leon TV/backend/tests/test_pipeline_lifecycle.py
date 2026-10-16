import asyncio
import json
import threading
from unittest.mock import patch

import pytest

from pipelines import events, runner, storage


@pytest.fixture
def task_store(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "TASK_DATA_DIR", str(tmp_path / "tasks"))
    monkeypatch.setattr(storage, "TASK_RESULT_DIR", str(tmp_path / "result"))
    return storage


@pytest.mark.parametrize("identifier", ["../outside", "absolute", "..", "bad\\id"])
def test_task_identifier_cannot_escape_storage(task_store, identifier, tmp_path):
    if identifier == "absolute":
        identifier = str(tmp_path / "outside")
    with pytest.raises(ValueError):
        task_store.load_task(identifier)


def test_delete_rejects_output_directory_outside_task_root(task_store, tmp_path):
    task = task_store.create_task("standard", {})
    protected = tmp_path / "protected"
    protected.mkdir()
    (protected / "keep.txt").write_text("user asset")
    task_store.update_task(task["task_id"], output_dir=str(protected))
    with pytest.raises(ValueError):
        task_store.delete_task(task["task_id"])
    assert (protected / "keep.txt").exists()
    assert task_store.load_task(task["task_id"]) is not None


def test_completion_preserves_concurrently_appended_artifact(task_store):
    task = task_store.create_task("standard", {})
    loaded = threading.Event()
    proceed = threading.Event()
    append_done = threading.Event()
    original_load = task_store.load_task
    first = True
    failures = []

    def paused_load(task_id):
        nonlocal first
        data = original_load(task_id)
        if threading.current_thread().name == "complete" and first:
            first = False
            loaded.set()
            assert proceed.wait(2)
        return data

    def finish():
        try:
            task_store.mark_completed(task["task_id"], {}, [{"kind": "video", "path": "final.mp4"}])
        except BaseException as exc:
            failures.append(exc)

    def append():
        try:
            task_store.append_artifact(task["task_id"], {"kind": "image", "path": "reference.png"})
            append_done.set()
        except BaseException as exc:
            failures.append(exc)

    with patch.object(task_store, "load_task", paused_load):
        complete_thread = threading.Thread(target=finish, name="complete")
        complete_thread.start()
        assert loaded.wait(2)
        append_thread = threading.Thread(target=append, name="append")
        append_thread.start()
        append_done.wait(0.1)
        proceed.set()
        complete_thread.join(2)
        append_thread.join(2)
    assert not complete_thread.is_alive() and not append_thread.is_alive()
    assert not failures
    assert {x["path"] for x in original_load(task["task_id"])["artifacts"]} == {"reference.png", "final.mp4"}


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["completed", "failed"])
async def test_terminal_task_event_subscription_closes(status):
    stream = events.task_event_stream("terminal", initial_event={"type": "snapshot", "status": status})
    try:
        snapshot = await anext(stream)
        assert json.loads(snapshot.removeprefix("data: ").strip())["status"] == status
        with pytest.raises(StopAsyncIteration):
            await asyncio.wait_for(anext(stream), timeout=0.1)
    finally:
        await stream.aclose()


@pytest.mark.asyncio
async def test_pipeline_error_does_not_return_internal_traceback(task_store):
    task = task_store.create_task("standard", {})

    async def broken(*args):
        raise RuntimeError("private-internal-implementation")

    with patch.dict(runner.PIPELINE_REGISTRY, {"standard": broken}):
        await runner.run_pipeline_task(task["task_id"], "standard", {})
    error = task_store.load_task(task["task_id"])["error"]
    assert "Traceback" not in error
    assert "private-internal-implementation" not in error


def test_restarted_pipeline_is_not_reported_as_still_running(task_store):
    task = task_store.create_task("standard", {})
    # Write the previous process snapshot without registering a live worker.
    task.update(status="running")
    task_store.save_task(task)
    recovered = task_store.load_task(task["task_id"])
    assert recovered["status"] == "failed"
    assert "中断" in recovered["error"]


def test_live_pipeline_keeps_running_status(task_store):
    task = task_store.create_task("standard", {})
    task_store.mark_running(task["task_id"])
    assert task_store.load_task(task["task_id"])["status"] == "running"
    task_store.mark_completed(task["task_id"], {}, [])
