"""One detached, bounded capture worker. Credentials arrive only on stdin."""
import base64
import hashlib
import importlib
import json
import os
import sys
import threading
import time
from pathlib import Path

from core.creator.store import digest, utc_now
from core.execution.policy import now
from core.execution.receipts import (
    MAX_BODY,
    MAX_IPC,
    ReceiptError,
    ReceiptStore,
    binding_for,
    credential_echo,
    require,
    seal,
    timestamp,
)
from core.execution.transport import ResponseUnconfirmed, unconfirmed_code


def collect(store, claim, job):
    import httpx

    if "kind" not in job:
        operation, policy = job["operation"], job["policy"]
        binding = binding_for(operation)
        endpoint, wire, validation_only = policy["endpoint"], operation["wire"], policy["validation_only"]
    else:
        from experiments.planner_live.empirical_capture import worker_request

        binding, endpoint, wire, validation_only = worker_request(job, claim)
    require(binding == claim["binding"])
    deadline, cutoff = timestamp(binding["deadline_at"]), timestamp(claim["cleanup_cutoff"])
    if now() >= deadline:
        store.error(binding, "OVERALL_DEADLINE")
        return
    factory = job["worker_factory"]
    transport = None
    if factory is not None:
        module, attribute = factory.split(":")
        transport = getattr(importlib.import_module(module), attribute)(job["worker_options"])
    require(not validation_only or isinstance(transport, httpx.MockTransport))
    if now() >= deadline:
        store.error(binding, "OVERALL_DEADLINE")
        return
    started = seal({"version": 1, "binding_hash": digest(binding), "worker_pid": os.getpid(), "dispatched_at": utc_now()}, "started_hash")
    if not store.write_new("started.json", started):
        return

    def submit_boundary(request):
        if now() >= deadline:
            raise ResponseUnconfirmed("OVERALL_DEADLINE")

    client = None
    record_id = None
    try:
        client = httpx.Client(transport=transport, timeout=max(0.001, (cutoff - now()).total_seconds()), follow_redirects=False,
            trust_env=False, event_hooks={"request": [submit_boundary]})
        with client.stream("POST", endpoint, content=wire.encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": "Bearer " + job["api_key"], "X-Client-Operation-Id": binding["operation_id"]}) as response:
            data = bytearray()
            for chunk in response.iter_bytes():
                if now() >= cutoff:
                    raise ResponseUnconfirmed("OVERALL_DEADLINE")
                data.extend(chunk)
                if len(data) > MAX_BODY:
                    raise ResponseUnconfirmed("RECEIPT_TOO_LARGE")
            raw = {"http_status": response.status_code,
                "headers": {key.lower(): value for key, value in response.headers.items() if key.lower() == "x-request-id"},
                "body_base64": base64.b64encode(data).decode("ascii"), "received_at": utc_now()}
            if credential_echo(raw, job["api_key"]):
                store.error(binding, "CREDENTIAL_ECHO")
                return
            record_id = store.commit(binding, raw, job["api_key"])
        client.close()
        client = None
        # Fault tests interrupt here after complete commit, before notification.
        store.notify(binding, record_id)
    except Exception as error:
        store.error(binding, "PERSISTENCE_FAILED" if isinstance(error, (ReceiptError, OSError)) else unconfirmed_code(error))
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass


def main():
    store, claim = None, None
    try:
        require(len(sys.argv) == 3)
        root, operation_id = Path(sys.argv[1]).absolute(), sys.argv[2]
        store = ReceiptStore(root).operation(operation_id)
        claim = store.claim()
        # This timer exists before stdin is read and survives the observer.
        cutoff = timestamp(claim["cleanup_cutoff"])
        elapsed_limit = time.monotonic() + min(150, max(0, (cutoff - now()).total_seconds()))

        def watchdog():
            while True:
                remaining = min((cutoff - now()).total_seconds(), elapsed_limit - time.monotonic())
                if remaining <= 0:
                    os._exit(70)
                threading.Event().wait(min(0.05, remaining))

        threading.Thread(target=watchdog, name="receipt-cutoff-watchdog", daemon=True).start()
        store.write_new("worker.json", seal({"version": 1, "binding_hash": claim["binding_hash"], "worker_pid": os.getpid(), "booted_at": utc_now()}, "worker_hash"))
        data = sys.stdin.buffer.read(MAX_IPC + 1)
        require(len(data) <= MAX_IPC)
        value = json.loads(data)
        require(set(value) == {"capability", "api_key", "job"})
        require(hashlib.sha256(value["capability"].encode()).hexdigest() == claim["capability_hash"])
        require(digest(value["job"]) == claim["payload_hash"])
        require(isinstance(value["api_key"], str) and bool(value["api_key"]))
        job = {**value["job"], "api_key": value["api_key"]}
        collect(store, claim, job)
    except Exception:
        if store is not None and claim is not None:
            try:
                store.error(claim["binding"], "WORKER_INPUT_REJECTED")
            except Exception:
                pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
