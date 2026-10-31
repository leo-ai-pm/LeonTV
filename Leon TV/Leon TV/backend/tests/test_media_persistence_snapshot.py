"""A second control/store read inside a transaction must reuse committed proof."""
import copy
import json
import os
import threading

import pytest

from core.creator.store import CreatorError, digest, utc_now
from core.execution import store as module
from core.execution.store import EMPTY, ExecutionStore


def journal(path, count=8):
    frames = []
    previous = None
    for index in range(count):
        frame = {"revision": index + 1, "saved_at": utc_now(), "previous_hash": previous, "delta": {}}
        frame["hash"] = previous = digest(frame)
        frames.append(frame)
    path.mkdir(mode=0o700)
    (path / "state.json").write_text(json.dumps({"schema_version": 2, "head_revision": count, "head_hash": previous, "frames": frames}))
    return ExecutionStore(path)


def test_transaction_second_control_read_reuses_proof_without_exposing_mutable_payload(tmp_path, monkeypatch):
    store = journal(tmp_path / "execution")
    calls = []
    original = module.validate_state

    def counted(state):
        calls.append(state["head_revision"])
        return original(state)

    monkeypatch.setattr(module, "validate_state", counted)
    with store.transaction() as working:
        working["media"] = {}
        one = ExecutionStore(store.root).read_only()
        one["media"] = {"untrusted": True}
        assert ExecutionStore(store.root).read_only() == EMPTY
        assert len(calls) == 1
        working.pop("media")
    assert ExecutionStore(store.root).read_only() == EMPTY
    assert len(calls) == 2


def test_committed_proof_bound_root_thread_file_bytes_and_lifetime(tmp_path):
    a = journal(tmp_path / "a")
    b = journal(tmp_path / "b", 9)
    with a.transaction():
        assert b.read_only() == EMPTY
        results = []
        worker = threading.Thread(target=lambda: results.append(a.read_only()))
        worker.start()
        worker.join()
        assert results == [EMPTY]
        path = a.path("state.json")
        raw = path.read_bytes()
        changed = json.loads(raw)
        changed["head_hash"] = "0" * 64
        path.write_text(json.dumps(changed))
        with pytest.raises(CreatorError):
            a.read_only()
        path.write_bytes(raw)
    assert a.read_only() == copy.deepcopy(EMPTY)


def test_exception_drops_transaction_proof(tmp_path):
    a = journal(tmp_path / "a")
    with pytest.raises(RuntimeError):
        with a.transaction():
            raise RuntimeError("synthetic abort")
    a.path("state.json").write_text("{}")
    with pytest.raises(CreatorError):
        a.read_only()


def test_snapshot_rejects_new_hardlink_and_oversized_file_before_read(tmp_path):
    a = journal(tmp_path / "a")
    with a.transaction():
        link = tmp_path / "extra-link"
        os.link(a.path("state.json"), link)
        try:
            with pytest.raises(CreatorError):
                a.read_only()
        finally:
            link.unlink()
        raw = a.path("state.json").read_bytes()
        try:
            with a.path("state.json").open("r+b") as stream:
                stream.truncate(module.MAX_STATE_BYTES + 1)
            with pytest.raises(CreatorError):
                a.read_only()
        finally:
            a.path("state.json").write_bytes(raw)
