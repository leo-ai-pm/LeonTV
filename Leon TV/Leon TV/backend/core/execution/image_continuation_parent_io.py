"""Server-derived parent allocation I/O for activation preparation only.

No caller-provided observation path chooses a write target. The original
control's authenticated task derives the existing parent location. A durable
native reservation intent must already retain the extra risk before this code
can append one allocation. Parent and native journals are not one transaction.
"""
import base64
import copy
import fcntl
import hashlib
import json
import os
import re
import stat
import tempfile
from contextlib import contextmanager
from pathlib import Path

from core.creator.store import CreatorError, digest, utc_now
from core.execution.control import _json, _private_path, _read
from core.execution.image_code_continuation import _collect_code_manifest
from core.execution.image_continuation_parent import (
    MAX_EVIDENCE_BYTES,
    MAX_PARENT_BYTES,
    _allocation,
    _checked_evidence,
    load_parent_evidence,
    verify_parent_evidence,
    verify_parent_transition,
)
from core.execution.image_control import LoadedImageControl
from core.execution.policy import money, units
from core.execution.receipts import timestamp
from core.execution.store import ExecutionStore
from core.planner.review import json_copy

ERROR_CODE = "IMAGE_CONTINUATION_PARENT_IO_UNAVAILABLE"
MAX_RECEIPT_BYTES = 1048576
REF_KIND = "image_continuation_parent_evidence_ref_v1"
ARTIFACT_KIND = "image_continuation_parent_before_artifact_v1"
RECEIPT_KIND = "image_continuation_parent_reservation_receipt_v1"
BEGIN_KIND = "image_continuation_parent_allocation_begin_v1"
PARENT_SUMMARY_FIELDS = ("evidence_hash", "authorization_hash", "frozen_parent_sha256", "current_parent_sha256",
    "old_operations_hash", "old_operation_count", "retained_risk_cny", "max_calls", "original_budget_cny")
REF_FIELDS = {"schema_version", "kind", "proposal_hash", "control_hash", "authority_hash", "evidence_hash",
    "artifact_hash", "artifact_sha256", "path_hash", "ref_hash"}
ARTIFACT_FIELDS = {"schema_version", "kind", "proposal_hash", "control_hash", "authority_hash", "evidence_hash", "evidence", "artifact_hash"}
RECEIPT_FIELDS = {"schema_version", "kind", "control_hash", "proposal_hash", "basis_hash", "authority_hash",
    "intent_id", "intent_hash", "before_evidence_hash", "before_parent_sha256", "after_parent_sha256",
    "old_operations_hash", "old_operation_count", "operation_key_hash", "allocation_hash", "request_sha256",
    "retained_risk_cny", "max_calls", "new_risk_cny", "new_max_calls", "allocation_reserved", "dispatch_authorized", "begin", "receipt_hash"}
BEGIN_FIELDS = {"schema_version", "kind", "proposal_hash", "basis_hash", "control_hash", "authority_hash", "intent_id",
    "intent_hash", "before_evidence_hash", "before_parent_sha256", "after_parent_sha256", "allocation_hash", "request_sha256",
    "operation_key_hash", "status", "http_dispatch_performed", "reserved_observed_at", "begin_hash"}


def unavailable():
    return CreatorError(ERROR_CODE, "Pinned parent continuation preparation is unavailable", 409)


def require(value):
    if not value:
        raise unavailable()


def _hash(value):
    return type(value) is str and bool(re.fullmatch(r"[0-9a-f]{64}", value))


def _id(value):
    return type(value) is str and bool(re.fullmatch(r"[0-9a-f]{32}", value))


def _same(left, right):
    return digest(left) == digest(right)


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _serialized(value, maximum=MAX_EVIDENCE_BYTES):
    value = json_copy(value, limit=maximum)
    result = json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode("utf-8")
    require(len(result) <= maximum)
    return result


def _static(control):
    return (control.backend_root / "code", control.backend_root.parent / "frontend/public", *control.static_roots)


