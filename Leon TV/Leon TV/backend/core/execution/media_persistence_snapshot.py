"""Committed native proof, only while its owning store transaction is locked.

Not a permission cache: every read checks the exact file and returns a defensive
copy of the committed input, never the mutable transaction working payload.
"""
import copy
import os
import stat
import threading
from contextlib import contextmanager

from core.creator.store import CreatorError

_local = threading.local()


def read_committed_bytes(path, maximum):
    if path.resolve() != path or path.parent.resolve() != path.parent:
        raise CreatorError("CORRUPT_STORAGE", "Unsafe execution snapshot path")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > maximum:
            raise CreatorError("CORRUPT_STORAGE", "Unsafe or oversized execution snapshot")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            raw = stream.read(maximum + 1)
        if len(raw) > maximum:
            raise CreatorError("CORRUPT_STORAGE", "Execution snapshot exceeds capacity")
        return raw, (info.st_dev, info.st_ino)
    finally:
        os.close(descriptor)


def read_snapshot(path, maximum):
    proof = getattr(_local, "proof", None)
    if proof is None or proof[0] != path:
        return None
    _, identity, raw, payload = proof
    try:
        current, actual_identity = read_committed_bytes(path, maximum)
        if actual_identity != identity or current != raw:
            raise ValueError("committed file changed")
    except (OSError, ValueError):
        raise CreatorError("CORRUPT_STORAGE", "Committed execution snapshot changed during transaction") from None
    return copy.deepcopy(payload)


@contextmanager
def committed_snapshot(path, raw, payload, identity):
    # Nested, unrelated transactions restore their parent's exact proof. It
    # cannot be used by another thread or after any normal/exceptional exit.
    previous = getattr(_local, "proof", None)
    _local.proof = (path, identity, raw, copy.deepcopy(payload)) if raw is not None else None
    try:
        yield
    finally:
        _local.proof = previous
