from enum import StrEnum
import time

from pydantic import BaseModel, Field, model_validator

from app.schemas.vision import BBox


class TeamValue(StrEnum):
    ENEMY = "enemy"
    FRIEND = "friend"
    UNKNOWN = "unknown"


class ColorSpace(StrEnum):
    HSV = "HSV"
    LAB = "LAB"


class HSVRange(BaseModel):
    h_min: int = Field(ge=0, le=180)
    h_max: int = Field(ge=0, le=180)
    s_min: int = Field(default=0, ge=0, le=255)
    v_min: int = Field(default=0, ge=0, le=255)

    @model_validator(mode="after")
    def validate_hue_range(self) -> "HSVRange":
        if self.h_min > self.h_max:
            raise ValueError("HSV h_min must be <= h_max")
        return self


class ColorClassifierConfig(BaseModel):
    color_space: ColorSpace = ColorSpace.HSV
    enemy_hsv_ranges: list[HSVRange]
    friend_hsv_ranges: list[HSVRange]
    saturation_min: int = Field(default=70, ge=0, le=255)
    value_min: int = Field(default=50, ge=0, le=255)
    lab_enabled: bool = False
    min_body_pixels: int = Field(default=200, gt=0)
    decision_threshold: float = Field(default=0.55, ge=0, le=1)
    temporal_window: int = Field(default=5, ge=1)
    required_consistent_frames: int = Field(default=3, ge=1)
    balloon_mask_enabled: bool = True
    balloon_hsv_ranges: list[HSVRange]
    morphology_kernel: int = Field(default=3, ge=1)
    updated_at: float = 0.0


class ColorClassifySampleRequest(BaseModel):
    frame_id: int = 1
    detection_id: int = 1
    body_crop_bbox: BBox | None = None
    mock_team: TeamValue = TeamValue.ENEMY
    balloon_bbox_present: bool = True
    body_pixel_count: int | None = None


class ColorCalibrationReferenceRequest(BaseModel):
    expected_team: TeamValue
    capture_id: str = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def expected_team_must_be_known(self) -> "ColorCalibrationReferenceRequest":
        if self.expected_team == TeamValue.UNKNOWN:
            raise ValueError("expected_team must be enemy or friend")
        return self


class ColorCalibrationReference(BaseModel):
    expected_team: TeamValue
    capture_id: str
    frame_id: int
    detection_id: int
    body_track_id: int | None = None
    body_pixel_count: int
    decision: TeamValue
    confidence: float
    profile_hash: str
    frame_hash: str | None = None
    recorded_at: float = Field(default_factory=time.time)


class ColorCalibrationStatus(BaseModel):
    valid: bool = False
    profile_hash: str | None = None
    enemy_reference_count: int = 0
    friend_reference_count: int = 0
    references: list[ColorCalibrationReference] = Field(default_factory=list)
    reason_codes: list[str] = Field(default_factory=lambda: ["A3_IFF_CALIBRATION_REQUIRED"])
    updated_at: float = Field(default_factory=time.time)


class ColorDecisionResult(BaseModel):
    frame_id: int
    detection_id: int
    body_crop_bbox: BBox | None = None
    balloon_mask_applied: bool
    body_pixel_count: int
    enemy_pixel_ratio: float
    friend_pixel_ratio: float
    unknown_pixel_ratio: float
    decision: TeamValue
    confidence: float
    blocking_warnings: list[str] = Field(default_factory=list)
    debug_masks_available: bool = False
    # ``mock_sample`` is deliberately never live-fire evidence.  Only a
    # body-ROI produced from the current camera frame may be promoted.
    evidence_source: str = "mock_sample"
    body_track_id: int | None = None
    temporal_frames: int = 0
    consistent_frames: int = 0
    profile_hash: str | None = None
    frame_hash: str | None = None
    usable_for_live_fire: bool = False
    updated_at: float


class MaskPreviewResult(BaseModel):
    frame_id: int
    detection_id: int
    balloon_mask_enabled: bool
    balloon_mask_applied: bool
    debug_masks_available: bool
    warnings: list[str] = Field(default_factory=list)
    updated_at: float
