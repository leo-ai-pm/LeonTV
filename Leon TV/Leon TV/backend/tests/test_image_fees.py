"""Exact platform image fee facts; no financial claim or paid requests."""
import copy
import hashlib
import json
from datetime import timedelta

import pytest
from test_image_policy import make_submit
from test_image_receipts import raw_for

from core.creator.store import CreatorError, digest
from core.execution.fees import request_key
from core.execution.image_fees import candidate_for, verify_candidate
from core.execution.receipts import timestamp

SOURCE = {"account_namespace": "synthetic-original100", "token_name": "synthetic-token", "group": "synthetic-group", "quota_per_cny": 500000}


def export(*rows):
    return json.dumps({"success": True, "message": "", "data": {"page": 1, "page_size": 100,
                      "total": len(rows), "items": list(rows)}}, separators=(",", ":")).encode()


def receipt(request, request_id="real-submit-request", *, status=200, task_id="task-original"):
    return raw_for(request, {"code": status, "data": {"id": task_id}}, status=status,
                   headers={"x-request-id": request_id} if request_id else {})


def row_for(request, raw, *, request_id="real-submit-request", quota=20000, **changes):
    row = {"id": 1, "user_id": 1, "created_at": int(timestamp(raw["received_at"]).timestamp()), "type": 2,
           "content": "SYNTHETIC", "username": "synthetic", "token_name": SOURCE["token_name"],
           "model_name": request.frozen()["policy"]["model"], "quota": quota, "prompt_tokens": 0,
           "completion_tokens": 0, "use_time": 1, "is_stream": False, "channel": 1, "channel_name": "synthetic",
           "token_id": 1, "group": SOURCE["group"], "ip": "127.0.0.1", "request_id": request_id, "other": "{}"}
    return row | changes


def observed(raw):
    return (timestamp(raw["received_at"]) + timedelta(seconds=1)).isoformat()


def test_exact_request_link_returns_unclaimed_fee_fact_without_settlement():
    request = make_submit()
    raw = receipt(request)
    blob = export(row_for(request, raw))
    candidate = candidate_for(request, raw, blob, SOURCE, observed_at=observed(raw))
    assert candidate["submit_request_id"] == "real-submit-request"
    assert candidate["provider_task_id"] == "task-original"
    assert candidate["platform_log_id"] == request_key("real-submit-request")
    assert candidate["platform_fee_cny"] == "0.040000"
    assert candidate["source_export_hash"] == hashlib.sha256(blob).hexdigest()
    assert candidate["candidate_hash"] == digest({key: value for key, value in candidate.items() if key != "candidate_hash"})
    assert candidate["global_claim_status"] == "not_claimed"
    assert candidate["settlement_performed"] is candidate["risk_released"] is candidate["completion_verified"] is False
    assert verify_candidate(candidate, request, raw, blob, SOURCE) is True
    assert verify_candidate(candidate, request, raw, blob, SOURCE) is True


def test_task_id_time_usage_and_one_unique_row_cannot_replace_missing_request_id():
    request = make_submit()
    raw = receipt(request, request_id=None)
    with pytest.raises(CreatorError) as error:
        candidate_for(request, raw, export(row_for(request, raw, request_id="task-original")), SOURCE, observed_at=observed(raw))
    assert error.value.code == "FEE_EXPORT_PENDING"


def test_query_request_id_cannot_be_used_to_find_a_generation_charge():
    request = make_submit()
    raw = receipt(request)
    with pytest.raises(CreatorError) as error:
        candidate_for(request, raw, export(row_for(request, raw, request_id="query-request-only")), SOURCE, observed_at=observed(raw))
    assert error.value.code == "FEE_EXPORT_PENDING"


@pytest.mark.parametrize("changes", [{"token_name": "foreign"}, {"group": "foreign"}, {"model_name": "foreign-model"},
                                    {"created_at": 0}])
def test_same_request_identity_with_conflicting_source_model_or_time_stops(changes):
    request = make_submit()
    raw = receipt(request)
    with pytest.raises(CreatorError) as error:
        candidate_for(request, raw, export(row_for(request, raw, **changes)), SOURCE, observed_at=observed(raw))
    assert error.value.code == "FEE_EVIDENCE_CONFLICT"


def test_conflicting_financial_rows_for_the_same_request_are_not_summed_or_chosen():
    request = make_submit()
    raw = receipt(request)
    row = row_for(request, raw)
    with pytest.raises(CreatorError) as error:
        candidate_for(request, raw, export(row, row | {"id": 2, "quota": row["quota"] + 1}), SOURCE, observed_at=observed(raw))
    assert error.value.code == "FEE_EVIDENCE_CONFLICT"


