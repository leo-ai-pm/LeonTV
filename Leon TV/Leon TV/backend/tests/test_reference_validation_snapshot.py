"""Reference evidence reuse stays within one fresh synchronous journal read."""
import copy
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context

import pytest
from test_reference_derivation import derive_case

from core.creator.store import CreatorError, digest
from core.execution import image_policy, reference_derivation


def case():
    args = derive_case()
    refs, proofs = reference_derivation.derive_references(**args)
    return args, refs, proofs


def counted_source(monkeypatch):
    original = reference_derivation._source
    calls = []

    def source(value):
        calls.append(value["delivery_id"])
        return original(value)

    monkeypatch.setattr(reference_derivation, "_source", source)
    return calls


def seal_proof(proof):
    proof["derivation_hash"] = digest({key: value for key, value in proof.items() if key != "derivation_hash"})


def test_complete_reference_proof_is_reconstructed_once_per_fresh_snapshot(monkeypatch):
    args, refs, proofs = case()
    calls = counted_source(monkeypatch)
    for _ in range(2):
        calls.clear()
        with image_policy._descriptor_snapshot():
            first = reference_derivation.references_from_proofs(**args, proofs=proofs)
            assert first == refs
            first[0]["sha256"] = "0" * 64
            assert reference_derivation.references_from_proofs(**copy.deepcopy(args), proofs=copy.deepcopy(proofs)) == refs
            assert calls == [args["sources"][0]["delivery_id"]]
        assert reference_derivation.references_from_proofs(**args, proofs=proofs) == refs
        assert len(calls) == 2


@pytest.mark.parametrize("change", ["profile", "source_id", "source_bytes", "target_job", "policy", "scope_id", "task_id", "operation_id", "proof_output", "proof_version"])
def test_rehashed_different_complete_input_cannot_borrow_a_success(change):
    args, _, proofs = case()
    with image_policy._descriptor_snapshot():
        reference_derivation.references_from_proofs(**args, proofs=proofs)
        bad, changed_proofs = copy.deepcopy(args), copy.deepcopy(proofs)
        if change == "profile":
            bad["profile"]["allow_lossy_jpeg"] = True
            bad["profile"]["profile_hash"] = digest({k: v for k, v in bad["profile"].items() if k != "profile_hash"})
        elif change == "source_id":
            bad["sources"][0]["operation_id"] = "a" * 32
        elif change == "source_bytes":
            bad["sources"][0]["bytes_base64"] = "YWJjZA=="
        elif change == "target_job":
            bad["target_job"]["prompt"] += " altered"
        elif change == "policy":
            bad["policy"]["max_request_bytes"] = 128
        elif change in {"scope_id", "task_id", "operation_id"}:
            bad[change] = "a" * 32
        elif change == "proof_output":
            changed_proofs[0]["output_sha256"] = "0" * 64
            seal_proof(changed_proofs[0])
        else:
            changed_proofs[0]["source_first_frame_version"] = 1.0
            seal_proof(changed_proofs[0])
        with pytest.raises(CreatorError):
            reference_derivation.references_from_proofs(**bad, proofs=changed_proofs)


def test_unused_policy_fields_still_make_a_distinct_full_input(monkeypatch):
    args, refs, proofs = case()
    calls = counted_source(monkeypatch)
    with image_policy._descriptor_snapshot():
        assert reference_derivation.references_from_proofs(**args, proofs=proofs) == refs
        changed = copy.deepcopy(args)
        changed["policy"]["version"] += "-changed"
        assert reference_derivation.references_from_proofs(**changed, proofs=proofs) == refs
        assert len(calls) == 2


def test_valid_legacy_tuple_source_is_not_normalized_into_a_cached_list(monkeypatch):
    args, refs, proofs = case()
    calls = counted_source(monkeypatch)
    with image_policy._descriptor_snapshot():
        assert reference_derivation.references_from_proofs(**args, proofs=proofs) == refs
        changed = dict(args, sources=tuple(args["sources"]))
        # Preserve the original helper's behavior, but never use list proof for
        # a non-strict-JSON input or admit that input to the cache.
        assert reference_derivation.references_from_proofs(**changed, proofs=proofs) == refs
        assert len(calls) == 2
        assert len(reference_derivation._REFERENCE_PROOFS.get()["inputs"]) == 1


@pytest.mark.parametrize("reencode", [1, 0, "", "false", None, []])
def test_reencode_is_a_strict_boolean_even_after_success(reencode):
    args, _, proofs = case()
    with image_policy._descriptor_snapshot():
        reference_derivation.references_from_proofs(**args, proofs=proofs)
        with pytest.raises(CreatorError):
            reference_derivation.references_from_proofs(**args, proofs=proofs, reencode=reencode)


def test_reencode_and_actual_encoder_identity_are_part_of_the_key(monkeypatch):
    args, refs, proofs = case()
    calls = counted_source(monkeypatch)
    with image_policy._descriptor_snapshot():
        assert reference_derivation.references_from_proofs(**args, proofs=proofs) == refs
        assert reference_derivation.references_from_proofs(**args, proofs=proofs, reencode=True) == refs
        calls_after_reencode = len(calls)
        assert calls_after_reencode > 1
        assert reference_derivation.references_from_proofs(**args, proofs=proofs, reencode=True) == refs
        assert len(calls) == calls_after_reencode
        old = reference_derivation.encoder_identity()
        monkeypatch.setattr(reference_derivation, "encoder_identity", lambda: dict(old, pillow="changed-version"))
        with pytest.raises(CreatorError) as error:
            reference_derivation.references_from_proofs(**args, proofs=proofs, reencode=True)
        assert error.value.code == "MEDIA_REFERENCE_ENCODER_MISMATCH"
        # Historical bytes remain verifiable without requiring their reencode.
        before = len(calls)
        assert reference_derivation.references_from_proofs(**args, proofs=proofs) == refs
        assert len(calls) == before + 1


