"""Allowlisted local tools. No model, provider, network or publishing adapter."""

import hashlib
import json
import re
import subprocess
import sys
import time
from pathlib import Path

from path_security import contained_storage_path, validate_storage_id


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def atomic_json(path, value):
    import os

    temporary = path.with_suffix(".tmp")
    with temporary.open("w") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def inspect_video(path):
    from imageio_ffmpeg import count_frames_and_secs, read_frames

    from media_tools import ffmpeg_executable

    if not path.is_file():
        return {"passed": False, "failures": ["missing_artifact"]}
    decoded = subprocess.run(
        [ffmpeg_executable(), "-nostdin", "-hide_banner", "-xerror", "-i", str(path), "-f", "null", "-"],
        check=True, capture_output=True, timeout=25,
    )
    frames, _ = count_frames_and_secs(str(path))
    reader = read_frames(str(path))
    metadata = next(reader)
    colors = []
    try:
        for index, pixels in enumerate(reader):
            if index in (0, 30):
                channels = [pixels[offset::768] for offset in range(3)]
                means = [sum(channel) / len(channel) for channel in channels]
                red, green, blue = means
                colors.append("red" if red > 200 and green < 40 and blue < 40 else
                              "blue" if blue > 200 and red < 40 and green < 40 else "other")
            if index >= 30:
                break
    finally:
        reader.close()
    facts = {
        "frames": frames, "fps": metadata["fps"],
        "duration_seconds": round(frames / metadata["fps"], 3),
        "size": list(metadata["size"]), "sample_colors": colors,
        "has_audio": bool(re.search(r"Stream .*Audio:", decoded.stderr.decode(errors="replace"))),
        "full_decode": True,
    }
    checks = {
        "duration_2_seconds": frames == 60 and metadata["fps"] == 30,
        "landscape_1080p": facts["size"] == [1920, 1080],
        "red_then_blue": colors == ["red", "blue"], "silent": not facts["has_audio"],
    }
    return {"passed": all(checks.values()), "failures": [k for k, v in checks.items() if not v], "facts": facts}


def execute(root, request):
    def local(value):
        return contained_storage_path(root, value)

    args = request["args"]
    tool = request["tool"]
    if tool == "create_clip":
        from PIL import Image

        from pipelines.utils import create_static_image_clip

        if args["color"] not in {"red", "blue"}:
            raise ValueError("Only synthetic red/blue fixtures are allowed")
        image, output = local(args["image"]), local(args["output"])
        image.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (320, 180), args["color"]).save(image)
        create_static_image_clip(str(image), None, str(output), video_ratio="16:9", duration=1)
        return {"function": "pipelines.utils.create_static_image_clip", "synthetic": True}, [image, output]
    if tool == "concat":
        from pipelines.utils import concat_videos

        output = local(args["output"])
        output.parent.mkdir(parents=True, exist_ok=True)
        result = concat_videos([str(local(p)) for p in args["inputs"]], str(output))
        if result != str(output) or not output.is_file():
            raise RuntimeError("Production concat did not produce the requested artifact")
        return {"function": "pipelines.utils.concat_videos"}, [output, output.parent / "concat.txt"]
    if tool == "inspect":
        path = local(args["input"])
        return inspect_video(path), [path] if path.is_file() else []
    raise ValueError(f"Unknown tool: {tool}")


def main():
    root = Path(sys.argv[1]).resolve()
    task_id = validate_storage_id(sys.argv[2])
    request_path = contained_storage_path(root, "calls", task_id, "request.json")
    request = json.loads(request_path.read_text())
    started = time.monotonic()
    receipt = {"request_sha": sha(request_path)}
    try:
        result, files = execute(root, request)
        receipt.update(ok=True, result=result, outputs=[
            {"path": str(p.relative_to(root)), "sha256": sha(p), "bytes": p.stat().st_size} for p in files
        ])
    except Exception as exc:
        receipt.update(ok=False, error={"type": type(exc).__name__, "message": str(exc)[:2000]}, outputs=[])
    receipt["duration_seconds"] = time.monotonic() - started
    atomic_json(request_path.with_name("receipt.json"), receipt)


if __name__ == "__main__":
    main()
