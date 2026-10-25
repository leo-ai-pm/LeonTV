"""D09 controlled execution. Server bootstrap is disabled and holds no API key."""
import threading
from pathlib import Path as FilePath
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from starlette.routing import Mount

from api.routers.planner import get_planner_runtime
from core.creator.store import CreatorError
from core.execution.policy import ExecutionAction, GrantInput, QuoteInput, RevokeInput


class SafeExecutionRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()
        async def safe(request):
            try:
                allowed = {"after_sequence", "limit"} if request.method == "GET" and request.url.path.endswith("/events") else set()
                if set(request.query_params) - allowed:
                    raise CreatorError("INVALID_INPUT", "Unknown execution query field", 422)
                response = await handler(request)
            except RequestValidationError:
                response = JSONResponse({"detail": {"code": "INVALID_INPUT", "message": "Invalid execution input"}}, status_code=422)
            except CreatorError as error:
                detail = {"code": error.code, "message": error.message}
                if error.current_revision is not None:
                    detail["current_revision"] = error.current_revision
                response = JSONResponse({"detail": detail}, status_code=error.status)
            except Exception:
                response = JSONResponse({"detail": {"code": "PRIVATE_STORAGE_UNAVAILABLE", "message": "Private execution storage is unavailable"}}, status_code=503)
            response.headers["Cache-Control"] = "no-store"
            response.headers["X-Content-Type-Options"] = "nosniff"
            return response
        return safe


router = APIRouter(prefix="/api/planner", tags=["Controlled Planner execution"], route_class=SafeExecutionRoute)
TaskId = Annotated[str, Path(pattern=r"^[0-9a-f]{32}$")]
GrantId = Annotated[str, Path(pattern=r"^[0-9a-f]{32}$")]
ClientKey = Annotated[str, Path(pattern=r"^[A-Za-z0-9_-]{8,128}$")]
_runtime, _lock = None, threading.RLock()


def get_execution_runtime():
    global _runtime
    with _lock:
        if _runtime is None:
            from core.execution.bootstrap import build_execution_runtime
            _runtime = build_execution_runtime(FilePath(__file__).resolve().parents[2] / "harness_data" / "planner_execution", get_planner_runtime())
        return _runtime


def shutdown_execution_runtime():
    global _runtime
    with _lock:
        if _runtime is not None:
            _runtime.close()
            _runtime = None


def private_execution(request: Request, runtime=Depends(get_execution_runtime)):
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
    for store in [runtime.store, runtime.planner.store, runtime.planner.brief_store]:
        store.check_private(roots)
    if getattr(runtime, "control", None) is not None:
        runtime.control.check_private(roots)
        runtime.start_observer()
    task_id = request.path_params.get("task_id")
    if task_id:
        if getattr(runtime, "operator_observe_only", False):
            exists = task_id in runtime.store.read_only()["tasks"]
        else:
            with runtime.store.transaction() as payload:
                exists = task_id in payload["tasks"]
        if not exists:
            if getattr(runtime, "operator_observe_only", False):
                raise CreatorError("NOT_FOUND", "Execution record not found", 404)
            runtime.base(task_id)
    return runtime


async def private_inspection(request: Request, runtime=Depends(get_execution_runtime)):
    async for chunk in request.stream():
        if chunk:
            raise CreatorError("INVALID_INPUT", "Inspection does not accept a request body", 422)
    # Deliberately never call private_execution: it opens a write transaction.
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
    for store in [runtime.store, runtime.planner.store, runtime.planner.brief_store]:
        store.check_private(roots)
    if getattr(runtime, "control", None) is not None:
        runtime.control.check_private(roots)
    return runtime


@router.get("/tasks/{task_id}/execution/operations/{operation_id}/receipts/inspect")
def inspect_receipts(task_id: TaskId, operation_id: TaskId, runtime=Depends(private_inspection)):
    return runtime.inspect_receipts(task_id, operation_id)


@router.get("/tasks/{task_id}/execution/asset-handoff")
def asset_handoff(task_id: TaskId, runtime=Depends(private_inspection)):
    from core.execution.asset_handoff import build_handoff
    return build_handoff(runtime, task_id)


@router.get("/tasks/{task_id}/execution/media")
def media_status(task_id: TaskId, runtime=Depends(private_inspection)):
    from core.execution.media_runtime import MediaExecution
    media = getattr(runtime, "media", None)
    return (media if media is not None else MediaExecution(runtime)).get(task_id)


@router.get("/tasks/{task_id}/execution/asset-design")
def asset_design_status(task_id: TaskId, runtime=Depends(private_inspection)):
    from core.execution.asset_design import AssetDesignExecution
    design = getattr(runtime, "asset_design", None)
    return (design if design is not None else AssetDesignExecution(runtime)).get(task_id)


@router.get("/execution/policy")
def policy(runtime=Depends(private_execution)):
    return runtime.policy_view()


@router.get("/tasks/{task_id}/execution")
def observe(task_id: TaskId, runtime=Depends(private_execution)):
    return runtime.get(task_id)


@router.post("/tasks/{task_id}/execution/quotes")
def quote(task_id: TaskId, body: QuoteInput, response: Response, runtime=Depends(private_execution)):
    result = runtime.quote(task_id, body.model_dump())
    response.status_code = 200 if result["replayed"] else 201
    return result


@router.get("/tasks/{task_id}/execution/quotes/by-key/{client_request_key}")
def quote_key(task_id: TaskId, client_request_key: ClientKey, runtime=Depends(private_execution)):
    return runtime.by_key(task_id, "quote", client_request_key)


@router.post("/tasks/{task_id}/execution/grants")
def grant(task_id: TaskId, body: GrantInput, response: Response, runtime=Depends(private_execution)):
    result = runtime.grant(task_id, body.model_dump())
    response.status_code = 200 if result["replayed"] else 201
    return result


@router.get("/tasks/{task_id}/execution/grants/by-key/{client_request_key}")
def grant_key(task_id: TaskId, client_request_key: ClientKey, runtime=Depends(private_execution)):
    return runtime.by_key(task_id, "grant", client_request_key)


@router.post("/tasks/{task_id}/execution/grants/{grant_id}/revoke")
def revoke(task_id: TaskId, grant_id: GrantId, body: RevokeInput, runtime=Depends(private_execution)):
    return runtime.revoke(task_id, grant_id, body.model_dump())


@router.post("/tasks/{task_id}/execution/actions")
def action(task_id: TaskId, body: ExecutionAction, runtime=Depends(private_execution)):
    return runtime.action(task_id, body.model_dump())


@router.get("/tasks/{task_id}/execution/events")
def events(task_id: TaskId, after_sequence: Annotated[int, Query(ge=0)] = 0, limit: Annotated[int, Query(ge=1, le=200)] = 100, runtime=Depends(private_execution)):
    return runtime.events(task_id, after_sequence, limit)
