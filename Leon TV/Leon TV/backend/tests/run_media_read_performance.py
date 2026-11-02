"""One persistent synthetic fixture; subsequent readonly HTTP reuse optional."""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

GUARD = '''import json, os, socket
def denied(*args, **kwargs):
    with open(os.environ["D51_NETWORK_LOG"], "a") as stream:
        stream.write(json.dumps({"event": "blocked_network"}) + "\\n")
    raise RuntimeError("D51: all outbound DNS/socket/UDP denied")
socket.getaddrinfo = denied
socket.socket.connect = denied
socket.socket.connect_ex = denied
socket.socket.sendto = denied
'''


def build(output):
    source = Path(__file__).resolve().parents[1]
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    stage = output / "public-backend"
    manifest = {}
    names = {p.relative_to(source) for p in source.glob("*.py") if not p.is_symlink()}
    for folder in ("api", "core", "utils", "models", "experiments", "tests", "pipelines", "services", "prompts"):
        names.update(p.relative_to(source) for p in (source / folder).rglob("*.py") if not p.is_symlink() and "__pycache__" not in p.parts)
    for folder in ("schemas", "prompts", "tests/fixtures"):
        names.update(p.relative_to(source) for p in (source / folder).rglob("*") if p.is_file() and not p.is_symlink() and p.suffix in {".json", ".txt", ".md"})
    for name in sorted(names):
        path = stage / name
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / name, path)
        manifest[name.as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    (output / "public-source-manifest.json").write_text(json.dumps(manifest, indent=2))
    guard = output / "guard"
    guard.mkdir()
    (guard / "sitecustomize.py").write_text(GUARD)
    network = output / "network.jsonl"
    network.touch()
    env = {k: v for k, v in os.environ.items() if k in {"PATH", "LANG", "LC_ALL", "TMPDIR", "SYSTEMROOT"}}
    env.update(PYTHONPATH=os.pathsep.join(map(str, (guard, stage / "tests", stage))), PYTHONDONTWRITEBYTECODE="1",
               PYTEST_DISABLE_PLUGIN_AUTOLOAD="1", D51_ISOLATED_RUNNER="1", D51_EVIDENCE=str(output), D51_NETWORK_LOG=str(network))
    command = [sys.executable, "-m", "pytest", "-q", "-s", "tests/media_read_performance_scenario.py", "--basetemp", str(output / "synthetic-fixtures")]
    timed_out = False
    try:
        result = subprocess.run(command, cwd=stage, env=env, text=True, capture_output=True, timeout=900)
        stdout, stderr, code = result.stdout, result.stderr, result.returncode
    except subprocess.TimeoutExpired as error:
        def decode(value):
            return value.decode(errors="replace") if isinstance(value, bytes) else value or ""
        stdout, stderr, code, timed_out = decode(error.stdout), decode(error.stderr), 124, True
    (output / "pytest.log").write_text(stdout + stderr)
    unchanged = all(hashlib.sha256((stage / n).read_bytes()).hexdigest() == h for n, h in manifest.items())
    report = {"command": command[1:], "exit_code": code, "timed_out": timed_out,
              "public_files": len(manifest), "source_unchanged": unchanged, "network_attempts": len(network.read_text().splitlines()),
              "actual_download_worker_calls": 0, "fixture_assembly": "text/asset MockTransports + synchronous image raw fixtures + offline PNG proof writer; capture untested"}
    (output / "runner-result.json").write_text(json.dumps(report, indent=2))
    print(stdout + stderr)
    print(json.dumps(report))
    return code or (0 if unchanged and not report["network_attempts"] else 1)


def serve(metadata_path, port):
    import socket

    def denied(*args, **kwargs):
        raise RuntimeError("D51 readonly fixture server denies outbound network")

    socket.getaddrinfo = denied
    socket.socket.connect = denied
    socket.socket.connect_ex = denied
    socket.socket.sendto = denied
    import uvicorn
    from media_read_fixture import readonly_app

    metadata = json.loads(metadata_path.read_text())
    root = Path(metadata["root"])
    if not root.is_absolute() or root.resolve() != root:
        raise ValueError("Expected canonical synthetic fixture root")
    app = readonly_app(root)

    @app.middleware("http")
    async def readonly(request, next_handler):
        if request.method != "GET":
            from starlette.responses import JSONResponse
            return JSONResponse({"error": "readonly synthetic fixture"}, status_code=405)
        return await next_handler(request)

    try:
        print(json.dumps({"fixture": str(root), "task_id": metadata["task_id"], "port": port}), flush=True)
        uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning", lifespan="off")
    finally:
        app.state.close_fixture()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    parser.add_argument("--serve", type=Path, metavar="FIXTURE_JSON")
    parser.add_argument("--port", type=int, default=39151)
    args = parser.parse_args()
    if args.serve:
        serve(args.serve.resolve(), args.port)
    elif args.output:
        raise SystemExit(build(args.output))
    else:
        parser.error("Choose --output NEW_DIRECTORY or --serve FIXTURE_JSON")
