"""Independent adversarial audit of pure proofs scoped to one journal read."""
import copy
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Lock, current_thread

import pytest
from media_ledger_fixture import authorize, media_case, reserve
from test_execution_empirical import empirical_env as empirical_env
from test_image_policy import make_query, make_submit, reseal

from core.creator.store import CreatorError, digest
from core.execution import image_policy
from core.execution.image_policy import FrozenImageRequest, ImagePolicyError
from core.execution.store import validate_state


def test_same_seal_never_substitutes_for_changed_full_descriptor():
    value = make_submit().frozen()
    changed = copy.deepcopy(value)
    changed["wire"] = "{}"
    with image_policy._descriptor_snapshot():
        FrozenImageRequest.from_frozen(value)
        with pytest.raises(ImagePolicyError):
            FrozenImageRequest.from_frozen(changed)
        with pytest.raises(ImagePolicyError):
            FrozenImageRequest.from_frozen(reseal(changed))


@pytest.mark.parametrize("number", [True, 1.0])
def test_json_number_types_do_not_borrow_cached_integer_proof(number):
    value = make_submit().frozen()
    changed = copy.deepcopy(value)
    changed["permit"]["first_frame_version"] = number
    changed["permit"]["permit_hash"] = digest({key: item for key, item in changed["permit"].items() if key != "permit_hash"})
    with image_policy._descriptor_snapshot():
        FrozenImageRequest.from_frozen(value)
        with pytest.raises(ImagePolicyError):
            FrozenImageRequest.from_frozen(reseal(changed))


def test_non_json_container_rejected_before_any_cache_lookup():
    value = make_submit(2).frozen()
    changed = copy.deepcopy(value)
    changed["references"] = tuple(changed["references"])
    with image_policy._descriptor_snapshot():
        FrozenImageRequest.from_frozen(value)
        with pytest.raises(ImagePolicyError):
            FrozenImageRequest.from_frozen(changed)


def test_get_cannot_reuse_cached_parent_seal_after_parent_tamper():
    submit = make_submit()
    query = make_query(submit).frozen()
    changed = copy.deepcopy(query)
    changed["parent_submit"]["wire"] = "{}"
    with image_policy._descriptor_snapshot():
        FrozenImageRequest.from_frozen(submit.frozen())
        FrozenImageRequest.from_frozen(query)
        with pytest.raises(ImagePolicyError):
            FrozenImageRequest.from_frozen(reseal(changed))


def test_failed_reconstruction_is_not_cached_and_does_not_poison_valid_input(monkeypatch):
    value = make_submit().frozen()
    changed = copy.deepcopy(value)
    changed["wire"] = "{}"
    changed = reseal(changed)
    original = image_policy._submit_data
    calls = []

    def tracked(**kwargs):
        calls.append(True)
        return original(**kwargs)

    monkeypatch.setattr(image_policy, "_submit_data", tracked)
    with image_policy._descriptor_snapshot():
        for _ in range(2):
            with pytest.raises(ImagePolicyError):
                FrozenImageRequest.from_frozen(changed)
        FrozenImageRequest.from_frozen(value)
        FrozenImageRequest.from_frozen(value)
    assert len(calls) == 3


def test_cached_outputs_are_independent_and_nested_failure_restores_outer_scope(monkeypatch):
    value = make_submit().frozen()
    original = image_policy._submit_data
    calls = []

    def tracked(**kwargs):
        calls.append(True)
        return original(**kwargs)

    monkeypatch.setattr(image_policy, "_submit_data", tracked)
    with image_policy._descriptor_snapshot():
        first = image_policy._checked(value)
        first["manifest"]["frames"].clear()
        assert image_policy._checked(value)["manifest"]["frames"]
        with pytest.raises(RuntimeError), image_policy._descriptor_snapshot():
            image_policy._checked(value)
            raise RuntimeError("synthetic scope exit")
        assert image_policy._checked(value) == value
    assert len(calls) == 2
    image_policy._checked(value)
    assert len(calls) == 3


def test_two_overlapping_threads_have_independent_snapshot_proofs(monkeypatch):
    value = make_submit().frozen()
    original = image_policy._submit_data
    barrier = Barrier(2)
    lock = Lock()
    calls = {}

    def tracked(**kwargs):
        with lock:
            key = current_thread().name
            calls[key] = calls.get(key, 0) + 1
        return original(**kwargs)

    def read():
        with image_policy._descriptor_snapshot():
            image_policy._checked(value)
            barrier.wait(timeout=5)
            image_policy._checked(value)

    monkeypatch.setattr(image_policy, "_submit_data", tracked)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(read) for _ in range(2)]
        for future in futures:
            future.result(timeout=10)
    assert sorted(calls.values()) == [1, 1]


def test_later_rehashed_frame_cannot_borrow_an_earlier_descriptor_proof(empirical_env):
    case = media_case(empirical_env)
    _, grant = authorize(case)
    operation = reserve(case, grant)
    state = json.loads(case.media.store.path("state.json").read_text())
    record = copy.deepcopy(case.media.store.read_only()["media"]["operation:" + operation["id"]])
    record["descriptor"]["wire"] = "{}"
    record["descriptor"] = reseal(record["descriptor"])
    frame = {"revision": state["head_revision"] + 1, "saved_at": state["frames"][-1]["saved_at"],
             "previous_hash": state["head_hash"], "delta": {"media": {"operation:" + operation["id"]: record}}}
    frame["hash"] = digest(frame)
    state["frames"].append(frame)
    state["head_revision"] += 1
    state["head_hash"] = frame["hash"]
    with pytest.raises(CreatorError):
        validate_state(state)


def test_hard_cache_count_cap_preserves_validation_when_reuse_is_full(monkeypatch):
    value = make_submit().frozen()
    original = image_policy._submit_data
    calls = []

    def tracked(**kwargs):
        calls.append(True)
        return original(**kwargs)

    monkeypatch.setattr(image_policy, "_submit_data", tracked)
    monkeypatch.setattr(image_policy, "_MAX_SNAPSHOT_PROOFS", 0)
    with image_policy._descriptor_snapshot():
        image_policy._checked(value)
        image_policy._checked(value)
    assert len(calls) == 2
