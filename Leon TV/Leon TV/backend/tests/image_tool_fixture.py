"""Detached image HTTP fixtures: synthetic MockTransport only, never real I/O."""
import json
import os
import sys
import time
from pathlib import Path

import httpx

from core.creator.store import utc_now
from core.execution.receipts import ReceiptStore


def make_transport(options):
    fault = options.get("fault")
    time.sleep(options.get("factory_delay", 0))
    if fault == "before_commit":
        ReceiptStore.commit = lambda *args, **kwargs: os._exit(81)
    elif fault == "after_commit":
        ReceiptStore.notify = lambda *args, **kwargs: os._exit(82)
    elif fault in {"before_seal", "partial_ready", "persist_fail"}:
        original = ReceiptStore.write_new

        def interrupted(self, name, value):
            if fault == "before_seal" and name.endswith(".commit.json"):
                os._exit(83)
            if fault == "partial_ready" and name.endswith(".ready.json"):
                self.path(name).write_bytes(b"{")
                os._exit(84)
            if fault == "persist_fail" and name.endswith(".record.json"):
                raise OSError("SYNTHETIC IMAGE PERSISTENCE FAILURE")
            return original(self, name, value)

        ReceiptStore.write_new = interrupted

    def handle(request):
        assert request.url.host == "synthetic.invalid"
        counter = Path(options["counter"])
        record = {"pid": os.getpid(), "method": request.method, "url": str(request.url),
                  "operation_id": request.headers["x-client-operation-id"], "wire": request.content.decode("utf-8"),
                  "credential_in_environment": bool(os.environ.get("SYNTHETIC_IMAGE_KEY")), "received_at": utc_now()}
        with counter.open("a") as stream:
            stream.write(json.dumps(record) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        if options.get("release"):
            while not Path(options["release"]).exists():
                time.sleep(0.01)
        time.sleep(options.get("delay", 0))
        if fault == "unknown":
            raise httpx.ReadTimeout("SYNTHETIC IMAGE TRANSPORT FAILURE")
        if fault == "oversize":
            return httpx.Response(200, content=b"x" * options.get("response_bytes", 1048577))
        if fault == "broken_stream":
            class Broken(httpx.SyncByteStream):
                def __iter__(self):
                    yield b'{"data":'
                    raise httpx.ReadError("SYNTHETIC INCOMPLETE IMAGE STREAM")
            return httpx.Response(200, stream=Broken())
        if fault == "credential_echo":
            return httpx.Response(200, json={"credential": request.headers["authorization"].removeprefix("Bearer ")})
        if fault == "redirect":
            return httpx.Response(302, headers={"location": "https://synthetic.invalid/forbidden-second-request"})
        task_id = "synthetic-image-task-1"
        if request.method == "POST":
            assert request.url.path == "/v1/images/generations/async"
            assert json.loads(request.content)["model"] == "gpt-image-2-all"
            body = {"code": 0, "data": {"id": task_id}}
        else:
            assert request.method == "GET" and request.content == b""
            assert request.url.path == "/v1/tasks/" + task_id
            body = {"code": 0, "data": {"id": "different-image-task" if fault == "wrong_task" else task_id,
                    "status": options.get("query_status", "completed"),
                    "result": {"images": [{"url": "https://synthetic.invalid/image.png?synthetic-expiry=1"}]}}}
        return httpx.Response(200, json=body, headers={"x-request-id": "image-" + request.headers["x-client-operation-id"]})

    return httpx.MockTransport(handle)


def observer():
    """A disposable observer process; its detached worker survives termination."""
    from core.execution.image_capture import ControlledImageCapture
    from core.execution.image_policy import FrozenImageRequest
    from core.execution.receipts import ObservationEnded, ReceiptCaptureSettings
    from core.execution.transport import ResponseUnconfirmed

    root, descriptor_path, options = Path(sys.argv[1]), Path(sys.argv[2]), json.loads(sys.argv[3])
    request = FrozenImageRequest.from_frozen(json.loads(descriptor_path.read_text()))
    adapter = ControlledImageCapture(root / "receipts", authority_verifier=lambda candidate: candidate == request.frozen(),
        settings=ReceiptCaptureSettings(observation_wait_seconds=4, worker_factory="image_tool_fixture:make_transport",
            worker_options={"counter": str(root / "requests.jsonl"), **options}))
    (root / "observer.json").write_text(json.dumps({"pid": os.getpid(), "capture_id": request.binding()["capture_id"]}))
    try:
        adapter.capture(request, "SYNTHETIC-IMAGE-PRIVATE-KEY")
    except (ObservationEnded, ResponseUnconfirmed):
        pass
    (root / "observer-ended").write_text("observer does not grant or retry")
    time.sleep(30)


if __name__ == "__main__":
    observer()
