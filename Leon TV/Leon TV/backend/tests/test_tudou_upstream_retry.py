"""上游「生成失败」有限重试：只重试明确失败，且失败回执要留档。"""
import json
from pathlib import Path

import pytest

from models import tudou_client
from models.tudou_client import TudouClient, TudouTaskError, should_retry_upstream


def _upstream_failure():
    return TudouTaskError("生成失败，请稍后重试", "task_x", "upstream_failed", "failed")


def test_only_explicit_upstream_failures_are_retried():
    assert should_retry_upstream(_upstream_failure())
    assert not should_retry_upstream(TudouTaskError("x", None, "submit_unknown", "unknown"))
    assert not should_retry_upstream(TudouTaskError("x", "t", "submit_rejected", "rejected"))
    assert not should_retry_upstream(TudouTaskError("x", "t", "delivery_failed", "completed"))
    assert not should_retry_upstream(ValueError("other"))


def test_video_retries_then_succeeds(tmp_path, monkeypatch):
    calls = {"n": 0}

    def fake_once(self, prompt, image_path, save_path, model="wan3.0", duration=5, ratio="16:9", resolution="720p"):
        calls["n"] += 1
        if calls["n"] < 3:
            raise _upstream_failure()
        return str(tmp_path / "ok.mp4")

    monkeypatch.setattr(TudouClient, "_generate_video_once", fake_once)
    monkeypatch.setattr(tudou_client.time, "sleep", lambda *_: None)

    result = TudouClient(api_key="k").generate_video(prompt="p", image_path="", save_path=str(tmp_path / "v.mp4"))
    assert result.endswith("ok.mp4")
    assert calls["n"] == 3


def test_video_gives_up_after_the_retry_budget(tmp_path, monkeypatch):
    calls = {"n": 0}

    def fake_once(self, *args, **kwargs):
        calls["n"] += 1
        raise _upstream_failure()

    monkeypatch.setattr(TudouClient, "_generate_video_once", fake_once)
    monkeypatch.setattr(tudou_client.time, "sleep", lambda *_: None)

    with pytest.raises(TudouTaskError):
        TudouClient(api_key="k").generate_video(prompt="p", image_path="", save_path=str(tmp_path / "v.mp4"))
    assert calls["n"] == len(tudou_client.UPSTREAM_RETRY_DELAYS) + 1


def test_image_retry_does_not_resubmit_on_rejection(tmp_path, monkeypatch):
    calls = {"n": 0}

    def fake_once(self, *args, **kwargs):
        calls["n"] += 1
        raise TudouTaskError("拒绝", None, "submit_rejected", "rejected")

    monkeypatch.setattr(TudouClient, "_generate_image_once", fake_once)
    monkeypatch.setattr(tudou_client.time, "sleep", lambda *_: None)

    with pytest.raises(TudouTaskError):
        TudouClient(api_key="k").generate_image("p", None, "9:16", str(tmp_path))
    assert calls["n"] == 1


def test_failed_receipt_is_archived_so_the_next_attempt_can_submit(tmp_path):
    target = tmp_path / "seg.mp4"
    receipt = Path(f"{target}.tudou-task.json")
    receipt.write_text(json.dumps({"status": "failed"}), encoding="utf-8")

    TudouClient._archive_failed_receipt(str(target), 1)

    assert not receipt.exists()
    assert (tmp_path / "seg.mp4.tudou-task.json.failed1").exists()
