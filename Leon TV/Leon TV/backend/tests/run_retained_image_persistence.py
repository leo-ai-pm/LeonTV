"""Persistent-output public runner for one native retained-unknown seam."""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


def run(output):
    source = Path(__file__).resolve().parents[1]
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    stage = output / "public-backend"
    stage.mkdir()
    names = set()
    for folder in ("api", "core", "utils", "models", "experiments", "tests", "pipelines", "services", "prompts"):
        names.update(p.relative_to(source) for p in (source / folder).rglob("*.py") if not p.is_symlink() and "__pycache__" not in p.parts)
    names.update(p.relative_to(source) for p in source.glob("*.py"))
    for folder in ("schemas", "prompts", "tests/fixtures"):
        names.update(p.relative_to(source) for p in (source / folder).rglob("*") if p.is_file() and not p.is_symlink() and p.suffix in {".json", ".txt", ".md"})
    manifest = {}
    for name in sorted(names):
        path = stage / name
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / name, path)
        manifest[name.as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    (output / "public-source-manifest.json").write_text(json.dumps(manifest, indent=2))
    guard = output / "guard"
    guard.mkdir()
    raw = (stage / "tests/production_image_network_guard.py").read_text()
    raw += '''
# Only the explicit synthetic HTTP server port is reachable, including loopback.
_local_connector = socket.socket.connect
def bounded_local(sock, address):
    port = os.environ.get("D48_LOOPBACK_PORT")
    permitted = port and isinstance(address, tuple) and (address[:2] == ("8.8.8.8", 443) or address[:2] == ("127.0.0.1", int(port)))
    if not permitted:
        _log("D48_EXTERNAL_LOG", "blocked_unlisted_connection")
        raise RuntimeError("Retained persistence: connection outside synthetic server denied")
    return _local_connector(sock, address)
def blocked_udp(*args, **kwargs):
    _log("D48_EXTERNAL_LOG", "blocked_udp")
    raise RuntimeError("Retained persistence: UDP denied")
def bounded_connect_ex(sock, address):
    bounded_local(sock, address)
    return 0
socket.socket.connect = bounded_local
socket.socket.connect_ex = bounded_connect_ex
socket.socket.sendto = blocked_udp
'''
    (guard / "sitecustomize.py").write_text(raw)
    external, local = output / "external.jsonl", output / "local.jsonl"
    external.touch()
    local.touch()
    env = {k: v for k, v in os.environ.items() if k in {"PATH", "LANG", "LC_ALL", "TMPDIR", "SYSTEMROOT"}}
    env.update(PYTHONPATH=os.pathsep.join(map(str, (guard, stage / "tests", stage))), PYTHONDONTWRITEBYTECODE="1",
        PYTEST_DISABLE_PLUGIN_AUTOLOAD="1", D48_EXTERNAL_LOG=str(external), D48_LOCAL_LOG=str(local),
        D50_EVIDENCE=str(output), D50_ISOLATED_RUNNER="1")
    command = [sys.executable, "-m", "pytest", "-q", "-s", "tests/retained_image_persistence_scenario.py", "--basetemp", str(output / "synthetic-fixtures")]
    timed_out = False
    try:
        result = subprocess.run(command, cwd=stage, env=env, capture_output=True, text=True, timeout=1500)
        stdout, stderr, exit_code = result.stdout, result.stderr, result.returncode
    except subprocess.TimeoutExpired as error:
        def decoded(value):
            return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value or ""
        stdout, stderr, exit_code, timed_out = decoded(error.stdout), decoded(error.stderr), 124, True
    (output / "pytest.log").write_text(stdout + stderr)
    unchanged = all(hashlib.sha256((stage / n).read_bytes()).hexdigest() == h for n, h in manifest.items())
    summary = {"command": command[1:], "exit_code": exit_code, "timed_out": timed_out, "public_files": len(manifest),
        "source_unchanged": unchanged, "external_attempts": len(external.read_text().splitlines()),
        "local_worker_connections": local.read_text().splitlines().count("mapped_worker"),
        "transport": "actual worker / one explicit local HTTP seam, synthetic supplier only; no real TLS/provider"}
    (output / "runner-result.json").write_text(json.dumps(summary, indent=2))
    print(stdout + stderr)
    print(json.dumps(summary))
    return exit_code or (0 if unchanged and not summary["external_attempts"] else 1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True, help="New persistent directory; never automatically cleaned")
    args = parser.parse_args()
    raise SystemExit(run(args.output))
