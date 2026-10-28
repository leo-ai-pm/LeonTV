"""Independent retained-risk evidence and funded/deferred boundary attacks."""
import copy
import json
import os
import socket
from datetime import timedelta
from types import SimpleNamespace

import httpx
import pytest
from test_image_policy import accepted_raw
from test_image_retained_unknown import plan_case

from core.creator.store import CreatorError, digest
from core.execution import image_retained_unknown as retained
from core.execution import image_runtime_control, media_ledger
from core.execution.receipts import seal


@pytest.fixture(autouse=True)
def deny_external_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Independent retained-risk audit attempted network")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", forbidden)


def reseal(plan):
    plan["plan_hash"] = digest({k: v for k, v in plan.items() if k != "plan_hash"})
    return plan


def test_valid_partial_record_is_blocked_even_when_inspection_hides_candidate_count(tmp_path):
    case = plan_case(tmp_path)
    operation = case["receipts"].operation(case["new_unknown"]["id"])
    binding = case["request"].binding()
    raw = accepted_raw(case["request"])
    raw["received_at"] = (retained.timestamp(operation.claim(binding)["created_at"]) + timedelta(seconds=1)).isoformat()
    record_id = operation.commit(binding, raw, "SYNTHETIC-AUDIT-KEY")
    operation.path(record_id + ".commit.json").unlink()
    operation.path(".writer.lock").unlink()
    summary = case["receipts"].inspect_binding(case["new_unknown"]["id"], binding)
    assert summary["status"] == "incomplete" and summary["candidate_count"] == 0
    before = {p.name: p.read_bytes() for p in operation.root.iterdir()}
    with pytest.raises(CreatorError):
        retained.verify_plan_current(case["control"], case["payload"], case["plan"])
    assert {p.name: p.read_bytes() for p in operation.root.iterdir()} == before


@pytest.mark.parametrize("name", ["error.json", "a.commit.json", "a.ready.json", ".writer.lock", "unexpected.json"])
def test_any_new_collector_file_stops_retained_plan(tmp_path, name):
    case = plan_case(tmp_path)
    path = case["receipts"].operation(case["new_unknown"]["id"]).path(name)
    path.write_text('{}')
    path.chmod(0o600)
    with pytest.raises(CreatorError):
        retained.verify_plan_current(case["control"], case["payload"], case["plan"])
    assert path.read_text() == '{}'


def test_semantically_valid_observer_marker_change_invalidates_snapshot(tmp_path):
    case = plan_case(tmp_path)
    operation = case["receipts"].operation(case["new_unknown"]["id"])
    path = operation.path("observer-closed.json")
    value = operation.read("observer-closed.json")
    value.pop("closed_hash")
    value["closed_at"] = (retained.timestamp(value["closed_at"]) + timedelta(seconds=1)).isoformat()
    path.write_text(json.dumps(seal(value, "closed_hash")))
    path.chmod(0o600)
    assert case["receipts"].inspect_binding(case["new_unknown"]["id"], case["request"].binding())["status"] == "incomplete"
    with pytest.raises(CreatorError):
        retained.verify_plan_current(case["control"], case["payload"], case["plan"])


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "public_mode"])
def test_receipt_inventory_rejects_aliases_or_nonprivate_bytes(tmp_path, kind):
    case = plan_case(tmp_path)
    operation = case["receipts"].operation(case["new_unknown"]["id"])
    path = operation.path("worker.json")
    outside = tmp_path / 'copied-worker.json'
    outside.write_bytes(path.read_bytes())
    outside.chmod(0o600)
    if kind == 'symlink':
        path.unlink()
        path.symlink_to(outside)
    elif kind == 'hardlink':
        path.unlink()
        os.link(outside, path)
    else:
        path.chmod(0o644)
    with pytest.raises((CreatorError, OSError)):
        retained.verify_plan_current(case["control"], case["payload"], case["plan"])


def test_previously_missing_collector_appearing_invalidates_disposition(tmp_path):
    case = plan_case(tmp_path)
    missing = case["receipts"].operation(case["old_unknown"]["id"]).root
    assert not missing.exists()
    missing.mkdir(mode=0o700)
    with pytest.raises(CreatorError):
        retained.verify_plan_current(case["control"], case["payload"], case["plan"])


def test_plan_cannot_dispose_a_request_while_cleanup_is_still_open(tmp_path, monkeypatch):
    case = plan_case(tmp_path)
    cutoff = retained.timestamp(case["plan"]["retained_unknowns"][-1]["collector"]["cleanup_cutoff"])
    monkeypatch.setattr(retained, 'now', lambda: cutoff - timedelta(microseconds=1))
    with pytest.raises(CreatorError):
        retained.verify_plan_current(case["control"], case["payload"], case["plan"])


