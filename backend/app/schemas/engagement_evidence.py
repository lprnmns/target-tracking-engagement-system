"""Read-only evidence records created from target lock and acknowledged shots."""

from __future__ import annotations

import time
from typing import Any, Literal

from pydantic import BaseModel, Field


EngagementEvidenceState = Literal["LOCKED_RECORDING", "SHOT_PENDING_CONFIRMATION", "COMPLETED", "ABORTED"]


class EngagementEvidenceSummary(BaseModel):
    engagement_id: str
    shot_id: str | None = None
    state: EngagementEvidenceState
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)
    mission_stage: str = "unknown"
    command_profile: str = "DRY_RUN"
    body_track_id: int | None = None
    body_detection_id: int | None = None
    balloon_track_id: int | None = None
    balloon_detection_id: int | None = None
    target_class: str | None = None
    target_team: str | None = None
    association_state: str = "unresolved"
    frame_id: int | None = None
    reason_codes: list[str] = Field(default_factory=list)
    evidence_path: str
    camera_capture_status: str = "TIMELINE_ONLY"
    outcome: str = "PENDING"
    no_physical_command_generated: bool = True


class EngagementEvidenceStatus(BaseModel):
    active: EngagementEvidenceSummary | None = None
    recent: list[EngagementEvidenceSummary] = Field(default_factory=list)
    pre_roll_frame_count: int = 0
    writer_queue_depth: int = 0
    dropped_timeline_entries: int = 0
    no_physical_command_generated: bool = True


class EngagementEvidenceManifest(BaseModel):
    schema_version: str = "engagement_evidence.v1"
    summary: EngagementEvidenceSummary
    monotonic_ns: int
    lock_snapshot: dict[str, Any]
    shot_snapshot: dict[str, Any] | None = None
    outcome: dict[str, Any] | None = None
    camera_capture: dict[str, Any] = Field(default_factory=dict)
    digital_twin_capture: dict[str, Any] = Field(default_factory=dict)
    no_physical_command_generated: bool = True


class EngagementEvidenceRecordList(BaseModel):
    records: list[EngagementEvidenceSummary] = Field(default_factory=list)
    no_physical_command_generated: bool = True
