"""Synthetic control files prove authorization without any private data or network."""
import copy
import hashlib
import json
import os
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.creator.store import PRODUCTION, CreatorBriefStore, CreatorError, digest
from core.execution.control import (
    CONTROL_ENV,
    MESSAGE,
    REQUIRED_CODE_FILES,
    load_control,
    read_control,
)
from core.execution.empirical import SCOPE, BudgetAnchor, EmpiricalExecutionPolicy
from core.execution.policy import now
from core.execution.store import ExecutionStore
from core.planner.runtime import PlannerRuntime


def private_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    path.chmod(0o600)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def make_control(tmp_path, mode="observe_only", legacy=False):
    root = Path(tmp_path).resolve()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    backend = root / "backend"
    backend.mkdir(mode=0o700)
    code = backend / "app.py"
    code.write_text("# synthetic backend, no model client\n")
    for name in REQUIRED_CODE_FILES:
        target = backend / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("# synthetic inert code closure\n")
    roots = {name: str(root / name) for name in ("creator", "planner", "execution", "fees")}
    briefs = CreatorBriefStore(roots["creator"])
    brief = briefs.create("synthetic-create-key", {"title": "Synthetic source", "script": "Synthetic scene.", "requirements": "Synthetic requirements.", "production": copy.deepcopy(PRODUCTION), "budget_cny": "100.00"})["brief"]
    planner = PlannerRuntime(roots["planner"], briefs)
    task = planner.create({"client_request_key": "synthetic-planner-key", "brief_id": brief["id"], "brief_revision": 1})["task"]
    admission = {"task_id": task["id"], "planner_task_revision": task["revision"], "snapshot_hash": task["source"]["snapshot_hash"], "brief_id": brief["id"], "brief_revision": 1, "source_hash": task["source"]["source_hash"]}
    manifest = {path.relative_to(backend).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest() for path in backend.rglob("*.py")}
    authorization = {"task_id": "synthetic-parent", "status": "approved_bounded_live_api_trial", "budget": {"currency": "CNY", "hard_total": 100}, "provider_origin": "https://synthetic.invalid"}
    request = {"kind": "empirical_product_text_role_scope_envelope", "model": "synthetic-model", "endpoint": "https://synthetic.invalid/v1/chat/completions", "source_hash": admission["source_hash"], "role_workflow": "bounded_revision_v1", "max_new_calls": 6, "per_call_risk_cny": "1.000000", "original_budget_cny": "100.00", "opening_retained_risk_cny": "3.00", "opening_budget_record_count": 2, "prior_parent_ledger_hash": "1" * 64, "backend_manifest_hash": digest(manifest), "risk_basis": "empirical_not_hard_billing_bound", "pricing_observation_hash": "2" * 64}
    if not legacy:
        request.update(scope_status="open", admission=copy.deepcopy(admission), chat_output_parameter="max_completion_tokens", scope_risk_cny="6.00")
    allocation = {"operation_key": "synthetic-scope", "request_sha256": digest(request), "request": request, "upper_fen": 600, "quote_ref": "synthetic-quote", "status": "reserved", "provider_task_id": None, "actual_charge_fen": None}
    ledger = {"schema_version": 1, "task_id": "synthetic-parent", "currency": "CNY", "authorization_hash": digest(authorization), "limit_fen": 10000, "operations": {"old-call": {"request": {"kind": "legacy"}, "upper_fen": 100}, "old-scope": {"request": {"max_new_calls": 3}, "upper_fen": 200}, "synthetic-scope": allocation}}
    anchor = {"schema_version": 1, "account_scope_id": "synthetic-account", "parent_authorization_hash": digest(authorization), "parent_ledger_hash": "3" * 64, "parent_allocation_hash": digest(allocation), "original_budget_cny": "100.00", "opening_exposure_cny": "3.00", "opening_call_count": 4, "scope_risk_cny": "6.00", "scope_max_calls": 6, "account_limit_cny": "9.00", "account_max_calls": 10, "currency": "CNY"}
    policy = {"kind": "empirical_risk_v1", "id": "synthetic-control", "version": "1", "provider": "openai_compatible", "model": request["model"], "endpoint": request["endpoint"], "wire_api": "chat_completions", "chat_output_parameter": "max_completion_tokens", "role_workflow": "bounded_revision_v1", "currency": "CNY", "privacy_scope": SCOPE, "risk_basis": request["risk_basis"], "per_call_risk_cny": "1.000000", "parent_anchor_hash": "4" * 64, "input_cny_per_million": "1", "output_cny_per_million": "2", "max_output_tokens": 1000, "max_input_bytes": 100000, "max_output_bytes": 65536, "request_deadline_seconds": 120, "max_calls": 6, "task_limit_cny": "6.00", "account_limit_cny": "9.00", "account_max_calls": 10, "account_scope_id": "synthetic-account", "valid_until": (now() + timedelta(hours=1)).isoformat(), "pricing_basis": "Synthetic pricing only", "operator_risk_approved": True, "validation_only": False}
    path, auth_path, ledger_path = root / "control.json", root / "authorization.json", root / "ledger.json"
    data = {"schema_version": 1, "mode": mode, "roots": roots, "policy": {}, "budget_anchor": {}, "authority": {"authorization_path": str(auth_path), "authorization_sha256": "5" * 64, "parent_ledger_path": str(ledger_path), "allocation_key": "synthetic-scope"}, "code_manifest": manifest, "admission": admission, "fee_source": {"account_namespace": "synthetic-account", "token_name": "synthetic-token", "group": "synthetic-group", "quota_per_cny": 500000}, "observer_enabled": False}

    def save():
        data["authority"]["authorization_sha256"] = private_json(auth_path, authorization)
        anchor["parent_authorization_hash"] = digest(authorization)
        ledger["authorization_hash"] = digest(authorization)
        allocation["request_sha256"] = digest(request)
        anchor["parent_allocation_hash"] = digest(allocation)
        anchor["parent_ledger_hash"] = private_json(ledger_path, ledger)
        data["budget_anchor"] = BudgetAnchor.model_validate(anchor).frozen()
        policy["parent_anchor_hash"] = data["budget_anchor"]["parent_anchor_hash"]
        data["policy"] = EmpiricalExecutionPolicy.model_validate(policy).frozen()
        private_json(path, data)

    save()
    return SimpleNamespace(root=root, backend=backend, code=code, roots=roots, planner=planner, path=path, auth_path=auth_path, ledger_path=ledger_path, data=data, authorization=authorization, ledger=ledger, request=request, allocation=allocation, anchor=anchor, policy=policy, save=save)

