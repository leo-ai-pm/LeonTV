"""Private cross-process append-only logical task journal, rejecting damaged data."""

import copy
import hashlib
import json
import os
import tempfile
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from core.creator.store import (
    ID_RE,
    KEY_RE,
    CreatorBriefStore,
    CreatorError,
    digest,
    source_hash,
    utc_now,
    valid_content,
)
from core.harness.store import lock_file, unlock_file
from core.planner.protocol import (
    LIMITS,
    REVIEW_SCENARIOS,
    REVISION_SCENARIOS,
    SCENARIOS,
    STATUSES,
    checked_plan,
    parse_json,
)


class PlannerError(CreatorError):
    pass


def require(condition):
    if not condition:
        raise PlannerError("CORRUPT_STORAGE", "Planner storage failed integrity checks")


SOURCE_FIELDS = {"brief_id", "brief_revision", "source_version", "source_hash", "snapshot_hash", "title", "script", "requirements", "production", "budget_cny", "currency", "frozen_at"}
PAYLOAD_FIELDS = {"status", "blocked_reason", "step_count", "repair_count", "pending", "messages", "operations", "read_source", "plans", "events", "actions"}


def _json_tree(value):
    if type(value) is dict:
        return all(type(key) is str and _json_tree(item) for key, item in value.items())
    if type(value) is list:
        return all(_json_tree(item) for item in value)
    return value is None or type(value) in {str, int, float, bool}


def _state_fingerprint(state):
    try:
        require(_json_tree(state))
        wire = json.dumps(state, sort_keys=True, ensure_ascii=False, allow_nan=False).encode("utf-8")
        return hashlib.sha256(wire).hexdigest()
    except (ValueError, TypeError, OverflowError, UnicodeError, RecursionError):
        raise PlannerError("CORRUPT_STORAGE", "Planner storage failed integrity checks") from None


def check_source(source):
    require(isinstance(source, dict) and set(source) == SOURCE_FIELDS)
    valid_content({k: source[k] for k in ["title", "script", "requirements", "production", "budget_cny"]})
    require(bool(ID_RE.fullmatch(source["brief_id"])) and type(source["brief_revision"]) is int and source["brief_revision"] >= 1)
    require(type(source["source_version"]) is int and source["source_version"] >= 1 and source["currency"] == "CNY")
    require(source["source_hash"] == source_hash(source["script"]))
    require(datetime.fromisoformat(source["frozen_at"]).tzinfo is not None)
    require(source["snapshot_hash"] == digest({k: v for k, v in source.items() if k != "snapshot_hash"}))


def frame_hash(document, frame):
    return digest({"id": document["id"], "client_request_key": document["client_request_key"], "created_at": document["created_at"],
                   "request": document["request"], "source": document["source"], **{k: v for k, v in frame.items() if k != "hash"}})


