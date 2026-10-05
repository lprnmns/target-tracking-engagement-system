from enum import StrEnum

from pydantic import BaseModel, Field


class DecisionStatus(StrEnum):
    NO_FIRE = "NO_FIRE"
    WAIT = "WAIT"
    LOCKED = "LOCKED"
    FIRE_READY = "FIRE_READY"


class SafetyGateState(BaseModel):
    armed: bool = False
    estop_released: bool = False
    pico_heartbeat: bool = False
    track_stable: bool = False
    target_enemy: bool = False
    balloon_detected: bool = False
    range_valid: bool = False
    aim_point_valid: bool = False
    zone_valid: bool = True
    operator_or_auto_permission: bool = False
    hardware_enabled: bool = False
    dry_run: bool = True
    motion_soft_limits: bool = True
    motion_estop: bool = True
    motion_fault_clear: bool = True
    motion_driver: bool = False
    motion_dry_run: bool = True
    person_safety_clear: bool = True


class SafetyState(BaseModel):
    decision: DecisionStatus = DecisionStatus.NO_FIRE
    gates: SafetyGateState = Field(default_factory=SafetyGateState)
    reason: str = "Default safety policy is NO_FIRE."
    blocking_reasons: list[str] = Field(
        default_factory=lambda: [
            "system_disarmed",
            "estop_not_confirmed",
            "pico_heartbeat_missing",
            "track_not_stable",
            "target_not_enemy",
            "balloon_not_detected",
            "range_not_valid",
            "operator_permission_missing",
            "hardware_disabled",
        ]
    )


class SafetyCommandResult(BaseModel):
    accepted: bool
    command: str
    decision: DecisionStatus
    reason: str
    blocking_reasons: list[str]


class FireRequest(BaseModel):
    track_id: int | None = None
    operator_confirmed: bool = False


class MotorJogRequest(BaseModel):
    axis: str
    degrees: float
    operator_confirmed: bool = False