@pytest.fixture
def bundle(tmp_path):
    resources = []

    def make(mode="enabled", legacy=False):
        env = make_control(tmp_path / str(len(resources)), mode=mode, legacy=legacy)
        resources.append(env.planner)
        return env

    yield make
    for planner in resources:
        planner.close()


def rejected(call):
    with pytest.raises(CreatorError) as error:
        call()
    assert error.value.code == "CONTROL_UNAVAILABLE" and error.value.message == MESSAGE
    assert error.value.args == ("CONTROL_UNAVAILABLE", MESSAGE, 503)


def test_off_uses_only_the_explicit_pointer_and_does_not_create_files(tmp_path):
    assert load_control(tmp_path.resolve(), environ={}) is None
    assert load_control(tmp_path.resolve(), environ={CONTROL_ENV: ""}) is None
    assert load_control(tmp_path.resolve(), environ={"LEONTV_EXECUTION_ENABLED": "true"}) is None
    assert list(tmp_path.iterdir()) == []
    rejected(lambda: load_control(tmp_path.resolve(), environ={CONTROL_ENV: str(tmp_path / "absent.json")}))


def test_enabled_load_and_guard_preserve_files_and_never_create_an_execution_store(bundle):
    env = bundle()
    before = {path: path.read_bytes() for path in env.root.rglob("*") if path.is_file()}
    control = load_control(env.backend, environ={CONTROL_ENV: str(env.path)})
    assert control.roots["planner"] == Path(env.roots["planner"])
    assert control.verify_authority(env.backend, for_dispatch=True)
    assert control.check_admission(env.planner, env.data["admission"]["task_id"])
    assert control.guard(env.data["budget_anchor"], env.planner, ExecutionStore(env.roots["execution"]))
    assert not Path(env.roots["execution"]).exists() and not Path(env.roots["fees"]).exists()
    assert before == {path: path.read_bytes() for path in env.root.rglob("*") if path.is_file()}


