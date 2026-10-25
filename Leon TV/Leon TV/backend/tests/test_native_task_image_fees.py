"""Native task-quota imports use fictional receipts and the original journal only."""
import base64
import copy
import hashlib
import json
import socket
from datetime import timedelta

import httpx
import pytest
from image_tool_fixture import make_transport as original_transport
from media_ledger_fixture import authorize, image_fee, media_case, posts, reserve
from test_execution_empirical import empirical_env as empirical_env
from test_image_fees import export, observed, row_for
from test_image_receipts import image_bytes

from core.creator.store import CreatorError, digest
from core.execution.fees import FrozenFeeSource, request_key
from core.execution.image_policy import FrozenImageRequest
from core.execution.media_ledger import CORE, fee_claimed, validate
from core.execution.media_runtime import MediaExecution
from core.execution.receipts import ReceiptCaptureSettings, timestamp
from core.execution.scope_budget import _validate_fee_owners, scope_envelope
from core.execution.task_image_fees import FrozenTaskFeeSourceV1, task_key


def task_fee_transport(options):
    """The mock can omit a POST identity; it never changes an existing receipt."""
    original = original_transport(options)

    def handle(request):
        response = original.handle_request(request)
        if request.method == "POST" and options.get("missing_submit_id"):
            return httpx.Response(response.status_code, content=response.content, headers={})
        return response

    return httpx.MockTransport(handle)


def submitted_case(empirical_env, *, missing=True):
    case = media_case(empirical_env)
    case.settings = ReceiptCaptureSettings(observation_wait_seconds=5,
        worker_factory="test_native_task_image_fees:task_fee_transport",
        worker_options={"counter": str(case.counter), "missing_submit_id": missing})
    case.media.capture.settings = case.settings
    _, grant = authorize(case)
    op = reserve(case, grant)
    op = case.media.submit(case.task["id"], op["id"])["operation"]
    assert op["status"] == "accepted"
    return case, grant, op


def task_evidence(case, operation, *, quota=20000, row_changes=None, source_changes=None):
    request = FrozenImageRequest.from_frozen(operation["descriptor"])
    raw = operation["raw"]
    moment = int(timestamp(raw["received_at"]).timestamp())
    row = {"id": 1, "created_at": moment, "updated_at": moment,
        "task_id": operation["receipt"]["accepted_task_id"], "platform": "gpt-image-2",
        "model_name": case.policy.model, "user_id": 1, "group": case.source["group"],
        "channel_id": 1, "quota": quota, "action": "generate", "status": "SUCCESS",
        "fail_reason": "", "result_url": "https://synthetic.invalid/image.png", "submit_time": moment,
        "start_time": moment, "finish_time": moment, "progress": "100%", "properties": {}, "data": {}}
    row.update(row_changes or {})
    # This independent account export binds only user/token/group. Its price and
    # request ID are deliberately different and cannot be added to the task fee.
    account_blob = export(row_for(request, raw, request_id="independent-account-observation", quota=123456,
        token_name=case.source["token_name"], group=case.source["group"]))
    source = FrozenTaskFeeSourceV1(account_namespace=case.source["account_namespace"],
        fee_source_hash=digest(FrozenFeeSource.model_validate(case.source).model_dump()),
        platform_user_id=1, account_export_hash=hashlib.sha256(account_blob).hexdigest(),
        origin=case.policy.origin).frozen()
    source.update(source_changes or {})
    return export(row), account_blob, source


def import_fee(case, operation, evidence=None):
    return case.media.observe_task_fee(case.task["id"], operation["id"],
        *(evidence or task_evidence(case, operation)), observed_at=observed(operation["raw"]))


def deliver(case, operation):
    query = case.media.query(case.task["id"], operation["id"], key="native-task-fee-query")["query"]
    case.media.record_delivery(case.task["id"], operation["id"], query["id"], image_bytes())
    return query


