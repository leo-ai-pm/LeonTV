"""Independent image authority proofs over original native asset evidence."""
import copy
import hashlib
import os
import time
from datetime import timedelta

import pytest
from asset_operator_fixture import asset_control_case as asset_control_case
from asset_operator_fixture import private_json
from image_operator_fixture import authorize, images_case, make_media, posts

from core.creator.store import CreatorError, digest
from core.execution.image_control import EXTRA_CODE, read_image_control
from core.execution.policy import now


def load(case):
    return read_image_control(case.path, case.backend)


def denied(case, *, dispatch=False):
    with pytest.raises(CreatorError) as error:
        load(case).verify_authority(for_dispatch=dispatch)
    assert error.value.code == "IMAGE_CONTROL_UNAVAILABLE"
    assert str(case.root) not in str(error.value)


def test_native_paid_asset_manifest_and_parent_proof_enable_metadata_only(asset_control_case):
    case = images_case(asset_control_case)
    before = (case.env.runtime.store.root / "state.json").read_bytes()
    control = load(case)
    assert control.verify_authority(for_dispatch=True) is True
    ready = control.verified_asset()
    assert ready["manifest"] == case.manifest and ready["origin"] == case.origin
    assert case.origin["asset_operation_id"] == case.asset_operation["id"]
    assert case.data["budget_anchor"]["opening_risk_cny"] == "76.37"
    assert case.data["budget_anchor"]["opening_paid_call_count"] == 48
    assert len(case.asset_case.counter.read_text().splitlines()) == 1
    assert not posts(case)
    assert (case.env.runtime.store.root / "state.json").read_bytes() == before


@pytest.mark.parametrize("field", ["kind", "endpoint", "query_endpoint_prefix", "model", "policy_hash", "admission", "source_hash",
    "max_new_calls", "max_queries", "per_submission_risk_cny", "scope_risk_cny", "risk_basis", "scope_envelope_hash",
    "backend_manifest_hash", "fee_source_hash", "account_scope_id", "asset_operation_id", "asset_manifest_hash", "asset_proof_hash",
    "manifest_origin_hash", "reference_profile_hash"])
def test_fully_rehashed_parent_request_cannot_substitute_bound_fields(asset_control_case, field):
    case = images_case(asset_control_case)
    value = case.request[field]
    case.request[field] = 99 if type(value) is int else {} if type(value) is dict else "substitute"
    case.save()
    denied(case)


@pytest.mark.parametrize("field", ["asset_operation_id", "asset_manifest_hash", "asset_proof_hash", "source_hash", "plan_hash",
    "acceptance_input_hash", "acceptance_report_hash", "handoff_hash", "provenance_hash"])
def test_self_declared_admission_hashes_cannot_replace_native_manifest_receipt(asset_control_case, field):
    case = images_case(asset_control_case)
    case.data["admission"][field] = "0" * (32 if field == "asset_operation_id" else 64)
    if field in case.request:
        case.request[field] = case.data["admission"][field]
    case.save()
    denied(case)


def test_actual_scope_identity_origin_profile_and_guard_are_exact(asset_control_case):
    case = images_case(asset_control_case, profile=False)
    native = copy.deepcopy(case.env.runtime.store.read_only())
    quote, _ = authorize(case)
    control = load(case)
    payload = case.env.runtime.store.read_only()
    scope = payload["media"]["scope:" + quote["scope_id"]]
    assert control.guard(scope) is True
    assert scope["manifest_origin"] == case.origin and scope["reference_profile"] == case.profile
    for field, value in (("manifest", {}), ("manifest_origin", {}), ("reference_profile", None), ("id", "0" * 32),
        ("scope_id", "0" * 32), ("account_id", "other"), ("account_hash", "0" * 64), ("schema_version", True),
        ("kind", "grant"), ("asset_input", {}), ("handoff", {}), ("fee_source", {})):
        changed = copy.deepcopy(scope)
        changed[field] = value
        assert control.guard(changed) is False, field
    assert all(payload[key] == value for key, value in native.items())
    assert not posts(case)


