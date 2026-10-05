from fastapi import APIRouter, Depends

from app.api.deps import get_runtime
from app.schemas.digital_twin import (
    DigitalTwinAssetsResponse,
    DigitalTwinReplayGenerateResult,
    DigitalTwinReplaySummary,
    DigitalTwinState,
)
from app.services.runtime_state import RuntimeState

router = APIRouter(prefix="/api/digital-twin", tags=["digital-twin"])


@router.get("/state", response_model=DigitalTwinState)
def get_digital_twin_state(runtime: RuntimeState = Depends(get_runtime)) -> DigitalTwinState:
    return runtime.digital_twin.state_cached(runtime)


@router.get("/assets", response_model=DigitalTwinAssetsResponse)
def get_digital_twin_assets(runtime: RuntimeState = Depends(get_runtime)) -> DigitalTwinAssetsResponse:
    return runtime.digital_twin.assets()


@router.get("/replay/latest", response_model=DigitalTwinReplaySummary)
def get_latest_digital_twin_replay(runtime: RuntimeState = Depends(get_runtime)) -> DigitalTwinReplaySummary:
    return runtime.digital_twin.latest_replay()


@router.get("/replay/{run_id}", response_model=DigitalTwinReplaySummary)
def get_digital_twin_replay(run_id: str, runtime: RuntimeState = Depends(get_runtime)) -> DigitalTwinReplaySummary:
    replay = runtime.digital_twin.latest_replay()
    return replay.model_copy(update={"run_id": run_id or replay.run_id})


@router.post("/replay/generate", response_model=DigitalTwinReplayGenerateResult)
def generate_digital_twin_replay(runtime: RuntimeState = Depends(get_runtime)) -> DigitalTwinReplayGenerateResult:
    return runtime.digital_twin.generate_replay_report()


@router.post("/panel-rendered")
def digital_twin_panel_rendered(runtime: RuntimeState = Depends(get_runtime)) -> dict[str, bool | str]:
    return runtime.digital_twin.panel_rendered()
