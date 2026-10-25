"""Read-only continuation proposals retain every old risk and unknown intent."""
import copy
import json
import socket
from datetime import timedelta

import pytest
from asset_operator_fixture import asset_control_case as asset_control_case
from image_operator_fixture import authorize, images_case, make_media, posts
from media_ledger_fixture import image_fee
from test_image_receipts import image_bytes

from core.creator.store import CreatorError, digest, utc_now
from core.execution import image_continuation_proposal as proposal
from core.execution.image_control import read_image_control
from core.execution.policy import now
from core.execution.receipts import ReceiptStore


def complete_synthetic_parent(case):
    # The historical authority fixture intentionally stores minimal retained
    # records. This new evidence profile requires full fictional ledger rows.
    for key, record in case.ledger["operations"].items():
        record.setdefault("operation_key", key)
        record.setdefault("request_sha256", digest(record["request"]))
        record.setdefault("status", "reserved")
        record.setdefault("provider_task_id", None)
        record.setdefault("actual_charge_fen", None)
    case.save()
    case.media = make_media(case)
    case.live_parent_path = case.root / "live-parent-ledger.json"
    case.live_parent_path.write_bytes((case.root / "parent-ledger.json").read_bytes())
    case.live_parent_path.chmod(0o600)


def prepared_proposal_case(asset_control_case):
    case = images_case(asset_control_case, profile=False)
    complete_synthetic_parent(case)
    _, case.grant = authorize(case)
    first_shot, second_shot = [frame["shot_id"] for frame in case.manifest["frames"][:2]]
    reserved = case.media.reserve_submit(case.task["id"], case.grant["id"], first_shot, key="proposal-first-image")["operation"]
    first = case.media.submit(case.task["id"], reserved["id"])["operation"]
    image_fee(case, first)
    query = case.media.query(case.task["id"], first["id"], key="proposal-first-image-query")["query"]
    case.media.record_delivery(case.task["id"], first["id"], query["id"], image_bytes())
    second = case.media.reserve_submit(case.task["id"], case.grant["id"], second_shot, key="proposal-second-image")["operation"]
    with case.env.runtime.store.transaction() as payload:
        record = payload["media"]["operation:" + second["id"]]
        record.update(status="submitting", capture_started_at=utc_now())
    with case.env.runtime.store.transaction() as payload:
        payload["media"]["operation:" + second["id"]]["status"] = "unknown"
    payload = case.env.runtime.store.read_only()
    case.first = copy.deepcopy(payload["media"]["operation:" + first["id"]])
    case.unknown = copy.deepcopy(payload["media"]["operation:" + second["id"]])
    case.control = read_image_control(case.path, case.backend)
    from core.execution.image_continuation_parent import load_parent_evidence
    case.parent_evidence = load_parent_evidence(case.control, case.live_parent_path)
    case.valid_until = (now() + timedelta(hours=2)).isoformat()
    return case


def built(case):
    return proposal.build_proposal(case.control, case.parent_evidence, valid_until=case.valid_until)