@pytest.mark.parametrize("attack", ["asset_missing", "asset_changed", "opening_risk", "weighted_calls", "begin_kind", "future_begin", "request_sha"])
def test_parent_ledger_retains_complete_native_and_asset_allocations(asset_control_case, attack):
    case = images_case(asset_control_case)
    allocation_key = case.asset_case.selected["operation_key"]
    close_request = True
    if attack == "asset_missing":
        del case.ledger["operations"][allocation_key]
        case.ledger["operations"]["historical-0"]["upper_fen"] += 100
    elif attack == "asset_changed":
        allocation = case.ledger["operations"][allocation_key]
        allocation["request"]["model"] = "other"
        allocation["request_sha256"] = digest(allocation["request"])
    elif attack == "opening_risk":
        case.ledger["operations"]["historical-0"]["upper_fen"] -= 1
    elif attack == "weighted_calls":
        case.ledger["operations"]["old-native-envelope"]["request"]["max_new_calls"] = 7
    elif attack == "begin_kind":
        case.begin["kind"] = "asset_design_allocation_begin_v1"
    elif attack == "future_begin":
        case.begin["begun_at"] = (now() + timedelta(days=1)).isoformat()
    else:
        case.selected["request_sha256"] = "0" * 64
        close_request = False
    case.save(close_request=close_request)
    denied(case)


@pytest.mark.parametrize("attack", ["code_drift", "incomplete_closure", "expired", "closed", "provider_task", "charged", "not_reserved"])
def test_observation_survives_code_expiry_consumption_but_dispatch_rejects(asset_control_case, attack):
    case = images_case(asset_control_case, mode="observe_only" if attack == "expired" else "enabled")
    if attack == "code_drift":
        (case.backend / "core/execution/image_control.py").write_text("# public code changed\n")
    elif attack == "incomplete_closure":
        del case.data["code_manifest"][next(iter(EXTRA_CODE))]
        case.request["backend_manifest_hash"] = digest(case.data["code_manifest"])
    elif attack == "expired":
        policy = case.data["policy"]
        policy["valid_until"] = (now() - timedelta(days=1)).isoformat()
        policy["policy_hash"] = digest({key: value for key, value in policy.items() if key != "policy_hash"})
        case.request["policy_hash"] = policy["policy_hash"]
    elif attack == "closed":
        case.request["scope_status"] = "closed"
    elif attack == "provider_task":
        case.selected["provider_task_id"] = "external-task"
    elif attack == "charged":
        case.selected["actual_charge_fen"] = 0
    else:
        case.selected["status"] = "begun"
    case.save()
    assert load(case).verify_authority() is True
    assert load(case).verified_asset()["manifest"] == case.manifest
    denied(case, dispatch=True)


@pytest.mark.parametrize("attack", ["duplicate", "hardlink", "symlink", "mode", "parent_mode", "root_overlap", "arbitrary_manifest", "path_traversal", "incomplete_fee_source"])
def test_private_strict_json_and_configuration_boundaries(asset_control_case, attack):
    case = images_case(asset_control_case)
    if attack == "duplicate":
        case.path.write_text(case.path.read_text()[:-1] + ',"schema_version":1}')
    elif attack == "hardlink":
        os.link(case.path, case.path.with_name("other.json"))
    elif attack == "symlink":
        real = case.path.with_name("real.json")
        case.path.rename(real)
        case.path.symlink_to(real)
    elif attack == "mode":
        case.path.chmod(0o644)
    elif attack == "parent_mode":
        case.root.chmod(0o777)
    elif attack == "root_overlap":
        case.data["roots"]["creator"] = case.data["roots"]["execution"]
        private_json(case.path, case.data)
    elif attack == "arbitrary_manifest":
        case.data["manifest"] = case.manifest
        private_json(case.path, case.data)
    elif attack == "incomplete_fee_source":
        del case.data["fee_source"]["quota_per_cny"]
        private_json(case.path, case.data)
    else:
        case.data["code_manifest"]["../private.py"] = hashlib.sha256(b"").hexdigest()
        private_json(case.path, case.data)
    denied(case)


def test_control_and_proof_raw_drift_and_actual_static_mounts_fail_closed(asset_control_case):
    case = images_case(asset_control_case)
    control = load(case)
    for path in (case.path, case.root / "parent-ledger.json", case.root / "begin.json", case.asset_case.root / "authorization.json", *control.roots.values()):
        with pytest.raises(CreatorError) as error:
            control.check_private([path])
        assert error.value.code == "IMAGE_CONTROL_UNAVAILABLE"
    for path in (case.path, case.root / "parent-ledger.json", case.root / "begin.json", case.asset_case.root / "authorization.json"):
        original = path.read_bytes()
        path.write_bytes(original + b" ")
        with pytest.raises(CreatorError) as error:
            control.verify_authority(for_dispatch=True)
        assert error.value.code == "IMAGE_CONTROL_UNAVAILABLE"
        path.write_bytes(original)
    assert not posts(case)


