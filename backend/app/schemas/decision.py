from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from app.schemas.person_safety import PersonSafetyGateStatus


class DecisionStateValue(StrEnum):
    NO_TARGET = "NO_TARGET"
    TRACKING = "TRACKING"
    WAIT = "WAIT"
    LOCKED = "LOCKED"
    FIRE_READY = "FIRE_READY"
    NO_FIRE = "NO_FIRE"
    FAULT = "FAULT"


class GateStatus(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    WARNING = "warning"
    NOT_APPLICABLE = "not_applicable"


class GateSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class SafetyGate(BaseModel):
    name: str
    status: GateStatus
    severity: GateSeverity
    reason: str
    updated_at: float


class DecisionState(BaseModel):
    decision_state: DecisionStateValue
    fire_policy: str = "NO_FIRE_DEFAULT"
    active_target_id: int | None = None
    logical_target_id: str | None = None
    logical_target_name: str | None = None
    logical_target_state: str | None = None
    logical_balloon_target_id: str | None = None
    selected_body_detection_id: int | None = None
    selected_balloon_detection_id: int | None = None
    target_class: str | None = None
    target_team: str = "unknown"
    range_m: float | None = None
    stable_frames: int = 0
    required_stable_frames: int = 5
    gates: list[SafetyGate] = Field(default_factory=list)
    blocking_reasons: list[str] = Field(default_factory=list)
    decision_reason: str
    updated_at: float
    aim_point: dict[str, Any] | None = None
    person_safety: PersonSafetyGateStatus | None = None


class FireEvaluationResult(BaseModel):
    accepted: bool
    dry_run: bool
    decision_state: DecisionStateValue
    blocking_reasons: list[str]
    gates: list[SafetyGate]
    reason: str


class ArmDisarmResult(BaseModel):
    accepted: bool
    armed: bool
    reason: str
    blocking_reasons: list[str] = Field(default_factory=list)
    decision: DecisionState | None = None
