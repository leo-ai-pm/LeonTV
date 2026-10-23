"""D17 historical read prefix proofs, built only from synthetic data."""
import base64
import copy
import hashlib
import importlib
import json
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta

import pytest
from empirical_worker_fixtures import descriptor_for, rebind
from test_empirical_chat_receipt import ACTION, body, raw

from core.creator.store import PRODUCTION, digest, source_hash
from core.planner.protocol import execute_tool
from experiments.tudou_ab.ledger import fingerprint


def module():
    return importlib.import_module("experiments.planner_live.read_context")


def fixture(*, protocol_sha256="d" * 64):
    source = {"brief_id": "a" * 32, "brief_revision": 1, "source_version": 1,
        "title": "Synthetic source", "script": "SYNTHETIC PRIVATE SOURCE", "requirements": "SYNTHETIC ONLY",
        "production": copy.deepcopy(PRODUCTION), "budget_cny": "100.00", "currency": "CNY",
        "frozen_at": "2026-10-23T00:00:00+00:00"}
    source["source_hash"] = source_hash(source["script"])
    source["snapshot_hash"] = digest(source)
    context = [{"role": "system", "content": {"z_instruction": "SYNTHETIC strict tools", "a_rules": "read first"}},
        {"role": "user", "content": {"task_id": "synthetic-task", "source_snapshot_hash": source["snapshot_hash"]}}]
    initial = descriptor_for(seconds=120, context=context)
    intent = replace(initial.intent, source_snapshot_hash=source["snapshot_hash"],
        intent_id="SYNTHETIC-PARENT/E01", operation_key="SYNTHETIC-PARENT/E01")
    descriptor = rebind(initial, intent=intent)
    issued = datetime.fromisoformat(intent.issued_at)
    value = body()
    value["choices"][0]["message"]["reasoning_content"] = "SYNTHETIC PRIVATE THOUGHT NEVER INHERITED"
    envelope = raw(value, with_sha=True)
    envelope["received_at"] = (issued + timedelta(seconds=1)).isoformat()
    body_sha = envelope["body_sha256"]
    controller_intent = {"key": "E01", "operation": intent.operation_key, "capture_id": intent.capture_id,
        "issued_at": intent.issued_at, "deadline_at": intent.deadline_at,
        "body_sha256": fingerprint(json.loads(descriptor.wire)),
        "wire_sha256": hashlib.sha256(descriptor.wire.encode()).hexdigest(), "plan_sha256": "f" * 64,
        "source_snapshot_hash": source["snapshot_hash"], "rules_sha256": intent.rules_hash,
        "protocol_sha256": protocol_sha256, "context_sha256": fingerprint(context),
        "endpoint_sha256": hashlib.sha256(intent.endpoint.encode()).hexdigest(), "model_sha256": hashlib.sha256(intent.model.encode()).hexdigest()}
    summary = {"state": "received", "http_status": 200, "key": "E01", "operation": intent.operation_key,
        "model_requested": intent.model, "model_returned": value["model"],
        "wire_sha256": controller_intent["wire_sha256"], "usage": {"prompt_tokens": 17, "completion_tokens": 11, "total_tokens": 28},
        "provider_request_id": None, "provider_response_id": value["id"], "capture_id": intent.capture_id,
        "original_deadline_at": intent.deadline_at, "output_parameter": intent.chat_output_parameter, "requested_output_limit": intent.max_output_tokens}
    charge = {"operation": intent.operation_key, "key": "E01", "provider_response_id": value["id"], "http_request_id": None,
        "provider_log_id": "synthetic-log-only", "provider_time": "2026-10-23 12:00:00", "raw_body_sha256": body_sha,
        "prompt_tokens": 17, "completion_tokens": 11, "platform_fee_cny": "0.001512",
        "within_operation_risk_allocation": True, "token_name": "SYNTHETIC TOKEN", "group": "synthetic", "model": value["model"],
        "source_export": "synthetic-export.xlsx", "source_export_sha256": "e" * 64, "native_quota": 1512,
        "formal_invoice_confirmed": False, "production_normalizer_settled": False, "unit": "export fee(CNY)",
        "matching": "unique token/model/group/usage/time tuple, separate HTTP, body and platform-log IDs"}
    feedback, _, _ = execute_tool(ACTION, source, False, detailed_feedback=True)
    step = {"status": "processed", "key": "E01", "after_charge_status": "ready", "output_limit_observed": True, "read_before": False, "read_after": True, "tool_result": feedback,
        "intent_sha256": fingerprint(controller_intent), "summary_sha256": fingerprint(summary),
        "receipt_sha256": fingerprint(value), "raw_body_sha256": body_sha, "charge_sha256": fingerprint(charge)}
    inspection = {"operation_id": intent.capture_id, "status": "candidate_found", "candidate_count": 1,
        "binding_verified": True, "isolated": True, "recovery_performed": False, "notification_present": True,
        "body_sha256": body_sha, "response_received_at": envelope["received_at"],
        "durable_confirmation_at": (issued + timedelta(seconds=2)).isoformat(),
        "original_deadline_at": intent.deadline_at, "observer_until": intent.deadline_at}
    proof = {"source": source, "initial_context": context, "descriptor": descriptor.frozen(), "raw": envelope,
        "intent": controller_intent, "summary": summary, "step": step, "charge": charge, "inspection": inspection}
    expected = {"proof_hash": digest(proof), "parent_operation": intent.operation_key, "parent_task_id": intent.task_id,
        "source_snapshot_hash": source["snapshot_hash"], "rules_hash": intent.rules_hash,
        "protocol_sha256": controller_intent["protocol_sha256"], "authorization_hash": intent.authorization_hash}
    return proof, expected


