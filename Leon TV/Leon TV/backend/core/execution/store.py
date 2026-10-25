"""One execution journal is authoritative; no two-file atomicity claim."""
import copy
import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path

from core.creator.store import ID_RE, KEY_RE, CreatorBriefStore, CreatorError, digest, utc_now
from core.execution.policy import chat_output_parameter, wire_api
from core.execution.transport import TRANSPORT_DIAGNOSTIC_PHASES
from core.harness.store import lock_file, unlock_file
from core.planner.protocol import parse_json
from core.planner.store import check_source

EMPTY = {"tasks": {}, "quotes": {}, "grants": {}, "operations": {}, "keys": {}, "accounts": {}}
NAMESPACES = set(EMPTY) | {"media"}
MAX_STATE_BYTES = 64 * 1048576


def validate_media(payload, previous=None):
    if "media" in payload or previous is not None and "media" in previous:
        from core.execution.media_ledger import validate
        validate(payload, previous)


def has_roles(payload):
    names = {"role_workflow", "role_state", "environment", "role", "role_call_index", "role_binding_hash", "role_binding", "role_scope", "role_scope_hash", "accounting_kind", "empirical", "parent_anchor_hash"}
    return any(bool(set(record) & names) or bool(set(record.get("permit", {})) & names)
               or "role_workflow" in record.get("policy", {})
               for namespace in ("tasks", "quotes", "grants", "operations", "accounts") for record in payload[namespace].values())


def validate_roles(payload, previous=None):
    if has_roles(payload) or previous is not None and has_roles(previous):
        from core.execution.roles import validate
        validate(payload, previous)


def require(condition):
    if not condition:
        raise CreatorError("CORRUPT_STORAGE", "Execution journal failed integrity checks")


def exposure(operation):
    if "empirical" in operation:
        return max(operation["hold_units"], operation["local_units"] or 0, operation["empirical"]["platform_units"] or 0)
    return operation["local_units"] if operation["status"] == "settled" else operation["hold_units"]


def totals(payload, *, task_id=None, account_id=None):
    ops = [op for op in payload["operations"].values() if (task_id is None or op["task_id"] == task_id) and (account_id is None or op["account_id"] == account_id)]
    account = payload["accounts"].get(account_id, {}) if task_id is None else {}
    opening = account.get("anchor", {})
    from core.execution.policy import units
    return {"accounted": sum(exposure(op) for op in ops if op["status"] == "settled"),
            "held": sum(exposure(op) for op in ops if op["status"] != "settled") + units(opening.get("opening_exposure_cny", "0")),
            "calls": len(ops) + opening.get("opening_call_count", 0)}


