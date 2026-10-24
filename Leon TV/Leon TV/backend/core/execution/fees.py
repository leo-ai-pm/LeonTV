"""Server-owned Tudou exports and recoverable delivery to the native journal.

The inbox records evidence and delivery, never spend or execution authority.
Importing an export cannot start a model request. Native observation and inbox
acknowledgement are separate durable writes, recovered by exact evidence replay.
"""
import base64
import copy
import hashlib
import json
import os
import re
import tempfile
import threading
from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Literal

from pydantic import ConfigDict, Field, field_validator

from core.creator.store import CreatorError, digest, utc_now
from core.execution.empirical import KIND, decode, observation
from core.execution.transport import wire_for
from core.harness.store import lock_file, unlock_file
from core.planner.protocol import Strict, parse_json

MAX_EXPORT_BYTES = 8 * 1048576
MAX_STATE_BYTES = 32 * 1048576
SHA = re.compile(r"^[0-9a-f]{64}$")
ROW_FIELDS = {"id", "user_id", "created_at", "type", "content", "username", "token_name", "model_name", "quota",
              "prompt_tokens", "completion_tokens", "use_time", "is_stream", "channel", "channel_name", "token_id",
              "group", "ip", "request_id", "other"}
MATCH_FIELDS = {"created_at", "type", "token_name", "model_name", "quota", "prompt_tokens", "completion_tokens", "group", "request_id"}
EVIDENCE_FIELDS = {"schema_version", "operation_id", "wire_hash", "raw_body_sha256", "model", "provider_response_id",
                   "provider_request_id", "prompt_tokens", "completion_tokens", "platform_log_id", "platform_fee_cny",
                   "source_export_hash", "observed_at"}


def fail(code="FEE_EXPORT_INVALID"):
    raise CreatorError(code, "Server fee export evidence is unavailable or conflicting", 409)


def require(condition, code="FEE_EXPORT_INVALID"):
    if not condition:
        fail(code)


def request_key(request_id):
    return "tudou-request-sha256:" + hashlib.sha256(request_id.encode("utf-8")).hexdigest()


def timestamp(value):
    require(type(value) is str)
    result = datetime.fromisoformat(value)
    require(result.tzinfo is not None)
    return result.timestamp()


class FrozenFeeSource(Strict):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)
    account_namespace: str = Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")
    token_name: str = Field(min_length=1, max_length=512)
    group: str = Field(min_length=1, max_length=512)
    quota_per_cny: Literal[500000] = 500000

    @field_validator("quota_per_cny", mode="before")
    @classmethod
    def strict_quota(cls, value):
        if type(value) is not int:
            raise ValueError("Integer native quota unit required")
        return value


def parse_export(raw):
    """Decode the complete, original /api/log/self page; never a fee form."""
    try:
        require(type(raw) is bytes and 0 < len(raw) <= MAX_EXPORT_BYTES)
        value = parse_json(raw.decode("utf-8"))
        require(type(value) is dict and set(value) == {"success", "message", "data"})
        require(value["success"] is True and type(value["message"]) is str)
        data = value["data"]
        require(type(data) is dict and set(data) == {"page", "page_size", "total", "items"})
        require(type(data["page"]) is int and data["page"] == 1)
        require(type(data["page_size"]) is int and data["page_size"] == 100)
        require(type(data["total"]) is int and 0 <= data["total"] <= 100)
        require(type(data["items"]) is list and data["total"] == len(data["items"]))
        rows = {}
        for item in data["items"]:
            require(type(item) is dict and ROW_FIELDS <= set(item))
            for name in ("id", "created_at", "type", "quota", "prompt_tokens", "completion_tokens"):
                require(type(item[name]) is int and item[name] >= 0)
            for name in ("token_name", "group", "model_name", "request_id"):
                require(type(item[name]) is str and 0 < len(item[name]) <= 512)
                item[name].encode("utf-8")
            row = {name: item[name] for name in MATCH_FIELDS}
            key = request_key(row["request_id"])
            require(key not in rows or digest(rows[key]) == digest(row), "FEE_EVIDENCE_CONFLICT")
            rows[key] = row
        return rows
    except CreatorError:
        raise
    except Exception:
        fail()