def test_missing_post_id_can_import_exact_task_quota_without_dispatch_or_rewriting_history(empirical_env, monkeypatch):
    case, _, operation = submitted_case(empirical_env)
    deliver(case, operation)
    before = case.media.store.read_only()
    envelope = scope_envelope(before, case.anchor.account_scope_id)
    query_count = len(posts(case))
    guard_calls = case.guard["calls"]
    evidence = task_evidence(case, operation)
    case.guard["allowed"] = False  # Fee observation cannot become dispatch authority.

    def forbidden(*args, **kwargs):
        raise AssertionError("Import attempted execution or network")

    with monkeypatch.context() as patch:
        for owner, names in ((case.media.capture, ("capture", "ready_raw")),
                (case.env.runtime, ("recover",)), (socket, ("create_connection", "getaddrinfo")),
                (socket.socket, ("connect", "connect_ex")), (MediaExecution, ("__init__",)),
                (type(case.env.runtime), ("__init__",))):
            for name in names:
                patch.setattr(owner, name, forbidden)
        result = import_fee(case, operation, evidence)
        assert result["replayed"] is False
        state_bytes = case.media.store.path("state.json").read_bytes()
        assert import_fee(case, operation, evidence)["replayed"] is True
        later = (timestamp(operation["raw"]["received_at"]) + timedelta(minutes=10)).isoformat()
        assert case.media.observe_task_fee(case.task["id"], operation["id"], *evidence,
            observed_at=later)["replayed"] is True
        assert case.media.store.path("state.json").read_bytes() == state_bytes

    after = case.media.store.read_only()
    fee_id = task_key(operation["receipt"]["accepted_task_id"])
    recorded_op = after["media"]["operation:" + operation["id"]]
    fee = after["media"]["fee:" + fee_id]
    assert recorded_op["fee_id"] == fee_id
    assert fee["candidate"]["schema_version"] == 2
    assert fee["candidate"]["kind"] == "image_task_fee_candidate_v1"
    assert fee["candidate"]["platform_fee_cny"] == "0.040000"
    assert fee["candidate"]["submit_request_id"] is None
    assert fee["candidate"]["request_id_status"] == "missing"
    assert fee["candidate"]["claim_keys"] == [fee_id]
    assert fee["candidate"]["completion_verified"] is False
    assert fee["candidate"]["provider_bill_confirmed"] is False
    assert fee["candidate"]["settlement_performed"] is False
    assert fee["candidate"]["risk_released"] is False
    assert base64.b64decode(fee["export_base64"]) == evidence[0]
    assert base64.b64decode(fee["account_export_base64"]) == evidence[1]
    assert fee["task_fee_source"] == evidence[2]
    assert after["media"]["claim:" + fee_id]["owner"] == "media"
    assert fee_claimed(after, fee_id) and not fee_claimed(after, fee_id, operation["id"])
    assert scope_envelope(after, case.anchor.account_scope_id) == envelope
    assert all(after[name] == before[name] for name in CORE)
    for key, value in before["media"].items():
        expected = {**value, "fee_id": fee_id} if key == "operation:" + operation["id"] else value
        assert after["media"][key] == expected
    assert len(posts(case)) == query_count == 2 and case.guard["calls"] == guard_calls
    view = case.media.get(case.task["id"])["media"]
    assert view["platform_observed_cny"] == "0.040000" and view["paid_call_count"] == 1
    assert view["query_count"] == 1 and view["quality_status"] == "not_reviewed"
    assert view["fees_settled"] is view["risk_released"] is view["provider_bill_confirmed"] is False
    assert view["allowed_actions"] == []


@pytest.mark.parametrize("first_basis", ["direct", "task"])
def test_task_identity_prevents_direct_and_task_reclaim_by_a_second_native_operation(empirical_env, first_basis):
    case, grant, operation = submitted_case(empirical_env, missing=False)
    if first_basis == "direct":
        image_fee(case, operation)
    else:
        import_fee(case, operation)
    deliver(case, operation)
    second = reserve(case, grant, "S02", "native-task-fee-second-submit")
    second = case.media.submit(case.task["id"], second["id"])["operation"]
    assert second["receipt"]["accepted_task_id"] == operation["receipt"]["accepted_task_id"]
    assert second["receipt"]["submit_request_id"] != operation["receipt"]["submit_request_id"]
    before = case.media.store.path("state.json").read_bytes()
    with pytest.raises(CreatorError) as error:
        import_fee(case, second) if first_basis == "direct" else image_fee(case, second)
    assert error.value.code == "FEE_EVIDENCE_CONFLICT"
    assert case.media.store.path("state.json").read_bytes() == before
    payload = case.media.store.read_only()
    assert payload["media"]["operation:" + second["id"]]["fee_id"] is None
    assert len([record for record in payload["media"].values() if record["kind"] == "fee"]) == 1
    assert len(posts(case)) == 3


def test_same_operation_conflicting_amount_never_overwrites_a_native_task_fee(empirical_env):
    case, _, operation = submitted_case(empirical_env)
    import_fee(case, operation)
    before = case.media.store.path("state.json").read_bytes()
    with pytest.raises(CreatorError) as error:
        import_fee(case, operation, task_evidence(case, operation, quota=25000))
    assert error.value.code == "FEE_EVIDENCE_CONFLICT"
    assert case.media.store.path("state.json").read_bytes() == before and len(posts(case)) == 1


def test_task_quota_overrun_is_retained_whole_and_blocks_later_submit(empirical_env):
    case, grant, operation = submitted_case(empirical_env)
    envelope = scope_envelope(case.media.store.read_only(), case.anchor.account_scope_id)
    import_fee(case, operation, task_evidence(case, operation, quota=750000))
    view = case.media.get(case.task["id"])["media"]
    assert view["status"] == "halted" and view["platform_observed_cny"] == "1.500000"
    assert view["extra_exposure_cny"] == "0.500000"
    assert view["reserved_risk_cny"] == f"{case.policy.max_submissions}.000000"
    assert view["risk_released"] is False
    assert scope_envelope(case.media.store.read_only(), case.anchor.account_scope_id) == envelope
    with pytest.raises(CreatorError) as error:
        reserve(case, grant, "S02", "native-task-fee-overrun-submit")
    assert error.value.code == "EMPIRICAL_RISK_OVERRUN" and len(posts(case)) == 1


