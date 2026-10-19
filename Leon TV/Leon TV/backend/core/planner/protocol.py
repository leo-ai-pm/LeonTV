"""Strict inert JSON tools and source-bound shot plans."""

import json
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from core.creator.store import unique_object

LIMITS = {"max_steps": 6, "max_repairs": 2, "max_output_bytes": 65536, "max_context_bytes": 1048576}
SCENARIOS = {"success", "repair", "invalid_tool", "step_limit", "response_unknown"}
STATUSES = {"awaiting_authorization", "ready", "queued", "running", "stopped", "response_unknown", "plan_ready", "blocked"}
ShotId = Annotated[str, Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,31}$")]


class Strict(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")


class Shot(Strict):
    id: ShotId
    duration_ms: int = Field(ge=1, le=15000)
    visual: str = Field(min_length=1, max_length=2000)
    camera: str = Field(max_length=500)
    audio: str = Field(max_length=500)
    depends_on: list[ShotId] = Field(max_length=11)

    @field_validator("visual", "camera", "audio")
    @classmethod
    def text(cls, value, info):
        value.encode("utf-8")
        if info.field_name == "visual" and not value.strip():
            raise ValueError("Blank visual")
        return value


class ShotPlan(Strict):
    source_snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    duration_ms: int = Field(ge=15000, le=15000)
    ratio: Literal["9:16"]
    shots: list[Shot] = Field(min_length=1, max_length=12)


class ReadArgs(Strict):
    pass


class SubmitArgs(Strict):
    plan: ShotPlan


class Action(Strict):
    tool: Literal["read_frozen_brief", "submit_shot_plan"]
    arguments: dict


def parse_json(raw):
    def invalid(_):
        raise ValueError("Nonfinite JSON")

    return json.loads(raw, object_pairs_hook=unique_object, parse_constant=invalid)


def checked_plan(value, snapshot_hash):
    try:
        plan = ShotPlan.model_validate(value).model_dump()
    except (ValidationError, ValueError, UnicodeError):
        return None, "INVALID_PLAN"
    if plan["source_snapshot_hash"] != snapshot_hash:
        return None, "SOURCE_BINDING"
    shots = plan["shots"]
    ids = [shot["id"] for shot in shots]
    if len(set(ids)) != len(ids):
        return None, "DUPLICATE_SHOT_ID"
    if sum(shot["duration_ms"] for shot in shots) != 15000:
        return None, "DURATION_TOTAL"
    dependencies = {shot["id"]: shot["depends_on"] for shot in shots}
    for key, parents in dependencies.items():
        if len(parents) != len(set(parents)) or key in parents or set(parents) - set(ids):
            return None, "INVALID_DEPENDENCY"
    completed, active = set(), set()

    def visit(key):
        if key in active:
            return False
        if key in completed:
            return True
        active.add(key)
        for parent in dependencies[key]:
            if not visit(parent):
                return False
        active.remove(key)
        completed.add(key)
        return True

    if not all(visit(key) for key in ids):
        return None, "DEPENDENCY_CYCLE"
    return plan, None


def execute_tool(raw, source, already_read):
    try:
        action = Action.model_validate(parse_json(raw))
        if action.tool == "read_frozen_brief":
            ReadArgs.model_validate(action.arguments)
            return {"ok": True, "tool": action.tool, "source": source}, None, True
        arguments = SubmitArgs.model_validate(action.arguments)
    except (ValueError, ValidationError, UnicodeError, TypeError, RecursionError):
        return {"ok": False, "code": "INVALID_ACTION"}, None, already_read
    if not already_read:
        return {"ok": False, "code": "SOURCE_NOT_READ"}, None, already_read
    plan, error = checked_plan(arguments.plan.model_dump(), source["snapshot_hash"])
    if error:
        return {"ok": False, "tool": action.tool, "code": error}, None, already_read
    return {"ok": True, "tool": action.tool, "code": "STRUCTURE_VALID_NOT_REVIEWED"}, plan, already_read