def operation_basis(operation, policy):
    """Reconstruct receipt/wire independently of a proposed platform fee."""
    try:
        require(operation["accounting_kind"] == policy["kind"] == KIND)
        require(policy["policy_hash"] == digest({key: value for key, value in policy.items() if key != "policy_hash"}))
        require(operation["policy_hash"] == policy["policy_hash"] == operation["permit"]["policy_hash"])
        require(operation["wire"] == wire_for(operation["context"], policy))
        require(operation["wire_hash"] == digest(operation["wire"]) == operation["permit"]["wire_hash"])
        require(operation["context_hash"] == digest(operation["context"]) == operation["permit"]["context_hash"])
        require(operation["permit"]["operation_id"] == operation["id"] and operation["permit"]["task_id"] == operation["task_id"])
        require(operation["permit"]["model"] == policy["model"])
        require(operation["raw"] is not None)
        receipt, _ = decode(operation["raw"], policy)
        require(receipt["receipt_classification"] in {"complete", "truncated"})
        if operation["normalized"] is not None:
            require(digest(receipt) == digest(operation["normalized"]))
        body_hash = hashlib.sha256(base64.b64decode(operation["raw"]["body_base64"], validate=True)).hexdigest()
        issued, received = timestamp(operation["permit"]["issued_at"]), timestamp(operation["raw"]["received_at"])
        require(issued <= received)
        checked = copy.deepcopy(operation)
        checked["normalized"] = receipt
        binding = digest({"id": operation["id"], "task_id": operation["task_id"], "account_id": operation["account_id"],
                          "policy_hash": policy["policy_hash"], "wire_hash": operation["wire_hash"], "raw_body_sha256": body_hash,
                          "issued_at": operation["permit"]["issued_at"], "received_at": operation["raw"]["received_at"],
                          "receipt": receipt})
        return checked, receipt, body_hash, issued, received, binding
    except CreatorError:
        raise
    except Exception:
        fail()