def verify(proof, expected):
    return module().verify_read_context(proof, expected)


def reject(proof, expected):
    with pytest.raises(module().ReadContextProofError) as error:
        verify(proof, expected)
    assert str(error.value) == error.value.code == "EMPIRICAL_READ_CONTEXT_REJECTED"
    assert not hasattr(error.value, "proof") and error.value.__cause__ is None


def test_actual_proof_seam_reconstructs_only_successful_read_prefix_without_thought_or_parent_state():
    proof, expected = fixture()
    before = copy.deepcopy(proof)
    result = verify(proof, expected)
    context = result.messages()
    assert context == proof["initial_context"] + [{"role": "assistant", "content": ACTION}, {"role": "tool", "content": proof["step"]["tool_result"]}]
    assert result.context_hash == digest(context) and result.proof_hash == expected["proof_hash"]
    assert result.anchors_hash == digest(expected) and result.source_snapshot_hash == expected["source_snapshot_hash"]
    assert "SYNTHETIC PRIVATE THOUGHT" not in json.dumps(result.frozen(), ensure_ascii=False)
    assert not hasattr(result, "read_source") and "status" not in result.frozen()
    context[-1]["content"]["source"]["script"] = "later mutation"
    assert result.messages()[-1]["content"]["source"]["script"] == proof["source"]["script"]
    with pytest.raises(FrozenInstanceError):
        result.context_json = "[]"
    assert proof == before


@pytest.mark.parametrize("part", ["source", "initial_context", "descriptor", "raw", "intent", "summary", "step", "charge", "inspection"])
def test_whole_proof_anchor_pins_every_original_component(part):
    proof, expected = fixture()
    if type(proof[part]) is dict:
        proof[part]["synthetic_changed"] = True
    else:
        proof[part].append({"role": "assistant", "content": "bad later tail"})
    reject(proof, expected)


@pytest.mark.parametrize("anchor", ["parent_operation", "parent_task_id", "source_snapshot_hash", "rules_hash", "protocol_sha256", "authorization_hash"])
def test_independent_expected_identities_cannot_be_replaced_by_self_reported_proof(anchor):
    proof, expected = fixture()
    expected[anchor] = "0" * 64 if anchor.endswith("hash") or anchor.endswith("sha256") else "other-synthetic"
    reject(proof, expected)


@pytest.mark.parametrize("case", ["source_snapshot", "source_script", "context_tail", "wire", "raw_unknown", "raw_body", "not_read", "feedback", "read_flag", "summary", "fee_missing", "fee_operation", "intent_link", "summary_link", "receipt_link", "charge_link", "late_received", "late_durable", "observer", "unknown_receipt"])
def test_structural_and_cross_link_checks_still_reject_when_hash_anchor_matches_input(case):
    proof, expected = fixture()
    if case == "source_snapshot":
        proof["source"]["snapshot_hash"] = "0" * 64
    elif case == "source_script":
        proof["source"]["script"] = "another source"
    elif case == "context_tail":
        proof["initial_context"].append({"role": "assistant", "content": "SYNTHETIC unknown truncated tail"})
    elif case == "wire":
        proof["descriptor"]["wire"] += " "
    elif case == "raw_unknown":
        proof["raw"]["platform_log_id"] = "cannot authorize raw"
    elif case == "raw_body":
        proof["raw"]["body_base64"] = base64.b64encode(b"{}").decode()
    elif case in {"not_read", "unknown_receipt"}:
        value = json.loads(base64.b64decode(proof["raw"]["body_base64"]))
        if case == "not_read":
            value["choices"][0]["message"]["content"] = '{"tool":"open_url","arguments":{}}'
        else:
            value["choices"][0]["finish_reason"] = "length"
        data = json.dumps(value).encode()
        proof["raw"].update(body_base64=base64.b64encode(data).decode(), body_sha256=hashlib.sha256(data).hexdigest())
    elif case == "feedback":
        proof["step"]["tool_result"]["source"]["script"] = "changed original feedback"
    elif case == "read_flag":
        proof["step"]["read_before"] = True
    elif case == "summary":
        proof["summary"]["state"] = "unknown"
        proof["step"]["summary_sha256"] = fingerprint(proof["summary"])
    elif case == "fee_missing":
        proof["charge"] = {}
        proof["step"]["charge_sha256"] = fingerprint({})
    elif case == "fee_operation":
        proof["charge"]["operation"] = "other-synthetic"
        proof["step"]["charge_sha256"] = fingerprint(proof["charge"])
    elif case.endswith("_link"):
        proof["step"][{"intent_link": "intent_sha256", "summary_link": "summary_sha256", "receipt_link": "receipt_sha256", "charge_link": "charge_sha256"}[case]] = "0" * 64
    elif case in {"late_received", "late_durable"}:
        deadline = proof["descriptor"]["intent"]["deadline_at"]
        if case == "late_received":
            proof["raw"]["received_at"] = deadline
            proof["inspection"]["response_received_at"] = deadline
        proof["inspection"]["durable_confirmation_at"] = deadline
    else:
        proof["inspection"]["observer_until"] = proof["raw"]["received_at"]
    expected["proof_hash"] = digest(proof)
    reject(proof, expected)


