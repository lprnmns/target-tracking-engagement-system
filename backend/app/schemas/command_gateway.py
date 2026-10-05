from enum import StrEnum

from pydantic import BaseModel, Field


class CommandProfile(StrEnum):
    DRY_RUN = "DRY_RUN"
    LIVE_TEST = "LIVE_TEST"
    VIDEO_DEMO = "VIDEO_DEMO"
    COMPETITION = "COMPETITION"


class PreflightGate(BaseModel):
    code: str
    ready: bool
    detail: str


class GatewayPreflightResult(BaseModel):
    profile: CommandProfile
    physical_motion_enabled: bool
    physical_fire_enabled: bool
    ready: bool
    reason_codes: list[str] = Field(default_factory=list)
    gates: list[PreflightGate] = Field(default_factory=list)
    pico_protocol: str | None = None
    actuator_armed: bool = False


class GatewayCommandResult(BaseModel):
    accepted: bool
    command: str
    reason_codes: list[str] = Field(default_factory=list)
    detail: str
    pico_ack: str | None = None
    physical_command_generated: bool = False
