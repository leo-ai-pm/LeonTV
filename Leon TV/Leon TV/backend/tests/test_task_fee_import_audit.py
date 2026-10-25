"""Independent ownership and cross-time import review with synthetic history."""
from datetime import timedelta

import pytest
from test_execution_empirical import empirical_env as empirical_env
from test_native_task_image_fees import submitted_case, task_evidence

from core.creator.store import CreatorError
from core.execution.fees import request_key
from core.execution.media_ledger import candidate_claimed
from core.execution.receipts import timestamp
from core.execution.scope_budget import _validate_fee_owners
from core.execution.task_image_fees import task_key


def proof(*, task="task-native", request="direct-native", kind="image_task_fee_candidate_v1"):
    primary = task_key(task) if kind == "image_task_fee_candidate_v1" else request_key(request)
    return {"kind": kind, "provider_task_id": task, "submit_request_id": request, "platform_log_id": primary}


def empty():
    return {"operations": {}, "media": {}, "asset_design": {}}


@pytest.mark.parametrize("alias", ["task", "request"])
@pytest.mark.parametrize("kind", ["image_task_fee_candidate_v1", "image_fee_candidate_v1"])
def test_global_identity_detects_different_primary_keys_for_either_available_alias(alias, kind):
    first = proof(kind=kind)
    payload = empty()
    payload["media"]["fee:first"] = {"kind": "fee", "operation_id": "first-op", "candidate": first}
    _validate_fee_owners(payload)  # Positive control: the first candidate is valid.
    second = proof(task="another-task" if alias == "request" else "task-native",
                   request="another-request" if alias == "task" else "direct-native")
    assert candidate_claimed(payload, second, "second-op")
    payload["media"]["fee:second"] = {"kind": "fee", "operation_id": "second-op", "candidate": second}
    with pytest.raises(CreatorError):
        _validate_fee_owners(payload)


@pytest.mark.parametrize("namespace,owner", [("asset_design", "asset"), ("media", "text")])
def test_local_id_equality_cannot_change_the_typed_fee_owner(namespace, owner):
    candidate = proof()
    payload = empty()
    payload["media"]["fee:first"] = {"kind": "fee", "operation_id": "same-id", "candidate": candidate}
    _validate_fee_owners(payload)
    payload[namespace]["claim:second"] = {"kind": "claim", "operation_id": "same-id", "owner": owner,
                                          "id": request_key("direct-native")}
    with pytest.raises(CreatorError):
        _validate_fee_owners(payload)


def test_missing_request_identity_does_not_claim_the_unrelated_account_consumption_row():
    candidate = proof(request=None)
    payload = empty()
    payload["media"]["fee:first"] = {"kind": "fee", "operation_id": "native-op", "candidate": candidate}
    payload["operations"]["text-op"] = {"id": "text-op", "empirical": {"fee_evidence": {
        "platform_log_id": request_key("unrelated-account-witness")}}}
    _validate_fee_owners(payload)
    assert not candidate_claimed(payload, proof(task="another-task", request="another-request"), "another-op")


def test_identical_task_import_is_idempotent_across_caller_observation_times(empirical_env):
    case, _, operation = submitted_case(empirical_env)
    evidence = task_evidence(case, operation)
    first_time = (timestamp(operation["raw"]["received_at"]) + timedelta(seconds=1)).isoformat()
    later_time = (timestamp(operation["raw"]["received_at"]) + timedelta(seconds=30)).isoformat()
    first = case.media.observe_task_fee(case.task["id"], operation["id"], *evidence, observed_at=first_time)
    assert not first["replayed"]
    original = case.media.store.path("state.json").read_bytes()
    later = case.media.observe_task_fee(case.task["id"], operation["id"], *evidence, observed_at=later_time)
    assert later["replayed"]
    assert case.media.store.path("state.json").read_bytes() == original
    payload = case.media.store.read_only()
    recorded = payload["media"]["fee:" + first["operation"]["fee_id"]]["candidate"]
    assert recorded["observed_at"] == first_time
