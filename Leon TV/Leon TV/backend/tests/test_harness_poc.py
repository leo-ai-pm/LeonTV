"""Real FFmpeg integrations plus explicit simulated crash/tamper boundary cases."""

import copy
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path

import pytest

pytest.importorskip("fcntl", reason="The isolated Harness experiment requires POSIX process/lock APIs")

from experiments.harness_poc.runtime import EXPERIMENT, GOAL, Harness, digest
from experiments.harness_poc.worker import atomic_json, execute, sha


@pytest.fixture
def run_dir():
    parent = EXPERIMENT / ".test-runs"
    parent.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=parent) as directory:
        yield Path(directory)


@pytest.fixture(scope="module")
def real_run():
    parent = EXPERIMENT / ".test-runs"
    parent.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=parent) as directory:
        root = Path(directory)
        with Harness(root) as harness:
            paused = copy.deepcopy(harness.run(pause_after=2))
        with Harness(root, resume=True) as harness:
            final = copy.deepcopy(harness.run())
        yield root, paused, final


def test_real_failure_repair_machine_review_and_pending_human(real_run):
    root, _, state = real_run
    assert state["status"] == "waiting_human_review", state.get("reason")
    assert state["tasks"]["concat-0"]["receipt"]["error"]["type"] == "FileNotFoundError"
    assert state["verdicts"]["0"] == {"passed": False, "failures": ["missing_artifact"]}
    assert state["verdicts"]["1"]["passed"] is True
    assert state["verdicts"]["1"]["facts"] == {
        "frames": 60, "fps": 30.0, "duration_seconds": 2.0, "size": [1920, 1080],
        "sample_colors": ["red", "blue"], "has_audio": False, "full_decode": True,
    }
    assert sha(root / state["artifact"]["path"]) == state["artifact"]["sha256"]
    assert state["budget"]["calls_used"] == 6
    assert state["budget"]["repairs_used"] == 1
    assert state["human_review"] is None


def test_honest_roles_dependencies_and_production_tool_reuse(real_run):
    _, _, state = real_run
    assert state["driver_mode"] == "deterministic_adapters"
    assert state["model_calls"] == state["paid_cost"] == state["external_messages"] == 0
    assert {m["sender"] for m in state["messages"]} == {"planner", "worker", "reviewer"}
    assert all(m["adapter"] == "deterministic" for m in state["messages"])
    repair = next(m for m in state["messages"] if m["kind"] == "repair")
    assert repair["depends_on"] == ["inspect-0"]
    assert repair["data"]["based_on"] == state["verdicts"]["0"]
    assert state["tasks"]["concat-1"]["depends_on"] == ["red", "blue", "inspect-0"]
    assert state["tasks"]["red"]["receipt"]["result"]["function"] == "pipelines.utils.create_static_image_clip"
    assert state["tasks"]["concat-1"]["receipt"]["result"]["function"] == "pipelines.utils.concat_videos"
    assert "pipelines/utils.py" in state["sources"]


def test_pause_resume_and_terminal_resume_never_repeat_tools(real_run):
    root, paused, final = real_run
    assert paused["status"] == "paused"
    assert paused["budget"]["calls_used"] == 2
    for color in GOAL["colors"]:
        assert paused["tasks"][color] == final["tasks"][color]
    before = {p: (p.stat().st_mtime_ns, sha(p)) for p in root.rglob("*") if p.is_file() and p.name != ".lock"}
    with Harness(root, resume=True, max_calls=1) as harness:
        assert harness.run() == final  # Resume uses persisted limits, never resets them.
    assert {p: (p.stat().st_mtime_ns, sha(p)) for p in before} == before


def test_valid_short_video_is_rejected_then_fixed(run_dir):
    with Harness(run_dir, fault="omit_clip") as harness:
        state = harness.run()
    assert state["status"] == "waiting_human_review", state.get("reason")
    assert state["tasks"]["concat-0"]["receipt"]["ok"] is True
    assert state["verdicts"]["0"]["facts"]["frames"] == 30
    assert "duration_2_seconds" in state["verdicts"]["0"]["failures"]
    assert "red_then_blue" in state["verdicts"]["0"]["failures"]


def test_zero_repair_budget_fails_instead_of_success(run_dir):
    with Harness(run_dir, max_repairs=0) as harness:
        state = harness.run()
    assert state["status"] == "failed"
    assert "Repair budget exhausted" in state["reason"]
    assert state["budget"]["calls_used"] == 4
    assert "artifact" not in state


