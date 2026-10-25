"""Independent attacks on the read-only continuation proposal contract."""
import copy
import socket
from datetime import timedelta

from asset_operator_fixture import asset_control_case as asset_control_case
from asset_operator_fixture import private_json
from test_image_continuation_proposal import prepared_proposal_case

from core.creator.store import CreatorError, digest
from core.execution import image_code_continuation, image_control
from core.execution import image_continuation_parent as parent
from core.execution import image_continuation_proposal as proposal
from core.execution.image_capture import ControlledImageCapture
from core.execution.media_runtime import MediaExecution
from core.execution.receipts import timestamp
from core.execution.runtime import ExecutionRuntime
from core.execution.store import ExecutionStore


def reseal(value):
    """An attacker knows the documented seals; those seals grant no authority."""
    result = copy.deepcopy(value)
    if "basis" in result:
        result["basis_hash"] = digest(result["basis"])
        allocation = result.get("parent_allocation", {})
        if type(allocation.get("request")) is dict:
            allocation["request"]["continuation_basis_hash"] = result["basis_hash"]
            allocation["operation_key"] = "image-continuation-" + result["basis_hash"][:32]
            allocation["quote_ref"] = "image-continuation-basis:" + result["basis_hash"]
    result["proposal_hash"] = digest({key: item for key, item in result.items() if key != "proposal_hash"})
    return result


def rejected(call):
    try:
        result = call()
    except CreatorError:
        return
    assert result is False, "A completely resealed forged contract was accepted"


def protected_bytes(case):
    paths = [case.path, case.env.runtime.store.path("state.json")]
    paths.extend(case.root / name for name in ("parent-ledger.json", "begin.json"))
    if hasattr(case, "live_parent_path"):
        paths.append(case.live_parent_path)
    return {str(path): path.read_bytes() for path in paths}


def test_resealed_schema_extra_binding_and_nested_basis_attacks_never_verify(asset_control_case):
    case = prepared_proposal_case(asset_control_case)
    original = proposal.build_proposal(case.control, case.parent_evidence, valid_until=case.valid_until)
    assert proposal.verify_proposal(original, case.control, case.parent_evidence) is True
    protected = protected_bytes(case)
    attacks = []
    for key, value in (("schema_version", True), ("schema_version", 2), ("kind", "image_execution_authority_v1"),
                       ("allocated", True), ("dispatch_available", True), ("unexpected", None)):
        changed = copy.deepcopy(original)
        changed[key] = value
        attacks.append(changed)
    for key in original["basis"]:
        changed = copy.deepcopy(original)
        # Each server-built binding is mandatory, even if the caller can seal it.
        del changed["basis"][key]
        attacks.append(changed)
    changed = copy.deepcopy(original)
    changed["basis"]["unexpected"] = "caller-owned binding"
    attacks.append(changed)
    for changed in attacks:
        rejected(lambda changed=changed: proposal.verify_proposal(
            reseal(changed), case.control, case.parent_evidence))
    assert protected_bytes(case) == protected


