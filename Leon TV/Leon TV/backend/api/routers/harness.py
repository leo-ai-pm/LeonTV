"""Formal zero-paid Harness endpoints; production model clients are not registered."""

import threading
from pathlib import Path as FilePath
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Path, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.routing import APIRoute
from starlette.routing import Mount

from api.schemas.harness import CreateRun, FinalReview, RunAction
from core.harness.runtime import HarnessRuntime
from core.harness.store import HarnessError


class SafeHarnessRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def safe(request):
            try:
                response = await handler(request)
                response.headers["X-LeonTV-Mode"] = "test"
                return response
            except RequestValidationError:
                return JSONResponse({"mode": "test", "detail": {"code": "INVALID_INPUT", "message": "Invalid test Harness input"}}, status_code=422)
            except HarnessError as exc:
                return JSONResponse({"mode": "test", "detail": {"code": exc.code, "message": exc.message}}, status_code=exc.status)
            except Exception:
                return JSONResponse({"mode": "test", "detail": {"code": "PRIVATE_STORAGE_UNAVAILABLE", "message": "Test Harness state is unavailable"}}, status_code=503)

        return safe


router = APIRouter(prefix="/api/harness/runs", tags=["Harness test mode"], route_class=SafeHarnessRoute)
RunId = Annotated[str, Path(pattern=r"^[0-9a-f]{32}$")]
ClientKey = Annotated[str, Path(pattern=r"^[A-Za-z0-9_-]{8,128}$")]
_runtime = None
_runtime_lock = threading.RLock()


def project_exists(project_id):
    from api.dependencies import workflow_engine
    from api.services.studio_store import studio_store

    project = studio_store.get(project_id, workflow_engine)
    return bool(project and not project.get("trashed"))


def get_harness_runtime():
    global _runtime
    with _runtime_lock:
        if _runtime is None:
            root = FilePath(__file__).resolve().parents[2] / "harness_data" / "product_runs"
            _runtime = HarnessRuntime(root, project_reader=project_exists)
        return _runtime


def shutdown_harness_runtime():
    global _runtime
    with _runtime_lock:
        if _runtime is not None:
            _runtime.close()
            _runtime = None


def private_runtime(request: Request, runtime=Depends(get_harness_runtime)):
    static_roots = []
    pending, seen = [request.app], set()
    while pending:
        app = pending.pop()
        if id(app) in seen:
            continue
        seen.add(id(app))
        for route in getattr(app, "routes", []):
            if isinstance(route, Mount):
                static_roots.extend(getattr(route.app, "all_directories", []))
                directory = getattr(route.app, "directory", None)
                if directory:
                    static_roots.append(directory)
                pending.append(route.app)
    runtime.store.check_private(static_roots)
    return runtime


@router.post("")
def create_run(body: CreateRun, response: Response, runtime=Depends(private_runtime)):
    result = runtime.create(body.model_dump())
    response.status_code = 200 if result["replayed"] else 201
    return result


@router.get("")
def list_runs(project_id: Annotated[str | None, Query(pattern=r"^[A-Za-z0-9_-]{1,80}$")] = None,
              runtime=Depends(private_runtime)):
    return runtime.list(project_id)


@router.get("/by-key/{client_request_key}")
def find_run(client_request_key: ClientKey, runtime=Depends(private_runtime)):
    return runtime.get(key=client_request_key)


@router.get("/{run_id}")
def read_run(run_id: RunId, runtime=Depends(private_runtime)):
    return runtime.get(run_id)


@router.get("/{run_id}/events")
def read_events(run_id: RunId, after_sequence: Annotated[int, Query(ge=0)] = 0,
                limit: Annotated[int, Query(ge=1, le=200)] = 100, runtime=Depends(private_runtime)):
    return runtime.events(run_id, after_sequence, limit)


@router.post("/{run_id}/actions")
def act(run_id: RunId, body: RunAction, runtime=Depends(private_runtime)):
    return runtime.action(run_id, body.model_dump())


@router.post("/{run_id}/reviews")
def review(run_id: RunId, body: FinalReview, runtime=Depends(private_runtime)):
    return runtime.review(run_id, body.model_dump())


@router.get("/{run_id}/artifacts/{artifact_id}/media")
def media(request: Request, run_id: RunId, artifact_id: Literal["reference", "shot", "final"],
          version: Annotated[int, Query(ge=1)], runtime=Depends(private_runtime)):
    if set(request.query_params) - {"version"}:
        raise HarnessError("INVALID_INPUT", "Test media accepts a version, not a file path", 422)
    path = runtime.media(run_id, artifact_id, version)
    return FileResponse(path, media_type="video/mp4", filename=f"test-fixture-{artifact_id}-v{version}.mp4",
                        headers={"X-LeonTV-Mode": "test", "Cache-Control": "no-store"})
