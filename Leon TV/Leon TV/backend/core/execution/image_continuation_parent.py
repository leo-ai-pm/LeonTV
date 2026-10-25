"""Read-only original-parent evidence and a counterfactual one-slot transition.

Evidence is private input. Its self seal cannot establish current authority;
``verify_parent_evidence`` must freshly rebuild it from the original control and
explicit live file. A pure transition observation never reserves or authorizes.
"""
import base64
import hashlib
from pathlib import Path, PurePosixPath

from core.creator.store import CreatorError, digest
from core.execution.control import _call_weight, _json, _private_path, _read
from core.execution.image_control import LoadedImageControl
from core.execution.policy import money, units
from core.planner.review import json_copy

MAX_PARENT_BYTES = 8 * 1048576
MAX_EVIDENCE_BYTES = 32 * 1048576
EVIDENCE_KIND = "image_continuation_parent_evidence_v1"
ALLOCATION_KIND = "image_continuation_parent_allocation_proposal_v1"
REQUEST_KIND = "image_validity_continuation_parent_scope_v1"
TRANSITION_KIND = "image_continuation_parent_transition_observation_v1"
PARENT_FIELDS = {"schema_version", "task_id", "authorization_hash", "currency", "limit_fen", "operations"}
IDENTITY_FIELDS = PARENT_FIELDS - {"operations"}
RECORD_FIELDS = {"operation_key", "request", "request_sha256", "upper_fen", "status", "provider_task_id", "actual_charge_fen"}
LEGACY_RECORD_FIELDS = RECORD_FIELDS | {"quote_ref", "response_ref", "platform_charge_cny", "platform_charge_ref"}
EVIDENCE_FIELDS = {"schema_version", "kind", "control_hash", "authorization_hash", "provider_origin",
    "frozen_parent_sha256", "current_parent_sha256", "live_parent_path", "live_parent_path_hash",
    "old_operations_hash", "old_operation_count", "retained_risk_cny", "max_calls", "original_budget_cny",
    "parent_identity", "old_operations", "frozen_parent_base64", "current_parent_base64", "authorization_base64", "evidence_hash"}
PROPOSAL_FIELDS = {"schema_version", "kind", "operation_key", "request", "upper_cny", "quote_ref"}
REQUEST_FIELDS = {"kind", "max_new_calls", "scope_risk_cny", "prior_control_hash",
    "prior_parent_operations_hash", "prior_parent_sha256", "continuation_basis_hash"}
STATUSES = {"reserved", "submitting", "responded", "accepted", "unknown", "completed", "failed"}


def unavailable():
    return CreatorError("IMAGE_CONTINUATION_PARENT_UNAVAILABLE", "Original image parent evidence is unavailable", 409)


def require(value):
    if not value:
        raise unavailable()


def _hash(value):
    return type(value) is str and len(value) == 64 and all(char in "0123456789abcdef" for char in value)


def _text(value, maximum=10000):
    return type(value) is str and 0 < len(value) <= maximum and bool(value.encode("utf-8"))


def _copy(value, maximum=MAX_EVIDENCE_BYTES):
    return json_copy(value, limit=maximum)


def _same(left, right):
    """JSON identity keeps bool/int and int/float distinct."""
    return digest(left) == digest(right)


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _raw_json(raw):
    require(type(raw) is bytes and 0 < len(raw) <= MAX_PARENT_BYTES)
    return _json(raw)


def _operations(value):
    require(type(value) is dict and 1 <= len(value) <= 10000)
    risk, calls = 0, 0
    for key, record in value.items():
        require(_text(key, 256) and type(record) is dict and RECORD_FIELDS <= set(record) <= LEGACY_RECORD_FIELDS)
        require(type(record["operation_key"]) is str and key == record["operation_key"])
        require(type(record["request"]) is dict and bool(record["request"]))
        require(_hash(record["request_sha256"]) and digest(record["request"]) == record["request_sha256"])
        require(type(record["upper_fen"]) is int and 0 <= record["upper_fen"] <= 10000)
        require(type(record["status"]) is str and record["status"] in STATUSES)
        require(record["provider_task_id"] is None or _text(record["provider_task_id"]))
        charge = record["actual_charge_fen"]
        require(charge is None or type(charge) is int and charge >= 0)
        if record["status"] == "reserved":
            require(record["provider_task_id"] is None and charge is None)
        if "quote_ref" in record:
            require(_text(record["quote_ref"]))
        for optional in ("response_ref", "platform_charge_cny", "platform_charge_ref"):
            if optional in record:
                require(_text(record[optional]))
        # Reuse the existing conservative full-envelope weight, never the count
        # of successful requests or confirmed charges.
        risk += record["upper_fen"]
        calls += _call_weight(record)
    require(risk <= 10000 and calls <= 10000)
    return risk, calls


