"""Private immutable receipt candidates; inspection never changes execution."""
import base64
import fcntl
import hashlib
import json
import os
import stat
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from core.creator.store import ID_RE, SHA_RE, CreatorBriefStore, CreatorError, digest, utc_now
from core.execution.policy import now
from core.execution.transport import TRANSPORT_DIAGNOSTIC_PHASES
from core.harness.store import unlock_file
from core.planner.protocol import parse_json

MAX_BODY = 1048576
MAX_FILE = 2 * 1048576
MAX_STORE = 64 * 1048576
MAX_OPERATIONS = 128
MAX_CANDIDATES = 8
MAX_IPC = 8 * 1048576
ERRORS = set(TRANSPORT_DIAGNOSTIC_PHASES) | {"CREDENTIAL_ECHO", "PERSISTENCE_FAILED", "WORKER_INPUT_REJECTED", "CAPACITY_LIMIT", "COLLECTION_CUTOFF"}
BINDING_FIELDS = ("task_id", "snapshot_hash", "context_hash", "wire_hash", "policy_hash", "permit_hash", "grant_id", "quote_id", "account_id")


class ReceiptError(CreatorError):
    def __init__(self):
        super().__init__("PRIVATE_RECEIPT_UNAVAILABLE", "Private receipt evidence is unavailable")


class BindingMismatch(ReceiptError):
    pass


class ObservationEnded(Exception):
    """The observer ended; this is not an HTTP timeout classification."""


@dataclass(frozen=True)
class ReceiptCaptureSettings:
    observation_wait_seconds: float | None = None
    cleanup_grace_seconds: float = 5
    worker_factory: str | None = None
    worker_options: dict = field(default_factory=dict)

    def __post_init__(self):
        for value, minimum, maximum in [(self.observation_wait_seconds, 0.01, 120), (self.cleanup_grace_seconds, 0.1, 30)]:
            if value is not None and (type(value) not in {int, float} or not minimum <= value <= maximum):
                raise ReceiptError()
        if self.worker_factory is not None and (not isinstance(self.worker_factory, str) or len(self.worker_factory) > 200):
            raise ReceiptError()
        if not isinstance(self.worker_options, dict):
            raise ReceiptError()


def require(value):
    if not value:
        raise ReceiptError()


def timestamp(value):
    require(isinstance(value, str) and len(value) <= 64)
    result = datetime.fromisoformat(value)
    require(result.tzinfo is not None)
    return result


def binding_for(operation):
    return {"operation_id": operation["id"], **{key: operation[key] for key in BINDING_FIELDS}, "deadline_at": operation["permit"]["deadline_at"]}


def seal(value, field_name):
    return {**value, field_name: digest(value)}


def check_seal(value, field_name):
    require(isinstance(value, dict) and value.get(field_name) == digest({key: item for key, item in value.items() if key != field_name}))


def read_json(path, maximum=MAX_FILE):
    path = Path(path).absolute()
    require(path.resolve() == path and path.parent.resolve() == path.parent)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(descriptor)
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size <= maximum)
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            data = stream.read(maximum + 1)
        require(len(data) <= maximum)
        return parse_json(data.decode("utf-8"))
    finally:
        os.close(descriptor)


def receipt_lock(path):
    path = Path(path).absolute()
    require(path.resolve() == path and path.parent.resolve() == path.parent)
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    stream = None
    try:
        info = os.fstat(descriptor)
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1)
        stream = os.fdopen(descriptor, "a+b")
        fcntl.flock(stream, fcntl.LOCK_EX)
        return stream
    except Exception:
        if stream is not None:
            stream.close()
        else:
            os.close(descriptor)
        raise


