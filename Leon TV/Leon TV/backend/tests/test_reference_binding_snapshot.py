"""Exact reference-source bindings across callers of one native read."""
import copy
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context

import pytest
from PIL import Image, ImageFile
from test_reference_derivation import derive_case

from core.creator.store import CreatorError, digest
from core.execution import image_continuation_policy as binding
from core.execution import image_policy, reference_derivation


def case():
    arguments = derive_case()
    references, _ = reference_derivation.derive_references(**arguments)
    return arguments["sources"], references


def test_source_binding_reuses_full_original_and_transformed_reference_inputs_within_read(monkeypatch):
    sources, references = case()
    original, calls = reference_derivation._source, []

    def source(value):
        calls.append(value["delivery_id"])
        return original(value)

    monkeypatch.setattr(reference_derivation, "_source", source)
    for _ in range(2):
        calls.clear()
        with image_policy._descriptor_snapshot():
            expected = binding.bind_reference_sources(sources, references)
            for _caller in range(3):
                actual = binding.bind_reference_sources(copy.deepcopy(sources), copy.deepcopy(references))
                assert actual == expected
                actual[0]["source_operation_id"] = "f" * 32
            assert len(calls) == 1, "same complete source/reference pair decoded again at later callers"
        assert binding.bind_reference_sources(sources, references) == expected
        assert len(calls) == 2


@pytest.mark.parametrize("change", ["bytes", "delivery_hash", "rehashed_dimensions", "source_version", "reference_version", "reference_hash", "tuple"])
def test_same_ids_and_claimed_hashes_cannot_replace_changed_complete_inputs(change):
    sources, references = case()
    with image_policy._descriptor_snapshot():
        binding.bind_reference_sources(sources, references)
        changed, refs = copy.deepcopy(sources), copy.deepcopy(references)
        if change == "bytes":
            changed[0]["bytes_base64"] = "YWJjZA=="
        elif change == "delivery_hash":
            changed[0]["delivery"]["delivery_hash"] = "0" * 64
        elif change == "rehashed_dimensions":
            delivery = changed[0]["delivery"]
            delivery["width"] += 1
            delivery["delivery_hash"] = digest({k: v for k, v in delivery.items() if k != "delivery_hash"})
        elif change == "source_version":
            changed[0]["first_frame_version"] = True
        elif change == "reference_version":
            refs[0]["first_frame_version"] = 1.0
        elif change == "reference_hash":
            refs[0]["sha256"] = "0" * 64
        else:
            changed = tuple(changed)
        for _ in range(2):
            with pytest.raises(CreatorError):
                binding.bind_reference_sources(changed, refs)


def test_complete_source_identity_change_recomputes_output_without_changing_old_proof(monkeypatch):
    sources, references = case()
    original, calls = reference_derivation._source, []

    def source(value):
        calls.append(value["operation_id"])
        return original(value)

    monkeypatch.setattr(reference_derivation, "_source", source)
    with image_policy._descriptor_snapshot():
        first = binding.bind_reference_sources(sources, references)
        changed = copy.deepcopy(sources)
        changed[0]["operation_id"] = "a" * 32
        second = binding.bind_reference_sources(changed, references)
        assert second[0]["source_operation_id"] == "a" * 32 and second != first
        assert binding.bind_reference_sources(sources, references) == first
        assert len(calls) == 2


@pytest.mark.parametrize("setting", ["source_bytes", "source_pixels", "reference_bytes", "pillow_pixels"])
def test_current_decoder_and_size_limits_are_rechecked(setting, monkeypatch):
    sources, references = case()
    with image_policy._descriptor_snapshot():
        binding.bind_reference_sources(sources, references)
        module, attribute = {
            "source_bytes": (reference_derivation, "MAX_SOURCE_BYTES"),
            "source_pixels": (reference_derivation, "MAX_PIXELS"),
            "reference_bytes": (image_policy, "MAX_BYTES"),
            "pillow_pixels": (Image, "MAX_IMAGE_PIXELS"),
        }[setting]
        monkeypatch.setattr(module, attribute, 1)
        with pytest.raises(CreatorError):
            binding.bind_reference_sources(sources, references)


def test_codec_configuration_and_encoder_identity_change_force_recomputation(monkeypatch):
    sources, references = case()
    original, calls = reference_derivation._source, []

    def source(value):
        calls.append(True)
        return original(value)

    monkeypatch.setattr(reference_derivation, "_source", source)
    with image_policy._descriptor_snapshot():
        expected = binding.bind_reference_sources(sources, references)
        monkeypatch.setattr(ImageFile, "LOAD_TRUNCATED_IMAGES", not ImageFile.LOAD_TRUNCATED_IMAGES)
        assert binding.bind_reference_sources(sources, references) == expected
        encoder = reference_derivation.encoder_identity()
        monkeypatch.setattr(reference_derivation, "encoder_identity", lambda: dict(encoder, pillow="changed-codec"))
        assert binding.bind_reference_sources(sources, references) == expected
        assert len(calls) == 3


