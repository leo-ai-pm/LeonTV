"""Complete-but-invalid synthetic HTTP over the existing image worker fixture."""
import httpx
from image_tool_fixture import make_transport as image_transport


def make_transport(options):
    original = image_transport(options)

    def handle(request):
        response = original.handle_request(request)
        if options.get("force_request_id"):
            response = httpx.Response(response.status_code, content=response.content,
                headers={"x-request-id": options["force_request_id"]})
        malformed = options.get("malformed")
        if malformed == "html_502":
            return httpx.Response(502, content=b"<html>synthetic gateway error</html>")
        if malformed == "bad_json_200":
            return httpx.Response(200, content=b'{"data":')
        if malformed == "duplicate_json":
            return httpx.Response(200, content=b'{"data":{"id":"a"},"data":{"id":"b"}}')
        return response
    return httpx.MockTransport(handle)
