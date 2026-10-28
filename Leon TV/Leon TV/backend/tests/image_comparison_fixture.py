"""A synthetic 403 supplier over the original detached image worker."""

import httpx
from image_tool_fixture import make_transport as original_transport


def make_transport(options):
    original = original_transport({**options, "fault": None})

    def handle(request):
        original.handle_request(request)  # Preserve exact synthetic call counter.
        assert request.method == "POST"
        if options.get("comparison_fault") == "html502":
            return httpx.Response(502, content=b"<html>SYNTHETIC UPSTREAM ERROR</html>")
        if options.get("comparison_fault") == "invalid200":
            return httpx.Response(200, content=b"{invalid-SYNTHETIC-json")
        return httpx.Response(
            403,
            json={"code": "fail_to_fetch_task", "message": "", "data": None},
            headers={"x-request-id": "synthetic-403-" + request.headers["x-client-operation-id"]},
        )

    return httpx.MockTransport(handle)
