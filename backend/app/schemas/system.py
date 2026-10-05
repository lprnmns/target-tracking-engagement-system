from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, Field


class MissionMode(StrEnum):
    BOOTING = "BOOTING"
    DISARMED = "DISARMED"
    STANDBY = "STANDBY"
    MANUAL = "MANUAL"
    AUTONOMOUS = "AUTONOMOUS"
    LOCKED = "LOCKED"
    FIRE_READY = "FIRE_READY"
    FIRING = "FIRING"
    FAULT = "FAULT"
    ESTOP_ACTIVE = "ESTOP_ACTIVE"
    REPLAY = "REPLAY"
    CALIBRATION = "CALIBRATION"


class FirePolicy(StrEnum):
    NO_FIRE = "NO_FIRE"
    FIRE_ALLOWED = "FIRE_ALLOWED"


class SystemState(BaseModel):
    mode: MissionMode = MissionMode.AUTONOMOUS
    armed: bool = True
    fire_policy: FirePolicy = FirePolicy.FIRE_ALLOWED
    dry_run: bool = False
    hardware_enabled: bool = True
    ready: bool = True
    uptime_s: Annotated[float, Field(ge=0)] = 0.0
    reason: str = "System starts ARMED."
    blocking_reasons: list[str] = Field(
        default_factory=lambda: []
    )


class HealthResponse(BaseModel):
    ok: bool
    version: str
    uptime_s: Annotated[float, Field(ge=0)]

