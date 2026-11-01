"""Synthetic-only reserve failure capture; real validators continue unchanged."""
import copy
import json
import linecache
import os
import sys
import time
import traceback
from pathlib import Path
from urllib.parse import urlsplit


def sample(label):
    return {"label": label, "wall_ns": time.time_ns(), "monotonic_ns": time.monotonic_ns()}


def write_private(path, value):
    # Preserve insertion order: text wire contracts can contain embedded JSON.
    raw = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        view = memoryview(raw)
        while view:
            size = os.write(fd, view)
            if size <= 0:
                raise OSError("Diagnostic capture write incomplete")
            view = view[size:]
        os.fsync(fd)
    finally:
        os.close(fd)


class ReserveTrace:
    """Capture before store exception wrapping, without replacing a validator."""

    def __init__(self, output, *, clock_returns=True):
        self.output = Path(output).resolve()
        self.clock_returns = clock_returns
        self.events, self.errors, self.capture_errors = [], [], []
        self.captured = False
        self.operation_times = None
        self.pytest_current_test = os.environ.get("PYTEST_CURRENT_TEST")

    def __enter__(self):
        if sys.gettrace() is not None:
            raise RuntimeError("Use an isolated process without another trace hook")
        self.output.mkdir(parents=True, mode=0o700)
        self.events.append(sample("reserve_entry"))
        sys.settrace(self.trace)
        return self

    def __exit__(self, kind, error, tb):
        sys.settrace(None)
        self.events.append(sample("reserve_exit"))
        self.result = {"raised": kind.__name__ if kind else None, "code": getattr(error, "code", None),
                       "candidate_captured": self.captured, "errors": self.errors,
                       "clock_samples": self.events, "operation_times": self.operation_times,
                       "pytest_current_test": self.pytest_current_test,
                       "capture_errors": self.capture_errors}
        try:
            write_private(self.output / "result.json", self.result)
        except Exception as capture_error:
            self.capture_errors.append(type(capture_error).__name__)
        return False

    def trace(self, frame, event, arg):
        try:
            return self._trace(frame, event, arg)
        except Exception as capture_error:
            # Even stack/source/clock sampling failure must not replace the
            # application's result or its original validator exception.
            self.capture_errors.append(type(capture_error).__name__)
            return self.trace

    def _trace(self, frame, event, arg):
        name, path = frame.f_code.co_name, frame.f_code.co_filename
        if event == "call":
            if "/core/execution/" not in path and not path.endswith("/core/creator/store.py"):
                return None
            if name in {"require", "validate", "_validate", "transaction", "validate_roles", "validate_media", "validate_payload"}:
                return self.trace
            if name in {"now", "utc_now", "_permit", "_record"} and self.clock_returns:
                return self.trace
            return None
        if event == "return" and name in {"now", "utc_now", "_permit", "_record"}:
            value = sample(Path(path).name + ":" + name)
            if name in {"now", "utc_now"}:
                value["returned"] = arg.isoformat() if hasattr(arg, "isoformat") else arg
            elif isinstance(arg, dict):
                value["returned"] = {k: v for k, v in arg.items() if k in {"kind", "created_at", "issued_at", "deadline_at"}}
                if name == "_record" and arg.get("kind") == "operation":
                    self.operation_times = {"created_at": arg["created_at"], **{k: arg["descriptor"]["permit"][k] for k in ("issued_at", "deadline_at")}}
            self.events.append(value)
        if event != "exception" or not isinstance(arg[1], (ValueError, TypeError, KeyError, ArithmeticError, AssertionError, RecursionError)) and not hasattr(arg[1], "code"):
            return self.trace
        transaction = frame
        while transaction is not None:
            if transaction.f_code.co_name == "transaction" and transaction.f_code.co_filename.endswith("/core/execution/store.py"):
                break
            transaction = transaction.f_back
        if transaction is None or "original" not in transaction.f_locals:
            return self.trace
        payload, previous = transaction.f_locals.get("payload"), transaction.f_locals["original"]
        if not isinstance(payload, dict) or not any(r.get("kind") == "operation" and key not in previous.get("media", {}) for key, r in payload.get("media", {}).items()):
            return self.trace
        stack = traceback.extract_stack(frame)
        detail = {"type": type(arg[1]).__name__, "code": getattr(arg[1], "code", None),
                  "assertion": [{"file": Path(f.filename).name, "function": f.name, "line": f.lineno,
                                 "source": linecache.getline(f.filename, f.lineno).strip()} for f in stack[-10:]],
                  "clock": sample("validator_exception")}
        self.errors.append(detail)
        if not self.captured:
            try:
                scopes = [r for r in payload["media"].values() if r["kind"] == "scope"]
                if not scopes or not all(s["policy"]["validation_only"] is True and s["policy"]["origin"] == "https://synthetic.invalid" for s in scopes):
                    raise ValueError("Capture only accepts synthetic validation scopes")
                if not all(q["policy"].get("validation_only") is True and urlsplit(q["policy"].get("endpoint", "")).hostname in {"synthetic.invalid", "synthetic.example"} for q in payload["quotes"].values()):
                    raise ValueError("Capture only accepts synthetic text policy")
                write_private(self.output / "candidate-first-error.json", {"payload": copy.deepcopy(payload),
                              "previous": copy.deepcopy(previous), "first_exception": detail,
                              "pytest_current_test": self.pytest_current_test,
                              "clock_samples": copy.deepcopy(self.events), "format": "ordered_json_v1"})
                self.captured = True
            except Exception as capture_error:
                # Collection failure must never replace or clear the validator.
                self.capture_errors.append(type(capture_error).__name__)
        return self.trace
