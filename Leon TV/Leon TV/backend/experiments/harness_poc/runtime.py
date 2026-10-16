"""Deterministic role coordination, real local media tools and a manual final gate."""

import fcntl
import hashlib
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from path_security import contained_storage_path

from .worker import atomic_json, sha

BACKEND = Path(__file__).resolve().parents[2]
EXPERIMENT = Path(__file__).resolve().parent
EVIDENCE = BACKEND.parents[2] / "docs/evidence/LTV-HARNESS-20261015/backend"
GOAL = {"seconds": 2, "colors": ["red", "blue"], "ratio": "16:9", "audio": False, "synthetic": True}
TERMINAL = {"waiting_human_review", "approved", "rejected", "failed", "budget_exceeded", "interrupted"}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def sources():
    return {str(p.relative_to(BACKEND)): sha(p) for p in [
        EXPERIMENT / "runtime.py", EXPERIMENT / "worker.py", BACKEND / "media_tools.py",
        BACKEND / "path_security.py", BACKEND / "pipelines/utils.py",
    ]}


class Halt(Exception):
    def __init__(self, status, reason):
        self.status, self.reason = status, reason


class Harness:
    def __init__(self, root, *, resume=False, fault="missing_clip", max_calls=6,
                 max_repairs=1, tool_timeout=30, total_seconds=120):
        self.root = Path(root).resolve()
        allowed = [EXPERIMENT / "runs", EXPERIMENT / ".test-runs", EVIDENCE]
        if not any(self.root != p.resolve() and self.root.is_relative_to(p.resolve()) for p in allowed):
            raise ValueError("Run directory must be inside the isolated experiment/evidence roots")
        self.resume, self.fault = resume, fault
        self.limits = {"max_calls": max_calls, "max_repairs": max_repairs,
                       "tool_timeout": tool_timeout, "total_tool_seconds": total_seconds}
        if fault not in {"missing_clip", "omit_clip", "none"}:
            raise ValueError("Unsupported deterministic fault")
        if not (1 <= max_calls <= 6 and 0 <= max_repairs <= 1 and
                0 < tool_timeout <= 30 and 0 < total_seconds <= 120):
            raise ValueError("Limits exceed this experiment's bounded scope")
        self.dispatched = 0

    def __enter__(self):
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = (self.root / ".lock").open("a+")
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            path = self.root / "state.json"
            if self.resume:
                self.state = json.loads(path.read_text())
                state = self.state
                if state["goal"] != GOAL or state["goal_sha"] != digest(GOAL) or state["sources"] != sources():
                    raise ValueError("Goal/source changed; create a new run")
                if state["budget"]["calls_used"] != len(state["tasks"]) or state["budget"]["repairs_used"] != sum(
                    m["kind"] == "repair" for m in state["messages"]
                ):
                    raise ValueError("Persisted budget counters are inconsistent")
                charged = sum(t.get("charged_seconds", 0) for t in state["tasks"].values())
                if abs(charged - state["budget"]["tool_seconds_used"]) > 0.001:
                    raise ValueError("Persisted runtime budget is inconsistent")
            else:
                if path.exists():
                    raise ValueError("Run already exists; use resume")
                self.state = {
                    "schema_version": 1, "driver_mode": "deterministic_adapters", "goal": GOAL,
                    "goal_sha": digest(GOAL), "sources": sources(), "fault": self.fault,
                    "status": "planned", "limits": self.limits, "tasks": {}, "messages": [],
                    "budget": {"calls_used": 0, "repairs_used": 0, "tool_seconds_used": 0},
                    "model_calls": 0, "paid_cost": 0, "external_messages": 0, "human_review": None,
                }
                self.save()
            return self
        except BaseException:
            self.lock.close()
            raise

    def __exit__(self, *exc):
        self.lock.close()

    def save(self):
        atomic_json(self.root / "state.json", self.state)

    def message(self, message_id, sender, recipient, kind, depends_on, data):
        if not any(m["id"] == message_id for m in self.state["messages"]):
            self.state["messages"].append({
                "id": message_id, "sender": sender, "recipient": recipient, "kind": kind,
                "adapter": "deterministic", "depends_on": depends_on, "data": data,
            })
            self.save()

    def path(self, relative):
        return contained_storage_path(self.root, relative)

    def validate_receipt(self, task):
        receipt_path = self.path(f"calls/{task['id']}/receipt.json")
        if sha(receipt_path.with_name("request.json")) != task["request_sha"]:
            raise ValueError("Reserved request changed")
        if not receipt_path.is_file():
            raise ValueError("Missing receipt; execution is uncertain")
        if task.get("receipt_sha") and task["receipt_sha"] != sha(receipt_path):
            raise ValueError("Receipt changed")
        receipt = json.loads(receipt_path.read_text())
        if receipt["request_sha"] != task["request_sha"]:
            raise ValueError("Receipt does not match reserved request")
        for output in receipt["outputs"]:
            path = self.path(output["path"])
            if not path.is_file() or sha(path) != output["sha256"]:
                raise ValueError("Artifact changed or missing")
        return receipt

    def finish(self, task, elapsed=None):
        receipt = self.validate_receipt(task)
        task.update(status="done" if receipt["ok"] else "failed", receipt=receipt,
                    receipt_sha=sha(self.path(f"calls/{task['id']}/receipt.json")),
                    charged_seconds=elapsed if elapsed is not None else receipt["duration_seconds"])
        self.state["budget"]["tool_seconds_used"] = sum(t.get("charged_seconds", 0) for t in self.state["tasks"].values())
        self.message(f"worker:{task['id']}", "worker", "reviewer", "tool_result", task["depends_on"],
                     {"task_id": task["id"], "ok": receipt["ok"], "receipt_sha": task["receipt_sha"]})
        self.save()
        return receipt

    def call(self, task_id, tool, args, dependencies, pause_after=None):
        if tool not in {"create_clip", "concat", "inspect"}:
            raise ValueError("Unknown tool")
        for dep in dependencies:
            if dep not in self.state["tasks"] or self.state["tasks"][dep]["status"] not in {"done", "failed"}:
                raise ValueError("Dependency is not terminal")
            if self.state["tasks"][dep]["status"] == "failed" and tool != "inspect":
                raise ValueError("Required dependency failed")
        inputs = args.get("inputs", [args["input"]] if "input" in args else [])
        input_hashes = {p: sha(self.path(p)) if self.path(p).is_file() else "MISSING" for p in inputs}
        for key in ("input", "output", "image"):
            if key in args:
                self.path(args[key])
        request = {"tool": tool, "args": args, "input_hashes": input_hashes,
                   "goal_sha": self.state["goal_sha"], "sources": self.state["sources"]}
        tasks, budget, limits = self.state["tasks"], self.state["budget"], self.state["limits"]
        if task_id in tasks:
            task = tasks[task_id]
            if task["request"] != request or task["depends_on"] != dependencies:
                raise ValueError("Task inputs changed; refusing duplicate execution")
            if task["status"] == "running" and not self.path(f"calls/{task_id}/receipt.json").is_file():
                task["charged_seconds"] = task["timeout"]
                budget["tool_seconds_used"] = sum(t.get("charged_seconds", 0) for t in tasks.values())
                raise Halt("interrupted", "In-flight task has no receipt; inspect manually, never auto-retry")
            return self.finish(task) if task["status"] == "running" else self.validate_receipt(task)
        if pause_after is not None and self.dispatched >= pause_after:
            raise Halt("paused", "Requested checkpoint reached")
        remaining = limits["total_tool_seconds"] - budget["tool_seconds_used"]
        if budget["calls_used"] >= limits["max_calls"] or remaining <= 0:
            raise Halt("budget_exceeded", "Tool call/runtime budget exhausted")
        call_dir = self.path(f"calls/{task_id}")
        call_dir.mkdir(parents=True, exist_ok=True)
        atomic_json(call_dir / "request.json", request)
        task = {"id": task_id, "request": request, "request_sha": sha(call_dir / "request.json"),
                "depends_on": dependencies, "status": "running", "timeout": min(limits["tool_timeout"], remaining)}
        tasks[task_id] = task
        budget["calls_used"] += 1
        self.state["status"] = "running"
        self.save()  # Reserve the call before dispatch; failed/interrupted calls consume budget.
        self.dispatched += 1
        started = time.monotonic()
        with (call_dir / "worker.log").open("w") as log:
            process = subprocess.Popen(
                [sys.executable, "-m", "experiments.harness_poc.worker", str(self.root), task_id],
                cwd=BACKEND, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1",
                                  "IMAGEIO_FFMPEG_NO_PREVENT_SIGINT": "1"},
                stdout=log, stderr=subprocess.STDOUT, start_new_session=True, pass_fds=(self.lock.fileno(),),
            )
            task["worker_pid"] = process.pid  # Evidence only; resume never signals a stored PID.
            self.save()
            try:
                process.wait(timeout=task["timeout"])
            except BaseException as exc:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
                task.update(status="uncertain", charged_seconds=time.monotonic() - started)
                budget["tool_seconds_used"] = sum(t.get("charged_seconds", 0) for t in tasks.values())
                raise Halt("failed", "tool_timeout" if isinstance(exc, subprocess.TimeoutExpired) else "tool_interrupted") from exc
        if not (call_dir / "receipt.json").is_file():
            task.update(status="uncertain", charged_seconds=time.monotonic() - started)
            budget["tool_seconds_used"] = sum(t.get("charged_seconds", 0) for t in tasks.values())
            raise Halt("interrupted", "Worker exited without receipt; inspect manually")
        return self.finish(task, time.monotonic() - started)

    def run(self, pause_after=None):
        try:
            # Validate completed work even when resuming a terminal run.
            for task in self.state["tasks"].values():
                if task["status"] in {"done", "failed"}:
                    self.validate_receipt(task)
            if self.state["status"] in TERMINAL:
                return self.state
            self.message("plan", "planner", "worker", "plan", [], {"goal": GOAL, "tool_graph": [
                "red + blue -> concat-0 -> inspect-0 -> repair? -> concat-1 -> inspect-1 -> human",
            ]})
            for color in GOAL["colors"]:
                self.call(color, "create_clip", {"color": color, "image": f"artifacts/{color}.png",
                          "output": f"artifacts/{color}.mp4"}, [], pause_after)
            for attempt in range(2):
                if attempt and self.state["budget"]["repairs_used"] == 0:
                    if self.state["limits"]["max_repairs"] == 0:
                        raise Halt("failed", "Repair budget exhausted; human escalation required")
                    self.state["budget"]["repairs_used"] += 1
                    self.message("repair", "reviewer", "worker", "repair", ["inspect-0"], {
                        "change": "Use both verified red and blue clips", "based_on": self.state["verdicts"]["0"],
                    })
                clips = ["artifacts/red.mp4", "artifacts/blue.mp4"]
                if attempt == 0 and self.state["fault"] == "missing_clip":
                    clips[1] = "artifacts/intentionally-missing.mp4"
                elif attempt == 0 and self.state["fault"] == "omit_clip":
                    clips = clips[:1]
                concat, review = f"concat-{attempt}", f"inspect-{attempt}"
                output = f"artifacts/attempt-{attempt}/final.mp4"
                dependencies = ["red", "blue"] + (["inspect-0"] if attempt else [])
                self.call(concat, "concat", {"inputs": clips, "output": output}, dependencies, pause_after)
                receipt = self.call(review, "inspect", {"input": output}, [concat], pause_after)
                verdict = receipt.get("result", {"passed": False, "failures": [receipt.get("error")]})
                self.state.setdefault("verdicts", {})[str(attempt)] = verdict
                self.message(f"review:{attempt}", "reviewer", "planner", "verdict", [review], verdict)
                if verdict["passed"]:
                    self.state.update(status="waiting_human_review", artifact={"path": output, "sha256": sha(self.path(output))})
                    self.save()
                    return self.state
            raise Halt("failed", "Machine review failed after finite repair; human escalation required")
        except Halt as exc:
            self.state.update(status=exc.status, reason=exc.reason)
        except Exception as exc:
            self.state.update(status="failed", reason=f"{type(exc).__name__}: {exc}")
        self.save()
        return self.state

    def human_decision(self, approve, artifact_sha, reviewer):
        if self.state["status"] != "waiting_human_review" or not reviewer.strip():
            raise ValueError("A named human review is required at the final gate")
        artifact = self.state["artifact"]
        if artifact_sha != artifact["sha256"] or sha(self.path(artifact["path"])) != artifact_sha:
            raise ValueError("Human decision must refer to the verified artifact hash")
        self.state.update(status="approved" if approve else "rejected", human_review={
            "reviewer": reviewer, "artifact_sha": artifact_sha, "approved": approve,
        })
        self.save()
        return self.state
