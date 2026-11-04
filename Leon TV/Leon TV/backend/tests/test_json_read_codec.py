"""Actual digest/json-copy callers must preserve bytes and JSON copy semantics."""
import hashlib
import json
import math
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context

import pytest

from core.creator.store import digest
from core.execution import json_read_codec as codec
from core.execution.image_policy import _descriptor_snapshot
from core.planner import review
from core.planner.protocol import parse_json
from core.planner.review import json_copy


def test_digest_and_copy_escape_identical_long_strings_once_per_fresh_read(monkeypatch):
    text = ('scene\\prompt\n中文😀\t"quoted"' * 512)
    shared = {"text": text}
    value = {"first": shared, "second": shared, "raw": text}
    expected_digest = hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
    expected_copy = json_copy(value)
    original = json.encoder.encode_basestring
    calls = []

    def encoded(current):
        if current == text:
            calls.append(True)
        return original(current)

    monkeypatch.setattr(json.encoder, "encode_basestring", encoded)
    for _ in range(2):
        calls.clear()
        with _descriptor_snapshot():
            for _caller in range(2):
                assert digest(value) == expected_digest
                output = json_copy(value)
                assert output == expected_copy
                assert output["first"] is not output["second"]
            assert len(calls) == 1, "digest/copy repeatedly escaped the same immutable long scalar"


def original_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def original_copy(value, limit=review.MAX_INPUT_BYTES):
    review.require(review.json_value(value))
    data = json.dumps(value, ensure_ascii=False, allow_nan=False)
    review.require(len(data.encode("utf-8")) <= limit)
    return parse_json(data)


def outcome(call):
    try:
        value = call()
        return "ok", json.dumps(value, ensure_ascii=True, allow_nan=True)
    except Exception as error:
        return "error", type(error), getattr(error, "code", None)


def test_stdlib_bytes_default_spaces_unicode_escapes_numbers_and_modes_are_exact():
    values = [None, True, 1, 1.0, -0.0, 0.0, 1e-300, 1e100, float("nan"), float("inf"), -float("inf"),
              "中文😀\\\n\t\"/\u2028", {"z": [False, None, -0.0], "a": "界😀" * 5000},
              (1, "tuple", {3: "integer key"}), {1: "int", None: "null"}, b"unsupported"]
    with _descriptor_snapshot():
        for value in values:
            for options in ({"ensure_ascii": False}, {"sort_keys": True, "ensure_ascii": False},
                            {"ensure_ascii": True, "allow_nan": False}):
                expected = outcome(lambda: json.dumps(value, **options))
                actual = outcome(lambda: codec.encoder(**options).encode(value))
                assert actual == expected
            assert outcome(lambda: digest(value)) == outcome(lambda: original_digest(value))
        assert codec.encoder(sort_keys=True, ensure_ascii=False).encode({"a": 1, "b": 2}) == '{"a": 1, "b": 2}'


@pytest.mark.parametrize("kind", ["tuple", "non_string_key", "dict_subclass", "str_subclass", "int_subclass",
                                   "nonfinite", "surrogate", "huge_integer", "cycle", "deep"])
def test_actual_digest_broad_inputs_and_json_copy_strict_inputs_match_old_behavior(kind):
    if kind == "tuple":
        value = {"value": (1, 2)}
    elif kind == "non_string_key":
        value = {1: "one"}
    elif kind == "dict_subclass":
        value = type("SyntheticDict", (dict,), {})({"value": 1})
    elif kind == "str_subclass":
        class SyntheticString(str):
            def __hash__(self):
                raise AssertionError("String subclass must never enter escape lookup")
        value = {"value": SyntheticString("x" * 5000)}
    elif kind == "int_subclass":
        value = {"value": type("SyntheticInt", (int,), {})(3)}
    elif kind == "nonfinite":
        value = {"value": float("nan")}
    elif kind == "surrogate":
        value = {"value": "x" * 5000 + "\ud800"}
    elif kind == "huge_integer":
        value = {"value": 10 ** 5000}
    elif kind == "cycle":
        value = []
        value.append(value)
    else:
        value = "long" * 1500
        for _ in range(70):
            value = [value]
    expected_digest = outcome(lambda: original_digest(value))
    expected_copy = outcome(lambda: original_copy(value))
    for active in (False, True):
        if active:
            with _descriptor_snapshot():
                assert outcome(lambda: digest(value)) == expected_digest
                assert outcome(lambda: json_copy(value)) == expected_copy
        else:
            assert outcome(lambda: digest(value)) == expected_digest
            assert outcome(lambda: json_copy(value)) == expected_copy


