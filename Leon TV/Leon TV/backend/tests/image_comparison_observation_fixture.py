"""Same synthetic supplier, with missing POST request ID like actual A."""
import httpx
from image_tool_fixture import make_transport as original


def make_transport(options):
    transport = original(options)

    def handle(request):
        response = transport.handle_request(request)
        if request.method == "POST":
            return httpx.Response(response.status_code, content=response.read())
        return response

    return httpx.MockTransport(handle)
