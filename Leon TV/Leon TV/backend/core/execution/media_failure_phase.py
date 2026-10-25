"""Private immutable observer stages; never a receipt or retry authorization."""
import copy
import os
import stat
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

from core.creator.store import ID_RE, CreatorError, digest, utc_now
from core.execution.image_policy import FrozenImageRequest
from core.execution.policy import now
from core.execution.receipts import ReceiptStore, check_seal, read_json, seal, timestamp
from core.planner.review import json_copy

STAGES = frozenset({
    "capture_entry", "control_before_key", "native_before_key", "deadline_before_key",
    "credential_load", "credential_check", "control_after_key", "native_after_key",
    "deadline_after_key", "image_descriptor", "image_authority", "image_credential",
    "image_deadline", "image_environment", "collector_ipc", "collector_prepare",
    "collector_existing_claim", "worker_launch", "worker_feed", "collector_observe",
    "collector_close",
})
_CURRENT = ContextVar("media_capture_failure_stage", default=None)
_FIELDS = {"schema_version", "kind", "binding", "binding_hash", "native_record_kind", "scope_id",
    "capture_started_at", "stage", "recorded_at", "observer_stage_only", "http_outcome",
    "fee_outcome", "retry_authorized", "evidence_hash"}


def unavailable():
    return CreatorError("MEDIA_FAILURE_EVIDENCE_UNAVAILABLE", "Private image failure evidence is unavailable", 409)


def require(value):
    if not value:
        raise unavailable()


class CaptureTrace:
    """One synchronous call stack; no trace state survives scope exit."""
    def __init__(self):
        self.stage = "capture_entry"

    @contextmanager
    def active(self):
        token = _CURRENT.set(self)
        try:
            yield self
        finally:
            _CURRENT.reset(token)


def capture_stage(stage):
    """No-op outside the native image capture stack, including text capture."""
    current = _CURRENT.get()
    if current is not None:
        require(type(stage) is str and stage in STAGES)
        current.stage = stage


def _request(record):
    require(type(record) is dict and record.get("kind") in {"operation", "query"})
    request = FrozenImageRequest.from_frozen(record["descriptor"])
    binding = request.binding()
    require(record["id"] == binding["operation_id"] and record["task_id"] == binding["task_id"])
    require(type(record["scope_id"]) is str and ID_RE.fullmatch(record["scope_id"]) is not None)
    require(record["capture_started_at"] is not None)
    require(timestamp(request.frozen()["permit"]["issued_at"]) <= timestamp(record["capture_started_at"]) <= now())
    require((record["kind"] == "operation") is (request.method == "POST"))
    return request


def checked_failure_phase(value, record):
    """Pure full-binding verification against an independently checked native record."""
    try:
        value = json_copy(value, limit=16384)
        request = _request(record)
        binding = request.binding()
        require(type(value) is dict and set(value) == _FIELDS)
        check_seal(value, "evidence_hash")
        require(type(value["schema_version"]) is int and value["schema_version"] == 1)
        require(value["kind"] == "native_image_failure_phase_v1")
        require(digest(value["binding"]) == digest(binding)
            and value["binding_hash"] == digest(value["binding"]) == digest(binding))
        require(value["native_record_kind"] == record["kind"] and value["scope_id"] == record["scope_id"])
        require(value["capture_started_at"] == record["capture_started_at"])
        require(type(value["stage"]) is str and value["stage"] in STAGES)
        require(timestamp(value["capture_started_at"]) <= timestamp(value["recorded_at"]) <= now())
        require(value["observer_stage_only"] is True and value["retry_authorized"] is False)
        require(value["http_outcome"] == value["fee_outcome"] == "unknown")
        return value
    except Exception:
        raise unavailable() from None


def _store(execution_root):
    result = ReceiptStore(Path(execution_root) / "media-failures")
    backend = Path(__file__).resolve().parents[2]
    result.check_private((backend / "code", backend.parent / "frontend/public"))
    if result.root.exists():
        info = result.root.stat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o700)
    return result


def record_failure_phase(execution_root, record, stage):
    """First write only; caller must still persist unknown if this write fails."""
    try:
        require(record["status"] == "submitting")
        request = _request(record)
        binding = request.binding()
        value = seal({"schema_version": 1, "kind": "native_image_failure_phase_v1", "binding": binding,
            "binding_hash": digest(binding), "native_record_kind": record["kind"], "scope_id": record["scope_id"],
            "capture_started_at": record["capture_started_at"], "stage": stage, "recorded_at": utc_now(),
            "observer_stage_only": True, "http_outcome": "unknown", "fee_outcome": "unknown",
            "retry_authorized": False}, "evidence_hash")
        checked_failure_phase(value, record)
        store = _store(execution_root)
        store.write_new(record["id"] + ".json", value)
        return read_failure_phase(execution_root, record)
    except Exception:
        raise unavailable() from None


def read_failure_phase(execution_root, record):
    """Strictly read-only; missing historical evidence stays missing."""
    try:
        _request(record)
        store = _store(execution_root)
        path = store.path(record["id"] + ".json")
        if not path.exists():
            return None
        info = path.stat()
        require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o600)
        return checked_failure_phase(read_json(path, maximum=16384), record)
    except Exception:
        raise unavailable() from None


def failure_projection(execution_root, record):
    if record["capture_started_at"] is None:
        return {"status": "missing"}
    value = read_failure_phase(execution_root, record)
    if value is None:
        return {"status": "missing"}
    return {"status": "observed", **{key: copy.deepcopy(value[key]) for key in ("stage", "recorded_at",
        "observer_stage_only", "http_outcome", "fee_outcome", "retry_authorized", "evidence_hash")}}
