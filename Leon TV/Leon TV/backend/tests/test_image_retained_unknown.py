"""Pure proposal fixtures and exact read-only receipt snapshots; no live authority."""
import copy
import hashlib
import json
import socket
from datetime import timedelta
from types import SimpleNamespace

import pytest
from test_image_continuation_policy import arguments, continuation_case, seal_authority

from core.creator.store import CreatorError, digest
from core.execution import image_retained_unknown as retained
from core.execution.image_continuation_policy import build_image_submit_v2, make_continuation_permit
from core.execution.image_policy import ImageToolPermit, build_image_submit
from core.execution.policy import now
from core.execution.receipts import ReceiptStore, seal
from core.planner.product_assets import image_jobs


def plan_case(tmp_path):
    """A helper-level validated-input stand-in; it grants no native authority."""
    case = continuation_case(3)
    scope, grant = case["scope"], case["grant"]
    scope.update(kind="scope", account_id="fictional-retained-account")
    grant.update(scope_id=scope["id"], task_id=scope["task_id"], revoked_at=None)
    jobs = image_jobs(scope["asset_input"], scope["manifest"])
    old_permit = {key: value for key, value in case["permit"].items() if key in ImageToolPermit.model_fields}
    deadline = retained.timestamp(scope["policy"]["valid_until"])
    old_permit.update(schema_version=1, scope="accepted_plan_image_tools_v1", job_kind="controlled_image_http_v1",
        operation_id="7" * 32, capture_id="7" * 32, nonce="6" * 32,
        policy_hash=scope["policy"]["policy_hash"], scope_submission_index=2,
        shot_id=jobs[1]["shot_id"], job_hash=digest(jobs[1]),
        issued_at=(deadline - timedelta(seconds=120)).isoformat(), deadline_at=deadline.isoformat())
    old_references = copy.deepcopy(case["references"])
    old_references[0]["shot_id"] = jobs[1]["reference_shot_ids"][0]
    old_request = build_image_submit(policy=scope["policy"], budget_anchor=scope["anchor"], permit=old_permit,
        asset_input=scope["asset_input"], manifest=scope["manifest"], shot_id=jobs[1]["shot_id"], references=old_references)

    def operation(identifier, index, job, descriptor=None, delivered=False):
        return {"kind": "operation", "id": identifier, "scope_id": scope["id"], "task_id": scope["task_id"],
            "grant_id": grant["id"], "shot_id": job["shot_id"], "submission_index": index,
            "status": "accepted" if delivered else "unknown", "capture_started_at": (now() - timedelta(minutes=5)).isoformat(),
            "descriptor": descriptor, "raw": {"synthetic": True} if delivered else None,
            "receipt": {"synthetic": True} if delivered else None,
            "fee_id": ("a" if index == 1 else "b") * 32 if delivered else None,
            "delivery_id": ("c" if index == 1 else "d") * 32 if delivered else None}

    old_unknown = operation("7" * 32, 2, jobs[1], old_request.frozen())
    base = case["authority"]
    base.update(scope_hash=digest(scope), grant_hash=digest(grant))
    base["unknown_disposition"]["operation_hash"] = digest(old_unknown)
    case["base_authority"] = case["authority"] = base = seal_authority(base)
    permit = make_continuation_permit(scope, grant, case["job"], base, operation_id="2" * 32, index=4,
        reference_bindings=case["reference_bindings"])
    issued = now() - timedelta(minutes=5)
    permit.update(issued_at=issued.isoformat(), deadline_at=(issued + timedelta(seconds=120)).isoformat())
    case["permit"] = permit
    request = build_image_submit_v2(**arguments(case))
    new_unknown = operation("2" * 32, 4, jobs[2], request.frozen())
    new_unknown["capture_started_at"] = (issued + timedelta(seconds=1)).isoformat()
    operations = [operation("0" * 32, 1, jobs[0], delivered=True), old_unknown,
        operation("8" * 32, 3, jobs[1], delivered=True), new_unknown]
    payload = {"media": {"scope:" + scope["id"]: scope, **{"operation:" + op["id"]: op for op in operations}},
        "operations": {}, "asset_design": {}}
    funding = {"scope_id": scope["id"], "intent_id": base["intent_id"], "validity_activation_id": base["validity_activation_id"],
        "parent_receipt_hash": base["parent_receipt_hash"], "allocations": copy.deepcopy(base["allocations"])}
    funding["funding_hash"] = digest(funding)
    execution = tmp_path / "execution"
    execution.mkdir(mode=0o700)
    control = SimpleNamespace(roots={"execution": execution}, backend_root=tmp_path / "backend", static_roots=())
    receipts = ReceiptStore(execution / "media-receipts")
    binding = request.binding()
    observer = binding["deadline_at"]
    cutoff = (retained.timestamp(observer) + timedelta(seconds=5)).isoformat()
    # A fully valid frozen claim from the old elapsed request, without network.
    with pytest.MonkeyPatch.context() as patch:
        from core.execution import receipts as receipt_module
        created = (issued + timedelta(seconds=2)).isoformat()
        patch.setattr(receipt_module, "utc_now", lambda: created)
        op, made = receipts.prepare(binding, "a" * 64, "b" * 64, observer, cutoff)
    assert made
    op.write_new("worker.json", seal({"version": 1, "binding_hash": digest(binding), "worker_pid": 12345,
        "booted_at": (issued + timedelta(seconds=3)).isoformat()}, "worker_hash"))
    op.write_new("started.json", seal({"version": 1, "binding_hash": digest(binding), "worker_pid": 12345,
        "dispatched_at": (issued + timedelta(seconds=4)).isoformat()}, "started_hash"))
    op.write_new("observer-closed.json", seal({"version": 1, "binding_hash": digest(binding),
        "closed_at": observer, "reason": "absolute_deadline"}, "closed_hash"))
    plan = retained.build_plan(control, payload, scope, grant, base, funding)
    case.update(control=control, payload=payload, funding=funding, plan=plan, old_unknown=old_unknown,
        new_unknown=new_unknown, request=request, receipts=receipts, old_request=old_request)
    return case


