"""Independent native media regression probes with fictional public fixtures."""
import time

import httpx
from image_tool_fixture import make_transport
from media_ledger_fixture import authorize, media_case, posts, reserve
from test_execution_empirical import empirical_env as empirical_env

from core.execution.image_policy import FrozenImageRequest
from core.execution.receipts import ReceiptCaptureSettings


def malformed_transport(options):
    """Record one mock POST, then return a complete non-JSON HTTP response."""
    original = make_transport(options)

    def handle(request):
        original.handle_request(request)
        return httpx.Response(502, content=b"<html>synthetic gateway error</html>",
                              headers={"x-request-id": "synthetic-original-request"})

    return httpx.MockTransport(handle)


def test_existing_media_scope_does_not_corrupt_a_new_text_intent(empirical_env):
    case = media_case(empirical_env)
    authorize(case)
    env = case.env
    task = env.source_task("receipt-audit-002")
    quote = env.quote(task, key="receipt-audit-quote-002")
    grant = env.grant(task, quote, key="receipt-audit-grant-002")
    baseline_calls = len(env.calls())
    errors = []
    worker = env.runtime.worker

    def monitored(*args):
        try:
            worker(*args)
        except Exception as error:
            errors.append(error)

    env.runtime.worker = monitored
    result = env.action(task, grant["execution"], key="receipt-audit-resume-002")
    assert result.status_code == 200
    deadline = time.monotonic() + 5
    state = None
    while time.monotonic() < deadline and not errors:
        state = env.runtime.store.read_only()["tasks"][task["id"]]
        if state["status"] in {"blocked", "needs_reconciliation"}:
            break
        time.sleep(.01)
    assert not errors, [(type(error).__name__, getattr(error, "code", None)) for error in errors]
    assert state["status"] in {"blocked", "needs_reconciliation"}
    assert len(env.calls()) == baseline_calls + 1
    assert case.media.get(case.task["id"])["media"]["scope_count"] == 1


def test_complete_malformed_http_remains_durable_unknown_and_never_reposts(empirical_env):
    case = media_case(empirical_env)
    _, grant = authorize(case)
    operation = reserve(case, grant)
    case.media.capture.settings = ReceiptCaptureSettings(observation_wait_seconds=5,
        worker_factory="test_media_receipt_audit:malformed_transport", worker_options={"counter": str(case.counter)})
    result = case.media.submit(case.task["id"], operation["id"])
    assert result["operation"]["status"] == "unknown"
    assert result["operation"]["raw"] is result["operation"]["receipt"] is None
    descriptor = FrozenImageRequest.from_frozen(operation["descriptor"])
    raw, inspection = case.media.capture.ready_raw(descriptor)
    assert inspection["status"] == "candidate_found"
    assert raw["http_status"] == 502
    assert case.media.recover(case.task["id"], operation["id"])["operation"]["status"] == "unknown"
    assert case.media.submit(case.task["id"], operation["id"])["replayed"] is True
    assert len(posts(case)) == 1
    assert case.media.get(case.task["id"])["media"]["risk_released"] is False
