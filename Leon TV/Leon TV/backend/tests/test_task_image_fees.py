"""Synthetic exact native task quota proofs; no keys, files or HTTP calls."""
import copy
import hashlib
import json
from datetime import timedelta

import pytest
from test_image_fees import SOURCE, export, receipt, row_for
from test_image_policy import make_query, make_submit
from test_image_receipts import raw_for

from core.creator.store import CreatorError, digest
from core.execution.fees import request_key
from core.execution.image_fees import candidate_for
from core.execution.receipts import timestamp
from core.execution.task_image_fees import (
    FrozenTaskFeeSourceV1,
    candidate_for_task,
    checked_task_source,
    claim_keys,
    parse_task_export,
    task_key,
    verify_task_candidate,
)


def task_row(request, raw, **changes):
    moment = int(timestamp(raw["received_at"]).timestamp())
    return {"id": 1, "created_at": moment, "updated_at": moment, "task_id": "task-original", "platform": "gpt-image-2",
        "model_name": request.frozen()["policy"]["model"], "user_id": 1, "group": SOURCE["group"], "channel_id": 1,
        "quota": 20000, "action": "generate", "status": "SUCCESS", "fail_reason": "", "result_url": "https://synthetic.invalid/private",
        "submit_time": moment, "start_time": moment, "finish_time": moment, "progress": "100%",
        "properties": {"input": {"prompt": "SYNTHETIC PRIVATE"}},
        "data": {"code": 200, "data": {"id": "different-upstream-id"}}} | changes


def task_source_for(request, account_blob, *, fee_source=SOURCE, **changes):
    return FrozenTaskFeeSourceV1(account_namespace=fee_source["account_namespace"], fee_source_hash=digest(fee_source),
        platform_user_id=1, account_export_hash=hashlib.sha256(account_blob).hexdigest(),
        origin=request.frozen()["policy"]["origin"], **changes).frozen()


def case(*, request_id=None, quota=20000, task_changes=None, account_changes=None):
    request = make_submit()
    raw = receipt(request, request_id=request_id)
    # This is account ownership evidence, deliberately a different request and
    # amount from the native task observation. It is never a request mapping.
    account = export(row_for(request, raw, **({"request_id": "unrelated-account-witness", "quota": 7} | (account_changes or {}))))
    task = export(task_row(request, raw, quota=quota, **(task_changes or {})))
    source = task_source_for(request, account)
    observed_at = (timestamp(raw["received_at"]) + timedelta(seconds=2)).isoformat()
    return request, raw, task, account, SOURCE, source, observed_at


def candidate(values):
    return candidate_for_task(*values[:-1], observed_at=values[-1])


def test_missing_post_request_id_can_observe_only_exact_native_task_quota():
    values = case()
    old = copy.deepcopy(values)
    result = candidate(values)
    assert values == old
    assert result["schema_version"] == 2 and result["kind"] == "image_task_fee_candidate_v1"
    assert result["proof_basis"] == "exact_native_task_quota"
    assert result["provider_task_id"] == "task-original"
    assert result["submit_request_id"] is None and result["request_id_status"] == "missing"
    assert result["platform_log_id"] == task_key("task-original")
    assert result["claim_keys"] == [task_key("task-original")]
    assert result["platform_fee_cny"] == "0.040000" and result["platform_units"] == 20000
    assert result["source_export_hash"] == hashlib.sha256(values[2]).hexdigest()
    assert result["account_export_hash"] == hashlib.sha256(values[3]).hexdigest()
    assert result["task_fee_source_hash"] == values[5]["task_fee_source_hash"]
    assert result["candidate_hash"] == digest({key: value for key, value in result.items() if key != "candidate_hash"})
    assert result["completion_verified"] is result["settlement_performed"] is result["risk_released"] is False
    assert result["provider_bill_confirmed"] is False and result["global_claim_status"] == "not_claimed"
    assert verify_task_candidate(result, *values[:-1]) is True