@pytest.fixture(autouse=True)
def deny_network(monkeypatch):
    def denied(*args, **kwargs):
        pytest.fail("Read-only retained plan attempted network")
    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket.socket, "connect_ex", denied)
    monkeypatch.setattr(socket, "create_connection", denied)


def test_plan_reuses_last_slot_and_defers_fourth_without_ledger_mutation(tmp_path):
    case = plan_case(tmp_path)
    before = copy.deepcopy(case["payload"])
    plan = case["plan"]
    assert plan["used_submissions"] == 4 and plan["max_cumulative_submissions"] == 5
    assert plan["remaining_funded_slots"] == 1
    assert len(plan["retained_unknowns"]) == 2 and plan["new_unknown_ids"] == [case["new_unknown"]["id"]]
    funded, deferred = plan["funded_targets"][0], plan["deferred_targets"][0]
    assert funded["shot_id"] == "S03" and funded["attempt_version"] == 2 and funded["planned_submission_index"] == 5
    assert funded["replacement_of"] == case["new_unknown"]["id"]
    assert deferred["shot_id"] == "S04" and "planned_submission_index" not in deferred
    assert deferred["required_paid_calls"] == 1 and deferred["required_risk_cny"] == "1.000000"
    assert retained.verify_plan_current(case["control"], case["payload"], plan)
    assert case["payload"] == before


@pytest.mark.parametrize("field,value", [("schema_version", True), ("used_submissions", 3),
    ("remaining_funded_slots", 2), ("call_slots_released", True), ("retained_risk_released_cny", "1.000000"),
    ("old_request_retry_authorized", True), ("new_unknown_stops_continuation", False),
    ("new_unknown_ids", []), ("original_records_must_remain_unchanged", False)])
def test_resealed_plan_cannot_gain_release_or_change_limits(tmp_path, field, value):
    case = plan_case(tmp_path)
    plan = copy.deepcopy(case["plan"])
    plan[field] = value
    plan["plan_hash"] = digest({k: v for k, v in plan.items() if k != "plan_hash"})
    with pytest.raises(CreatorError):
        retained.validate_plan(plan)


def test_current_check_allows_new_candidate_but_keeps_old_facts_immutable(tmp_path):
    case = plan_case(tmp_path)
    payload = copy.deepcopy(case["payload"])
    payload["media"]["operation:" + "9" * 32] = {"kind": "operation", "id": "9" * 32,
        "scope_id": case["scope"]["id"], "grant_id": case["grant"]["id"], "submission_index": 5,
        "status": "submitting", "fee_id": None}
    assert retained.verify_plan_current(case["control"], payload, case["plan"])
    assert retained.validate_plan_history(payload, case["scope"], case["grant"], case["plan"],
        case["base_authority"], case["funding"])
    with pytest.raises(CreatorError):
        retained.validate_plan_history(payload, case["scope"], case["grant"], case["plan"],
            case["base_authority"], case["funding"], birth=True)
    payload["media"]["operation:" + case["old_unknown"]["id"]]["status"] = "accepted"
    with pytest.raises(CreatorError):
        retained.verify_plan_current(case["control"], payload, case["plan"])


def test_late_partial_record_is_not_hidden_by_incomplete_inspection(tmp_path):
    case = plan_case(tmp_path)
    root = case["receipts"].operation(case["new_unknown"]["id"]).root
    path = root / ("a" * 64 + ".record.json")
    path.write_text(json.dumps({"not_complete": True}))
    path.chmod(0o600)
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(CreatorError):
        retained.verify_plan_current(case["control"], case["payload"], case["plan"])
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_birth_rejects_other_account_scope_unknown_and_unsettled_query(tmp_path):
    case = plan_case(tmp_path)
    for kind in ("operation", "query"):
        payload = copy.deepcopy(case["payload"])
        other = {**case["scope"], "id": "f" * 32}
        payload["media"]["scope:" + other["id"]] = other
        payload["media"][kind + ":" + "9" * 32] = {"kind": kind, "id": "9" * 32,
            "scope_id": other["id"], "grant_id": case["grant"]["id"], "status": "unknown"}
        with pytest.raises(CreatorError):
            retained.build_plan(case["control"], payload, case["scope"], case["grant"], case["base_authority"], case["funding"])


def test_current_snapshot_cannot_be_approved_before_original_cleanup(tmp_path, monkeypatch):
    case = plan_case(tmp_path)
    cutoff = retained.timestamp(case["plan"]["retained_unknowns"][-1]["collector"]["cleanup_cutoff"])
    monkeypatch.setattr(retained, "now", lambda: cutoff - timedelta(microseconds=1))
    with pytest.raises(CreatorError):
        retained.verify_plan_current(case["control"], case["payload"], case["plan"])
    monkeypatch.setattr(retained, "now", lambda: cutoff)
    assert retained.verify_plan_current(case["control"], case["payload"], case["plan"])
