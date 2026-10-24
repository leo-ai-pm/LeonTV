"""Server-owned scope configuration; loading authority never schedules execution."""
import hashlib
import os
import stat
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Literal
from urllib.parse import urlparse

from pydantic import Field, model_validator

from core.creator.store import CreatorError, digest
from core.execution.empirical import BudgetAnchor, EmpiricalExecutionPolicy, checked_anchor
from core.execution.policy import now, units
from core.planner.protocol import Strict, parse_json

CONTROL_ENV = "LEONTV_EXECUTION_CONTROL_FILE"
MESSAGE = "Server execution control is unavailable"
MAX_CONTROL = 2 * 1048576
MAX_PROOF = 64 * 1048576
SHA = r"^[0-9a-f]{64}$"
ID = r"^[0-9a-f]{32}$"
CODE_CLOSURE_DIRS = ("core/execution", "core/planner", "experiments/planner_live")
REQUIRED_CODE_FILES = (
    "core/__init__.py", "core/creator/__init__.py", "core/creator/store.py",
    "core/harness/__init__.py", "core/harness/store.py", "experiments/__init__.py",
    "core/execution/bootstrap.py", "core/execution/control.py", "core/execution/fees.py",
    "core/execution/operator.py", "core/execution/runtime.py", "core/execution/empirical.py",
    "core/execution/store.py", "core/execution/receipts.py", "core/execution/receipt_worker.py",
    "core/planner/runtime.py", "core/planner/protocol.py", "core/planner/store.py",
    "experiments/planner_live/empirical_capture.py", "experiments/planner_live/chat_receipt.py",
    "api/__init__.py", "api/app.py", "api/dependencies.py", "api/validation.py",
    "api/routers/__init__.py", "api/routers/creator.py", "api/routers/planner.py",
    "api/routers/execution.py", "config.py",
)


def unavailable():
    return CreatorError("CONTROL_UNAVAILABLE", MESSAGE, 503)


def require(value):
    if not value:
        raise unavailable()


def _absolute(value):
    path = Path(value)
    require(path.is_absolute() and path == path.resolve())
    return path


def _overlap(left, right):
    return left.is_relative_to(right) or right.is_relative_to(left)


def _private_path(path, static_roots=()):
    path = _absolute(path)
    require(not any(_overlap(path, _absolute(root)) for root in static_roots))
    return path


def _read(path, *, maximum=MAX_PROOF, private=True):
    """Read a bounded single-link file without following a symlink at any level."""
    path = _absolute(path)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(descriptor)
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size <= maximum)
        if private:
            parent = path.parent.stat()
            require(info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o600)
            require(parent.st_uid == os.getuid() and not stat.S_IMODE(parent.st_mode) & 0o022)
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            raw = stream.read(maximum + 1)
        require(len(raw) <= maximum)
        return raw
    finally:
        os.close(descriptor)


def _json(raw):
    value = parse_json(raw.decode("utf-8"))
    require(type(value) is dict)
    return value


class Authority(Strict):
    authorization_path: str
    authorization_sha256: str = Field(pattern=SHA)
    parent_ledger_path: str
    allocation_key: str = Field(min_length=1, max_length=256)


class Admission(Strict):
    task_id: str = Field(pattern=ID)
    planner_task_revision: int = Field(ge=1)
    snapshot_hash: str = Field(pattern=SHA)
    brief_id: str = Field(pattern=ID)
    brief_revision: int = Field(ge=1)
    source_hash: str = Field(pattern=SHA)


class FeeSource(Strict):
    account_namespace: str = Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")
    token_name: str = Field(min_length=1, max_length=128)
    group: str = Field(min_length=1, max_length=128)
    quota_per_cny: Literal[500000] = 500000


class ControlDocument(Strict):
    schema_version: Literal[1]
    mode: Literal["observe_only", "enabled"]
    roots: dict[str, str]
    policy: dict
    budget_anchor: dict
    authority: Authority
    code_manifest: dict[str, str] = Field(min_length=1, max_length=1024)
    admission: Admission
    fee_source: FeeSource
    observer_enabled: bool

    @model_validator(mode="after")
    def checked(self):
        require(set(self.roots) == {"creator", "planner", "execution", "fees"})
        paths = [_absolute(value) for value in self.roots.values()]
        require(all(not _overlap(left, right) for index, left in enumerate(paths) for right in paths[index + 1:]))
        _absolute(self.authority.authorization_path)
        _absolute(self.authority.parent_ledger_path)
        policy = EmpiricalExecutionPolicy.model_validate({key: value for key, value in self.policy.items() if key != "policy_hash"}).frozen()
        anchor = BudgetAnchor.model_validate({key: value for key, value in self.budget_anchor.items() if key != "parent_anchor_hash"}).frozen()
        require(digest(policy) == digest(self.policy) and digest(anchor) == digest(self.budget_anchor))
        checked_anchor(anchor, policy)
        for name, sha in self.code_manifest.items():
            path = PurePosixPath(name)
            require(bool(name) and not path.is_absolute() and str(path) == name and ".." not in path.parts and "\\" not in name)
            require(len(name) <= 500 and len(sha) == 64 and all(letter in "0123456789abcdef" for letter in sha))
        return self


