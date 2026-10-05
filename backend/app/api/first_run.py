from fastapi import APIRouter, Depends

from app.api.deps import get_runtime
from app.schemas.first_run import FirstRunActionResult, FirstRunReport, FirstRunStatus
from app.services.runtime_state import RuntimeState

router = APIRouter(prefix="/api/first-run", tags=["first-run"])


@router.get("/status", response_model=FirstRunStatus)
def status(runtime: RuntimeState = Depends(get_runtime)) -> FirstRunStatus:
    return runtime.first_run.status(runtime)


@router.post("/check", response_model=FirstRunReport)
def check(runtime: RuntimeState = Depends(get_runtime)) -> FirstRunReport:
    return runtime.first_run.check(runtime)


@router.post("/mark-complete", response_model=FirstRunActionResult)
def mark_complete(runtime: RuntimeState = Depends(get_runtime)) -> FirstRunActionResult:
    return runtime.first_run.mark_complete(runtime)


@router.post("/reset", response_model=FirstRunActionResult)
def reset(runtime: RuntimeState = Depends(get_runtime)) -> FirstRunActionResult:
    return runtime.first_run.reset(runtime)


@router.get("/report", response_model=FirstRunReport)
def report(runtime: RuntimeState = Depends(get_runtime)) -> FirstRunReport:
    return runtime.first_run.report(runtime)