def raw_body(raw):
    require(isinstance(raw, dict) and set(raw) == {"http_status", "headers", "body_base64", "received_at"})
    require(type(raw["http_status"]) is int and 100 <= raw["http_status"] <= 599)
    require(isinstance(raw["headers"], dict) and set(raw["headers"]) <= {"x-request-id"})
    require(all(isinstance(value, str) and len(value) <= 512 for value in raw["headers"].values()))
    require(isinstance(raw["body_base64"], str) and len(raw["body_base64"]) <= 4 * ((MAX_BODY + 2) // 3))
    body = base64.b64decode(raw["body_base64"], validate=True)
    require(len(body) <= MAX_BODY)
    timestamp(raw["received_at"])
    return body


def credential_echo(raw, key):
    data = raw_body(raw)
    if not isinstance(key, str) or not key:
        return True
    if key.encode("utf-8") in data or any(key in value for value in raw["headers"].values()):
        return True
    try:
        pending, decoded, nodes, text_bytes = [(parse_json(data), 0)], set(), 0, 0
        while pending:
            value, layer = pending.pop()
            nodes += 1
            if nodes > 10000:
                return True
            if isinstance(value, str):
                if key in value:
                    return True
                if value not in decoded and layer < 3:
                    decoded.add(value)
                    text_bytes += len(value.encode("utf-8"))
                    if text_bytes > 2 * MAX_BODY:
                        return True
                    try:
                        pending.append((parse_json(value), layer + 1))
                    except (UnicodeError, RecursionError):
                        return True
                    except (ValueError, TypeError):
                        pass
            elif isinstance(value, dict):
                pending.extend((part, layer) for part in value)
                pending.extend((part, layer) for part in value.values())
            elif isinstance(value, list):
                pending.extend((part, layer) for part in value)
    except (UnicodeError, RecursionError):
        return True
    except (ValueError, TypeError):
        pass
    return False


class ReceiptStore(CreatorBriefStore):
    def __init__(self, root, *, quota_root=None):
        super().__init__(root)
        self.quota_root = Path(quota_root).absolute() if quota_root is not None else self.root

    def operation(self, operation_id):
        require(isinstance(operation_id, str) and bool(ID_RE.fullmatch(operation_id)))
        return ReceiptStore(self.root / operation_id, quota_root=self.quota_root)

    def _capacity(self, size, *, marker=False, extra_operations=0):
        total, operations = 0, 0
        for directory, dirs, files in os.walk(self.quota_root, followlinks=False):
            for name in dirs:
                path = Path(directory) / name
                require(not path.is_symlink())
                if path.parent == self.quota_root and ID_RE.fullmatch(name):
                    operations += 1
            for name in files:
                path = Path(directory) / name
                require(not path.is_symlink())
                total += path.stat().st_size
        operations += extra_operations
        reserve = 0 if marker else operations * 2048
        require(total + size + reserve <= MAX_STORE and operations <= MAX_OPERATIONS)

    def write_new(self, filename, value):
        target = self.path(filename)
        if target.exists():
            return False
        require(self.root == self.quota_root or self.root.exists())
        data = json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode("utf-8")
        require(len(data) <= MAX_FILE)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        quota = receipt_lock(self.quota_root / ".quota.lock")
        try:
            if target.exists():
                return False
            self._capacity(len(data), marker=filename in {"error.json", "observer-closed.json"})
            descriptor, temporary = tempfile.mkstemp(prefix=".receipt-", dir=self.root)
            try:
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                try:
                    os.link(temporary, target, follow_symlinks=False)
                except FileExistsError:
                    return False
                Path(temporary).unlink()
                directory = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
                return True
            finally:
                Path(temporary).unlink(missing_ok=True)
        finally:
            unlock_file(quota)

    def read(self, filename):
        return read_json(self.path(filename))

    def prepare(self, binding, payload_hash, capability_hash, observer_until, cleanup_cutoff):
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        op = self.operation(binding["operation_id"])
        value = seal({"version": 1, "binding": binding, "binding_hash": digest(binding), "payload_hash": payload_hash,
            "capability_hash": capability_hash, "observer_until": observer_until, "cleanup_cutoff": cleanup_cutoff,
            "observer_pid": os.getpid(), "created_at": utc_now()}, "claim_hash")
        quota = receipt_lock(self.quota_root / ".quota.lock")
        try:
            if not op.root.exists():
                size = len(json.dumps(value, sort_keys=True, ensure_ascii=False).encode("utf-8"))
                self._capacity(size, extra_operations=1)
                op.root.mkdir(mode=0o700)
        finally:
            unlock_file(quota)
        return op, op.write_new("claim.json", value)

    def claim(self, binding=None):
        value = self.read("claim.json")
        require(set(value) == {"version", "binding", "binding_hash", "payload_hash", "capability_hash", "observer_until", "cleanup_cutoff", "observer_pid", "created_at", "claim_hash"})
        check_seal(value, "claim_hash")
        require(type(value["version"]) is int and value["version"] == 1 and value["binding_hash"] == digest(value["binding"]))
        if binding is not None and value["binding"] != binding:
            raise BindingMismatch()
        for name in ["payload_hash", "capability_hash"]:
            require(isinstance(value[name], str) and bool(SHA_RE.fullmatch(value[name])))
        require(timestamp(value["observer_until"]) <= timestamp(value["binding"]["deadline_at"]) < timestamp(value["cleanup_cutoff"]))
        require(type(value["observer_pid"]) is int and value["observer_pid"] > 0)
        require(timestamp(value["created_at"]) <= now())
        return value

    def error(self, binding, code):
        require(code in ERRORS)
        self.write_new("error.json", seal({"version": 1, "binding_hash": digest(binding), "code": code, "recorded_at": utc_now()}, "error_hash"))

    def commit(self, binding, raw, api_key):
        body = raw_body(raw)
        require(not credential_echo(raw, api_key))
        claim = self.claim(binding)
        require(timestamp(claim["created_at"]) <= timestamp(raw["received_at"]) <= now())
        identity = {"binding_hash": digest(binding), "body_sha256": hashlib.sha256(body).hexdigest(), "http_status": raw["http_status"], "headers": raw["headers"]}
        record_id = digest(identity)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        writer = receipt_lock(self.path(".writer.lock"))
        try:
            require(len(list(self.root.glob("*.record.json"))) < MAX_CANDIDATES or self.path(record_id + ".record.json").exists())
            record = seal({"version": 1, "record_id": record_id, "binding": binding, **identity, "raw": raw, "commit_started_at": utc_now()}, "envelope_hash")
            if not self.write_new(record_id + ".record.json", record):
                record = self.read(record_id + ".record.json")
                self._record(record, binding)
            confirmation = seal({"version": 1, "record_id": record_id, "binding_hash": digest(binding),
                "envelope_hash": record["envelope_hash"], "record_durable_at": utc_now()}, "commit_hash")
            self.write_new(record_id + ".commit.json", confirmation)
            return record_id
        finally:
            unlock_file(writer)

    def notify(self, binding, record_id):
        confirmation = self.read(record_id + ".commit.json")
        self.write_new(record_id + ".ready.json", seal({"version": 1, "record_id": record_id,
            "binding_hash": digest(binding), "commit_hash": confirmation["commit_hash"], "durable_confirmation_at": utc_now()}, "ready_hash"))

    @staticmethod
    def _record(record, binding):
        require(set(record) == {"version", "record_id", "binding", "binding_hash", "body_sha256", "http_status", "headers", "raw", "commit_started_at", "envelope_hash"})
        check_seal(record, "envelope_hash")
        require(type(record["version"]) is int and record["version"] == 1)
        if record["binding"] != binding or record["binding_hash"] != digest(binding):
            raise BindingMismatch()
        raw = record["raw"]
        require(isinstance(raw, dict) and set(raw) == {"http_status", "headers", "body_base64", "received_at"})
        body = raw_body(raw)
        require(hashlib.sha256(body).hexdigest() == record["body_sha256"])
        require(type(raw["http_status"]) is int and 100 <= raw["http_status"] <= 599 and raw["http_status"] == record["http_status"])
        require(isinstance(raw["headers"], dict) and set(raw["headers"]) <= {"x-request-id"} and raw["headers"] == record["headers"])
        require(all(isinstance(value, str) and len(value) <= 512 for value in raw["headers"].values()))
        identity = {key: record[key] for key in ["binding_hash", "body_sha256", "http_status", "headers"]}
        require(record["record_id"] == digest(identity))
        require(timestamp(raw["received_at"]) <= timestamp(record["commit_started_at"]) <= now())

    def inspect(self, operation):
        return self.inspect_binding(operation["id"], binding_for(operation))

    def inspect_binding(self, capture_id, binding):
        """Read-only inspection against an explicit, server-verified binding."""
        require(binding["operation_id"] == capture_id)
        op = self.operation(capture_id)
        result = {"operation_id": capture_id, "status": "missing", "candidate_count": 0, "binding_verified": False,
            "isolated": True, "recovery_performed": False, "notification_present": False, "body_sha256": None,
            "response_received_at": None, "durable_confirmation_at": None, "original_deadline_at": binding["deadline_at"], "observer_until": None}
        if not op.root.exists():
            return result
        try:
            require(op.root.is_dir() and op.root.resolve() == op.root)
            names = set()
            for entry in op.root.iterdir():
                require(not entry.is_symlink() and entry.is_file())
                names.add(entry.name)
                require(len(names) <= 3 * MAX_CANDIDATES + 8)
            claim = op.claim(binding)
            result["observer_until"] = claim["observer_until"]
            expected = {"claim.json", ".writer.lock"}
            for name, hash_name, fields in [
                ("worker.json", "worker_hash", {"version", "binding_hash", "worker_pid", "booted_at", "worker_hash"}),
                ("started.json", "started_hash", {"version", "binding_hash", "worker_pid", "dispatched_at", "started_hash"}),
                ("observer-closed.json", "closed_hash", {"version", "binding_hash", "closed_at", "reason", "closed_hash"}),
                ("error.json", "error_hash", {"version", "binding_hash", "code", "recorded_at", "error_hash"}),
            ]:
                if name in names:
                    value = op.read(name)
                    require(set(value) == fields and type(value["version"]) is int and value["version"] == 1 and value["binding_hash"] == digest(binding))
                    if name == "worker.json":
                        require(type(value["worker_pid"]) is int and value["worker_pid"] > 0)
                        require(timestamp(claim["created_at"]) <= timestamp(value["booted_at"]) <= now())
                    elif name == "started.json":
                        require(type(value["worker_pid"]) is int and value["worker_pid"] > 0)
                        require(timestamp(claim["created_at"]) <= timestamp(value["dispatched_at"]) < timestamp(binding["deadline_at"]))
                    elif name == "observer-closed.json":
                        require(value["reason"] in {"observation_ended", "absolute_deadline"})
                        require(timestamp(claim["created_at"]) <= timestamp(value["closed_at"]) <= now())
                    else:
                        require(timestamp(claim["created_at"]) <= timestamp(value["recorded_at"]) <= now())
                    check_seal(value, hash_name)
                    if name == "error.json":
                        require(value["code"] in ERRORS)
                    expected.add(name)
            records = sorted(name for name in names if name.endswith(".record.json"))
            require(len(records) <= MAX_CANDIDATES)
            candidates = []
            for name in records:
                record = op.read(name)
                self._record(record, binding)
                require(timestamp(record["raw"]["received_at"]) >= timestamp(claim["created_at"]))
                record_id = record["record_id"]
                require(name == record_id + ".record.json")
                expected.update({name, record_id + ".commit.json"})
                if record_id + ".commit.json" not in names:
                    result["status"] = "incomplete"
                    return result
                confirmation = op.read(record_id + ".commit.json")
                require(set(confirmation) == {"version", "record_id", "binding_hash", "envelope_hash", "record_durable_at", "commit_hash"})
                check_seal(confirmation, "commit_hash")
                require(type(confirmation["version"]) is int and confirmation["version"] == 1)
                require(confirmation["record_id"] == record_id and confirmation["binding_hash"] == digest(binding) and confirmation["envelope_hash"] == record["envelope_hash"])
                require(timestamp(record["commit_started_at"]) <= timestamp(confirmation["record_durable_at"]) <= now())
                ready = None
                if record_id + ".ready.json" in names:
                    ready = op.read(record_id + ".ready.json")
                    expected.add(record_id + ".ready.json")
                    require(set(ready) == {"version", "record_id", "binding_hash", "commit_hash", "durable_confirmation_at", "ready_hash"})
                    check_seal(ready, "ready_hash")
                    require(type(ready["version"]) is int and ready["version"] == 1)
                    require(ready["record_id"] == record_id and ready["binding_hash"] == digest(binding) and ready["commit_hash"] == confirmation["commit_hash"])
                    require(timestamp(confirmation["record_durable_at"]) <= timestamp(ready["durable_confirmation_at"]) <= now())
                candidates.append((record, ready))
            require(names <= expected)
            if "error.json" in names:
                result["status"] = "capture_error"
                return result
            if not candidates:
                result["status"] = "incomplete"
                return result
            result["candidate_count"] = len(candidates)
            if len(candidates) != 1:
                result["status"] = "ambiguous"
                return result
            record, ready = candidates[0]
            result.update(status="candidate_found", binding_verified=True, notification_present=ready is not None,
                body_sha256=record["body_sha256"], response_received_at=record["raw"]["received_at"],
                durable_confirmation_at=ready["durable_confirmation_at"] if ready else None)
            return result
        except BindingMismatch:
            result.update(status="binding_mismatch", binding_verified=False, candidate_count=0)
            return result
        except (CreatorError, ValueError, TypeError, KeyError, AttributeError, UnicodeError, OSError, RecursionError):
            result.update(status="corrupt", binding_verified=False, candidate_count=0)
            return result

    def ready_raw(self, operation):
        return self.ready_raw_binding(operation["id"], binding_for(operation))

    def ready_raw_binding(self, capture_id, binding):
        result = self.inspect_binding(capture_id, binding)
        if result["status"] != "candidate_found" or not result["notification_present"]:
            return None, result
        op = self.operation(capture_id)
        names = list(op.root.glob("*.record.json"))
        require(len(names) == 1)
        record = op.read(names[0].name)
        self._record(record, binding)
        return record["raw"], result



def main():
    """Inspection-only CLI. It has no submission, recovery or writer command."""
    import argparse

    class Parser(argparse.ArgumentParser):
        def error(self, message):
            raise ReceiptError()

    try:
        parser = Parser(add_help=False)
        parser.add_argument("command", choices=["inspect"])
        parser.add_argument("--execution-root", required=True)
        parser.add_argument("--task-id", required=True)
        parser.add_argument("--operation-id", required=True)
        args = parser.parse_args()
        require(bool(ID_RE.fullmatch(args.task_id)) and bool(ID_RE.fullmatch(args.operation_id)))
        from core.execution.store import ExecutionStore
        store = ExecutionStore(Path(args.execution_root).absolute())
        operation = store.read_only()["operations"].get(args.operation_id)
        require(operation is not None and operation["task_id"] == args.task_id)
        result = {"inspection": ReceiptStore(store.root / "receipts").inspect(operation)}
        print(json.dumps(result, sort_keys=True))
        return 0
    except Exception:
        print(json.dumps({"error": "INSPECTION_UNAVAILABLE"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
