"""Self-contained public-only test runner; no private evidence dependency.

Run with the backend interpreter: python tests/run_production_image_persistence.py
Optional --output stores source manifest, actual ASGI fixtures and test results.
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def run(output):
    source = Path(__file__).resolve().parents[1]
    output.mkdir(parents=True, exist_ok=True)
    stage = output / "public-backend"
    stage.mkdir()
    names = set()
    for folder in ("api", "core", "utils", "models", "experiments", "tests", "pipelines", "services", "prompts"):
        for path in (source / folder).rglob("*.py"):
            if not path.is_symlink() and not any(p in {"__pycache__", ".venv", "harness_data", "data", "docs"} for p in path.relative_to(source).parts):
                names.add(path.relative_to(source))
    names.update(p.relative_to(source) for p in source.glob("*.py"))
    for folder in ("schemas", "prompts", "tests/fixtures"):
        for path in (source / folder).rglob("*"):
            if path.is_file() and not path.is_symlink() and path.suffix in {".json", ".txt", ".md"}:
                names.add(path.relative_to(source))
    manifest = {}
    for name in sorted(names):
        target = stage / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / name, target)
        manifest[name.as_posix()] = hashlib.sha256(target.read_bytes()).hexdigest()
    (output / "public-source-manifest.json").write_text(json.dumps(manifest, indent=2))
    guard = output / "guard"
    guard.mkdir()
    shutil.copyfile(stage / "tests/production_image_network_guard.py", guard / "sitecustomize.py")
    external, local = output / "external.jsonl", output / "local.jsonl"
    external.touch()
    local.touch()
    env = {k: v for k, v in os.environ.items() if k in {"PATH", "LANG", "LC_ALL", "SYSTEMROOT", "TMPDIR"}}
    env.update(PYTHONPATH=os.pathsep.join(map(str, (guard, stage / "tests", stage))), PYTHONDONTWRITEBYTECODE="1",
               D48_EXTERNAL_LOG=str(external), D48_LOCAL_LOG=str(local), D48_EVIDENCE=str(output),
               D48_ISOLATED_RUNNER="1", PYTEST_DISABLE_PLUGIN_AUTOLOAD="1")
    command = [sys.executable, "-m", "pytest", "-q", "-s", "tests/production_image_persistence_scenario.py", "--basetemp", str(output / "synthetic-fixtures")]
    result = subprocess.run(command, cwd=stage, env=env, capture_output=True, text=True, timeout=1200)
    (output / "pytest.log").write_text(result.stdout + result.stderr)
    unchanged = all(hashlib.sha256((stage / name).read_bytes()).hexdigest() == sha for name, sha in manifest.items())
    report = {"command": command[1:], "exit_code": result.returncode, "public_files": len(manifest),
              "source_sha256": hashlib.sha256((output / "public-source-manifest.json").read_bytes()).hexdigest(),
              "source_unchanged": unchanged, "external_attempts": len(external.read_text().splitlines()),
              "mapped_local_workers": local.read_text().splitlines().count("mapped_worker"),
              "transport": "actual subprocess worker / explicit local HTTP mapping; no real TLS or provider"}
    (output / "runner-result.json").write_text(json.dumps(report, indent=2))
    print(result.stdout + result.stderr)
    print(json.dumps(report))
    return result.returncode if result.returncode else 0 if unchanged and not report["external_attempts"] else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.output:
        raise SystemExit(run(args.output.resolve()))
    with tempfile.TemporaryDirectory(prefix="leontv-production-image-") as tmp:
        raise SystemExit(run(Path(tmp)))
