import time
from typing import Literal

from pydantic import BaseModel, Field


MissionStage = Literal["stage1", "stage2", "stage3"]
Stage1Target = Literal["Balistik Füze", "Helikopter", "F16", "Mini/Micro İHA"]
Stage1RangeScore = Literal[5, 10, 20]
Stage3TargetClass = Literal["f16", "helicopter", "ballistic_missile", "mini_micro_uav"]

STAGE1_REQUIRED_TARGETS: tuple[Stage1Target, ...] = ("Balistik Füze", "Helikopter", "F16", "Mini/Micro İHA")


class Stage1HitRequest(BaseModel):
    target: Stage1Target
    score_awarded: Stage1RangeScore


class Stage1WrongTargetRequest(BaseModel):
    target: Stage1Target


class Stage1ManualMotionRequest(BaseModel):
    speed_x: int = Field(ge=-12000, le=12000)
    speed_y: int = Field(ge=-12000, le=12000)
    duration_ms: int = Field(ge=50, le=1200)


class Stage1PlanRequest(BaseModel):
    order: list[Stage1Target] = Field(min_length=4, max_length=4)


class Stage1Event(BaseModel):
    kind: Literal["hit", "wrong_target"]
    target: Stage1Target
    score_awarded: int = 0
    penalty: int = 0
    elapsed_s: int
    timestamp: float = Field(default_factory=time.time)


class Stage2RoundCompleteRequest(BaseModel):
    confirmed_hits: int = Field(ge=0, le=3)


class Stage2RoundEvent(BaseModel):
    round_number: int = Field(ge=1, le=4)
    confirmed_hits: int = Field(ge=0, le=3)
    points: int
    zero_hit_streak: int = Field(ge=0)
    timestamp: float = Field(default_factory=time.time)


class Stage3RoundCompleteRequest(BaseModel):
    enemy_class: Stage3TargetClass
    enemy_hit: bool
    friend_hit: bool = False


class Stage3RoundEvent(BaseModel):
    round_number: int = Field(ge=1, le=8)
    enemy_class: Stage3TargetClass
    enemy_hit: bool
    friend_hit: bool
    points: int
    penalty: int
    miss_streak: int = Field(ge=0)
    timestamp: float = Field(default_factory=time.time)


class MissionState(BaseModel):
    active_stage: MissionStage = "stage1"
    elapsed_s: int = 0
    timer_running: bool = False
    stage1_hits: int = 0
    stage1_wrong_hits: int = 0
    stage1_order: list[str] = Field(default_factory=lambda: ["Balistik Füze", "Helikopter", "F16", "Mini/Micro İHA"])
    stage1_order_locked: bool = False
    stage1_completed_targets: list[Stage1Target] = Field(default_factory=list)
    stage1_raw_points: int = 0
    stage1_penalty_points: int = 0
    stage1_events: list[Stage1Event] = Field(default_factory=list)
    stage2_round: int = 1
    stage2_hits: int = 0
    stage2_completed_rounds: int = 0
    stage2_round_events: list[Stage2RoundEvent] = Field(default_factory=list)
    stage2_zero_hit_streak: int = 0
    stage2_failed: bool = False
    stage3_round: int = 1
    stage3_hits: int = 0
    stage3_friend_or_miss_penalties: int = 0
    stage3_completed_rounds: int = 0
    stage3_round_events: list[Stage3RoundEvent] = Field(default_factory=list)
    stage3_miss_streak: int = 0
    stage3_failed: bool = False
    updated_at: float = Field(default_factory=time.time)


class MissionUpdate(BaseModel):
    active_stage: MissionStage | None = None
    elapsed_s: int | None = Field(default=None, ge=0)
    timer_running: bool | None = None
    stage1_hits: int | None = Field(default=None, ge=0)
    stage1_wrong_hits: int | None = Field(default=None, ge=0)
    stage1_order: list[str] | None = None
    # Kept only to return STAGE2_EVENT_API_REQUIRED to legacy clients rather
    # than silently accepting an obsolete mutable score update.
    stage2_round: int | None = Field(default=None, ge=1, le=4)
    stage2_hits: int | None = Field(default=None, ge=0)
    stage3_round: int | None = Field(default=None, ge=1, le=8)
    stage3_hits: int | None = Field(default=None, ge=0)
    stage3_friend_or_miss_penalties: int | None = Field(default=None, ge=0)


class MissionScore(BaseModel):
    stage1_score: int
    stage2_score: int
    stage3_score: int
    active_score: int
    remaining_s: int
    total_estimated_score: int
    stage1_raw_points: int = 0
    stage1_penalty_points: int = 0
    stage1_bonus_points: int = 0
    stage1_next_target: Stage1Target | None = None
    stage1_plan_locked: bool = False
    stage2_round_scores: list[int] = Field(default_factory=list)
    stage2_zero_hit_streak: int = 0
    stage2_failed: bool = False
    stage2_passing_threshold_met: bool = False
    stage3_round_scores: list[int] = Field(default_factory=list)
    stage3_miss_streak: int = 0
    stage3_failed: bool = False
    stage3_award_threshold_met: bool = False


class MissionSnapshot(BaseModel):
    state: MissionState
    score: MissionScore
    no_physical_command_generated: bool = True
