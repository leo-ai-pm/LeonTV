"""Formal product runtime with fixture-only actors and real durable control boundaries."""

import copy
import hashlib
import json
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from .store import HarnessError, HarnessStore, unlock_file

GRAPH = {"reference": [], "shot": ["reference"], "final": ["shot"]}
SCOPES = ["fixture_integrity", "fixture_content"]
FIXTURE = Path(__file__).with_name("fixtures") / "test-output.mp4"


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat()


def file_sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def event(run, kind, **data):
    run["revision"] += 1
    run["updated_at"] = now()
    run["events"].append({"sequence": len(run["events"]) + 1, "timestamp": run["updated_at"],
                          "run_id": run["id"], "mode": "test", "type": kind, **data})


class FixtureActors:
    """Only these actors are substituted. They contain no model/network client."""

    def review(self, run, artifact):
        if artifact["id"] == "shot":
            if run["scenario"] == "review_unknown":
                return "unknown"
            if run["scenario"] == "review_rejected" or (run["scenario"] == "repair" and artifact["version"] == 1):
                return "rejected"
        return "accepted"


class HarnessRuntime:
    def __init__(self, root, project_reader=None, actors=None, step_delay=0.02):
        self.store = HarnessStore(root)
        self.project_reader = project_reader
        self.actors = actors or FixtureActors()
        self.step_delay = step_delay
        self.closing = threading.Event()
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="leontv-test-harness")

    def close(self):
        self.closing.set()
        self.executor.shutdown(wait=True, cancel_futures=False)

    @staticmethod
    def require(state, run_id):
        if run_id not in state["runs"]:
            raise HarnessError("NOT_FOUND", "Test run not found", 404)
        return state["runs"][run_id]

    @staticmethod
    def current(run, artifact_id):
        versions = run["artifacts"].get(artifact_id, [])
        return versions[-1] if versions else None

    @staticmethod
    def unresolved(run):
        return {key for key, op in run["operations"].items() if op["status"] in {"submitting", "unknown"}}

    def dependency_signature(self, run, artifact_id):
        result = []
        for dep in GRAPH[artifact_id]:
            artifact = self.current(run, dep)
            if artifact:
                result.append({"artifact_id": dep, "version": artifact["version"], "sha256": artifact["sha256"]})
        return result

    def valid_reviews(self, run, artifact):
        if artifact["dependencies"] != self.dependency_signature(run, artifact["id"]):
            return []
        return [r for r in run["reviews"] if r["valid"] and r["artifact_id"] == artifact["id"]
                and r["artifact_version"] == artifact["version"] and r["artifact_sha256"] == artifact["sha256"]
                and r["rule_version"] == run["rule_version"] and r["dependencies"] == artifact["dependencies"]
                and r["receipt_id"] in run["receipts"]
                and all(run["receipts"][r["receipt_id"]].get(key) == value for key, value in {
                    "kind": "fixture_reviewer", "parsed_status": r["status"],
                    "artifact_id": r["artifact_id"], "artifact_version": r["artifact_version"],
                    "artifact_sha256": r["artifact_sha256"], "dependencies": r["dependencies"],
                    "rule_version": r["rule_version"],
                }.items())
                and (r["status"] == "unknown" or run["receipts"][r["receipt_id"]].get("raw_response") == r["status"])]

    def quality(self, run, artifact):
        if artifact["stale"] or artifact["generation_status"] != "succeeded":
            return "unknown"
        if any(not self.current(run, dep) or self.quality(run, self.current(run, dep)) != "accepted" for dep in GRAPH[artifact["id"]]):
            return "unknown"
        path = self.store.media_path(run["id"], artifact["id"], artifact["version"])
        if not path.is_file() or file_sha(path) != artifact["sha256"]:
            return "unknown"
        reviews = self.valid_reviews(run, artifact)
        if not reviews:
            return "unknown"
        latest = reviews[-1]
        if latest["status"] == "rejected":
            return "rejected"
        if latest["status"] != "accepted" or set(latest["scopes"]) != set(SCOPES):
            return "unknown"
        if not all(latest["scopes"][scope] == "accepted" for scope in SCOPES):
            return "unknown"
        return "accepted"

    def summary(self, run):
        if run["status"] in {"waiting_human_review", "test_accepted", "test_returned"} and run["manifest"]:
            try:
                self.check_review_binding(run, {"manifest_version": run["manifest"]["version"],
                                                "final_sha256": run["manifest"]["final_sha256"]})
            except HarnessError as exc:
                if exc.code == "PRIVATE_STORAGE_UNAVAILABLE":
                    raise
                run["manifest"]["valid"] = False
                if run["final_review"]:
                    run.setdefault("past_final_reviews", []).append(run["final_review"])
                    run["final_review"] = None
                self.block(run, exc.code)
        artifacts = []
        for artifact_id in GRAPH:
            artifact = self.current(run, artifact_id)
            if not artifact:
                continue
            artifacts.append({k: copy.deepcopy(artifact[k]) for k in (
                "id", "version", "kind", "generation_status", "dependencies", "sha256", "stale",
            )} | {"mode": "test", "quality_status": self.quality(run, artifact), "required_scopes": SCOPES,
                  "review_ids": [r["id"] for r in self.valid_reviews(run, artifact)],
                  "media_url": f"/api/harness/runs/{run['id']}/artifacts/{artifact_id}/media?version={artifact['version']}"
                  if artifact["generation_status"] == "succeeded" else None})
        reviews = [{k: copy.deepcopy(r[k]) for k in (
            "id", "artifact_id", "artifact_version", "artifact_sha256", "status", "scopes",
            "rule_version", "valid", "reason", "receipt_id",
        )} | {"mode": "test", "evidence_type": "fixture"} for r in run["reviews"]]
        allowed = ["revise"] if run["status"] in {"test_accepted", "test_returned"} else ["stop", "revise"]
        if run["status"] == "stopped":
            allowed.append("resume")
        if run["status"] == "needs_reconciliation":
            allowed.append("reconcile")
        if run["blocked_reason"] == "STALE_DEPENDENCY":
            allowed.append("resume")
        if self.unresolved(run):
            allowed = ["stop"] if run["status"] in {"queued", "running"} else ["stop", "reconcile"]
        return {k: copy.deepcopy(run[k]) for k in (
            "id", "project_id", "client_request_key", "scenario", "status", "revision", "source",
            "production", "blocked_reason", "manifest", "metrics", "created_at", "updated_at", "final_review",
        )} | {"schema_version": 1, "mode": "test", "test_mode": True, "evidence_type": "fixture",
              "snapshot_sequence": len(run["events"]), "artifacts": artifacts, "reviews": reviews,
              "plan": [{"id": key, "depends_on": deps} for key, deps in GRAPH.items()],
              "budget": {"currency": "CNY", "paid_limit": "0.00", "paid_reserved": "0.00", "paid_actual": "0.00",
                         "fixture_limit": run["fixture_limit"], "fixture_used": run["fixture_used"],
                         "unresolved_operations": sum(op["status"] in {"submitting", "unknown"} for op in run["operations"].values())},
              "allowed_actions": allowed}

    def create(self, payload):
        semantics = {k: v for k, v in payload.items() if k != "client_request_key"}
        request_hash = digest(semantics)
        lock = None
        try:
            with self.store.transaction() as state:
                key = payload["client_request_key"]
                if key in state["keys"]:
                    run = state["runs"][state["keys"][key]]
                    if run["semantic_hash"] != request_hash:
                        raise HarnessError("KEY_CONFLICT", "Request key already belongs to different inputs")
                    return {"run": self.summary(run), "replayed": True}
                project_id = payload["project_id"]
                if project_id and (not self.project_reader or not self.project_reader(project_id)):
                    raise HarnessError("NOT_FOUND", "Studio project not found", 404)
                run_id = uuid.uuid4().hex
                lock = self.store.execution_lock(run_id)
                run = {"id": run_id, **payload, "semantic_hash": request_hash, "status": "queued",
                       "revision": 0, "events": [], "artifacts": {}, "reviews": [], "receipts": {},
                       "operations": {}, "action_keys": {}, "review_keys": {}, "manifest": None,
                       "manifest_counter": 0, "final_review": None, "rule_version": "fixture-v1",
                       "fixture_limit": 0 if payload["scenario"] == "zero_budget" else 8, "fixture_used": 0,
                       "blocked_reason": None, "planned": False, "remote_resolved": False, "checkpoint_done": False,
                       "created_at": now(), "updated_at": now(),
                       "metrics": {"model_http_calls": 0, "paid_calls": 0, "supplier_submissions": 0,
                                   "planner_calls": 0, "review_calls": 0, "query_calls": 0, "repair_count": 0,
                                   "intervention_count": 0, "final_decision_count": 0}}
                event(run, "run_created", reason="TEST_MODE_ONLY")
                state["runs"][run_id] = run
                state["keys"][key] = run_id
                if project_id:
                    state["project_runs"].setdefault(project_id, []).append(run_id)
                result = {"run": self.summary(run), "replayed": False}
            self.executor.submit(self._drive, run_id, lock)
            lock = None
            return result
        finally:
            if lock:
                unlock_file(lock)

    def recover(self, run):
        if run["status"] not in {"running", "queued"}:
            return
        probe = self.store.execution_lock(run["id"])
        if not probe:
            return
        try:
            uncertain = any(op["status"] in {"submitting", "unknown"} for op in run["operations"].values())
            run["status"] = "needs_reconciliation" if uncertain else "stopped"
            run["blocked_reason"] = "REMOTE_OUTCOME_UNKNOWN" if uncertain else "RECOVERABLE_INTERRUPTION"
            event(run, "run_recovered", reason=run["blocked_reason"])
        finally:
            unlock_file(probe)

    def get(self, run_id=None, key=None):
        with self.store.transaction() as state:
            if key is not None:
                run_id = state["keys"].get(key)
            run = self.require(state, run_id)
            self.recover(run)
            return {"run": self.summary(run)}

    def list(self, project_id=None):
        with self.store.transaction() as state:
            ids = state["project_runs"].get(project_id, []) if project_id else state["runs"]
            runs = [self.summary(state["runs"][run_id]) for run_id in ids]
            return {"runs": sorted(runs, key=lambda r: r["created_at"], reverse=True)}

    def events(self, run_id, after, limit):
        with self.store.transaction() as state:
            run = self.require(state, run_id)
            events = copy.deepcopy(run["events"][after:after + limit])
            cursor = events[-1]["sequence"] if events else after
            return {"mode": "test", "events": events, "next_sequence": cursor, "has_more": cursor < len(run["events"])}

    @staticmethod
    def block(run, reason, remote=False):
        run["status"] = "needs_reconciliation" if remote else "blocked"
        run["blocked_reason"] = reason
        event(run, "run_blocked", reason=reason)

    def deliver(self, run, artifact):
        path = self.store.media_path(run["id"], artifact["id"], artifact["version"])
        path.parent.mkdir(parents=True, exist_ok=True)
        content = FIXTURE.read_bytes()
        if not path.exists():
            path.write_bytes(content)
        if path.read_bytes() != content:
            raise HarnessError("STALE_DEPENDENCY", "Fixture bytes changed")
        artifact.update(generation_status="succeeded", sha256=hashlib.sha256(content).hexdigest(), stale=False)
        op = run["operations"][artifact["operation_id"]]
        op.update(status="succeeded", receipt_id=f"receipt-{uuid.uuid4().hex}")
        run["receipts"][op["receipt_id"]] = {"kind": "fixture_supplier", "sha256": artifact["sha256"]}
        event(run, "tool_completed", operation_id=artifact["operation_id"], tool_call_id=op["tool_call_id"],
              artifact_id=artifact["id"], version=artifact["version"], dependencies=artifact["dependencies"],
              generation_status="succeeded", receipt_id=op["receipt_id"], amount_cny="0.00")

    def invalidate(self, run, artifact_id):
        affected = {artifact_id}
        for candidate, deps in GRAPH.items():
            if any(dep in affected for dep in deps):
                affected.add(candidate)
        for target in affected:
            artifact = self.current(run, target)
            if artifact:
                artifact["stale"] = True
            for review in run["reviews"]:
                if review["artifact_id"] == target:
                    review["valid"] = False
        if run["manifest"]:
            run["manifest"]["valid"] = False
        if run["final_review"]:
            run.setdefault("past_final_reviews", []).append(run["final_review"])
            run["final_review"] = None
        event(run, "artifacts_invalidated", artifact_id=artifact_id, reason="STALE_DEPENDENCY")

    def _tick(self, run):
        if any(op["status"] == "unknown" for op in run["operations"].values()):
            self.block(run, "REMOTE_OUTCOME_UNKNOWN", remote=True)
            return False
        if not run["planned"]:
            run["planned"] = True
            run["metrics"]["planner_calls"] += 1
            event(run, "plan_created", reason="DETERMINISTIC_FIXTURE_PLAN")
            return True
        for artifact_id in GRAPH:
            artifact = self.current(run, artifact_id)
            if not artifact or artifact["stale"]:
                if self.unresolved(run):
                    self.block(run, "REMOTE_OUTCOME_UNKNOWN", remote=True)
                    return False
                if run["fixture_used"] >= run["fixture_limit"]:
                    self.block(run, "BUDGET_EXHAUSTED")
                    return False
                # Dependencies must pass before reserving any supplier operation.
                if any(not self.current(run, dep) or self.quality(run, self.current(run, dep)) != "accepted" for dep in GRAPH[artifact_id]):
                    self.block(run, "QUALITY_UNKNOWN")
                    return False
                version = artifact["version"] + 1 if artifact else 1
                artifact = {"id": artifact_id, "version": version, "kind": "test_fixture_video",
                            "generation_status": "submitting", "sha256": None, "stale": False,
                            "dependencies": self.dependency_signature(run, artifact_id),
                            "operation_id": f"{artifact_id}-v{version}"}
                run["artifacts"].setdefault(artifact_id, []).append(artifact)
                run["fixture_used"] += 1
                run["metrics"]["supplier_submissions"] += 1
                run["operations"][artifact["operation_id"]] = {"status": "submitting", "tool_call_id": uuid.uuid4().hex}
                event(run, "tool_intent", operation_id=artifact["operation_id"],
                      tool_call_id=run["operations"][artifact["operation_id"]]["tool_call_id"],
                      artifact_id=artifact_id, version=version, dependencies=artifact["dependencies"],
                      generation_status="submitting", amount_cny="0.00", usage={"fixture_calls": 1, "model_http_calls": 0})
                return True  # Intent and budget commit before fixture dispatch on the next tick.
            if artifact["generation_status"] == "submitting":
                if self.unresolved(run) - {artifact["operation_id"]}:
                    self.block(run, "REMOTE_OUTCOME_UNKNOWN", remote=True)
                    return False
                if run["scenario"] == "unknown_acceptance" and artifact_id == "reference" and not run["remote_resolved"]:
                    artifact["generation_status"] = "unknown"
                    run["operations"][artifact["operation_id"]]["status"] = "unknown"
                    self.block(run, "REMOTE_OUTCOME_UNKNOWN", remote=True)
                    return False
                self.deliver(run, artifact)
                return True
            if artifact["generation_status"] == "unknown":
                self.block(run, "REMOTE_OUTCOME_UNKNOWN", remote=True)
                return False
            reviews = self.valid_reviews(run, artifact)
            if not reviews:
                if run["metrics"]["review_calls"] >= 12:
                    self.block(run, "BUDGET_EXHAUSTED")
                    return False
                run["metrics"]["review_calls"] += 1
                try:
                    raw_verdict = self.actors.review(run, copy.deepcopy(artifact))
                except Exception as exc:
                    raw_verdict = {"fixture_error_type": type(exc).__name__}
                verdict = raw_verdict if isinstance(raw_verdict, str) and raw_verdict in {"accepted", "rejected", "unknown"} else "unknown"
                receipt_id = f"judge-{uuid.uuid4().hex}"
                run["receipts"][receipt_id] = {
                    "kind": "fixture_reviewer", "parsed_status": verdict, "raw_response": raw_verdict,
                    "artifact_id": artifact_id, "artifact_version": artifact["version"],
                    "artifact_sha256": artifact["sha256"], "dependencies": artifact["dependencies"],
                    "rule_version": run["rule_version"],
                }
                review = {"id": uuid.uuid4().hex, "artifact_id": artifact_id, "artifact_version": artifact["version"],
                          "artifact_sha256": artifact["sha256"], "dependencies": artifact["dependencies"],
                          "status": verdict, "scopes": {scope: verdict for scope in SCOPES},
                          "rule_version": run["rule_version"], "valid": True, "reason": "FIXTURE_JUDGE",
                          "receipt_id": receipt_id}
                run["reviews"].append(review)
                event(run, "review_completed", artifact_id=artifact_id, version=artifact["version"],
                      dependencies=artifact["dependencies"], quality_status=verdict, judge_id=review["id"],
                      receipt_id=receipt_id, amount_cny="0.00", usage={"fixture_review_calls": 1, "model_http_calls": 0})
                return True
            quality = self.quality(run, artifact)
            if quality != "accepted":
                if quality == "rejected" and run["scenario"] == "repair" and artifact_id == "shot" and run["metrics"]["repair_count"] == 0:
                    run["metrics"]["repair_count"] += 1
                    self.invalidate(run, artifact_id)
                    event(run, "repair_requested", artifact_id=artifact_id, reason="FIXTURE_LOCAL_REPAIR")
                    return True
                self.block(run, "QUALITY_REJECTED" if quality == "rejected" else "QUALITY_UNKNOWN")
                return False
            if run["scenario"] == "resume_checkpoint" and artifact_id == "reference" and not run["checkpoint_done"]:
                run["checkpoint_done"] = True
                run["status"], run["blocked_reason"] = "stopped", "TEST_CHECKPOINT"
                event(run, "run_stopped", reason="TEST_CHECKPOINT")
                return False
        final = self.current(run, "final")
        binding = {"final_artifact_id": "final", "final_version": final["version"], "final_sha256": final["sha256"],
                   "review_bundle_hash": digest([r for r in run["reviews"] if r["valid"]]),
                   "dependency_hash": digest([self.current(run, key) for key in GRAPH])}
        old = run["manifest"]
        if not old or not old["valid"] or any(old.get(k) != v for k, v in binding.items()):
            run["manifest_counter"] += 1
            run["manifest"] = {"version": run["manifest_counter"], **binding, "valid": True}
        run["status"], run["blocked_reason"] = "waiting_human_review", None
        event(run, "waiting_human_review", reason="TEST_FIXTURE_ONLY")
        return False

    def _drive(self, run_id, lock):
        try:
            for _ in range(60):
                time.sleep(self.step_delay)
                with self.store.transaction() as state:
                    run = self.require(state, run_id)
                    if run["status"] not in {"queued", "running"}:
                        return
                    if self.closing.is_set():
                        run["status"], run["blocked_reason"] = "stopped", "RECOVERABLE_INTERRUPTION"
                        event(run, "run_stopped", reason="RECOVERABLE_INTERRUPTION")
                        return
                    run["status"] = "running"
                    if not self._tick(run):
                        return
            raise RuntimeError("Fixture step limit exhausted")
        except Exception:
            with self.store.transaction() as state:
                run = self.require(state, run_id)
                run["status"], run["blocked_reason"] = "failed", "INTERNAL_EXECUTION_FAILURE"
                event(run, "run_failed", reason="INTERNAL_EXECUTION_FAILURE")
        finally:
            unlock_file(lock)

    def action(self, run_id, payload):
        lock = None
        try:
            with self.store.transaction() as state:
                run = self.require(state, run_id)
                self.recover(run)
                key, request_hash = payload["client_action_key"], digest(payload)
                if key in run["action_keys"]:
                    if run["action_keys"][key] != request_hash:
                        raise HarnessError("KEY_CONFLICT", "Action key already used with different inputs")
                    return {"run": self.summary(run), "replayed": True}
                if run["revision"] != payload["expected_revision"]:
                    raise HarnessError("VERSION_CONFLICT", "Refresh the current test run")
                action = payload["action"]
                if self.unresolved(run) and action in {"resume", "revise"}:
                    raise HarnessError("REMOTE_OUTCOME_UNKNOWN", "Reconcile every original operation before new work")
                if run["status"] in {"test_accepted", "test_returned"} and action != "revise":
                    raise HarnessError("RUN_ACTIVE", "Final test decision requires a new version to continue")
                if action == "stop":
                    run["status"] = "stopped"
                    event(run, "run_stopped", reason="TEST_OPERATOR_STOP")
                elif action == "revise":
                    target = payload.get("artifact_id")
                    if target not in {"reference", "shot"} or not self.current(run, target):
                        raise HarnessError("NOT_FOUND", "Test artifact not found", 404)
                    self.invalidate(run, target)
                    self.block(run, "STALE_DEPENDENCY")
                else:
                    if run["status"] in {"queued", "running", "waiting_human_review", "test_accepted", "test_returned"}:
                        raise HarnessError("RUN_ACTIVE", "No resumable test work at this state")
                    if run["blocked_reason"] in {"QUALITY_UNKNOWN", "QUALITY_REJECTED", "BUDGET_EXHAUSTED"}:
                        raise HarnessError(run["blocked_reason"], "Test gate blocks dependent work")
                    if run["status"] == "needs_reconciliation" and action != "reconcile":
                        raise HarnessError("REMOTE_OUTCOME_UNKNOWN", "Reconcile the original fixture operation")
                    lock = self.store.execution_lock(run_id)
                    if not lock:
                        raise HarnessError("RUN_ACTIVE", "Test run is already executing")
                    if action == "reconcile":
                        uncertain = [a[-1] for a in run["artifacts"].values() if a[-1]["generation_status"] in {"submitting", "unknown"}]
                        if not uncertain:
                            raise HarnessError("REMOTE_OUTCOME_UNKNOWN", "No uncertain fixture operation to reconcile")
                        run["remote_resolved"] = True
                        for artifact in uncertain:
                            run["metrics"]["query_calls"] += 1
                            self.deliver(run, artifact)  # Existing operation, no supplier reservation/submission.
                        event(run, "operation_reconciled", reason="EXISTING_FIXTURE_RECEIPT")
                    run["status"], run["blocked_reason"] = "queued", None
                    event(run, "run_resumed", reason="TEST_OPERATOR_RESUME")
                run["metrics"]["intervention_count"] += 1
                run["action_keys"][key] = request_hash
                result = {"run": self.summary(run), "replayed": False}
            if lock:
                self.executor.submit(self._drive, run_id, lock)
                lock = None
            return result
        finally:
            if lock:
                unlock_file(lock)

    def check_review_binding(self, run, payload):
        manifest = run["manifest"]
        if not manifest or not manifest["valid"] or manifest["version"] != payload["manifest_version"] or manifest["final_sha256"] != payload["final_sha256"]:
            raise HarnessError("VERSION_CONFLICT", "Final fixture version changed")
        if manifest["review_bundle_hash"] != digest([r for r in run["reviews"] if r["valid"]]):
            raise HarnessError("STALE_DEPENDENCY", "Fixture review evidence changed")
        if manifest["dependency_hash"] != digest([self.current(run, key) for key in GRAPH]):
            raise HarnessError("STALE_DEPENDENCY", "Fixture dependencies changed")
        for key in GRAPH:
            artifact = self.current(run, key)
            if not artifact or self.quality(run, artifact) != "accepted":
                raise HarnessError("QUALITY_UNKNOWN", "Current fixture evidence is not accepted")
            path = self.store.media_path(run["id"], key, artifact["version"])
            if not path.is_file() or file_sha(path) != artifact["sha256"]:
                raise HarnessError("STALE_DEPENDENCY", "Fixture file no longer matches evidence")

    def review(self, run_id, payload):
        with self.store.transaction() as state:
            run = self.require(state, run_id)
            key, request_hash = payload["client_review_key"], digest(payload)
            previous = run["review_keys"].get(key)
            if previous:
                if previous["hash"] != request_hash:
                    raise HarnessError("KEY_CONFLICT", "Review key cannot change its decision")
                if previous["written_revision"] != run["revision"]:
                    raise HarnessError("VERSION_CONFLICT", "Old fixture decision is no longer current")
                self.check_review_binding(run, payload)
                return {"run": self.summary(run), "replayed": True}
            if run["status"] != "waiting_human_review" or run["revision"] != payload["expected_revision"]:
                raise HarnessError("VERSION_CONFLICT", "Refresh the current reviewable test run")
            self.check_review_binding(run, payload)
            decision = {"id": uuid.uuid4().hex, "mode": "test", "test_mode": True,
                        "actor": "test_operator", "decision": f"test_{payload['decision']}", "recorded_at": now(),
                        "manifest_version": payload["manifest_version"], "final_sha256": payload["final_sha256"],
                        "feedback_recorded": bool(payload["feedback"])}
            run["private_feedback"] = payload["feedback"]
            run["final_review"] = decision
            run["status"] = "test_accepted" if payload["decision"] == "accept" else "test_returned"
            run["metrics"]["final_decision_count"] += 1
            event(run, run["status"], reason="TEST_OPERATOR_ONLY")
            run["review_keys"][key] = {"hash": request_hash, "written_revision": run["revision"]}
            return {"run": self.summary(run), "replayed": False}

    def media(self, run_id, artifact_id, version):
        with self.store.transaction() as state:
            run = self.require(state, run_id)
            artifact = next((a for a in run["artifacts"].get(artifact_id, []) if a["version"] == version), None)
            if not artifact or artifact["generation_status"] != "succeeded":
                raise HarnessError("NOT_FOUND", "Test media not found", 404)
            path = self.store.media_path(run_id, artifact_id, version)
            if not path.is_file() or file_sha(path) != artifact["sha256"]:
                raise HarnessError("STALE_DEPENDENCY", "Test media no longer matches its receipt")
            return path
