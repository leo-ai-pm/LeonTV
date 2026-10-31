"""Measured same-read descriptor repetition, not a permission/read cache."""
import copy

import pytest
from test_image_comparison_policy import synthetic_descriptor

from core.creator.store import CreatorError, digest
from core.execution import image_policy


def test_repeated_exact_descriptor_does_not_repeat_roundtrip_copy(monkeypatch):
    value = synthetic_descriptor()[2].frozen()
    original = image_policy.json_copy
    calls = []

    def measured(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(image_policy, "json_copy", measured)
    with image_policy._descriptor_snapshot():
        image_policy.FrozenImageRequest.from_frozen(value)
        calls.clear()
        for _ in range(8):
            actual = image_policy.FrozenImageRequest.from_frozen(value).frozen()
            assert actual == value and actual is not value
        assert calls == [], "warm exact full inputs repeated json_copy in same native read"


@pytest.mark.parametrize("attack", ["tuple", "integer_key", "bool_version", "changed_wire", "NaN"])
def test_same_read_proof_still_checks_actual_strict_input_and_clears(attack):
    value = synthetic_descriptor()[2].frozen()
    with image_policy._descriptor_snapshot():
        image_policy.FrozenImageRequest.from_frozen(value)
        bad = copy.deepcopy(value)
        if attack == "tuple":
            bad["references"] = tuple(bad["references"])
        elif attack == "integer_key":
            bad[0] = None
        elif attack == "bool_version":
            bad["schema_version"] = True
        elif attack == "changed_wire":
            bad["wire"] += " "
            bad["descriptor_hash"] = digest({k: v for k, v in bad.items() if k != "descriptor_hash"})
        else:
            bad["schema_version"] = float("nan")
        with pytest.raises(CreatorError):
            image_policy.FrozenImageRequest.from_frozen(bad)
    assert image_policy._SNAPSHOT_PROOFS.get() is None


def test_warm_read_returns_defensive_tree():
    value = synthetic_descriptor()[2].frozen()
    with image_policy._descriptor_snapshot():
        image_policy.FrozenImageRequest.from_frozen(value)
        actual = image_policy.FrozenImageRequest.from_frozen(value).frozen()
        actual["references"].clear()
        assert image_policy.FrozenImageRequest.from_frozen(value).frozen() == value


def test_frozen_value_equality_is_stable_across_snapshot_boundary():
    value = synthetic_descriptor()[2].frozen()
    expected = image_policy.FrozenImageRequest.from_frozen(value)
    with image_policy._descriptor_snapshot():
        assert image_policy.FrozenImageRequest.from_frozen(value) == expected


def test_media_validation_does_not_parse_an_unused_defensive_copy(monkeypatch):
    from core.execution import media_ledger
    from core.execution.store import EMPTY

    calls = []
    from core.planner.review import json_copy

    original = getattr(media_ledger, "json_copy", json_copy)

    def measured(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(media_ledger, "json_copy", measured, raising=False)
    media_ledger.validate({**copy.deepcopy(EMPTY), "media": {}})
    assert calls == [], "the media validation's discarded json_copy still parses a whole snapshot"


@pytest.mark.parametrize("value,limit", [
    ({"文字": "你好", "nested": [True, None, 2.0]}, 128), ({"文字": "你好"}, 8),
    ({"x": (1, 2)}, 128), ({0: "integer-key"}, 128), ({"x": float("nan")}, 128),
    ({"x": float("inf")}, 128), ({"x": "\ud800"}, 128), ({"x": "a" * 63}, 64),
    ({"x": "a" * 55}, 64),
])
def test_media_json_elision_preserves_exact_strict_and_UTF8_size_semantics(value, limit):
    from core.execution.media_ledger import _check_media_json
    from core.planner.review import json_copy

    outcomes = []
    for function in (json_copy, _check_media_json):
        try:
            function(value, limit=limit)
            outcomes.append(True)
        except (ValueError, TypeError, CreatorError, UnicodeError):
            outcomes.append(False)
    assert outcomes[0] == outcomes[1]
