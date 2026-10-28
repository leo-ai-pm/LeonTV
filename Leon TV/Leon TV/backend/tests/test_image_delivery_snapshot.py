"""Decode successful image bytes once per fresh snapshot, never receipt authority."""
import copy
import json
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from datetime import timedelta

import pytest
from media_ledger_fixture import authorize, image_fee, media_case, reserve
from test_execution_empirical import empirical_env as empirical_env
from test_image_policy import make_query
from test_image_receipts import image_bytes, query_raw

from core.creator.store import CreatorError
from core.execution import image_policy, image_receipts
from core.execution.image_policy import FrozenImageRequest
from core.execution.receipts import timestamp
from core.execution.store import validate_state


def count_opens(monkeypatch):
    original = image_receipts.Image.open
    calls = []

    def tracked(*args, **kwargs):
        calls.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(image_receipts.Image, "open", tracked)
    return calls


def test_repeated_delivery_checks_decode_bytes_once_but_recheck_receipts(monkeypatch):
    request = make_query()
    raw, image = query_raw(request), image_bytes()
    opens = count_opens(monkeypatch)
    original = image_receipts.decode_query
    receipts = []

    def counted(*args):
        receipts.append(True)
        return original(*args)

    monkeypatch.setattr(image_receipts, "decode_query", counted)
    with image_policy._descriptor_snapshot():
        first = image_receipts.verify_delivery(request, raw, image)
        for _ in range(3):
            assert image_receipts.verify_delivery(request, copy.deepcopy(raw), bytes(image)) == first
        assert len(opens) == 2, "one snapshot repeatedly decoded identical valid image bytes"
        assert len(receipts) == 4


def test_native_history_rechecks_delivery_once_per_fresh_snapshot(empirical_env, monkeypatch):
    case = media_case(empirical_env)
    _, grant = authorize(case)
    operation = reserve(case, grant)
    submitted = case.media.submit(case.task["id"], operation["id"])["operation"]
    image_fee(case, submitted)
    query = case.media.query(case.task["id"], operation["id"], key="delivery-proof-query")["query"]
    assert query["status"] == "completed"
    case.media.record_delivery(case.task["id"], operation["id"], query["id"], image_bytes())
    # Later native grant frames retain the exact historical delivery and force
    # it through real media-ledger validation, including the final payload pass.
    quote = next(v for v in case.media.store.read_only()["media"].values() if v["kind"] == "quote")
    for index in range(2):
        case.media.grant(case.task["id"], quote["id"], disclosure_hash=quote["disclosure_hash"],
            consent=True, key=f"delivery-proof-grant-{index}")
    state = json.loads(case.media.store.path("state.json").read_text())
    saved = case.media.store.path("state.json").read_bytes()
    opens = count_opens(monkeypatch)
    for _ in range(2):
        opens.clear()
        validate_state(state)
        assert len(opens) == 2
    assert case.media.store.path("state.json").read_bytes() == saved


def test_new_snapshot_and_standalone_calls_decode_again(monkeypatch):
    request = make_query()
    raw, image = query_raw(request), image_bytes()
    opens = count_opens(monkeypatch)
    for _ in range(2):
        with image_policy._descriptor_snapshot():
            image_receipts.verify_delivery(request, raw, image)
            image_receipts.verify_delivery(request, raw, image)
    assert len(opens) == 4
    image_receipts.verify_delivery(request, raw, image)
    image_receipts.verify_delivery(request, raw, image)
    assert len(opens) == 8 and image_receipts._DELIVERY_PROOFS.get() is None