def test_duplicate_identical_rows_do_not_manufacture_two_fees_or_ambiguity():
    request = make_submit()
    raw = receipt(request)
    row = row_for(request, raw)
    candidate = candidate_for(request, raw, export(row, row | {"id": 2}), SOURCE, observed_at=observed(raw))
    assert candidate["platform_units"] == row["quota"]


def test_body_and_header_conflict_is_ambiguous_even_with_one_matching_row():
    request = make_submit()
    raw = raw_for(request, {"code": 200, "request_id": "body-request", "data": {"id": "task"}},
                  headers={"x-request-id": "header-request"})
    with pytest.raises(CreatorError) as error:
        candidate_for(request, raw, export(row_for(request, raw, request_id="header-request")), SOURCE, observed_at=observed(raw))
    assert error.value.code == "FEE_EXPORT_AMBIGUOUS"


def test_zero_fee_does_not_establish_completion_or_release_risk():
    request = make_submit()
    raw = receipt(request, status=400)
    candidate = candidate_for(request, raw, export(row_for(request, raw, quota=0)), SOURCE, observed_at=observed(raw))
    assert candidate["platform_fee_cny"] == "0.000000"
    assert candidate["submission_classification"] == "rejected"
    assert candidate["provider_task_id"] is None
    assert candidate["completion_verified"] is candidate["risk_released"] is candidate["settlement_performed"] is False


def test_overrun_remains_an_exact_fee_fact_and_recommends_stopping():
    request = make_submit()
    raw = receipt(request)
    candidate = candidate_for(request, raw, export(row_for(request, raw, quota=50000000)), SOURCE, observed_at=observed(raw))
    assert candidate["platform_fee_cny"] == "100.000000"
    assert candidate["overrun"] is candidate["stop_recommended"] is True
    assert candidate["risk_released"] is False


def test_direct_fee_fact_for_late_post_does_not_recover_the_unconfirmed_task():
    request = make_submit()
    raw = receipt(request)
    raw["received_at"] = (timestamp(request.frozen()["permit"]["deadline_at"]) + timedelta(milliseconds=1)).isoformat()
    candidate = candidate_for(request, raw, export(row_for(request, raw)), SOURCE, observed_at=observed(raw))
    assert candidate["submission_classification"] == "unconfirmed"
    assert candidate["provider_task_id"] is None and candidate["submit_request_id"] == "real-submit-request"
    assert candidate["settlement_performed"] is candidate["risk_released"] is False


def test_refund_or_non_consumption_row_is_not_guessed_as_image_charge():
    request = make_submit()
    raw = receipt(request)
    with pytest.raises(CreatorError) as error:
        candidate_for(request, raw, export(row_for(request, raw, type=1)), SOURCE, observed_at=observed(raw))
    assert error.value.code == "FEE_EXPORT_PENDING"


def test_small_native_quota_uses_exact_six_decimal_conversion():
    request = make_submit()
    raw = receipt(request)
    candidate = candidate_for(request, raw, export(row_for(request, raw, quota=1)), SOURCE, observed_at=observed(raw))
    assert candidate["platform_fee_cny"] == "0.000002"


@pytest.mark.parametrize("source", [SOURCE | {"quota_per_cny": True}, SOURCE | {"quota_per_cny": 1},
                                   SOURCE | {"unexpected": "synthetic"}])
def test_invalid_frozen_fee_source_is_a_fixed_error(source):
    request = make_submit()
    raw = receipt(request)
    with pytest.raises(CreatorError) as error:
        candidate_for(request, raw, export(row_for(request, raw)), source, observed_at=observed(raw))
    assert error.value.code == "FEE_EXPORT_INVALID"


@pytest.mark.parametrize("field", ["raw_hash", "descriptor_hash", "platform_fee_cny", "platform_log_id", "fee_source_hash", "extra"])
def test_candidate_verifier_replays_all_binding_and_financial_fields(field):
    request = make_submit()
    raw = receipt(request)
    blob = export(row_for(request, raw))
    candidate = candidate_for(request, raw, blob, SOURCE, observed_at=observed(raw))
    changed = copy.deepcopy(candidate)
    changed[field] = "corrupt"
    changed["candidate_hash"] = digest({key: value for key, value in changed.items() if key != "candidate_hash"})
    assert verify_candidate(changed, request, raw, blob, SOURCE) is False


def test_changed_raw_or_export_never_verifies_an_old_candidate():
    request = make_submit()
    raw = receipt(request)
    blob = export(row_for(request, raw))
    candidate = candidate_for(request, raw, blob, SOURCE, observed_at=observed(raw))
    assert verify_candidate(candidate, request, receipt(request, task_id="foreign-task"), blob, SOURCE) is False
    assert verify_candidate(candidate, request, raw, export(row_for(request, raw, quota=1)), SOURCE) is False
