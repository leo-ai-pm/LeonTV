from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class SandboxLLMRequest(BaseModel):
    model: str
    prompt: str
    temperature: Optional[float] = Field(default=0.7, ge=0, le=2)
    web_search: Optional[bool] = False


class SandboxVLMRequest(BaseModel):
    model: str
    prompt: str
    images: List[str]


class SandboxT2IRequest(BaseModel):
    model: str
    prompt: str
    style: Optional[str] = None
    ratio: Optional[str] = "16:9"


class SandboxI2IRequest(BaseModel):
    model: str
    prompt: str
    image: str
    ratio: Optional[str] = "16:9"


class SandboxVideoRequest(BaseModel):
    model: str
    prompt: str
    image: Optional[str] = None
    ratio: str = "16:9"
    duration: int = Field(default=5, ge=1, le=120)
    resolution: str = "720p"


class SandboxJobRequest(BaseModel):
    job_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    tool: Literal["t2i", "i2i", "video"]
    model: str
    prompt: str = Field(min_length=1)
    image: Optional[str] = None
    ratio: str = "16:9"
    duration: int = Field(default=5, ge=1, le=120)
    resolution: str = "720p"
