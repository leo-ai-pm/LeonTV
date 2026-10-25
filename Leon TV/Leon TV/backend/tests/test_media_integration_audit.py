"""Independent media accounting attacks against the original execution journal."""
import base64
import copy
import json
import uuid

import httpx
import pytest
from image_tool_fixture import make_transport
from media_ledger_fixture import authorize, image_fee, media_case, posts, reserve
from test_execution_empirical import empirical_env as empirical_env
from test_planner_execution import no_network as no_network

from core.creator.store import CreatorError, digest, utc_now
from core.execution.image_fees import candidate_for
from core.execution.image_policy import FrozenImageRequest
from core.execution.media_ledger import records
from core.execution.media_runtime import MediaExecution
from core.execution.receipts import ReceiptCaptureSettings
from core.execution.store import ExecutionStore, validate_state

CORE = ("tasks", "quotes", "grants", "operations", "keys", "accounts")


def collision_transport(options):
    """A real detached synthetic HTTP request with a deliberately reused ID."""
    transport = make_transport(options)

    def handle(request):
        response = transport.handle_request(request)
        response.headers["x-request-id"] = options["request_id"]
        return response
    return httpx.MockTransport(handle)


def restart(case, *, settings=None):
    return MediaExecution(case.env.runtime, policy=case.policy, budget_anchor=case.anchor, manifest=case.manifest,
        authority_guard=case.authority, fee_source=case.source, receipt_capture=settings or case.settings)


def rechain(state):
    state = copy.deepcopy(state)
    previous = None
    for revision, frame in enumerate(state["frames"], 1):
        frame["revision"], frame["previous_hash"] = revision, previous
        frame["hash"] = digest({key: value for key, value in frame.items() if key != "hash"})
        previous = frame["hash"]
    state["head_revision"], state["head_hash"] = len(state["frames"]), previous
    return state


def append_attack(state, changed):
    state = copy.deepcopy(state)
    state["frames"].append({"revision": 0, "saved_at": utc_now(), "previous_hash": None,
                            "delta": {"media": changed}, "hash": None})
    return rechain(state)


def assert_all_entry_points_reject(store, state):
    # Every frame seal is valid: the expected rejection is semantic/history based.
    assert rechain(state) == state
    with pytest.raises(CreatorError):
        validate_state(state)
    path = store.path("state.json")
    original = path.read_bytes()
    encoded = json.dumps(state, ensure_ascii=False, allow_nan=False).encode()
    path.write_bytes(encoded)
    try:
        with pytest.raises(CreatorError) as error:
            ExecutionStore(store.root).read_only()
        assert error.value.code == "CORRUPT_STORAGE"
        entered = False
        with pytest.raises(CreatorError) as error:
            with store.transaction():
                entered = True
        assert error.value.code == "CORRUPT_STORAGE" and entered is False
        assert path.read_bytes() == encoded
    finally:
        path.write_bytes(original)


def submitted(case):
    quote, grant = authorize(case)
    operation = reserve(case, grant)
    return quote, grant, case.media.submit(case.task["id"], operation["id"])["operation"]


def test_media_quote_and_grant_preserve_core_history_and_unused_parent_reservations(empirical_env):
    case = media_case(empirical_env)
    store = case.env.runtime.store
    before = store.read_only()
    frames = json.loads(store.path("state.json").read_text())["frames"]
    _, _ = authorize(case)
    after = store.read_only()
    assert {key: after[key] for key in CORE} == {key: before[key] for key in CORE}
    assert json.loads(store.path("state.json").read_text())["frames"][:len(frames)] == frames
    scope = records(after, "scope")[0]
    assert scope["anchor"]["opening_risk_cny"] == "75.37"
    assert scope["anchor"]["opening_paid_call_count"] == 47
    assert scope["anchor"]["account_limit_cny"] == "77.37" and scope["anchor"]["account_max_calls"] == 49
    result = case.media.get(case.task["id"])["media"]
    assert result["paid_call_count"] == result["query_count"] == 0 and posts(case) == []
    assert result["risk_released"] is False and result["provider_bill_confirmed"] is False
    assert not {"descriptor", "wire", "raw", "export_base64", "bytes_base64", "api_key"} & set(result)


