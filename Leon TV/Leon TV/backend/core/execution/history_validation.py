"""Exact immutable dependency proofs inside one owned native reconstruction.

Positions only address proof slots: complete typed values determine equality.
Every frame hash/global/transition rule still runs. A frame's successful pure
relationship proofs become reusable only after its complete validation returns.
Nothing here reads authority/files/time or survives this reconstruction.
"""
import copy
import math
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field

from core.creator.store import CreatorError, digest
from core.execution.validation_read_proofs import _key, _thaw

_ACTIVE = ContextVar("native_history_reconstruction", default=None)
_MAX_GRAPH_BYTES = 64 * 1048576
_MAX_PROOFS = 512
CORE = ("tasks", "quotes", "grants", "operations", "keys", "accounts")
_RUNTIME_FAMILIES = {"code_continuation_quote", "code_continuation_activation",
    "validity_continuation_quote", "validity_continuation_intent", "validity_continuation_activation",
    "runtime_continuation_quote", "runtime_continuation_activation"}


def _environment():
    """Fresh semantic inputs, never a permission, clock or external-file read.

    These module limits and codec settings can change during a synchronous
    read. The complete values participate in each relationship proof; a
    computation spanning a change cannot seed a reusable proof.
    """
    from decimal import getcontext

    from PIL import Image, ImageFile

    from core.execution import (
        fees,
        image_code_continuation,
        image_policy,
        image_receipts,
        reference_derivation,
    )

    decimal = getcontext()
    return {
        "decimal": [decimal.prec, decimal.rounding, decimal.Emin, decimal.Emax, decimal.capitals, decimal.clamp,
                    [[name, enabled] for name, enabled in sorted((error.__name__, enabled) for error, enabled in decimal.traps.items())]],
        "pillow": [Image.MAX_IMAGE_PIXELS, ImageFile.LOAD_TRUNCATED_IMAGES],
        "delivery": [image_receipts.MAX_IMAGE_BYTES, image_receipts.MAX_IMAGE_PIXELS, image_receipts.MAX_IMAGE_DIMENSION],
        "descriptor": [image_policy.MAX_BYTES, image_policy.MAX_DESCRIPTOR_BYTES],
        "reference": [reference_derivation.MAX_SOURCE_BYTES, reference_derivation.MAX_PIXELS,
                      reference_derivation.MAX_BYTES, reference_derivation.MAX_DESCRIPTOR_BYTES,
                      list(reference_derivation.JPEG_QUALITIES), reference_derivation.encoder_identity()],
        "fee_export": [fees.MAX_EXPORT_BYTES, sorted(fees.ROW_FIELDS), sorted(fees.MATCH_FIELDS)],
        "manifest": [list(image_code_continuation.REQUIRED_CODE_FILES), sorted(image_code_continuation.EXTRA_CODE)],
    }


@dataclass(frozen=True)
class _Value:
    marker: tuple
    size: int = field(compare=False)
    fingerprint: int = field(compare=False)

    @classmethod
    def capture(cls, value):
        marker, size = _key(value)
        return cls(marker, size, hash(marker))

    def __hash__(self):
        return self.fingerprint


def _matches(value, marker):
    tag = marker[0]
    if tag == "dict":
        return (type(value) is dict and tuple(value) == tuple(name for name, _ in marker[1])
                and all(_matches(value[name], item) for name, item in marker[1]))
    if tag == "list":
        return type(value) is list and len(value) == len(marker[1]) and all(_matches(a, b) for a, b in zip(value, marker[1]))
    if tag == "null":
        return value is None
    if tag == "float":
        return type(value) is float and math.isfinite(value) and value.hex() == marker[1]
    expected = {"str": str, "int": int, "bool": bool}[tag]
    return type(value) is expected and (value is marker[1] or value == marker[1])


def _error():
    return CreatorError("CORRUPT_STORAGE", "Execution journal failed integrity checks")


class _Reconstruction:
    def __init__(self):
        self.thread = threading.get_ident()
        self.active = self.enabled = True
        self.payload = None
        self.tables, self.proofs, self.pending = {}, {}, {}
        self.bytes = 0

    def start(self, payload):
        self.payload = payload
        self.tables = {name: {} for name in payload}

    def apply(self, namespace, changed):
        if not self.enabled:
            return
        try:
            table = self.tables.setdefault(namespace, {})
            for name, value in changed.items():
                if type(name) is not str:
                    raise TypeError("non-JSON record key")
                captured = _Value.capture(value)
                self.bytes += captured.size - (table[name].size if name in table else 0)
                table[name] = captured
            if self.bytes > _MAX_GRAPH_BYTES:
                raise TypeError("proof graph capacity")
        except (ValueError, TypeError, RecursionError):
            # Unsupported/oversized inputs run the original validators, never
            # a partial graph and never a new relaxed schema.
            self.enabled = False
            self.tables.clear()
            self.proofs.clear()
            self.pending.clear()

    def guard(self):
        if not self.enabled or self.payload is None:
            return
        if (tuple(self.payload) != tuple(self.tables)
                or any(type(self.payload[n]) is not dict or tuple(self.payload[n]) != tuple(t)
                       or any(not _matches(self.payload[n][k], v.marker) for k, v in t.items())
                       for n, t in self.tables.items())):
            raise _error()

    def signature(self, selection):
        return tuple((name, tuple((key, self.tables[name][key]) for key in keys)) for name, keys in selection.items())

    @staticmethod
    def materialize(signature):
        return {name: {key: _thaw(value.marker) for key, value in values} for name, values in signature}

    def prove(self, slot, selection, extra, compute):
        signature = self.signature(selection)
        try:
            argument = _Value.capture(extra)
            environment = _Value.capture(_environment())
        except (ValueError, TypeError, RecursionError):
            return compute(self.materialize(signature), copy.deepcopy(extra))
        marker = (signature, argument, environment)
        prior = self.pending.get(slot, self.proofs.get(slot))
        if prior is not None and prior[0] == marker:
            return copy.deepcopy(prior[1])
        # Both computation and its key consume the same immutable capture.
        result = compute(self.materialize(signature), _thaw(argument.marker))
        if (environment == _Value.capture(_environment()) and
                (slot in self.proofs or slot in self.pending or len(self.proofs) + len(self.pending) < _MAX_PROOFS)):
            self.pending[slot] = (marker, copy.deepcopy(result))
        return result

    def commit(self):
        self.guard()
        self.proofs.update(self.pending)
        self.pending.clear()