def _parent(raw):
    value = _raw_json(raw)
    require(PARENT_FIELDS <= set(value) <= PARENT_FIELDS | {"reserved_upper_fen"})
    require(type(value["schema_version"]) is int and value["schema_version"] == 1)
    require(_text(value["task_id"], 256) and _hash(value["authorization_hash"]))
    require(type(value["currency"]) is str and value["currency"] == "CNY")
    require(type(value["limit_fen"]) is int and value["limit_fen"] == 10000)
    risk, calls = _operations(value["operations"])
    if "reserved_upper_fen" in value:
        require(type(value["reserved_upper_fen"]) is int and value["reserved_upper_fen"] == risk)
    return value, risk, calls


def _path_text(value):
    require(type(value) is str and len(value) <= 4096)
    path = PurePosixPath(value)
    require(path.is_absolute() and str(path) == value and ".." not in path.parts)
    return value


def _encoded(raw):
    return base64.b64encode(raw).decode("ascii")


def _decoded(value):
    require(type(value) is str and len(value) <= 4 * ((MAX_PARENT_BYTES + 2) // 3))
    raw = base64.b64decode(value, validate=True)
    require(0 < len(raw) <= MAX_PARENT_BYTES and _encoded(raw) == value)
    return raw


def _evidence(*, control_hash, live_parent_path, provider_origin, authorization_raw, frozen_raw, current_raw):
    require(_hash(control_hash) and provider_origin in {"https://api.ai-tudou.net", "https://synthetic.invalid"})
    path = _path_text(live_parent_path)
    authorization = _raw_json(authorization_raw)
    require(authorization.get("status") == "approved_bounded_live_api_trial")
    budget = authorization.get("budget")
    require(type(budget) is dict and budget.get("currency") == "CNY")
    require(type(budget.get("hard_total")) is int and budget["hard_total"] == 100)
    require(authorization.get("provider_origin") == provider_origin)
    frozen, risk, calls = _parent(frozen_raw)
    current, current_risk, current_calls = _parent(current_raw)
    identity = {key: frozen[key] for key in IDENTITY_FIELDS}
    require(_same(identity, {key: current[key] for key in IDENTITY_FIELDS}))
    require(frozen["authorization_hash"] == digest(authorization))
    require(frozen["task_id"] == authorization.get("task_id"))
    require(_same(frozen["operations"], current["operations"]) and (risk, calls) == (current_risk, current_calls))
    value = {"schema_version": 1, "kind": EVIDENCE_KIND, "control_hash": control_hash,
        "authorization_hash": digest(authorization), "provider_origin": provider_origin,
        "frozen_parent_sha256": _sha(frozen_raw), "current_parent_sha256": _sha(current_raw),
        "live_parent_path": path, "live_parent_path_hash": digest(path),
        "old_operations_hash": digest(frozen["operations"]), "old_operation_count": len(frozen["operations"]),
        "retained_risk_cny": money(risk * 10000), "max_calls": calls, "original_budget_cny": "100.000000",
        "parent_identity": identity, "old_operations": frozen["operations"],
        "frozen_parent_base64": _encoded(frozen_raw), "current_parent_base64": _encoded(current_raw),
        "authorization_base64": _encoded(authorization_raw)}
    value["evidence_hash"] = digest(value)
    return value


def load_parent_evidence(control, live_parent_path):
    """Read original authority and the explicit live file, with no ledger writer.

    This preflight requires that the live parent still has exactly its original
    allocations. It deliberately cannot accept a previously extended account.
    """
    try:
        require(type(control) is LoadedImageControl)
        control.check_private()
        require(control.verify_authority(for_dispatch=False) is True)
        static = (control.backend_root / "code", control.backend_root.parent / "frontend/public", *control.static_roots)
        path = _private_path(Path(live_parent_path), static)
        authority, anchor = control.document.authority, control.document.budget_anchor
        frozen_path = _private_path(Path(authority.parent_ledger_path), static)
        authorization_path = _private_path(Path(authority.authorization_path), static)
        require(path != frozen_path and path != authorization_path and path != control.path)
        authorization_raw = _read(authorization_path, maximum=MAX_PARENT_BYTES)
        frozen_raw = _read(frozen_path, maximum=MAX_PARENT_BYTES)
        current_raw = _read(path, maximum=MAX_PARENT_BYTES)
        require(_sha(authorization_raw) == authority.authorization_sha256)
        require(_sha(frozen_raw) == anchor["parent_ledger_hash"])
        evidence = _evidence(control_hash=control.control_hash, live_parent_path=str(path),
            provider_origin=control.document.policy["origin"], authorization_raw=authorization_raw,
            frozen_raw=frozen_raw, current_raw=current_raw)
        require(evidence["authorization_hash"] == anchor["parent_authorization_hash"])
        return _copy(evidence)
    except Exception:
        raise unavailable() from None


def _checked_evidence(evidence):
    value = _copy(evidence)
    require(type(value) is dict and set(value) == EVIDENCE_FIELDS)
    require(type(value["schema_version"]) is int and value["schema_version"] == 1 and value["kind"] == EVIDENCE_KIND)
    require(_hash(value["evidence_hash"]) and value["evidence_hash"] == digest({key: item for key, item in value.items() if key != "evidence_hash"}))
    rebuilt = _evidence(control_hash=value["control_hash"], live_parent_path=value["live_parent_path"],
        provider_origin=value["provider_origin"], authorization_raw=_decoded(value["authorization_base64"]),
        frozen_raw=_decoded(value["frozen_parent_base64"]), current_raw=_decoded(value["current_parent_base64"]))
    require(_same(value, rebuilt))
    return rebuilt


def verify_parent_evidence(evidence, control):
    """Fresh full private-file reconstruction; self-sealed input is insufficient."""
    try:
        checked = _checked_evidence(evidence)
        rebuilt = load_parent_evidence(control, checked["live_parent_path"])
        require(_same(checked, rebuilt))
        return rebuilt
    except Exception:
        raise unavailable() from None


def _allocation(proposal, before):
    value = _copy(proposal, maximum=1048576)
    require(type(value) is dict and set(value) == PROPOSAL_FIELDS)
    require(type(value["schema_version"]) is int and value["schema_version"] == 1 and value["kind"] == ALLOCATION_KIND)
    require(type(value["upper_cny"]) is str and value["upper_cny"] == "1.000000")
    request = value["request"]
    require(type(request) is dict and set(request) == REQUEST_FIELDS and request["kind"] == REQUEST_KIND)
    require(type(request["max_new_calls"]) is int and request["max_new_calls"] == 1)
    require(type(request["scope_risk_cny"]) is str and request["scope_risk_cny"] == "1.000000")
    require(request["prior_control_hash"] == before["control_hash"])
    require(request["prior_parent_operations_hash"] == before["old_operations_hash"])
    require(request["prior_parent_sha256"] == before["current_parent_sha256"])
    basis = request["continuation_basis_hash"]
    require(_hash(basis))
    require(value["operation_key"] == "image-continuation-" + basis[:32])
    require(value["quote_ref"] == "image-continuation-basis:" + basis)
    return value


def verify_parent_transition(before_evidence, after_raw_bytes, proposed_request):
    """Observe a proposed old-plus-one parent transition, without reading or writing.

    The caller must separately freshly ``verify_parent_evidence`` before relying
    on the original files. Internal raw/hash reconstruction here prevents simple
    seal-only tampering, but it does not prove live provenance or grant authority.
    """
    try:
        before = _checked_evidence(before_evidence)
        proposal = _allocation(proposed_request, before)
        after, risk, calls = _parent(after_raw_bytes)
        require(_same({key: after[key] for key in IDENTITY_FIELDS}, before["parent_identity"]))
        old, key = before["old_operations"], proposal["operation_key"]
        require(key not in old and set(after["operations"]) == set(old) | {key})
        require(all(_same(record, after["operations"][old_key]) for old_key, record in old.items()))
        expected = {"operation_key": key, "request": proposal["request"], "request_sha256": digest(proposal["request"]),
            "upper_fen": 100, "quote_ref": proposal["quote_ref"], "status": "reserved",
            "provider_task_id": None, "actual_charge_fen": None}
        require(_same(after["operations"][key], expected))
        require(risk * 10000 == units(before["retained_risk_cny"]) + 1000000)
        require(calls == before["max_calls"] + 1)
        value = {"schema_version": 1, "kind": TRANSITION_KIND, "proposed_only": True, "authority_granted": False,
            "before_evidence_hash": before["evidence_hash"], "before_parent_sha256": before["current_parent_sha256"],
            "after_parent_sha256": _sha(after_raw_bytes), "allocation_proposal_hash": digest(proposal),
            "operation_key_hash": digest(key), "allocation_hash": digest(expected),
            "old_operation_count": len(old), "new_operation_count": 1,
            "retained_risk_cny": money(risk * 10000), "max_calls": calls,
            "new_risk_cny": "1.000000", "new_max_calls": 1, "original_budget_cny": "100.000000"}
        value["transition_hash"] = digest(value)
        return value
    except Exception:
        raise unavailable() from None
