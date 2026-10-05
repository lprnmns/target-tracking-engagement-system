import time
from typing import Literal

from pydantic import BaseModel, Field


TargetType = Literal["f16", "helicopter", "ballistic_missile", "mini_micro_uav", "unknown"]
TeamLabel = Literal["enemy", "friend", "unknown"]
DistanceLabel = Literal["5", "10", "15", "custom"]
LaneLabel = Literal["left", "center", "right", "unknown"]
AngleLabel = Literal["front", "side", "diagonal", "top_pitch", "bottom_pitch", "partial_occlusion", "unknown"]
LightingLabel = Literal["indoor_led", "sunlight", "low_light", "mixed", "unknown"]
LensLabel = Literal["3.6mm", "8mm", "12mm", "varifocal_custom", "unknown"]
SessionMode = Literal["capture", "replay", "mock", "field_test"]
QualityLabel = Literal["good", "needs_review", "bad", "unreviewed"]


class SessionScenario(BaseModel):
    target_type: TargetType = "unknown"
    team: TeamLabel = "unknown"
    distance_m: DistanceLabel = "custom"
    lane: LaneLabel = "unknown"
    angle: AngleLabel = "unknown"
    color_profile: str | None = None
    lighting: LightingLabel = "unknown"
    lens_profile: LensLabel = "unknown"
    camera_resolution: str = "640x360"
    yolo_imgsz: int = 960
    active_model_ids: list[str] = Field(default_factory=list)
    notes: str | None = None


class SessionStats(BaseModel):
    frame_count: int = 0
    snapshot_count: int = 0
    detection_count: int = 0
    annotation_count: int = 0
    duration_sec: float = 0.0


class SessionSafety(BaseModel):
    dry_run: bool = True
    hardware_enabled: bool = False
    no_physical_command_generated: bool = True


class SessionRecord(BaseModel):
    session_id: str
    name: str
    created_at: float = Field(default_factory=time.time)
    ended_at: float | None = None
    operator: str = "operator"
    mode: SessionMode = "capture"
    scenario: SessionScenario = Field(default_factory=SessionScenario)
    stats: SessionStats = Field(default_factory=SessionStats)
    safety: SessionSafety = Field(default_factory=SessionSafety)
    quality: QualityLabel = "unreviewed"


class StartSessionRequest(BaseModel):
    name: str = "field_capture"
    operator: str = "operator"
    mode: SessionMode = "capture"
    scenario: SessionScenario = Field(default_factory=SessionScenario)


class RecordEventRequest(BaseModel):
    event_type: Literal["detection", "color_decision", "decision", "operator_action"] = "operator_action"
    payload: dict = Field(default_factory=dict)


class SnapshotResponse(BaseModel):
    session_id: str
    frame_id: str
    image_path: str
    metadata_path: str
    no_physical_command_generated: bool = True


class SessionQualityRequest(BaseModel):
    quality: QualityLabel
