"""Native replay must recheck changes, not unchanged records at every prefix."""
import copy
import json
import threading
from contextvars import copy_context

import pytest
from media_ledger_fixture import authorize, image_fee, media_case, reserve
from test_execution_empirical import empirical_env as empirical_env
from test_image_receipts import image_bytes

from core.creator.store import CreatorError, digest
from core.execution import history_validation as history
from core.execution import image_receipts, media_ledger
from core.execution.store import validate_state


def saved_history(empirical_env):
    case = media_case(empirical_env)
    assert case.policy.validation_only is True
    _, grant = authorize(case)
    operation = reserve(case, grant)
    operation = case.media.submit(case.task["id"], operation["id"])["operation"]
    image_fee(case, operation)
    query = case.media.query(case.task["id"], operation["id"], key="history-proof-query")["query"]
    case.media.record_delivery(case.task["id"], operation["id"], query["id"], image_bytes())
    for index in range(3):
        case.media.quote(case.task["id"], key="history-unrelated-quote-" + str(index))
    state = json.loads(case.media.store.path("state.json").read_bytes())
    return case, state


def test_native_replay_checks_unchanged_delivery_and_complete_scope_inputs_once(empirical_env, monkeypatch):
    case, state = saved_history(empirical_env)
    original_record, original_scopes = media_ledger._validate_record, media_ledger._validate_scope_set
    calls, scopes = [], []

    def record(payload, current, used):
        if current["kind"] == "delivery":
            calls.append(current["id"])
        return original_record(payload, current, used)

    def scope_set(payload, current):
        if current:
            scopes.append(True)
        return original_scopes(payload, current)

    monkeypatch.setattr(media_ledger, "_validate_record", record)
    monkeypatch.setattr(media_ledger, "_validate_scope_set", scope_set)
    original = case.media.store.path("state.json").read_bytes()
    for _ in range(2):
        calls.clear()
        scopes.clear()
        validated = validate_state(state)
        assert validated["media"]
        assert len(calls) == 1, "same complete delivery/query/scope inputs were checked again at later prefixes"
        assert len(scopes) == 1, "same complete scope/CORE/asset inputs were checked again"
    assert case.media.store.path("state.json").read_bytes() == original


def bind(payload):
    actual = copy.deepcopy(payload)
    history.start(actual)
    for name, records in actual.items():
        history.apply(name, records)
    return actual


@pytest.mark.parametrize("dependency", ["query", "scope", "account", "delivery", "decoder"])
def test_actual_relationship_validation_rechecks_changed_complete_dependencies(empirical_env, monkeypatch, dependency):
    case, state = saved_history(empirical_env)
    payload = validate_state(state)
    original = case.media.store.path("state.json").read_bytes()
    with history.reconstruction({}):
        actual = bind(payload)
        media_ledger.validate(actual)
        history.commit(actual)
        # This call uses successful proofs only for identical complete inputs.
        media_ledger.validate(actual)
        if dependency == "decoder":
            monkeypatch.setattr(image_receipts, "MAX_IMAGE_PIXELS", 1)
        else:
            kind = {"account": "accounts"}.get(dependency, "media")
            key = next(k for k, r in actual[kind].items() if dependency == "account" or r["kind"] == dependency)
            record = actual[kind][key]
            if dependency == "query":
                record["raw"]["http_status"] = 404
            elif dependency == "scope":
                record["policy"]["valid_until"] = "2000-01-01T00:00:00+00:00"
            elif dependency == "account":
                record["max_calls"] += 1
            else:
                record["delivery"]["width"] += 1
            history.apply(kind, {key: record})
        for _ in range(2):
            with pytest.raises(CreatorError) as error:
                media_ledger.validate(actual)
            assert error.value.code == "CORRUPT_STORAGE"
    assert case.media.store.path("state.json").read_bytes() == original


def append_frame(state, delta):
    value = copy.deepcopy(state)
    frame = {"revision": value["head_revision"] + 1, "saved_at": value["frames"][-1]["saved_at"],
             "previous_hash": value["head_hash"], "delta": delta}
    frame["hash"] = digest(frame)
    value["frames"].append(frame)
    value["head_revision"] += 1
    value["head_hash"] = frame["hash"]
    return value


@pytest.mark.parametrize("attack", ["immutable_delivery", "terminal_transition", "revoked_grant", "frame_hash"])
def test_all_history_and_frame_rules_still_reject_rehashed_illegal_changes(empirical_env, attack):
    case, state = saved_history(empirical_env)
    payload = validate_state(state)
    kind = "delivery" if attack == "immutable_delivery" else "grant" if attack == "revoked_grant" else "operation"
    key, record = next((k, copy.deepcopy(r)) for k, r in payload["media"].items() if r["kind"] == kind)
    if attack == "immutable_delivery":
        record["created_at"] = record["created_at"].replace("+00:00", "+01:00")
    elif attack == "terminal_transition":
        record["status"] = "reserved"
        for field in ("capture_started_at", "raw", "receipt", "fee_id", "delivery_id"):
            record[field] = None
    elif attack == "revoked_grant":
        record["revoked_at"] = "2000-01-01T00:00:00+00:00"
    changed = append_frame(state, {"media": {key: record}})
    if attack == "frame_hash":
        changed["frames"][0]["saved_at"] += "changed-without-hash"
    before = case.media.store.path("state.json").read_bytes()
    with pytest.raises(CreatorError) as error:
        validate_state(changed)
    assert error.value.code == "CORRUPT_STORAGE"
    assert case.media.store.path("state.json").read_bytes() == before


