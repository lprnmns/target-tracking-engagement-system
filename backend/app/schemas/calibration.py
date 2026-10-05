from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class LensProfile(StrEnum):
    MM_3_6 = "3.6mm"
    MM_8 = "8mm"
    MM_12 = "12mm"
    VARIFOCAL_CUSTOM = "varifocal_custom"
    UNKNOWN = "unknown"


class CalibrationStatusValue(StrEnum):
    NOT_STARTED = "not_started"
    PARTIAL = "partial"
    VALID = "valid"
    INVALID = "invalid"


class WarningLevel(StrEnum):
    GOOD = "good"
    MARGINAL = "marginal"
    POOR = "poor"


class TargetPosition(StrEnum):
    LEFT = "left"
    RIGHT = "right"
    UP = "up"
    DOWN = "down"
    CENTER = "center"


class SimulatedAxis(StrEnum):
    X = "x"
    Y = "y"


class DirectionMotion(StrEnum):
    CAMERA_LEFT = "camera_left"
    CAMERA_RIGHT = "camera_right"
    CAMERA_UP = "camera_up"
    CAMERA_DOWN = "camera_down"
    NO_MOTION = "no_motion"
    UNKNOWN = "unknown"


class OperatorConfidence(StrEnum):
    CONFIRMED = "confirmed"
    MANUAL = "manual"
    NEEDS_RETEST = "needs_retest"


class CameraCalibrationConfig(BaseModel):
    camera_id: str = "mock_camera_0"
    camera_name: str = "Mock Camera"
    lens_profile: LensProfile = LensProfile.UNKNOWN
    resolution_width: int = Field(default=640, gt=0)
    resolution_height: int = Field(default=360, gt=0)
    fps: int = Field(default=15, gt=0)
    camera_height_cm: float = Field(default=60.0, gt=0)
    target_height_cm: float = Field(default=130.0, gt=0)
    table_height_cm: float = Field(default=60.0, ge=0)
    hfov_deg: float = Field(default=45.0, gt=0, lt=180)
    vfov_deg: float | None = Field(default=None, gt=0, lt=180)
    distortion_enabled: bool = False
    homography_enabled: bool = False
    calibration_status: CalibrationStatusValue = CalibrationStatusValue.NOT_STARTED
    updated_at: float = 0.0


class CalibrationPoint(BaseModel):
    id: str
    label: str
    world_x_m: float
    world_y_m: float
    image_x_px: float = Field(ge=0)
    image_y_px: float = Field(ge=0)


class CalibrationPointCreate(BaseModel):
    label: str
    world_x_m: float
    world_y_m: float
    image_x_px: float = Field(ge=0)
    image_y_px: float = Field(ge=0)


class CalibrationComputeResult(BaseModel):
    calibration_points: list[CalibrationPoint] = Field(default_factory=list)
    homography_matrix: list[list[float]] | None = None
    reprojection_error_px: float | None = None
    inlier_count: int = 0
    calibration_hash: str | None = None
    homography_direction: str = "world_plane_to_image_px"
    valid: bool = False
    warnings: list[str] = Field(default_factory=list)
    updated_at: float


class CalibrationStatus(BaseModel):
    config: CameraCalibrationConfig
    calibration_points: list[CalibrationPoint] = Field(default_factory=list)
    homography_matrix: list[list[float]] | None = None
    reprojection_error_px: float | None = None
    inlier_count: int = 0
    calibration_hash: str | None = None
    homography_direction: str = "world_plane_to_image_px"
    valid: bool = False
    warnings: list[str] = Field(default_factory=list)
    updated_at: float


class FovEstimateRequest(BaseModel):
    hfov_deg: float = Field(gt=0, lt=180)
    distance_m: float = Field(gt=0)
    object_width_m: float = Field(gt=0)
    image_width_px: int = Field(gt=0)


class FovEstimateResponse(BaseModel):
    visible_width_m: float
    object_width_px: float
    warning_level: WarningLevel


class DirectionCalibrationProfile(BaseModel):
    profile_id: str = "default_direction_profile"
    created_at: float = 0.0
    updated_at: float = 0.0
    source: str = "manual_simulation"
    image_x_positive: str = "right"
    image_y_positive: str = "down"
    camera_mirror_x: bool = False
    camera_mirror_y: bool = False
    axis_swap: bool = False
    pan_positive_label: str = "camera_right"
    tilt_positive_label: str = "camera_up"
    x_axis_multiplier: Literal[1, -1] = 1
    y_axis_multiplier: Literal[1, -1] = 1
    target_error_convention: str = "target_center_minus_frame_center"
    expected_pan_response: str = "target_moves_opposite_to_camera_motion"
    expected_tilt_response: str = "target_moves_opposite_to_camera_motion"
    advisory_only: bool = True
    physical_command_enabled: bool = False
    no_physical_command_generated: bool = True
    notes: str = "Direction semantics profile is advisory only."


class DirectionSimulationRequest(BaseModel):
    target_position: TargetPosition
    target_center_x: float | None = None
    target_center_y: float | None = None
    frame_width: int = Field(default=640, gt=0)
    frame_height: int = Field(default=360, gt=0)


class DirectionSimulationResult(BaseModel):
    target_visual_side: str
    target_error_x: float
    target_error_y: float
    required_camera_motion: str
    expected_image_response: str
    frame_center_x: float
    frame_center_y: float
    target_center_x: float
    target_center_y: float
    advisory_motion_only: bool = True
    physical_command_enabled: bool = False
    no_physical_command_generated: bool = True


class DirectionObservationRequest(BaseModel):
    simulated_axis: SimulatedAxis
    system_expected_motion: DirectionMotion
    operator_observed_motion: DirectionMotion
    operator_confidence: OperatorConfidence = OperatorConfidence.MANUAL
    note: str | None = None


class DirectionObservationResult(BaseModel):
    observation_id: str
    simulated_axis: SimulatedAxis
    system_expected_motion: DirectionMotion
    operator_observed_motion: DirectionMotion
    operator_confidence: OperatorConfidence
    suggested_x_axis_multiplier: int = 1
    suggested_y_axis_multiplier: int = 1
    axis_swap_suspected: bool = False
    confidence: str = "manual"
    note: str | None = None
    advisory_only: bool = True
    physical_command_enabled: bool = False
    no_physical_command_generated: bool = True


class DirectionCalibrationStatus(BaseModel):
    profile: DirectionCalibrationProfile
    latest_simulation: DirectionSimulationResult | None = None
    latest_observation: DirectionObservationResult | None = None
    observation_count: int = 0
    advisory_only: bool = True
    physical_command_enabled: bool = False
    no_physical_command_generated: bool = True


class CalibrationConfigModel(CameraCalibrationConfig):
    @model_validator(mode="after")
    def validate_phase8_defaults(self) -> "CalibrationConfigModel":
        if self.camera_height_cm <= 0:
            raise ValueError("calibration.camera_height_cm must be positive")
        if self.target_height_cm <= 0:
            raise ValueError("calibration.target_height_cm must be positive")
        return self
