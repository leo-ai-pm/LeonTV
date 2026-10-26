"""Server-derived parent writes, immutable before proofs and idempotent failure recovery."""
import copy
import json
import os
import socket
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_image_continuation_parent import allocation, private, raw
from test_image_continuation_parent import original as original

from core.creator.store import CreatorError, digest
from core.execution import image_continuation_parent as parent
from core.execution import image_continuation_parent_io as parent_io


@pytest.fixture
def case(original, monkeypatch):
    actual = original.control.backend_root / "harness_data" / original.authorization["task_id"] / "budget-ledger.json"
    private(actual, original.ledger)
    original.live = actual
    code = {"fictional/current.py": "b" * 64}
    original.control.document.code_manifest = {"fictional/old.py": "c" * 64}
    monkeypatch.setattr(parent_io, "_collect_code_manifest", lambda control: code)
    evidence = parent.load_parent_evidence(original.control, actual)
    request = allocation(evidence)
    basis = {"control_hash": original.control.control_hash, "current_code_manifest": code,
        "original_code_manifest_hash": digest(original.control.document.code_manifest),
        "parent": {key: evidence[key] for key in ("evidence_hash", "authorization_hash", "frozen_parent_sha256",
            "current_parent_sha256", "old_operations_hash", "old_operation_count", "retained_risk_cny", "max_calls", "original_budget_cny")},
        "budget": {"inherited_retained_risk_cny": "81.370000", "inherited_max_calls": 53,
            "additional_risk_cny": "1.000000", "proposed_retained_risk_cny": "82.370000", "proposed_max_calls": 54}}
    request["request"]["continuation_basis_hash"] = digest(basis)
    request["operation_key"] = "image-continuation-" + digest(basis)[:32]
    request["quote_ref"] = "image-continuation-basis:" + digest(basis)
    proposal = {"schema_version": 1, "kind": "image_continuation_proposal_v1", "basis": basis,
        "basis_hash": digest(basis), "parent_allocation": request, "read_only": True,
        "dispatch_available": False, "approval_granted": False, "additional_allocation_status": "proposed_only",
        "paid_calls": 0, "automatic_followup_calls": 0}
    proposal["proposal_hash"] = digest(proposal)
    intent = {"schema_version": 1, "kind": "validity_continuation_intent", "id": "d" * 32,
        "quote_id": "e" * 32, "proposal_hash": proposal["proposal_hash"], "consent": True,
        "additional_risk_cny": "1.000000", "additional_paid_calls": 1, "created_at": "2026-10-25T10:00:00+00:00"}
    monkeypatch.setattr(parent_io, "_reservation_intent", lambda control, value: copy.deepcopy(intent))
    monkeypatch.setattr(parent_io, "_fresh_reservation_authority", lambda control, value, current: None)
    return SimpleNamespace(**vars(original), evidence=evidence, proposal=proposal, intent=intent)


def freeze(case):
    return parent_io.freeze_parent_evidence(case.control, case.proposal, case.evidence)


def test_server_path_comes_from_original_authorization_and_backend_not_observation_copy(case):
    assert parent_io.server_parent_path(case.control) == case.live
    assert parent_io.load_current_parent_evidence(case.control) == case.evidence
    copied = case.root / "parent-copy.json"
    private(copied, case.ledger)
    copied_evidence = parent.load_parent_evidence(case.control, copied)
    with pytest.raises(CreatorError):
        parent_io.freeze_parent_evidence(case.control, case.proposal, copied_evidence)
    assert copied.read_bytes() == raw(case.ledger)


def test_before_artifact_large_dedicated_writer_private_first_write_and_replay(case):
    marker = case.evidence["old_operations"]["retained-original"]["quote_ref"]
    assert marker
    before = case.live.read_bytes()
    ref = freeze(case)
    assert ref["kind"] == "image_continuation_parent_evidence_ref_v1"
    assert ref["ref_hash"] == digest({k: v for k, v in ref.items() if k != "ref_hash"})
    directory = Path(case.control.roots["execution"]) / "continuation-parent"
    path = directory / (case.proposal["proposal_hash"] + ".json")
    assert directory.stat().st_mode & 0o777 == 0o700
    assert path.stat().st_mode & 0o777 == 0o600
    stored = path.read_bytes()
    assert parent_io.read_frozen_parent_evidence(case.control, case.proposal) == case.evidence
    assert freeze(case) == ref and path.read_bytes() == stored
    assert case.live.read_bytes() == before
    assert str(path) not in json.dumps(ref)