@pytest.mark.parametrize("bound", ["entries", "bytes"])
def test_full_existing_reference_pool_preserves_original_fallback(bound, monkeypatch):
    sources, references = case()
    calls, original = [], reference_derivation._source
    monkeypatch.setattr(reference_derivation, "_MAX_REFERENCE_PROOFS" if bound == "entries" else "_MAX_REFERENCE_PROOF_BYTES", 0)

    def source(value):
        calls.append(True)
        return original(value)

    monkeypatch.setattr(reference_derivation, "_source", source)
    with image_policy._descriptor_snapshot():
        expected = binding.bind_reference_sources(sources, references)
        assert binding.bind_reference_sources(sources, references) == expected
        assert len(calls) == 2


def test_failed_reconstruction_never_seeds_success_and_read_exit_clears_shared_pool():
    sources, references = case()
    bad = copy.deepcopy(references)
    bad[0]["sha256"] = "0" * 64
    with image_policy._descriptor_snapshot():
        pool = reference_derivation._REFERENCE_PROOFS.get()
        for _ in range(2):
            with pytest.raises(CreatorError):
                binding.bind_reference_sources(sources, bad)
        assert not pool["inputs"]
        binding.bind_reference_sources(sources, references)
        assert pool["inputs"]
    assert not pool["active"] and not pool["inputs"] and pool["bytes"] == 0


def test_caller_mutation_after_capture_cannot_poison_exact_proof(monkeypatch):
    sources, references = case()
    expected = binding.bind_reference_sources(sources, references)
    capture = reference_derivation._reference_proof_input
    changed = []

    def captured(values):
        result = capture(values)
        if result is not None and not changed:
            sources[0]["delivery"]["width"] += 1
            changed.append(True)
        return result

    monkeypatch.setattr(reference_derivation, "_reference_proof_input", captured)
    with image_policy._descriptor_snapshot():
        assert binding.bind_reference_sources(sources, references) == expected
        with pytest.raises(CreatorError):
            binding.bind_reference_sources(sources, references)


def test_nested_failure_threads_and_copied_context_do_not_extend_proof_lifetime(monkeypatch):
    sources, references = case()
    original, calls = reference_derivation._source, []

    def source(value):
        calls.append(True)
        return original(value)

    monkeypatch.setattr(reference_derivation, "_source", source)
    def read():
        return binding.bind_reference_sources(sources, references)
    with image_policy._descriptor_snapshot():
        expected = read()
        with pytest.raises(RuntimeError), image_policy._descriptor_snapshot():
            assert read() == expected
            raise RuntimeError("nested exception")
        assert read() == expected and len(calls) == 2
        copied = copy_context()
        with ThreadPoolExecutor(max_workers=1) as executor:
            assert executor.submit(copied.run, read).result() == expected
        assert len(calls) == 3
    assert copied.run(read) == expected and len(calls) == 4


def test_existing_representation_proofs_and_new_bindings_share_capacity_without_aliasing():
    args = derive_case()
    references, proofs = reference_derivation.derive_references(**args)
    with image_policy._descriptor_snapshot():
        assert reference_derivation.references_from_proofs(**args, proofs=proofs) == references
        output = binding.bind_reference_sources(args["sources"], references)
        assert output[0]["source_delivery_id"] == args["sources"][0]["delivery_id"]
        output[0].clear()
        assert reference_derivation.references_from_proofs(**args, proofs=proofs) == references
        assert binding.bind_reference_sources(args["sources"], references)[0]["source_delivery_id"]


def test_environment_drift_during_success_does_not_admit_a_proof(monkeypatch):
    sources, references = case()
    original = binding._bind_reference_sources

    def changing(*args):
        output = original(*args)
        monkeypatch.setattr(reference_derivation, "MAX_PIXELS", reference_derivation.MAX_PIXELS + 1)
        return output

    monkeypatch.setattr(binding, "_bind_reference_sources", changing)
    with image_policy._descriptor_snapshot():
        assert binding.bind_reference_sources(sources, references)
        assert not reference_derivation._REFERENCE_PROOFS.get()["inputs"]


def test_optional_environment_probe_failure_preserves_original_computation(monkeypatch):
    sources, references = case()
    expected = binding.bind_reference_sources(sources, references)

    def unavailable():
        raise RuntimeError("optional conditions unavailable")

    monkeypatch.setattr(binding, "_binding_conditions", unavailable)
    with image_policy._descriptor_snapshot():
        assert binding.bind_reference_sources(sources, references) == expected
