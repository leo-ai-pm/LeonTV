"""Trusted synthetic worker dependency; no real HTTP or production state."""
import json
import os
import threading
import time
from pathlib import Path

import httpx

from core.creator.store import utc_now
from core.execution.receipts import ReceiptStore
from core.planner.actors import OfflineActor


def make_transport(options):
    fault = options.get("fault")
    time.sleep(options.get("factory_delay", 0))
    if fault == "before_commit":
        ReceiptStore.commit = lambda *a, **k: os._exit(81)
    elif fault == "after_commit":
        ReceiptStore.notify = lambda *a, **k: os._exit(82)
    elif fault in {"fsync_fail", "crash_before_link", "crash_before_seal", "partial_ready"}:
        original = ReceiptStore.write_new

        def interrupted(self, name, value):
            if fault == "crash_before_seal" and name.endswith(".commit.json"):
                os._exit(85)
            if fault == "partial_ready" and name.endswith(".ready.json"):
                self.path(name).write_bytes(b"{")
                os._exit(86)
            if name.endswith(".record.json") and fault in {"fsync_fail", "crash_before_link"}:
                old_fsync, old_link = os.fsync, os.link
                try:
                    if fault == "fsync_fail":
                        def fail_sync(fd):
                            raise OSError("SYNTHETIC-FSYNC-FAILURE")
                        os.fsync = fail_sync
                    else:
                        os.link = lambda *a, **k: os._exit(84)
                    return original(self, name, value)
                finally:
                    os.fsync, os.link = old_fsync, old_link
            return original(self, name, value)

        ReceiptStore.write_new = interrupted
    elif fault == "persist_fail":
        original = ReceiptStore.write_new

        def fail(self, name, value):
            if name.endswith(".record.json"):
                raise OSError("SYNTHETIC-PRIVATE-PERSISTENCE-ERROR")
            return original(self, name, value)

        ReceiptStore.write_new = fail
    for method, option in [("commit", "commit_delay"), ("notify", "notify_delay")]:
        if options.get(option):
            original = getattr(ReceiptStore, method)

            def delayed(self, *args, _original=original, _delay=options[option], **kwargs):
                time.sleep(_delay)
                return _original(self, *args, **kwargs)

            setattr(ReceiptStore, method, delayed)
    if fault == "factory_blocks":
        threading.Event().wait(60)

    def handle(request):
        counter = Path(options["counter"])
        with counter.open("a") as stream:
            stream.write(json.dumps({"pid": os.getpid(), "operation_id": request.headers["x-client-operation-id"], "posted_at": utc_now(),
                "credential_in_environment": bool(os.environ.get("SYNTHETIC_UPSTREAM_KEY"))}) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        if options.get("release"):
            while not Path(options["release"]).exists():
                time.sleep(0.01)
        time.sleep(options.get("delay", 0))
        if fault in {"clock_forward", "clock_backward"}:
            import sys
            from datetime import timedelta

            from core.execution.policy import now
            worker = sys.modules["__main__"]
            assert worker.__spec__.name == "core.execution.receipt_worker"
            shift = 3600 if fault == "clock_forward" else -3600
            with Path(options["counter"] + ".clock").open("w") as trace:
                trace.write(json.dumps({"main_module": worker.__spec__.name, "shift_seconds": shift}))
                trace.flush()
                os.fsync(trace.fileno())
            worker.now = lambda: now() + timedelta(seconds=shift)
            threading.Event().wait(60)
        if fault == "blocks":
            threading.Event().wait(60)
        if fault == "read_error":
            raise httpx.ReadTimeout("SYNTHETIC-PRIVATE-TRANSPORT-ERROR")
        if fault == "oversize":
            return httpx.Response(200, content=b"x" * 1048577)
        if fault == "truncated_stream":
            class Broken(httpx.SyncByteStream):
                def __iter__(self):
                    yield b'{"incomplete":'
                    raise httpx.ReadError("SYNTHETIC-PRIVATE-TRUNCATION")
            return httpx.Response(200, stream=Broken())
        body = json.loads(request.content)
        messages = []
        for message in body.get("messages", body.get("input", [])):
            value = json.loads(message["content"])
            role = "tool" if isinstance(value, dict) and "ok" in value else message["role"]
            messages.append({"role": role, "content": value})
        text = OfflineActor().respond(messages, {"id": "synthetic"}, {"scenario": "success"})
        key = request.headers["authorization"].removeprefix("Bearer ")
        if fault == "key_inner_action":
            inner = json.dumps({"tool": "submit_shot_plan", "arguments": {"plan": {"visual": key}}}).replace(key, ''.join('\\u%04x' % ord(char) for char in key))
            return httpx.Response(200, json={"choices": [{"message": {"content": inner}}]}, headers={"x-request-id": "req_synthetic"})
        if fault in {"key_body", "key_escaped"}:
            raw = json.dumps({"secret": key}) if fault == "key_body" else '{"secret":"' + ''.join('\\u%04x' % ord(char) for char in key) + '"}'
            return httpx.Response(200, content=raw, headers={"x-request-id": "req_synthetic"})
        headers = {"x-request-id": key if fault == "key_header" else "req_" + request.headers["x-client-operation-id"]}
        if "input" in body:
            value = {"object": "response", "id": "resp_" + request.headers["x-client-operation-id"], "model": body["model"], "status": "completed",
                "output": [{"id": "msg_synthetic", "type": "message", "role": "assistant", "status": "completed",
                    "content": [{"type": "output_text", "text": text, "annotations": [], "logprobs": []}]}],
                "usage": {"input_tokens": 10, "output_tokens": 20, "total_tokens": 30}}
        else:
            value = {"id": "resp_" + request.headers["x-client-operation-id"], "model": body["model"],
                "choices": [{"message": {"content": text}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30}}
        return httpx.Response(500 if fault == "http500" else 200, json=value, headers=headers)

    return httpx.MockTransport(handle)
