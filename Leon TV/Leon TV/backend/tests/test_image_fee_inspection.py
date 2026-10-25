"""Synthetic, read-only image fee diagnosis; no files, native scopes or HTTP."""
import base64
import copy
import json
from datetime import timedelta

import pytest
from test_image_fees import SOURCE, export, observed, receipt, row_for
from test_image_policy import make_query, make_submit
from test_image_receipts import raw_for

from core.creator.store import CreatorError
from core.execution import image_fee_inspection as inspection
from core.execution.image_fee_inspection import inspect_receipt
from core.execution.image_policy import FrozenImageRequest
from core.execution.receipts import timestamp

STATUSES = {
    "receipt_unavailable", "receipt_invalid", "missing_submit_request_id",
    "conflicting_submit_request_id", "invalid_submit_request_id", "export_required",
    "export_invalid", "direct_match_missing", "evidence_conflict", "direct_match",
}
SAFE_ERROR = "Explicit image fee inspection is unavailable"


def inspect(request, raw, blob=None, *, source=None, observed_at=None):
    source = SOURCE if source is None else source
    before = copy.deepcopy((request, raw, source))
    try:
        return inspect_receipt(request, raw, source,
            export_bytes=blob, observed_at=observed_at or observed(raw))
    finally:
        assert (request, raw, source) == before


def assert_read_only(result, status):
    assert result["fee_match_status"] == status
    assert status in STATUSES
    assert result["read_only"] is True
    assert type(result["automatic_followup_calls"]) is int
    assert result["automatic_followup_calls"] == 0
    assert result["import_authorized"] is False
    assert result["new_dispatch_authorized"] is False
    assert result["candidate_matches"] is (status == "direct_match")
    assert result["export_status"] in {"not_provided", "valid", "invalid"}
    assert result["export_error_code"] in {None, "FEE_EXPORT_INVALID", "FEE_EVIDENCE_CONFLICT"}
    if result["export_status"] != "invalid":
        assert result["export_error_code"] is None
    assert result["upstream_other_request_id_presence"] == "unknown"
    for name in ("captured_standard_header_present", "body_request_id_present", "accepted_task_id_present"):
        assert type(result[name]) is bool
    if status != "direct_match":
        assert result["candidate_platform_fee_cny"] is None
        assert result["candidate_overrun"] is None


@pytest.mark.parametrize("row_identity", ["unique-unrelated-request", "task-original", "query-request-only"])
def test_missing_post_identity_never_uses_unique_time_task_or_get_row(row_identity):
    request = make_submit()
    raw = receipt(request, request_id=None)
    result = inspect(request, raw, export(row_for(request, raw, request_id=row_identity)))
    assert_read_only(result, "missing_submit_request_id")
    assert result["receipt_classification"] == "accepted"
    assert result["request_id_status"] == "missing"
    assert result["accepted_task_id_present"] is True
    assert result["captured_standard_header_present"] is False
    assert result["body_request_id_present"] is False
    assert result["same_account_model_row_count"] == 1


def test_missing_identity_precedes_absent_or_invalid_export():
    request = make_submit()
    raw = receipt(request, request_id=None)
    for blob in (None, b"not-json"):
        result = inspect(request, raw, blob)
        assert_read_only(result, "missing_submit_request_id")
        assert result["same_account_model_row_count"] is None
        assert result["export_status"] == ("not_provided" if blob is None else "invalid")
        assert result["export_error_code"] == (None if blob is None else "FEE_EXPORT_INVALID")


@pytest.mark.parametrize("identity_location", ["header", "body", "both"])
def test_only_direct_post_identity_produces_an_unclaimed_fee_fact(identity_location):
    request = make_submit()
    body = {"code": 200, "data": {"id": "task-original"}}
    if identity_location in {"body", "both"}:
        body["request_id"] = "real-submit-request"
    headers = {"x-request-id": "real-submit-request"} if identity_location in {"header", "both"} else {}
    raw = raw_for(request, body, headers=headers)
    result = inspect(request, raw, export(row_for(request, raw)))
    assert_read_only(result, "direct_match")
    assert result["request_id_status"] == "direct"
    assert result["receipt_classification"] == "accepted"
    assert result["captured_standard_header_present"] is (identity_location in {"header", "both"})
    assert result["body_request_id_present"] is (identity_location in {"body", "both"})
    assert result["candidate_platform_fee_cny"] == "0.040000"
    assert result["candidate_overrun"] is False
    assert result["same_account_model_row_count"] == 1


@pytest.mark.parametrize("quota,amount,overrun", [(0, "0.000000", False), (1, "0.000002", False),
                                                (50000000, "100.000000", True)])