def validate_payload(payload):
    require(isinstance(payload, dict) and set(EMPTY) <= set(payload) <= NAMESPACES and all(isinstance(value, dict) for value in payload.values()))
    require(len(payload["accounts"]) <= 1)
    for task_id, task in payload["tasks"].items():
        require(bool(ID_RE.fullmatch(task_id)) and task["id"] == task_id)
        check_source(task["source"])
        require(type(task["revision"]) is int and task["revision"] >= 1 and len(task["events"]) == task["revision"])
        require(task["source"]["snapshot_hash"] == task["snapshot_hash"])
        require(type(task["task_limit_units"]) is int and task["task_limit_units"] > 0)
        amount = totals(payload, task_id=task_id)
        overrun = any(op.get("empirical", {}).get("fee_status") == "overrun" for op in payload["operations"].values() if op["task_id"] == task_id)
        require((amount["accounted"] + amount["held"] <= task["task_limit_units"] or overrun) and amount["calls"] <= min(task["max_calls"], 6))
        for sequence, event in enumerate(task["events"], 1):
            require(set(event) == {"sequence", "type", "execution_revision", "created_at", "operation_id", "code"} and event["sequence"] == sequence and event["execution_revision"] == sequence)
    for account_id, account in payload["accounts"].items():
        require(account["id"] == account_id)
        amount = totals(payload, account_id=account_id)
        overrun = any(op.get("empirical", {}).get("fee_status") == "overrun" for op in payload["operations"].values() if op["account_id"] == account_id)
        require((amount["accounted"] + amount["held"] <= account["limit_units"] or overrun) and amount["calls"] <= account["max_calls"])
    for op_id, operation in payload["operations"].items():
        require(bool(ID_RE.fullmatch(op_id)) and operation["id"] == op_id and operation["task_id"] in payload["tasks"])
        require(operation["account_id"] in payload["accounts"] and operation["grant_id"] in payload["grants"] and operation["quote_id"] in payload["quotes"])
        require(type(operation["hold_units"]) is int and operation["hold_units"] > 0)
        require(operation["status"] in {"submitting", "received", "settled", "needs_reconciliation"})
        require(operation["local_units"] is None or type(operation["local_units"]) is int and 0 <= operation["local_units"] and ("empirical" in operation or operation["local_units"] <= operation["hold_units"]))
        require(operation["status"] != "settled" or operation["local_units"] is not None)
        if "transport_diagnostic" in operation:
            diagnostic = operation["transport_diagnostic"]
            require(isinstance(diagnostic, dict) and set(diagnostic) == {"code", "phase"})
            require(type(diagnostic["code"]) is str and diagnostic["code"] in TRANSPORT_DIAGNOSTIC_PHASES)
            require(diagnostic["phase"] == TRANSPORT_DIAGNOSTIC_PHASES[diagnostic["code"]])
            require(operation["status"] == "needs_reconciliation" and operation["raw"] is None and operation["normalized"] is None and operation["local_units"] is None)
        require(operation["context_hash"] == digest(operation["context"]) and operation["wire_hash"] == digest(operation["wire"]))
        require(operation["permit_hash"] == digest(operation["permit"]))
        task, grant, quote = payload["tasks"][operation["task_id"]], payload["grants"][operation["grant_id"]], payload["quotes"][operation["quote_id"]]
        require(operation["snapshot_hash"] == task["snapshot_hash"] == grant["snapshot_hash"] == quote["snapshot_hash"])
        tagged = "wire_api" in quote["policy"]
        require(("wire_api" in operation) == tagged and ("wire_api" in operation["permit"]) == tagged)
        require(wire_api(operation) == wire_api(operation["permit"]) == wire_api(quote["policy"]))
        parameter_tagged = "chat_output_parameter" in quote["policy"]
        require(("chat_output_parameter" in operation) == parameter_tagged and ("chat_output_parameter" in operation["permit"]) == parameter_tagged)
        require(chat_output_parameter(operation) == chat_output_parameter(operation["permit"]) == chat_output_parameter(quote["policy"]))
    for quote_id, quote in payload["quotes"].items():
        require(quote["id"] == quote_id and quote["task_id"] in payload["tasks"])
        require(quote["disclosure_hash"] == digest({key: value for key, value in quote.items() if key != "disclosure_hash"}))
        policy = quote["policy"]
        require(policy["policy_hash"] == digest({key: value for key, value in policy.items() if key != "policy_hash"}))
    for grant_id, grant in payload["grants"].items():
        require(grant["id"] == grant_id and grant["task_id"] in payload["tasks"] and grant["quote_id"] in payload["quotes"])
        require(grant["snapshot_hash"] == payload["tasks"][grant["task_id"]]["snapshot_hash"])
    for key, receipt in payload["keys"].items():
        namespace, _, token = key.partition(":")
        require(namespace in {"quote", "grant", "revoke", "action"} and bool(KEY_RE.fullmatch(token)))
        require(receipt["request_hash"] == digest({"key": key, **receipt["request"]}))
        require(receipt["task_id"] in payload["tasks"] and receipt["request"]["task_id"] == receipt["task_id"])
    validate_roles(payload)
    validate_media(payload)


def validate_state(state):
    require(set(state) == {"schema_version", "head_revision", "head_hash", "frames"} and type(state["schema_version"]) is int and state["schema_version"] == 2)
    require(isinstance(state["frames"], list) and type(state["head_revision"]) is int and state["head_revision"] == len(state["frames"]))
    previous = None
    payload = copy.deepcopy(EMPTY)
    for revision, frame in enumerate(state["frames"], 1):
        require(set(frame) == {"revision", "saved_at", "previous_hash", "hash", "delta"} and type(frame["revision"]) is int and frame["revision"] == revision)
        require(frame["previous_hash"] == previous and frame["hash"] == digest({key: value for key, value in frame.items() if key != "hash"}))
        require(isinstance(frame["delta"], dict) and set(frame["delta"]) <= NAMESPACES)
        before = {namespace: dict(records) for namespace, records in payload.items()}
        for namespace, records in frame["delta"].items():
            require(isinstance(records, dict))
            if namespace == "media" and namespace not in payload:
                payload[namespace] = {}
            payload[namespace].update(copy.deepcopy(records))
        validate_roles(payload, before)
        validate_media(payload, before)
        previous = frame["hash"]
    require(state["head_hash"] == previous)
    validate_payload(payload)
    return payload