def test_fully_rebound_unknown_replacement_caps_risk_and_allocation_claims_are_rejected(asset_control_case):
    case = prepared_proposal_case(asset_control_case)
    original = proposal.build_proposal(case.control, case.parent_evidence, valid_until=case.valid_until)
    unknown = ("basis", "unknown_disposition")
    limits = ("basis", "limits")
    budget = ("basis", "budget")
    replacement_index = next(index for index, job in enumerate(original["basis"]["remaining_jobs"])
        if job["replacement_of"] is not None)
    replacement = ("basis", "remaining_jobs", replacement_index)
    attacks = [
        ((*unknown, "operation_id"), case.first["id"]),
        ((*unknown, "status_after_proposal"), "failed"),
        ((*unknown, "raw_hash"), "0" * 64),
        ((*unknown, "zero_http_proven"), True),
        ((*unknown, "zero_fee_proven"), True),
        ((*unknown, "call_slots_released"), 1),
        ((*unknown, "consumed_call_slots"), 0),
        ((*unknown, "retained_risk_cny"), "0.000000"),
        ((*unknown, "old_request_retry_authorized"), True),
        ((*unknown, "new_replacement_authorized"), True),
        ((*unknown, "original_record_must_remain_unchanged"), False),
        ((*limits, "used_submissions"), 1),
        ((*limits, "inherited_remaining_submissions"), original["basis"]["limits"]["inherited_remaining_submissions"] + 1),
        ((*limits, "additional_paid_calls"), 0),
        ((*limits, "additional_paid_calls"), True),
        ((*limits, "proposed_cumulative_max_submissions"), original["basis"]["limits"]["original_max_submissions"]),
        ((*limits, "original_intents_released"), 1),
        ((*budget, "additional_risk_cny"), "0.000000"),
        ((*budget, "retained_risk_released_cny"), "1.000000"),
        ((*budget, "final_bill"), True),
        ((*replacement, "replacement_of"), None),
        ((*replacement, "replacement_of"), case.first["id"]),
        ((*replacement, "attempt_version"), 1),
        ((*replacement, "new_operation_id"), case.unknown["id"]),
        ((*replacement, "new_nonce"), case.unknown["descriptor"]["permit"]["nonce"]),
        (("parent_allocation", "upper_cny"), "0.000000"),
        (("parent_allocation", "request", "max_new_calls"), 0),
        (("additional_allocation_status",), "allocated"),
        (("approval_granted",), True),
        (("dispatch_available",), True),
        (("paid_calls",), 1),
    ]
    before = protected_bytes(case)
    for path, value in attacks:
        changed = copy.deepcopy(original)
        target = changed
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value
        rejected(lambda changed=changed: proposal.verify_proposal(
            reseal(changed), case.control, case.parent_evidence))
    assert protected_bytes(case) == before


def test_build_and_verify_remain_read_only_without_runtime_key_capture_or_network(asset_control_case, monkeypatch):
    case = prepared_proposal_case(asset_control_case)
    before = protected_bytes(case)
    attempts = []

    def forbidden(*args, **kwargs):
        attempts.append(True)
        raise AssertionError("Read-only proposal attempted a side effect")

    with monkeypatch.context() as patch:
        for owner, names in ((ExecutionStore, ("transaction",)), (ExecutionRuntime, ("__init__", "recover")),
                (MediaExecution, ("__init__", "reserve_submit", "submit", "recover")),
                (ControlledImageCapture, ("__init__", "capture", "ready_raw")),
                (socket, ("create_connection", "getaddrinfo")), (socket.socket, ("connect", "connect_ex"))):
            for name in names:
                patch.setattr(owner, name, forbidden)
        first = proposal.build_proposal(case.control, case.parent_evidence, valid_until=case.valid_until)
        assert proposal.verify_proposal(first, case.control, case.parent_evidence) is True
        assert proposal.build_proposal(case.control, case.parent_evidence, valid_until=case.valid_until) == first
    assert attempts == [] and protected_bytes(case) == before


def test_expired_original_contract_can_be_proposed_but_cannot_dispatch_or_code_activate(asset_control_case, monkeypatch):
    case = prepared_proposal_case(asset_control_case)
    expired = timestamp(case.policy["valid_until"]) + timedelta(seconds=1)
    with monkeypatch.context() as patch:
        for module in (image_control, image_code_continuation, proposal):
            patch.setattr(module, "now", lambda: expired)
        before = protected_bytes(case)
        assert case.control.verify_authority(for_dispatch=False) is True
        value = proposal.build_proposal(case.control, case.parent_evidence,
            valid_until=(expired + timedelta(hours=1)).isoformat())
        assert proposal.verify_proposal(value, case.control, case.parent_evidence) is True
        rejected(lambda: case.control.verify_authority(for_dispatch=True))
        rejected(lambda: image_code_continuation.quote_code_continuation(case.control, case.grant["id"]))
        assert protected_bytes(case) == before


