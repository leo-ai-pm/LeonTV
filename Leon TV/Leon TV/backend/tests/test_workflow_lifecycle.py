import asyncio
from unittest.mock import patch

import pytest

from core.agents.base_agent import AgentInterface
from core.agents.script_agent import ScriptWriterAgent
from core.orchestrator import WorkflowEngine, WorkflowStage


@pytest.fixture
def engine(tmp_path):
    return WorkflowEngine(session_dir=str(tmp_path))


@pytest.mark.asyncio
async def test_smart_continue_reaches_review_without_unbound_language(tmp_path):
    agent = ScriptWriterAgent()
    responses = ["第2集\n邮差看向灯塔。", "台词可用", "情节可用", "第2集\n灯塔亮起。",
                 '{"new_characters": [], "new_settings": []}',
                 '[{"episode_number": 2, "content": "灯塔亮起。"}]']
    with patch("models.llm_client.LLM"), \
         patch.object(agent, "_cancellable_query", side_effect=responses), \
         patch.object(agent, "_save_result"):
        result = await agent.process(
            {"session_id": "continuation", "title": "海边来信", "llm_model": "mock",
             "episodes": [{"episode_number": 1, "content": "取信。"}]},
            {"action": "smart_continue", "episodes_to_add": 1, "sequel_idea": "灯塔亮起"},
        )
    assert result["requires_intervention"] is True
    assert result["payload"]["new_episodes"][0]["episode_number"] == 2


def test_explicit_false_survives_session_defaults(engine):
    engine.create_session("defaults", {"web_search": True, "enable_concurrency": True})
    _, data = engine.prepare_stage_execution("defaults", "script_generation", {"web_search": False})
    assert data["web_search"] is False
    agent = ScriptWriterAgent()
    merged = agent._merge_session_params({"web_search": False, "_session_meta": {"web_search": True}})
    assert merged["web_search"] is False


def test_restart_marks_orphaned_running_stage_stopped_and_preserves_assets(engine):
    engine.create_session("recover", {})
    state = engine.sessions["recover"]
    state.current_stage = WorkflowStage.VIDEO_GENERATION
    state.status["video_generation"] = "running"
    state.artifacts["video_generation"] = {"clips": [{"id": "clip", "selected": "result/clip.mp4"}]}
    engine.save_session_to_disk("recover")
    engine.sessions.clear()
    engine._load_sessions_from_disk()
    recovered = engine.get_status_snapshot("recover")
    assert recovered["status"]["video_generation"] == "stopped"
    assert recovered["artifacts"]["video_generation"]["clips"][0]["selected"] == "result/clip.mp4"


def test_lazy_load_restores_error_like_startup_load(engine):
    engine.create_session("error-state", {})
    state = engine.sessions["error-state"]
    state.error = "provider unavailable"
    engine.save_session_to_disk("error-state")
    engine.sessions.clear()
    assert engine.get_state("error-state").error == "provider unavailable"


@pytest.mark.parametrize("identifier", ["../outside", "absolute", "..", "bad\\id"])
def test_session_identifier_cannot_escape_storage(engine, identifier, tmp_path):
    if identifier == "absolute":
        identifier = str(tmp_path / "outside")
    with pytest.raises(ValueError):
        engine.create_session(identifier, {})


def test_invalid_session_delete_cannot_remove_sibling_results(engine, tmp_path, monkeypatch):
    from config import settings
    results = tmp_path / "results"
    (results / "video").mkdir(parents=True)
    valuable = results / "video" / "keep.mp4"
    valuable.write_bytes(b"user video")
    monkeypatch.setattr(settings, "RESULT_DIR", str(results))
    # An invalid legacy in-memory entry must not bypass identifier validation.
    engine.sessions[".."] = engine.sessions.get("safe")
    with pytest.raises(ValueError):
        engine.delete_session("..")
    assert valuable.exists()


def test_stage_upload_rejects_fake_image_bytes(engine, tmp_path, monkeypatch):
    from io import BytesIO

    from config import settings
    monkeypatch.setattr(settings, "RESULT_DIR", str(tmp_path / "results"))
    engine.create_session("upload", {})
    with pytest.raises(ValueError):
        engine.upload_artifact_image("upload", "reference_generation", "scenes", "scene-1", BytesIO(b"not PNG"), "frame.png")
    assert not list((tmp_path / "results").rglob("*.png"))


@pytest.mark.asyncio
async def test_stop_during_agent_execution_stays_stopped(engine):
    engine.create_session("stop", {})

    class StopAwareAgent(AgentInterface):
        async def process(self, input_data, intervention=None):
            engine.stop_session("stop")
            self._check_cancel()

    engine.agent_factories[WorkflowStage.SCRIPT_GENERATION] = StopAwareAgent
    try:
        await engine.execute_stage(engine.sessions["stop"], WorkflowStage.SCRIPT_GENERATION, {})
    except (RuntimeError, asyncio.CancelledError):
        pass
    assert engine.sessions["stop"].status["script_generation"] == "stopped"
    assert engine.sessions["stop"].error is None


@pytest.mark.asyncio
async def test_continue_after_new_session_selects_script_stage(engine):
    engine.create_session("new-project", {})
    result = await engine.continue_workflow("new-project")
    assert result["status"] == "ready"
    assert result["next_stage"] == "script_generation"


@pytest.mark.asyncio
async def test_continue_does_not_convert_stopped_stage_to_completed(engine):
    engine.create_session("stopped", {})
    state = engine.sessions["stopped"]
    state.current_stage = WorkflowStage.VIDEO_GENERATION
    state.status["video_generation"] = "stopped"
    state.artifacts["video_generation"] = {"clips": [{"id": "clip", "selected": "old.mp4"}]}
    result = await engine.continue_workflow("stopped")
    assert result["status"] == "error"
    assert state.status["video_generation"] == "stopped"


@pytest.mark.asyncio
async def test_agent_context_is_not_recursively_saved_into_artifact(engine):
    engine.create_session("context", {"idea": "sample"})

    class EchoAgent(AgentInterface):
        async def process(self, input_data, intervention=None):
            return {"payload": {**input_data, "title": "result"}, "stage_completed": True}

    engine.agent_factories[WorkflowStage.SCRIPT_GENERATION] = EchoAgent
    await engine.execute_stage(engine.sessions["context"], WorkflowStage.SCRIPT_GENERATION, {})
    artifact = engine.sessions["context"].artifacts["script_generation"]
    assert artifact["title"] == "result"
    assert "_session_artifacts" not in artifact
    assert "_session_meta" not in artifact