class ExecutionStore(CreatorBriefStore):
    def read_only(self):
        """Atomic file snapshots need no write lock or recovery side effects."""
        from core.execution.receipts import read_json

        path = self.path("state.json")
        if not path.exists():
            return copy.deepcopy(EMPTY)
        try:
            return validate_state(read_json(path, maximum=MAX_STATE_BYTES))
        except (CreatorError, ValueError, TypeError, KeyError, AttributeError, ArithmeticError, UnicodeError, OSError, RecursionError):
            raise CreatorError("CORRUPT_STORAGE", "Execution journal failed integrity checks") from None

    @contextmanager
    def transaction(self):
        self.path("state.json")
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.thread_lock:
            lock = lock_file(self.path(".state.lock"))
            try:
                path = self.path("state.json")
                try:
                    state = parse_json(path.read_text(encoding="utf-8")) if path.exists() else {"schema_version": 2, "head_revision": 0, "head_hash": None, "frames": []}
                    payload = validate_state(state)
                except (CreatorError, ValueError, TypeError, KeyError, AttributeError, ArithmeticError, UnicodeError, RecursionError):
                    raise CreatorError("CORRUPT_STORAGE", "Execution journal failed integrity checks") from None
                original = copy.deepcopy(payload)
                original_digest = digest(original) if has_roles(original) or "media" in original else None
                yield payload
                try:
                    profiled = has_roles(payload) or has_roles(original) or "media" in payload or "media" in original
                    if profiled:
                        from core.execution.roles import json_tree
                        json_tree(payload)
                    changed_payload = digest(payload) != (original_digest or digest(original)) if profiled else payload != original
                    if changed_payload and profiled:
                        validate_roles(payload, original)
                        validate_media(payload, original)
                except (CreatorError, ValueError, TypeError, KeyError, AttributeError, ArithmeticError, UnicodeError, RecursionError):
                    raise CreatorError("CORRUPT_STORAGE", "Execution journal failed integrity checks") from None
                if changed_payload or not path.exists():
                    try:
                        validate_payload(payload)
                    except (CreatorError, ValueError, TypeError, KeyError, AttributeError, ArithmeticError, UnicodeError, RecursionError):
                        raise CreatorError("CORRUPT_STORAGE", "Execution journal failed integrity checks") from None
                    require(all(namespace in payload and set(original[namespace]) <= set(payload[namespace]) for namespace in original))
                    delta = {}
                    for namespace, records in payload.items():
                        old_records = original.get(namespace, {})
                        changed = {key: copy.deepcopy(value) for key, value in records.items() if key not in old_records or (digest(old_records[key]) != digest(value) if profiled else old_records[key] != value)}
                        if changed:
                            delta[namespace] = changed
                    frame = {"revision": len(state["frames"]) + 1, "saved_at": utc_now(), "previous_hash": state["head_hash"], "delta": delta}
                    frame["hash"] = digest(frame)
                    state["frames"].append(frame)
                    state["head_revision"], state["head_hash"] = frame["revision"], frame["hash"]
                    serialized = json.dumps(state, ensure_ascii=False, allow_nan=False).encode("utf-8")
                    if len(serialized) > MAX_STATE_BYTES:
                        raise CreatorError("EXECUTION_STORAGE_LIMIT", "Execution journal capacity is exhausted", 409)
                    fd, temporary = tempfile.mkstemp(prefix=".execution-", dir=self.root)
                    try:
                        with os.fdopen(fd, "wb") as stream:
                            stream.write(serialized)
                            stream.flush()
                            os.fsync(stream.fileno())
                        os.replace(temporary, path)
                    finally:
                        Path(temporary).unlink(missing_ok=True)
            finally:
                unlock_file(lock)

    def execution_lock(self, task_id):
        if not ID_RE.fullmatch(task_id):
            raise CreatorError("INVALID_INPUT", "Invalid task ID", 422)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            return lock_file(self.path(f".{task_id}.execution.lock"), blocking=False)
        except BlockingIOError:
            return None
