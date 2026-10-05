from __future__ import annotations

import time
from pydantic import BaseModel, Field

from app.schemas.mission import Stage3TargetClass


class Stage3FriendLink(BaseModel):
    balloon_track_id: int
    body_track_id: int


class Stage3EngagementStatus(BaseModel):
    current_round: int = 1
    enemy_class: Stage3TargetClass | None = None
    enemy_balloon_track_id: int | None = None
    friend_links: list[Stage3FriendLink] = Field(default_factory=list)
    enemy_confirmation_state: str | None = None
    enemy_hit_confirmed: bool = False
    friend_safety_verified: bool = False
    friend_hit_suspected: bool = False
    ready_to_close: bool = False
    reason_codes: list[str] = Field(default_factory=list)
    shot_at: float | None = None
    updated_at: float = Field(default_factory=time.time)
