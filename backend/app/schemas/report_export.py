import time
from typing import Literal

from pydantic import BaseModel, Field


ReportExportKind = Literal["ktr_summary", "demo_pack", "readiness_pack"]
ReportExportStatus = Literal["idle", "running", "completed", "failed"]


class ReportExportRequest(BaseModel):
    title: str | None = None
    include_screenshots: bool | None = None
    notes: str | None = None


class ReportExportRecord(BaseModel):
    export_id: str
    kind: ReportExportKind
    status: ReportExportStatus
    created_at: float = Field(default_factory=time.time)
    output_dir: str
    files: list[str] = Field(default_factory=list)
    summary: dict = Field(default_factory=dict)
    no_physical_command_generated: bool = True
    error: str | None = None


class ReportsStatus(BaseModel):
    exports_count: int
    latest_export: ReportExportRecord | None = None
    root_dir: str
    no_physical_command_generated: bool = True
