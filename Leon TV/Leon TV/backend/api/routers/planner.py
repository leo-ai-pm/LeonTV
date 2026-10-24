"""Persistent planning observations and explicit, visibly offline verification."""

import threading
from pathlib import Path as FilePath
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from starlette.routing import Mount

from api.routers.creator import get_brief_store
from api.schemas.planner import CreateOfflineTask, CreateTask, TaskAction
from core.creator.store import CreatorError
from core.planner.store import PlannerError


class SafePlannerRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def safe(request):
            try:
                allowed = set()
                if request.method == "GET":
                    if request.url.path.endswith("/events"):
                        allowed = {"after_sequence", "limit"}
                    elif "task_id" not in request.path_params and "client_request_key" not in request.path_params:
                        allowed = {"offset", "limit", "brief_id"}
                if set(request.query_params) - allowed:
                    raise PlannerError("INVALID_INPUT", "Unknown Planner query field", 422)
                response = await handler(request)
            except RequestValidationError:
                response = JSONResponse({"detail": {"code": "INVALID_INPUT", "message": "Invalid Planner input"}}, status_code=422)
            except CreatorError as exc:
                detail = {"code": exc.code, "message": exc.message}
                if exc.current_revision is not None:
                    detail["current_revision"] = exc.current_revision
                response = JSONResponse({"detail": detail}, status_code=exc.status)
            except Exception:
                response = JSONResponse({"detail": {"code": "PRIVATE_STORAGE_UNAVAILABLE", "message": "Private Planner storage is unavailable"}}, status_code=503)
            response.headers["Cache-Control"] = "no-store"
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["X-LeonTV-Planner-Mode"] = getattr(request.state, "planner_mode", "mixed")
            return response

        return safe


router = APIRouter(prefix="/api/planner", tags=["Brief Planner"], route_class=SafePlannerRoute)
TaskId = Annotated[str, Path(pattern=r"^[0-9a-f]{32}$")]
ClientKey = Annotated[str, Path(pattern=r"^[A-Za-z0-9_-]{8,128}$")]
_runtime, _lock = None, threading.RLock()


def get_planner_runtime():
    global _runtime
    with _lock:
        if _runtime is None:
            from core.execution.bootstrap import build_planner
            _runtime = build_planner(FilePath(__file__).resolve().parents[2] / "harness_data" / "brief_planner", get_brief_store())
        return _runtime


def shutdown_planner_runtime():
    global _runtime
    with _lock:
        if _runtime is not None:
            _runtime.close()
            _runtime = None


def private_runtime(request: Request, runtime=Depends(get_planner_runtime)):
    pending, seen, roots = [request.app], set(), []
    while pending:
        app = pending.pop()
        if id(app) in seen:
            continue
        seen.add(id(app))
        for route in getattr(app, "routes", []):
            if isinstance(route, Mount):
                roots.extend(getattr(route.app, "all_directories", []))
                directory = getattr(route.app, "directory", None)
                if directory:
                    roots.append(directory)
                pending.append(route.app)
    runtime.store.check_private(roots)
    runtime.brief_store.check_private(roots)
    if getattr(runtime, "operator_control", None) is not None:
        runtime.operator_control.check_private(roots)
    return runtime


def with_mode(request, result):
    request.state.planner_mode = result["task"]["mode"]
    return result


@router.post("/tasks")
def create_task(request: Request, body: CreateTask, response: Response, runtime=Depends(private_runtime)):
    result = runtime.create(body.model_dump())
    response.status_code = 200 if result["replayed"] else 201
    return with_mode(request, result)


@router.post("/offline-tasks")
def create_offline_task(request: Request, body: CreateOfflineTask, response: Response, runtime=Depends(private_runtime)):
    result = runtime.create(body.model_dump(), offline=True)
    response.status_code = 200 if result["replayed"] else 201
    return with_mode(request, result)


@router.get("/tasks")
def list_tasks(offset: Annotated[int, Query(ge=0)] = 0, limit: Annotated[int, Query(ge=1, le=100)] = 50,
               brief_id: Annotated[str | None, Query(pattern=r"^[0-9a-f]{32}$")] = None, runtime=Depends(private_runtime)):
    return runtime.list(offset, limit, brief_id)


@router.get("/tasks/by-key/{client_request_key}")
def find_task(request: Request, client_request_key: ClientKey, runtime=Depends(private_runtime)):
    return with_mode(request, runtime.get(key=client_request_key, full=False))


@router.get("/tasks/{task_id}")
def read_task(request: Request, task_id: TaskId, runtime=Depends(private_runtime)):
    return with_mode(request, runtime.get(task_id))


@router.get("/tasks/{task_id}/source")
def read_source(request: Request, task_id: TaskId, runtime=Depends(private_runtime)):
    with_mode(request, runtime.get(task_id, full=False))
    return runtime.source(task_id)


@router.get("/tasks/{task_id}/events")
def read_events(request: Request, task_id: TaskId, after_sequence: Annotated[int, Query(ge=0)] = 0,
                limit: Annotated[int, Query(ge=1, le=200)] = 100, runtime=Depends(private_runtime)):
    with_mode(request, runtime.get(task_id, full=False))
    return runtime.events(task_id, after_sequence, limit)


@router.post("/tasks/{task_id}/actions")
def act(request: Request, task_id: TaskId, body: TaskAction, runtime=Depends(private_runtime)):
    return with_mode(request, runtime.action(task_id, body.model_dump()))