class FeeInbox:
    def __init__(self, root, source):
        self.root = Path(root).resolve()
        self.source = FrozenFeeSource.model_validate(source.model_dump() if isinstance(source, FrozenFeeSource) else source)
        self.source_hash = digest(self.source.model_dump())
        self.thread_lock = threading.RLock()
        self._path("state.json")

    def _path(self, name):
        path = self.root / name
        require(self.root.resolve() == self.root and path.resolve() == path and path.parent == self.root, "FEE_INBOX_CORRUPT")
        return path

    def _atomic(self, path, data):
        fd, temporary = tempfile.mkstemp(prefix=".fee-", dir=self.root)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def _empty(self):
        return {"schema_version": 1, "source": self.source.model_dump(), "source_hash": self.source_hash,
                "exports": {}, "claims": {}}

    def _raw(self, export_hash):
        require(type(export_hash) is str and SHA.fullmatch(export_hash), "FEE_INBOX_CORRUPT")
        path = self._path(export_hash + ".json")
        require(path.stat().st_size <= MAX_EXPORT_BYTES, "FEE_INBOX_CORRUPT")
        raw = path.read_bytes()
        require(hashlib.sha256(raw).hexdigest() == export_hash, "FEE_INBOX_CORRUPT")
        return raw

    def _load(self):
        path = self._path("state.json")
        if not path.exists():
            return self._empty()
        try:
            require(path.stat().st_size <= MAX_STATE_BYTES, "FEE_INBOX_CORRUPT")
            state = parse_json(path.read_text(encoding="utf-8"))
            require(type(state) is dict and set(state) == set(self._empty()) | {"state_hash"}, "FEE_INBOX_CORRUPT")
            require(state.pop("state_hash") == digest(state), "FEE_INBOX_CORRUPT")
            require(type(state["schema_version"]) is int and state["schema_version"] == 1, "FEE_INBOX_CORRUPT")
            require(digest(state["source"]) == self.source_hash == state["source_hash"], "FEE_INBOX_CORRUPT")
            require(type(state["exports"]) is dict and type(state["claims"]) is dict, "FEE_INBOX_CORRUPT")
            rows = self._rows(state)
            used = set()
            for key, claim in state["claims"].items():
                require(type(claim) is dict and set(claim) == {"operation_id", "task_id", "binding_hash", "export_hash", "row_hash", "evidence", "legacy", "status"}, "FEE_INBOX_CORRUPT")
                require(claim["status"] in {"prepared", "applied"} and type(claim["legacy"]) is bool, "FEE_INBOX_CORRUPT")
                require(type(claim["binding_hash"]) is str and SHA.fullmatch(claim["binding_hash"]), "FEE_INBOX_CORRUPT")
                require(claim["operation_id"] not in used and key in rows, "FEE_INBOX_CORRUPT")
                used.add(claim["operation_id"])
                row = parse_export(self._raw(claim["export_hash"]))[key]
                require(digest(row) == claim["row_hash"], "FEE_INBOX_CORRUPT")
                evidence = claim["evidence"]
                require(type(evidence) is dict and set(evidence) == EVIDENCE_FIELDS, "FEE_INBOX_CORRUPT")
                require(evidence["operation_id"] == claim["operation_id"] and evidence["source_export_hash"] == claim["export_hash"], "FEE_INBOX_CORRUPT")
                require(claim["legacy"] or evidence["platform_log_id"] == key, "FEE_INBOX_CORRUPT")
                require(claim["legacy"] or evidence["observed_at"] == state["exports"][claim["export_hash"]]["imported_at"], "FEE_INBOX_CORRUPT")
            return state
        except Exception:
            fail("FEE_INBOX_CORRUPT")

    def _rows(self, state):
        rows = {}
        for export_hash, record in state["exports"].items():
            require(type(record) is dict and set(record) == {"imported_at"}, "FEE_INBOX_CORRUPT")
            timestamp(record["imported_at"])
            for key, row in parse_export(self._raw(export_hash)).items():
                require(key not in rows or digest(rows[key][0]) == digest(row), "FEE_EVIDENCE_CONFLICT")
                if key not in rows:
                    rows[key] = (row, export_hash)
        return rows

    @contextmanager
    def _transaction(self):
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.thread_lock:
            lock = lock_file(self._path(".state.lock"))
            try:
                state = self._load()
                before = digest(state)
                yield state
                if digest(state) != before:
                    value = state | {"state_hash": digest(state)}
                    self._atomic(self._path("state.json"), json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8"))
            finally:
                unlock_file(lock)

    def import_bytes(self, raw):
        incoming = parse_export(raw)
        export_hash = hashlib.sha256(raw).hexdigest()
        with self._transaction() as state:
            if export_hash in state["exports"]:
                require(self._raw(export_hash) == raw, "FEE_INBOX_CORRUPT")
                return export_hash
            rows = self._rows(state)
            for key, row in incoming.items():
                require(key not in rows or digest(rows[key][0]) == digest(row), "FEE_EVIDENCE_CONFLICT")
            self._atomic(self._path(export_hash + ".json"), raw)
            state["exports"][export_hash] = {"imported_at": utc_now()}
        return export_hash

    def status(self):
        state = self._load()
        return {"exports": len(state["exports"]), "prepared": sum(item["status"] == "prepared" for item in state["claims"].values()),
                "applied": sum(item["status"] == "applied" for item in state["claims"].values())}

    def _matching(self, state, operation, policy, *, export_hash=None):
        checked, receipt, body_hash, issued, received, binding = operation_basis(operation, policy)
        rows = self._rows(state) if export_hash is None else {key: (row, export_hash) for key, row in parse_export(self._raw(export_hash)).items()}
        matches = [(key, row, selected_hash) for key, (row, selected_hash) in rows.items()
                   if row["type"] == 2 and row["token_name"] == self.source.token_name and row["group"] == self.source.group
                   and row["model_name"] == policy["model"] and row["prompt_tokens"] == receipt["input_tokens"]
                   and row["completion_tokens"] == receipt["output_tokens"] and issued - 5 <= row["created_at"] <= received + 5]
        require(bool(matches), "FEE_EXPORT_PENDING")
        require(len(matches) == 1, "FEE_EXPORT_AMBIGUOUS")
        return checked, receipt, body_hash, binding, matches[0]

    def _claim_for(self, state, operation_id):
        return next(((key, claim) for key, claim in state["claims"].items() if claim["operation_id"] == operation_id), None)

    def _verified(self, state, evidence, operation, policy):
        existing = self._claim_for(state, operation["id"])
        require(existing is not None)
        key, claim = existing
        require(digest(evidence) == digest(claim["evidence"]))
        checked, receipt, body_hash, binding, match = self._matching(state, operation, policy, export_hash=claim["export_hash"])
        require(match[0] == key and claim["task_id"] == operation["task_id"] and claim["binding_hash"] == binding)
        row = parse_export(self._raw(claim["export_hash"]))[key]
        require(digest(row) == claim["row_hash"])
        expected = {"schema_version": 1, "operation_id": operation["id"], "wire_hash": operation["wire_hash"], "raw_body_sha256": body_hash,
                    "model": policy["model"], "provider_response_id": receipt["provider_response_id"], "provider_request_id": receipt["provider_request_id"],
                    "prompt_tokens": receipt["input_tokens"], "completion_tokens": receipt["output_tokens"],
                    "platform_log_id": evidence["platform_log_id"] if claim["legacy"] else key,
                    "platform_fee_cny": format(Decimal(row["quota"]) / self.source.quota_per_cny, ".6f"),
                    "source_export_hash": claim["export_hash"], "observed_at": claim["evidence"]["observed_at"]}
        require(digest(expected) == digest(evidence))
        record = observation(evidence, checked, policy)
        if claim["legacy"]:
            require(digest(record) == digest(operation["empirical"]["fee_evidence"]))
        return True

    def prepare(self, operation, policy):
        with self._transaction() as state:
            old = self._claim_for(state, operation["id"])
            if old:
                self._verified(state, old[1]["evidence"], operation, policy)
                return copy.deepcopy(old[1]["evidence"])
            checked, receipt, body_hash, binding, (key, row, export_hash) = self._matching(state, operation, policy)
            require(key not in state["claims"], "FEE_EVIDENCE_CONFLICT")
            require(operation["empirical"]["fee_evidence"] is None, "FEE_EVIDENCE_CONFLICT")
            evidence = {"schema_version": 1, "operation_id": operation["id"], "wire_hash": operation["wire_hash"], "raw_body_sha256": body_hash,
                        "model": policy["model"], "provider_response_id": receipt["provider_response_id"], "provider_request_id": receipt["provider_request_id"],
                        "prompt_tokens": receipt["input_tokens"], "completion_tokens": receipt["output_tokens"], "platform_log_id": key,
                        "platform_fee_cny": format(Decimal(row["quota"]) / self.source.quota_per_cny, ".6f"),
                        "source_export_hash": export_hash, "observed_at": state["exports"][export_hash]["imported_at"]}
            observation(evidence, checked, policy)
            state["claims"][key] = {"operation_id": operation["id"], "task_id": operation["task_id"], "binding_hash": binding,
                                    "export_hash": export_hash, "row_hash": digest(row), "evidence": evidence, "legacy": False, "status": "prepared"}
            return copy.deepcopy(evidence)

    def verifier(self, evidence, operation, policy):
        try:
            return self._verified(self._load(), evidence, operation, policy)
        except Exception:
            return False

    def register_legacy(self, operation, evidence, exportbytes, policy):
        checked, _, _, _, _, binding = operation_basis(operation, policy)
        require(type(evidence) is dict and set(evidence) in (EVIDENCE_FIELDS, EVIDENCE_FIELDS | {"evidence_hash", "platform_units"}))
        plain = {key: evidence[key] for key in EVIDENCE_FIELDS}
        record = observation(plain, checked, policy)
        require(digest(record) == digest(operation["empirical"]["fee_evidence"]) and operation["empirical"]["fee_status"] == "matched", "FEE_EVIDENCE_CONFLICT")
        if set(evidence) != EVIDENCE_FIELDS:
            require(digest(evidence) == digest(record), "FEE_EVIDENCE_CONFLICT")
        require(hashlib.sha256(exportbytes).hexdigest() == plain["source_export_hash"])
        export_hash = self.import_bytes(exportbytes)
        with self._transaction() as state:
            _, _, _, _, (key, row, _) = self._matching(state, operation, policy, export_hash=export_hash)
            require(format(Decimal(row["quota"]) / self.source.quota_per_cny, ".6f") == plain["platform_fee_cny"])
            old = self._claim_for(state, operation["id"])
            if old:
                require(old[0] == key and digest(old[1]["evidence"]) == digest(plain), "FEE_EVIDENCE_CONFLICT")
                self._verified(state, plain, operation, policy)
                return export_hash
            require(key not in state["claims"], "FEE_EVIDENCE_CONFLICT")
            state["claims"][key] = {"operation_id": operation["id"], "task_id": operation["task_id"], "binding_hash": binding,
                                    "export_hash": export_hash, "row_hash": digest(row), "evidence": plain, "legacy": True, "status": "applied"}
        return export_hash

    def _acknowledge(self, operation, policy):
        if self._claim_for(self._load(), operation["id"]) is None:
            return False
        with self._transaction() as state:
            old = self._claim_for(state, operation["id"])
            if old is None:
                return False
            plain = old[1]["evidence"]
            checked = operation_basis(operation, policy)[0]
            require(digest(observation(plain, checked, policy)) == digest(operation["empirical"]["fee_evidence"]), "FEE_EVIDENCE_CONFLICT")
            self._verified(state, plain, operation, policy)
            old[1]["status"] = "applied"
            return True

    def drain(self, runtime, *, observe_only=True):
        """Explicit application-side delivery; default does no preparation/dispatch."""
        result = {"prepared": 0, "applied": 0, "pending": 0, "conflicts": 0}
        if observe_only is not False:
            return result
        state = self._load()
        if not state["exports"] and not state["claims"]:
            return result
        payload = runtime.store.read_only()
        for operation in payload["operations"].values():
            if "empirical" not in operation or operation["raw"] is None:
                continue
            policy = payload["quotes"][operation["quote_id"]]["policy"]
            try:
                if operation["empirical"]["fee_evidence"] is not None:
                    result["applied"] += int(self._acknowledge(operation, policy))
                    continue
                evidence = self.prepare(operation, policy)
                result["prepared"] += 1
                runtime.observe_fee(operation["task_id"], operation["id"], evidence)
                current = runtime.store.read_only()["operations"][operation["id"]]
                result["applied"] += int(self._acknowledge(current, policy))
            except CreatorError as error:
                result["pending" if error.code in {"FEE_EXPORT_PENDING", "ACTION_NOT_ALLOWED", "FEE_EXPORT_AMBIGUOUS"} else "conflicts"] += 1
            except Exception:
                result["pending"] += 1
        return result
