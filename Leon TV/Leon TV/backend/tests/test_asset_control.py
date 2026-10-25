"""Independent proofs for explicit asset authority; no real evidence or network."""
import hashlib
import os
from datetime import timedelta

import pytest
from asset_operator_fixture import asset_control_case as asset_control_case
from asset_operator_fixture import private_json

from core.creator.store import CreatorError, digest
from core.execution.asset_control import EXTRA_CODE, read_asset_control
from core.execution.policy import now


def load(case):
    return read_asset_control(case.path, case.backend)


def denied(case, *, dispatch=False):
    with pytest.raises(CreatorError) as error:
        load(case).verify_authority(for_dispatch=dispatch)
    assert error.value.code == "ASSET_CONTROL_UNAVAILABLE"
    assert str(case.root) not in str(error.value)


def test_exact_proof_and_native_admission_enable_no_calls(asset_control_case):
    case = asset_control_case()
    before = (case.env.runtime.store.root / "state.json").read_bytes()
    control = load(case)
    assert control.verify_authority(for_dispatch=True) is True
    assert control.roots["execution"] == case.env.runtime.store.root
    assert control.guard({"task_id": case.task["id"], "policy": case.data["policy"], "anchor": case.anchor,
        "fee_source": case.source, "asset_input": case.context, "handoff": case.handoff}) is True
    assert (case.env.runtime.store.root / "state.json").read_bytes() == before
    assert not case.counter.exists()


@pytest.mark.parametrize("field", ["kind", "model", "endpoint", "chat_output_parameter", "source_hash", "admission",
    "policy_hash", "max_new_calls", "per_call_risk_cny", "scope_risk_cny", "risk_basis", "scope_envelope_hash",
    "backend_manifest_hash", "fee_source_hash", "account_scope_id"])
def test_rehashed_parent_request_cannot_substitute_bound_fields(asset_control_case, field):
    case = asset_control_case()
    value = case.request[field]
    case.request[field] = 2 if type(value) is int else {} if type(value) is dict else "other"
    case.save()
    denied(case)


@pytest.mark.parametrize("field", ["snapshot_hash", "source_hash", "plan_hash", "acceptance_input_hash", "acceptance_report_hash",
    "handoff_hash", "provenance_hash"])
def test_rehashed_admission_still_requires_original_native_evidence(asset_control_case, field):
    case = asset_control_case()
    case.data["admission"][field] = "0" * 64
    if field == "source_hash":
        case.request["source_hash"] = "0" * 64
    case.save()
    denied(case)


@pytest.mark.parametrize("proof", ["authorization.json", "parent-ledger.json", "begin.json", "asset-control.json"])
def test_raw_proof_drift_rejected_without_sensitive_output(asset_control_case, proof):
    case = asset_control_case()
    control = load(case)
    path = case.root / proof
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(CreatorError, match="Explicit asset operator control is unavailable"):
        control.verify_authority(for_dispatch=True)


def test_observe_only_expired_and_code_drift_still_reads_no_dispatch(asset_control_case):
    case = asset_control_case(mode="observe_only")
    policy = case.data["policy"]
    policy["valid_until"] = (now() - timedelta(days=1)).isoformat()
    policy["policy_hash"] = digest({key: value for key, value in policy.items() if key != "policy_hash"})
    case.request["policy_hash"] = policy["policy_hash"]
    case.save()
    (case.backend / "core/execution/asset_control.py").write_text("# changed\n")
    assert load(case).verify_authority() is True
    denied(case, dispatch=True)


@pytest.mark.parametrize("attack", ["code_drift", "closure_missing", "scope_closed", "charged", "provider_task", "not_reserved"])
def test_dispatch_requires_full_code_and_open_unspent_allocation(asset_control_case, attack):
    case = asset_control_case()
    if attack == "code_drift":
        (case.backend / "core/execution/asset_control.py").write_text("# drift\n")
    elif attack == "closure_missing":
        del case.data["code_manifest"][next(iter(EXTRA_CODE))]
        case.request["backend_manifest_hash"] = digest(case.data["code_manifest"])
    elif attack == "scope_closed":
        case.request["scope_status"] = "closed"
    elif attack == "charged":
        case.selected["actual_charge_fen"] = 0
    elif attack == "provider_task":
        case.selected["provider_task_id"] = "provider-id"
    else:
        case.selected["status"] = "begun"
    case.save()
    assert load(case).verify_authority() is True
    denied(case, dispatch=True)


