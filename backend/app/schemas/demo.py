import time
from typing import Literal

from pydantic import BaseModel, Field


DemoStepStatus = Literal["pending", "completed", "warning", "blocked"]
DemoStepSource = Literal["system", "first_run", "vision", "data_lab", "report", "safety"]


class DemoTimelineEvent(BaseModel):
    event_id: str
    step: str
    title: str
    status: DemoStepStatus
    source: DemoStepSource
    timestamp: float = Field(default_factory=time.time)
    summary: str
    evidence_ref: str | None = None
    advisory_only: bool = True
    no_physical_command_generated: bool = True


class DemoVerdict(BaseModel):
    release_demo_ready: bool = False
    release_demo_warnings: list[str] = Field(default_factory=list)
    release_demo_blockers: list[str] = Field(default_factory=list)
    competition_ready: bool = False
    competition_blockers: list[str] = Field(default_factory=list)
    dataset_ready_for_training: bool = False
    dataset_blockers: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    advisory_only: bool = True
    no_physical_command_generated: bool = True


class DemoTimeline(BaseModel):
    run_id: str
    created_at: float = Field(default_factory=time.time)
    status: Literal["not_run", "completed", "warning", "blocked"] = "not_run"
    events: list[DemoTimelineEvent] = Field(default_factory=list)
    verdict: DemoVerdict = Field(default_factory=DemoVerdict)
    report_export_id: str | None = None
    advisory_only: bool = True
    no_physical_command_generated: bool = True


class DemoReadiness(BaseModel):
    release_demo_ready: bool
    release_demo_warnings: list[str] = Field(default_factory=list)
    release_demo_blockers: list[str] = Field(default_factory=list)
    competition_ready: bool
    competition_blockers: list[str] = Field(default_factory=list)
    dataset_ready_for_training: bool
    dataset_blockers: list[str] = Field(default_factory=list)
    no_physical_command_generated: bool = True
