"""A fictional four-shot Planner response through the real synthetic worker."""
import copy
import json

import httpx
from empirical_product_fixture import make_transport as original_transport


def make_transport(options):
    transport = original_transport(options)

    def handle(request):
        response = transport.handle_request(request)
        value = response.json()
        message = value["choices"][0]["message"]
        content = json.loads(message["content"])
        if content.get("tool") == "submit_shot_plan":
            plan = content["arguments"]["plan"]
            templates = plan["shots"]
            shots = []
            for index in range(4):
                shot = copy.deepcopy(templates[min(index, len(templates) - 1)])
                shot.update(id=f"S{index + 1:02d}", duration_ms=3750,
                    depends_on=[] if index == 0 else [f"S{index:02d}"])
                shots.append(shot)
            plan["shots"] = shots
            message["content"] = json.dumps(content, ensure_ascii=False)
        return httpx.Response(response.status_code, json=value, headers=dict(response.headers))

    return httpx.MockTransport(handle)
