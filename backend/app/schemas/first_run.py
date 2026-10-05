import time
from typing import Any, Literal

from pydantic import BaseModel, Field


FirstRunStepStatus = Literal["pending", "passed", "warning", "failed"]
ReadinessProfileName = Literal[
    "development_ready",
    "demo_ready",
    "field_dry_run_ready",
    "hardware_telemetry_ready",
    "competition_rehearsal_ready",
    "release_candidate_ready",
]
ReadinessProfileStatus = Literal["not_evaluated", "passed", "warning", "failed", "blocked"]
CurrentFirstRunStatus = Literal["open", "passed", "warning", "failed"]


class FirstRunStep(BaseModel):
    step_id: str
    title: str
    status: FirstRunStepStatus
    explanation: str
    suggested_fix: str | None = None
    blocking: bool = False
    detail: dict[str, Any] = Field(default_factory=dict)


class FirstRunReport(BaseModel):
    run_id: str
    created_at: float = Field(default_factory=time.time)
    mode: str
    completed: bool = False
    overall_status: FirstRunStepStatus = "pending"
    steps: list[FirstRunStep] = Field(default_factory=list)
    summary: dict[str, Any] = Field(default_factory=dict)
    profile_statuses: dict[ReadinessProfileName, ReadinessProfileStatus] = Field(default_factory=dict)
    profile_checklists: dict[ReadinessProfileName, list[FirstRunStep]] = Field(default_factory=dict)
    report_path: str | None = None
    no_physical_command_generated: bool = True


class FirstRunStatus(BaseModel):
    completed: bool = False
    latest_report: FirstRunReport | None = None
    mode: str
    checks_count: int = 0
    current_first_run_status: CurrentFirstRunStatus = "open"
    current_profile_id: ReadinessProfileName = "release_candidate_ready"
    current_profile_evaluation_status: ReadinessProfileStatus = "not_evaluated"
    last_successful_first_run: dict[str, Any] | None = None
    stale_evidence: bool = False
    no_physical_command_generated: bool = True


class FirstRunActionResult(BaseModel):
    accepted: bool
    reason: str
    status: FirstRunStatus