@pytest.mark.parametrize("invalid", ["task_json", "account_json", "task_user", "task_group", "task_model", "pending", "source_seal", "account_hash"])
def test_invalid_task_or_account_evidence_never_writes_a_fee_or_dispatches(empirical_env, invalid):
    case, _, operation = submitted_case(empirical_env)
    changes = {"task_user": {"user_id": 2}, "task_group": {"group": "other"},
        "task_model": {"model_name": "other"}, "pending": {"status": "RUNNING"}}.get(invalid)
    evidence = list(task_evidence(case, operation, row_changes=changes))
    if invalid == "task_json":
        evidence[0] = b"{}"
    elif invalid == "account_json":
        evidence[1] = b"{}"
    elif invalid == "source_seal":
        evidence[2]["task_fee_source_hash"] = "0" * 64
    elif invalid == "account_hash":
        evidence[2]["account_export_hash"] = "0" * 64
        evidence[2]["task_fee_source_hash"] = digest({key: value for key, value in evidence[2].items()
            if key != "task_fee_source_hash"})
    before = case.media.store.path("state.json").read_bytes()
    with pytest.raises(CreatorError):
        import_fee(case, operation, evidence)
    assert case.media.store.path("state.json").read_bytes() == before and len(posts(case)) == 1
    assert case.media.store.read_only()["media"]["operation:" + operation["id"]]["fee_id"] is None


def test_old_direct_fee_field_shape_and_records_remain_byte_compatible(empirical_env):
    case, _, operation = submitted_case(empirical_env, missing=False)
    image_fee(case, operation)
    before = case.media.store.read_only()
    fee_id = operation["receipt"]["submit_request_id"]
    direct_fee = before["media"]["fee:" + request_key(fee_id)]
    assert direct_fee["candidate"]["schema_version"] == 1
    assert direct_fee["candidate"]["kind"] == "image_fee_candidate_v1"
    assert "task_fee_source" not in direct_fee and "account_export_base64" not in direct_fee
    old_json = json.dumps(before, sort_keys=True, separators=(",", ":"))
    validate(copy.deepcopy(before), historical=True)
    assert json.dumps(case.media.store.read_only(), sort_keys=True, separators=(",", ":")) == old_json
    state_bytes = case.media.store.path("state.json").read_bytes()
    with pytest.raises(CreatorError) as error:
        import_fee(case, operation)
    assert error.value.code == "FEE_EVIDENCE_CONFLICT"
    assert case.media.store.path("state.json").read_bytes() == state_bytes
    assert image_fee(case, operation)["replayed"]


@pytest.mark.parametrize("identity", ["request", "task"])
def test_alias_ownership_is_global_and_typed_even_when_local_ids_coincide(identity):
    candidate = {"kind": "image_task_fee_candidate_v1", "provider_task_id": "synthetic-task-global",
        "submit_request_id": "synthetic-request-global", "platform_log_id": task_key("synthetic-task-global")}
    key = request_key(candidate["submit_request_id"]) if identity == "request" else task_key(candidate["provider_task_id"])
    payload = {"operations": {}, "media": {"fee:image": {"kind": "fee", "operation_id": "same-local-id", "candidate": candidate}},
        "asset_design": {"claim:asset": {"kind": "claim", "owner": "asset", "operation_id": "same-local-id", "id": key}}}
    _validate_fee_owners({**payload, "asset_design": {}})
    with pytest.raises(CreatorError):
        _validate_fee_owners(payload)


@pytest.mark.parametrize("field", ["task_fee_source", "account_export_base64", "candidate"])
def test_task_fee_reconstruction_rejects_resealed_tampering(empirical_env, field):
    case, _, operation = submitted_case(empirical_env)
    import_fee(case, operation)
    before = case.media.store.read_only()
    changed = copy.deepcopy(before)
    fee = next(record for record in changed["media"].values() if record["kind"] == "fee")
    if field == "task_fee_source":
        fee[field]["platform_user_id"] = 2
        fee[field]["task_fee_source_hash"] = digest({key: value for key, value in fee[field].items()
            if key != "task_fee_source_hash"})
    elif field == "account_export_base64":
        fee[field] = base64.b64encode(b"{}").decode()
    else:
        fee[field]["platform_fee_cny"] = "0.000000"
        fee[field]["candidate_hash"] = digest({key: value for key, value in fee[field].items() if key != "candidate_hash"})
    with pytest.raises(CreatorError):
        validate(changed, historical=True)
    assert case.media.store.read_only() == before