def test_historical_observation_accepts_expiry_and_code_drift_without_dispatch(bundle):
    env = bundle(mode="observe_only", legacy=True)
    env.policy["valid_until"] = (now() - timedelta(days=1)).isoformat()
    env.save()
    env.code.write_text("# changed public code\n")
    control = read_control(env.path, env.backend)
    assert control.verify_authority(env.backend)
    rejected(lambda: control.verify_authority(env.backend, for_dispatch=True))
    assert not control.guard(env.data["budget_anchor"], env.planner, ExecutionStore(env.roots["execution"]))
    env.data["mode"] = "enabled"
    private_json(env.path, env.data)
    rejected(lambda: read_control(env.path, env.backend))


def test_legacy_scope_cannot_activate_unused_calls_after_restart(bundle):
    env = bundle(legacy=True)
    rejected(lambda: read_control(env.path, env.backend))


@pytest.mark.parametrize("target", ["control", "authorization", "ledger", "code"])
def test_raw_file_drift_stops_loaded_authority(bundle, target):
    env = bundle()
    control = read_control(env.path, env.backend)
    path = {"control": env.path, "authorization": env.auth_path, "ledger": env.ledger_path, "code": env.code}[target]
    path.write_bytes(path.read_bytes() + b"\n")
    rejected(lambda: control.verify_authority(env.backend, for_dispatch=True))
    assert not control.guard(env.data["budget_anchor"], env.planner, ExecutionStore(env.roots["execution"]))


@pytest.mark.parametrize("change", ["wrong_model", "wrong_endpoint", "wrong_output", "wrong_source", "wrong_workflow", "closed", "started", "wrong_manifest", "wrong_admission", "too_few_calls", "wrong_opening", "wrong_limit", "wrong_original"])
def test_rehashed_authority_must_remain_semantically_bound(bundle, change):
    env = bundle()
    if change == "wrong_model":
        env.request["model"] = "synthetic-other-model"
    elif change == "wrong_endpoint":
        env.request["endpoint"] = "https://synthetic-other.invalid/v1/chat/completions"
    elif change == "wrong_output":
        env.request["chat_output_parameter"] = "max_tokens"
    elif change == "wrong_source":
        env.request["source_hash"] = "f" * 64
    elif change == "wrong_workflow":
        env.request["role_workflow"] = "unbounded"
    elif change == "closed":
        env.request["scope_status"] = "closed"
    elif change == "started":
        env.allocation["status"] = "submitting"
    elif change == "wrong_manifest":
        env.request["backend_manifest_hash"] = "f" * 64
    elif change == "wrong_admission":
        env.request["admission"]["task_id"] = "f" * 32
    elif change == "too_few_calls":
        env.anchor.update(opening_call_count=2, account_max_calls=8)
        env.policy["account_max_calls"] = 8
    elif change == "wrong_opening":
        env.ledger["operations"]["old-call"]["upper_fen"] = 99
    elif change == "wrong_limit":
        env.ledger["limit_fen"] = 9900
    else:
        env.authorization["budget"]["hard_total"] = 99
    env.save()
    rejected(lambda: read_control(env.path, env.backend))


@pytest.mark.parametrize("field", ["task_id", "planner_task_revision", "snapshot_hash", "brief_id", "brief_revision", "source_hash"])
def test_rehashed_admission_cannot_select_another_task_or_source_version(bundle, field):
    env = bundle()
    env.data["admission"][field] = 2 if field.endswith("revision") else "f" * (32 if field.endswith("_id") else 64)
    env.request["admission"] = copy.deepcopy(env.data["admission"])
    if field == "source_hash":
        env.request["source_hash"] = env.data["admission"][field]
    env.save()
    rejected(lambda: read_control(env.path, env.backend))


def test_loaded_guard_rejects_wrong_anchor_roots_and_in_memory_control_mutation(bundle):
    env = bundle()
    control = read_control(env.path, env.backend)
    wrong = dict(env.data["budget_anchor"], parent_allocation_hash="f" * 64)
    assert not control.guard(wrong, env.planner, ExecutionStore(env.roots["execution"]))
    assert not control.guard(env.data["budget_anchor"], env.planner, ExecutionStore(env.root / "other-execution"))
    rejected(lambda: control.check_admission(env.planner, "f" * 32))
    control.document.policy["model"] = "mutated"
    rejected(lambda: control.verify_authority(env.backend, for_dispatch=True))


