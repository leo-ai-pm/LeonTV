import httpx
import pytest

from api.app import app
from api.routers import workflow
from config import _coerce_config
from core.orchestrator import WorkflowEngine


@pytest.mark.asyncio
@pytest.mark.parametrize("method,path,body,status", [
    ("POST", "/api/project/%2E%2E/execute/script_generation", {}, 400),
    ("DELETE", "/api/sessions/%2E%2E", None, 400),
    ("DELETE", "/api/tasks/%2E%2E", None, 400),
    ("POST", "/api/project/missing/execute/not-a-stage", {}, 400),
    ("POST", "/api/project/missing/execute/script_generation", {}, 404),
    ("POST", "/api/project/missing/execute/script_generation", [], 400),
    ("POST", "/api/project/missing/stop", {}, 404),
])
async def test_full_app_rejects_invalid_requests_before_creating_state(tmp_path, monkeypatch, method, path, body, status):
    engine = WorkflowEngine(session_dir=str(tmp_path))
    monkeypatch.setattr(workflow, "workflow_engine", engine)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.request(method, path, json=body)
    assert response.status_code == status
    assert engine.sessions == {}
    assert not list(tmp_path.iterdir())


@pytest.mark.asyncio
async def test_api_service_and_openapi_use_current_brand():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get("/")).json()["service"] == "Leon TV"
        assert (await client.get("/openapi.json")).json()["info"]["title"] == "Leon TV"


@pytest.mark.parametrize("section", ["server", "api_providers", "models", "generation"])
def test_invalid_config_sections_fail_validation_without_writing(section):
    with pytest.raises(ValueError, match="mapping"):
        _coerce_config({section: None})


def test_legacy_config_brand_is_normalized_in_memory():
    assert _coerce_config({"project_name": "小云雀"})["project_name"] == "Leon TV"
