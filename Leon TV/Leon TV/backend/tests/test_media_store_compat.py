"""Independent legacy/optional-namespace checks; no private authority or HTTP."""
import base64
import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_execution_empirical import empirical_env as empirical_env
from test_execution_roles import role_env as role_env
from test_planner_execution import no_network as no_network

from core.creator.store import CreatorError, digest, utc_now
from core.execution.media_ledger import account_halted, has_pending_unknown, projection
from core.execution.media_runtime import MediaExecution
from core.execution.runtime import ExecutionRuntime
from core.execution.store import EMPTY, ExecutionStore, validate_payload, validate_state
from core.planner.runtime import PlannerRuntime

CORE = {"tasks", "quotes", "grants", "operations", "keys", "accounts"}
FIXTURE = Path(__file__).parent / "fixtures/d09_legacy_execution.json"


def legacy_bytes():
    fixture = json.loads(FIXTURE.read_text())
    assert fixture["synthetic_only"] is True
    return base64.b64decode(fixture["journal_base64"])


def store_with_legacy(tmp_path):
    store = ExecutionStore(tmp_path.resolve() / "execution")
    store.root.mkdir(mode=0o700)
    store.path("state.json").write_bytes(legacy_bytes())
    store.path("state.json").chmod(0o600)
    return store


def append_frame(state, delta):
    state = copy.deepcopy(state)
    frame = {"revision": len(state["frames"]) + 1, "saved_at": utc_now(),
             "previous_hash": state["head_hash"], "delta": copy.deepcopy(delta)}
    frame["hash"] = digest(frame)
    state["frames"].append(frame)
    state["head_revision"], state["head_hash"] = frame["revision"], frame["hash"]
    return state


def test_absent_execution_store_remains_exactly_the_original_six_namespaces(tmp_path):
    store = ExecutionStore(tmp_path.resolve() / "missing")
    assert set(EMPTY) == CORE
    assert store.read_only() == {name: {} for name in CORE}
    assert set(store.read_only()) == CORE and not store.root.exists()


def test_d09_replay_and_reopen_never_backfill_media_or_change_original_journal_bytes(tmp_path):
    store = store_with_legacy(tmp_path)
    original = store.path("state.json").read_bytes()
    payload = store.read_only()
    assert set(payload) == CORE
    runtime = ExecutionRuntime(store.root, SimpleNamespace(source_summary=PlannerRuntime.source_summary))
    try:
        for _ in range(2):
            assert validate_state(json.loads(original)) == payload
            assert ExecutionStore(store.root).read_only() == payload
            with store.transaction() as current:
                assert current == payload and set(current) == CORE
            for verb in ("quote", "grant"):
                _, receipt = next((name, value) for name, value in payload["keys"].items() if name.startswith(verb + ":"))
                request = copy.deepcopy(receipt["request"])
                task_id = request.pop("task_id")
                assert getattr(runtime, verb)(task_id, request)["replayed"] is True
            assert set(runtime.store.read_only()) == CORE
            assert store.path("state.json").read_bytes() == original
    finally:
        runtime.close()


@pytest.mark.parametrize("profile", ["roles", "empirical"])
def test_pre_media_role_and_empirical_histories_remain_native_and_disabled_for_images(role_env, empirical_env, profile):
    env = role_env("first_accepted") if profile == "roles" else empirical_env("first_accepted")
    task = env.source_task()
    grant = env.grant(task)
    assert env.action(task, grant["execution"]).status_code == 200
    final = env.wait(task, {"plan_ready"}) if profile == "roles" else env.complete(task)
    assert final["status"] == "plan_ready"
    store = env.runtime.store
    original = store.path("state.json").read_bytes()
    original_payload = store.read_only()
    assert set(original_payload) == CORE
    calls = len(env.provider.calls) if profile == "roles" else len(env.calls())
    media = MediaExecution(env.runtime)
    for _ in range(2):
        result = media.get(task["id"])["media"]
        assert result["status"] == "not_authorized" and result["paid_call_count"] == result["scope_count"] == 0
        assert result["allowed_actions"] == [] and result["risk_released"] is False
        assert ExecutionStore(store.root).read_only() == original_payload
        with store.transaction() as payload:
            assert set(payload) == CORE
        assert store.path("state.json").read_bytes() == original
    with pytest.raises(CreatorError) as error:
        media.quote(task["id"], key="no-implicit-image-authority")
    assert error.value.code == "MEDIA_DISABLED"
    assert store.path("state.json").read_bytes() == original and not (store.root / "media-receipts").exists()
    assert (len(env.provider.calls) if profile == "roles" else len(env.calls())) == calls


