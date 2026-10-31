"""Synthetic supplier returns a public-host-shaped URL; never real DNS/HTTP."""
import httpx
from image_tool_fixture import make_transport as original


def make_transport(options):
    transport = original(options)

    def handle(request):
        response = transport.handle_request(request)
        if request.method == "GET":
            body = response.json()
            body["data"]["result"]["images"][0]["url"] = "https://media.example.com/image.png?synthetic=1"
            return httpx.Response(response.status_code, json=body, headers=dict(response.headers))
        return response

    return httpx.MockTransport(handle)
