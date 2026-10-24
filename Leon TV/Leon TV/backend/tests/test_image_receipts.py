"""Synthetic image HTTP and byte checks; no model, network or visual review."""
import base64
import copy
import hashlib
import io
import json
from datetime import timedelta

import pytest
from PIL import Image
from test_image_policy import accepted_raw, make_query, make_submit

from core.creator.store import CreatorError, digest
from core.execution.image_policy import FrozenImageRequest
from core.execution.image_receipts import decode_query, decode_submit, verify_delivery
from core.execution.receipts import timestamp


def raw_for(request, body, *, status=200, headers=None):
    return {"http_status": status, "headers": headers or {},
            "body_base64": base64.b64encode(json.dumps(body, separators=(",", ":")).encode()).decode(),
            "received_at": (timestamp(request.frozen()["permit"]["issued_at"]) + timedelta(seconds=1)).isoformat()}


def query_raw(request, *, status="completed", task=None, result=None):
    task = task or request.frozen()["accepted_task_id"]
    return raw_for(request, {"code": 200, "data": {"id": task, "status": status,
                   "result": result if result is not None else {"images": [{"url": ["https://media.example/frame.png"]}]}}},
                   headers={"x-request-id": "query-request-only"})


def image_bytes(size=(90, 160), format_name="PNG"):
    output = io.BytesIO()
    Image.new("RGB", size, (50, 100, 150)).save(output, format=format_name)
    return output.getvalue()


def test_submission_exact_bytes_and_direct_request_identity_are_separate_from_task():
    request = make_submit()
    raw = raw_for(request, {"code": 200, "request_id": "billing-request", "data": {"id": "task-original"}},
                  headers={"x-request-id": "billing-request"})
    before = copy.deepcopy(raw)
    result = decode_submit(request, raw)
    assert result["classification"] == "accepted"
    assert result["accepted_task_id"] == "task-original"
    assert result["submit_request_id"] == "billing-request"
    assert result["request_id_status"] == "direct"
    assert result["raw_hash"] == digest(raw)
    assert result["raw_body_sha256"] == hashlib.sha256(base64.b64decode(raw["body_base64"])).hexdigest()
    assert result["receipt_hash"] == digest({key: value for key, value in result.items() if key != "receipt_hash"})
    assert raw == before


def test_missing_http_response_stays_unconfirmed_without_legacy_inference():
    result = decode_submit(make_submit(), None)
    assert result["classification"] == "unconfirmed"
    assert result["accepted_task_id"] is result["submit_request_id"] is result["raw_hash"] is None


def test_accepted_task_without_request_id_cannot_manufacture_billing_identity():
    request = make_submit()
    result = decode_submit(request, raw_for(request, {"code": 200, "data": {"id": "task-only"}}))
    assert result["classification"] == "accepted"
    assert result["accepted_task_id"] == "task-only"
    assert result["submit_request_id"] is None and result["request_id_status"] == "missing"


def test_explicit_body_request_identity_can_be_used_when_header_is_absent():
    request = make_submit()
    result = decode_submit(request, raw_for(request, {"code": 200, "request_id": "explicit-request", "data": {"id": "task"}}))
    assert result["classification"] == "accepted"
    assert result["submit_request_id"] == "explicit-request" and result["request_id_status"] == "direct"


@pytest.mark.parametrize("body", [
    {"code": True, "data": {"id": "task"}},
    {"code": "200", "data": {"id": "task"}},
    {"code": 200, "data": {"id": "task", "task_id": "other"}},
    {"code": 200, "data": {"id": True}},
    {"code": 200, "data": {"id": "../../task"}},
    {"code": 200, "data": {}},
    {"code": 200, "data": []},
])
def test_business_or_task_identity_corruption_is_unconfirmed(body):
    request = make_submit()
    result = decode_submit(request, raw_for(request, body))
    assert result["classification"] == "unconfirmed" and result["accepted_task_id"] is None


@pytest.mark.parametrize("status,code,classification", [(200, 403, "rejected"), (400, 400, "rejected"),
                                                       (408, 408, "unconfirmed"), (502, 200, "unconfirmed")])
def test_http_and_business_failure_never_accept_an_embedded_task(status, code, classification):
    request = make_submit()
    result = decode_submit(request, raw_for(request, {"code": code, "data": {"id": "upstream-task"}}, status=status))
    assert result["classification"] == classification and result["accepted_task_id"] is None


def test_conflicting_request_ids_remain_unconfirmed_and_provide_no_billing_link():
    request = make_submit()
    result = decode_submit(request, raw_for(request, {"code": 200, "request_id": "body-request", "data": {"id": "task"}},
                                           headers={"x-request-id": "header-request"}))
    assert result["classification"] == "unconfirmed" and result["accepted_task_id"] is None
    assert result["submit_request_id"] is None and result["request_id_status"] == "conflicting"


def test_complete_post_receipt_after_deadline_keeps_fee_identity_but_cannot_continue():
    request = make_submit()
    raw = raw_for(request, {"code": 200, "data": {"id": "late-task"}}, headers={"x-request-id": "late-submit-request"})
    raw["received_at"] = (timestamp(request.frozen()["permit"]["deadline_at"]) + timedelta(milliseconds=1)).isoformat()
    result = decode_submit(request, raw)
    assert result["classification"] == "unconfirmed" and result["accepted_task_id"] is None
    assert result["submit_request_id"] == "late-submit-request" and result["raw_hash"] == digest(raw)


