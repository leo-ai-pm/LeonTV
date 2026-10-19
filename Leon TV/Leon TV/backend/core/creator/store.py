"""Real private immutable draft history with atomic idempotency and revision control."""

import copy
import hashlib
import json
import os
import re
import tempfile
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from core.harness.store import lock_file, unlock_file

CONTENT_FIELDS = {"title", "script", "requirements", "production", "budget_cny"}
PRODUCTION = {"duration_seconds": 15, "ratio": "9:16", "style": "realistic_cinematic"}
KEY_RE = re.compile(r"^[A-Za-z0-9_-]{8,128}$")
ID_RE = re.compile(r"^[0-9a-f]{32}$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")


class CreatorError(Exception):
    def __init__(self, code, message, status=503, current_revision=None):
        self.code, self.message, self.status = code, message, status
        self.current_revision = current_revision


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def source_hash(script):
    return hashlib.sha256(script.encode("utf-8")).hexdigest()


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def require(condition):
    if not condition:
        raise CreatorError("CORRUPT_STORAGE", "Draft storage failed integrity checks")


def valid_content(content):
    require(isinstance(content, dict) and set(content) == CONTENT_FIELDS)
    require(isinstance(content["title"], str) and 1 <= len(content["title"]) <= 120 and content["title"] == content["title"].strip())
    require(isinstance(content["script"], str) and 1 <= len(content["script"]) <= 200000 and bool(content["script"].strip()))
    require(isinstance(content["requirements"], str) and len(content["requirements"]) <= 20000)
    require(content["production"] == PRODUCTION and type(content["production"].get("duration_seconds")) is int)
    money = content["budget_cny"]
    require(money is None or (isinstance(money, str) and bool(re.fullmatch(r"(?:0|[1-9][0-9]{0,6})\.[0-9]{2}", money)) and Decimal(money) <= Decimal("1000000")))


def snapshot_digest(document, snapshot):
    return digest({"brief_id": document["id"], "created_at": document["created_at"],
                   **{key: value for key, value in snapshot.items() if key != "snapshot_hash"}})


def validate_state(state):
    require(isinstance(state, dict) and set(state) == {"schema_version", "keys", "briefs"} and type(state["schema_version"]) is int and state["schema_version"] == 1)
    require(isinstance(state["keys"], dict) and isinstance(state["briefs"], dict) and len(state["keys"]) == len(state["briefs"]))
    for brief_id, document in state["briefs"].items():
        require(bool(ID_RE.fullmatch(brief_id)) and isinstance(document, dict))
        require(set(document) == {"id", "client_request_key", "created_at", "versions", "update_keys"} and document["id"] == brief_id)
        require(isinstance(document["created_at"], str) and datetime.fromisoformat(document["created_at"]).tzinfo is not None)
        versions, updates = document["versions"], document["update_keys"]
        require(isinstance(versions, list) and bool(versions) and isinstance(updates, dict))
        previous = None
        for revision, snapshot in enumerate(versions, 1):
            require(isinstance(snapshot, dict) and set(snapshot) == {"revision", "source_version", "source_hash", "saved_at", "content", "previous_snapshot_hash", "snapshot_hash"})
            require(type(snapshot["revision"]) is int and snapshot["revision"] == revision)
            valid_content(snapshot["content"])
            expected_source_version = 1 if previous is None else previous["source_version"] + (snapshot["content"]["script"] != previous["content"]["script"])
            require(type(snapshot["source_version"]) is int and snapshot["source_version"] == expected_source_version)
            require(snapshot["source_hash"] == source_hash(snapshot["content"]["script"]))
            require(snapshot["previous_snapshot_hash"] == (previous["snapshot_hash"] if previous else None))
            require(snapshot["snapshot_hash"] == snapshot_digest(document, snapshot))
            require(isinstance(snapshot["saved_at"], str) and datetime.fromisoformat(snapshot["saved_at"]).tzinfo is not None)
            previous = snapshot
        create_key = document["client_request_key"]
        require(isinstance(create_key, str) and bool(KEY_RE.fullmatch(create_key)))
        require(state["keys"].get(create_key) == {"brief_id": brief_id, "request_hash": digest(versions[0]["content"])})
        written = []
        for key, receipt in updates.items():
            require(bool(KEY_RE.fullmatch(key)) and isinstance(receipt, dict) and set(receipt) == {"request_hash", "written_revision"})
            number = receipt["written_revision"]
            require(type(number) is int and 2 <= number <= len(versions))
            require(receipt["request_hash"] == digest({"client_update_key": key, "expected_revision": number - 1, "content": versions[number - 1]["content"]}))
            written.append(number)
        require(sorted(written) == list(range(2, len(versions) + 1)))
    require(all(isinstance(key, str) and bool(KEY_RE.fullmatch(key)) and isinstance(index, dict)
                and index.get("brief_id") in state["briefs"] for key, index in state["keys"].items()))


