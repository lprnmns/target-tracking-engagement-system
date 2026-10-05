import time
from typing import Any, Literal

from pydantic import BaseModel, Field


SelfTestRunStatus = Literal["idle", "running", "passed", "warning", "failed", "cancelled"]
SelfTestReadinessLevel = Literal["not_ready", "demo_ready", "hardware_readonly_ready", "field_test_ready", "hardware_blocked"]
SelfTestStepStatus = Literal["pending", "running", "passed", "warning", "failed", "skipped"]
SelfTestStepCategory = Literal[
    "system",
    "backend",
    "frontend",
    "config",
    "safety",
    "hardware",
    "pico",
    "serial",
    "vision",
    "model",
    "motion",
    "dataset",
    "replay",
    "logging",
    "interface",
    "deployment",
    "first_run",
]
SelfTestSeverity = Literal["info", "warning", "critical"]


class SelfTestStep(BaseModel):
    step_id: str
    name: str
    category: SelfTestStepCategory
    status: SelfTestStepStatus = "pending"
    severity: SelfTestSeverity = "info"
    started_at: float | None = None
    ended_at: float | None = None
    duration_ms: float | None = None
    message: str = ""
    details: dict[str, Any] = Field(default_factory=dict)
    blocking: bool = False
    suggested_action: str | None = None


class SelfTestRun(BaseModel):
    run_id: str
    started_at: float = Field(default_factory=time.time)
    ended_at: float | None = None
    status: SelfTestRunStatus = "idle"
    overall_ready: bool = False
    readiness_level: SelfTestReadinessLevel = "not_ready"
    dry_run: bool = True
    hardware_enabled: bool = False
    no_physical_command_generated: bool = True
    steps: list[SelfTestStep] = Field(default_factory=list)
    summary: dict[str, Any] = Field(default_factory=dict)
    report_path: str | None = None


class SelfTestStatus(BaseModel):
    latest_run: SelfTestRun | None = None
    running: bool = False
    runs_count: int = 0


class SelfTestRunRequest(BaseModel):
    include_warnings: bool = True


class SelfTestCancelResult(BaseModel):
    accepted: bool
    reason: str
    run: SelfTestRun | None = None
