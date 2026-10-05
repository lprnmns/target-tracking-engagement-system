import time
from typing import Any

from pydantic import BaseModel, Field

from app.schemas.session import SessionRecord


class DataLabDetectionRecord(BaseModel):
    frame_id: int | str
    source: str
    camera_source_kind: str | None = None
    frame_origin: str | None = None
    detector_kind: str | None = None
    body_count: int = 0
    balloon_count: int = 0
    detections: list[dict[str, Any]] = Field(default_factory=list)
    latency_ms: float | None = None
    camera_fps: float | None = None
    detector_fps: float | None = None
    advisory_only: bool = True
    no_physical_command_generated: bool = True


class DataLabSessionSummary(BaseModel):
    session_id: str
    name: str
    created_at: float
    ended_at: float | None = None
    mode: str
    scenario: dict[str, Any]
    stats: dict[str, Any]
    safety: dict[str, Any]
    quality: str
    latest_detection: DataLabDetectionRecord | None = None
    advisory_only: bool = True
    no_physical_command_generated: bool = True


class DataLabStatus(BaseModel):
    generated_at: float = Field(default_factory=time.time)
    sessions_count: int
    latest_session_id: str | None = None
    latest_detection: DataLabDetectionRecord | None = None
    export_root: str
    replay_status: str = "replay_execution_not_implemented"
    replay_ready: bool
    advisory_only: bool = True
    no_physical_command_generated: bool = True
    warnings: list[str] = Field(default_factory=list)


class DataLabRecordResponse(BaseModel):
    accepted: bool
    session: SessionRecord
    detection_record: DataLabDetectionRecord
    no_physical_command_generated: bool = True


class DataLabExportResponse(BaseModel):
    accepted: bool = True
    export_id: str
    created_at: float
    output_dir: str
    files: list[str]
    sessions_count: int
    detection_events_count: int
    advisory_only: bool = True
    no_physical_command_generated: bool = True


class DataLabReplayResult(BaseModel):
    replay_id: str
    source_session_id: str | None = None
    frame_origin: str = "not_available"
    detector: str = "not_available"
    replay_status: str = "idle"
    frames_replayed: int = 0
    events_replayed: int = 0
    detections_replayed: int = 0
    advisory_only: bool = True
    no_physical_command_generated: bool = True
    replay_execution_not_physical: bool = True
    created_at: float = Field(default_factory=time.time)
    warnings: list[str] = Field(default_factory=list)


class DataLabAnnotationCandidate(BaseModel):
    candidate_id: str
    session_id: str
    frame_id: int | str = "unknown"
    class_name: str = "unknown"
    target_group: str = "unknown"
    bbox: list[float] | dict[str, Any] | None = None
    circle: dict[str, Any] | None = None
    confidence: float | None = None
    source: str = "unknown"
    detector: str = "not_available"
    review_status: str = "pending"
    reviewer_note: str | None = None
    advisory_only: bool = True
    no_physical_command_generated: bool = True


class DataLabAnnotationReviewRequest(BaseModel):
    candidate_id: str
    status: str = Field(pattern="^(accepted|rejected|uncertain|pending)$")
    reviewer_note: str | None = None


class DataLabDatasetHealth(BaseModel):
    sessions_count: int
    detection_events_count: int
    annotation_candidates: int
    accepted_annotations: int
    rejected_annotations: int
    uncertain_annotations: int = 0
    class_distribution: dict[str, int] = Field(default_factory=dict)
    source_distribution: dict[str, int] = Field(default_factory=dict)
    dataset_ready_for_training: bool = False
    reason: str = "only mock/surrogate evidence or insufficient real data"
    advisory_only: bool = True
    no_physical_command_generated: bool = True