def test_zero_small_and_overrun_amounts_remain_read_only_facts(quota, amount, overrun):
    request = make_submit()
    raw = receipt(request, status=400)
    result = inspect(request, raw, export(row_for(request, raw, quota=quota)))
    assert_read_only(result, "direct_match")
    assert result["receipt_classification"] == "rejected"
    assert result["accepted_task_id_present"] is False
    assert result["candidate_platform_fee_cny"] == amount
    assert result["candidate_overrun"] is overrun


def test_direct_identity_requires_an_export_and_exact_consumption_row():
    request = make_submit()
    raw = receipt(request)
    result = inspect(request, raw)
    assert_read_only(result, "export_required")
    assert result["same_account_model_row_count"] is None
    assert result["export_status"] == "not_provided"
    for blob in (export(), export(row_for(request, raw, request_id="query-request-only")),
                 export(row_for(request, raw, type=1))):
        assert_read_only(inspect(request, raw, blob), "direct_match_missing")


def test_conflicting_body_and_header_ids_never_choose_one_matching_row():
    request = make_submit()
    raw = raw_for(request, {"code": 200, "request_id": "body-request", "data": {"id": "task-original"}},
        headers={"x-request-id": "header-request"})
    for blob in (None, b"bad-export", export(row_for(request, raw, request_id="header-request"))):
        result = inspect(request, raw, blob)
        assert_read_only(result, "conflicting_submit_request_id")
        assert result["request_id_status"] == "conflicting"
        assert result["receipt_classification"] == "unconfirmed"
        assert result["accepted_task_id_present"] is False
        assert result["captured_standard_header_present"] is result["body_request_id_present"] is True


@pytest.mark.parametrize("identity_location", ["header", "body"])
def test_invalid_direct_identity_is_not_replaced_by_task_or_export(identity_location):
    request = make_submit()
    body = {"code": 200, "data": {"id": "task-original"}}
    headers = {}
    if identity_location == "header":
        headers["x-request-id"] = "../../private-request"
    else:
        body["request_id"] = "../../private-request"
    raw = raw_for(request, body, headers=headers)
    result = inspect(request, raw, export(row_for(request, raw, request_id="task-original")))
    assert_read_only(result, "invalid_submit_request_id")
    assert result["request_id_status"] == "invalid"
    assert result["accepted_task_id_present"] is False


def test_missing_raw_is_distinct_from_a_present_but_invalid_receipt():
    request = make_submit()
    result = inspect_receipt(request, None, SOURCE, export_bytes=export(),
        observed_at=request.frozen()["permit"]["deadline_at"])
    assert_read_only(result, "receipt_unavailable")
    assert result["receipt_classification"] == "unconfirmed"
    assert result["accepted_task_id_present"] is False
    assert result["captured_standard_header_present"] is result["body_request_id_present"] is False


@pytest.mark.parametrize("mutation", ["extra", "bad_base64", "boolean_http", "duplicate_body_key",
                                    "before_intent", "invalid_json", "oversized"])
def test_invalid_raw_never_enters_fee_matching(mutation):
    request = make_submit()
    raw = receipt(request)
    valid_time = observed(raw)
    if mutation == "extra":
        raw["private_source"] = "PRIVATE-RAW-SOURCE"
    elif mutation == "bad_base64":
        raw["body_base64"] = "%%%"
    elif mutation == "boolean_http":
        raw["http_status"] = True
    elif mutation == "duplicate_body_key":
        raw["body_base64"] = base64.b64encode(b'{"data":{"id":"a"},"data":{"id":"b"}}').decode()
    elif mutation == "before_intent":
        raw["received_at"] = (timestamp(request.frozen()["permit"]["issued_at"]) - timedelta(seconds=1)).isoformat()
    elif mutation == "invalid_json":
        raw["body_base64"] = base64.b64encode(b"not-json").decode()
    else:
        raw["body_base64"] = base64.b64encode(b" " * (request.max_response_bytes + 1)).decode()
    result = inspect(request, raw, export(row_for(request, raw)), observed_at=valid_time)
    assert_read_only(result, "receipt_invalid")
    assert result["accepted_task_id_present"] is False


def test_late_post_can_explain_a_direct_fee_but_cannot_restore_the_task():
    request = make_submit()
    raw = receipt(request)
    raw["received_at"] = (timestamp(request.deadline_at) + timedelta(milliseconds=1)).isoformat()
    result = inspect(request, raw, export(row_for(request, raw)))
    assert_read_only(result, "direct_match")
    assert result["receipt_classification"] == "unconfirmed"
    assert result["request_id_status"] == "direct"
    assert result["accepted_task_id_present"] is False
    assert result["candidate_platform_fee_cny"] == "0.040000"


