"""Atomic private key/run/project/event persistence with OS-backed execution locks."""

import copy
import json
import os
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path


class HarnessError(Exception):
    def __init__(self, code, message, status=409):
        self.code, self.message, self.status = code, message, status


def lock_file(path, blocking=True):
    stream = path.open("a+b")
    try:
        if os.name == "nt":
            import msvcrt

            if not path.stat().st_size:
                stream.write(b"0")
                stream.flush()
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(stream, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
    except BaseException:
        stream.close()
        raise
    return stream


def unlock_file(stream):
    if os.name == "nt":
        import msvcrt

        stream.seek(0)
        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(stream, fcntl.LOCK_UN)
    stream.close()


class HarnessStore:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.thread_lock = threading.RLock()

    def check_private(self, static_roots):
        if any(self.root.is_relative_to(Path(p).resolve()) for p in static_roots):
            raise HarnessError("PRIVATE_STORAGE_UNAVAILABLE", "Harness storage must be private", 503)

    def private_path(self, *parts):
        path = self.root.joinpath(*parts)
        if self.root.resolve() != self.root or path.resolve() != path or not path.is_relative_to(self.root):
            raise HarnessError("PRIVATE_STORAGE_UNAVAILABLE", "Private symlinks or escaped paths are forbidden", 503)
        return path

    @contextmanager
    def transaction(self):
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.thread_lock:
            lock = lock_file(self.private_path(".state.lock"))
            try:
                path = self.private_path("state.json")
                state = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {
                    "schema_version": 1, "keys": {}, "project_runs": {}, "runs": {},
                }
                before = copy.deepcopy(state)
                yield state
                if state != before or not path.exists():
                    fd, temporary = tempfile.mkstemp(prefix=".harness-", dir=self.root)
                    try:
                        with os.fdopen(fd, "w", encoding="utf-8") as stream:
                            json.dump(state, stream, ensure_ascii=False)
                            stream.flush()
                            os.fsync(stream.fileno())
                        os.replace(temporary, path)
                    finally:
                        Path(temporary).unlink(missing_ok=True)
            finally:
                unlock_file(lock)

    def execution_lock(self, run_id):
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            return lock_file(self.private_path(f".{run_id}.execution.lock"), blocking=False)
        except OSError:
            return None

    def media_path(self, run_id, artifact_id, version):
        # IDs are server-owned. Resolve again to reject substituted symlink parents.
        return self.private_path("media", run_id, artifact_id, f"v{version}.mp4")
