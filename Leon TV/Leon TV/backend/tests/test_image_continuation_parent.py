"""Strict read-only parent evidence and counterfactual one-call transition checks."""

import base64
import copy
import hashlib
import json
import os
import socket
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.creator.store import CreatorError, digest
from core.execution import image_continuation_parent as parent
from core.execution.image_control import LoadedImageControl


def raw(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()


def private(path, value):
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    data = raw(value)
    path.write_bytes(data)
    path.chmod(0o600)
    return hashlib.sha256(data).hexdigest()


def record(key, upper_fen=100, max_calls=1):
    request = {"kind": "fictional_retained_scope", "max_new_calls": max_calls}
    return {
        "operation_key": key,
        "request": request,
        "request_sha256": digest(request),
        "upper_fen": upper_fen,
        "quote_ref": "fictional original authorization",
        "status": "reserved",
        "provider_task_id": None,
        "actual_charge_fen": None,
    }


@pytest.fixture
def original(tmp_path, monkeypatch):
    root = tmp_path / "original-parent"
    root.mkdir(mode=0o700)
    authorization = {
        "task_id": "fictional-original-task",
        "status": "approved_bounded_live_api_trial",
        "budget": {"currency": "CNY", "hard_total": 100},
        "provider_origin": "https://synthetic.invalid",
    }
    operations = {
        "retained-original": record("retained-original", 6937, 41),
        "text-envelope": record("text-envelope", 600, 6),
        "asset": record("asset"),
        "image": record("image", 400, 4),
        "unused-retained": record("unused-retained"),
    }
    operations["retained-original"]["status"] = "unknown"
    ledger = {
        "schema_version": 1,
        "task_id": authorization["task_id"],
        "authorization_hash": digest(authorization),
        "currency": "CNY",
        "limit_fen": 10000,
        "operations": operations,
        "reserved_upper_fen": 8137,
    }
    authorization_path = root / "authorization.json"
    frozen_path = root / "frozen-parent.json"
    live_path = root / "budget-ledger.json"
    authorization_sha = private(authorization_path, authorization)
    frozen_sha = private(frozen_path, ledger)
    private(live_path, ledger)
    control_path = root / "control.json"
    control_sha = private(control_path, {"kind": "fictional-unit-control"})
    backend = root / "backend"
    backend.mkdir()
    roots = {key: str(root / key) for key in ("creator", "planner", "execution")}
    for path in roots.values():
        Path(path).mkdir()
    authority = SimpleNamespace(
        authorization_path=str(authorization_path),
        authorization_sha256=authorization_sha,
        parent_ledger_path=str(frozen_path),
        allocation_begin_path=str(root / "begin.json"),
    )
    anchor = {
        "parent_authorization_hash": digest(authorization),
        "parent_ledger_hash": frozen_sha,
        "parent_allocation_hash": digest(operations["image"]),
        "scope_risk_cny": "4.000000",
        "opening_risk_cny": "77.370000",
        "opening_paid_call_count": 49,
    }
    document = SimpleNamespace(
        authority=authority,
        budget_anchor=anchor,
        policy={"origin": "https://synthetic.invalid"},
        roots=roots,
    )
    control = LoadedImageControl(
        document=document, control_hash=control_sha, path=control_path, backend_root=backend
    )
    calls = []

    def authority_check(self, backend_root=None, *, for_dispatch=False):
        assert self is control and for_dispatch is False
        calls.append(for_dispatch)
        return True

    # Isolate file/parent reconstruction; the real control verifier has separate
    # native tests and is not replaced by evidence's self-declared hashes.
    monkeypatch.setattr(LoadedImageControl, "verify_authority", authority_check)
    return SimpleNamespace(
        root=root,
        control=control,
        ledger=ledger,
        authorization=authorization,
        live=live_path,
        frozen=frozen_path,
        authorization_path=authorization_path,
        authority_calls=calls,
    )


def allocation(evidence):
    basis = "a" * 64
    request = {
        "kind": "image_validity_continuation_parent_scope_v1",
        "max_new_calls": 1,
        "scope_risk_cny": "1.000000",
        "prior_control_hash": evidence["control_hash"],
        "prior_parent_operations_hash": evidence["old_operations_hash"],
        "prior_parent_sha256": evidence["current_parent_sha256"],
        "continuation_basis_hash": basis,
    }
    return {
        "schema_version": 1,
        "kind": "image_continuation_parent_allocation_proposal_v1",
        "operation_key": "image-continuation-" + basis[:32],
        "request": request,
        "upper_cny": "1.000000",
        "quote_ref": "image-continuation-basis:" + basis,
    }


def after_for(case, proposal):
    after = copy.deepcopy(case.ledger)
    after["operations"][proposal["operation_key"]] = {
        "operation_key": proposal["operation_key"],
        "request": copy.deepcopy(proposal["request"]),
        "request_sha256": digest(proposal["request"]),
        "upper_fen": 100,
        "quote_ref": proposal["quote_ref"],
        "status": "reserved",
        "provider_task_id": None,
        "actual_charge_fen": None,
    }
    after["reserved_upper_fen"] = 8237
    return after


def reseal(evidence):
    evidence["evidence_hash"] = digest(
        {key: value for key, value in evidence.items() if key != "evidence_hash"}
    )
    return evidence


def test_load_is_read_only_retains_full_unknown_and_unused_risk(original, monkeypatch):
    before = {path: path.read_bytes() for path in original.root.rglob("*") if path.is_file()}
    forbidden = []

    def deny(*args, **kwargs):
        forbidden.append(True)
        raise AssertionError("Parent evidence attempted mutation or network")

    from core.execution.store import ExecutionStore
    from experiments.tudou_ab.ledger import BudgetLedger

    for owner, names in (
        (BudgetLedger, ("__init__", "reserve", "snapshot", "begin", "record")),
        (ExecutionStore, ("transaction", "execution_lock")),
        (socket, ("getaddrinfo", "create_connection")),
        (socket.socket, ("connect", "connect_ex")),
    ):
        for name in names:
            monkeypatch.setattr(owner, name, deny)
    evidence = parent.load_parent_evidence(original.control, original.live)
    assert (
        evidence["schema_version"] == 1
        and evidence["kind"] == "image_continuation_parent_evidence_v1"
    )
    assert evidence["retained_risk_cny"] == "81.370000" and evidence["max_calls"] == 53
    assert evidence["original_budget_cny"] == "100.000000" and evidence["old_operation_count"] == 5
    assert evidence["old_operations"] == original.ledger["operations"]
    assert evidence["old_operations"]["retained-original"]["status"] == "unknown"
    assert evidence["live_parent_path"] == str(original.live)
    assert evidence["live_parent_path_hash"] == digest(str(original.live))
    assert evidence["evidence_hash"] == digest(
        {key: value for key, value in evidence.items() if key != "evidence_hash"}
    )
    assert parent.verify_parent_evidence(evidence=evidence, control=original.control) == evidence
    assert original.authority_calls == [False, False] and not forbidden
    assert {
        path: path.read_bytes() for path in original.root.rglob("*") if path.is_file()
    } == before


def test_live_can_differ_in_encoding_without_changing_the_frozen_allocations(original):
    original.live.write_text(json.dumps(original.ledger, indent=4))
    evidence = parent.load_parent_evidence(original.control, original.live)
    assert evidence["current_parent_sha256"] != evidence["frozen_parent_sha256"]
    assert evidence["old_operations_hash"] == digest(original.ledger["operations"])


@pytest.mark.parametrize(
    "change",
    [
        "old_risk",
        "old_unknown",
        "old_key",
        "new_allocation",
        "task",
        "auth",
        "limit",
        "summary",
        "float",
        "bool",
        "request_hash",
        "unbounded_calls",
    ],
)
def test_load_rejects_live_parent_drift_from_immutable_original(original, change):
    altered = copy.deepcopy(original.ledger)
    if change == "old_risk":
        altered["operations"]["image"]["upper_fen"] = 0
    elif change == "old_unknown":
        altered["operations"]["retained-original"]["status"] = "completed"
    elif change == "old_key":
        altered["operations"]["image"]["operation_key"] = "copied-key"
    elif change == "new_allocation":
        altered["operations"]["extra"] = record("extra")
    elif change == "task":
        altered["task_id"] = "other-task"
    elif change == "auth":
        altered["authorization_hash"] = "f" * 64
    elif change == "limit":
        altered["limit_fen"] = 20000
    elif change == "summary":
        altered["reserved_upper_fen"] = 0
    elif change == "float":
        altered["operations"]["asset"]["upper_fen"] = 100.0
    elif change == "bool":
        altered["schema_version"] = True
    elif change == "request_hash":
        altered["operations"]["asset"]["request_sha256"] = "0" * 64
    else:
        altered["operations"]["asset"]["request"]["max_new_calls"] = 0
    private(original.live, altered)
    with pytest.raises(CreatorError):
        parent.load_parent_evidence(original.control, original.live)


@pytest.mark.parametrize(
    "fault", ["permissions", "symlink", "hardlink", "static", "duplicate", "oversize"]
)
def test_load_enforces_private_bounded_nosymlink_single_link_json(original, fault):
    path = original.live
    if fault == "permissions":
        path.chmod(0o644)
    elif fault == "symlink":
        path = original.root / "linked-parent.json"
        path.symlink_to(original.live)
    elif fault == "hardlink":
        path = original.root / "hard-parent.json"
        os.link(original.live, path)
    elif fault == "static":
        path = original.control.backend_root / "code" / "budget-ledger.json"
        private(path, original.ledger)
    elif fault == "duplicate":
        path.write_bytes(b'{"schema_version":1,"schema_version":1}')
    else:
        path.write_bytes(b" " * (parent.MAX_PARENT_BYTES + 1))
    with pytest.raises(CreatorError):
        parent.load_parent_evidence(original.control, path)


def test_explicit_live_parent_must_be_distinct_from_frozen_snapshot(original):
    with pytest.raises(CreatorError):
        parent.load_parent_evidence(original.control, original.frozen)


def test_evidence_reverification_reads_current_live_parent_not_self_seal(original):
    evidence = parent.load_parent_evidence(original.control, original.live)
    live = copy.deepcopy(original.ledger)
    live["operations"]["retained-original"]["status"] = "failed"
    private(original.live, live)
    with pytest.raises(CreatorError):
        parent.verify_parent_evidence(evidence=evidence, control=original.control)


@pytest.mark.parametrize(
    "field,value",
    [
        ("retained_risk_cny", "0.000000"),
        ("max_calls", 0),
        ("old_operation_count", True),
        ("current_parent_sha256", "0" * 64),
        ("authorization_hash", "0" * 64),
        ("live_parent_path_hash", "0" * 64),
    ],
)
def test_self_resealed_evidence_cannot_change_derived_original_facts(original, field, value):
    evidence = parent.load_parent_evidence(original.control, original.live)
    evidence[field] = value
    reseal(evidence)
    with pytest.raises(CreatorError):
        parent.verify_parent_evidence(evidence=evidence, control=original.control)
    with pytest.raises(CreatorError):
        parent.verify_parent_transition(evidence, raw(original.ledger), allocation(evidence))


def test_transition_reconstructs_unique_one_call_without_authorization_or_io(original, monkeypatch):
    evidence = parent.load_parent_evidence(original.control, original.live)
    proposal = allocation(evidence)
    after = after_for(original, proposal)
    before = raw(evidence)
    path_before = original.live.read_bytes()
    monkeypatch.setattr(
        Path, "read_bytes", lambda *args: pytest.fail("Pure transition attempted file read")
    )
    monkeypatch.setattr(
        Path,
        "read_text",
        lambda *args, **kwargs: pytest.fail("Pure transition attempted file read"),
    )
    observation = parent.verify_parent_transition(evidence, raw(after), proposal)
    assert observation["proposed_only"] is True and observation["authority_granted"] is False
    assert observation["retained_risk_cny"] == "82.370000" and observation["max_calls"] == 54
    assert observation["new_operation_count"] == 1 and observation["old_operation_count"] == 5
    assert observation["new_risk_cny"] == "1.000000" and observation["new_max_calls"] == 1
    assert observation["transition_hash"] == digest(
        {key: value for key, value in observation.items() if key != "transition_hash"}
    )
    assert raw(evidence) == before
    # Preserve original bytes without invoking the patched read methods.
    with original.live.open("rb") as stream:
        assert stream.read() == path_before


@pytest.mark.parametrize(
    "change",
    [
        "missing",
        "two_new",
        "old_deleted",
        "old_changed",
        "copied_id",
        "old_unknown",
        "key",
        "risk_zero",
        "risk_double",
        "calls_zero",
        "calls_double",
        "calls_bool",
        "request_hash",
        "request_changed",
        "started",
        "provider",
        "charged",
        "quote_ref",
        "extra_field",
        "limit",
        "task",
        "derived_total",
        "over_budget",
    ],
)
def test_transition_rejects_reset_reuse_extra_allocations_or_unbegun_record_drift(original, change):
    evidence = parent.load_parent_evidence(original.control, original.live)
    proposal = allocation(evidence)
    after = after_for(original, proposal)
    item = after["operations"][proposal["operation_key"]]
    if change == "missing":
        del after["operations"][proposal["operation_key"]]
    elif change == "two_new":
        after["operations"]["another"] = record("another")
    elif change == "old_deleted":
        del after["operations"]["asset"]
    elif change == "old_changed":
        after["operations"]["asset"]["upper_fen"] = 0
    elif change == "copied_id":
        item["operation_key"] = "image"
    elif change == "old_unknown":
        after["operations"]["retained-original"]["status"] = "completed"
    elif change == "key":
        after["operations"]["arbitrary-key"] = after["operations"].pop(proposal["operation_key"])
    elif change == "risk_zero":
        item["upper_fen"] = 0
    elif change == "risk_double":
        item["upper_fen"] = 200
    elif change == "calls_zero":
        item["request"]["max_new_calls"] = 0
    elif change == "calls_double":
        item["request"]["max_new_calls"] = 2
    elif change == "calls_bool":
        item["request"]["max_new_calls"] = True
    elif change == "request_hash":
        item["request_sha256"] = "f" * 64
    elif change == "request_changed":
        item["request"]["continuation_basis_hash"] = "f" * 64
    elif change == "started":
        item["status"] = "submitting"
    elif change == "provider":
        item["provider_task_id"] = "invented-task"
    elif change == "charged":
        item["actual_charge_fen"] = 0
    elif change == "quote_ref":
        item["quote_ref"] = "other-approval"
    elif change == "extra_field":
        item["fake_permission"] = True
    elif change == "limit":
        after["limit_fen"] = 20000
    elif change == "task":
        after["task_id"] = "other-task"
    elif change == "derived_total":
        after["reserved_upper_fen"] = 0
    else:
        item["upper_fen"] = 10000
        after["reserved_upper_fen"] = 18137
    with pytest.raises(CreatorError):
        parent.verify_parent_transition(evidence, raw(after), proposal)


@pytest.mark.parametrize(
    "field,value",
    [
        ("upper_cny", "0.000000"),
        ("upper_cny", "1"),
        ("schema_version", True),
        ("kind", "other-kind"),
        ("operation_key", "unstable-key"),
        ("quote_ref", "other-basis"),
    ],
)
def test_allocation_proposal_is_exactly_bound_and_canonical(original, field, value):
    evidence = parent.load_parent_evidence(original.control, original.live)
    proposal = allocation(evidence)
    after = after_for(original, proposal)
    proposal[field] = value
    with pytest.raises(CreatorError):
        parent.verify_parent_transition(evidence, raw(after), proposal)


@pytest.mark.parametrize(
    "field,value",
    [
        ("prior_control_hash", "f" * 64),
        ("prior_parent_operations_hash", "f" * 64),
        ("prior_parent_sha256", "f" * 64),
        ("continuation_basis_hash", "invalid"),
        ("max_new_calls", 1.0),
        ("scope_risk_cny", "0.000000"),
    ],
)
def test_rehashed_new_allocation_cannot_switch_original_or_basis_bindings(original, field, value):
    evidence = parent.load_parent_evidence(original.control, original.live)
    proposal = allocation(evidence)
    proposal["request"][field] = value
    after = after_for(original, proposal)
    with pytest.raises(CreatorError):
        parent.verify_parent_transition(evidence, raw(after), proposal)


def test_pure_transition_rejects_duplicate_keys_and_non_json_evidence(original):
    evidence = parent.load_parent_evidence(original.control, original.live)
    proposal = allocation(evidence)
    for blob in (b'{"schema_version":1,"schema_version":1}', b"NaN", b"[]", b"\xff"):
        with pytest.raises(CreatorError):
            parent.verify_parent_transition(evidence, blob, proposal)
    modified = copy.deepcopy(evidence)
    modified["old_operations"]["asset"]["request"]["tuple"] = ("repaired-json",)
    with pytest.raises(CreatorError):
        parent.verify_parent_transition(modified, raw(after_for(original, proposal)), proposal)


def test_frozen_raw_and_authorization_cannot_be_resealed_into_different_risk(original):
    evidence = parent.load_parent_evidence(original.control, original.live)
    frozen = json.loads(base64.b64decode(evidence["frozen_parent_base64"]))
    frozen["operations"]["retained-original"]["upper_fen"] = 0
    evidence["frozen_parent_base64"] = base64.b64encode(raw(frozen)).decode()
    reseal(evidence)
    with pytest.raises(CreatorError):
        parent.verify_parent_transition(evidence, raw(original.ledger), allocation(evidence))
