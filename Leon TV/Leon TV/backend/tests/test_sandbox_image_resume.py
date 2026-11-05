"""图片任务失败可恢复：回执定位与恢复信息投影。"""
import json
from pathlib import Path

from api.routers import sandbox


def _write_receipt(directory: Path, name: str, payload: dict) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_image_receipt_is_found_by_task_id(tmp_path, monkeypatch):
    directory = tmp_path / "result" / "image_client"
    _write_receipt(directory, "a.png.tudou-task.json", {"task_id": "task_A", "status": "unknown"})
    _write_receipt(directory, "b.png.tudou-task.json", {"task_id": "task_B", "status": "completed"})
    monkeypatch.setattr(sandbox.settings, "CODE_DIR", str(tmp_path))

    found = sandbox._image_receipt_for_task("task_B")
    assert found is not None
    assert found.endswith("b.png.tudou-task.json")


def test_unknown_task_and_corrupt_files_are_skipped(tmp_path, monkeypatch):
    directory = tmp_path / "result" / "image_client"
    _write_receipt(directory, "broken.png.tudou-task.json", {"task_id": "task_C"})
    (directory / "broken.png.tudou-task.json").write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(sandbox.settings, "CODE_DIR", str(tmp_path))

    assert sandbox._image_receipt_for_task("task_C") is None
    assert sandbox._image_receipt_for_task("task_D") is None


def test_missing_directory_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(sandbox.settings, "CODE_DIR", str(tmp_path / "nope"))
    assert sandbox._image_receipt_for_task("task_any") is None


def test_persist_image_task_records_receipt_and_provider_task(tmp_path, monkeypatch):
    code_dir = tmp_path
    image_dir = code_dir / "result" / "image_client"
    _write_receipt(image_dir, "c.png.tudou-task.json", {"task_id": "task_E", "status": "unknown"})
    jobs_dir = code_dir / "result" / "sandbox" / "jobs"
    jobs_dir.mkdir(parents=True)
    job_path = jobs_dir / f"{'a' * 32}.json"
    job_path.write_text(json.dumps({"id": "a" * 32, "status": "running", "input": {}}), encoding="utf-8")

    monkeypatch.setattr(sandbox.settings, "CODE_DIR", str(code_dir))
    monkeypatch.setattr(sandbox, "SANDBOX_JOBS_DIR", jobs_dir)

    class FakeTaskError(Exception):
        task_id = "task_E"
        status = "unknown"
        code = "query_failed"

    sandbox._persist_image_task("a" * 32, "task_E", FakeTaskError("boom"))

    job = json.loads(job_path.read_text(encoding="utf-8"))
    assert job["provider_receipt_path"].endswith("c.png.tudou-task.json")
    assert job["provider_task"] == {
        "provider": "tudou", "task_id": "task_E",
        "status": "unknown", "error_code": "query_failed",
    }


def test_persist_image_task_is_a_noop_without_job_or_receipt(tmp_path, monkeypatch):
    monkeypatch.setattr(sandbox.settings, "CODE_DIR", str(tmp_path))
    jobs_dir = tmp_path / "result" / "sandbox" / "jobs"
    jobs_dir.mkdir(parents=True)
    monkeypatch.setattr(sandbox, "SANDBOX_JOBS_DIR", jobs_dir)

    class FakeTaskError(Exception):
        task_id = "task_missing"
        status = "unknown"
        code = "query_failed"

    sandbox._persist_image_task(None, "task_missing", FakeTaskError("boom"))
    sandbox._persist_image_task("b" * 32, "task_missing", FakeTaskError("boom"))
    assert list(jobs_dir.iterdir()) == []