@pytest.mark.parametrize("mutation", ["wrong_task", "late_response", "failed", "bad_descriptor", "bad_type"])
def test_cached_image_never_substitutes_for_fresh_receipt_and_descriptor(mutation, monkeypatch):
    request = make_query()
    raw, image = query_raw(request), image_bytes()
    opens = count_opens(monkeypatch)
    with image_policy._descriptor_snapshot():
        image_receipts.verify_delivery(request, raw, image)
        if mutation == "wrong_task":
            raw = query_raw(request, task="another-task")
        elif mutation == "late_response":
            raw["received_at"] = (timestamp(request.frozen()["permit"]["deadline_at"]) + timedelta(seconds=1)).isoformat()
        elif mutation == "failed":
            raw = query_raw(request, status="failed")
        elif mutation == "bad_descriptor":
            value = request.frozen()
            value["wire"] = "forged-wire"
            request = FrozenImageRequest(json.dumps(value))
        else:
            request = request.frozen()
        with pytest.raises(CreatorError) as error:
            image_receipts.verify_delivery(request, raw, image)
        assert error.value.code == "IMAGE_DELIVERY_INVALID" and len(opens) == 2


def test_new_valid_receipt_gets_its_own_identity_even_when_image_proof_is_reused(monkeypatch):
    request = make_query()
    raw, image = query_raw(request), image_bytes()
    changed = query_raw(request, result={"images": [{"url": ["https://media.example/another.png"]}]})
    opens = count_opens(monkeypatch)
    with image_policy._descriptor_snapshot():
        first = image_receipts.verify_delivery(request, raw, image)
        second = image_receipts.verify_delivery(request, changed, image)
        for field in ("query_raw_hash", "query_receipt_hash", "delivery_hash"):
            assert first[field] != second[field]
        assert first["delivery_sha256"] == second["delivery_sha256"] and len(opens) == 2


@pytest.mark.parametrize("kind", ["bytearray", "memoryview", "subclass"])
def test_mutable_or_nonexact_bytes_cannot_borrow_a_valid_proof(kind):
    class ImageBytes(bytes):
        pass
    request = make_query()
    raw, image = query_raw(request), image_bytes()
    changed = {"bytearray": bytearray, "memoryview": memoryview, "subclass": ImageBytes}[kind](image)
    with image_policy._descriptor_snapshot():
        image_receipts.verify_delivery(request, raw, image)
        with pytest.raises(CreatorError):
            image_receipts.verify_delivery(request, raw, changed)


@pytest.mark.parametrize("changed", [b"not-an-image", b"", image_bytes((90, 159)), image_bytes(format_name="GIF")])
def test_different_invalid_bytes_are_rechecked_and_failures_are_not_retained(changed, monkeypatch):
    request = make_query()
    raw, image = query_raw(request), image_bytes()
    opens = count_opens(monkeypatch)
    with image_policy._descriptor_snapshot():
        image_receipts.verify_delivery(request, raw, image)
        for _ in range(2):
            with pytest.raises(CreatorError):
                image_receipts.verify_delivery(request, raw, changed)
        assert len(image_receipts._DELIVERY_PROOFS.get()["inputs"]) == 1
        assert len(opens) == (2 if changed == b"" else 4)


def test_delivery_result_mutation_does_not_corrupt_immutable_shape_proof():
    request = make_query()
    raw, image = query_raw(request), image_bytes()
    with image_policy._descriptor_snapshot():
        first = image_receipts.verify_delivery(request, raw, image)
        first.update(width=1, format="GIF", delivery_hash="0" * 64)
        second = image_receipts.verify_delivery(request, raw, image)
        assert second["width"] == 90 and second["format"] == "PNG"
        assert second["delivery_hash"] != first["delivery_hash"]
        assert all(type(shape) is tuple for shape in image_receipts._DELIVERY_PROOFS.get()["inputs"].values())


@pytest.mark.parametrize("bound", ["entries", "bytes"])
def test_shape_proof_has_capacity_bounds_and_rechecks_unstored_bytes(bound, monkeypatch):
    request = make_query()
    raw, first, second = query_raw(request), image_bytes(), image_bytes((180, 320))
    if bound == "entries":
        monkeypatch.setattr(image_receipts, "_MAX_DELIVERY_PROOFS", 1)
    else:
        monkeypatch.setattr(image_receipts, "_MAX_DELIVERY_PROOF_BYTES", len(first))
    opens = count_opens(monkeypatch)
    with image_policy._descriptor_snapshot():
        for image in (first, second, second, first):
            image_receipts.verify_delivery(request, raw, image)
        facts = image_receipts._DELIVERY_PROOFS.get()
        assert facts["bytes"] == len(first) and len(facts["inputs"]) == 1 and len(opens) == 6


