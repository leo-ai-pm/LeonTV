"""Frozen response observation policy; it never extends dispatch authority."""
from datetime import datetime

from core.creator.store import CreatorError, digest

IMAGE_KIND = "controlled_image_http_v3"
RESPONSE_KINDS = frozenset({IMAGE_KIND, "controlled_image_comparison_http_v1"})
RESPONSE_KIND = "image_response_observation_v1"
RESPONSE_FIELDS = frozenset({"response_deadline_at", "response_window_seconds", "response_contract_hash"})


def _require(value):
    if not value:
        raise CreatorError("IMAGE_RESPONSE_LIFECYCLE_INVALID", "Frozen image response policy is unavailable", 409)


def _timestamp(value):
    _require(type(value) is str and len(value) <= 64)
    try:
        result = datetime.fromisoformat(value)
        _require(result.tzinfo is not None)
        return result
    except (TypeError, ValueError):
        _require(False)


def make_response_policy(seconds):
    _require(type(seconds) is int and 1 <= seconds <= 180)
    value = {"schema_version": 1, "kind": RESPONSE_KIND, "dispatch_window_seconds": 120,
        "response_window_seconds": seconds, "cleanup_grace_seconds": 5}
    return {**value, "contract_hash": digest(value)}


def validate_response_policy(value):
    _require(type(value) is dict and set(value) == {"schema_version", "kind", "dispatch_window_seconds",
        "response_window_seconds", "cleanup_grace_seconds", "contract_hash"})
    expected = make_response_policy(value["response_window_seconds"])
    _require(all(type(value[key]) is type(item) and value[key] == item for key, item in expected.items()))
    return expected


def response_deadline(binding):
    """Return the immutable response cutoff, rejecting extension of old bindings."""
    _require(type(binding) is dict)
    dispatch = _timestamp(binding.get("deadline_at"))
    if binding.get("kind") not in RESPONSE_KINDS:
        _require(not RESPONSE_FIELDS.intersection(binding))
        return dispatch
    _require(RESPONSE_FIELDS.issubset(binding))
    policy = make_response_policy(binding["response_window_seconds"])
    _require(type(binding["response_contract_hash"]) is str
        and binding["response_contract_hash"] == policy["contract_hash"])
    response = _timestamp(binding["response_deadline_at"])
    _require((response - dispatch).total_seconds() == policy["response_window_seconds"])
    return response