def test_verification_reloads_exact_live_parent_even_when_old_snapshot_stays_valid(asset_control_case):
    case = prepared_proposal_case(asset_control_case)
    live = case.root / "independent-current-parent.json"
    private_json(live, case.ledger)
    evidence = parent.load_parent_evidence(case.control, live)
    value = proposal.build_proposal(case.control, evidence, valid_until=case.valid_until)
    frozen_bytes = case.root.joinpath("parent-ledger.json").read_bytes()
    altered = copy.deepcopy(case.ledger)
    selected = altered["operations"][case.selected["operation_key"]]
    selected["upper_fen"] += 1
    private_json(live, altered)
    assert case.control.verify_authority(for_dispatch=False) is True
    rejected(lambda: proposal.verify_proposal(value, case.control, evidence))
    assert case.root.joinpath("parent-ledger.json").read_bytes() == frozen_bytes


def test_current_code_and_checkpoint_drift_invalidate_fully_sealed_proposal(asset_control_case):
    case = prepared_proposal_case(asset_control_case)
    value = proposal.build_proposal(case.control, case.parent_evidence, valid_until=case.valid_until)
    before = protected_bytes(case)
    code = case.backend / "core/execution/independent_audit_revision.py"
    code.write_text("# A current source change must invalidate the old proposal.\n")
    rejected(lambda: proposal.verify_proposal(value, case.control, case.parent_evidence))
    code.unlink()
    assert proposal.verify_proposal(value, case.control, case.parent_evidence) is True
    assert protected_bytes(case) == before
    # A legitimate append changes the current checkpoint without altering old frames.
    case.media.revoke(case.task["id"], case.grant["id"])
    rejected(lambda: proposal.verify_proposal(value, case.control, case.parent_evidence))


def test_the_named_image_unknown_cannot_hide_another_unknown_query(asset_control_case, monkeypatch):
    case = prepared_proposal_case(asset_control_case)
    assert proposal.build_proposal(case.control, case.parent_evidence, valid_until=case.valid_until)

    def no_receipt(*args, **kwargs):
        raise CreatorError("SYNTHETIC_UNCONFIRMED", "Fictional query produced no receipt", 409)

    # Existing accepted tasks may legitimately gain independent GET evidence.
    # This synthetic GET stops before its collector or any transport is called.
    monkeypatch.setattr(case.media.capture, "capture", no_receipt)
    query = case.media.query(case.task["id"], case.first["id"], key="audit-another-unknown-query")["query"]
    assert query["status"] == "unknown" and query["raw"] is None
    payload = case.env.runtime.store.read_only()
    assert payload["media"]["operation:" + case.unknown["id"]] == case.unknown
    before = protected_bytes(case)
    rejected(lambda: proposal.build_proposal(case.control, case.parent_evidence, valid_until=case.valid_until))
    assert protected_bytes(case) == before


def test_claim_appearing_during_final_code_collection_cannot_leave_a_stale_missing_observation(
        asset_control_case, monkeypatch):
    case = prepared_proposal_case(asset_control_case)
    original_collect = proposal._collect_code_manifest
    attempts = {"collections": 0, "injected": False}
    operation_root = case.control.roots["execution"] / "media-receipts" / case.unknown["id"]
    assert not operation_root.exists()

    def collect_with_late_private_claim(control):
        value = original_collect(control)
        attempts["collections"] += 1
        if attempts["collections"] == 2:
            # This is fictional external evidence appearing between the first
            # inspection and the final freshness check; it is not a receipt.
            operation_root.mkdir(mode=0o700)
            private_json(operation_root / "claim.json", {"unreadable_late_claim": True})
            attempts["injected"] = True
        return value

    before = protected_bytes(case)
    monkeypatch.setattr(proposal, "_collect_code_manifest", collect_with_late_private_claim)
    rejected(lambda: proposal.build_proposal(case.control, case.parent_evidence, valid_until=case.valid_until))
    assert attempts["collections"] == 2 and attempts["injected"] is True
    assert protected_bytes(case) == before
    assert case.env.runtime.store.read_only()["media"]["operation:" + case.unknown["id"]] == case.unknown
