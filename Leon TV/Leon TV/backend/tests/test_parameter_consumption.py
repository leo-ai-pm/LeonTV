from unittest.mock import AsyncMock, Mock

import pytest

from pipelines import standard


@pytest.fixture
def standard_dependencies(tmp_path, monkeypatch):
    monkeypatch.setattr(standard, "task_output_dir", lambda _: str(tmp_path))
    monkeypatch.setattr(standard, "update_task", Mock())
    monkeypatch.setattr(standard, "append_artifact", Mock())
    monkeypatch.setattr(standard, "LLM", Mock())
    monkeypatch.setattr(standard, "generate_image_prompts", AsyncMock(side_effect=lambda narrations, *args, **kwargs: ["image"] * len(narrations)))
    monkeypatch.setattr(standard, "generate_image_api", Mock(return_value=str(tmp_path / "frame.png")))
    monkeypatch.setattr(standard, "generate_edge_tts", AsyncMock())
    monkeypatch.setattr(standard, "media_duration_seconds", lambda _: 1)
    monkeypatch.setattr(standard, "create_static_image_clip", Mock(return_value=str(tmp_path / "clip.mp4")))
    monkeypatch.setattr(standard, "concat_videos", lambda paths, output: output)
    return {"text": "第一句。第二句。\n第三句。", "title": "固定标题", "llm_model": "mock", "image_model": "mock"}


@pytest.mark.asyncio
async def test_standard_explicit_line_split_is_used(standard_dependencies):
    await standard.run("mock-task", {**standard_dependencies, "split_mode": "line"})
    assert len(standard.generate_image_prompts.call_args.args[0]) == 2


@pytest.mark.asyncio
async def test_standard_audio_false_does_not_call_tts(standard_dependencies):
    output, _ = await standard.run("mock-task", {**standard_dependencies, "generate_audio": False})
    standard.generate_edge_tts.assert_not_called()
    assert output["audios"] == []


@pytest.mark.asyncio
async def test_standard_legacy_n_scenes_is_used_for_inspiration(standard_dependencies, monkeypatch):
    generate = AsyncMock(return_value=["一。", "二。", "三。"])
    monkeypatch.setattr(standard, "generate_narrations_from_inspiration", generate)
    await standard.run("mock-task", {**standard_dependencies, "mode": "inspiration", "n_scenes": 3})
    assert generate.call_args.args[1] == 3


@pytest.mark.asyncio
async def test_explicit_sandbox_temperature_reaches_model_adapter(tmp_path, monkeypatch):
    from api.routers import sandbox
    from api.schemas.sandbox import SandboxLLMRequest
    from models.llm_client import LLM
    query = Mock(return_value="text")
    monkeypatch.setattr(LLM, "query", query)
    monkeypatch.setattr(sandbox, "SANDBOX_HISTORY_FILE", str(tmp_path / "history.json"))
    result = await sandbox.sandbox_llm(SandboxLLMRequest(model="mock", prompt="test", temperature=0.2))
    assert result["success"] is True
    assert query.call_args.kwargs["temperature"] == 0.2


@pytest.mark.asyncio
async def test_explicit_sandbox_style_is_applied(tmp_path, monkeypatch):
    from api.routers import sandbox
    from api.schemas.sandbox import SandboxT2IRequest
    from models.image_client import ImageClient
    query = Mock(return_value=["https://example.com/image.png"])
    monkeypatch.setattr(ImageClient, "generate_image", query)
    monkeypatch.setattr(sandbox, "SANDBOX_HISTORY_FILE", str(tmp_path / "history.json"))
    result = await sandbox.sandbox_t2i(SandboxT2IRequest(model="mock", prompt="原始文案", style="realistic"))
    assert result["success"] is True
    assert "原始文案" in query.call_args.args[0]
    assert query.call_args.args[0] != "原始文案"


@pytest.mark.parametrize("model,attribute,method", [
    ("gpt-mock", "_gpt_client", "query"), ("gemini-mock", "_gemini_client", "query"),
    ("deepseek-chat", "_deepseek_client", "query"), ("qwen-max", "_dashscope_client", "query"),
    ("kimi-mock", "_dashscope_vl_client", "chat"),
])
def test_temperature_survives_provider_dispatch(model, attribute, method):
    from models.llm_client import LLM
    client = LLM()
    provider = Mock()
    getattr(provider, method).return_value = "ok"
    setattr(client, attribute, provider)
    assert client.query("prompt", model=model, temperature=0.2) == "ok"
    kwargs = getattr(provider, method).call_args.kwargs
    assert kwargs.get("temperature", kwargs.get("parameters", {}).get("temperature")) == 0.2