@pytest.mark.parametrize("attack", ["promote_deferred", "deferred_index", "new_nonce", "clear_old_unknown",
    "claim_zero_fee", "unknown_float_count", "duplicate_inventory", "replace_delivered_old", "new_unknown_is_old"])
def test_fully_resealed_plan_rejects_extra_authority_or_erased_risk(tmp_path, attack):
    case = plan_case(tmp_path)
    plan = copy.deepcopy(case["plan"])
    if attack == "promote_deferred":
        extra = plan["deferred_targets"].pop()
        for key in ('reason', 'required_paid_calls', 'required_risk_cny'):
            extra.pop(key)
        extra['planned_submission_index'] = 6
        plan['funded_targets'].append(extra)
    elif attack == 'deferred_index':
        plan['deferred_targets'][0]['planned_submission_index'] = 6
    elif attack == 'new_nonce':
        plan['funded_targets'][0]['new_nonce'] = 'a' * 32
    elif attack == 'clear_old_unknown':
        plan['retained_unknowns'] = plan['retained_unknowns'][1:]
    elif attack == 'claim_zero_fee':
        plan['retained_unknowns'][0]['zero_fee_proven'] = True
    elif attack == 'unknown_float_count':
        plan['retained_unknowns'][0]['consumed_call_slots'] = 1.0
    elif attack == 'duplicate_inventory':
        collector = plan['retained_unknowns'][-1]['collector']
        collector['files'].append(copy.deepcopy(collector['files'][0]))
        collector['collector_hash'] = digest({k: v for k, v in collector.items() if k != 'collector_hash'})
    elif attack == 'replace_delivered_old':
        plan['funded_targets'][0]['replacement_of'] = case['old_unknown']['id']
    else:
        plan['new_unknown_ids'] = [case['old_unknown']['id']]
    with pytest.raises(CreatorError):
        retained.validate_plan(reseal(plan))


def test_self_consistent_job_hash_substitution_fails_native_history_reconstruction(tmp_path):
    case = plan_case(tmp_path)
    plan = copy.deepcopy(case['plan'])
    plan['funded_targets'][0]['job_hash'] = 'a' * 64
    # Shape-valid dictionaries remain non-authoritative; actual job reconstruction is the deciding boundary.
    retained.validate_plan(reseal(plan))
    with pytest.raises(CreatorError):
        retained.validate_plan_history(case['payload'], case['scope'], case['grant'], plan,
            case['base_authority'], case['funding'], birth=True)


def test_funded_delivery_keeps_deferred_work_visible_in_status(tmp_path, monkeypatch):
    case = plan_case(tmp_path)
    payload = copy.deepcopy(case['payload'])
    payload['media']['operation:' + '9' * 32] = {'kind': 'operation', 'id': '9' * 32,
        'scope_id': case['scope']['id'], 'shot_id': 'S03', 'status': 'accepted', 'fee_id': 'e' * 32,
        'delivery_id': 'f' * 32, 'capture_started_at': case['new_unknown']['capture_started_at']}
    authority = {**case['base_authority'], 'schema_version': 4, 'retained_unknown_plan': case['plan'],
        'remaining_jobs': case['plan']['funded_targets']}
    monkeypatch.setattr(image_runtime_control, 'ExecutionStore', lambda root: SimpleNamespace(read_only=lambda: payload))
    monkeypatch.setattr(image_runtime_control, 'authority_for', lambda *args: authority)
    monkeypatch.setattr(image_runtime_control, 'verify_current', lambda *args: True)
    monkeypatch.setattr(media_ledger, 'projection', lambda *args: {'quality_status': 'not_reviewed'})
    control = SimpleNamespace(roots=case['control'].roots, base=case['control'], runtime_activation_id='b' * 32,
        document=SimpleNamespace(mode='enabled', admission=SimpleNamespace(task_id=case['scope']['task_id'])))
    status = image_runtime_control.runtime_status(control)
    assert status['funded_remaining_jobs'] == 0 and status['deferred_jobs'] == status['remaining_jobs'] == 1
    assert status['blocked_reason'] == 'NEEDS_FUNDING_OR_NEW_PLAN' and status['dispatch_available'] is False
    assert status['media']['quality_status'] == 'not_reviewed'
    assert status['global_unknown_retained'] is True
