from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from app.api.deps import get_runtime
from app.schemas.self_test import SelfTestCancelResult, SelfTestRun, SelfTestRunRequest, SelfTestStatus
from app.services.runtime_state import RuntimeState

router = APIRouter(prefix="/api/self-test", tags=["self-test"])


@router.get("/status", response_model=SelfTestStatus)
def status(runtime: RuntimeState = Depends(get_runtime)) -> SelfTestStatus:
    return runtime.self_test.status()


@router.post("/run", response_model=SelfTestRun)
def run_self_test(
    request: SelfTestRunRequest | None = None,
    runtime: RuntimeState = Depends(get_runtime),
) -> SelfTestRun:
    _ = request
    return runtime.self_test.run(runtime)


@router.post("/cancel", response_model=SelfTestCancelResult)
def cancel(runtime: RuntimeState = Depends(get_runtime)) -> SelfTestCancelResult:
    run = runtime.self_test.cancel()
    return SelfTestCancelResult(accepted=run is not None, reason="Self-test cancellation requested.", run=run)


@router.get("/runs", response_model=list[SelfTestRun])
def runs(runtime: RuntimeState = Depends(get_runtime)) -> list[SelfTestRun]:
    return runtime.self_test.list_runs()


@router.get("/runs/{run_id}", response_model=SelfTestRun)
def get_run(run_id: str, runtime: RuntimeState = Depends(get_runtime)) -> SelfTestRun:
    try:
        return runtime.self_test.get_run(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="self-test run not found") from exc


@router.get("/runs/{run_id}/report")
def get_report(run_id: str, runtime: RuntimeState = Depends(get_runtime)) -> FileResponse:
    try:
        run = runtime.self_test.get_run(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="self-test run not found") from exc
    if not run.report_path or not Path(run.report_path).exists():
        raise HTTPException(status_code=404, detail="self-test report not found")
    return FileResponse(run.report_path, media_type="text/markdown", filename=Path(run.report_path).name)


@router.post("/export-report", response_model=SelfTestRun)
def export_report(runtime: RuntimeState = Depends(get_runtime)) -> SelfTestRun:
    if runtime.self_test.latest_run is None:
        raise HTTPException(status_code=404, detail="no self-test run available")
    return runtime.self_test.export_report(runtime.self_test.latest_run)
