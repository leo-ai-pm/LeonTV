"""Stdlib JSON encoding with bounded immutable string escaping in one read.

Every container, key, number and cycle is still visited by the original C
encoder. Only encode_basestring results for exact, long, immutable strings are
reused. No JSON document, digest, validation or authority result is cached.
"""
import json
import threading
from contextlib import contextmanager
from contextvars import ContextVar

_ACTIVE = ContextVar("json_read_string_escapes", default=None)
_MIN_CHARS = 4096
_MAX_ENTRIES = 64
_MAX_BYTES = 16 * 1048576


@contextmanager
def string_snapshot():
    facts = {"inputs": {}, "bytes": 0, "hits": 0, "misses": 0,
             "active": True, "thread": threading.get_ident(),
             "encoders": {False: json.encoder.encode_basestring,
                          True: json.encoder.encode_basestring_ascii}}
    token = _ACTIVE.set(facts)
    try:
        yield facts
    finally:
        facts["active"] = False
        facts["inputs"].clear()
        facts["encoders"].clear()
        facts["bytes"] = 0
        _ACTIVE.reset(token)


class _Encoder(json.JSONEncoder):
    def __init__(self, facts, **options):
        super().__init__(**options)
        self._facts = facts

    def iterencode(self, value, _one_shot=False):
        maker = json.encoder.c_make_encoder
        if not _one_shot or maker is None or self.indent is not None:
            return super().iterencode(value, _one_shot)
        base = json.encoder.encode_basestring_ascii if self.ensure_ascii else json.encoder.encode_basestring
        facts = self._facts
        ascii_mode = self.ensure_ascii

        def escaped(text):
            # Subclasses may customize hashing/equality: never look them up.
            if (type(text) is not str or len(text) < _MIN_CHARS
                    or not facts["active"] or facts["thread"] != threading.get_ident()
                    or facts["encoders"].get(ascii_mode) is not base):
                return base(text)
            key = (ascii_mode, text)
            if key in facts["inputs"]:
                facts["hits"] += 1
                return facts["inputs"][key]
            encoded = base(text)
            facts["misses"] += 1
            # Conservative Unicode storage accounting, not an RSS claim.
            size = 128 + 4 * (len(text) + len(encoded))
            if (facts["active"] and facts["thread"] == threading.get_ident()
                    and facts["encoders"].get(ascii_mode) is base
                    and len(facts["inputs"]) < _MAX_ENTRIES and facts["bytes"] + size <= _MAX_BYTES):
                facts["inputs"][key] = encoded
                facts["bytes"] += size
            return encoded

        try:
            encode = maker({} if self.check_circular else None, self.default, escaped, self.indent,
                           self.key_separator, self.item_separator, self.sort_keys, self.skipkeys, self.allow_nan)
        except (TypeError, AttributeError):
            # A different Python C-encoder interface keeps original behavior.
            return super().iterencode(value, _one_shot)
        return encode(value, 0)


def encoder(*, sort_keys=False, ensure_ascii=True, allow_nan=True):
    """Return an optional scoped encoder; callers retain original fallbacks."""
    facts = _ACTIVE.get()
    options = {"sort_keys": sort_keys, "ensure_ascii": ensure_ascii, "allow_nan": allow_nan}
    if (facts is None or not facts["active"] or facts["thread"] != threading.get_ident()
            or any(type(option) is not bool for option in options.values())):
        return None
    return _Encoder(facts, **options)
