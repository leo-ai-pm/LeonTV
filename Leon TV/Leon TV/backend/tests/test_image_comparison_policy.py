"""Synthetic descriptor/receipt contracts; self-seals grant no native authority."""

import copy

import pytest
from test_image_continuation_policy import arguments, continuation_case
from test_image_policy import accepted_raw
from test_image_receipts import raw_for

from core.creator.store import CreatorError, digest
from core.execution import image_comparison as contract
from core.execution.image_comparison_policy import make_request
from core.execution.image_continuation_policy import build_image_submit_v2
from core.execution.image_policy import FrozenImageRequest
from core.execution.image_receipts import decode_query, decode_submit


def synthetic_descriptor():
    source = build_image_submit_v2(**arguments(continuation_case()))
    from core.execution.image_response_lifecycle import make_response_policy

    a = {
        "schema_version": 1,
        "kind": "image_comparison_authority_v1",
        "activation_id": "1" * 32,
        "quote_id": "2" * 32,
        "intent_id": "3" * 32,
        "intent_hash": "4" * 64,
        "scope_id": "5" * 32,
        "task_id": source.frozen()["permit"]["task_id"],
        "account_id": "synthetic-account",
        "grant_id": source.frozen()["permit"]["grant_id"],
        "proposal_hash": "6" * 64,
        "allocation_hash": "7" * 64,
        "parent_after_sha256": "8" * 64,
        "code_manifest_hash": "9" * 64,
        "purpose": contract.PURPOSE,
        "policy": source.frozen()["policy"],
        "response_policy": make_response_policy(180),
        "source_descriptor_hash": source.descriptor_hash,
        "wire_sha256": contract.sha(source.wire.encode()),
        "submission_index": 6,
        "max_posts": 1,
    }
    a = contract.sealed(a, "authority_hash")
    request = make_request(a, source.frozen(), identifier="a" * 32)
    return source, a, request


def test_exact_source_wire_and_response_identity_are_preserved():
    source, authority, request = synthetic_descriptor()
    assert request.wire == source.wire and request.endpoint == source.endpoint
    assert request.frozen()["permit"]["parent_allocation_hash"] == authority["allocation_hash"]
    raw = accepted_raw(request)
    raw["received_at"] = request.frozen()["permit"]["issued_at"]
    assert decode_submit(request, raw)["classification"] == "accepted"
    query = make_request(
        authority,
        source.frozen(),
        identifier="b" * 32,
        parent=request.frozen(),
        submit_raw=raw,
        query_index=1,
    )
    assert query.method == "GET" and query.wire == ""
    assert FrozenImageRequest.from_frozen(query.frozen()) == query
    mismatched = raw_for(query, {"code": 0, "data": {"id": "wrong-task", "status": "completed"}})
    assert decode_query(query, mismatched)["classification"] == "unconfirmed"


@pytest.mark.parametrize(
    "field,value",
    [
        ("purpose", "formal_delivery"),
        ("max_posts", 2),
        ("max_posts", True),
        ("submission_index", 1),
        ("schema_version", True),
    ],
)
def test_resealed_authority_cannot_gain_another_post_or_formal_purpose(field, value):
    source, authority, _ = synthetic_descriptor()
    authority[field] = value
    authority = contract.sealed(
        {k: v for k, v in authority.items() if k != "authority_hash"}, "authority_hash"
    )
    with pytest.raises(CreatorError):
        make_request(authority, source.frozen(), identifier="b" * 32)


@pytest.mark.parametrize(
    "attack", ["wire", "input", "reference", "identity", "funding", "purpose", "nonce"]
)
def test_fully_resealed_descriptor_changes_are_rejected(attack):
    _, _, request = synthetic_descriptor()
    value = copy.deepcopy(request.frozen())
    if attack == "wire":
        value["wire"] += " "
    elif attack == "input":
        value["job"]["prompt"] += " modified"
    elif attack == "reference":
        value["references"] = []
    elif attack == "identity":
        value["permit"]["comparison_activation_id"] = "f" * 32
    elif attack == "funding":
        value["permit"]["parent_allocation_hash"] = "f" * 64
    elif attack == "purpose":
        value["permit"]["purpose"] = "formal_delivery"
    else:
        value["permit"]["nonce"] = True
    value["permit"]["permit_hash"] = digest(
        {k: v for k, v in value["permit"].items() if k != "permit_hash"}
    )
    value["descriptor_hash"] = digest({k: v for k, v in value.items() if k != "descriptor_hash"})
    with pytest.raises(CreatorError):
        FrozenImageRequest.from_frozen(value)


