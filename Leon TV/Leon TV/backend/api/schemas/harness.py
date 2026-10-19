"""Public, strict zero-paid Harness contracts. Raw documents/receipts are not accepted."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

Key = Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{8,128}$")]
Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
ProjectId = Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")]
RecordId = Annotated[str, Field(pattern=r"^[A-Za-z0-9_.-]{1,128}$")]


class StrictInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class SourceInput(StrictInput):
    hash: Sha256
    record_ids: list[RecordId] = Field(min_length=1, max_length=100)


class ProductionInput(StrictInput):
    ratio: Literal["9:16"]
    duration_ms: Literal[15000]


class CreateRun(StrictInput):
    mode: Literal["test"]
    client_request_key: Key
    project_id: ProjectId | None = None
    source: SourceInput
    production: ProductionInput
    authorization_ref: Literal["test-zero-paid-v1"]
    scenario: Literal[
        "success", "repair", "review_unknown", "review_rejected",
        "unknown_acceptance", "zero_budget", "resume_checkpoint",
    ] = "success"


class RunAction(StrictInput):
    mode: Literal["test"]
    client_action_key: Key
    expected_revision: int = Field(ge=1)
    action: Literal["stop", "resume", "reconcile", "revise"]
    artifact_id: Literal["reference", "shot"] | None = None


class FinalReview(StrictInput):
    mode: Literal["test"]
    client_review_key: Key
    expected_revision: int = Field(ge=1)
    manifest_version: int = Field(ge=1)
    final_sha256: Sha256
    decision: Literal["accept", "return"]
    feedback: str = Field(default="", max_length=1000)
