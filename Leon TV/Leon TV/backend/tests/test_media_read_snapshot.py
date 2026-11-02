"""Complete native validation once per inspection, fresh at every new boundary."""
import json
import os
import threading
from types import SimpleNamespace

import pytest
from test_media_persistence_snapshot import journal

from core.creator.store import CreatorError
from core.execution import store as module
from core.execution.media_runtime import MediaExecution
from core.execution.store import EMPTY, ExecutionStore


def counted(monkeypatch):
    calls = []
    original = module.validate_state

    def measured(value):
        calls.append(value.get("head_revision"))
        return original(value)

    monkeypatch.setattr(module, "validate_state", measured)
    return calls


def test_inspection_reuses_only_exact_committed_input_and_revalidates_next_request(tmp_path, monkeypatch):
    store = journal(tmp_path / "execution")
    calls = counted(monkeypatch)
    raw = store.path("state.json").read_bytes()
    with store.inspection_snapshot() as payload:
        payload["media"] = {"untrusted": True}
        first = ExecutionStore(store.root).read_only()
        first["media"] = {"also-untrusted": True}
        assert ExecutionStore(store.root).read_only() == EMPTY
        assert calls == [8]
    assert store.path("state.json").read_bytes() == raw
    with store.inspection_snapshot() as payload:
        assert payload == EMPTY
        assert ExecutionStore(store.root).read_only() == EMPTY
    assert calls == [8, 8]
    assert store.read_only() == EMPTY
    assert calls == [8, 8, 8]


@pytest.mark.parametrize("mutation", ["bytes", "inode", "symlink", "hardlink", "oversize", "unlink"])
def test_inspection_rejects_file_changes_before_nested_read(tmp_path, mutation):
    store = journal(tmp_path / "execution")
    path = store.path("state.json")
    raw = path.read_bytes()
    with store.inspection_snapshot():
        if mutation == "bytes":
            changed = json.loads(raw)
            changed["head_hash"] = "0" * 64
            path.write_text(json.dumps(changed))
        elif mutation == "inode":
            replacement = tmp_path / "replacement"
            replacement.write_bytes(raw)
            os.replace(replacement, path)
        elif mutation == "symlink":
            target = tmp_path / "target"
            target.write_bytes(raw)
            path.unlink()
            path.symlink_to(target)
        elif mutation == "hardlink":
            os.link(path, tmp_path / "extra-link")
        elif mutation == "oversize":
            with path.open("r+b") as stream:
                stream.truncate(module.MAX_STATE_BYTES + 1)
        else:
            path.unlink()
        with pytest.raises(CreatorError):
            ExecutionStore(store.root).read_only()


def test_nested_inspection_root_thread_and_exception_lifetimes(tmp_path, monkeypatch):
    a, b = journal(tmp_path / "a"), journal(tmp_path / "b", 9)
    calls = counted(monkeypatch)
    with a.inspection_snapshot():
        with pytest.raises(RuntimeError):
            with b.inspection_snapshot():
                assert b.read_only() == EMPTY
                raise RuntimeError("synthetic boundary exit")
        assert a.read_only() == EMPTY
        result = []
        worker = threading.Thread(target=lambda: result.append(a.read_only()))
        worker.start()
        worker.join()
        assert result == [EMPTY] and calls == [8, 9, 8]
    a.path("state.json").write_text("{}")
    with pytest.raises(CreatorError):
        with a.inspection_snapshot():
            pytest.fail("corrupt subsequent input was reused")


def test_missing_store_is_readonly_and_missing_task_preserves_error_and_cleanup(tmp_path, monkeypatch):
    root = tmp_path / "missing"
    store = ExecutionStore(root)
    with store.inspection_snapshot() as payload:
        assert payload == EMPTY and store.read_only() == EMPTY
    assert not root.exists()
    store = journal(tmp_path / "present")
    calls = counted(monkeypatch)
    with pytest.raises(CreatorError) as error:
        MediaExecution(SimpleNamespace(store=store)).get("0" * 32)
    assert (error.value.code, error.value.status) == ("NOT_FOUND", 404)
    store.path("state.json").write_text("{}")
    with pytest.raises(CreatorError):
        store.read_only()
    assert len(calls) == 2
