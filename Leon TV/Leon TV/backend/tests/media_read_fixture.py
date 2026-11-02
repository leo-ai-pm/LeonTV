"""Reusable readonly ASGI app and explicit pre-D51 GET algorithm for timing.

No execution runtime/session/config or credential loader is constructed here.
The baseline changes only orchestration; all current production validators and
the same actual native/control/source/file data remain in force.
"""
from types import SimpleNamespace

from fastapi import FastAPI

from api.routers import creator, execution, planner
from core.creator.store import CreatorBriefStore, CreatorError
from core.execution.media_ledger import projection
from core.execution.media_runtime import MediaExecution
from core.execution.production_image_status import production_images
from core.execution.store import ExecutionStore
from core.planner.runtime import PlannerRuntime


def pre_d51_get(store, task_id):
    payload = store.read_only()
    if task_id not in payload["tasks"]:
        raise CreatorError("NOT_FOUND", "Execution record not found", 404)
    return {"media": projection(payload, task_id), "production_images": production_images(store, payload, task_id)}


def readonly_app(root):
    brief_store = CreatorBriefStore(root / "creator")
    planner_runtime = PlannerRuntime(root / "planner", brief_store)
    runtime = SimpleNamespace(store=ExecutionStore(root / "execution"), planner=planner_runtime, control=None)
    runtime.media = MediaExecution(runtime)
    app = FastAPI()
    for router in (creator.router, planner.router, execution.router):
        app.include_router(router)
    app.dependency_overrides[creator.get_brief_store] = lambda: brief_store
    app.dependency_overrides[planner.get_planner_runtime] = lambda: planner_runtime
    app.dependency_overrides[execution.get_execution_runtime] = lambda: runtime
    app.state.read_runtime = runtime
    app.state.close_fixture = planner_runtime.close
    return app
