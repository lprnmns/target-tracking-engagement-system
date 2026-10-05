import time
from typing import Literal

from pydantic import BaseModel, Field


ReplayState = Literal["idle", "loaded", "playing", "paused", "stopped"]


class ReplayStatus(BaseModel):
    state: ReplayState = "idle"
    session_id: str | None = None
    frame_index: int = 0
    frame_count: int = 0
    speed: float = 1.0
    source: str = "replay"
    current_frame_path: str | None = None
    no_physical_command_generated: bool = True
    updated_at: float = Field(default_factory=time.time)


class ReplayLoadRequest(BaseModel):
    session_id: str


class ReplaySeekRequest(BaseModel):
    frame_index: int = Field(ge=0)


class ReplaySpeedRequest(BaseModel):
    speed: float = Field(gt=0, le=4)
