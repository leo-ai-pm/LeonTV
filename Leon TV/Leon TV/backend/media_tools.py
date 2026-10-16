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
