"""Persisted, fail-closed range-calibration contract for Aşama 3."""

from __future__ import annotations

import time
from pydantic import BaseModel, Field, field_validator


STAGE3_CLASSES = ("f16", "helicopter", "ballistic_missile", "mini_micro_uav")


class Stage3RangeObservationCreate(BaseModel):
    class_name: str
    distance_m: float = Field(gt=0, le=30)
    bbox_height_px: float = Field(gt=0)
    capture_id: str = Field(min_length=1, max_length=128)
    note: str | None = Field(default=None, max_length=500)

    @field_validator("class_name")
    @classmethod
    def known_class(cls, value: str) -> str:
        normalised = value.strip().lower().replace("-", "_").replace(" ", "_")
        if normalised not in STAGE3_CLASSES:
            raise ValueError("class_name must be a supported stage3 target class")
        return normalised


class Stage3RangeObservation(Stage3RangeObservationCreate):
    observation_id: str
    recorded_at: float = Field(default_factory=time.time)


class Stage3RangeClassFit(BaseModel):
    class_name: str
    scale_px_m: float
    sample_count: int
    calibration_distances_m: list[float]
    mean_abs_error_m: float
    uncertainty_m: float


class Stage3RangeEstimate(BaseModel):
    class_name: str
    range_m: float | None = None
    uncertainty_m: float | None = None
    lower_bound_m: float | None = None
    upper_bound_m: float | None = None
    calibration_hash: str | None = None
    ready: bool = False
    reason_code: str


class Stage3RangeCalibrationStatus(BaseModel):
    valid: bool = False
    reason_codes: list[str] = Field(default_factory=lambda: ["A3_RANGE_CALIBRATION_UNAVAILABLE"])
    body_model_id: str | None = None
    body_model_hash: str | None = None
    calibration_hash: str | None = None
    observations: list[Stage3RangeObservation] = Field(default_factory=list)
    fits: list[Stage3RangeClassFit] = Field(default_factory=list)
    validated_at: float | None = None
    updated_at: float = Field(default_factory=time.time)