@pytest.mark.parametrize("change", ["nested_roots", "relative_root", "extra_key", "bool_schema", "bool_fee_units", "float_fee_units", "absolute_manifest", "escape_manifest", "duplicate_json", "missing_frozen_hash"])
def test_control_contract_rejects_ambiguous_or_extra_inputs(bundle, change):
    env = bundle(mode="observe_only")
    if change == "nested_roots":
        env.data["roots"]["fees"] = str(Path(env.roots["execution"]) / "fees")
    elif change == "relative_root":
        env.data["roots"]["creator"] = "relative"
    elif change == "extra_key":
        env.data["api_key"] = "synthetic-secret-must-not-be-accepted"
    elif change == "bool_schema":
        env.data["schema_version"] = True
    elif change == "bool_fee_units":
        env.data["fee_source"]["quota_per_cny"] = True
    elif change == "float_fee_units":
        env.data["fee_source"]["quota_per_cny"] = 500000.0
    elif change == "absolute_manifest":
        env.data["code_manifest"] = {"/app.py": "f" * 64}
    elif change == "escape_manifest":
        env.data["code_manifest"] = {"../app.py": "f" * 64}
    elif change == "missing_frozen_hash":
        env.data["policy"].pop("policy_hash")
    private_json(env.path, env.data)
    if change == "duplicate_json":
        env.path.write_text(env.path.read_text().replace('"schema_version": 1,', '"schema_version": 1, "schema_version": 1,', 1))
    rejected(lambda: read_control(env.path, env.backend))


@pytest.mark.parametrize("target", ["control", "authorization", "ledger"])
@pytest.mark.parametrize("attack", ["symlink", "hardlink", "world_readable", "unsafe_parent"])
def test_private_proof_file_and_parent_protections(bundle, target, attack):
    env = bundle()
    path = {"control": env.path, "authorization": env.auth_path, "ledger": env.ledger_path}[target]
    if attack == "symlink":
        moved = path.with_suffix(".original")
        path.rename(moved)
        path.symlink_to(moved)
    elif attack == "hardlink":
        os.link(path, path.with_suffix(".linked"))
    elif attack == "world_readable":
        path.chmod(0o644)
    else:
        path.parent.chmod(0o777)
    rejected(lambda: read_control(env.path, env.backend))


def test_static_roots_cannot_cover_controls_authority_or_any_store(bundle):
    env = bundle()
    rejected(lambda: read_control(env.path, env.backend, static_roots=(env.root,)))
    env.data["roots"]["execution"] = str(env.backend / "code" / "private")
    private_json(env.path, env.data)
    rejected(lambda: read_control(env.path, env.backend))


def test_public_source_symlink_and_owner_mismatch_fail_without_diagnostics(bundle, monkeypatch):
    env = bundle()
    other = env.backend / "other.py"
    env.code.rename(other)
    env.code.symlink_to(other)
    rejected(lambda: read_control(env.path, env.backend))
    env.code.unlink()
    other.rename(env.code)
    monkeypatch.setattr(os, "getuid", lambda: env.path.stat().st_uid + 1)
    rejected(lambda: read_control(env.path, env.backend))


@pytest.mark.parametrize("change", ["unrelated_only", "missing_control", "new_module"])
def test_enabled_manifest_covers_the_runtime_closure_even_after_rehash(bundle, change):
    env = bundle()
    if change == "unrelated_only":
        env.data["code_manifest"] = {"app.py": env.data["code_manifest"]["app.py"]}
    elif change == "missing_control":
        env.data["code_manifest"].pop("core/execution/control.py")
    else:
        (env.backend / "core/execution/new_module.py").write_text("# newly imported module\n")
    env.request["backend_manifest_hash"] = digest(env.data["code_manifest"])
    env.save()
    rejected(lambda: read_control(env.path, env.backend))


@pytest.mark.parametrize("target", ["creator", "planner", "execution", "fees", "control", "authorization", "ledger"])
def test_actual_app_mounts_cannot_expose_any_control_or_proof_path(bundle, target):
    env = bundle(mode="observe_only")
    control = read_control(env.path, env.backend)
    path = {**control.roots, "control": env.path, "authorization": env.auth_path, "ledger": env.ledger_path}[target]
    before = {file: file.read_bytes() for file in env.root.rglob("*") if file.is_file()}
    rejected(lambda: control.check_private([path]))
    assert before == {file: file.read_bytes() for file in env.root.rglob("*") if file.is_file()}
