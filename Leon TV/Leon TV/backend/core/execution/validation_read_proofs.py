"""Bounded pure full-input proofs during one native history reconstruction.

These two groups contain no current authority, filesystem, clock or credential
decision. New/changed JSON input always runs the original computation. Actual
store/control/source/file reads remain independent freshness boundaries.
"""
import copy
import math

_GROUPS = {"historical_context", "manifest_schema"}
_MAX_BYTES = 8 * 1048576


def _key(value, depth=0, _budget=None):
    if _budget is None:
        _budget = [0]
    _budget[0] += 1
    if depth > 64 or _budget[0] > 65536:
        raise TypeError("No proof for deeply nested input")
    kind = type(value)
    if value is None:
        return ("null",), 48
    if kind is str:
        return ("str", value), 64 + len(value) * 4
    if kind is bool:
        return ("bool", value), 64
    if kind is int:
        return ("int", value), 64 + value.bit_length() // 8
    if kind is float and math.isfinite(value):
        return ("float", value.hex()), 96
    if kind is list:
        parts = [_key(item, depth + 1, _budget) for item in value]
        return ("list", tuple(item[0] for item in parts)), 64 + sum(item[1] for item in parts)
    if kind is dict and all(type(name) is str for name in value):
        parts = [(name, _key(item, depth + 1, _budget)) for name, item in value.items()]
        return ("dict", tuple((name, item[0]) for name, item in parts)), 64 + sum(64 + len(name) * 4 + item[1] for name, item in parts)
    raise TypeError("No proof for non-strict JSON")


def _thaw(marker):
    kind = marker[0]
    if kind == "null":
        return None
    if kind == "list":
        return [_thaw(item) for item in marker[1]]
    if kind == "dict":
        return {name: _thaw(item) for name, item in marker[1]}
    if kind == "float":
        return float.fromhex(marker[1])
    return marker[1]


def reconstructed(group, value, compute):
    from core.execution.image_policy import _MAX_SNAPSHOT_PROOFS, _snapshot_proofs

    facts = _snapshot_proofs()
    if facts is None or group not in _GROUPS:
        return compute(value)
    try:
        marker, cost = _key(value)
    except (ValueError, TypeError, RecursionError):
        return compute(value)
    groups = facts.setdefault("value_proofs", {})
    pool = groups.setdefault(group, {"inputs": {}, "bytes": 0})
    if marker in pool["inputs"]:
        return copy.deepcopy(pool["inputs"][marker])
    # Compute from this exact frozen input, not a second read of a mutable
    # caller object after its key was captured. Only this successful result
    # can prove the marker; concurrent caller mutation cannot poison the proof.
    result = compute(_thaw(marker))
    # Conservative typed-tree accounting; immutable string references may be
    # shared, but the budget counts their full possible Unicode storage anyway.
    if len(pool["inputs"]) < min(32, _MAX_SNAPSHOT_PROOFS) and pool["bytes"] + cost * 2 <= _MAX_BYTES:
        pool["inputs"][marker] = copy.deepcopy(result)
        pool["bytes"] += cost * 2
    return result
