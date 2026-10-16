"""Keep raw generations; produce a separately logged 720x1280, 24fps, 15s review file."""

import argparse
import hashlib
import json
import subprocess
import wave
from pathlib import Path

from media_tools import ffmpeg_executable

from .inspect_media import inspect
from .ledger import atomic_json


def normalize(source, directory, output):
    original = json.loads((directory / "inspection.json").read_text())
    audio_duration = 0
    if original["has_audio"]:
        with wave.open(str(directory / "audio.wav")) as audio:
            audio_duration = audio.getnframes() / audio.getframerate()
    source_duration = max(original["frame_duration_seconds"], audio_duration)
    if source_duration < 14.5:
        raise ValueError("Source is too short; do not manufacture 15 seconds by padding")
    factor = source_duration / 15
    vf = (f"setpts=PTS/{factor:.12f},scale=720:1280:force_original_aspect_ratio=increase,"
          "crop=720:1280,setsar=1,fps=24,tpad=stop_mode=clone:stop_duration=0.1,trim=duration=15")
    filters = f"[0:v]{vf}[v]"
    if original["has_audio"]:
        filters += f";[0:a]atempo={factor:.12f},apad,atrim=duration=15[a]"
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise ValueError("Review output exists; preserve it instead of overwriting")
    command = [ffmpeg_executable(), "-y", "-nostdin", "-v", "error", "-i", str(source),
               "-filter_complex", filters, "-map", "[v]"]
    if original["has_audio"]:
        command += ["-map", "[a]", "-c:a", "aac", "-ar", "48000"]
    command += ["-c:v", "libx264", "-preset", "fast", "-crf", "18", "-pix_fmt", "yuv420p",
                "-movflags", "+faststart", "-frames:v", "360", "-t", "15", str(output)]
    atomic_json(directory / "normalization-intent.json", {
        "source": str(source), "source_sha256": original["sha256"], "source_duration_seconds": source_duration,
        "target": str(output), "retime_factor": factor, "command": command,
        "notes": "All source audio/video fed through a small common retime, crop/scale, then frame quantization; raw generation retained. No speech or creative-quality pass inferred.",
    })
    subprocess.run(command, check=True, capture_output=True, timeout=90)
    final = inspect(output, directory / "final")
    if final["size"] != [720, 1280] or final["frames"] != 360 or final["fps"] != 24:
        raise ValueError("Review-file technical specification failed")
    atomic_json(directory / "normalization-complete.json", {
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "final_sha256": final["sha256"], "output": str(output), "frames": final["frames"],
        "seconds": final["frame_duration_seconds"], "human_review": None,
    })
    return final


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = normalize(args.source.resolve(), args.directory.resolve(), args.output.resolve())
    print({k: result[k] for k in ["file", "sha256", "full_decode", "frames", "frame_duration_seconds", "size", "has_audio"]})
