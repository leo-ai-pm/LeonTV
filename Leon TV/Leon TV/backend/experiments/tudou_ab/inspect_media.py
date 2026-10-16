"""Real local decoding and frame evidence; does not invent creative-quality verdicts."""

import argparse
import hashlib
import re
import subprocess
from pathlib import Path

from imageio_ffmpeg import count_frames_and_secs, read_frames

from media_tools import ffmpeg_executable

from .ledger import atomic_json


def inspect(path, directory):
    directory.mkdir(parents=True, exist_ok=True)
    ffmpeg = ffmpeg_executable()
    decoded = subprocess.run([ffmpeg, "-nostdin", "-hide_banner", "-xerror", "-i", str(path),
                              "-f", "null", "-"], capture_output=True, timeout=90)
    (directory / "full-decode.txt").write_bytes(decoded.stderr)
    if decoded.returncode:
        raise RuntimeError("Full decode failed; preserve the original file and decoder log")
    frames, seconds = count_frames_and_secs(str(path))
    reader = read_frames(str(path))
    metadata = next(reader)
    reader.close()
    stream_lines = [line.strip() for line in decoded.stderr.decode(errors="replace").splitlines()
                    if re.search(r"Stream .* (Video|Audio):", line)]
    subprocess.run([ffmpeg, "-y", "-nostdin", "-v", "error", "-i", str(path),
                    "-vf", "fps=1,scale=180:-1,tile=5x3", "-frames:v", "1", str(directory / "contact-sheet.jpg")],
                   check=True, capture_output=True, timeout=45)
    has_audio = any("Audio:" in line for line in stream_lines)
    if has_audio:
        subprocess.run([ffmpeg, "-y", "-nostdin", "-v", "error", "-i", str(path), "-vn",
                        "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(directory / "audio.wav")],
                       check=True, capture_output=True, timeout=45)
    report = {
        "file": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "bytes": path.stat().st_size,
        "full_decode": True, "frames": frames, "counted_duration_seconds": seconds,
        "fps": metadata["fps"], "frame_duration_seconds": frames / metadata["fps"],
        "size": list(metadata["size"]), "metadata": metadata, "has_audio": has_audio,
        "stream_lines": stream_lines, "visual_review_frames": str(directory / "contact-sheet.jpg"),
        "dialogue_status": "unknown_pending_independent_audiovisual_review",
        "action_and_identity_status": "unknown_pending_independent_audiovisual_review",
        "quality_status": "unknown", "human_review": None,
        "duration_policy": "Measured only; tolerance not frozen, no automatic content pass",
    }
    atomic_json(directory / "inspection.json", report)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    parser.add_argument("--directory", required=True, type=Path)
    args = parser.parse_args()
    result = inspect(args.path.resolve(), args.directory.resolve())
    print({k: result[k] for k in ["file", "full_decode", "frames", "frame_duration_seconds", "size", "has_audio"]})
