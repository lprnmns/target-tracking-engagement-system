from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_runtime
from app.schemas.report_export import ReportExportRecord, ReportExportRequest, ReportsStatus
from app.services.runtime_state import RuntimeState

router = APIRouter(prefix="/api/reports", tags=["reports"])


@router.get("", response_model=ReportsStatus)
def root_status(runtime: RuntimeState = Depends(get_runtime)) -> ReportsStatus:
    return runtime.report_export.status()


@router.get("/status", response_model=ReportsStatus)
def status(runtime: RuntimeState = Depends(get_runtime)) -> ReportsStatus:
    return runtime.report_export.status()


@router.post("/generate-ktr-summary", response_model=ReportExportRecord)
def generate_ktr_summary(
    request: ReportExportRequest | None = None,
    runtime: RuntimeState = Depends(get_runtime),
) -> ReportExportRecord:
    return runtime.ktr_export.generate_summary(runtime, request)


@router.post("/generate-demo-pack", response_model=ReportExportRecord)
def generate_demo_pack(
    request: ReportExportRequest | None = None,
    runtime: RuntimeState = Depends(get_runtime),
) -> ReportExportRecord:
    return runtime.report_export.generate_demo_pack(runtime, request)


@router.post("/generate-readiness-pack", response_model=ReportExportRecord)
def generate_readiness_pack(
    request: ReportExportRequest | None = None,
    runtime: RuntimeState = Depends(get_runtime),
) -> ReportExportRecord:
    return runtime.report_export.generate_readiness_pack(runtime, request)


@router.get("/exports", response_model=list[ReportExportRecord])
def list_exports(runtime: RuntimeState = Depends(get_runtime)) -> list[ReportExportRecord]:
    return runtime.report_export.list_exports()


@router.get("/exports/{export_id}", response_model=ReportExportRecord)
def get_export(export_id: str, runtime: RuntimeState = Depends(get_runtime)) -> ReportExportRecord:
    try:
        return runtime.report_export.get_export(export_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="report export not found") from exc