def test_original_text_revoke_preserves_existing_image_scope_historical_identity(asset_control_case):
    case = images_case(asset_control_case)
    quote, _ = authorize(case)
    payload = case.env.runtime.store.read_only()
    original = payload["tasks"][case.task["id"]]
    grant = next(value for value in payload["grants"].values() if value["task_id"] == original["id"])
    response = case.env.client.post(case.env.url(case.task) + "/grants/" + grant["id"] + "/revoke",
        json={"client_action_key": "image-original-text-revoke", "expected_execution_revision": original["revision"]})
    assert response.status_code == 200, response.text
    control = load(case)
    assert control.verify_authority(for_dispatch=True) is True
    scope = case.env.runtime.store.read_only()["media"]["scope:" + quote["scope_id"]]
    assert control.guard(scope) is True
    assert make_media(case).quote(case.task["id"], key="image-after-text-revoke-quote")["quote"]["scope_id"] == scope["id"]
    case.data["mode"] = "observe_only"
    case.save()
    assert load(case).verified_asset()["origin"] == case.origin
    denied(case, dispatch=True)


def test_each_authority_check_uses_one_independent_native_snapshot(asset_control_case, monkeypatch):
    from core.execution.store import ExecutionStore
    case = images_case(asset_control_case)
    quote, _ = authorize(case)
    scope = case.env.runtime.store.read_only()["media"]["scope:" + quote["scope_id"]]
    control = load(case)
    original, reads = ExecutionStore.read_only, []

    def counted(store):
        reads.append(store.root)
        return original(store)

    monkeypatch.setattr(ExecutionStore, "read_only", counted)
    assert control.verify_authority(for_dispatch=True) is True
    assert reads == [case.env.runtime.store.root]
    reads.clear()
    assert control.guard(scope) is True
    assert reads == [case.env.runtime.store.root]


def test_real_operator_reserve_and_start_fit_the_original_permit_deadline(asset_control_case):
    from core.execution import image_operator
    case = images_case(asset_control_case)
    _, grant = authorize(case)

    def command(name, *args):
        parsed = image_operator.parser().parse_args([name, *args])
        return image_operator.execute(load(case), name, parsed,
            session_factory=lambda control: image_operator.build_image_session(control, receipt_capture=case.settings,
                credential_loader=lambda: pytest.fail("synthetic phase loaded a live credential")))

    started = time.monotonic()
    operation = command("reserve", "--grant", grant["id"], "--shot", "S01", "--key", "timed-image-reserve")["operation"]
    received = command("start", "--operation", operation["id"])["operation"]
    elapsed = time.monotonic() - started
    print(f"image_reserve_to_start_seconds={elapsed:.3f}")
    assert received["status"] == "accepted"
    assert elapsed < case.policy["request_deadline_seconds"]
    assert [row["method"] for row in posts(case)] == ["POST"]


def test_unused_prior_asset_allocation_is_retained_in_higher_opening(asset_control_case):
    case = images_case(asset_control_case, retained_unused=True)
    control = load(case)
    assert control.verify_authority(for_dispatch=True) is True
    assert case.anchor["opening_risk_cny"] == "77.37" and case.anchor["opening_paid_call_count"] == 49
    assert case.anchor["account_limit_cny"] == "79.370000" and case.anchor["account_max_calls"] == 51
    asset_scope = case.env.runtime.store.read_only()["asset_design"]["scope:" + case.origin["asset_proof"]["scope_id"]]
    assert asset_scope["anchor"]["account_limit_cny"] == "77.37" and asset_scope["anchor"]["account_max_calls"] == 49
    account = next(iter(case.env.runtime.store.read_only()["accounts"].values()))
    assert account["limit_units"] == 75370000 and account["max_calls"] == 47
    assert case.ledger["operations"]["synthetic-retained-unused-asset-allocation"]["status"] == "reserved"
    assert len(case.asset_case.counter.read_text().splitlines()) == 1 and not posts(case)