def test_direct_post_identity_is_retained_as_alias_without_reading_account_request_id():
    values = case(request_id="native-post-request")
    result = candidate(values)
    assert result["submit_request_id"] == "native-post-request" and result["request_id_status"] == "direct"
    assert result["claim_keys"] == sorted([task_key("task-original"), request_key("native-post-request")])
    assert request_key("unrelated-account-witness") not in result["claim_keys"]
    assert claim_keys(result) == tuple(result["claim_keys"])


def test_legacy_direct_candidate_also_exposes_the_secondary_task_identity():
    request = make_submit()
    raw = receipt(request)
    result = candidate_for(request, raw, export(row_for(request, raw)), SOURCE,
                           observed_at=(timestamp(raw["received_at"]) + timedelta(seconds=1)).isoformat())
    assert claim_keys(result) == tuple(sorted([request_key("real-submit-request"), task_key("task-original")]))


def test_legacy_rejected_candidate_only_exposes_original_direct_request_identity():
    request = make_submit()
    raw = receipt(request, status=400)
    result = candidate_for(request, raw, export(row_for(request, raw, quota=0)), SOURCE,
                           observed_at=(timestamp(raw["received_at"]) + timedelta(seconds=1)).isoformat())
    assert claim_keys(result) == (request_key("real-submit-request"),)


@pytest.mark.parametrize("quota,amount,overrun", [(0, "0.000000", False), (1, "0.000002", False),
                                                   (500000, "1.000000", False), (500001, "1.000002", True)])
def test_quota_conversion_zero_and_overrun_never_release_risk(quota, amount, overrun):
    result = candidate(case(quota=quota))
    assert result["platform_fee_cny"] == amount and result["overrun"] is result["stop_recommended"] is overrun
    assert result["risk_released"] is result["settlement_performed"] is False


@pytest.mark.parametrize("changes", [{"task_id": "foreign-task"}, {"user_id": 2}, {"group": "foreign"},
    {"model_name": "foreign"}, {"platform": "foreign"}, {"action": "other"}, {"status": "PENDING"},
    {"status": "FAILED"}, {"status": "SUCCESS", "fail_reason": "contradictory failure"}, {"created_at": 1},
    {"submit_time": 1}, {"updated_at": 1}, {"start_time": 0}, {"finish_time": 0}])
def test_wrong_task_source_state_and_time_are_not_selected_by_fee_or_unique_row(changes):
    with pytest.raises(CreatorError):
        candidate(case(task_changes=changes))


def test_future_task_update_is_not_current_observed_evidence():
    values = list(case())
    item = json.loads(values[2])["data"]["items"][0]
    item["updated_at"] += 60
    values[2] = export(item)
    with pytest.raises(CreatorError):
        candidate(values)


@pytest.mark.parametrize("change", ["missing", "late", "rejected", "conflicting_request", "invalid_request", "task_conflict", "query"])
def test_only_accepted_original_post_can_support_task_quota(change):
    values = list(case())
    request, raw = values[:2]
    if change == "missing":
        values[1] = None
    elif change == "late":
        values[1]["received_at"] = (timestamp(request.frozen()["permit"]["deadline_at"]) + timedelta(seconds=1)).isoformat()
        values[-1] = (timestamp(values[1]["received_at"]) + timedelta(seconds=1)).isoformat()
    elif change == "rejected":
        values[1] = receipt(request, status=400)
    elif change == "conflicting_request":
        values[1] = raw_for(request, {"code": 200, "request_id": "body", "data": {"id": "task-original"}}, headers={"x-request-id": "header"})
    elif change == "invalid_request":
        values[1] = receipt(request, request_id="invalid request")
    elif change == "task_conflict":
        values[1] = raw_for(request, {"code": 200, "data": {"id": "task-original", "task_id": "foreign"}})
    else:
        values[0] = make_query()
    with pytest.raises(CreatorError):
        candidate(values)


def test_task_id_nested_upstream_id_never_becomes_a_billing_or_native_task_alias():
    result = candidate(case())
    assert result["claim_keys"] == [task_key("task-original")]
    assert task_key("different-upstream-id") not in result["claim_keys"]


