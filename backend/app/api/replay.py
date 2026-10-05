from fastapi import APIRouter, Depends

from app.api.deps import get_runtime
from app.schemas.replay import ReplayLoadRequest, ReplaySeekRequest, ReplaySpeedRequest, ReplayStatus
from app.services.runtime_state import RuntimeState

router = APIRouter(prefix="/api/replay", tags=["replay"])


@router.get("/status", response_model=ReplayStatus)
def replay_status(runtime: RuntimeState = Depends(get_runtime)) -> ReplayStatus:
    return runtime.replay.status


@router.post("/load-session", response_model=ReplayStatus)
def load_session(request: ReplayLoadRequest, runtime: RuntimeState = Depends(get_runtime)) -> ReplayStatus:
    return runtime.replay.load_session(request.session_id)


@router.post("/play", response_model=ReplayStatus)
def play(runtime: RuntimeState = Depends(get_runtime)) -> ReplayStatus:
    return runtime.replay.play()


@router.post("/pause", response_model=ReplayStatus)
def pause(runtime: RuntimeState = Depends(get_runtime)) -> ReplayStatus:
    return runtime.replay.pause()


@router.post("/stop", response_model=ReplayStatus)
def stop(runtime: RuntimeState = Depends(get_runtime)) -> ReplayStatus:
    return runtime.replay.stop()


@router.post("/seek", response_model=ReplayStatus)
def seek(request: ReplaySeekRequest, runtime: RuntimeState = Depends(get_runtime)) -> ReplayStatus:
    return runtime.replay.seek(request.frame_index)


@router.post("/step", response_model=ReplayStatus)
def step(runtime: RuntimeState = Depends(get_runtime)) -> ReplayStatus:
    return runtime.replay.step()


@router.put("/speed", response_model=ReplayStatus)
def speed(request: ReplaySpeedRequest, runtime: RuntimeState = Depends(get_runtime)) -> ReplayStatus:
    return runtime.replay.speed(request.speed)
