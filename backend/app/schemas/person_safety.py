from typing import Literal

from pydantic import BaseModel, Field


RecommendedPersonSafetyState = Literal["CLEAR", "SAFE_HOLD", "FIRE_BLOCKED"]


class PersonSafetyGateStatus(BaseModel):
    enabled: bool = True
    person_detected: bool = False
    fire_gate_blocked_reason: str | None = None
    recommended_state: RecommendedPersonSafetyState = "CLEAR"
    confidence_threshold: float = Field(ge=0.0, le=1.0)
    hold_ms: int = Field(ge=0)
    clear_after_ms: int = Field(gt=0)
    last_detection_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    last_detection_class: str | None = None
    last_detection_id: int | None = None
    last_detection_timestamp_ms: int | None = None
    active_until_ms: int | None = None
    source: str = "vision_pipeline_read_only"
    no_physical_command_generated: bool = True


class PersonSafetyConfigUpdate(BaseModel):
    enabled: bool | None = None
    confidence_threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    hold_ms: int | None = Field(default=None, ge=0)
    clear_after_ms: int | None = Field(default=None, gt=0)
