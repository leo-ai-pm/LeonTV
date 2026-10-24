"""Complete synthetic exports only; no account, configuration or live network."""
import base64
import copy
import hashlib
import json
import multiprocessing
from datetime import datetime, timedelta, timezone

import pytest
from test_execution_empirical import empirical_env as empirical_env
from test_execution_empirical import start
from test_planner_execution import no_network as no_network
from test_planner_execution import policy as formal_policy

from core.creator.store import CreatorError, digest
from core.execution.empirical import (
    SCOPE,
    BudgetAnchor,
    EmpiricalExecutionPolicy,
    decode,
    initial,
    observation,
)
from core.execution.fees import FeeInbox, FrozenFeeSource, request_key
from core.execution.transport import wire_for

SOURCE = {"account_namespace": "synthetic-tudou-account", "token_name": "synthetic-token", "group": "synthetic-group", "quota_per_cny": 500000}


def fixture_operation(number=1, output=20, *, shared_policy=None):
    anchor = BudgetAnchor(account_scope_id="synthetic-empirical-account", parent_authorization_hash="a" * 64, parent_ledger_hash="b" * 64,
        parent_allocation_hash="c" * 64, original_budget_cny="100", opening_exposure_cny="69.37", opening_call_count=41,
        scope_risk_cny="6", scope_max_calls=6, account_limit_cny="75.37", account_max_calls=47)
    data = formal_policy().model_dump()
    for key in ("max_input_tokens", "input_bound_basis", "operator_approved", "billing_bound_verified"):
        data.pop(key)
    data.update(account_scope_id=anchor.account_scope_id, parent_anchor_hash=anchor.frozen()["parent_anchor_hash"],
        account_limit_cny="75.37", account_max_calls=47, task_limit_cny="6", chat_output_parameter="max_completion_tokens",
        operator_risk_approved=True, privacy_scope=SCOPE)
    policy = shared_policy or EmpiricalExecutionPolicy.model_validate(data).frozen()
    context = [{"role": "system", "content": "SYNTHETIC"}, {"role": "user", "content": "SYNTHETIC"}]
    wire = wire_for(context, policy)
    received = datetime.now(timezone.utc)
    body = {"id": f"synthetic-response-{number}", "model": policy["model"], "choices": [{"message": {"role": "assistant", "content": "SYNTHETIC"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": output, "total_tokens": 10 + output}}
    raw = {"http_status": 200, "headers": {}, "body_base64": base64.b64encode(json.dumps(body).encode()).decode(), "received_at": received.isoformat()}
    permit = {"operation_id": f"{number:032x}", "task_id": "a" * 32, "policy_hash": policy["policy_hash"], "wire_hash": digest(wire),
              "context_hash": digest(context), "issued_at": (received - timedelta(seconds=1)).isoformat(), "model": policy["model"]}
    normalized = decode(raw, policy)[0]
    operation = {"id": permit["operation_id"], "task_id": permit["task_id"], "account_id": policy["account_scope_id"], "accounting_kind": policy["kind"],
                 "policy_hash": policy["policy_hash"], "wire": wire, "wire_hash": digest(wire), "context": context, "context_hash": digest(context),
                 "permit": permit, "raw": raw, "normalized": normalized, "empirical": initial()}
    operation["empirical"].update(fee_status="awaiting_platform_fee", receipt_classification="complete")
    return operation, policy


def row_for(operation, policy, request="synthetic-platform-request-1", quota=25, **changes):
    receipt = decode(operation["raw"], policy)[0]
    row = {"id": 1, "user_id": 10, "created_at": int(datetime.fromisoformat(operation["raw"]["received_at"]).timestamp()), "type": 2,
           "content": "SYNTHETIC", "username": "synthetic", "token_name": SOURCE["token_name"], "model_name": policy["model"], "quota": quota,
           "prompt_tokens": receipt["input_tokens"], "completion_tokens": receipt["output_tokens"], "use_time": 1, "is_stream": False,
           "channel": 1, "channel_name": "synthetic", "token_id": 1, "group": SOURCE["group"], "ip": "127.0.0.1", "request_id": request, "other": "{}"}
    return row | changes


def export(*rows, **changes):
    data = {"page": 1, "page_size": 100, "total": len(rows), "items": list(rows)} | changes
    return json.dumps({"success": True, "message": "SYNTHETIC", "data": data}, ensure_ascii=False).encode()


def test_default_readonly_and_source_freeze(tmp_path):
    root = tmp_path / "absent"
    inbox = FeeInbox(root, SOURCE)
    assert inbox.status() == {"exports": 0, "prepared": 0, "applied": 0}
    assert inbox.drain(None) == {"prepared": 0, "applied": 0, "pending": 0, "conflicts": 0}
    assert not root.exists()
    with pytest.raises(Exception):
        FrozenFeeSource(**(SOURCE | {"quota_per_cny": 1}))
    operation, policy = fixture_operation()
    inbox.import_bytes(export(row_for(operation, policy)))
    with pytest.raises(CreatorError) as error:
        FeeInbox(root, SOURCE | {"group": "other"}).status()
    assert error.value.code == "FEE_INBOX_CORRUPT"


def test_one_export_many_operations_exact_replay_and_overlapping_ordinal(tmp_path):
    first, policy = fixture_operation()
    second, _ = fixture_operation(2, output=21, shared_policy=policy)
    inbox = FeeInbox(tmp_path / "fees", SOURCE)
    row1, row2 = row_for(first, policy), row_for(second, policy, request="synthetic-platform-request-2")
    raw = export(row1, row2)
    export_hash = inbox.import_bytes(raw)
    one, two = inbox.prepare(first, policy), inbox.prepare(second, policy)
    assert one["source_export_hash"] == two["source_export_hash"] == export_hash
    assert one["platform_log_id"] == request_key(row1["request_id"]) != two["platform_log_id"]
    assert one["provider_request_id"] is None and one["provider_response_id"] != one["platform_log_id"]
    original = (tmp_path / "fees" / "state.json").read_bytes()
    restarted = FeeInbox(tmp_path / "fees", SOURCE)
    assert restarted.import_bytes(raw) == export_hash and restarted.prepare(first, policy) == one
    assert (tmp_path / "fees" / "state.json").read_bytes() == original
    restarted.import_bytes(export(row1 | {"id": 99}, row2 | {"id": 42}))
    assert restarted.prepare(first, policy) == one and restarted.verifier(one, first, policy) is True


@pytest.mark.parametrize("change", [{"page": 2}, {"page_size": 99}, {"total": 2}, {"total": True}])
def test_partial_or_wrong_page_is_rejected_without_state(tmp_path, change):
    operation, policy = fixture_operation()
    inbox = FeeInbox(tmp_path / "fees", SOURCE)
    with pytest.raises(CreatorError):
        inbox.import_bytes(export(row_for(operation, policy), **change))
    assert not inbox.root.exists()


def test_duplicate_json_and_conflicting_request_rows_rejected(tmp_path):
    inbox = FeeInbox(tmp_path / "fees", SOURCE)
    with pytest.raises(CreatorError):
        inbox.import_bytes(b'{"success":true,"success":true,"message":"","data":{}}')
    operation, policy = fixture_operation()
    row = row_for(operation, policy)
    with pytest.raises(CreatorError) as error:
        inbox.import_bytes(export(row, row | {"quota": 26}))
    assert error.value.code == "FEE_EVIDENCE_CONFLICT"


@pytest.mark.parametrize("field,value", [("token_name", "wrong"), ("group", "wrong"), ("model_name", "wrong"), ("prompt_tokens", 11),
                                         ("completion_tokens", 21), ("created_at", 1), ("type", 1)])
def test_wrong_platform_tuple_cannot_prepare(tmp_path, field, value):
    operation, policy = fixture_operation()
    inbox = FeeInbox(tmp_path / "fees", SOURCE)
    inbox.import_bytes(export(row_for(operation, policy, **{field: value})))
    with pytest.raises(CreatorError) as error:
        inbox.prepare(operation, policy)
    assert error.value.code == "FEE_EXPORT_PENDING" and inbox.status()["prepared"] == 0


def test_ambiguity_never_uses_claim_filtering_and_request_cannot_cross_operation(tmp_path):
    first, policy = fixture_operation()
    second, _ = fixture_operation(2, shared_policy=policy)
    inbox = FeeInbox(tmp_path / "fees", SOURCE)
    row = row_for(first, policy)
    inbox.import_bytes(export(row))
    evidence = inbox.prepare(first, policy)
    with pytest.raises(CreatorError) as error:
        inbox.prepare(second, policy)
    assert error.value.code == "FEE_EVIDENCE_CONFLICT"
    inbox.import_bytes(export(row_for(first, policy, request="another-request")))
    with pytest.raises(CreatorError) as error:
        inbox.prepare(second, policy)
    assert error.value.code == "FEE_EXPORT_AMBIGUOUS"
    assert inbox.verifier(evidence, first, policy) is True


@pytest.mark.parametrize("field", ["wire", "raw", "model", "amount", "observed_at", "export_hash"])
def test_verifier_reopens_export_and_rejects_changed_evidence_or_receipt(tmp_path, field):
    operation, policy = fixture_operation()
    inbox = FeeInbox(tmp_path / "fees", SOURCE)
    export_hash = inbox.import_bytes(export(row_for(operation, policy)))
    evidence = inbox.prepare(operation, policy)
    before = (inbox.root / "state.json").read_bytes()
    op, proposed = copy.deepcopy(operation), dict(evidence)
    if field == "wire":
        op["wire"] += " "
    elif field == "raw":
        (inbox.root / (export_hash + ".json")).write_bytes(b"{}")
    else:
        key = {"model": "model", "amount": "platform_fee_cny", "observed_at": "observed_at", "export_hash": "source_export_hash"}[field]
        proposed[key] = "changed"
    assert inbox.verifier(proposed, op, policy) is False
    assert (inbox.root / "state.json").read_bytes() == before


def test_legacy_exact_alias_preserves_native_and_blocks_other_aliases(tmp_path):
    operation, policy = fixture_operation()
    raw = export(row_for(operation, policy))
    producer = FeeInbox(tmp_path / "producer", SOURCE)
    producer.import_bytes(raw)
    evidence = producer.prepare(operation, policy) | {"platform_log_id": "tudou-log-1"}
    operation["empirical"].update(fee_status="matched", fee_evidence=observation(evidence, operation, policy))
    original = digest(operation)
    inbox = FeeInbox(tmp_path / "fees", SOURCE)
    assert inbox.register_legacy(operation, operation["empirical"]["fee_evidence"], raw, policy) == hashlib.sha256(raw).hexdigest()
    assert digest(operation) == original and inbox.verifier(evidence, operation, policy) is True
    changed = dict(evidence, platform_log_id=request_key("synthetic-platform-request-1"))
    assert inbox.verifier(changed, operation, policy) is False
    second, _ = fixture_operation(2, shared_policy=policy)
    second_evidence = dict(evidence, operation_id=second["id"], raw_body_sha256=hashlib.sha256(base64.b64decode(second["raw"]["body_base64"])).hexdigest(),
                           provider_response_id=second["normalized"]["provider_response_id"], platform_log_id="different-alias")
    second["empirical"].update(fee_status="matched", fee_evidence=observation(second_evidence, second, policy))
    with pytest.raises(CreatorError) as error:
        inbox.register_legacy(second, second_evidence, raw, policy)
    assert error.value.code == "FEE_EVIDENCE_CONFLICT"


def _prepare_child(root, operation, policy, queue):
    try:
        queue.put(("ok", FeeInbox(root, SOURCE).prepare(operation, policy)["operation_id"]))
    except CreatorError as error:
        queue.put(("error", error.code))


def test_process_lock_keeps_one_request_owner(tmp_path):
    first, policy = fixture_operation()
    second, _ = fixture_operation(2, shared_policy=policy)
    root = tmp_path / "fees"
    FeeInbox(root, SOURCE).import_bytes(export(row_for(first, policy)))
    context = multiprocessing.get_context("fork")
    queue = context.Queue()
    processes = [context.Process(target=_prepare_child, args=(root, operation, policy, queue)) for operation in (first, second)]
    for process in processes:
        process.start()
    results = [queue.get(timeout=5) for _ in processes]
    for process in processes:
        process.join(timeout=5)
        assert process.exitcode == 0
    assert sorted(result[0] for result in results) == ["error", "ok"]
    assert ("error", "FEE_EVIDENCE_CONFLICT") in results


def test_native_delivery_crash_recovery_and_overrun(empirical_env, tmp_path, monkeypatch):
    env = empirical_env("first_accepted")
    task, pending = start(env)
    stopped = env.action(task, pending, "stop", "stop-before-export-inbox").json()["execution"]
    operation = env.runtime.store.read_only()["operations"][stopped["operations"][-1]["id"]]
    inbox = FeeInbox(tmp_path / "fees", SOURCE)
    env.runtime.fee_verifier = inbox.verifier
    inbox.import_bytes(export(row_for(operation, env.runtime.policy, quota=4500000)))
    original = env.runtime.observe_fee

    def crash_after_native(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("SYNTHETIC crash after native commit")
    monkeypatch.setattr(env.runtime, "observe_fee", crash_after_native)
    assert inbox.drain(env.runtime, observe_only=False)["pending"] == 1
    native = env.runtime.store.read_only()
    current = native["operations"][operation["id"]]
    assert current["empirical"]["platform_units"] == 9000000 and current["hold_units"] == 1000000
    assert native["tasks"][task["id"]]["reason"] == "EMPIRICAL_RISK_OVERRUN" and len(env.calls()) == 1
    assert inbox.status()["prepared"] == 1
    monkeypatch.setattr(env.runtime, "observe_fee", lambda *args, **kwargs: pytest.fail("Native evidence must only be acknowledged"))
    assert FeeInbox(inbox.root, SOURCE).drain(env.runtime, observe_only=False)["applied"] == 1
    assert inbox.status() == {"exports": 1, "prepared": 0, "applied": 1}
    assert len(env.calls()) == 1