def test_unknown_media_holds_survive_regrant_new_shot_new_key_and_restart(empirical_env):
    case = media_case(empirical_env, fault="unknown")
    _, grant, operation = submitted(case)
    assert operation["status"] == "unknown" and operation["raw"] is None and len(posts(case)) == 1
    quote = case.media.quote(case.task["id"], key="audit-replacement-quote")["quote"]
    renewed = case.media.grant(case.task["id"], quote["id"], disclosure_hash=quote["disclosure_hash"], consent=True,
        key="audit-replacement-grant")["grant"]
    reloaded = restart(case)
    before = case.env.runtime.store.path("state.json").read_bytes()
    for selected_grant in (grant, renewed):
        for shot in ("S01", "S02"):
            with pytest.raises(CreatorError) as error:
                reloaded.reserve_submit(case.task["id"], selected_grant["id"], shot, key="audit-repeat-" + selected_grant["id"] + "-" + shot)
            assert error.value.code == "MEDIA_RESPONSE_UNCONFIRMED"
    assert reloaded.submit(case.task["id"], operation["id"])["replayed"] is True
    assert reloaded.recover(case.task["id"], operation["id"])["operation"]["status"] == "unknown"
    result = reloaded.get(case.task["id"])["media"]
    assert result["paid_call_count"] == 1 and result["reserved_risk_cny"] == "2.000000" and not result["risk_released"]
    assert len(posts(case)) == 1 and case.env.runtime.store.path("state.json").read_bytes() == before


def test_legacy_fee_aliases_require_exact_bridge_without_rewriting_text_evidence(empirical_env):
    case = media_case(empirical_env, legacy=True)
    quote = case.media.quote(case.task["id"], key="audit-legacy-quote")["quote"]
    before = case.env.runtime.store.read_only()
    original_core = {name: copy.deepcopy(before[name]) for name in CORE}
    with pytest.raises(CreatorError) as error:
        case.media.grant(case.task["id"], quote["id"], disclosure_hash=quote["disclosure_hash"], consent=True, key="audit-legacy-grant")
    assert error.value.code == "MEDIA_LEGACY_FEE_BRIDGE_REQUIRED" and posts(case) == []
    first_id, first_blob = next(iter(case.text_exports.items()))
    original = case.env.runtime.store.path("state.json").read_bytes()
    with pytest.raises(CreatorError):
        case.media.register_text_claim(first_id, first_blob + b"\n")
    assert case.env.runtime.store.path("state.json").read_bytes() == original
    for operation_id, blob in case.text_exports.items():
        record = case.media.register_text_claim(operation_id, blob)
        assert record["replayed"] is False
        assert case.media.register_text_claim(operation_id, blob)["replayed"] is True
    after = case.env.runtime.store.read_only()
    assert {name: after[name] for name in CORE} == original_core
    assert len(records(after, "claim")) == len(case.text_exports)
    assert all(op["empirical"]["fee_evidence"]["platform_log_id"].startswith("tudou:1:") for op in after["operations"].values())
    assert case.media.grant(case.task["id"], quote["id"], disclosure_hash=quote["disclosure_hash"], consent=True, key="audit-legacy-grant")["replayed"] is False
    assert posts(case) == []


def test_media_fee_overrun_is_retained_and_halts_another_ready_text_task(empirical_env):
    case = media_case(empirical_env)
    second = case.env.source_task("audit-overrun-second")
    ready = case.env.grant(second, case.env.quote(second, key="audit-overrun-text-quote"), key="audit-overrun-text-grant")["execution"]
    _, _, operation = submitted(case)
    image_fee(case, operation, quota=1250000)
    payload = case.env.runtime.store.read_only()
    fee = records(payload, "fee")[0]
    assert fee["candidate"]["platform_fee_cny"] == "2.500000" and fee["candidate"]["overrun"] is True
    view = case.media.get(case.task["id"])["media"]
    assert view["status"] == "halted" and view["platform_observed_cny"] == "2.500000" and not view["risk_released"]
    calls = len(case.env.calls())
    response = case.env.action(second, ready, key="audit-overrun-cannot-start-text")
    assert response.status_code == 409
    assert len(case.env.calls()) == calls and len(posts(case)) == 1
    assert case.env.runtime.store.read_only()["tasks"][second["id"]]["plan"] is None
    assert records(case.env.runtime.store.read_only(), "fee")[0] == fee


