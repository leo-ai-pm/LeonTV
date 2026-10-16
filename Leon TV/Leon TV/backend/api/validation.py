from fastapi import HTTPException, Request

from path_security import validate_storage_id


def validate_resource_ids(request: Request) -> None:
    """Reject unsafe IDs before routers create state or access the filesystem."""
    for key in ("session_id", "task_id", "project_id", "job_id"):
        if key in request.path_params:
            try:
                validate_storage_id(request.path_params[key])
            except ValueError as exc:
                raise HTTPException(400, "Invalid resource ID") from exc