@pytest.mark.parametrize("limit", ["MAX_IMAGE_BYTES", "MAX_IMAGE_DIMENSION", "MAX_IMAGE_PIXELS", "PIL_WARNING", "PIL_ERROR"])
def test_current_size_and_shape_limits_are_not_bypassed_by_cached_bytes(limit, monkeypatch):
    request = make_query()
    raw, image = query_raw(request), image_bytes()
    with image_policy._descriptor_snapshot():
        image_receipts.verify_delivery(request, raw, image)
        if limit.startswith("PIL_"):
            # The real Pillow warning is promoted to an error; its hard
            # decompression-bomb limit must also invalidate the prior proof.
            monkeypatch.setattr(image_receipts.Image, "MAX_IMAGE_PIXELS", 10000 if limit == "PIL_WARNING" else 7000)
        else:
            monkeypatch.setattr(image_receipts, limit, 1)
        with pytest.raises(CreatorError):
            image_receipts.verify_delivery(request, raw, image)


def test_pillow_truncation_policy_change_requires_a_fresh_decode(monkeypatch):
    request = make_query()
    # A JPEG without its EOI marker is accepted only by Pillow's optional
    # tolerant loader. Restoring strict mode must reject those identical bytes.
    raw, image = query_raw(request), image_bytes(format_name="JPEG")[:-2]
    opens = count_opens(monkeypatch)
    with image_policy._descriptor_snapshot():
        monkeypatch.setattr(image_receipts.ImageFile, "LOAD_TRUNCATED_IMAGES", True)
        image_receipts.verify_delivery(request, raw, image)
        monkeypatch.setattr(image_receipts.ImageFile, "LOAD_TRUNCATED_IMAGES", False)
        with pytest.raises(CreatorError):
            image_receipts.verify_delivery(request, raw, image)
        assert len(opens) == 4


def test_nested_snapshot_restores_outer_proofs_after_error(monkeypatch):
    request = make_query()
    raw, image = query_raw(request), image_bytes()
    opens = count_opens(monkeypatch)
    with image_policy._descriptor_snapshot():
        image_receipts.verify_delivery(request, raw, image)
        outer = image_receipts._DELIVERY_PROOFS.get()
        with pytest.raises(RuntimeError), image_policy._descriptor_snapshot():
            image_receipts.verify_delivery(request, raw, image)
            inner = image_receipts._DELIVERY_PROOFS.get()
            raise RuntimeError("nested exit")
        assert not inner["active"] and not inner["inputs"] and inner["bytes"] == 0
        assert image_receipts._DELIVERY_PROOFS.get() is outer
        image_receipts.verify_delivery(request, raw, image)
        assert len(opens) == 4
    assert not outer["active"] and not outer["inputs"] and outer["bytes"] == 0


def test_copied_context_cannot_reuse_proofs_after_snapshot_exit(monkeypatch):
    request = make_query()
    raw, image = query_raw(request), image_bytes()
    opens = count_opens(monkeypatch)
    with image_policy._descriptor_snapshot():
        image_receipts.verify_delivery(request, raw, image)
        copied = copy_context()
    copied.run(image_receipts.verify_delivery, request, raw, image)
    copied.run(image_receipts.verify_delivery, request, raw, image)
    assert len(opens) == 6


def test_another_thread_cannot_borrow_active_proof(monkeypatch):
    request = make_query()
    raw, image = query_raw(request), image_bytes()
    opens = count_opens(monkeypatch)
    with image_policy._descriptor_snapshot():
        image_receipts.verify_delivery(request, raw, image)
        copied = copy_context()
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(copied.run, image_receipts.verify_delivery, request, raw, image).result()
            pool.submit(copied.run, image_receipts.verify_delivery, request, raw, image).result()
        image_receipts.verify_delivery(request, raw, image)
        assert len(opens) == 6
