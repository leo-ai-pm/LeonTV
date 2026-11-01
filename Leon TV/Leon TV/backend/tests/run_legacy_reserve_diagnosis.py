"""Public self-contained offline COMP01 feedback command. No private inputs."""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

GUARD = '''import os, socket
def blocked(*args, **kwargs):
    with open(os.environ["RESERVE_NETWORK_LOG"], "a") as f: f.write("blocked_network_attempt\\n")
    raise RuntimeError("Legacy reserve diagnostic: all network denied")
socket.getaddrinfo = blocked
socket.socket.connect = blocked
socket.socket.connect_ex = blocked
socket.socket.sendto = blocked
'''


def run(output, iterations, clocks, delay_ms, mode):
    source = Path(__file__).resolve().parents[1]
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    stage = output / "public-backend"
    stage.mkdir()
    manifest, names = {}, set()
    for folder in ("api", "core", "utils", "models", "experiments", "tests", "pipelines", "services", "prompts"):
        names.update(p.relative_to(source) for p in (source / folder).rglob("*.py") if not p.is_symlink() and "__pycache__" not in p.parts)
    names.update(p.relative_to(source) for p in source.glob("*.py"))
    for folder in ("schemas", "prompts", "tests/fixtures"):
        names.update(p.relative_to(source) for p in (source / folder).rglob("*") if p.is_file() and not p.is_symlink() and p.suffix in {".json", ".txt", ".md"})
    for name in sorted(names):
        destination = stage / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / name, destination)
        manifest[name.as_posix()] = hashlib.sha256(destination.read_bytes()).hexdigest()
    (output / "public-source-manifest.json").write_text(json.dumps(manifest, indent=2))
    guard = output / "guard"
    guard.mkdir()
    (guard / "sitecustomize.py").write_text(GUARD)
    network = output / "network.jsonl"
    network.touch()
    env = {k: v for k, v in os.environ.items() if k in {"PATH", "LANG", "LC_ALL", "TMPDIR", "SYSTEMROOT"}}
    env.update(PYTHONPATH=os.pathsep.join(map(str, (guard, stage / "tests", stage))),
               PYTHONDONTWRITEBYTECODE="1", PYTEST_DISABLE_PLUGIN_AUTOLOAD="1", LEONTV_RESERVE_DIAGNOSTIC="1",
               RESERVE_NETWORK_LOG=str(network), RESERVE_DIAGNOSTIC_OUTPUT=str(output),
               RESERVE_DIAGNOSTIC_BUILD_DELAY_MS=str(delay_ms),
               RESERVE_DIAGNOSTIC_MODE=mode,
               RESERVE_DIAGNOSTIC_ITERATIONS=str(iterations), RESERVE_DIAGNOSTIC_CLOCKS="1" if clocks else "0")
    command = [sys.executable, "-m", "pytest", "-q", "-s", "tests/legacy_reserve_scenario.py", "--basetemp", str(output / "synthetic-fixtures")]
    if mode == "prefix":
        command = [sys.executable, "-m", "pytest", "-q", "-s", "-p", "legacy_reserve_diagnostic_plugin",
                   "tests/test_image_operator.py", "tests/test_media_ledger.py", "tests/test_media_runtime.py",
                   "--basetemp", str(output / "synthetic-fixtures")]
    timeout = 660 if mode == "prefix" else 300
    timed_out = False
    try:
        result = subprocess.run(command, cwd=stage, env=env, capture_output=True, text=True, timeout=timeout)
        stdout, stderr, exit_code = result.stdout, result.stderr, result.returncode
    except subprocess.TimeoutExpired as error:
        def decoded(value):
            return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value or ""
        stdout, stderr, exit_code = decoded(error.stdout), decoded(error.stderr), 124
        timed_out = True
    (output / "pytest.log").write_text(stdout + stderr)
    unchanged = all(hashlib.sha256((stage / n).read_bytes()).hexdigest() == h for n, h in manifest.items())
    report = {"exit_code": exit_code, "timed_out": timed_out, "timeout_seconds": timeout,
              "partial_output_preserved": timed_out, "source_files": len(manifest), "source_unchanged": unchanged,
              "network_attempts": len(network.read_text().splitlines()), "iterations": iterations, "clock_returns": clocks,
              "controlled_scheduling_delay_ms": delay_ms,
              "mode": mode,
              "clock_info": {k: vars(__import__("time").get_clock_info(k)) for k in ("time", "monotonic")},
              "public_self_contained": True}
    (output / "runner-result.json").write_text(json.dumps(report, indent=2))
    print(stdout + stderr)
    print(json.dumps(report))
    return exit_code or (0 if unchanged and not report["network_attempts"] else 1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True,
                        help="New persistent output directory; captures are never automatically removed")
    parser.add_argument("--iterations", type=int, default=8)
    parser.add_argument("--no-clock-returns", action="store_true")
    parser.add_argument("--build-delay-ms", type=int, default=0)
    parser.add_argument("--mode", choices=("batch", "prefix"), default="batch")
    args = parser.parse_args()
    if not 1 <= args.iterations <= 256:
        parser.error("iterations must be 1..256")
    if not 0 <= args.build_delay_ms <= 40:
        parser.error("build delay must be 0..40 milliseconds")
    raise SystemExit(run(args.output, args.iterations, not args.no_clock_returns, args.build_delay_ms, args.mode))