def test_account_page_binds_pinned_user_across_all_fixed_token_group_rows():
    values = list(case())
    first = json.loads(values[3])["data"]["items"][0]
    values[3] = export(first, first | {"id": 2, "request_id": "second-unrelated", "user_id": 2})
    values[5] = task_source_for(values[0], values[3])
    with pytest.raises(CreatorError) as error:
        candidate(values)
    assert error.value.code == "FEE_EVIDENCE_CONFLICT"


@pytest.mark.parametrize("changes", [{"token_name": "foreign"}, {"group": "foreign"}, {"user_id": 2},
                                    {"user_id": True}, {"user_id": "1"}])
def test_account_witness_cannot_be_missing_foreign_or_coerced(changes):
    with pytest.raises(CreatorError):
        candidate(case(account_changes=changes))


def test_account_amount_and_time_do_not_participate_in_task_charge_identity():
    values = case(account_changes={"created_at": 0, "quota": 50000000, "model_name": "different-account-model"})
    result = candidate(values)
    assert result["platform_fee_cny"] == "0.040000"


@pytest.mark.parametrize("field,value", [("schema_version", True), ("platform_user_id", True), ("platform_user_id", 0),
    ("platform_user_id", "1"), ("account_namespace", "foreign"), ("fee_source_hash", "0" * 64),
    ("account_export_hash", "0" * 64), ("origin", "https://api.ai-tudou.net"), ("task_fee_source_hash", "0" * 64), ("extra", "bad")])
def test_invalid_or_foreign_pinned_source_is_rejected(field, value):
    values = list(case())
    values[5] = {key: item for key, item in values[5].items() if key != "task_fee_source_hash"}
    values[5][field] = value
    with pytest.raises(CreatorError):
        candidate(values)


def test_model_and_sealed_source_forms_reconstruct_identical_source():
    values = case()
    plain = {key: value for key, value in values[5].items() if key != "task_fee_source_hash"}
    model = FrozenTaskFeeSourceV1.model_validate(plain)
    assert checked_task_source(model) == checked_task_source(plain) == checked_task_source(values[5])


def test_wrong_original_account_export_sha_is_not_accepted_even_when_rows_match():
    values = list(case())
    values[3] += b" "
    with pytest.raises(CreatorError) as error:
        candidate(values)
    assert error.value.code == "FEE_EVIDENCE_CONFLICT"


def test_duplicate_identical_task_rows_collapse_but_conflicting_original_rows_stop():
    values = list(case())
    row = json.loads(values[2])["data"]["items"][0]
    values[2] = export(row, copy.deepcopy(row))
    assert candidate(values)["platform_fee_cny"] == "0.040000"
    for changes in ({"quota": 1}, {"id": 2}, {"user_id": 2}, {"status": "FAILED"}, {"data": {"different": True}}):
        values[2] = export(row, row | changes)
        with pytest.raises(CreatorError) as error:
            candidate(values)
        assert error.value.code == "FEE_EVIDENCE_CONFLICT"


@pytest.mark.parametrize("field,value", [("id", True), ("user_id", True), ("quota", True), ("created_at", True),
    ("quota", 1.0), ("quota", -1), ("quota", "1"), ("id", 0), ("task_id", "../task"), ("model_name", ""),
    ("group", []), ("progress", 100), ("data", []), ("properties", None), ("extra", "bad")])
def test_task_parser_rejects_malformed_rows_and_boolean_integer_aliases(field, value):
    values = case()
    row = json.loads(values[2])["data"]["items"][0] | {field: value}
    with pytest.raises(CreatorError):
        parse_task_export(export(row))


@pytest.mark.parametrize("change", ["success", "page", "page_size", "total", "partial", "extra", "list", "duplicate_json", "nonfinite", "empty", "oversized"])
def test_task_parser_rejects_non_original_or_partial_page_shapes(change):
    blob = case()[2]
    value = json.loads(blob)
    if change == "success":
        value["success"] = 1
    elif change == "page":
        value["data"]["page"] = True
    elif change == "page_size":
        value["data"]["page_size"] = 1
    elif change == "total":
        value["data"]["total"] = True
    elif change == "partial":
        value["data"]["total"] = 101
    elif change == "extra":
        value["extra"] = True
    elif change == "list":
        value = []
    elif change == "duplicate_json":
        blob = b'{"success":true,"success":true}'
    elif change == "nonfinite":
        value["data"]["items"][0]["quota"] = float("nan")
        blob = json.dumps(value).encode()
    elif change == "empty":
        blob = b""
    elif change == "oversized":
        blob = b" " * (8 * 1048576 + 1)
    if change in {"success", "page", "page_size", "total", "partial", "extra", "list"}:
        blob = json.dumps(value).encode()
    with pytest.raises(CreatorError):
        parse_task_export(blob)