def test_missing_frozen_proof_read_does_not_create_directory(case):
    directory = Path(case.control.roots["execution"]) / "continuation-parent"
    with pytest.raises(CreatorError):
        parent_io.read_frozen_parent_evidence(case.control, case.proposal)
    assert not directory.exists()


def test_reserve_is_exact_one_call_and_replays_without_rewriting_original_parent_or_begin(case):
    freeze(case)
    old = case.live.read_bytes()
    receipt = parent_io.reserve_extension(case.control, case.proposal, before_evidence=case.evidence)
    after = case.live.read_bytes()
    assert after != old
    current = json.loads(after)
    assert len(current["operations"]) == 6 and current["reserved_upper_fen"] == 8237
    assert all(current["operations"][key] == value for key, value in case.ledger["operations"].items())
    assert receipt["allocation_reserved"] is True and receipt["dispatch_authorized"] is False
    assert receipt["retained_risk_cny"] == "82.370000" and receipt["max_calls"] == 54
    assert receipt["begin"]["status"] == "allocation_reserved_observed"
    assert receipt["begin"]["http_dispatch_performed"] is False
    assert parent_io.validate_extension_receipt(receipt, case.proposal, intent=case.intent) is True
    assert parent_io.verify_extension(case.control, case.proposal, before_evidence=case.evidence, receipt=receipt) is True
    assert parent_io.reserve_extension(case.control, case.proposal, before_evidence=case.evidence) == receipt
    assert case.live.read_bytes() == after and case.live.stat().st_mode & 0o777 == 0o600
    assert parent_io.read_frozen_parent_evidence(case.control, case.proposal) == case.evidence


def test_parent_write_requires_exact_native_reservation_intent(case, monkeypatch):
    freeze(case)
    old = case.live.read_bytes()
    def absent(*args):
        raise CreatorError("INVALID_INTENT", "No valid intent")
    monkeypatch.setattr(parent_io, "_reservation_intent", absent)
    with pytest.raises(CreatorError):
        parent_io.reserve_extension(case.control, case.proposal, before_evidence=case.evidence)
    assert case.live.read_bytes() == old


@pytest.mark.parametrize("failure", ["before_replace", "after_replace", "before_receipt"])
def test_parent_write_interruptions_retain_risk_and_reuse_the_same_allocation(case, monkeypatch, failure):
    freeze(case)
    original_replace = parent_io._replace_parent
    original_once = parent_io._write_once
    def interrupted_replace(path, data):
        if failure == "before_replace":
            raise OSError("fictional disk failure")
        original_replace(path, data)
        raise OSError("fictional failure after replace")
    def interrupted_receipt(path, value):
        if str(path).endswith(".reserved.json"):
            raise OSError("fictional proof failure")
        return original_once(path, value)
    with monkeypatch.context() as patch:
        if failure == "before_receipt":
            patch.setattr(parent_io, "_write_once", interrupted_receipt)
        else:
            patch.setattr(parent_io, "_replace_parent", interrupted_replace)
        with pytest.raises(CreatorError):
            parent_io.reserve_extension(case.control, case.proposal, before_evidence=case.evidence)
    count = len(json.loads(case.live.read_bytes())["operations"])
    assert count == (5 if failure == "before_replace" else 6)
    receipt = parent_io.reserve_extension(case.control, case.proposal, before_evidence=case.evidence)
    assert len(json.loads(case.live.read_bytes())["operations"]) == 6
    assert receipt["max_calls"] == 54
    assert parent_io.verify_extension(case.control, case.proposal, before_evidence=case.evidence, receipt=receipt)