def test_diagnostic_dispatch120_response180_use_existing_claim2(tmp_path):
    from datetime import timedelta

    from core.execution.image_capture import worker_request
    from core.execution.image_response_lifecycle import response_deadline
    from core.execution.receipts import ReceiptStore, timestamp

    _, _, request = synthetic_descriptor()
    binding = request.binding()
    issued = timestamp(request.frozen()["permit"]["issued_at"])
    dispatch = timestamp(binding["deadline_at"])
    response = response_deadline(binding)
    assert (dispatch - issued).total_seconds() == 120
    assert (response - dispatch).total_seconds() == 180
    receipts = ReceiptStore(tmp_path / "receipts")
    operation, created = receipts.prepare(
        binding,
        "a" * 64,
        "b" * 64,
        response.isoformat(),
        (response + timedelta(seconds=5)).isoformat(),
    )
    assert created and operation.claim(binding)["version"] == 2
    job = {
        "kind": binding["kind"],
        "descriptor": request.frozen(),
        "binding": binding,
        "worker_factory": "image_tool_fixture:make_transport",
        "worker_options": {},
        "api_key": "SYNTHETIC",
    }
    assert worker_request(job, operation.claim(binding))[0] == binding
    raw = accepted_raw(request)
    raw["received_at"] = (dispatch + timedelta(seconds=1)).isoformat()
    assert decode_submit(request, raw)["classification"] == "accepted"
    raw["received_at"] = (response + timedelta(microseconds=1)).isoformat()
    assert decode_submit(request, raw)["classification"] == "unconfirmed"


@pytest.mark.parametrize("field", ["response_deadline_at", "response_contract_hash"])
def test_resealed_response_extension_or_contract_change_is_rejected(field):
    _, _, request = synthetic_descriptor()
    value = request.frozen()
    if field == "response_deadline_at":
        from datetime import timedelta

        from core.execution.receipts import timestamp

        value["permit"][field] = (
            timestamp(value["permit"][field]) + timedelta(seconds=1)
        ).isoformat()
    else:
        value["permit"][field] = "f" * 64
    value["permit"]["permit_hash"] = digest(
        {k: v for k, v in value["permit"].items() if k != "permit_hash"}
    )
    value["descriptor_hash"] = digest({k: v for k, v in value.items() if k != "descriptor_hash"})
    with pytest.raises(CreatorError):
        FrozenImageRequest.from_frozen(value)


def test_exact_task_fee_can_observe_missing_post_request_id_without_guessing():
    from test_image_fees import SOURCE, export, observed, row_for
    from test_task_image_fees import task_row, task_source_for

    from core.execution.task_image_fees import candidate_for_task

    _, _, request = synthetic_descriptor()
    raw = accepted_raw(request)
    raw["headers"] = {}
    fee_source = {
        **SOURCE,
        "account_namespace": request.frozen()["budget_anchor"]["account_scope_id"],
    }
    receipt = decode_submit(request, raw)
    assert receipt["classification"] == "accepted" and receipt["submit_request_id"] is None
    account = export(row_for(request, raw, request_id="SYNTHETIC-ACCOUNT-ONLY", quota=7))
    source = task_source_for(request, account, fee_source=fee_source)
    task = export(task_row(request, raw, task_id=receipt["accepted_task_id"]))
    candidate = candidate_for_task(
        request, raw, task, account, fee_source, source, observed_at=observed(raw)
    )
    assert candidate["provider_task_id"] == receipt["accepted_task_id"]
    assert candidate["platform_fee_cny"] == "0.040000" and candidate["risk_released"] is False
    for bad in (
        export(task_row(request, raw, task_id="SYNTHETIC-UNBOUND")),
        export(task_row(request, raw, task_id=receipt["accepted_task_id"], user_id=2)),
    ):
        with pytest.raises(CreatorError):
            candidate_for_task(
                request, raw, bad, account, fee_source, source, observed_at=observed(raw)
            )