def test_proof_keys_use_all_typed_values_and_defensive_outputs_not_ids_or_hashes(monkeypatch):
    # Even deliberately colliding actual-value fingerprints cannot substitute
    # for full typed marker equality.
    monkeypatch.setattr(history, "hash", lambda _value: 0, raising=False)
    calls = []
    with history.reconstruction({}):
        actual = bind({"tasks": {"same-id": {"claimed_hash": "a" * 64, "value": True}}})
        facts = history.current(actual)

        def compute(payload, extra):
            calls.append(True)
            return {"record": payload["tasks"]["same-id"], "extra": extra}

        selection = {"tasks": ("same-id",)}
        for value in (True, 1, 1.0, 0.0, -0.0):
            actual["tasks"]["same-id"]["value"] = value
            history.apply("tasks", actual["tasks"])
            output = facts.prove("slot", selection, {}, compute)
            output["record"].clear()
            assert facts.prove("slot", selection, {}, compute)["record"]["value"] == value
            history.commit(actual)
        assert len(calls) == 5


def test_failed_proofs_environment_drift_and_capacity_do_not_seed_success(monkeypatch):
    calls = []
    with history.reconstruction({}):
        actual = bind({"tasks": {"x": {"n": 1}}})
        facts = history.current(actual)

        def failed(*_args):
            calls.append(True)
            raise ValueError("original rejection")

        for _ in range(2):
            with pytest.raises(ValueError, match="original rejection"):
                facts.prove("slot", {"tasks": ("x",)}, {}, failed)
        assert not facts.pending and not facts.proofs
        before = history._environment
        drift = [0]
        monkeypatch.setattr(history, "_environment", lambda: {"version": drift[0]})

        def changing(*_args):
            calls.append(True)
            drift[0] += 1
            return {}

        facts.prove("slot", {"tasks": ("x",)}, {}, changing)
        assert not facts.pending
        monkeypatch.setattr(history, "_environment", before)
        monkeypatch.setattr(history, "_MAX_PROOFS", 0)
        for _ in range(2):
            facts.prove("slot", {"tasks": ("x",)}, {}, lambda *_args: calls.append(True))
        assert len(calls) == 5 and not facts.pending


@pytest.mark.parametrize("mutation", ["owned_payload", "native_snapshot"])
def test_same_read_mutation_of_owned_inputs_cannot_escape_integrity_guard(mutation):
    with pytest.raises(CreatorError) as error, history.reconstruction({"native": [1]}) as stable:
        actual = bind({"tasks": {"x": {"n": 1}}})
        if mutation == "owned_payload":
            actual["tasks"]["x"]["n"] = True
            history.commit(actual)
        else:
            stable["native"].append(2)
    assert error.value.code == "CORRUPT_STORAGE"
    assert history.current() is None


def test_nested_exception_copied_context_and_threads_cannot_extend_proof_lifetime():
    with history.reconstruction({}):
        actual = bind({"tasks": {"x": {"n": 1}}})
        outer = history.current(actual)
        copied = copy_context()
        result = []
        thread = threading.Thread(target=lambda: result.append(copied.run(history.current, actual)))
        thread.start()
        thread.join()
        assert result == [None]
        with pytest.raises(RuntimeError), history.reconstruction({}):
            assert history.current() is not outer
            raise RuntimeError("nested exit")
        assert history.current(actual) is outer
        with history.reconstruction({"non_json": (1, 2)}):
            assert history.current() is None
        assert history.current(actual) is outer
    assert copied.run(history.current, actual) is None
    assert not outer.active and not outer.tables and not outer.proofs and not outer.pending


def test_oversize_graph_falls_back_to_original_validator(empirical_env, monkeypatch):
    _, state = saved_history(empirical_env)
    expected = validate_state(state)
    monkeypatch.setattr(history, "_MAX_GRAPH_BYTES", 1)
    assert validate_state(state) == expected


def test_caller_mutation_after_owned_snapshot_cannot_poison_a_later_read(empirical_env, monkeypatch):
    _, state = saved_history(empirical_env)
    expected = validate_state(state)
    original = media_ledger._validate_record
    changed = []

    def validate(payload, record, used):
        if record["kind"] == "delivery" and not changed:
            for frame in state["frames"]:
                for source in frame["delta"].get("media", {}).values():
                    if source["kind"] == "delivery":
                        source["delivery"]["width"] += 1
                        changed.append(True)
        return original(payload, record, used)

    monkeypatch.setattr(media_ledger, "_validate_record", validate)
    assert validate_state(state) == expected and changed
    with pytest.raises(CreatorError):
        validate_state(state)


def test_scope_manifest_origin_binds_global_media_capture_and_financial_membership(monkeypatch):
    calls = []
    payload = {name: {} for name in history.CORE}
    payload["asset_design"] = {}
    payload["media"] = {"scope:s": {"kind": "scope", "ordinal": 1, "manifest_origin": {"hash": "a" * 64}}}
    with history.reconstruction({}):
        actual = bind(payload)
        scopes = list(actual["media"].values())

        def verify(current, _scopes):
            calls.append(copy.deepcopy(current))

        history.scope_set(actual, scopes, verify)
        history.commit(actual)
        history.scope_set(actual, scopes, verify)
        assert len(calls) == 1
        for kind in ("operation", "query", "fee", "claim", "image_comparison_fee"):
            key = kind + ":changed"
            actual["media"][key] = {"kind": kind, "id": "changed", "claimed_hash": "a" * 64}
            history.apply("media", {key: actual["media"][key]})
            history.scope_set(actual, scopes, verify)
            history.commit(actual)
            assert key in calls[-1]["media"]
        assert len(calls) == 6
