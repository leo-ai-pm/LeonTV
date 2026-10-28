"""Synthetic contract checks; never grant media authority or load private inputs."""

import copy

import pytest
from test_image_retained_unknown import plan_case

from core.creator.store import CreatorError, digest
from core.execution import image_runtime_operator


def test_comparison_entry_is_explicit_and_never_activates(monkeypatch, capsys):
    # The existing runtime command must recognize the new review-only entry.
    # Missing control still fails closed, with a comparison-specific result.
    monkeypatch.setattr(
        image_runtime_operator, "read_image_control", lambda *args: pytest.fail("implicit control")
    )
    assert image_runtime_operator.main(["comparison-quote", "--max-comparison-posts", "2"]) == 1
    import json

    result = json.loads(capsys.readouterr().out)
    assert result["error"] == "IMAGE_COMPARISON_UNAVAILABLE"
    assert result["paid_calls"] == 0 and result["dispatch_available"] is False


def test_raw_absent_retention_is_not_silently_upgraded(tmp_path):
    from core.execution import image_comparison as comparison

    case = plan_case(tmp_path)
    # A helper fixture is not an actual loaded control; no permission stand-in.
    before = copy.deepcopy(case["payload"])
    with pytest.raises(CreatorError):
        comparison.quote_comparison(case["control"], max_posts=2)
    assert digest(case["payload"]) == digest(before)


@pytest.mark.parametrize("maximum", [True, 0, 2, 3, "2", None])
def test_maximum_requires_strict_one_or_two(maximum):
    from core.execution import image_comparison as comparison

    with pytest.raises(CreatorError):
        comparison.quote_comparison(None, max_posts=maximum)
