"""Desktop studio project and canvas endpoints."""

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from api.dependencies import workflow_engine
from api.services.studio_store import studio_store

router = APIRouter(prefix="/api/studio", tags=["Studio"])


class CreateProject(BaseModel):
    title: str = Field(default="未命名作品", max_length=120)


class SaveCanvas(BaseModel):
    expected_revision: int
    title: str = Field(min_length=1, max_length=120)
    nodes: list[dict[str, Any]] = Field(max_length=5000)
    edges: list[dict[str, Any]] = Field(max_length=10000)


class ExportCanvas(BaseModel):
    expected_revision: int


@router.get("/projects")
def list_projects(trashed: bool = False):
    return {"projects": studio_store.list(workflow_engine, trashed)}


@router.post("/projects")
def create_project(request: CreateProject):
    return studio_store.create(request.title)


@router.post("/projects/{project_id}/clone")
def clone_project(project_id: str):
    try:
        return studio_store.clone(project_id, workflow_engine)
    except KeyError as exc:
        raise HTTPException(404, "Project not found") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/projects/{project_id}/export")
def export_project(project_id: str, request: ExportCanvas):
    try:
        return studio_store.export(project_id, workflow_engine, request.expected_revision)
    except KeyError as exc:
        raise HTTPException(404, "Project not found") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except RuntimeError as exc:
        if str(exc).startswith("Project changed"):
            raise HTTPException(409, str(exc)) from exc
        raise HTTPException(500, "Video export failed; check server logs") from exc


@router.get("/projects/{project_id}")
def get_project(project_id: str):
    try:
        result = studio_store.get(project_id, workflow_engine)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not result:
        raise HTTPException(404, "Project not found")
    return result


@router.put("/projects/{project_id}")
def save_project(project_id: str, request: SaveCanvas):
    try:
        return studio_store.save(project_id, workflow_engine, request.model_dump())
    except KeyError as exc:
        raise HTTPException(404, "Project not found") from exc
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.delete("/projects/{project_id}")
def trash_project(project_id: str):
    try:
        return studio_store.set_trashed(project_id, workflow_engine, True)
    except KeyError as exc:
        raise HTTPException(404, "Project not found") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/projects/{project_id}/restore")
def restore_project(project_id: str):
    try:
        return studio_store.set_trashed(project_id, workflow_engine, False)
    except KeyError as exc:
        raise HTTPException(404, "Project not found") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
