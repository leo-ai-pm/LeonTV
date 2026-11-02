"""Pure proof reuse must bind complete current inputs and end with the read."""
import copy
import threading
from contextvars import copy_context

import pytest
from media_ledger_fixture import media_case
from test_execution_empirical import empirical_env as empirical_env

from core.creator.store import CreatorError
from core.execution import (
    asset_handoff,
    image_code_continuation,
    image_policy,
    media_ledger,
    validation_read_proofs,
)


def test_historical_context_reconstructs_once_and_changed_core_cannot_borrow_proof(empirical_env, monkeypatch):
    case = media_case(empirical_env)
    payload = case.media.store.read_only()
    task_id = case.task["id"]
    expected = media_ledger.native_context(payload, task_id, historical=True)
    original, calls = asset_handoff._prepare, []

    def prepare(*args):
        calls.append(True)
        return original(*args)

    monkeypatch.setattr(asset_handoff, "_prepare", prepare)
    with image_policy._descriptor_snapshot():
        first = media_ledger.native_context(payload, task_id, historical=True)
        first[0]["binding"]["manifest_version"] = 999
        assert media_ledger.native_context(payload, task_id, historical=True) == expected
        assert len(calls) == 1, "same complete CORE repeatedly rebuilt accepted history"
        assert media_ledger.native_context(payload, task_id, historical=False) == expected
        assert len(calls) == 2
        with pytest.raises(CreatorError) as missing:
            media_ledger.native_context(payload, "0" * 32, historical=True)
        assert (missing.value.code, missing.value.status) == ("NOT_FOUND", 404)
        bad = copy.deepcopy(payload)
        bad["tasks"][task_id]["plan"]["duration_ms"] = 1
        for _ in range(2):
            with pytest.raises(CreatorError):
                media_ledger.native_context(bad, task_id, historical=True)
        assert len(calls) == 5
        assert media_ledger.native_context(payload, task_id, historical=True) == expected
    assert media_ledger.native_context(payload, task_id, historical=True) == expected
    assert len(calls) == 6


def test_manifest_schema_rechecks_changed_content_and_required_files(monkeypatch):
    module = image_code_continuation
    manifest = {name: "a" * 64 for name in sorted(set(module.REQUIRED_CODE_FILES) | module.EXTRA_CODE)}
    original, calls = module._hash, []
    original_extra = module.EXTRA_CODE

    def checked(value):
        calls.append(True)
        return original(value)

    monkeypatch.setattr(module, "_hash", checked)
    with image_policy._descriptor_snapshot():
        assert module._manifest(manifest) is manifest
        assert module._manifest(manifest) is manifest
        assert len(calls) == len(manifest), "same immutable manifest schema was checked again"
        changed = dict(manifest)
        changed[next(iter(changed))] = "z" * 64
        for _ in range(2):
            with pytest.raises(CreatorError):
                module._manifest(changed)
        monkeypatch.setattr(module, "EXTRA_CODE", module.EXTRA_CODE | {"core/execution/required-new.py"})
        with pytest.raises(CreatorError):
            module._manifest(manifest)
        monkeypatch.setattr(module, "EXTRA_CODE", original_extra)
        monkeypatch.setattr(module, "REQUIRED_CODE_FILES", (*module.REQUIRED_CODE_FILES, "api/new-required.py"))
        with pytest.raises(CreatorError):
            module._manifest(manifest)


def test_complete_typed_keys_defensive_outputs_and_failed_proofs():
    calls = []

    def compute(value):
        calls.append(True)
        if "bad" in value:
            raise ValueError("synthetic rejected computation")
        return {"copy": copy.deepcopy(value)}

    with image_policy._descriptor_snapshot():
        for value in ({"n": True}, {"n": 1}, {"n": 1.0}, {"n": 0.0}, {"n": -0.0}):
            first = validation_read_proofs.reconstructed("historical_context", value, compute)
            first["copy"].clear()
            assert validation_read_proofs.reconstructed("historical_context", value, compute) == {"copy": value}
        assert len(calls) == 5
        for _ in range(2):
            with pytest.raises(ValueError):
                validation_read_proofs.reconstructed("historical_context", {"bad": True}, compute)
        assert len(calls) == 7


def test_proof_thread_nested_failure_and_copied_context_lifetimes():
    calls = []

    def read():
        def compute(_value):
            calls.append(True)
            return {"ok": True}
        return validation_read_proofs.reconstructed("historical_context", {"complete": [1, 2]}, compute)

    with image_policy._descriptor_snapshot():
        read()
        result = []
        copied = copy_context()
        worker = threading.Thread(target=lambda: result.append(copied.run(read)))
        worker.start()
        worker.join()
        assert result == [{"ok": True}] and len(calls) == 2
        with pytest.raises(RuntimeError), image_policy._descriptor_snapshot():
            read()
            raise RuntimeError("synthetic nested exit")
        read()
        assert len(calls) == 3
        copied = copy_context()
    copied.run(read)
    assert len(calls) == 4


@pytest.mark.parametrize("bound", ["entries", "bytes", "full"])
def test_proof_capacity_falls_back_to_real_computation(monkeypatch, bound):
    if bound == "entries":
        monkeypatch.setattr(image_policy, "_MAX_SNAPSHOT_PROOFS", 0)
    elif bound == "bytes":
        monkeypatch.setattr(validation_read_proofs, "_MAX_BYTES", 1)
    else:
        monkeypatch.setattr(image_policy, "_MAX_SNAPSHOT_PROOFS", 1)
    calls = []
    with image_policy._descriptor_snapshot():
        for value in ([1, 2, 2] if bound == "full" else [1, 1]):
            validation_read_proofs.reconstructed("manifest_schema", {"value": value}, lambda actual: calls.append(True))
    assert len(calls) == (3 if bound == "full" else 2)


@pytest.mark.parametrize("kind", ["tuple", "custom", "deep"])
def test_non_json_and_deep_inputs_preserve_original_fallback(kind):
    value = {"value": (1, 2)} if kind == "tuple" else {"value": object()}
    if kind == "deep":
        value = 1
        for _ in range(70):
            value = [value]
    calls = []

    def original(_value):
        calls.append(True)
        raise ValueError("same original rejection")

    with image_policy._descriptor_snapshot():
        for _ in range(2):
            with pytest.raises(ValueError, match="same original rejection"):
                validation_read_proofs.reconstructed("historical_context", value, original)
    assert len(calls) == 2


def test_proof_computation_uses_the_same_frozen_input_as_its_key(monkeypatch):
    value = {"n": 1}
    original = validation_read_proofs._key

    def changed_after_capture(item, depth=0, _budget=None):
        marker = original(item, depth, _budget)
        if depth == 0:
            value["n"] = 2
        return marker

    monkeypatch.setattr(validation_read_proofs, "_key", changed_after_capture)

    def compute(*captured):
        return copy.deepcopy(captured[0] if captured else value)

    with image_policy._descriptor_snapshot():
        assert validation_read_proofs.reconstructed("historical_context", value, compute) == {"n": 1}
        value["n"] = 1
        assert validation_read_proofs.reconstructed("historical_context", value, compute) == {"n": 1}