def test_failed_validation_does_not_enter_success_table(monkeypatch):
    args, _, proofs = case()
    bad = copy.deepcopy(proofs)
    bad[0]["output_sha256"] = "0" * 64
    seal_proof(bad[0])
    calls = counted_source(monkeypatch)
    with image_policy._descriptor_snapshot():
        for _ in range(2):
            with pytest.raises(CreatorError):
                reference_derivation.references_from_proofs(**args, proofs=bad)
        assert len(calls) == 2
        assert reference_derivation._REFERENCE_PROOFS.get()["inputs"] == {}


def test_nested_snapshot_has_independent_facts_and_restores_parent_on_error(monkeypatch):
    args, refs, proofs = case()
    calls = counted_source(monkeypatch)
    with image_policy._descriptor_snapshot():
        reference_derivation.references_from_proofs(**args, proofs=proofs)
        outer = reference_derivation._REFERENCE_PROOFS.get()
        with pytest.raises(RuntimeError):
            with image_policy._descriptor_snapshot():
                inner = reference_derivation._REFERENCE_PROOFS.get()
                assert inner is not outer
                assert reference_derivation.references_from_proofs(**args, proofs=proofs) == refs
                raise RuntimeError("synthetic scope failure")
        assert inner["active"] is False and inner["inputs"] == {} and inner["bytes"] == 0
        assert reference_derivation._REFERENCE_PROOFS.get() is outer
        reference_derivation.references_from_proofs(**args, proofs=proofs)
        assert len(calls) == 2
    assert outer["active"] is False and outer["inputs"] == {} and outer["bytes"] == 0
    assert reference_derivation._REFERENCE_PROOFS.get() is None


def test_copied_context_cannot_keep_a_finished_snapshot_alive(monkeypatch):
    args, refs, proofs = case()
    calls = counted_source(monkeypatch)
    with image_policy._descriptor_snapshot():
        reference_derivation.references_from_proofs(**args, proofs=proofs)
        copied = copy_context()
    assert copied.run(reference_derivation.references_from_proofs, **args, proofs=proofs) == refs
    assert len(calls) == 2


def test_another_thread_even_with_copied_context_cannot_borrow_proofs(monkeypatch):
    args, refs, proofs = case()
    calls = counted_source(monkeypatch)
    with image_policy._descriptor_snapshot():
        reference_derivation.references_from_proofs(**args, proofs=proofs)
        copied = copy_context()
        with ThreadPoolExecutor(max_workers=1) as pool:
            assert pool.submit(copied.run, reference_derivation.references_from_proofs, **args, proofs=proofs).result() == refs
        assert len(calls) == 2
        reference_derivation.references_from_proofs(**args, proofs=proofs)
        assert len(calls) == 2


def test_entry_limit_runs_uncached_validation_after_table_is_full(monkeypatch):
    monkeypatch.setattr(reference_derivation, "_MAX_REFERENCE_PROOFS", 2)
    cases = []
    for index in range(3):
        args = derive_case()
        args["operation_id"] = f"{index + 100:032x}"
        refs, proofs = reference_derivation.derive_references(**args)
        cases.append((args, refs, proofs))
    calls = counted_source(monkeypatch)
    with image_policy._descriptor_snapshot():
        for args, refs, proofs in cases:
            assert reference_derivation.references_from_proofs(**args, proofs=proofs) == refs
        args, refs, proofs = cases[-1]
        assert reference_derivation.references_from_proofs(**args, proofs=proofs) == refs
        facts = reference_derivation._REFERENCE_PROOFS.get()
        assert len(facts["inputs"]) == 2 and len(calls) == 4


def test_aggregate_key_and_result_bytes_are_bounded(monkeypatch):
    cases = []
    for index in range(3):
        args = derive_case()
        args["operation_id"] = f"{index + 200:032x}"
        refs, proofs = reference_derivation.derive_references(**args)
        cases.append((args, refs, proofs))
    with image_policy._descriptor_snapshot():
        args, _, proofs = cases[0]
        reference_derivation.references_from_proofs(**args, proofs=proofs)
        single_size = reference_derivation._REFERENCE_PROOFS.get()["bytes"]
    monkeypatch.setattr(reference_derivation, "_MAX_REFERENCE_PROOF_BYTES", single_size * 2)
    calls = counted_source(monkeypatch)
    with image_policy._descriptor_snapshot():
        for args, refs, proofs in cases:
            assert reference_derivation.references_from_proofs(**args, proofs=proofs) == refs
        facts = reference_derivation._REFERENCE_PROOFS.get()
        assert facts["bytes"] <= single_size * 2 and len(facts["inputs"]) == 2
        args, refs, proofs = cases[-1]
        assert reference_derivation.references_from_proofs(**args, proofs=proofs) == refs
        assert len(calls) == 4


def test_oversized_or_disabled_capacity_never_skips_original_validation(monkeypatch):
    args, refs, proofs = case()
    calls = counted_source(monkeypatch)
    monkeypatch.setattr(reference_derivation, "_MAX_REFERENCE_PROOF_BYTES", 1)
    with image_policy._descriptor_snapshot():
        for _ in range(2):
            assert reference_derivation.references_from_proofs(**args, proofs=proofs) == refs
        assert len(calls) == 2 and reference_derivation._REFERENCE_PROOFS.get()["bytes"] == 0