def test_call_budget_consumed_before_dispatch_and_persists(run_dir):
    with Harness(run_dir, max_calls=1) as harness:
        state = copy.deepcopy(harness.run())
    assert state["status"] == "budget_exceeded"
    assert state["budget"]["calls_used"] == 1
    with Harness(run_dir, resume=True) as harness:
        assert harness.run() == state
    state["budget"]["calls_used"] = 0
    atomic_json(run_dir / "state.json", state)
    with pytest.raises(ValueError, match="budget counters"):
        with Harness(run_dir, resume=True):
            pass


@pytest.mark.parametrize("limits", [{"tool_timeout": 0.001}, {"total_seconds": 0.001}])
def test_real_worker_timeout_is_terminal_and_has_no_live_child(run_dir, limits):
    with Harness(run_dir, **limits) as harness:
        state = harness.run()
    assert state["status"] == "failed"
    assert state["reason"] == "tool_timeout"
    assert state["tasks"]["red"]["status"] == "uncertain"
    assert state["budget"]["calls_used"] == 1
    assert state["budget"]["tool_seconds_used"] > 0
    with pytest.raises(ProcessLookupError):
        os.kill(state["tasks"]["red"]["worker_pid"], 0)
    with Harness(run_dir, resume=True) as harness:
        assert harness.run()["budget"]["calls_used"] == 1


def test_orphan_receipt_reconciles_without_duplicate_execution(run_dir):
    with Harness(run_dir) as harness:
        harness.run(pause_after=1)
        state = harness.state
        task = state["tasks"]["red"]
        original_hash = sha(run_dir / "artifacts/red.mp4")
        original_time = (run_dir / "artifacts/red.mp4").stat().st_mtime_ns
        for key in ("receipt", "receipt_sha", "charged_seconds"):
            task.pop(key)
        task["status"] = "running"  # Simulated parent crash after real worker receipt.
        state["budget"]["tool_seconds_used"] = 0
        harness.save()
    with Harness(run_dir, resume=True) as harness:
        result = harness.run(pause_after=0)
    assert result["status"] == "paused"
    assert result["tasks"]["red"]["status"] == "done"
    assert result["budget"]["calls_used"] == 1
    assert sha(run_dir / "artifacts/red.mp4") == original_hash
    assert (run_dir / "artifacts/red.mp4").stat().st_mtime_ns == original_time


def test_uncertain_task_without_receipt_never_auto_reexecutes(run_dir):
    with Harness(run_dir) as harness:
        harness.run(pause_after=1)
        task = harness.state["tasks"]["red"]
        for key in ("receipt", "receipt_sha", "charged_seconds"):
            task.pop(key)
        task["status"] = "running"
        harness.state["budget"]["tool_seconds_used"] = 0
        (run_dir / "calls/red/receipt.json").unlink()
        harness.save()
    with Harness(run_dir, resume=True) as harness:
        result = harness.run()
    assert result["status"] == "interrupted"
    assert result["budget"]["calls_used"] == 1
    assert not (run_dir / "calls/red/receipt.json").exists()


@pytest.mark.parametrize("target", ["artifact", "receipt", "request"])
def test_tampering_fails_closed(run_dir, target):
    with Harness(run_dir) as harness:
        harness.run(pause_after=1)
    relative = {"artifact": "artifacts/red.mp4", "receipt": "calls/red/receipt.json",
                "request": "calls/red/request.json"}[target]
    with (run_dir / relative).open("ab") as stream:
        stream.write(b"tampered")
    with Harness(run_dir, resume=True) as harness:
        result = harness.run()
    assert result["status"] == "failed"
    assert result["budget"]["calls_used"] == 1


@pytest.mark.parametrize("field", ["goal", "sources"])
def test_goal_or_source_mismatch_refuses_resume(run_dir, field):
    with Harness(run_dir) as harness:
        harness.state[field] = {"changed": True}
        harness.save()
    with pytest.raises(ValueError, match="Goal/source changed"):
        with Harness(run_dir, resume=True):
            pass


def test_path_tool_dependency_and_concurrent_run_boundaries(run_dir):
    with pytest.raises(ValueError, match="isolated"):
        Harness(run_dir.parent.parent.parent / "outside")
    with Harness(run_dir) as harness:
        with pytest.raises(BlockingIOError):
            with Harness(run_dir, resume=True):
                pass
        with pytest.raises(ValueError, match="Unknown tool"):
            harness.call("x", "shell", {}, [])
        with pytest.raises(ValueError, match="Dependency"):
            harness.call("x", "inspect", {"input": "x.mp4"}, ["unstarted"])
        with pytest.raises(ValueError, match="outside"):
            execute(run_dir, {"tool": "inspect", "args": {"input": "../outside.mp4"}})
        assert harness.state["budget"]["calls_used"] == 0