@pytest.mark.parametrize("change", ["old_record", "extra_allocation", "same_key_different_request", "existing_begun", "wrong_quote", "wrong_amount", "wrong_summary"])
def test_parent_cas_rejects_any_conflicting_transition_without_writes(case, change):
    freeze(case)
    ledger = copy.deepcopy(case.ledger)
    if change == "old_record":
        ledger["operations"]["retained-original"]["status"] = "completed"
    elif change == "extra_allocation":
        ledger["operations"]["other"] = copy.deepcopy(ledger["operations"]["asset"])
        ledger["operations"]["other"]["operation_key"] = "other"
    else:
        proposal = case.proposal["parent_allocation"]
        item = {"operation_key": proposal["operation_key"], "request": copy.deepcopy(proposal["request"]),
            "request_sha256": digest(proposal["request"]), "upper_fen": 100, "quote_ref": proposal["quote_ref"],
            "status": "reserved", "provider_task_id": None, "actual_charge_fen": None}
        ledger["operations"][item["operation_key"]] = item
        ledger["reserved_upper_fen"] = 8237
        if change == "same_key_different_request":
            item["request"]["continuation_basis_hash"] = "f" * 64
            item["request_sha256"] = digest(item["request"])
        elif change == "existing_begun":
            item["status"] = "submitting"
        elif change == "wrong_quote":
            item["quote_ref"] = "different approval"
        elif change == "wrong_amount":
            item["upper_fen"] = 0
        else:
            ledger["reserved_upper_fen"] = 0
    private(case.live, ledger)
    before = case.live.read_bytes()
    with pytest.raises(CreatorError):
        parent_io.reserve_extension(case.control, case.proposal, before_evidence=case.evidence)
    assert case.live.read_bytes() == before


@pytest.mark.parametrize("fault", ["parent_symlink", "parent_hardlink", "parent_mode", "lock_symlink", "lock_hardlink", "lock_writable", "proof_mode", "proof_symlink"])
def test_parent_and_proof_metadata_are_enforced_without_chmod_or_repair(case, fault):
    freeze(case)
    directory = case.live.parent
    lock = directory / ".budget.lock"
    if fault == "parent_symlink":
        moved = directory / "original-parent.json"
        case.live.rename(moved)
        case.live.symlink_to(moved)
    elif fault == "parent_hardlink":
        os.link(case.live, directory / "alias-parent.json")
    elif fault == "parent_mode":
        case.live.chmod(0o644)
    elif fault == "lock_symlink":
        lock.symlink_to(case.live)
    elif fault == "lock_hardlink":
        os.link(case.live, lock)
    elif fault == "lock_writable":
        lock.write_text("")
        lock.chmod(0o666)
    else:
        proof = Path(case.control.roots["execution"]) / "continuation-parent" / (case.proposal["proposal_hash"] + ".json")
        if fault == "proof_mode":
            proof.chmod(0o644)
        else:
            moved = proof.with_name("old-proof.json")
            proof.rename(moved)
            proof.symlink_to(moved)
    with pytest.raises(CreatorError):
        parent_io.reserve_extension(case.control, case.proposal, before_evidence=case.evidence)


def test_existing_private_budget_lock_0644_and_parent_directory_0755_are_preserved(case):
    freeze(case)
    case.live.parent.chmod(0o755)
    lock = case.live.parent / ".budget.lock"
    lock.write_bytes(b"")
    lock.chmod(0o644)
    before_inode = lock.stat().st_ino
    parent_io.reserve_extension(case.control, case.proposal, before_evidence=case.evidence)
    assert lock.stat().st_ino == before_inode and lock.stat().st_mode & 0o777 == 0o644
    assert case.live.parent.stat().st_mode & 0o777 == 0o755


def test_old_record_format_rewrite_after_receipt_is_rejected(case):
    freeze(case)
    receipt = parent_io.reserve_extension(case.control, case.proposal, before_evidence=case.evidence)
    case.live.write_text(json.dumps(json.loads(case.live.read_bytes()), indent=4))
    with pytest.raises(CreatorError):
        parent_io.verify_extension(case.control, case.proposal, before_evidence=case.evidence, receipt=receipt)