def _authority(control):
    require(type(control) is LoadedImageControl)
    authority = control.document.authority
    raw = _read(authority.authorization_path, maximum=MAX_PARENT_BYTES)
    authorization = _json(raw)
    require(_sha(raw) == authority.authorization_sha256
        and digest(authorization) == control.document.budget_anchor["parent_authorization_hash"])
    require(authorization.get("status") == "approved_bounded_live_api_trial")
    task_id = authorization.get("task_id")
    require(type(task_id) is str and bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", task_id)))
    budget = authorization.get("budget")
    require(type(budget) is dict and budget.get("currency") == "CNY"
        and type(budget.get("hard_total")) is int and budget["hard_total"] == 100)
    path = _private_path(control.backend_root / "harness_data" / task_id / "budget-ledger.json", _static(control))
    return path, {"schema_version": 1, "kind": "image_continuation_server_parent_authority_v1",
        "control_hash": control.control_hash, "authorization_hash": digest(authorization),
        "task_id": task_id, "parent_path_hash": digest(str(path)), "original_budget_cny": "100.000000"}


def server_parent_path(control):
    """Derive the write target from original server wiring, never evidence input."""
    try:
        return _authority(control)[0]
    except Exception:
        raise unavailable() from None


def parent_authority_hash(control):
    try:
        return digest(_authority(control)[1])
    except Exception:
        raise unavailable() from None


def load_current_parent_evidence(control):
    """Fresh before-only evidence from the one server-derived live path."""
    try:
        return load_parent_evidence(control, server_parent_path(control))
    except Exception:
        raise unavailable() from None


def _proposal(proposal):
    value = json_copy(proposal, limit=16 * 1048576)
    require(type(value) is dict and type(value.get("schema_version")) is int and value["schema_version"] == 1
        and value.get("kind") == "image_continuation_proposal_v1" and type(value.get("basis")) is dict)
    require(value.get("read_only") is True and value.get("dispatch_available") is False and value.get("approval_granted") is False
        and value.get("additional_allocation_status") == "proposed_only")
    require(type(value.get("paid_calls")) is int and value["paid_calls"] == 0
        and type(value.get("automatic_followup_calls")) is int and value["automatic_followup_calls"] == 0)
    require(_hash(value.get("basis_hash")) and value["basis_hash"] == digest(value["basis"]))
    require(_hash(value.get("proposal_hash")) and value["proposal_hash"] == digest({key: item for key, item in value.items() if key != "proposal_hash"}))
    return value


def _check_control(control, proposal):
    require(type(control) is LoadedImageControl)
    control.check_private()
    require(control.verify_authority(for_dispatch=False) is True)
    value = _proposal(proposal)
    require(value["basis"]["control_hash"] == control.control_hash)
    require(value["basis"]["original_code_manifest_hash"] == digest(control.document.code_manifest))
    require(_same(_collect_code_manifest(control), value["basis"]["current_code_manifest"]))
    server_parent_path(control)
    return value


def _before(control, proposal, evidence):
    value = _checked_evidence(evidence)
    require(value["control_hash"] == control.control_hash)
    require(value["authorization_hash"] == control.document.budget_anchor["parent_authorization_hash"])
    require(value["frozen_parent_sha256"] == control.document.budget_anchor["parent_ledger_hash"])
    require(value["live_parent_path"] == str(server_parent_path(control)))
    require(value["provider_origin"] == control.document.policy["origin"])
    require(_sha(base64.b64decode(value["authorization_base64"], validate=True))
        == control.document.authority.authorization_sha256)
    require(_same({key: value[key] for key in PARENT_SUMMARY_FIELDS}, proposal["basis"]["parent"]))
    _allocation(proposal["parent_allocation"], value)
    return value


def _proof_path(control, proposal, *, receipt=False, create=False):
    directory = _private_path(control.roots["execution"] / "continuation-parent", _static(control))
    if create:
        directory.mkdir(mode=0o700, exist_ok=True)
    info = directory.stat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o700)
    suffix = ".reserved.json" if receipt else ".json"
    return directory / (proposal["proposal_hash"] + suffix)


