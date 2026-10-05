from fastapi import APIRouter, Depends

from app.api.deps import get_runtime
from app.schemas.demo import DemoReadiness, DemoTimeline
from app.services.runtime_state import RuntimeState

router = APIRouter(prefix="/api/demo", tags=["demo"])


@router.get("/timeline", response_model=DemoTimeline)
def timeline(runtime: RuntimeState = Depends(get_runtime)) -> DemoTimeline:
    return runtime.demo.timeline(runtime)


@router.post("/run", response_model=DemoTimeline)
def run(runtime: RuntimeState = Depends(get_runtime)) -> DemoTimeline:
    return runtime.demo.run(runtime)


@router.get("/latest", response_model=DemoTimeline)
def latest(runtime: RuntimeState = Depends(get_runtime)) -> DemoTimeline:
    return runtime.demo.latest()


@router.get("/readiness", response_model=DemoReadiness)
def readiness(runtime: RuntimeState = Depends(get_runtime)) -> DemoReadiness:
    return runtime.demo.readiness(runtime)


@router.post("/jury-rehearsal/run")
def run_jury_rehearsal(runtime: RuntimeState = Depends(get_runtime)) -> dict:
    return runtime.demo.run_jury_rehearsal(runtime)


@router.get("/jury-rehearsal/latest")
def latest_jury_rehearsal(runtime: RuntimeState = Depends(get_runtime)) -> dict:
    return runtime.demo.latest_jury()