def test_copy_retains_UTF8_byte_limit_negative_zero_and_alias_separation_after_warmup():
    shared = {"text": "😀界\\\n" * 2000, "zero": -0.0}
    value = {"first": shared, "second": shared}
    limit = len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8"))
    with _descriptor_snapshot():
        json_copy(value)
        copied = json_copy(value, limit=limit)
        assert copied == original_copy(value, limit=limit)
        assert copied["first"] is not copied["second"] and copied["first"] is not shared
        assert math.copysign(1, copied["first"]["zero"]) == -1
        copied["first"]["text"] = "changed"
        assert copied["second"]["text"] == shared["text"]
        assert outcome(lambda: json_copy(value, limit=limit - 1)) == outcome(lambda: original_copy(value, limit=limit - 1))


def test_warm_scalar_never_skips_changed_whole_object_or_failed_document_validation():
    value = {"text": "same bytes" * 1000, "other": 1}
    with _descriptor_snapshot():
        assert digest(value) == original_digest(value)
        value["other"] = 2
        assert digest(value) == original_digest(value)
        value["other"] = object()
        for _ in range(2):
            assert outcome(lambda: digest(value)) == outcome(lambda: original_digest(value))
        value["other"] = float("nan")
        assert digest(value) == original_digest(value)
        assert outcome(lambda: json_copy(value)) == outcome(lambda: original_copy(value))


@pytest.mark.parametrize("bound", ["entries", "bytes"])
def test_escape_capacity_falls_back_without_changing_results(bound, monkeypatch):
    monkeypatch.setattr(codec, "_MAX_ENTRIES" if bound == "entries" else "_MAX_BYTES", 0)
    value = {"text": "😀escape" * 1000}
    expected = original_digest(value)
    with codec.string_snapshot() as facts:
        assert digest(value) == expected and digest(value) == expected
        assert not facts["inputs"] and facts["hits"] == 0


def test_nested_exception_copied_context_and_thread_do_not_extend_cache_lifetime():
    value = {"text": "immutable" * 1000}
    expected = original_digest(value)
    with codec.string_snapshot() as outer:
        assert digest(value) == expected and outer["misses"] == 1
        with pytest.raises(RuntimeError), codec.string_snapshot() as inner:
            assert digest(value) == expected and inner["misses"] == 1
            raise RuntimeError("nested exit")
        assert not inner["inputs"] and not inner["active"]
        assert digest(value) == expected and outer["hits"] == 1
        copied = copy_context()
        with ThreadPoolExecutor(max_workers=1) as executor:
            assert executor.submit(copied.run, digest, value).result() == expected
        assert outer["hits"] == 1
    assert not outer["inputs"] and not outer["encoders"] and not outer["active"]
    assert copied.run(digest, value) == expected and outer["hits"] == 1
    assert codec.encoder() is None


def test_changed_encoder_and_unavailable_C_encoder_preserve_stdlib_fallback(monkeypatch):
    value = {"text": "界escape" * 1000, "zero": -0.0}
    expected = original_digest(value)
    with codec.string_snapshot() as facts:
        assert digest(value) == expected
        before = facts["hits"]
        original = json.encoder.encode_basestring
        monkeypatch.setattr(json.encoder, "encode_basestring", lambda value: original(value))
        assert digest(value) == expected and facts["hits"] == before
        monkeypatch.setattr(json.encoder, "c_make_encoder", None)
        assert digest(value) == original_digest(value)
        assert json_copy(value) == original_copy(value)


def test_equal_distinct_strings_hit_full_value_and_changed_suffix_cannot_borrow():
    text = "界escape" * 1000
    equal = text.encode().decode()
    assert text == equal and text is not equal
    with codec.string_snapshot() as facts:
        assert digest({"text": text}) == original_digest({"text": text})
        assert digest({"text": equal}) == original_digest({"text": equal})
        assert facts["hits"] == 1 and facts["misses"] == 1
        assert digest({"text": text + "changed"}) == original_digest({"text": text + "changed"})
        assert facts["misses"] == 2