class CreatorBriefStore:
    def __init__(self, root):
        self.root = Path(root).absolute()
        self.thread_lock = threading.RLock()
        self.path("state.json")

    def path(self, filename):
        path = self.root / filename
        if self.root.resolve() != self.root or path.resolve() != path or path.parent != self.root:
            raise CreatorError("PRIVATE_STORAGE_UNAVAILABLE", "Private draft symlinks or escaped paths are forbidden")
        return path

    def check_private(self, static_roots):
        self.path("state.json")
        if any(self.root.is_relative_to(Path(root).resolve()) or Path(root).resolve().is_relative_to(self.root) for root in static_roots):
            raise CreatorError("PRIVATE_STORAGE_UNAVAILABLE", "Draft storage must remain private")

    @contextmanager
    def transaction(self):
        self.path("state.json")
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.thread_lock:
            lock = lock_file(self.path(".state.lock"))
            try:
                path = self.path("state.json")
                try:
                    state = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_object) if path.exists() else {"schema_version": 1, "keys": {}, "briefs": {}}
                    validate_state(state)
                except CreatorError:
                    raise
                except (ValueError, TypeError, KeyError, AttributeError, UnicodeError, ArithmeticError):
                    raise CreatorError("CORRUPT_STORAGE", "Draft storage failed integrity checks") from None
                original = copy.deepcopy(state)
                yield state
                validate_state(state)
                if state != original or not path.exists():
                    fd, temporary = tempfile.mkstemp(prefix=".brief-", dir=self.root)
                    try:
                        with os.fdopen(fd, "w", encoding="utf-8") as stream:
                            json.dump(state, stream, ensure_ascii=False)
                            stream.flush()
                            os.fsync(stream.fileno())
                        os.replace(temporary, path)
                    finally:
                        Path(temporary).unlink(missing_ok=True)
            finally:
                unlock_file(lock)

    @staticmethod
    def summary(document, snapshot=None):
        snapshot = snapshot or document["versions"][-1]
        content = snapshot["content"]
        return {"id": document["id"], "revision": snapshot["revision"], "title": content["title"],
                "production": copy.deepcopy(content["production"]), "budget_cny": content["budget_cny"], "currency": "CNY",
                "budget_status": "pending" if content["budget_cny"] is None else "draft",
                "authorization_status": "not_authorized", "execution_enabled": False,
                "source_hash": snapshot["source_hash"], "source_version": snapshot["source_version"],
                "script_characters": len(content["script"]), "created_at": document["created_at"], "updated_at": snapshot["saved_at"]}

    def detail(self, document, revision=None):
        current = len(document["versions"])
        if revision is not None and not 1 <= revision <= current:
            raise CreatorError("NOT_FOUND", "Brief revision not found", 404)
        snapshot = document["versions"][(revision or current) - 1]
        return self.summary(document, snapshot) | {
            "script": snapshot["content"]["script"], "requirements": snapshot["content"]["requirements"],
            "client_request_key": document["client_request_key"], "current_revision": current,
            "is_current": snapshot["revision"] == current,
            "versions": [{"revision": v["revision"], "source_version": v["source_version"], "source_hash": v["source_hash"], "saved_at": v["saved_at"]} for v in document["versions"]],
        }

    @staticmethod
    def require_document(state, brief_id):
        if brief_id not in state["briefs"]:
            raise CreatorError("NOT_FOUND", "Brief not found", 404)
        return state["briefs"][brief_id]

    @staticmethod
    def append(document, content):
        previous = document["versions"][-1] if document["versions"] else None
        snapshot = {"revision": len(document["versions"]) + 1,
                    "source_version": 1 if previous is None else previous["source_version"] + (previous["content"]["script"] != content["script"]),
                    "source_hash": source_hash(content["script"]), "saved_at": utc_now(), "content": copy.deepcopy(content),
                    "previous_snapshot_hash": previous["snapshot_hash"] if previous else None}
        snapshot["snapshot_hash"] = snapshot_digest(document, snapshot)
        document["versions"].append(snapshot)

    def create(self, key, content):
        valid_content(content)
        with self.transaction() as state:
            if key in state["keys"]:
                index = state["keys"][key]
                if index["request_hash"] != digest(content):
                    raise CreatorError("KEY_CONFLICT", "Create key already belongs to another request", 409)
                return {"brief": self.detail(state["briefs"][index["brief_id"]]), "replayed": True, "applied_revision": 1}
            document = {"id": uuid.uuid4().hex, "client_request_key": key, "created_at": utc_now(), "versions": [], "update_keys": {}}
            self.append(document, content)
            state["briefs"][document["id"]] = document
            state["keys"][key] = {"brief_id": document["id"], "request_hash": digest(content)}
            return {"brief": self.detail(document), "replayed": False, "applied_revision": 1}

    def update(self, brief_id, key, expected_revision, content):
        valid_content(content)
        request_hash = digest({"client_update_key": key, "expected_revision": expected_revision, "content": content})
        with self.transaction() as state:
            document = self.require_document(state, brief_id)
            if key in document["update_keys"]:
                receipt = document["update_keys"][key]
                if receipt["request_hash"] != request_hash:
                    raise CreatorError("KEY_CONFLICT", "Update key already belongs to another request", 409)
                return {"brief": self.detail(document), "replayed": True, "applied_revision": receipt["written_revision"]}
            current = len(document["versions"])
            if current != expected_revision:
                raise CreatorError("REVISION_CONFLICT", "Refresh the saved Brief without discarding local edits", 409, current)
            self.append(document, content)
            document["update_keys"][key] = {"request_hash": request_hash, "written_revision": current + 1}
            return {"brief": self.detail(document), "replayed": False, "applied_revision": current + 1}

    def get(self, brief_id=None, key=None, revision=None):
        with self.transaction() as state:
            if key is not None:
                if key not in state["keys"]:
                    raise CreatorError("NOT_FOUND", "Brief create key not found", 404)
                return {"brief": self.summary(state["briefs"][state["keys"][key]["brief_id"]])}
            return {"brief": self.detail(self.require_document(state, brief_id), revision)}

    def list(self, offset, limit):
        with self.transaction() as state:
            summaries = sorted((self.summary(document) for document in state["briefs"].values()),
                               key=lambda brief: (brief["updated_at"], brief["id"]), reverse=True)
            end = offset + limit
            return {"briefs": summaries[offset:end], "has_more": end < len(summaries),
                    "next_offset": end if end < len(summaries) else None}