@pytest.mark.parametrize("field,value", [("binding_verified", False), ("notification_present", False), ("candidate_count", 2),
    ("status", "capture_error"), ("recovery_performed", True), ("body_sha256", "0" * 64), ("operation_id", "0" * 32)])
def test_raw_inspection_must_prove_one_bound_timely_durable_notified_candidate(field, value):
    proof, expected = fixture()
    proof["inspection"][field] = value
    expected["proof_hash"] = digest(proof)
    reject(proof, expected)


def test_parent_later_stop_is_not_an_input_or_executable_tail():
    proof, expected = fixture()
    first = verify(proof, expected)
    # Parent lifecycle and later E02 are outside this original successful proof.
    parent_later = {"status": "unknown", "read_source": True, "tail": "SYNTHETIC bad truncated output"}
    assert parent_later["status"] == "unknown" and verify(proof, expected).messages() == first.messages()
    proof["parent_run"] = parent_later
    expected["proof_hash"] = digest(proof)
    reject(proof, expected)


@pytest.mark.parametrize("case", ["within", "matching", "body_id", "http_id", "body_sha", "usage", "unconfirmed", "fee_bound"])
def test_actual_charge_record_links_and_observed_risk_are_required_even_with_new_anchor(case):
    proof, expected = fixture()
    charge = proof["charge"]
    if case == "within":
        charge["within_operation_risk_allocation"] = False
    elif case == "matching":
        charge["matching"] = "unconfirmed"
    elif case == "body_id":
        charge["provider_response_id"] = charge["provider_log_id"]
    elif case == "http_id":
        charge["http_request_id"] = charge["provider_response_id"]
    elif case == "body_sha":
        charge["raw_body_sha256"] = "0" * 64
    elif case == "usage":
        charge["completion_tokens"] += 4
    elif case == "unconfirmed":
        charge.clear()
    else:
        charge["platform_fee_cny"] = "1.01"
    proof["step"]["charge_sha256"] = fingerprint(charge)
    expected["proof_hash"] = digest(proof)
    reject(proof, expected)


def test_verify_is_pure_and_original_tool_call_only_validates_historical_feedback(monkeypatch):
    import builtins
    import os
    import subprocess

    import core.execution.policy as policy
    from core.execution.receipts import ReceiptStore
    from experiments.planner_live.empirical_capture import EmpiricalCaptureAdapter

    proof, expected = fixture()
    verifier = module()
    before = copy.deepcopy(proof)
    calls = []

    def historical_check(content, source, already_read, **kwargs):
        calls.append((content, already_read, kwargs))
        return execute_tool(content, source, already_read, **kwargs)

    def forbidden(*args, **kwargs):
        pytest.fail("Read proof verifier attempted external state, time or execution")

    with monkeypatch.context() as guard:
        guard.setattr(builtins, "open", forbidden)
        guard.setattr(os, "getenv", forbidden)
        guard.setattr(subprocess, "Popen", forbidden)
        guard.setattr(policy, "now", forbidden)
        guard.setattr(ReceiptStore, "inspect", forbidden)
        guard.setattr(EmpiricalCaptureAdapter, "capture", forbidden)
        guard.setattr(verifier, "execute_tool", historical_check)
        result = verifier.verify_read_context(proof, expected)
    assert calls == [(ACTION, False, {"detailed_feedback": True})]
    assert result.messages()[-1]["content"] == proof["step"]["tool_result"] and proof == before


@pytest.mark.parametrize("field", ["endpoint_sha256", "model_sha256"])
def test_original_endpoint_and_model_hashes_cannot_be_replaced_with_rehashed_records(field):
    proof, expected = fixture()
    proof["intent"][field] = "0" * 64
    proof["step"]["intent_sha256"] = fingerprint(proof["intent"])
    expected["proof_hash"] = digest(proof)
    reject(proof, expected)


@pytest.mark.parametrize("field,value", [("capture_id", "0" * 32), ("original_deadline_at", "2026-01-01T00:00:00+00:00"),
    ("output_parameter", "max_completion_tokens"), ("requested_output_limit", 8192)])
def test_optional_original_summary_binding_fields_must_match_parent(field, value):
    proof, expected = fixture()
    proof["summary"][field] = value
    proof["step"]["summary_sha256"] = fingerprint(proof["summary"])
    expected["proof_hash"] = digest(proof)
    reject(proof, expected)
