"""Offline checks for the authorized relay comparison; no real HTTP calls."""

from unittest.mock import patch

import pytest

from models.video_client import VideoClient


@pytest.fixture(autouse=True)
def synthetic_trial_authorization(tmp_path, monkeypatch):
    """Keep offline tests independent of private live-trial authorization files."""
    from experiments.tudou_ab import runner

    unavailable_evidence = tmp_path / "private-evidence-not-distributed"
    assert not unavailable_evidence.exists()
    monkeypatch.setattr(runner, "EVIDENCE", unavailable_evidence)
    monkeypatch.setattr(runner, "load_authorization", lambda: {
        "task_id": runner.TASK,
        "status": "approved_bounded_live_api_trial",
        "budget": {"currency": "CNY", "hard_total": 100},
        "provider_origin": "https://api.ai-tudou.net",
        "operational_limits": {
            "image_submissions_including_repairs": 8,
            "video_submissions_per_model_including_initial": 3,
        },
    })


@pytest.mark.parametrize("model", ["minimax-h3", "seedance-2.5"])
def test_exact_ab_video_ids_route_to_tudou(model):
    from models.config_model import get_model_config

    assert get_model_config(model)["provider"] == "tudou"
    with patch("models.video_client.TudouClient") as relay, \
            patch.object(VideoClient, "_generate_seedance", return_value="wrong-provider") as ark:
        relay.return_value.generate_video.return_value = "https://media.example/clip.mp4"
        result = VideoClient().generate_video(
            "same shooting script", "https://media.example/frame.png", "/tmp/ab.mp4",
            model=model, duration=15, video_ratio="9:16", resolution="720P",
        )
        assert result == "https://media.example/clip.mp4"
        ark.assert_not_called()
        relay.return_value.generate_video.assert_called_once_with(
            prompt="same shooting script", image_path="https://media.example/frame.png",
            save_path="/tmp/ab.mp4", model=model, duration=15,
            ratio="9:16", resolution="720p",
        )


def test_ledger_preserves_budget_and_unknown_intent_on_reload(tmp_path):
    from experiments.tudou_ab.ledger import BudgetLedger

    request = {"model": "seedance-2.5", "duration": 15, "aspect_ratio": "9:16"}
    ledger = BudgetLedger(tmp_path, "test-authorization")
    ledger.reserve("video-seed", request, "19.50", "verified-price-fixture")
    ledger.begin("video-seed")
    ledger.record("video-seed", "unknown")
    restored = BudgetLedger(tmp_path, "test-authorization")
    assert restored.reserve("video-seed", request, "19.50", "verified-price-fixture")["status"] == "unknown"
    assert restored.snapshot()["reserved_upper_fen"] == 1950
    with pytest.raises(ValueError, match="never resubmit"):
        restored.begin("video-seed")
    with pytest.raises(ValueError, match="another request"):
        restored.reserve("video-seed", {**request, "duration": 10}, "19.50", "verified-price-fixture")


def test_hard_total_includes_all_operations_and_query_reservations(tmp_path):
    from experiments.tudou_ab.ledger import BudgetLedger

    ledger = BudgetLedger(tmp_path, "test-authorization")
    ledger.reserve("images", {"kind": "image"}, "79", "image-quote")
    ledger.reserve("h3", {"model": "minimax-h3"}, "1.50", "h3-quote")
    ledger.reserve("seed", {"model": "seedance-2.5"}, "19.50", "seed-quote")
    assert ledger.snapshot()["reserved_upper_fen"] == 10000
    with pytest.raises(ValueError, match="budget exceeded"):
        ledger.reserve("query", {"task_id": "known"}, "0.01", "query-quote")
    ledger.reserve("free-query", {"task_id": "known"}, "0", "verified-zero-query-quote")
    assert ledger.snapshot()["reserved_upper_fen"] == 10000


@pytest.mark.parametrize("amount", ["NaN", "Infinity", "-1", "100.01"])
def test_invalid_or_increased_limit_refused(tmp_path, amount):
    from experiments.tudou_ab.ledger import BudgetLedger

    with pytest.raises(ValueError):
        BudgetLedger(tmp_path, "test-authorization", amount)


def test_authorization_change_and_public_ledger_refused(tmp_path):
    from experiments.tudou_ab.ledger import BACKEND, BudgetLedger

    BudgetLedger(tmp_path, "original-authorization")
    with pytest.raises(ValueError, match="Authorization/limit changed"):
        BudgetLedger(tmp_path, "changed-authorization")
    with pytest.raises(ValueError, match="outside public"):
        BudgetLedger(BACKEND / "code/result/unsafe-ledger", "test")


def test_live_runner_persists_intent_before_only_post_and_reuses_response(tmp_path, monkeypatch):
    import json
    from types import SimpleNamespace

    from experiments.tudou_ab import runner

    monkeypatch.setattr(runner, "PRIVATE", tmp_path)
    monkeypatch.setattr(runner, "TudouClient", lambda: SimpleNamespace(
        api_key="unit-secret", base_url="https://api.ai-tudou.net", headers={"Authorization": "Bearer unit-secret"}))
    pilot = runner.Pilot()
    body = {"model": "minimax-h3", "prompt": "same", "duration": 15, "aspect_ratio": "9:16", "resolution": "720p"}

    def request(*args, **kwargs):
        state = json.loads((tmp_path / "budget-ledger.json").read_text())
        assert state["operations"]["h3-1"]["status"] == "submitting"
        assert state["operations"]["h3-1"]["upper_fen"] == 75
        return SimpleNamespace(status_code=200, ok=True, text=json.dumps({"data": {"id": "task-real-shape", "echo": "unit-secret"}}))

    with patch("experiments.tudou_ab.runner.requests.request", side_effect=request) as post:
        pilot.submit("h3-1", body)
        pilot.submit("h3-1", body)
    assert post.call_count == 1
    state = pilot.ledger.snapshot()
    assert state["reserved_upper_fen"] == 575
    assert state["operations"]["h3-1"]["provider_task_id"] == "task-real-shape"
    assert all("unit-secret" not in p.read_text() for p in tmp_path.rglob("*.json"))