@pytest.mark.parametrize("value", [None, [], True, 1, "authorized", {"operation:missing-id": {}},
    {"scope:claimed": {"kind": "scope", "id": "claimed", "paid_calls": 12}},
    {"grant:claimed": {"kind": "grant", "id": "claimed", "consent": True}},
    {"claim:claimed": {"kind": "claim", "id": "claimed", "owner": "media"}}])
def test_rehashed_optional_media_corruption_fails_read_and_transaction_entry_without_rewrite(tmp_path, value):
    store = store_with_legacy(tmp_path)
    state = append_frame(json.loads(store.path("state.json").read_text()), {"media": value})
    encoded = json.dumps(state, ensure_ascii=False).encode()
    store.path("state.json").write_bytes(encoded)
    with pytest.raises(CreatorError):
        validate_state(state)
    with pytest.raises(CreatorError) as error:
        store.read_only()
    assert error.value.code == "CORRUPT_STORAGE"
    entered = False
    with pytest.raises(CreatorError) as error:
        with store.transaction():
            entered = True
    assert error.value.code == "CORRUPT_STORAGE" and entered is False
    assert store.path("state.json").read_bytes() == encoded


@pytest.mark.parametrize("value", [None, [], True, 1.0, (), float("nan"), {True: {}}, {"grant:": {"id": "", "kind": "grant"}}])
def test_transaction_cannot_persist_non_json_or_self_declared_media_authority(tmp_path, value):
    store = store_with_legacy(tmp_path)
    original = store.path("state.json").read_bytes()
    with pytest.raises(CreatorError) as error:
        with store.transaction() as payload:
            payload["media"] = copy.deepcopy(value)
    assert error.value.code == "CORRUPT_STORAGE" and store.path("state.json").read_bytes() == original


@pytest.mark.parametrize("namespace", [*sorted(CORE), "media"])
def test_transaction_cannot_delete_any_previously_present_namespace(tmp_path, namespace):
    store = store_with_legacy(tmp_path)
    state = append_frame(json.loads(store.path("state.json").read_text()), {"media": {}})
    validate_state(state)
    original = json.dumps(state, ensure_ascii=False).encode()
    store.path("state.json").write_bytes(original)
    with pytest.raises(CreatorError) as error:
        with store.transaction() as payload:
            del payload[namespace]
    assert error.value.code == "CORRUPT_STORAGE" and store.path("state.json").read_bytes() == original


@pytest.mark.parametrize("namespace", ["images", "media_operations", "global_claims", "media_grants"])
def test_undeclared_namespace_cannot_smuggle_an_unvalidated_second_ledger(tmp_path, namespace):
    store = store_with_legacy(tmp_path)
    original = store.path("state.json").read_bytes()
    with pytest.raises(CreatorError):
        with store.transaction() as payload:
            payload[namespace] = {"grant": {"consent": True, "paid_calls": 12}}
    assert store.path("state.json").read_bytes() == original
    state = append_frame(json.loads(original), {namespace: {}})
    with pytest.raises(CreatorError):
        validate_state(state)


def test_an_empty_optional_media_namespace_grants_no_permission_or_risk(tmp_path):
    store = store_with_legacy(tmp_path)
    payload = store.read_only()
    payload["media"] = {}
    validate_payload(payload)
    result = projection(payload)
    assert result["status"] == "not_authorized" and result["scope_count"] == result["paid_call_count"] == result["query_count"] == 0
    assert result["reserved_risk_cny"] == "0.000000" and result["platform_observed_cny"] is None
    assert result["allowed_actions"] == []
    account_id = next(iter(payload["accounts"]))
    assert account_halted(payload, account_id) is False and has_pending_unknown(payload, account_id) is False