def _call_weight(operation):
    require(type(operation) is dict and type(operation.get("request")) is dict)
    request = operation["request"]
    if request.get("kind") == "empirical_product_text_role_scope_envelope":
        require("max_new_calls" in request)
    if "max_new_calls" in request:
        value = request["max_new_calls"]
        require(type(value) is int and 1 <= value <= 10000)
        return value
    return 1


def _code_closure(backend, manifest):
    required = set(REQUIRED_CODE_FILES)
    for name in CODE_CLOSURE_DIRS:
        directory = _absolute(backend / name)
        require(directory.is_dir())
        paths = list(directory.rglob("*.py"))
        require(bool(paths) and len(paths) <= 1024)
        required.update(path.relative_to(backend).as_posix() for path in paths)
    require(required <= set(manifest))


@dataclass(frozen=True)
class LoadedControl:
    document: ControlDocument
    control_hash: str
    path: Path
    _backend_root: Path | None = field(default=None, repr=False)
    _static_roots: tuple = field(default=(), repr=False)

    @property
    def roots(self):
        return {key: Path(value) for key, value in self.document.roots.items()}

    def check_private(self, static_roots):
        try:
            static = tuple(Path(root).resolve() for root in static_roots)
            paths = [self.path, self.document.authority.authorization_path, self.document.authority.parent_ledger_path, *self.roots.values()]
            for path in paths:
                _private_path(path, static)
            return True
        except Exception:
            raise unavailable() from None

    def _paths(self, backend_root):
        backend = _absolute(backend_root)
        static = (backend / "code", *self._static_roots)
        self.check_private(static)
        proofs = [self.path, Path(self.document.authority.authorization_path), Path(self.document.authority.parent_ledger_path)]
        require(len(set(proofs)) == 3)
        require(all(not proof.is_relative_to(root) for proof in proofs for root in self.roots.values()))
        return backend

    def _source(self):
        from core.planner.store import validate_state

        state = _json(_read(self.roots["planner"] / "state.json"))
        validate_state(state)
        document = state["tasks"][self.document.admission.task_id]
        self._check_source(document["id"], document["head_revision"], document["request"]["mode"], document["source"])

    def _check_source(self, task_id, revision, mode, source):
        admission = self.document.admission
        require(task_id == admission.task_id and type(revision) is int and revision == admission.planner_task_revision and mode == "normal")
        require(all(source[key] == getattr(admission, key) for key in ("snapshot_hash", "brief_id", "brief_revision", "source_hash")))

    def check_admission(self, planner, task_id):
        try:
            require(task_id == self.document.admission.task_id)
            require(Path(planner.store.root) == self.roots["planner"] and Path(planner.brief_store.root) == self.roots["creator"])
            task = planner.get(task_id)["task"]
            source = planner.source(task_id)["source"]
            self._check_source(task["id"], task["revision"], task["mode"], source)
            return True
        except Exception:
            raise unavailable() from None

    def verify_authority(self, backend_root, for_dispatch=False):
        try:
            require(type(for_dispatch) is bool)
            backend = self._paths(backend_root)
            raw = _read(self.path, maximum=MAX_CONTROL)
            require(hashlib.sha256(raw).hexdigest() == self.control_hash)
            parsed = _json(raw)
            document = ControlDocument.model_validate(parsed)
            require(digest(document.model_dump()) == digest(parsed))
            require(digest(document.model_dump()) == digest(self.document.model_dump()))
            authority, policy, anchor = document.authority, document.policy, document.budget_anchor
            authorization_raw = _read(authority.authorization_path)
            require(hashlib.sha256(authorization_raw).hexdigest() == authority.authorization_sha256)
            authorization = _json(authorization_raw)
            require(digest(authorization) == anchor["parent_authorization_hash"])
            require(authorization["status"] == "approved_bounded_live_api_trial")
            require(authorization["budget"]["currency"] == "CNY" and type(authorization["budget"]["hard_total"]) is int and authorization["budget"]["hard_total"] == 100)
            require(units(anchor["original_budget_cny"]) == 100000000)
            endpoint = urlparse(policy["endpoint"])
            require(f"{endpoint.scheme}://{endpoint.netloc}" == authorization["provider_origin"])
            ledger_raw = _read(authority.parent_ledger_path)
            require(hashlib.sha256(ledger_raw).hexdigest() == anchor["parent_ledger_hash"])
            ledger = _json(ledger_raw)
            require(type(ledger["schema_version"]) is int and ledger["schema_version"] == 1 and ledger["currency"] == "CNY")
            require(ledger["authorization_hash"] == anchor["parent_authorization_hash"])
            require(ledger["task_id"] == authorization["task_id"])
            require(type(ledger["limit_fen"]) is int and ledger["limit_fen"] * 10000 == units(anchor["original_budget_cny"]))
            operations = ledger["operations"]
            require(type(operations) is dict and len(operations) <= 10000 and authority.allocation_key in operations)
            selected = operations[authority.allocation_key]
            require(type(selected) is dict and digest(selected) == anchor["parent_allocation_hash"])
            require(selected["operation_key"] == authority.allocation_key and type(selected["request"]) is dict)
            request = selected["request"]
            require(selected["request_sha256"] == digest(request))
            require(type(selected["upper_fen"]) is int and selected["upper_fen"] * 10000 == units(anchor["scope_risk_cny"]))
            other = [operation for key, operation in operations.items() if key != authority.allocation_key]
            require(all(type(operation) is dict and type(operation.get("upper_fen")) is int and 0 <= operation["upper_fen"] <= 10000 for operation in operations.values()))
            require(sum(operation["upper_fen"] for operation in other) * 10000 == units(anchor["opening_exposure_cny"]))
            require(sum(operation["upper_fen"] for operation in operations.values()) <= ledger["limit_fen"])
            require(anchor["opening_call_count"] >= sum(_call_weight(operation) for operation in other))
            require(digest(document.code_manifest) == request["backend_manifest_hash"])
            expected = {"model": policy["model"], "endpoint": policy["endpoint"], "source_hash": document.admission.source_hash,
                        "role_workflow": policy["role_workflow"], "max_new_calls": policy["max_calls"],
                        "per_call_risk_cny": policy["per_call_risk_cny"], "risk_basis": policy["risk_basis"]}
            for key, value in expected.items():
                require(key in request and type(request[key]) is type(value) and request[key] == value)
            amounts = {"original_budget_cny": "original_budget_cny", "opening_retained_risk_cny": "opening_exposure_cny", "scope_risk_cny": "scope_risk_cny"}
            for key, field_name in amounts.items():
                if key in request:
                    require(type(request[key]) is str and units(request[key]) == units(anchor[field_name]))
            if "opening_budget_record_count" in request:
                require(type(request["opening_budget_record_count"]) is int and request["opening_budget_record_count"] == len(other))
            if "chat_output_parameter" in request:
                require(request["chat_output_parameter"] == policy["chat_output_parameter"])
            if "admission" in request:
                require(type(request["admission"]) is dict and digest(request["admission"]) == digest(document.admission.model_dump()))
            if for_dispatch:
                require(document.mode == "enabled" and datetime.fromisoformat(policy["valid_until"]) > now())
                require(selected["status"] == "reserved" and selected.get("provider_task_id") is None and selected.get("actual_charge_fen") is None)
                require(request.get("scope_status") == "open" and request.get("admission") == document.admission.model_dump())
                require(all(key in request for key in ("chat_output_parameter", "scope_risk_cny", "original_budget_cny", "opening_retained_risk_cny")))
                _code_closure(backend, document.code_manifest)
                total_bytes = 0
                for name, sha in document.code_manifest.items():
                    source_path = backend / name
                    require(source_path.is_relative_to(backend))
                    code = _read(source_path, maximum=4 * 1048576, private=False)
                    total_bytes += len(code)
                    require(total_bytes <= 128 * 1048576 and hashlib.sha256(code).hexdigest() == sha)
                self._source()
            return True
        except Exception:
            raise unavailable() from None

    def guard(self, anchor, planner, execution_store):
        try:
            require(self._backend_root is not None and digest(anchor) == digest(self.document.budget_anchor))
            require(Path(execution_store.root) == self.roots["execution"])
            self.verify_authority(self._backend_root, for_dispatch=True)
            self.check_admission(planner, self.document.admission.task_id)
            payload = execution_store.read_only()
            require(all(task["id"] == self.document.admission.task_id and task["snapshot_hash"] == self.document.admission.snapshot_hash for task in payload["tasks"].values()))
            require(all(account.get("parent_anchor_hash") == anchor["parent_anchor_hash"] for account in payload["accounts"].values()))
            return True
        except Exception:
            return False


def read_control(path, backend_root, *, static_roots=()):
    try:
        path, backend = _absolute(path), _absolute(backend_root)
        raw = _read(path, maximum=MAX_CONTROL)
        parsed = _json(raw)
        document = ControlDocument.model_validate(parsed)
        require(digest(document.model_dump()) == digest(parsed))
        loaded = LoadedControl(document, hashlib.sha256(raw).hexdigest(), path, backend, tuple(static_roots))
        loaded.verify_authority(backend, for_dispatch=document.mode == "enabled")
        return loaded
    except Exception:
        raise unavailable() from None


def load_control(backend_root, *, environ=None, static_roots=()):
    environment = os.environ if environ is None else environ
    path = environment.get(CONTROL_ENV)
    if path is None or path == "":
        return None
    return read_control(path, backend_root, static_roots=static_roots)