@pytest.mark.parametrize("field", ["platform_fee_cny", "source_export_hash", "account_export_hash", "task_fee_source_hash",
    "task_row_hash", "raw_hash", "descriptor_hash", "platform_log_id", "submit_request_id", "claim_keys", "extra"])
def test_fully_rehashed_candidate_tampering_never_verifies(field):
    values = case()
    changed = candidate(values)
    changed[field] = [] if field == "claim_keys" else "corrupt"
    changed["candidate_hash"] = digest({key: value for key, value in changed.items() if key != "candidate_hash"})
    assert verify_task_candidate(changed, *values[:-1]) is False


@pytest.mark.parametrize("index", [1, 2, 3, 4, 5])
def test_changed_original_receipt_exports_or_sources_never_verify(index):
    values = list(case())
    result = candidate(values)
    if index == 1:
        values[index] = receipt(values[0], task_id="foreign-task", request_id=None)
    elif index in {2, 3}:
        values[index] += b" "
    elif index == 4:
        values[index] = values[index] | {"token_name": "foreign"}
    else:
        values[index] = values[index] | {"platform_user_id": 2}
    assert verify_task_candidate(result, *values[:-1]) is False


def test_candidate_observed_time_must_not_predate_original_post():
    values = list(case())
    values[-1] = (timestamp(values[1]["received_at"]) - timedelta(seconds=1)).isoformat()
    with pytest.raises(CreatorError):
        candidate(values)


def test_empty_complete_task_page_is_pending_without_an_amount():
    values = list(case())
    values[2] = export()
    with pytest.raises(CreatorError) as error:
        candidate(values)
    assert error.value.code == "FEE_EXPORT_PENDING"


def test_claim_aliases_cannot_include_an_unproven_request():
    result = candidate(case())
    result["claim_keys"].append(request_key("unproven"))
    with pytest.raises(CreatorError):
        claim_keys(result)


@pytest.mark.parametrize("legacy", [False, True])
def test_claim_primary_key_must_match_the_candidate_proof_kind(legacy):
    values = case(request_id="native-post")
    result = candidate(values)
    if legacy:
        result["kind"] = "image_fee_candidate_v1"
    else:
        result["platform_log_id"] = request_key("native-post")
    with pytest.raises(CreatorError):
        claim_keys(result)


@pytest.mark.parametrize("change", ["wire", "endpoint", "job", "profile"])
def test_rehashed_forged_descriptor_is_independently_reconstructed(change):
    values = list(case())
    original = values[0]
    forged = original.frozen()
    if change == "wire":
        forged["wire"] = "{}"
    elif change == "endpoint":
        forged["endpoint"] = "https://synthetic.invalid/foreign"
    elif change == "job":
        forged["job"]["prompt"] = "forged"
    else:
        forged["policy"]["validation_only"] = False
        forged["policy"]["policy_hash"] = digest({k: v for k, v in forged["policy"].items() if k != "policy_hash"})
    forged["descriptor_hash"] = digest({k: v for k, v in forged.items() if k != "descriptor_hash"})
    # Even a real FrozenImageRequest instance with an overridden frozen() method
    # cannot bypass the independent canonical descriptor reconstruction.
    object.__setattr__(original, "frozen", lambda: copy.deepcopy(forged))
    with pytest.raises(CreatorError):
        candidate(values)


def test_pure_proof_does_not_use_any_socket(monkeypatch):
    import socket

    def forbidden(*args, **kwargs):
        pytest.fail("A pure task fee proof attempted network access")
    monkeypatch.setattr(socket, "socket", forbidden)
    assert candidate(case())["platform_fee_cny"] == "0.040000"