@pytest.mark.parametrize("blob", [b"", b"not-json", "PRIVATE-BAD-EXPORT", b"[]", b'{"success":true,"success":false}',
                                 b'{"success":false,"message":"PRIVATE-ERROR","data":{}}'])
def test_invalid_export_is_a_safe_diagnostic_not_an_import(blob):
    request = make_submit()
    raw = receipt(request)
    result = inspect(request, raw, blob)
    assert_read_only(result, "export_invalid")
    assert result["same_account_model_row_count"] is None
    assert result["export_status"] == "invalid"
    assert result["export_error_code"] == "FEE_EXPORT_INVALID"
    assert "PRIVATE-ERROR" not in json.dumps(result)
    assert "PRIVATE-BAD-EXPORT" not in json.dumps(result)


@pytest.mark.parametrize("changes", [{"quota": True}, {"quota": -1}, {"request_id": ""}])
def test_invalid_export_rows_are_not_coerced(changes):
    request = make_submit()
    raw = receipt(request)
    result = inspect(request, raw, export(row_for(request, raw, **changes)))
    assert_read_only(result, "export_invalid")


def test_conflicting_duplicate_rows_cannot_be_chosen_or_summed():
    request = make_submit()
    raw = receipt(request)
    row = row_for(request, raw)
    result = inspect(request, raw, export(row, row | {"id": 2, "quota": row["quota"] + 1}))
    assert_read_only(result, "evidence_conflict")
    assert result["same_account_model_row_count"] is None
    assert result["export_status"] == "invalid"
    assert result["export_error_code"] == "FEE_EVIDENCE_CONFLICT"


def test_identical_duplicate_rows_have_one_canonical_observation():
    request = make_submit()
    raw = receipt(request)
    row = row_for(request, raw)
    result = inspect(request, raw, export(row, row | {"id": 2}))
    assert_read_only(result, "direct_match")
    assert result["same_account_model_row_count"] == 1
    assert result["candidate_platform_fee_cny"] == "0.040000"


@pytest.mark.parametrize("changes", [{"token_name": "foreign-token"}, {"group": "foreign-group"},
                                    {"model_name": "foreign-model"}, {"created_at": 0}])
def test_same_direct_id_with_foreign_source_model_or_time_conflicts(changes):
    request = make_submit()
    raw = receipt(request)
    result = inspect(request, raw, export(row_for(request, raw, **changes)))
    assert_read_only(result, "evidence_conflict")


def test_safe_row_count_is_not_a_time_match_or_a_fee_identity():
    request = make_submit()
    raw = receipt(request, request_id=None)
    row = row_for(request, raw, request_id="unrelated-one")
    blob = export(row, row | {"id": 2},
        row | {"request_id": "unrelated-two", "created_at": 0},
        row | {"request_id": "foreign-token", "token_name": "foreign"},
        row | {"request_id": "foreign-group", "group": "foreign"},
        row | {"request_id": "foreign-model", "model_name": "foreign"},
        row | {"request_id": "refund-only", "type": 1})
    result = inspect(request, raw, blob)
    assert_read_only(result, "missing_submit_request_id")
    assert result["same_account_model_row_count"] == 2


def test_pure_report_never_discloses_private_identity_url_or_source():
    request = make_submit()
    task_id = "PRIVATE-task-identity-123"
    request_id = "PRIVATE-submit-identity-456"
    private_url = "https://private.example/image.png?token=PRIVATE-URL-TOKEN"
    source = SOURCE | {"token_name": "PRIVATE-token-name", "group": "PRIVATE-group-name"}
    raw = raw_for(request, {"code": 200, "request_id": request_id,
        "data": {"id": task_id, "result": {"url": private_url}, "private_source": "PRIVATE-STORY-SOURCE"}},
        headers={"x-request-id": request_id})
    blob = export(row_for(request, raw, request_id=request_id, token_name=source["token_name"], group=source["group"],
        content="PRIVATE-EXPORT-CONTENT", username="PRIVATE-USER", ip="192.0.2.45"))
    result = inspect(request, raw, blob, source=source)
    assert_read_only(result, "direct_match")
    rendered = json.dumps(result, sort_keys=True)
    for secret in (task_id, request_id, private_url, *source.values(), "PRIVATE-STORY-SOURCE",
                   "PRIVATE-EXPORT-CONTENT", "PRIVATE-USER", "192.0.2.45", raw["body_base64"]):
        if type(secret) is str:
            assert secret not in rendered
    forbidden = {"submit_request_id", "provider_task_id", "accepted_task_id", "platform_log_id", "result_url",
        "token_name", "group", "source", "raw", "candidate", "body_base64", "export_base64", "private_source"}
    assert not forbidden & set(result)