def validate_state(state):
    require(isinstance(state, dict) and set(state) == {"schema_version", "keys", "tasks"} and type(state["schema_version"]) is int and state["schema_version"] == 1)
    require(isinstance(state["keys"], dict) and isinstance(state["tasks"], dict) and len(state["keys"]) == len(state["tasks"]))
    for task_id, document in state["tasks"].items():
        require(bool(ID_RE.fullmatch(task_id)) and set(document) == {"id", "client_request_key", "created_at", "request", "source", "versions", "head_revision", "head_hash"})
        require(document["id"] == task_id and bool(KEY_RE.fullmatch(document["client_request_key"])))
        require(datetime.fromisoformat(document["created_at"]).tzinfo is not None)
        request, source = document["request"], document["source"]
        require(set(request) == {"mode", "scenario", "brief_id", "brief_revision"})
        require(request["mode"] == "normal" and request["scenario"] is None or request["mode"] == "offline_test" and request["scenario"] in SCENARIOS)
        check_source(source)
        require(request["brief_id"] == source["brief_id"] and type(request["brief_revision"]) is int and request["brief_revision"] == source["brief_revision"])
        require(state["keys"].get(document["client_request_key"]) == {"task_id": task_id, "request_hash": digest({"client_request_key": document["client_request_key"], **request})})
        require(isinstance(document["versions"], list) and bool(document["versions"]))
        require(type(document["head_revision"]) is int and document["head_revision"] == len(document["versions"]))
        require(document["head_hash"] == document["versions"][-1]["hash"])
        previous = None
        for number, frame in enumerate(document["versions"], 1):
            require(set(frame) == {"revision", "saved_at", "payload", "previous_hash", "hash"})
            require(type(frame["revision"]) is int and frame["revision"] == number and datetime.fromisoformat(frame["saved_at"]).tzinfo is not None)
            require(frame["previous_hash"] == (previous["hash"] if previous else None) and frame["hash"] == frame_hash(document, frame))
            payload = frame["payload"]
            collaboration = request["mode"] == "offline_test" and request["scenario"] in REVIEW_SCENARIOS
            revision = request["mode"] == "offline_test" and request["scenario"] in REVISION_SCENARIOS
            extra = {"revision_cycle"} if revision else {"collaboration"} if collaboration else set()
            require(set(payload) == PAYLOAD_FIELDS | extra and payload["status"] in STATUSES)
            require(type(payload["step_count"]) is int and 0 <= payload["step_count"] <= LIMITS["max_steps"])
            require(type(payload["repair_count"]) is int and 0 <= payload["repair_count"] <= LIMITS["max_repairs"])
            require(type(payload["read_source"]) is bool and isinstance(payload["messages"], list) and isinstance(payload["operations"], list))
            require(len(payload["operations"]) == payload["step_count"])
            ids = []
            for step, operation in enumerate(payload["operations"], 1):
                require(set(operation) == {"id", "step", "context", "context_hash", "status", "response", "error"} | ({"role"} if collaboration or revision else set()))
                if revision:
                    require(operation["role"] in {"planner", "reviewer", "repairer", "rechecker"})
                elif collaboration:
                    require(operation["role"] in {"planner", "reviewer"})
                require(bool(ID_RE.fullmatch(operation["id"])) and type(operation["step"]) is int and operation["step"] == step)
                require(operation["context_hash"] == digest(operation["context"]) and operation["status"] in {"intent", "received", "processed", "unknown"})
                require(operation["response"] is None or isinstance(operation["response"], str) and len(operation["response"].encode("utf-8")) <= LIMITS["max_output_bytes"])
                ids.append(operation["id"])
            require(len(set(ids)) == len(ids) and (payload["pending"] is None or payload["pending"] in ids))
            if request["mode"] == "normal":
                require(payload["step_count"] == 0 and not payload["plans"] and payload["status"] in {"awaiting_authorization", "stopped"})
            for version, plan in enumerate(payload["plans"], 1):
                require(set(plan) == {"version", "plan", "plan_hash", "created_at"} and type(plan["version"]) is int and plan["version"] == version)
                checked, error = checked_plan(plan["plan"], source["snapshot_hash"])
                require(error is None and plan["plan_hash"] == digest(checked))
            if revision:
                from core.planner.revision_cycle import validate

                validate(document, payload, previous["payload"] if previous else None)
            elif collaboration:
                from core.planner.collaboration import validate

                validate(document, payload, previous["payload"] if previous else None)
            require(isinstance(payload["events"], list) and len(payload["events"]) == number)
            for sequence, event in enumerate(payload["events"], 1):
                require(set(event) == {"sequence", "type", "task_revision", "created_at", "step", "code"} and event["sequence"] == sequence and event["task_revision"] == sequence)
            if previous:
                require(payload["events"][:-1] == previous["payload"]["events"])
            require(isinstance(payload["actions"], dict))
            for key, receipt in payload["actions"].items():
                require(bool(KEY_RE.fullmatch(key)) and set(receipt) == {"request", "request_hash", "applied_revision"})
                require(set(receipt["request"]) == {"client_action_key", "expected_revision", "action"} and receipt["request"]["client_action_key"] == key)
                require(receipt["request_hash"] == digest({"task_id": task_id, **receipt["request"]}))
                require(type(receipt["applied_revision"]) is int and 2 <= receipt["applied_revision"] <= number)
            previous = frame


class PlannerStore(CreatorBriefStore):
    @contextmanager
    def transaction(self):
        self.path("state.json")
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.thread_lock:
            lock = lock_file(self.path(".state.lock"))
            try:
                path = self.path("state.json")
                try:
                    state = parse_json(path.read_text(encoding="utf-8")) if path.exists() else {"schema_version": 1, "keys": {}, "tasks": {}}
                    validate_state(state)
                except (CreatorError, ValueError, TypeError, KeyError, AttributeError, UnicodeError, ArithmeticError, RecursionError):
                    raise PlannerError("CORRUPT_STORAGE", "Planner storage failed integrity checks") from None
                before = _state_fingerprint(state)
                yield state
                after = _state_fingerprint(state)
                if after != before or not path.exists():
                    validate_state(state)
                    fd, temporary = tempfile.mkstemp(prefix=".planner-", dir=self.root)
                    try:
                        with os.fdopen(fd, "w", encoding="utf-8") as stream:
                            json.dump(state, stream, ensure_ascii=False, allow_nan=False)
                            stream.flush()
                            os.fsync(stream.fileno())
                        os.replace(temporary, path)
                    finally:
                        Path(temporary).unlink(missing_ok=True)
            finally:
                unlock_file(lock)

    def execution_lock(self, task_id):
        if not ID_RE.fullmatch(task_id):
            raise PlannerError("INVALID_INPUT", "Invalid Planner task ID", 422)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            return lock_file(self.path(f".{task_id}.execution.lock"), blocking=False)
        except BlockingIOError:
            return None

    @staticmethod
    def commit(document, payload, event_type, code=None):
        number = len(document["versions"]) + 1
        now = utc_now()
        payload["events"].append({"sequence": number, "type": event_type, "task_revision": number, "created_at": now,
                                  "step": payload["step_count"] or None, "code": code})
        frame = {"revision": number, "saved_at": now, "payload": copy.deepcopy(payload),
                 "previous_hash": document["versions"][-1]["hash"] if document["versions"] else None}
        frame["hash"] = frame_hash(document, frame)
        document["versions"].append(frame)
        document["head_revision"], document["head_hash"] = number, frame["hash"]