def test_query_replays_are_not_paid_calls_and_all_new_queries_remain_bounded(empirical_env):
    case = media_case(empirical_env)
    _, _, operation = submitted(case)
    first = case.media.query(case.task["id"], operation["id"], key="audit-query-one")["query"]
    assert first["status"] == "completed" and len(posts(case)) == 2
    original = case.env.runtime.store.path("state.json").read_bytes()
    repeated = restart(case).query(case.task["id"], operation["id"], key="audit-query-one")
    assert repeated["query"] == first and repeated["replayed"] is True
    assert case.env.runtime.store.path("state.json").read_bytes() == original and len(posts(case)) == 2
    for index in (2, 3):
        result = case.media.query(case.task["id"], operation["id"], key=f"audit-query-{index}")["query"]
        descriptor = FrozenImageRequest.from_frozen(result["descriptor"])
        assert descriptor.method == "GET" and descriptor.wire == "" and result["query_index"] == index
        assert descriptor.frozen()["permit"]["paid_calls"] == 0 and descriptor.frozen()["permit"]["risk_cny"] == "0.000000"
    with pytest.raises(CreatorError) as error:
        case.media.query(case.task["id"], operation["id"], key="audit-query-four")
    assert error.value.code == "MEDIA_QUERY_LIMIT" and len(posts(case)) == 4
    view = case.media.get(case.task["id"])["media"]
    assert view["paid_call_count"] == 1 and view["query_count"] == 3 and view["reserved_risk_cny"] == "2.000000"


def test_text_canonical_fee_claim_prevents_media_reusing_the_same_request_identity(empirical_env):
    case = media_case(empirical_env)
    text_id = next(iter(case.text_exports))
    shared_id = "text-" + text_id
    settings = ReceiptCaptureSettings(observation_wait_seconds=5,
        worker_factory="test_media_integration_audit:collision_transport",
        worker_options={"counter": str(case.counter), "request_id": shared_id})
    case.media = restart(case, settings=settings)
    _, _, operation = submitted(case)
    assert operation["receipt"]["submit_request_id"] == shared_id
    before = case.env.runtime.store.path("state.json").read_bytes()
    with pytest.raises(CreatorError) as error:
        image_fee(case, operation)
    assert error.value.code == "FEE_EVIDENCE_CONFLICT"
    assert case.env.runtime.store.path("state.json").read_bytes() == before and len(posts(case)) == 1


def test_media_canonical_fee_claim_prevents_later_text_from_claiming_it(empirical_env):
    case = media_case(empirical_env)
    _, _, operation = submitted(case)
    image_fee(case, operation)
    claimed = records(case.env.runtime.store.read_only(), "fee")[0]["id"]
    second = case.env.source_task("audit-fee-claim-second")
    grant = case.env.grant(second, case.env.quote(second, key="audit-fee-second-quote"), key="audit-fee-second-grant")
    assert case.env.action(second, grant["execution"], key="audit-fee-second-start").status_code == 200
    current = case.env.wait(second, {"needs_reconciliation"})
    op_id = current["operations"][-1]["id"]
    evidence = case.env.evidence(op_id) | {"platform_log_id": claimed}
    before = case.env.runtime.store.path("state.json").read_bytes()
    with pytest.raises(CreatorError) as error:
        case.env.runtime.observe_fee(second["id"], op_id, evidence)
    assert error.value.code == "FEE_EVIDENCE_CONFLICT"
    assert case.env.runtime.store.path("state.json").read_bytes() == before
    assert len(posts(case)) == 1 and len(case.env.calls()) == 4


@pytest.mark.parametrize("attack", ["scope_calls", "scope_risk", "handoff", "grant_quote", "grant_disclosure", "wire",
    "shot", "paid_flag", "reset_operation", "replace_raw", "fee_amount", "closed_fee_rewrite", "query_task", "query_index", "implicit_grant"])