def test_runner_network_uncertainty_blocks_another_submit(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from experiments.tudou_ab import runner

    monkeypatch.setattr(runner, "PRIVATE", tmp_path)
    monkeypatch.setattr(runner, "TudouClient", lambda: SimpleNamespace(
        api_key="unit-secret", base_url="https://api.ai-tudou.net", headers={"Authorization": "Bearer unit-secret"}))
    pilot = runner.Pilot()
    body = {"model": "seedance-2.5", "duration": 15, "aspect_ratio": "9:16", "resolution": "720p"}
    with patch("experiments.tudou_ab.runner.requests.request", side_effect=TimeoutError) as post:
        with pytest.raises(RuntimeError, match="never resubmit"):
            pilot.submit("seed-1", body)
        with pytest.raises(ValueError, match="never resubmit"):
            pilot.submit("seed-1", body)
        with pytest.raises(ValueError, match="earlier submission"):
            pilot.submit("seed-2", body)
    assert post.call_count == 1
    assert pilot.ledger.snapshot()["reserved_upper_fen"] == 2450


@pytest.mark.parametrize("model", ["minimax-h3", "seedance-2.5"])
def test_new_video_models_use_minimal_body_without_wan_extra(tmp_path, model):
    from models.tudou_client import TudouClient

    client = TudouClient(api_key="unit-test-key")
    with patch.object(client, "_submit", return_value="known-task") as submit, \
            patch.object(client, "_complete_video", return_value="https://media.example/video.mp4"):
        client.generate_video("same", "https://media.example/frame.png", str(tmp_path / "clip.mp4"),
                              model=model, duration=15, ratio="9:16", resolution="720p")
    assert submit.call_args.args == ("/v1/videos/generations", {
        "model": model, "prompt": "same", "duration": 15, "aspect_ratio": "9:16",
        "resolution": "720p", "images": ["https://media.example/frame.png"],
    })


def test_cached_response_recovers_task_id_after_simulated_ledger_crash(tmp_path, monkeypatch):
    import json
    from types import SimpleNamespace

    from experiments.tudou_ab import runner
    from experiments.tudou_ab.ledger import atomic_json, fingerprint

    monkeypatch.setattr(runner, "PRIVATE", tmp_path)
    monkeypatch.setattr(runner, "TudouClient", lambda: SimpleNamespace(
        api_key="unit-secret", base_url="https://api.ai-tudou.net", headers={}))
    pilot = runner.Pilot()
    body = {"model": "minimax-h3", "duration": 15, "aspect_ratio": "9:16", "resolution": "720p"}
    with patch("experiments.tudou_ab.runner.requests.request", return_value=SimpleNamespace(
            status_code=200, text=json.dumps({"data": {"id": "task-already-accepted"}}))) as post:
        pilot.submit("h3", body)
        state = json.loads(pilot.ledger.path.read_text())
        state["operations"]["h3"].update(status="submitting", provider_task_id=None)
        atomic_json(pilot.ledger.path, state)
        pilot.submit("h3", body)
    assert post.call_count == 1
    assert pilot.ledger.snapshot()["operations"]["h3"]["provider_task_id"] == "task-already-accepted"
    assert (tmp_path / "calls" / fingerprint("h3") / "response.json").exists()


def test_existing_media_cannot_be_relabelled_as_another_arm(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from experiments.tudou_ab import runner

    class OfflineTudouClient(runner.TudouClient):
        def __init__(self):
            super().__init__(api_key="unit-secret", base_url="https://api.ai-tudou.net")

    monkeypatch.setattr(runner, "PRIVATE", tmp_path / "private")
    monkeypatch.setattr(runner, "BACKEND", tmp_path)
    monkeypatch.setattr(runner, "TudouClient", OfflineTudouClient)
    pilot = runner.Pilot()
    request = {"method": "POST", "body": {"model": "seedance-2.5"}}
    pilot.ledger.reserve("seed", request, "19.50", "test-quote")
    pilot.ledger.begin("seed")
    pilot.ledger.record("seed", "completed", provider_task_id="task-seed")
    monkeypatch.setattr(pilot, "poll_once", lambda _: {"status": "completed", "task_id": "task-seed",
                         "result": {"video_url": "https://media.example/seed.mp4"}})
    path = tmp_path / "code/result" / runner.TASK / "h3-original.mp4"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"existing-other-arm")
    download = SimpleNamespace(called=False)
    monkeypatch.setattr(pilot.client, "_download", lambda *args: setattr(download, "called", True))
    with pytest.raises(ValueError, match="never relabel"):
        pilot.download("seed", path)
    assert not download.called
    assert path.read_bytes() == b"existing-other-arm"
