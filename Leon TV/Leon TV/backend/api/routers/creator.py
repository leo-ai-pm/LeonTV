"""Real private Brief CRUD. This router does not import execution/model clients."""

import threading
from pathlib import Path as FilePath
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from starlette.routing import Mount

from api.schemas.creator import CreateBrief, UpdateBrief
from core.creator.store import CreatorError


class SafeCreatorRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def safe(request):
            try:
                allowed_query = set()
                if request.method == "GET" and "client_request_key" not in request.path_params:
                    allowed_query = {"revision"} if "brief_id" in request.path_params else {"offset", "limit"}
                if set(request.query_params) - allowed_query:
                    raise CreatorError("INVALID_INPUT", "Unknown Brief query field", 422)
                response = await handler(request)
            except RequestValidationError:
                response = JSONResponse({"detail": {"code": "INVALID_INPUT", "message": "Invalid Brief input"}}, status_code=422)
            except CreatorError as exc:
                detail = {"code": exc.code, "message": exc.message}
                if exc.current_revision is not None:
                    detail["current_revision"] = exc.current_revision
                response = JSONResponse({"detail": detail}, status_code=exc.status)
            except Exception:
                # Never log request bodies, exception reprs, source text or paths.
                response = JSONResponse({"detail": {"code": "PRIVATE_STORAGE_UNAVAILABLE", "message": "Private Brief storage is unavailable"}}, status_code=503)
            response.headers["Cache-Control"] = "no-store"
            response.headers["X-Content-Type-Options"] = "nosniff"
            return response

        return safe


router = APIRouter(prefix="/api/creator/briefs", tags=["Creator Brief drafts"], route_class=SafeCreatorRoute)
BriefId = Annotated[str, Path(pattern=r"^[0-9a-f]{32}$")]
RequestKey = Annotated[str, Path(pattern=r"^[A-Za-z0-9_-]{8,128}$")]
_store = None
_store_lock = threading.RLock()


def get_brief_store():
    global _store
    with _store_lock:
        if _store is None:
            from core.execution.bootstrap import build_brief_store
            _store = build_brief_store(FilePath(__file__).resolve().parents[2] / "harness_data" / "creator_briefs")
        return _store


def private_store(request: Request, store=Depends(get_brief_store)):
    pending, seen, static_roots = [request.app], set(), []
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
    store.check_private(static_roots)
    if getattr(store, "operator_control", None) is not None:
        store.operator_control.check_private(static_roots)
    return store


@router.post("")
def create_brief(body: CreateBrief, response: Response, store=Depends(private_store)):
    content = body.model_dump(exclude={"client_request_key"})
    result = store.create(body.client_request_key, content)
    response.status_code = 200 if result["replayed"] else 201
    return result


@router.get("")
def list_briefs(offset: Annotated[int, Query(ge=0)] = 0, limit: Annotated[int, Query(ge=1, le=100)] = 50,
                store=Depends(private_store)):
    return store.list(offset, limit)


@router.get("/by-key/{client_request_key}")
def find_brief(client_request_key: RequestKey, store=Depends(private_store)):
    return store.get(key=client_request_key)


@router.get("/{brief_id}")
def read_brief(brief_id: BriefId, revision: Annotated[int | None, Query(ge=1)] = None,
               store=Depends(private_store)):
    return store.get(brief_id, revision=revision)


@router.put("/{brief_id}")
def update_brief(brief_id: BriefId, body: UpdateBrief, store=Depends(private_store)):
    content = body.model_dump(exclude={"client_update_key", "expected_revision"})
    return store.update(brief_id, body.client_update_key, body.expected_revision, content)