@pytest.mark.parametrize("approve", [True, False])
def test_explicit_human_gate_checks_current_artifact(run_dir, real_run, approve):
    root, _, final = real_run
    # Isolated human-adapter fixture; the actual evidence/demo run stays unapproved.
    import shutil

    shutil.copytree(root, run_dir, dirs_exist_ok=True)
    with Harness(run_dir, resume=True) as harness:
        with pytest.raises(ValueError, match="verified artifact hash"):
            harness.human_decision(approve, "wrong-hash", "test-human-fixture")
        state = harness.human_decision(approve, final["artifact"]["sha256"], "test-human-fixture")
        assert state["status"] == ("approved" if approve else "rejected")
        with pytest.raises(ValueError, match="final gate"):
            harness.human_decision(approve, final["artifact"]["sha256"], "test-human-fixture")


def test_human_decision_before_machine_acceptance_is_refused(run_dir):
    with Harness(run_dir) as harness:
        with pytest.raises(ValueError, match="final gate"):
            harness.human_decision(True, digest(GOAL), "test-human-fixture")


def test_unfixable_media_exhausts_one_repair_and_escalates(run_dir, monkeypatch):
    original = Harness.call

    def persistent_fault(self, task_id, tool, args, dependencies, pause_after=None):
        # Controlled test fault: even the repair produces red+red using real FFmpeg.
        if task_id == "concat-1":
            args = {**args, "inputs": ["artifacts/red.mp4", "artifacts/red.mp4"]}
        return original(self, task_id, tool, args, dependencies, pause_after)

    monkeypatch.setattr(Harness, "call", persistent_fault)
    with Harness(run_dir, fault="omit_clip") as harness:
        state = harness.run()
    assert state["status"] == "failed"
    assert "finite repair" in state["reason"]
    assert state["budget"]["calls_used"] == 6
    assert state["budget"]["repairs_used"] == 1
    assert state["verdicts"]["1"]["facts"]["sample_colors"] == ["red", "red"]
    assert "artifact" not in state


def test_inspection_timeout_kills_real_imageio_decoder_group(run_dir, monkeypatch):
    original_popen = subprocess.Popen
    # Test-only fault injection: pause after real imageio metadata, while its real
    # FFmpeg child is alive. No delay hook or alternate worker is added to the PoC.
    script = '''
import json, os, sys, time
from pathlib import Path
import imageio_ffmpeg
from experiments.harness_poc import worker
original = imageio_ffmpeg.read_frames
def slow_reader(path):
    reader = original(path)
    metadata = next(reader)
    child = reader.gi_frame.f_locals['process']
    Path(sys.argv[1], 'decoder-test-marker.json').write_text(json.dumps({
        'pid': child.pid, 'pgid': os.getpgid(child.pid),
        'alive_before_timeout': child.poll() is None,
        'no_prevent_sigint': os.getenv('IMAGEIO_FFMPEG_NO_PREVENT_SIGINT'),
    }))
    yield metadata
    time.sleep(10)
    yield from reader
imageio_ffmpeg.read_frames = slow_reader
worker.main()
'''

    def inject_pause(command, *args, **kwargs):
        if len(command) > 3 and command[2] == "experiments.harness_poc.worker" and command[-1] == "inspect-0":
            command = [command[0], "-c", script, *command[3:]]
        return original_popen(command, *args, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", inject_pause)
    with Harness(run_dir, fault="none", tool_timeout=2) as harness:
        state = harness.run()
    assert state["status"] == "failed", state.get("reason")
    assert state["reason"] == "tool_timeout"
    task = state["tasks"]["inspect-0"]
    marker = json.loads((run_dir / "decoder-test-marker.json").read_text())
    assert marker["alive_before_timeout"] is True
    assert marker["no_prevent_sigint"] == "1"
    assert marker["pgid"] == task["worker_pid"]
    with pytest.raises(ProcessLookupError):
        os.kill(task["worker_pid"], 0)
    deadline = time.monotonic() + 2
    while True:
        child = subprocess.run(["ps", "-o", "stat=", "-p", str(marker["pid"])], capture_output=True, text=True)
        status = child.stdout.strip()
        if not status or status.startswith("Z"):
            break  # Absent or terminated zombie awaiting the OS reaper; cannot execute.
        assert time.monotonic() < deadline, f"Decoder is still alive: {marker} {status}"
        time.sleep(0.01)
    assert state["budget"]["calls_used"] == 4