def _sync_directory(directory):
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_once(path, value):
    """Dedicated large private artifact writer, without ReceiptStore's 2 MiB cap."""
    maximum = MAX_RECEIPT_BYTES if path.name.endswith(".reserved.json") else MAX_EVIDENCE_BYTES
    serialized = _serialized(value, maximum)
    if path.exists():
        require(_read(path, maximum=maximum) == serialized)
        return
    fd, temporary = tempfile.mkstemp(prefix=".continuation-proof-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(serialized)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path, follow_symlinks=False)
        except FileExistsError:
            require(_read(path, maximum=maximum) == serialized)
        Path(temporary).unlink()
        _sync_directory(path.parent)
        require(_read(path, maximum=maximum) == serialized)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _artifact(control, proposal, evidence):
    value = {"schema_version": 1, "kind": ARTIFACT_KIND, "proposal_hash": proposal["proposal_hash"],
        "control_hash": control.control_hash, "authority_hash": parent_authority_hash(control),
        "evidence_hash": evidence["evidence_hash"], "evidence": evidence}
    value["artifact_hash"] = digest(value)
    return value


def _reference(path, artifact):
    value = {"schema_version": 1, "kind": REF_KIND, "proposal_hash": artifact["proposal_hash"],
        "control_hash": artifact["control_hash"], "authority_hash": artifact["authority_hash"],
        "evidence_hash": artifact["evidence_hash"], "artifact_hash": artifact["artifact_hash"],
        "artifact_sha256": _sha(_read(path, maximum=MAX_EVIDENCE_BYTES)), "path_hash": digest(str(path))}
    value["ref_hash"] = digest(value)
    return value


def freeze_parent_evidence(control, proposal, evidence):
    try:
        proposal = _check_control(control, proposal)
        before = _before(control, proposal, evidence)
        verify_parent_evidence(before, control)
        path = _proof_path(control, proposal, create=True)
        artifact = _artifact(control, proposal, before)
        _write_once(path, artifact)
        return _reference(path, artifact)
    except Exception:
        raise unavailable() from None


def read_frozen_parent_evidence(control, proposal):
    """Read the immutable before proof even after the actual parent was extended."""
    try:
        proposal = _check_control(control, proposal)
        path = _proof_path(control, proposal)
        artifact = _json(_read(path, maximum=MAX_EVIDENCE_BYTES))
        require(set(artifact) == ARTIFACT_FIELDS and type(artifact["schema_version"]) is int and artifact["schema_version"] == 1
            and artifact["kind"] == ARTIFACT_KIND)
        before = _before(control, proposal, artifact["evidence"])
        require(_same(artifact, _artifact(control, proposal, before)))
        return before
    except Exception:
        raise unavailable() from None


def verify_frozen_parent_reference(control, proposal, reference):
    """Rebuild the quote's complete reference, including immutable raw bytes."""
    try:
        proposal = _proposal(proposal)
        value = json_copy(reference, limit=65536)
        require(type(value) is dict and set(value) == REF_FIELDS)
        require(type(value["schema_version"]) is int and value["schema_version"] == 1 and value["kind"] == REF_KIND)
        require(value["ref_hash"] == digest({key: item for key, item in value.items() if key != "ref_hash"}))
        before = read_frozen_parent_evidence(control, proposal)
        path = _proof_path(control, proposal)
        rebuilt = _reference(path, _artifact(control, proposal, before))
        require(_same(value, rebuilt))
        return True
    except Exception:
        raise unavailable() from None


def _reservation_intent(control, proposal):
    from core.execution.image_validity_continuation import require_reservation_intent
    payload = ExecutionStore(control.roots["execution"]).read_only()
    record = require_reservation_intent(payload, proposal)
    quote = payload["media"]["validity_continuation_quote:" + record["quote_id"]]
    verify_frozen_parent_reference(control, proposal, quote["parent_evidence_ref"])
    return record


def _fresh_reservation_authority(control, proposal, intent):
    from core.execution.image_validity_continuation import (
        require_current_continuation,
        require_reservation_intent,
    )
    payload = ExecutionStore(control.roots["execution"]).read_only()
    require(_same(require_reservation_intent(payload, proposal), intent))
    require(require_current_continuation(control, payload, proposal) is True)


def _intent(intent, proposal):
    value = json_copy(intent)
    require(type(value) is dict and _id(value.get("id")) and value.get("proposal_hash") == proposal["proposal_hash"])
    require(value.get("consent") is True and value.get("additional_risk_cny") == "1.000000"
        and type(value.get("additional_paid_calls")) is int and value["additional_paid_calls"] == 1)
    timestamp(value["created_at"])
    return value


def _expected_allocation(proposal):
    selected = proposal["parent_allocation"]
    request = selected["request"]
    return {"operation_key": selected["operation_key"], "request_sha256": digest(request), "request": request,
        "upper_fen": 100, "quote_ref": selected["quote_ref"], "status": "reserved",
        "provider_task_id": None, "actual_charge_fen": None}


@contextmanager
def _parent_lock(path):
    directory = path.parent
    require(directory.resolve() == directory)
    info = directory.stat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid() and not stat.S_IMODE(info.st_mode) & 0o022)
    fd = os.open(directory / ".budget.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and info.st_nlink == 1
            and not stat.S_IMODE(info.st_mode) & 0o022)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def _replace_parent(path, data):
    require(type(data) is bytes and len(data) <= MAX_PARENT_BYTES)
    _read(path, maximum=MAX_PARENT_BYTES)
    fd, temporary = tempfile.mkstemp(prefix=".parent-extension-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        _sync_directory(path.parent)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _new_receipt(control, proposal, before, intent, after_raw):
    selected = _expected_allocation(proposal)
    transition = verify_parent_transition(before, after_raw, proposal["parent_allocation"])
    common = {"control_hash": control.control_hash, "proposal_hash": proposal["proposal_hash"], "basis_hash": proposal["basis_hash"],
        "authority_hash": parent_authority_hash(control), "intent_id": intent["id"], "intent_hash": digest(intent),
        "before_evidence_hash": before["evidence_hash"], "before_parent_sha256": before["current_parent_sha256"],
        "after_parent_sha256": _sha(after_raw), "allocation_hash": digest(selected),
        "request_sha256": selected["request_sha256"], "operation_key_hash": digest(selected["operation_key"])}
    begin = {"schema_version": 1, "kind": BEGIN_KIND, **common,
        "status": "allocation_reserved_observed", "http_dispatch_performed": False, "reserved_observed_at": utc_now()}
    begin["begin_hash"] = digest(begin)
    value = {"schema_version": 1, "kind": RECEIPT_KIND, **common,
        "old_operations_hash": before["old_operations_hash"], "old_operation_count": before["old_operation_count"],
        "retained_risk_cny": transition["retained_risk_cny"], "max_calls": transition["max_calls"],
        "new_risk_cny": "1.000000", "new_max_calls": 1, "allocation_reserved": True, "dispatch_authorized": False, "begin": begin}
    value["receipt_hash"] = digest(value)
    validate_extension_receipt(value, proposal, intent=intent)
    return value


def validate_extension_receipt(receipt, proposal, *, intent):
    """Pure record binding only; actual live provenance needs verify_extension."""
    try:
        proposal = _proposal(proposal)
        intent = _intent(intent, proposal)
        value = json_copy(receipt, limit=MAX_RECEIPT_BYTES)
        require(type(value) is dict and set(value) == RECEIPT_FIELDS)
        require(type(value["schema_version"]) is int and value["schema_version"] == 1 and value["kind"] == RECEIPT_KIND)
        require(value["allocation_reserved"] is True and value["dispatch_authorized"] is False)
        require(_hash(value["receipt_hash"]) and value["receipt_hash"] == digest({key: item for key, item in value.items() if key != "receipt_hash"}))
        basis = proposal["basis"]
        selected = _expected_allocation(proposal)
        expected = {"control_hash": basis["control_hash"], "proposal_hash": proposal["proposal_hash"], "basis_hash": proposal["basis_hash"],
            "intent_id": intent["id"], "intent_hash": digest(intent), "before_evidence_hash": basis["parent"]["evidence_hash"],
            "before_parent_sha256": basis["parent"]["current_parent_sha256"], "old_operations_hash": basis["parent"]["old_operations_hash"],
            "old_operation_count": basis["parent"]["old_operation_count"], "operation_key_hash": digest(selected["operation_key"]),
            "allocation_hash": digest(selected), "request_sha256": selected["request_sha256"], "new_risk_cny": "1.000000", "new_max_calls": 1,
            "retained_risk_cny": money(units(basis["parent"]["retained_risk_cny"]) + 1000000), "max_calls": basis["parent"]["max_calls"] + 1}
        require(all(_same(value[key], item) for key, item in expected.items()))
        require(_hash(value["authority_hash"]) and _hash(value["after_parent_sha256"]))
        begin = value["begin"]
        require(type(begin) is dict and set(begin) == BEGIN_FIELDS and type(begin["schema_version"]) is int
            and begin["schema_version"] == 1 and begin["kind"] == BEGIN_KIND)
        require(begin["status"] == "allocation_reserved_observed" and begin["http_dispatch_performed"] is False)
        require(begin["begin_hash"] == digest({key: item for key, item in begin.items() if key != "begin_hash"}))
        require(timestamp(intent["created_at"]) <= timestamp(begin["reserved_observed_at"]))
        for key in BEGIN_FIELDS - {"schema_version", "kind", "status", "http_dispatch_performed", "reserved_observed_at", "begin_hash"}:
            require(_same(begin[key], value[key]))
        return True
    except Exception:
        raise unavailable() from None


def _verify_saved(control, proposal, before, receipt, intent, actual_raw):
    validate_extension_receipt(receipt, proposal, intent=intent)
    require(receipt["authority_hash"] == parent_authority_hash(control))
    require(receipt["before_evidence_hash"] == before["evidence_hash"])
    require(receipt["after_parent_sha256"] == _sha(actual_raw))
    transition = verify_parent_transition(before, actual_raw, proposal["parent_allocation"])
    require(receipt["allocation_hash"] == transition["allocation_hash"]
        and receipt["retained_risk_cny"] == transition["retained_risk_cny"] and receipt["max_calls"] == transition["max_calls"])
    return True


def reserve_extension(control, proposal, *, before_evidence):
    """Append at most one reserved envelope after a durable exact native intent."""
    try:
        proposal = _check_control(control, proposal)
        before = _before(control, proposal, before_evidence)
        require(_same(before, read_frozen_parent_evidence(control, proposal)))
        intent = _intent(_reservation_intent(control, proposal), proposal)
        path = server_parent_path(control)
        proof = _proof_path(control, proposal, receipt=True)
        with _parent_lock(path):
            _check_control(control, proposal)
            require(_same(_intent(_reservation_intent(control, proposal), proposal), intent))
            actual_raw = _read(path, maximum=MAX_PARENT_BYTES)
            if proof.exists():
                receipt = _json(_read(proof, maximum=MAX_RECEIPT_BYTES))
                _verify_saved(control, proposal, before, receipt, intent, actual_raw)
                return copy.deepcopy(receipt)
            if _sha(actual_raw) == before["current_parent_sha256"]:
                # Compare both the exact before bytes and the complete candidate
                # transition under the shared existing parent lock.
                after = _json(actual_raw)
                key = proposal["parent_allocation"]["operation_key"]
                require(key not in after["operations"])
                after["operations"][key] = _expected_allocation(proposal)
                after["reserved_upper_fen"] = sum(item["upper_fen"] for item in after["operations"].values())
                candidate = _serialized(after, MAX_PARENT_BYTES)
                verify_parent_transition(before, candidate, proposal["parent_allocation"])
                # An old intent is a historical fact, not current permission.
                # Check current grant, expiry, native CAS and unknown evidence
                # immediately before creating any new retained allocation.
                _fresh_reservation_authority(control, proposal, intent)
                _replace_parent(path, candidate)
                actual_raw = _read(path, maximum=MAX_PARENT_BYTES)
                require(actual_raw == candidate)
            # A prior interruption may have committed the same exact allocation
            # without its small proof. Observe it; never reserve another one.
            verify_parent_transition(before, actual_raw, proposal["parent_allocation"])
            require(_same(_intent(_reservation_intent(control, proposal), proposal), intent))
            receipt = _new_receipt(control, proposal, before, intent, actual_raw)
            _write_once(proof, receipt)
            saved = _json(_read(proof, maximum=MAX_RECEIPT_BYTES))
            require(_same(saved, receipt))
            _verify_saved(control, proposal, before, saved, intent, _read(path, maximum=MAX_PARENT_BYTES))
            return copy.deepcopy(saved)
    except Exception:
        raise unavailable() from None


def verify_extension(control, proposal, *, before_evidence, receipt):
    """Fresh actual parent/proof verification; still no dispatch authorization."""
    try:
        proposal = _check_control(control, proposal)
        before = _before(control, proposal, before_evidence)
        require(_same(before, read_frozen_parent_evidence(control, proposal)))
        intent = _intent(_reservation_intent(control, proposal), proposal)
        path = server_parent_path(control)
        proof = _proof_path(control, proposal, receipt=True)
        with _parent_lock(path):
            saved = _json(_read(proof, maximum=MAX_RECEIPT_BYTES))
            require(_same(json_copy(receipt, limit=MAX_RECEIPT_BYTES), saved))
            _verify_saved(control, proposal, before, saved, intent, _read(path, maximum=MAX_PARENT_BYTES))
        return True
    except Exception:
        raise unavailable() from None
