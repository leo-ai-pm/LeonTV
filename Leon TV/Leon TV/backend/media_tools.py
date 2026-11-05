"""Resolve the FFmpeg executable used by local video pipelines."""

import shutil
from pathlib import Path

from imageio_ffmpeg import get_ffmpeg_exe


def ffmpeg_executable() -> str:
    """Prefer a system FFmpeg, with the bundled executable as a fallback."""
    return shutil.which("ffmpeg") or get_ffmpeg_exe()


def ffconcat_entry(path: str) -> str:
    """Quote paths consistently for FFmpeg's concat demuxer."""
    absolute = str(Path(path).resolve())
    if any(char in absolute for char in ("\n", "\r", "\0")):
        raise ValueError("Media filename contains unsupported control characters")
    escaped = absolute.replace("'", "'\\''")
    return f"file '{escaped}'\n"


def move_media_with_provider_source(source: str, destination: str) -> None:
    """移动生成的图片，并同步迁移土豆的公网来源凭据。

    图生视频只接受公网 URL 或带 `<图片>.tudou-source.json` 的本地文件；
    阶段 Agent 重命名输出文件时若不同步迁移凭据，后续视频阶段就会失败。
    """
    import os

    sidecar = f"{source}.tudou-source.json"
    if os.path.exists(destination):
        os.remove(destination)
    os.rename(source, destination)
    if os.path.exists(sidecar):
        os.replace(sidecar, f"{destination}.tudou-source.json")
