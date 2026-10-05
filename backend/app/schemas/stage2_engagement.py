from __future__ import annotations

import time
from pydantic import BaseModel, Field


class Stage2EngagementStatus(BaseModel):
    current_round: int = 1
    fired_track_ids: list[int] = Field(default_factory=list)
    pending_track_ids: list[int] = Field(default_factory=list)
    confirmed_track_ids: list[int] = Field(default_factory=list)
    reengage_track_ids: list[int] = Field(default_factory=list)
    ready_to_close: bool = False
    reason_codes: list[str] = Field(default_factory=list)
    updated_at: float = Field(default_factory=time.time)