def test_fully_rehashed_media_history_attacks_fail_every_store_entry(empirical_env, attack):
    case = media_case(empirical_env)
    _, grant, operation = submitted(case)
    image_fee(case, operation)
    query = case.media.query(case.task["id"], operation["id"], key="audit-rehash-query")["query"]
    store = case.env.runtime.store
    payload = store.read_only()
    scope = copy.deepcopy(records(payload, "scope")[0])
    operation = copy.deepcopy(payload["media"]["operation:" + operation["id"]])
    grant = copy.deepcopy(payload["media"]["grant:" + grant["id"]])
    query = copy.deepcopy(payload["media"]["query:" + query["id"]])
    fee = copy.deepcopy(records(payload, "fee")[0])
    if attack == "scope_calls":
        scope["anchor"]["opening_paid_call_count"] = 45
        scope["anchor"]["account_max_calls"] = 47
        scope["anchor"]["budget_anchor_hash"] = digest({key: value for key, value in scope["anchor"].items() if key != "budget_anchor_hash"})
        changed = scope
    elif attack == "scope_risk":
        scope["anchor"]["opening_risk_cny"] = "73.37"
        scope["anchor"]["account_limit_cny"] = "75.37"
        scope["anchor"]["budget_anchor_hash"] = digest({key: value for key, value in scope["anchor"].items() if key != "budget_anchor_hash"})
        changed = scope
    elif attack == "handoff":
        scope["handoff"]["plan"]["artifact"]["shots"][0]["visual"] = "SYNTHETIC REPLACEMENT"
        scope["handoff"]["handoff_hash"] = digest({key: value for key, value in scope["handoff"].items() if key != "handoff_hash"})
        changed = scope
    elif attack in {"grant_quote", "grant_disclosure", "implicit_grant"}:
        if attack == "implicit_grant":
            grant["id"] = uuid.uuid4().hex
        else:
            grant["quote_id" if attack == "grant_quote" else "disclosure_hash"] = "f" * (32 if attack == "grant_quote" else 64)
        changed = grant
    elif attack in {"fee_amount", "closed_fee_rewrite"}:
        if attack == "closed_fee_rewrite":
            raw_export = json.loads(base64.b64decode(fee["export_base64"]))
            raw_export["data"]["items"][0]["quota"] = 0
            raw_export = json.dumps(raw_export, ensure_ascii=False).encode()
            fee["export_base64"] = base64.b64encode(raw_export).decode()
            fee["candidate"] = candidate_for(FrozenImageRequest.from_frozen(operation["descriptor"]), operation["raw"], raw_export,
                scope["fee_source"], observed_at=fee["candidate"]["observed_at"])
            claim = copy.deepcopy(payload["media"]["claim:" + fee["id"]])
            claim.update(evidence_hash=digest(fee["candidate"]), export_base64=fee["export_base64"])
            state = append_attack(json.loads(store.path("state.json").read_text()), {"fee:" + fee["id"]: fee, "claim:" + claim["id"]: claim})
            assert_all_entry_points_reject(store, state)
            assert len(posts(case)) == 2
            return
        fee["candidate"]["platform_fee_cny"] = "0.000000"
        fee["candidate"]["candidate_hash"] = digest({key: value for key, value in fee["candidate"].items() if key != "candidate_hash"})
        changed = fee
    elif attack in {"query_task", "query_index"}:
        if attack == "query_task":
            query["descriptor"]["accepted_task_id"] = "different-task"
        else:
            query["query_index"] = 2
            query["descriptor"]["query_index"] = 2
            query["descriptor"]["permit"]["query_index"] = 2
            query["descriptor"]["permit"]["permit_hash"] = digest({key: value for key, value in query["descriptor"]["permit"].items() if key != "permit_hash"})
        query["descriptor"]["descriptor_hash"] = digest({key: value for key, value in query["descriptor"].items() if key != "descriptor_hash"})
        changed = query
    else:
        if attack == "wire":
            wire = json.loads(operation["descriptor"]["wire"])
            wire["n"] = 2
            operation["descriptor"]["wire"] = json.dumps(wire)
        elif attack == "shot":
            operation["shot_id"] = "S02"
        elif attack == "paid_flag":
            operation["descriptor"]["permit"]["paid_calls"] = 0
            operation["descriptor"]["permit"]["permit_hash"] = digest({key: value for key, value in operation["descriptor"]["permit"].items() if key != "permit_hash"})
        elif attack == "reset_operation":
            operation.update(status="reserved", capture_started_at=None, raw=None, receipt=None, fee_id=None, delivery_id=None)
        else:
            operation["raw"]["headers"]["x-request-id"] = "replacement-request"
        operation["descriptor"]["descriptor_hash"] = digest({key: value for key, value in operation["descriptor"].items() if key != "descriptor_hash"})
        changed = operation
    state = append_attack(json.loads(store.path("state.json").read_text()), {changed["kind"] + ":" + changed["id"]: changed})
    assert_all_entry_points_reject(store, state)
    assert len(posts(case)) == 2