@contextmanager
def reconstruction(state):
    from core.planner.review import json_value

    try:
        supported = json_value(state)
        stable = copy.deepcopy(state) if supported else state
        seal = digest(stable) if supported else None
    except (ValueError, TypeError, UnicodeError, RecursionError):
        supported = False
    if not supported:
        token = _ACTIVE.set(None)
        try:
            yield state
        finally:
            _ACTIVE.reset(token)
        return
    facts = _Reconstruction()
    token = _ACTIVE.set(facts)
    try:
        yield stable
        facts.guard()
        if digest(stable) != seal:
            raise _error()
    finally:
        facts.active = False
        facts.tables.clear()
        facts.proofs.clear()
        facts.pending.clear()
        facts.payload = None
        _ACTIVE.reset(token)


def current(payload=None):
    facts = _ACTIVE.get()
    if (facts is None or not facts.active or not facts.enabled or facts.thread != threading.get_ident()
            or payload is not None and facts.payload is not payload):
        return None
    return facts


def start(payload):
    facts = current()
    if facts is not None:
        facts.start(payload)


def apply(namespace, changed):
    facts = current()
    if facts is not None:
        facts.apply(namespace, changed)


def commit(payload):
    facts = current(payload)
    if facts is not None:
        facts.commit()


def scope_set(payload, scopes, compute):
    facts = current(payload)
    if facts is None:
        return compute(payload, scopes)
    facts.guard()
    selection = {n: tuple(facts.tables[n]) for n in CORE}
    if "asset_design" in facts.tables:
        selection["asset_design"] = tuple(facts.tables["asset_design"])
    # verified_manifest independently validates the asset namespace, including
    # global capture-ID/allocation and fee-owner conflicts in these families.
    kinds = {"scope", "operation", "query", "fee", "claim", "image_comparison_fee"} if any("manifest_origin" in s for s in scopes) else {"scope"}
    selection["media"] = tuple(k for k, r in payload["media"].items() if r["kind"] in kinds)
    return facts.prove("scopes", selection, None,
        lambda actual, _: compute(actual, sorted((s for s in actual["media"].values() if s["kind"] == "scope"), key=lambda s: s["ordinal"])))


def _selection(payload, record):
    """Conservative complete read sets; complex profiles keep the full input."""
    media, kind = payload["media"], record["kind"]
    scope_id = record["scope_id"]
    scope_key = "scope:" + str(scope_id)
    wanted = {scope_key} if scope_id is not None else set()
    wanted.add(kind + ":" + record["id"])
    core = False
    asset = False
    if kind == "scope":
        return None
    if kind == "quote":
        pass
    elif kind == "grant":
        wanted.add("quote:" + record["quote_id"])
    elif kind == "delivery":
        wanted.add("query:" + record["query_id"])
    elif kind == "reference_derivation":
        wanted.add("operation:" + record["operation_id"])
    elif kind == "key":
        wanted.add(record["resource_key"])
    elif kind == "claim" and record["owner"] == "text":
        core = True
    elif kind == "claim":
        wanted.add("fee:" + record["id"])
    elif kind == "fee":
        core = asset = True
        wanted.add("operation:" + record["operation_id"])
        wanted.update(k for k, r in media.items() if r["kind"] in {"claim", "fee", "image_comparison_fee"})
    elif kind in {"operation", "query"}:
        descriptor = record["descriptor"]
        if descriptor["kind"] not in {"controlled_image_http_v1", "controlled_image_http_v2", "controlled_image_http_v3"}:
            return None
        # Retained v4 authority has a broader unknown/collector dependency set.
        # Keep its original full-input path rather than guess that closure.
        if descriptor.get("continuation_authority", {}).get("schema_version") == 4:
            return None
        wanted.update(k for k, r in media.items() if r["kind"] in _RUNTIME_FAMILIES)
        wanted.update(k for k, r in media.items() if r["scope_id"] == scope_id
                      and r["kind"] in {"scope", "quote", "grant", "operation", "delivery", "reference_derivation"})
        if kind == "operation" and record["fee_id"] is not None:
            wanted.add("fee:" + record["fee_id"])
    else:
        return None
    selection = {n: tuple(payload[n]) for n in CORE} if core else {n: () for n in CORE}
    if asset and "asset_design" in payload:
        selection["asset_design"] = tuple(payload["asset_design"])
    # Missing dependencies fail just as the original lookup would; they are
    # never silently represented as an absent optional record.
    for name in wanted:
        media[name]
    selection["media"] = tuple(name for name in media if name in wanted)
    return selection


def record(payload, item, used_requests, compute):
    facts = current(payload)
    if facts is None:
        return compute(payload, item, used_requests)
    selection = _selection(payload, item)
    if selection is None:
        # No guessed dependency closure for special contracts. Their full
        # validators and the store's specialized history checks still run.
        return compute(payload, item, used_requests)
    record_key = item["kind"] + ":" + item["id"]
    return facts.prove(("record", record_key), selection, used_requests,
        lambda actual, used: compute(actual, actual["media"][record_key], used))