@pytest.mark.parametrize("attack", ["amount", "weighted_calls", "original", "begin_kind", "begin_time", "request_sha", "authorization"])
def test_rehashed_budget_and_begin_attacks_fail_closed(asset_control_case, attack):
    case = asset_control_case()
    close_request = True
    if attack == "amount":
        case.ledger["operations"]["historical-0"]["upper_fen"] -= 1
    elif attack == "weighted_calls":
        case.ledger["operations"]["old-native-envelope"]["request"]["max_new_calls"] = 7
    elif attack == "original":
        case.authorization["budget"]["hard_total"] = 101
        case.anchor["parent_authorization_hash"] = digest(case.authorization)
        case.ledger["authorization_hash"] = digest(case.authorization)
    elif attack == "begin_kind":
        case.begin["kind"] = "old-text-begin"
    elif attack == "begin_time":
        case.begin["begun_at"] = (now() + timedelta(days=1)).isoformat()
    elif attack == "request_sha":
        case.selected["request_sha256"] = "0" * 64
        close_request = False
    else:
        case.authorization["status"] = "approved"
        case.anchor["parent_authorization_hash"] = digest(case.authorization)
        case.ledger["authorization_hash"] = digest(case.authorization)
    case.save(close_request=close_request)
    denied(case)


@pytest.mark.parametrize("attack", ["duplicate", "symlink", "hardlink", "mode", "parent_mode", "root_overlap", "private_inside_store", "manifest_traversal", "arbitrary_manifest"])
def test_strict_private_configuration_boundary(asset_control_case, attack):
    case = asset_control_case()
    if attack == "duplicate":
        raw = case.path.read_text()
        case.path.write_text(raw[:-1] + ',"schema_version":1}')
    elif attack == "symlink":
        real = case.path.with_name("real.json")
        case.path.rename(real)
        case.path.symlink_to(real)
    elif attack == "hardlink":
        os.link(case.path, case.path.with_name("linked.json"))
    elif attack == "mode":
        case.path.chmod(0o644)
    elif attack == "parent_mode":
        case.root.chmod(0o777)
    elif attack == "root_overlap":
        case.data["roots"]["planner"] = case.data["roots"]["execution"]
        private_json(case.path, case.data)
    elif attack == "private_inside_store":
        case.data["authority"]["authorization_path"] = str(case.env.runtime.store.root / "auth.json")
        private_json(case.path, case.data)
    elif attack == "manifest_traversal":
        case.data["code_manifest"]["../escape.py"] = hashlib.sha256(b"").hexdigest()
        private_json(case.path, case.data)
    else:
        case.data["manifest"] = {"frames": []}
        private_json(case.path, case.data)
    denied(case)


def test_actual_static_mounts_cannot_serve_control_proof_or_store(asset_control_case):
    case = asset_control_case()
    control = load(case)
    for path in (case.path, case.root / "authorization.json", case.root / "begin.json", case.root / "parent-ledger.json", *control.roots.values()):
        with pytest.raises(CreatorError) as error:
            control.check_private([path])
        assert error.value.code == "ASSET_CONTROL_UNAVAILABLE"
    assert not case.counter.exists()


def test_guard_rejects_source_or_policy_mutation(asset_control_case):
    case = asset_control_case()
    control = load(case)
    scope = {"task_id": case.task["id"], "policy": case.data["policy"], "anchor": case.anchor,
        "fee_source": case.source, "asset_input": case.context, "handoff": case.handoff}
    assert control.guard(scope) is True
    scope["asset_input"] = {}
    assert control.guard(scope) is False