def test_proposal_is_canonical_read_only_and_derives_the_consumed_unknown_slot(asset_control_case, monkeypatch):
    case = prepared_proposal_case(asset_control_case)
    state = case.env.runtime.store.path("state.json")
    before = state.read_bytes()
    parent_before = (case.root / "parent-ledger.json").read_bytes()
    control_before = case.path.read_bytes()
    count = len(posts(case))

    def forbidden(*args, **kwargs):
        pytest.fail("Read-only proposal attempted a writer, collector claim or network")

    monkeypatch.setattr(case.env.runtime.store.__class__, "transaction", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(ReceiptStore, "prepare", forbidden)
    value = built(case)
    assert value == built(case)
    assert proposal.verify_proposal(value, case.control, case.parent_evidence) is True
    assert value["proposal_hash"] == digest({key: item for key, item in value.items() if key != "proposal_hash"})
    assert value["dispatch_available"] is value["approval_granted"] is False
    assert value["additional_allocation_status"] == "proposed_only"
    assert value["paid_calls"] == value["automatic_followup_calls"] == 0
    basis = value["basis"]
    assert basis["limits"]["used_submissions"] == 2
    assert basis["limits"]["original_max_submissions"] == len(case.manifest["frames"])
    assert basis["limits"]["additional_paid_calls"] == 1
    assert basis["limits"]["proposed_cumulative_max_submissions"] == len(case.manifest["frames"]) + 1
    assert basis["budget"]["additional_risk_cny"] == "1.000000"
    assert basis["budget"]["retained_risk_released_cny"] == "0.000000"
    assert basis["unknown_disposition"]["operation_id"] == case.unknown["id"]
    assert basis["unknown_disposition"]["operation_hash"] == digest(case.unknown)
    assert basis["unknown_disposition"]["status_after_proposal"] == "unknown"
    assert basis["unknown_disposition"]["zero_http_proven"] is False
    targets = basis["remaining_jobs"]
    replacements = [item for item in targets if item["replacement_of"] is not None]
    assert len(replacements) == 1 and replacements[0]["replacement_of"] == case.unknown["id"]
    assert replacements[0]["attempt_version"] == 2
    assert all(item["new_operation_id"] is None and item["new_nonce"] is None for item in targets)
    request = value["parent_allocation"]["request"]
    assert request["max_new_calls"] == 1 and request["continuation_basis_hash"] == value["basis_hash"]
    assert state.read_bytes() == before and parent_before == (case.root / "parent-ledger.json").read_bytes()
    assert case.path.read_bytes() == control_before and len(posts(case)) == count
    assert "live_parent_path" not in json.dumps(value) and "base64" not in json.dumps(value)


@pytest.mark.parametrize("invalid", [None, False, "not-a-time", "2020-01-01T00:00:00+00:00", "2100-01-01T00:00:00+00:00"])
def test_proposed_window_is_bounded_and_never_backdates_a_new_permit(asset_control_case, invalid):
    case = prepared_proposal_case(asset_control_case)
    before = case.env.runtime.store.path("state.json").read_bytes()
    with pytest.raises(CreatorError):
        proposal.build_proposal(case.control, case.parent_evidence, valid_until=invalid)
    assert before == case.env.runtime.store.path("state.json").read_bytes()


@pytest.mark.parametrize("attack", ["changed_code", "new_code", "new_checkpoint", "grant_revoked", "control_drift", "parent_drift"])
def test_proposal_verification_rebuilds_current_evidence(asset_control_case, attack):
    case = prepared_proposal_case(asset_control_case)
    value = built(case)
    if attack == "changed_code":
        name = next(iter(case.control.document.code_manifest))
        (case.backend / name).write_text("# Drift after proposal.\n")
    elif attack == "new_code":
        (case.backend / "core/execution/new_after_proposal.py").write_text("# New code.\n")
    elif attack == "new_checkpoint":
        case.media.quote(case.task["id"], key="proposal-unrelated-checkpoint-change")
    elif attack == "grant_revoked":
        case.media.revoke(case.task["id"], case.grant["id"])
    elif attack == "control_drift":
        case.path.write_bytes(case.path.read_bytes() + b" ")
    else:
        path = case.live_parent_path
        path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(CreatorError):
        proposal.verify_proposal(value, case.control, case.parent_evidence)


def test_fully_resealed_unknown_and_budget_claims_are_not_authority(asset_control_case):
    case = prepared_proposal_case(asset_control_case)
    value = built(case)
    changed = copy.deepcopy(value)
    changed["basis"]["limits"]["used_submissions"] = 1
    changed["basis"]["budget"]["retained_risk_released_cny"] = "1.000000"
    changed["basis_hash"] = digest(changed["basis"])
    changed["parent_allocation"]["request"]["continuation_basis_hash"] = changed["basis_hash"]
    changed["proposal_hash"] = digest({key: item for key, item in changed.items() if key != "proposal_hash"})
    with pytest.raises(CreatorError):
        proposal.verify_proposal(changed, case.control, case.parent_evidence)


def test_no_proposal_with_a_reserved_or_submitting_intent(asset_control_case):
    case = images_case(asset_control_case, profile=False)
    complete_synthetic_parent(case)
    _, grant = authorize(case)
    case.media.reserve_submit(case.task["id"], grant["id"], "S01", key="proposal-unstarted-intent")
    control = read_image_control(case.path, case.backend)
    from core.execution.image_continuation_parent import load_parent_evidence
    evidence = load_parent_evidence(control, case.live_parent_path)
    with pytest.raises(CreatorError):
        proposal.build_proposal(control, evidence, valid_until=(now() + timedelta(hours=2)).isoformat())


def test_verify_never_accepts_a_truthy_imitation_of_loaded_control():
    with pytest.raises(CreatorError):
        proposal.build_proposal(object(), {}, valid_until=(now() + timedelta(hours=2)).isoformat())
