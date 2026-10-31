"""Self-sealed observation consistency still never substitutes native authority."""
import copy
from datetime import timedelta

import pytest
from test_image_comparison_policy import synthetic_descriptor
from test_image_policy import accepted_raw
from test_image_receipts import image_bytes, raw_for

from core.creator.store import CreatorError, digest
from core.execution import image_comparison_observation as contract
from core.execution.image_capture import worker_request
from core.execution.image_comparison_observation_policy import make_request
from core.execution.image_comparison_observation_runtime import ComparisonObservationExecution
from core.execution.image_observation_continuation import _effective
from core.execution.image_policy import FrozenImageRequest, timestamp
from core.execution.image_receipts import decode_query, decode_submit, verify_delivery
from core.execution.policy import now
from core.execution.receipts import ReceiptCaptureSettings, ReceiptStore


def observation_descriptor():
    _, parent, request = synthetic_descriptor()
    raw = accepted_raw(request)
    raw["headers"] = {}
    raw["received_at"] = request.frozen()["permit"]["issued_at"]
    receipt = decode_submit(request, raw)
    a = {
        "schema_version": 1, "kind": contract.AUTHORITY_KIND, "activation_id": "d" * 32, "quote_id": "e" * 32,
        "control_hash": "0" * 64, "scope_id": parent["scope_id"], "task_id": parent["task_id"],
        "source_operation_id": request.frozen()["permit"]["operation_id"], "source_operation_hash": "1" * 64,
        "source_descriptor_hash": request.descriptor_hash, "source_raw_hash": digest(raw),
        "source_receipt_hash": receipt["receipt_hash"], "source_capture_started_at": raw["received_at"],
        "accepted_task_id": receipt["accepted_task_id"], "parent_authority": parent, "parent_quote_hash": "2" * 64,
        "retained_unknowns": [{"operation_id": str(i) * 32} for i in range(3)], "original_baseline_hash": "3" * 64,
        "effective_policy": _effective(parent["policy"], (now() + timedelta(hours=2)).isoformat()),
        "response_policy": parent["response_policy"], "code_manifest_hash": "4" * 64, **contract.LIMITS,
    }
    a["authority_hash"] = digest(a)
    operation = {"descriptor": request.frozen(), "raw": raw}
    settings = ReceiptCaptureSettings(worker_factory="image_tool_fixture:make_transport")
    query = make_request(a, operation, identifier="f" * 32, query_index=1, settings=settings)
    return a, operation, query, settings


def test_compact_get_bound_original_parent_once_and_completed_receipt(tmp_path):
    a, op, query, settings = observation_descriptor()
    frozen = query.frozen()
    assert frozen["method"] == "GET" and frozen["wire"] == ""
    assert frozen["parent_submit"] == op["descriptor"] and "references" not in frozen
    assert frozen["permit"]["paid_calls"] == 0 and frozen["permit"]["risk_cny"] == "0.000000"
    assert frozen["policy"]["valid_until"] == a["effective_policy"]["valid_until"]
    raw = raw_for(query, {"code": 0, "data": {"id": a["accepted_task_id"], "status": "completed",
                                               "result": {"images": [{"url": "https://synthetic.invalid/result.png"}]}}})
    assert decode_query(query, raw)["classification"] == "completed"
    assert verify_delivery(query, raw, image_bytes())["visual_quality"] == "not_reviewed"
    receipts = ReceiptStore(tmp_path / "receipts")
    binding = query.binding()
    response = timestamp(binding["response_deadline_at"])
    operation, _ = receipts.prepare(binding, "a" * 64, "b" * 64, response.isoformat(), (response + timedelta(seconds=5)).isoformat())
    claim = operation.claim(binding)
    assert claim["version"] == 2
    job = {"kind": binding["kind"], "descriptor": frozen, "binding": binding, "worker_factory": settings.worker_factory,
           "worker_options": settings.worker_options, "api_key": "SYNTHETIC"}
    assert worker_request(job, claim)[4] == "GET"


@pytest.mark.parametrize("key,value", [
    ("post_cap", 1), ("post_cap", False), ("additional_paid_calls", True), ("additional_risk_cny", "1.000000"),
    ("max_new_queries", 2), ("max_new_queries", True), ("B_authorized", True),
    ("formal_delivery_authorized", True), ("exact_fee_required_for_GET", False), ("automatic_followup_calls", 1),
])
def test_resealed_observation_cannot_gain_posts_funds_or_polling(key, value):
    a, op, _, settings = observation_descriptor()
    a[key] = value
    a["authority_hash"] = digest({k: v for k, v in a.items() if k != "authority_hash"})
    with pytest.raises(CreatorError):
        make_request(a, op, identifier="f" * 32, query_index=1, settings=settings)


@pytest.mark.parametrize("attack", ["POST", "endpoint", "task", "raw", "wire", "authority", "response", "cleanup", "index", "namespace"])
def test_resealed_get_mutations_reject(attack):
    a, _, query, _ = observation_descriptor()
    d = copy.deepcopy(query.frozen())
    if attack == "POST":
        d["method"] = "POST"
    elif attack == "endpoint":
        d["endpoint"] += "wrong-task"
    elif attack == "task":
        d["accepted_task_id"] = "SYNTHETIC-WRONG-TASK"
    elif attack == "raw":
        d["submit_raw"]["body_base64"] = "e30="
    elif attack == "wire":
        d["parent_submit"]["wire"] += " "
    elif attack == "authority":
        d["observation_authority"]["source_operation_id"] = "0" * 32
        a = d["observation_authority"]
        a["authority_hash"] = digest({k: v for k, v in a.items() if k != "authority_hash"})
        d["permit"]["observation_authority_hash"] = a["authority_hash"]
    elif attack == "response":
        d["permit"]["response_deadline_at"] = (timestamp(d["permit"]["response_deadline_at"]) + timedelta(seconds=1)).isoformat()
    elif attack == "cleanup":
        d["policy"]["valid_until"] = d["permit"]["response_deadline_at"]
    elif attack == "index":
        d["query_index"] = True
        d["permit"]["query_index"] = True
    else:
        d["scope"] = "accepted_plan_image_tools_v1"
    d["permit"]["permit_hash"] = digest({k: v for k, v in d["permit"].items() if k != "permit_hash"})
    d["descriptor_hash"] = digest({k: v for k, v in d.items() if k != "descriptor_hash"})
    with pytest.raises(CreatorError):
        FrozenImageRequest.from_frozen(d)


@pytest.mark.parametrize("verb", ["quote", "grant", "reserve", "reserve_submit", "submit", "revoke", "observe_fee", "_dispatch", "_observe_fee"])
def test_observer_has_no_submission_or_funding_entry(verb):
    observer = ComparisonObservationExecution.__new__(ComparisonObservationExecution)
    with pytest.raises(CreatorError):
        getattr(observer, verb)("SYNTHETIC")


def test_response_tail_remains_original_180_and_cutoff_rejects():
    a, _, query, _ = observation_descriptor()
    raw = raw_for(query, {"code": 0, "data": {"id": a["accepted_task_id"], "status": "pending"}})
    deadline = timestamp(query.binding()["deadline_at"])
    raw["received_at"] = (deadline + timedelta(seconds=179)).isoformat()
    assert decode_query(query, raw)["classification"] == "pending"
    raw["received_at"] = (deadline + timedelta(seconds=180, microseconds=1)).isoformat()
    assert decode_query(query, raw)["classification"] == "unconfirmed"
