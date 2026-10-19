"""D07 inert draft input; authorization, hashes and execution are server owned."""

import re
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

DraftKey = Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{8,128}$")]


class StrictDraft(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")


class DraftProduction(StrictDraft):
    duration_seconds: Literal[15] = 15
    ratio: Literal["9:16"] = "9:16"
    style: Literal["realistic_cinematic"] = "realistic_cinematic"

    @field_validator("duration_seconds", mode="before")
    @classmethod
    def integer_duration(cls, value):
        if type(value) is not int:
            raise ValueError("Duration must be an integer")
        return value


class DraftContent(StrictDraft):
    title: str = Field(min_length=1, max_length=120)
    script: str = Field(min_length=1, max_length=200000)
    requirements: str = Field(max_length=20000)
    production: DraftProduction
    budget_cny: str | None

    @field_validator("title", mode="before")
    @classmethod
    def trim_title(cls, value):
        return value.strip() if isinstance(value, str) else value

    @field_validator("title", "script", "requirements")
    @classmethod
    def utf8_text(cls, value):
        try:
            value.encode("utf-8")
        except UnicodeError:
            raise ValueError("Text must be valid UTF-8") from None
        return value

    @field_validator("script")
    @classmethod
    def nonblank_script(cls, value):
        if not value.strip():
            raise ValueError("A script is required")
        return value

    @field_validator("budget_cny")
    @classmethod
    def fixed_money(cls, value):
        if value is None:
            return None
        if not re.fullmatch(r"(?:0|[1-9][0-9]{0,6})(?:\.[0-9]{1,2})?", value):
            raise ValueError("Use a nonnegative decimal string or null")
        if Decimal(value) > Decimal("1000000.00"):
            raise ValueError("Draft budget exceeds the supported range")
        return format(Decimal(value), ".2f")


class CreateBrief(DraftContent):
    client_request_key: DraftKey
    title: str = Field(default="未命名创作简报", min_length=1, max_length=120)
    requirements: str = Field(default="", max_length=20000)
    production: DraftProduction = Field(default_factory=DraftProduction)
    budget_cny: str | None = None


class UpdateBrief(DraftContent):
    client_update_key: DraftKey
    expected_revision: int = Field(ge=1)