def test_call_is_pure_and_returns_independent_results_without_caching():
    request = make_submit()
    raw, source = receipt(request), copy.deepcopy(SOURCE)
    frozen_before, raw_before, source_before = request.frozen(), copy.deepcopy(raw), copy.deepcopy(source)
    first_blob = export(row_for(request, raw))
    first = inspect(request, raw, first_blob, source=source)
    second = inspect(request, raw, first_blob, source=source)
    assert first == second and first is not second
    first["candidate_platform_fee_cny"] = "999.000000"
    assert second["candidate_platform_fee_cny"] == "0.040000"
    changed_export = inspect(request, raw, export(row_for(request, raw, quota=1)), source=source)
    assert_read_only(changed_export, "direct_match")
    assert changed_export["candidate_platform_fee_cny"] == "0.000002"
    source["token_name"] = "replacement-token"
    assert_read_only(inspect(request, raw, first_blob, source=source), "evidence_conflict")
    new_blob = export(row_for(request, raw, token_name="replacement-token", quota=0))
    fresh = inspect(request, raw, new_blob, source=source)
    assert_read_only(fresh, "direct_match")
    assert fresh["candidate_platform_fee_cny"] == "0.000000"
    assert source_before == SOURCE
    assert source == source_before | {"token_name": "replacement-token"}
    assert raw == raw_before and request.frozen() == frozen_before


@pytest.mark.parametrize("source", [SOURCE | {"quota_per_cny": True}, SOURCE | {"quota_per_cny": 1},
                                   SOURCE | {"extra": "PRIVATE-SOURCE-ERROR"},
                                   SOURCE | {"account_namespace": "PRIVATE-foreign-account"}])
def test_invalid_source_raises_only_the_fixed_safe_error(source):
    request = make_submit()
    raw = receipt(request)
    with pytest.raises(CreatorError) as error:
        inspect(request, raw, export(row_for(request, raw)), source=source)
    assert error.value.code == "IMAGE_FEE_INSPECTION_UNAVAILABLE"
    assert error.value.message == SAFE_ERROR
    assert "PRIVATE-SOURCE-ERROR" not in str(error.value)
    assert "PRIVATE-foreign-account" not in str(error.value)


@pytest.mark.parametrize("kind", ["lookalike", "tampered", "query"])
def test_invalid_or_get_descriptor_cannot_be_diagnosed_as_a_post(kind):
    request = make_submit()
    raw = receipt(request)
    if kind == "lookalike":
        candidate = request.frozen()
    elif kind == "tampered":
        value = request.frozen()
        value["wire"] = "PRIVATE-TAMPERED-WIRE"
        candidate = FrozenImageRequest(json.dumps(value))
    else:
        candidate = make_query(request)
    with pytest.raises(CreatorError) as error:
        inspect(candidate, raw, export(row_for(request, raw)))
    assert error.value.code == "IMAGE_FEE_INSPECTION_UNAVAILABLE"
    assert error.value.message == SAFE_ERROR
    assert "PRIVATE-TAMPERED-WIRE" not in str(error.value)


@pytest.mark.parametrize("argv", [[], ["status"]])
def test_cli_without_an_explicit_control_stays_off(argv, monkeypatch, capsys):
    def unexpected(*args, **kwargs):
        pytest.fail("Off mode attempted to inspect native data")

    monkeypatch.setattr(inspection, "inspect_native", unexpected)
    assert inspection.main(argv) == 0
    result = json.loads(capsys.readouterr().out)
    assert result == {"mode": "off", "read_only": True, "automatic_followup_calls": 0,
        "import_authorized": False, "new_dispatch_authorized": False}


@pytest.mark.parametrize("argv", [["--unexpected", "PRIVATE-CLI-TOKEN"], ["status", "--operation"],
                                 ["start", "PRIVATE-CLI-PATH"], ["--control"],
                                 ["--operation", "PRIVATE-CLI-OPERATION"],
                                 ["--export", "/PRIVATE-CLI-EXPORT.json"],
                                 ["status", "--operation", "PRIVATE-CLI-OPERATION", "--export", "/PRIVATE-CLI-EXPORT.json"]])
def test_cli_argument_errors_are_fixed_json_without_echoing_values(argv, capsys):
    assert inspection.main(argv) == 1
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert result == {"error": "IMAGE_FEE_INSPECTION_UNAVAILABLE", "read_only": True,
        "automatic_followup_calls": 0, "import_authorized": False, "new_dispatch_authorized": False}
    assert captured.err == ""
    assert "PRIVATE-CLI" not in captured.out