def test_receipt_pure_validator_rejects_resealed_caps_and_begin_dispatch(case, monkeypatch):
    freeze(case)
    receipt = parent_io.reserve_extension(case.control, case.proposal, before_evidence=case.evidence)
    monkeypatch.setattr(Path, "read_bytes", lambda *args: pytest.fail("pure receipt validator read a file"))
    monkeypatch.setattr(Path, "read_text", lambda *args, **kwargs: pytest.fail("pure receipt validator read a file"))
    assert parent_io.validate_extension_receipt(receipt, case.proposal, intent=case.intent)
    for key, value in (("max_calls", 1), ("retained_risk_cny", "0.000000"), ("intent_id", "f" * 32), ("dispatch_authorized", True)):
        changed = copy.deepcopy(receipt)
        changed[key] = value
        changed["receipt_hash"] = digest({k: v for k, v in changed.items() if k != "receipt_hash"})
        with pytest.raises(CreatorError):
            parent_io.validate_extension_receipt(changed, case.proposal, intent=case.intent)


def test_parent_io_never_constructs_budgetledger_loads_key_or_uses_network(case, monkeypatch):
    from experiments.tudou_ab.ledger import BudgetLedger
    attempts = []
    def denied(*args, **kwargs):
        attempts.append(True)
        raise AssertionError("forbidden provider or legacy writer")
    for owner, names in ((BudgetLedger, ("__init__", "reserve", "begin", "snapshot", "record")),
            (socket, ("create_connection", "getaddrinfo")), (socket.socket, ("connect", "connect_ex"))):
        for name in names:
            monkeypatch.setattr(owner, name, denied)
    freeze(case)
    receipt = parent_io.reserve_extension(case.control, case.proposal, before_evidence=case.evidence)
    assert parent_io.verify_extension(case.control, case.proposal, before_evidence=case.evidence, receipt=receipt)
    assert not attempts


def test_frozen_reference_rejects_byte_only_proof_changes_after_quote(case):
    reference = freeze(case)
    assert parent_io.verify_frozen_parent_reference(case.control, case.proposal, reference)
    path = Path(case.control.roots["execution"]) / "continuation-parent" / (case.proposal["proposal_hash"] + ".json")
    path.write_bytes(path.read_bytes() + b"\n")
    with pytest.raises(CreatorError):
        parent_io.verify_frozen_parent_reference(case.control, case.proposal, reference)


def test_large_private_writer_exceeds_receiptstore_limit_and_enforces_its_own_bound(tmp_path):
    directory = tmp_path / "large-proof"
    directory.mkdir(mode=0o700)
    path = directory / "before.json"
    value = {"fictional": "x" * (2 * 1048576 + 1)}
    parent_io._write_once(path, value)
    assert path.stat().st_size > 2 * 1048576 and path.stat().st_mode & 0o777 == 0o600
    assert json.loads(path.read_bytes()) == value
    oversized = directory / "oversized.json"
    with pytest.raises(Exception):
        parent_io._write_once(oversized, {"fictional": "x" * parent_io.MAX_EVIDENCE_BYTES})
    assert not oversized.exists()


@pytest.mark.parametrize("reason", ["expired", "revoked", "unknown_changed", "journal_drift"])
def test_current_permission_gate_stops_new_allocation_but_does_not_erase_existing_fact(case, monkeypatch, reason):
    freeze(case)
    before = case.live.read_bytes()
    def denied(*args):
        raise CreatorError("CURRENT_PERMISSION_UNAVAILABLE", reason)
    with monkeypatch.context() as patch:
        patch.setattr(parent_io, "_fresh_reservation_authority", denied)
        with pytest.raises(CreatorError):
            parent_io.reserve_extension(case.control, case.proposal, before_evidence=case.evidence)
    assert case.live.read_bytes() == before
    receipt = parent_io.reserve_extension(case.control, case.proposal, before_evidence=case.evidence)
    after = case.live.read_bytes()
    monkeypatch.setattr(parent_io, "_fresh_reservation_authority", denied)
    assert parent_io.verify_extension(case.control, case.proposal, before_evidence=case.evidence, receipt=receipt)
    assert parent_io.reserve_extension(case.control, case.proposal, before_evidence=case.evidence) == receipt
    assert case.live.read_bytes() == after
