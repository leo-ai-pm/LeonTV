"""One image HTTP tool call over the existing detached receipt collector.

No scheduler or financial ledger lives here. A trusted native controller must
verify its durable media reservation before every new POST or bounded GET.
"""
import copy
from pathlib import Path

from core.creator.store import CreatorError, digest
from core.execution.media_failure_phase import capture_stage
from core.execution.policy import now
from core.execution.receipt_transport import capture_once
from core.execution.receipts import ReceiptCaptureSettings, ReceiptStore, timestamp
from core.planner.review import json_copy

KIND = "controlled_image_http_v1"
KINDS = {KIND, "controlled_image_http_v2"}


def rejected():
    return CreatorError("MEDIA_TOOL_UNAVAILABLE", "Explicit durable media authority is required", 409)


def require(value):
    if not value:
        raise rejected()


def checked_request(request):
    from core.execution.image_policy import FrozenImageRequest
    try:
        require(type(request) is FrozenImageRequest)
        return FrozenImageRequest.from_frozen(request.frozen())
    except Exception:
        raise rejected() from None


class ControlledImageCapture:
    """Disabled without a strict independent authority verifier; inspect is read-only."""
    def __init__(self, receipt_root, *, authority_verifier=None, settings=None, static_roots=()):
        self.authority_verifier = authority_verifier
        selected = settings if settings is not None else ReceiptCaptureSettings()
        require(type(selected) is ReceiptCaptureSettings and selected.cleanup_grace_seconds == 5)
        self.settings = ReceiptCaptureSettings(selected.observation_wait_seconds, 5,
            selected.worker_factory, copy.deepcopy(selected.worker_options))
        self.receipts = ReceiptStore(receipt_root)
        backend = Path(__file__).resolve().parents[2]
        self.receipts.check_private((backend / "code", backend.parent / "frontend/public", *static_roots))

    def capture(self, request, api_key):
        capture_stage("image_descriptor")
        request = checked_request(request)
        try:
            capture_stage("image_authority")
            require(callable(self.authority_verifier))
            candidate = request.frozen()
            before = digest(candidate)
            permitted = self.authority_verifier(candidate)
            # A tuple/list or non-JSON substitution must not survive a digest
            # that serializes both containers to the same JSON representation.
            json_copy(candidate, limit=8 * 1048576)
            require(permitted is True and digest(candidate) == before)
            capture_stage("image_credential")
            require(type(api_key) is str and bool(api_key) and len(api_key.encode("utf-8")) <= 16384)
            capture_stage("image_deadline")
            require(now() < timestamp(request.deadline_at))
            capture_stage("image_environment")
            if request.validation_only:
                require(self.settings.worker_factory is not None)
            else:
                require(self.settings.worker_factory is None and not self.settings.worker_options)
        except Exception:
            raise rejected() from None
        job = {"kind": request.frozen()["kind"], "descriptor": request.frozen(), "binding": request.binding(),
               "worker_factory": self.settings.worker_factory, "worker_options": copy.deepcopy(self.settings.worker_options)}
        # Existing claims are inspect-only. capture_once never dispatches twice.
        return capture_once(self.receipts, request.binding(), job, api_key, self.settings)

    def inspect(self, request):
        request = checked_request(request)
        binding = request.binding()
        return {"inspection": self.receipts.inspect_binding(binding["operation_id"], binding)}

    def ready_raw(self, request):
        """Read committed evidence only; never adopt ambiguous or late receipts."""
        request = checked_request(request)
        binding = request.binding()
        return self.receipts.ready_raw_binding(binding["operation_id"], binding)


def worker_request(job, claim):
    """Validate the image-only method/endpoint/body before the shared HTTP call."""
    try:
        from core.execution.image_policy import FrozenImageRequest
        require(type(job) is dict and set(job) == {"kind", "descriptor", "binding", "worker_factory", "worker_options", "api_key"})
        require(job["kind"] in KINDS)
        request = FrozenImageRequest.from_frozen(job["descriptor"])
        binding = request.binding()
        require(job["kind"] == binding["kind"] == request.frozen()["kind"])
        require(job["binding"] == claim["binding"] == binding)
        require((timestamp(claim["cleanup_cutoff"]) - timestamp(binding["deadline_at"])).total_seconds() == 5)
        require(timestamp(request.frozen()["permit"]["issued_at"]) <= timestamp(claim["created_at"]))
        if not request.validation_only:
            require(job["worker_factory"] is None and not job["worker_options"])
        return binding, request.endpoint, request.wire, request.validation_only, request.method, request.max_response_bytes
    except Exception:
        raise rejected() from None
