"""D08 accepts saved references only; no source, authorization or live switches."""

from typing import Annotated, Literal

from pydantic import Field

from core.planner.protocol import Strict

Key = Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{8,128}$")]


class CreateTask(Strict):
    client_request_key: Key
    brief_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    brief_revision: int = Field(ge=1)


class CreateOfflineTask(CreateTask):
    scenario: Literal["success", "repair", "invalid_tool", "step_limit", "response_unknown", "review_accepted", "review_rejected", "review_unknown", "review_response_unknown", "review_invalid", "revision_accepted", "revision_rejected", "revision_needs_input", "revision_invalid", "revision_response_unknown", "recheck_unknown", "recheck_response_unknown", "recheck_invalid"] = "success"


class TaskAction(Strict):
    client_action_key: Key
    expected_revision: int = Field(ge=1)
    action: Literal["stop", "resume"]
