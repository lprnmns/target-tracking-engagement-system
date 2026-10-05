from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, model_validator


class MotionStateValue(StrEnum):
    IDLE = "IDLE"
    JOGGING = "JOGGING"
    HOMING = "HOMING"
    SCANNING = "SCANNING"
    TRACKING_DRY_RUN = "TRACKING_DRY_RUN"
    STOPPED = "STOPPED"
    FAULT = "FAULT"


class MotionSettings(BaseModel):
    pan_min_deg: float = -60.0
    pan_max_deg: float = 60.0
    tilt_min_deg: float = -28.0
    tilt_max_deg: float = 28.0
    pan_steps_per_degree: float = Field(default=10.0, gt=0)
    tilt_steps_per_degree: float = Field(default=10.0, gt=0)
    pan_max_speed_deg_s: float = Field(default=20.0, ge=0)
    tilt_max_speed_deg_s: float = Field(default=15.0, ge=0)
    pan_accel_deg_s2: float = Field(default=50.0, ge=0)
    tilt_accel_deg_s2: float = Field(default=40.0, ge=0)
    jog_step_deg: float = Field(default=1.0, gt=0)
    deadband_px: int = Field(default=12, ge=0)
    tracking_gain_x: float = 0.05
    tracking_gain_y: float = 0.05
    backlash_compensation_enabled: bool = False
    soft_limits_enabled: bool = False
    scan_enabled: bool = False
    scan_min_deg: float = -45.0
    scan_max_deg: float = 45.0
    scan_speed_deg_s: float = Field(default=10.0, ge=0)

    @model_validator(mode="after")
    def validate_limits(self) -> "MotionSettings":
        if self.pan_min_deg >= self.pan_max_deg:
            raise ValueError("pan_min_deg must be < pan_max_deg")
        if self.tilt_min_deg >= self.tilt_max_deg:
            raise ValueError("tilt_min_deg must be < tilt_max_deg")
        if self.scan_min_deg >= self.scan_max_deg:
            raise ValueError("scan_min_deg must be < scan_max_deg")
        return self


class MotionState(BaseModel):
    motion_state: MotionStateValue = MotionStateValue.IDLE
    pan_position_deg: float = 0.0
    tilt_position_deg: float = 0.0
    pan_target_deg: float = 0.0
    tilt_target_deg: float = 0.0
    pan_position_steps: int = 0
    tilt_position_steps: int = 0
    pan_error_deg: float = 0.0
    tilt_error_deg: float = 0.0
    pan_limit_left: bool = False
    pan_limit_right: bool = False
    tilt_limit_up: bool = False
    tilt_limit_down: bool = False
    homed: bool = False
    homing_phase: str = "IDLE"
    limit_reason_code: str | None = None
    physical_pan_deg: float | None = None
    physical_tilt_deg: float | None = None
    driver_enabled: bool = False
    estop_state: bool = False
    dry_run: bool = True
    last_command: str | None = None
    last_error: str | None = None
    updated_at: float


class MotionJogRequest(BaseModel):
    axis: str
    direction: str
    step_deg: float | None = None


class MotionGoToRequest(BaseModel):
    pan_target_deg: float
    tilt_target_deg: float


class MotionTrackDryRunRequest(BaseModel):
    frame_width: int = Field(gt=0)
    frame_height: int = Field(gt=0)
    target_center_x: float
    target_center_y: float


class TrackingDryRunPreview(BaseModel):
    frame_center_x: float
    frame_center_y: float
    target_center_x: float
    target_center_y: float
    error_x_px: float
    error_y_px: float
    computed_pan_delta_deg: float
    computed_tilt_delta_deg: float


class MotionCommandResponse(BaseModel):
    accepted: bool
    dry_run: bool
    command_id: str
    command_type: str
    requested_target: dict[str, Any] = Field(default_factory=dict)
    clamped_target: dict[str, Any] | None = None
    blocking_reasons: list[str] = Field(default_factory=list)
    safety_gates: list[dict[str, Any]] = Field(default_factory=list)
    generated_steps: dict[str, int] | None = None
    no_physical_command_generated: bool = True
    reason: str
    state: MotionState
    tracking_preview: TrackingDryRunPreview | None = None
