"""Exact immutable descriptor tokens avoid repeated parsing within one read."""
import copy
import json
import threading
from contextvars import copy_context

import pytest
from test_image_policy import make_submit

from core.execution import image_policy, validation_read_proofs
from core.execution.image_policy import FrozenImageRequest, ImagePolicyError


def checked_calls(monkeypatch):
    original, calls = image_policy._checked, []

    def checked(value):
        calls.append(True)
        return original(value)

    monkeypatch.setattr(image_policy, "_checked", checked)
    return calls


def test_frozen_complete_input_is_parsed_once_per_read_with_independent_outputs(monkeypatch):
    request = make_submit()
    expected = request.frozen()
    calls = checked_calls(monkeypatch)
    for _ in range(2):
        calls.clear()
        with image_policy._descriptor_snapshot():
            one = request.frozen()
            one["manifest"]["frames"].clear()
            assert request.frozen() == expected
            assert len(calls) == 1, "identical immutable descriptor token was parsed again"
    assert request.frozen() == expected and len(calls) == 2


def test_same_seal_and_changed_serialized_input_cannot_borrow_proof():
    request = make_submit()
    changed = copy.deepcopy(request.frozen())
    changed["wire"] = "{}"
    forged = FrozenImageRequest(json.dumps(changed))
    with image_policy._descriptor_snapshot():
        request.frozen()
        for _ in range(2):
            with pytest.raises(ImagePolicyError):
                forged.frozen()


def test_frozen_proof_thread_nested_exception_and_copied_context_boundaries(monkeypatch):
    request = make_submit()
    calls = checked_calls(monkeypatch)
    with image_policy._descriptor_snapshot():
        request.frozen()
        copied = copy_context()
        result = []
        worker = threading.Thread(target=lambda: result.append(copied.run(request.frozen)))
        worker.start()
        worker.join()
        assert len(result) == 1 and len(calls) == 2
        with pytest.raises(RuntimeError), image_policy._descriptor_snapshot():
            request.frozen()
            raise RuntimeError("synthetic exit")
        request.frozen()
        assert len(calls) == 3
        after = copy_context()
    after.run(request.frozen)
    assert len(calls) == 4


def test_frozen_proof_obeys_changed_capacity_and_size_bound(monkeypatch):
    request = make_submit()
    expected = request.frozen()
    calls = checked_calls(monkeypatch)
    with image_policy._descriptor_snapshot():
        request.frozen()
        monkeypatch.setattr(image_policy, "MAX_DESCRIPTOR_BYTES", 1)
        with pytest.raises(ImagePolicyError):
            request.frozen()
    monkeypatch.setattr(image_policy, "MAX_DESCRIPTOR_BYTES", 8 * image_policy.MAX_BYTES)
    monkeypatch.setattr(image_policy, "_MAX_SNAPSHOT_PROOFS", 0)
    with image_policy._descriptor_snapshot():
        assert request.frozen() == expected
        assert request.frozen() == expected
    assert len(calls) == 4


def test_constructor_reuses_exact_typed_input_without_parsing_it_again(monkeypatch):
    value = make_submit().frozen()
    calls = checked_calls(monkeypatch)
    with image_policy._descriptor_snapshot():
        first = FrozenImageRequest.from_frozen(value)
        second = FrozenImageRequest.from_frozen(copy.deepcopy(value))
        assert first == second and first is not second
        assert len(calls) == 1, "same complete constructor input was parsed again"
        bad = copy.deepcopy(value)
        bad["permit"]["first_frame_version"] = True
        with pytest.raises(ImagePolicyError):
            FrozenImageRequest.from_frozen(bad)


def test_constructor_uses_frozen_input_and_rejects_subsequent_caller_mutation(monkeypatch):
    value = make_submit().frozen()
    expected = copy.deepcopy(value)
    original = validation_read_proofs._key

    def captured_then_changed(item, depth=0, _budget=None):
        result = original(item, depth, _budget)
        if depth == 0:
            value["wire"] = "{}"
        return result

    monkeypatch.setattr(validation_read_proofs, "_key", captured_then_changed)
    with image_policy._descriptor_snapshot():
        assert FrozenImageRequest.from_frozen(value).frozen() == expected
        with pytest.raises(ImagePolicyError):
            FrozenImageRequest.from_frozen(value)


def test_constructor_proof_thread_exit_and_node_budget(monkeypatch):
    value = make_submit().frozen()
    calls = checked_calls(monkeypatch)
    with image_policy._descriptor_snapshot():
        FrozenImageRequest.from_frozen(value)
        copied = copy_context()
        result = []
        worker = threading.Thread(target=lambda: result.append(copied.run(FrozenImageRequest.from_frozen, value)))
        worker.start()
        worker.join()
        assert len(result) == 1 and len(calls) == 2
        FrozenImageRequest.from_frozen(value)
        assert len(calls) == 2
        copied = copy_context()
    copied.run(FrozenImageRequest.from_frozen, value)
    assert len(calls) == 3
    monkeypatch.setattr(image_policy, "_MAX_SNAPSHOT_TOKEN_NODES", 1)
    with image_policy._descriptor_snapshot():
        FrozenImageRequest.from_frozen(value)
        FrozenImageRequest.from_frozen(value)
    assert len(calls) == 5
