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
from core.execution.image_response_lifecycle import RESPONSE_KINDS, response_deadline
from core.execution.media_failure_phase import capture_stage
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
            self._preflight(operation, policy)
            payload = self.execution_store.read_only()
            from core.execution.media_ledger import (
                account_halted,
                has_pending_fees,
                has_pending_unknown,
            )
            require(not account_halted(payload, operation["account_id"]) and not has_pending_unknown(payload, operation["account_id"]) and not has_pending_fees(payload, operation["account_id"]))
            persisted = payload["operations"][operation["id"]]
            require(digest(persisted) == digest(operation) and persisted["status"] == "submitting")
            require(payload["quotes"][operation["quote_id"]]["policy"] == policy)
        except ResponseUnconfirmed:
            raise
        except Exception:
            raise ResponseUnconfirmed("PERMIT_REJECTED") from None
        settings = self.settings
        job = {"operation": copy.deepcopy(operation), "policy": copy.deepcopy(policy),
            "worker_factory": settings.worker_factory, "worker_options": copy.deepcopy(settings.worker_options)}
        return capture_once(self.receipts, binding_for(operation), job, api_key, self.settings)


def capture_once(receipt_store, binding, frozen_job, api_key, settings):
    """Internal delivery seam. The server adapter must verify authority first."""
    capture_stage("collector_ipc")
    deadline = response_deadline(binding)
    require(binding.get("kind") not in RESPONSE_KINDS or settings.cleanup_grace_seconds == 5)
    observer_until = min(deadline, now() + timedelta(seconds=settings.observation_wait_seconds)) if settings.observation_wait_seconds is not None else deadline
    cutoff = deadline + timedelta(seconds=settings.cleanup_grace_seconds)
    job = copy.deepcopy(frozen_job)
    capability = secrets.token_hex(32)
    ipc = json.dumps({"capability": capability, "api_key": api_key, "job": job}, ensure_ascii=False).encode("utf-8")
    require(len(ipc) <= MAX_IPC)
    capture_stage("collector_prepare")
    op, new = receipt_store.prepare(binding, digest(job), hashlib.sha256(capability.encode()).hexdigest(), observer_until.isoformat(), cutoff.isoformat())
    if not new:
        capture_stage("collector_existing_claim")
        op.claim(binding)
        raise ObservationEnded("EXISTING_CAPTURE_REQUIRES_INSPECTION")
    if new:
        backend = Path(__file__).resolve().parents[2]
        environment = {key: os.environ[key] for key in ["PATH", "LANG", "LC_ALL", "TMPDIR", "SYSTEMROOT", "LTV_TEST_NETWORK_LOG"] if key in os.environ}
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        # Factory selection is server-owned; tests supply synthetic source modules.
        environment["PYTHONPATH"] = os.pathsep.join([str(backend), str(backend / "tests")])
        capture_stage("worker_launch")
        process = subprocess.Popen([sys.executable, "-m", "core.execution.receipt_worker", str(receipt_store.root), binding["operation_id"]],
            cwd=backend, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True, close_fds=True, env=environment)

        def feed_and_reap():
            try:
                process.stdin.write(ipc)
                process.stdin.close()
            except (OSError, ValueError):
                pass
            process.wait()

        capture_stage("worker_feed")
        threading.Thread(target=feed_and_reap, name="receipt-ipc-reaper", daemon=True).start()
    capture_stage("collector_observe")
    while now() < observer_until and now() < deadline:
        raw, summary = receipt_store.ready_raw_binding(binding["operation_id"], binding)
        if raw is not None and now() < observer_until and now() < deadline:
            return raw
        if summary["status"] == "capture_error":
            error = op.read("error.json")["code"]
            raise ResponseUnconfirmed(error)
        time.sleep(min(0.01, max(0.001, (observer_until - now()).total_seconds())))
    reason = "absolute_deadline" if now() >= deadline else "observation_ended"
    capture_stage("collector_close")
    try:
        op.write_new("observer-closed.json", seal({"version": 1, "binding_hash": digest(binding), "closed_at": utc_now(), "reason": reason}, "closed_hash"))
    except Exception:
        pass
    if now() >= deadline:
        raise ResponseUnconfirmed("OVERALL_DEADLINE")
    raise ObservationEnded("OBSERVATION_ENDED")
