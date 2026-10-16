import subprocess
from unittest.mock import Mock, patch

import pytest

from api.routers import sandbox
from api.schemas.sandbox import SandboxT2IRequest
from config import settings
from core.agents.character_agent import CharacterDesignerAgent
from core.agents.editor_agent import VideoEditorAgent
from media_tools import ffmpeg_executable
from models.image_client import ImageClient
from pipelines.utils import concat_videos


def make_clip(path):
    subprocess.run([ffmpeg_executable(), "-y", "-v", "error", "-f", "lavfi", "-i",
                    "color=c=blue:s=64x64:r=10:d=0.5", "-c:v", "libx264", str(path)],
                   check=True, capture_output=True, timeout=20)


@pytest.mark.asyncio
async def test_editor_rejects_missing_selected_clip_before_export(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "RESULT_DIR", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    valid = tmp_path / "valid.mp4"
    make_clip(valid)
    with pytest.raises((ValueError, FileNotFoundError), match="missing-shot"):
        await VideoEditorAgent().process({"session_id": "editor",
            "_session_artifacts": {"video_generation": {"clips": [
                {"id": "valid", "selected": str(valid)},
                {"id": "missing-shot", "selected": str(tmp_path / "missing.mp4")},
            ]}}})


def test_concat_accepts_quoted_filename_and_rejects_missing_clip(tmp_path):
    source = tmp_path / "postman's shot.mp4"
    make_clip(source)
    output = tmp_path / "final.mp4"
    assert concat_videos([str(source)], str(output)) == str(output)
    assert output.stat().st_size > 500
    with pytest.raises(FileNotFoundError):
        concat_videos([str(source), str(tmp_path / "missing.mp4")], str(output))


@pytest.mark.asyncio
async def test_editor_complete_export_accepts_quoted_filename(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "RESULT_DIR", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    source = tmp_path / "postman's shot.mp4"
    make_clip(source)
    result = await VideoEditorAgent().process({"session_id": "complete-editor",
        "_session_artifacts": {"video_generation": {"clips": [{"id": "shot", "selected": str(source)}]}}})
    assert result["stage_completed"] is True
    subprocess.run([ffmpeg_executable(), "-v", "error", "-i", result["payload"]["final_video"], "-f", "null", "-"],
                   check=True, capture_output=True, timeout=20)


def test_static_clip_and_audio_replace_support_disabled_audio(tmp_path, monkeypatch):
    from PIL import Image

    from pipelines import utils
    image = tmp_path / "frame.png"
    Image.new("RGB", (16, 16), "blue").save(image)
    monkeypatch.setattr(utils, "_resolution_from_ratio", lambda _: (64, 64))
    static = str(tmp_path / "silent.mp4")
    utils.create_static_image_clip(str(image), None, static, duration=0.2)
    muted = str(tmp_path / "muted.mp4")
    utils.replace_video_audio(static, None, muted)
    subprocess.run([ffmpeg_executable(), "-v", "error", "-i", muted, "-f", "null", "-"],
                   check=True, capture_output=True, timeout=20)


def test_gpt_reference_images_reach_existing_adapter(tmp_path):
    output = tmp_path / "generated.png"
    output.write_bytes(b"image")
    client = ImageClient()
    client._gpt_client = Mock()
    client._gpt_client.generate_image.return_value = str(output)
    client.generate_image("same prompt", image_paths=["https://example.com/ref.png"],
                          model="gpt-image-2", save_dir=str(tmp_path))
    assert client._gpt_client.generate_image.call_args.kwargs["image_urls"] == ["https://example.com/ref.png"]


@pytest.mark.asyncio
async def test_sandbox_empty_image_output_is_failure(tmp_path):
    with patch("models.image_client.ImageClient.generate_image", return_value=[]), \
         patch.object(sandbox, "SANDBOX_HISTORY_FILE", str(tmp_path / "history.json")):
        result = await sandbox.sandbox_t2i(SandboxT2IRequest(model="mock", prompt="test"))
    assert result["success"] is False
    assert not (tmp_path / "history.json").exists()


def test_remote_media_urls_are_not_rewritten_as_local_results():
    assert sandbox._normalize_path("https://example.com/image.png") == "https://example.com/image.png"


@pytest.mark.parametrize("result", ["not JSON", '{"best_index": 0}'])
def test_unverified_vlm_quality_is_not_reported_as_acceptable(result):
    with patch("models.vlm_client.VLM.query", return_value=result):
        agent = CharacterDesignerAgent()
        if "best_index" in result:
            _, evaluation = agent._select_best_with_vlm(["a.png", "b.png"], "邮差", "深蓝制服", "characters", "human", "mock")
        else:
            evaluation = agent._evaluate_with_vlm("a.png", "深蓝制服", "characters", "mock")
    assert evaluation is None or evaluation["is_acceptable"] is False
