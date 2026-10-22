"""Explicit detached capture lane; old ControlledTransport remains unchanged."""
import copy
import hashlib
import json
import os
import secrets
import subprocess
import sys
import threading
import time
from datetime import timedelta
from pathlib import Path

from core.creator.store import digest, utc_now
from core.execution.policy import now
from core.execution.receipts import (
    MAX_IPC,
    ObservationEnded,
    ReceiptStore,
    binding_for,
    require,
    seal,
)
from core.execution.transport import ControlledTransport, ResponseUnconfirmed


class CapturedTransport(ControlledTransport):
    def __init__(self, transport, input_bounder, execution_store, settings):
        super().__init__(transport, input_bounder)
        self.execution_store, self.settings = execution_store, settings
        self.receipts = ReceiptStore(execution_store.root / "receipts")

    def perform(self, operation, policy, api_key):
        try:
            _, deadline = self._preflight(operation, policy)
            payload = self.execution_store.read_only()
            persisted = payload["operations"][operation["id"]]
            require(digest(persisted) == digest(operation) and persisted["status"] == "submitting")
            require(payload["quotes"][operation["quote_id"]]["policy"] == policy)
        except ResponseUnconfirmed:
            raise
        except Exception:
            raise ResponseUnconfirmed("PERMIT_REJECTED") from None
        settings = self.settings
        observer_until = min(deadline, now() + timedelta(seconds=settings.observation_wait_seconds)) if settings.observation_wait_seconds is not None else deadline
        cutoff = deadline + timedelta(seconds=settings.cleanup_grace_seconds)
        job = {"operation": copy.deepcopy(operation), "policy": copy.deepcopy(policy),
            "worker_factory": settings.worker_factory, "worker_options": copy.deepcopy(settings.worker_options)}
        capability = secrets.token_hex(32)
        ipc = json.dumps({"capability": capability, "api_key": api_key, "job": job}, ensure_ascii=False).encode("utf-8")
        require(len(ipc) <= MAX_IPC)
        binding = binding_for(operation)
        op, new = self.receipts.prepare(binding, digest(job), hashlib.sha256(capability.encode()).hexdigest(), observer_until.isoformat(), cutoff.isoformat())
        if not new:
            op.claim(binding)
            raise ObservationEnded("EXISTING_CAPTURE_REQUIRES_INSPECTION")
        if new:
            backend = Path(__file__).resolve().parents[2]
            environment = {key: os.environ[key] for key in ["PATH", "LANG", "LC_ALL", "TMPDIR", "SYSTEMROOT", "LTV_TEST_NETWORK_LOG"] if key in os.environ}
            environment["PYTHONDONTWRITEBYTECODE"] = "1"
            # Factory selection is server-owned; tests supply synthetic source modules.
            environment["PYTHONPATH"] = os.pathsep.join([str(backend), str(backend / "tests")])
            process = subprocess.Popen([sys.executable, "-m", "core.execution.receipt_worker", str(self.receipts.root), operation["id"]],
                cwd=backend, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True, close_fds=True, env=environment)

            def feed_and_reap():
                try:
                    process.stdin.write(ipc)
                    process.stdin.close()
                except (OSError, ValueError):
                    pass
                process.wait()

            threading.Thread(target=feed_and_reap, name="receipt-ipc-reaper", daemon=True).start()
        while now() < observer_until and now() < deadline:
            raw, summary = self.receipts.ready_raw(operation)
            if raw is not None and now() < observer_until and now() < deadline:
                return raw
            if summary["status"] == "capture_error":
                error = op.read("error.json")["code"]
                raise ResponseUnconfirmed(error)
            time.sleep(min(0.01, max(0.001, (observer_until - now()).total_seconds())))
        reason = "absolute_deadline" if now() >= deadline else "observation_ended"
        try:
            op.write_new("observer-closed.json", seal({"version": 1, "binding_hash": digest(binding), "closed_at": utc_now(), "reason": reason}, "closed_hash"))
        except Exception:
            pass
        if now() >= deadline:
            raise ResponseUnconfirmed("OVERALL_DEADLINE")
        raise ObservationEnded("OBSERVATION_ENDED")