@pytest.mark.parametrize("mutation", ["extra", "duplicate_json", "bad_base64", "boolean_http", "before_intent"])
def test_raw_envelope_is_strict(mutation):
    request = make_submit()
    raw = accepted_raw(request)
    if mutation == "extra":
        raw["legacy_task_id"] = "task"
    elif mutation == "duplicate_json":
        raw["body_base64"] = base64.b64encode(b'{"data":{"id":"a"},"data":{"id":"b"}}').decode()
    elif mutation == "bad_base64":
        raw["body_base64"] = "%%%"
    elif mutation == "boolean_http":
        raw["http_status"] = True
    else:
        raw["received_at"] = (timestamp(request.frozen()["permit"]["issued_at"]) - timedelta(seconds=1)).isoformat()
    with pytest.raises(CreatorError):
        decode_submit(request, raw)


def test_fake_descriptor_or_query_cannot_be_used_as_submission():
    request = make_submit()
    frozen = request.frozen()
    frozen["wire"] = "{}"
    fake = FrozenImageRequest(json.dumps(frozen))
    with pytest.raises(CreatorError):
        decode_submit(fake, accepted_raw(request))
    with pytest.raises(CreatorError):
        decode_submit(make_query(request), accepted_raw(request))


def test_query_preserves_post_request_identity_and_separates_query_request():
    request = make_query()
    result = decode_query(request, query_raw(request))
    submitted = decode_submit(FrozenImageRequest.from_frozen(request.frozen()["parent_submit"]), request.frozen()["submit_raw"])
    assert result["classification"] == "completed"
    assert result["accepted_task_id"] == request.frozen()["accepted_task_id"]
    assert result["submit_request_id"] == submitted["submit_request_id"]
    assert result["query_request_id"] == "query-request-only"
    assert result["submit_request_id"] != result["query_request_id"]


def test_missing_query_response_does_not_fabricate_completion_or_query_identity():
    request = make_query()
    result = decode_query(request, None)
    assert result["classification"] == "unconfirmed" and result["result_url"] is None
    assert result["accepted_task_id"] == request.frozen()["accepted_task_id"]
    assert result["query_request_id"] is None


def test_complete_query_after_deadline_cannot_authorize_delivery():
    request = make_query()
    raw = query_raw(request)
    raw["received_at"] = (timestamp(request.frozen()["permit"]["deadline_at"]) + timedelta(milliseconds=1)).isoformat()
    result = decode_query(request, raw)
    assert result["classification"] == "unconfirmed" and result["result_url"] is None
    assert result["query_request_id"] == "query-request-only"
    with pytest.raises(CreatorError):
        verify_delivery(request, raw, image_bytes())


def test_query_task_aliases_must_all_match_and_query_request_conflict_stops():
    request = make_query()
    task = request.frozen()["accepted_task_id"]
    raw = raw_for(request, {"code": 200, "id": "foreign-task", "data": {"task_id": task, "status": "processing"}})
    assert decode_query(request, raw)["classification"] == "unconfirmed"
    raw = raw_for(request, {"code": 200, "request_id": "query-body", "data": {"id": task, "status": "processing"}},
                  headers={"x-request-id": "query-header"})
    assert decode_query(request, raw)["classification"] == "unconfirmed"


@pytest.mark.parametrize("status,classification", [("submitted", "pending"), ("processing", "pending"),
                                                   ("failed", "failed"), ("completed", "completed"),
                                                   ("not-a-status", "unconfirmed")])
def test_query_classifies_only_the_original_task(status, classification):
    request = make_query()
    assert decode_query(request, query_raw(request, status=status))["classification"] == classification
    wrong = decode_query(request, query_raw(request, status=status, task="different-task"))
    assert wrong["classification"] == "unconfirmed" and wrong["result_url"] is None


@pytest.mark.parametrize("url", ["http://media.example/frame.png", "https://user:secret@media.example/frame.png",
                                 "https://media.example:444/frame.png", "https://media.example/frame.png#fragment",
                                 "https://media.example/frame with spaces.png", "file:///private/frame.png"])
def test_completed_query_requires_a_valid_https_image_result(url):
    request = make_query()
    result = decode_query(request, query_raw(request, result={"images": [{"url": [url]}]}))
    assert result["classification"] == "unconfirmed" and result["result_url"] is None


@pytest.mark.parametrize("format_name", ["PNG", "JPEG", "WEBP"])
def test_delivery_decodes_original_bytes_and_remains_not_reviewed(format_name):
    request = make_query()
    raw = query_raw(request)
    content = image_bytes(format_name=format_name)
    result = verify_delivery(request, raw, content)
    assert (result["width"], result["height"], result["format"]) == (90, 160, format_name)
    assert result["delivery_sha256"] == hashlib.sha256(content).hexdigest()
    assert result["delivery_size_bytes"] == len(content)
    assert result["visual_quality"] == "not_reviewed"
    assert result["delivery_hash"] == digest({key: value for key, value in result.items() if key != "delivery_hash"})


@pytest.mark.parametrize("content", [b"", b"not-an-image", image_bytes((160, 90)), image_bytes((90, 160))[:50]])
def test_bad_truncated_or_wrong_ratio_delivery_is_rejected(content):
    request = make_query()
    with pytest.raises(CreatorError) as error:
        verify_delivery(request, query_raw(request), content)
    assert error.value.code == "IMAGE_DELIVERY_INVALID"


def test_failed_or_foreign_query_cannot_verify_an_otherwise_valid_image():
    request = make_query()
    for raw in (query_raw(request, status="failed"), query_raw(request, task="foreign-task")):
        with pytest.raises(CreatorError):
            verify_delivery(request, raw, image_bytes())


def test_animation_is_not_accepted_as_one_still_frame():
    request = make_query()
    output = io.BytesIO()
    first, second = Image.new("RGB", (90, 160), "red"), Image.new("RGB", (90, 160), "blue")
    first.save(output, format="WEBP", append_images=[second], save_all=True, duration=100)
    with pytest.raises(CreatorError):
        verify_delivery(request, query_raw(request), output.getvalue())
