from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_runtime
from app.schemas.motion import (
    MotionCommandResponse,
    MotionGoToRequest,
    MotionJogRequest,
    MotionSettings,
    MotionState,
    MotionTrackDryRunRequest,
)
from app.services.runtime_state import RuntimeState

router = APIRouter(prefix="/api/motion", tags=["motion"])


def _armed(runtime: RuntimeState) -> bool:
    return runtime.system_state().armed


def _store_event(runtime: RuntimeState, response: MotionCommandResponse) -> None:
    if response.command_type in {"stop", "scan_stop"} and response.accepted:
        event_type = "motion.stopped"
    elif response.accepted:
        event_type = "motion.command_accepted_dry_run"
    else:
        event_type = "motion.command_rejected"
    runtime.last_motion_event = (event_type, response.model_dump(mode="json"))


@router.get("/status", response_model=MotionState)
def get_motion_status(runtime: RuntimeState = Depends(get_runtime)) -> MotionState:
    return runtime.motion.status()


@router.get("/settings", response_model=MotionSettings)
def get_motion_settings(runtime: RuntimeState = Depends(get_runtime)) -> MotionSettings:
    return runtime.motion.settings


@router.put("/settings", response_model=MotionSettings)
def update_motion_settings(
    settings: MotionSettings,
    runtime: RuntimeState = Depends(get_runtime),
) -> MotionSettings:
    try:
        updated = runtime.motion.update_settings(settings, system_armed=_armed(runtime))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    runtime.last_motion_event = ("motion.settings_updated", updated.model_dump(mode="json"))
    return updated


@router.post("/jog", response_model=MotionCommandResponse)
def jog_motion(
    request: MotionJogRequest,
    runtime: RuntimeState = Depends(get_runtime),
) -> MotionCommandResponse:
    response = runtime.motion.jog(request, system_armed=_armed(runtime))
    _store_event(runtime, response)
    return response


@router.post("/go-to", response_model=MotionCommandResponse)
def go_to_motion(
    request: MotionGoToRequest,
    runtime: RuntimeState = Depends(get_runtime),
) -> MotionCommandResponse:
    response = runtime.motion.go_to(request, system_armed=_armed(runtime))
    _store_event(runtime, response)
    return response


@router.post("/home", response_model=MotionCommandResponse)
def home_motion(runtime: RuntimeState = Depends(get_runtime)) -> MotionCommandResponse:
    response = runtime.motion.home(system_armed=_armed(runtime))
    _store_event(runtime, response)
    return response


@router.post("/stop", response_model=MotionCommandResponse)
def stop_motion(runtime: RuntimeState = Depends(get_runtime)) -> MotionCommandResponse:
    response = runtime.motion.stop()
    _store_event(runtime, response)
    return response


@router.post("/scan/start", response_model=MotionCommandResponse)
def start_scan(runtime: RuntimeState = Depends(get_runtime)) -> MotionCommandResponse:
    response = runtime.motion.scan_start(system_armed=_armed(runtime))
    _store_event(runtime, response)
    return response


@router.post("/scan/stop", response_model=MotionCommandResponse)
def stop_scan(runtime: RuntimeState = Depends(get_runtime)) -> MotionCommandResponse:
    response = runtime.motion.scan_stop()
    _store_event(runtime, response)
    return response


@router.post("/track-dry-run", response_model=MotionCommandResponse)
def track_dry_run(
    request: MotionTrackDryRunRequest,
    runtime: RuntimeState = Depends(get_runtime),
) -> MotionCommandResponse:
    response = runtime.motion.track_dry_run(request, system_armed=_armed(runtime))
    _store_event(runtime, response)
    return response


# ---- Tracking (Kapalı Çevrim Takip) ----

import asyncio
from pydantic import BaseModel, Field

from app.schemas.tracking import TrackingConfigUpdate, TrackingStatus, TrackingTargetSelectRequest
from app.schemas.tracking import AssociationStatus, EngagementStatus, TargetPriorityStatus, TargetRegistryStatus


class TrackingTrialStartRequest(BaseModel):
    preset_id: str


class TrackingTrialRateRequest(BaseModel):
    trial_id: str
    rating: int = Field(ge=1, le=5)
    note: str = ""


@router.get("/tracking/tuning")
def tracking_tuning_status(runtime: RuntimeState = Depends(get_runtime)) -> dict:
    return runtime.tracking_tuning.status()


@router.post("/tracking/tuning/start")
async def tracking_tuning_start(request: TrackingTrialStartRequest, runtime: RuntimeState = Depends(get_runtime)) -> dict:
    if runtime.auto_tracker.tracking_active:
        await runtime.tracking_loop.stop()
        runtime.auto_tracker.stop_tracking()
    try:
        status = runtime.tracking_tuning.start(request.preset_id, runtime.auto_tracker)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    runtime.auto_tracker.start_tracking()
    await runtime.tracking_loop.start()
    return status


@router.post("/tracking/tuning/stop")
async def tracking_tuning_stop(runtime: RuntimeState = Depends(get_runtime)) -> dict:
    await runtime.tracking_loop.stop()
    runtime.auto_tracker.stop_tracking()
    return runtime.tracking_tuning.finish()


@router.post("/tracking/tuning/rate")
def tracking_tuning_rate(request: TrackingTrialRateRequest, runtime: RuntimeState = Depends(get_runtime)) -> dict:
    return runtime.tracking_tuning.rate(request.trial_id, request.rating, request.note)


@router.post("/tracking/tuning/apply/{preset_id}")
async def tracking_tuning_apply(preset_id: str, runtime: RuntimeState = Depends(get_runtime)) -> dict:
    try:
        runtime.tracking_tuning.apply_preset(preset_id, runtime.auto_tracker)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return runtime.tracking_tuning.status()


@router.post("/tracking/start", response_model=TrackingStatus)
async def start_tracking(runtime: RuntimeState = Depends(get_runtime)) -> TrackingStatus:
    operation = getattr(runtime, "operation", None)
    if operation is not None:
        state = operation.state()
        if state.configured and state.control_mode.value != "AUTONOMOUS":
            raise HTTPException(status_code=409, detail="AUTONOMOUS_MODE_REQUIRED")
        # An explicit selection is an operator handoff, not an autonomous
        # acquisition prerequisite. With no selection the policy-filtered
        # tracker takes the closest eligible target to the aim point.
    status = runtime.auto_tracker.start_tracking()
    await runtime.tracking_loop.start()
    runtime.last_motion_event = ("tracking.started", status.model_dump(mode="json"))
    return status


@router.post("/tracking/stop", response_model=TrackingStatus)
async def stop_tracking(runtime: RuntimeState = Depends(get_runtime)) -> TrackingStatus:
    await runtime.tracking_loop.stop()
    status = runtime.auto_tracker.stop_tracking()
    runtime.last_motion_event = ("tracking.stopped", status.model_dump(mode="json"))
    return status


@router.get("/tracking/status", response_model=TrackingStatus)
def tracking_status(runtime: RuntimeState = Depends(get_runtime)) -> TrackingStatus:
    return runtime.auto_tracker.status()


@router.get("/tracking/associations", response_model=AssociationStatus)
def tracking_associations(runtime: RuntimeState = Depends(get_runtime)) -> AssociationStatus:
    return runtime.association.status()


@router.get("/tracking/priority", response_model=TargetPriorityStatus)
def tracking_priority(runtime: RuntimeState = Depends(get_runtime)) -> TargetPriorityStatus:
    return runtime.target_priority.status()


@router.get("/tracking/engagements", response_model=EngagementStatus)
def tracking_engagements(runtime: RuntimeState = Depends(get_runtime)) -> EngagementStatus:
    return runtime.hit_confirmation.status()


@router.get("/tracking/targets", response_model=TargetRegistryStatus)
def tracking_targets(runtime: RuntimeState = Depends(get_runtime)) -> TargetRegistryStatus:
    """Persistent body/balloon identities for operator and evidence HUDs."""
    return runtime.target_registry.status()


@router.put("/tracking/config", response_model=TrackingStatus)
async def update_tracking_config(
    update: TrackingConfigUpdate,
    runtime: RuntimeState = Depends(get_runtime),
) -> TrackingStatus:
    status = runtime.auto_tracker.update_config(update)
    runtime.last_motion_event = ("tracking.config_updated", status.model_dump(mode="json"))
    return status


@router.post("/tracking/select-target", response_model=TrackingStatus)
async def select_tracking_target(
    request: TrackingTargetSelectRequest,
    runtime: RuntimeState = Depends(get_runtime),
) -> TrackingStatus:
    # Friendly aircraft balloons remain non-trackable engagement proxies.
    # Explicit body selections are allowed for observation and are kept
    # physically NO_FIRE by CommandGateway.
    if request.detection_id is not None:
        logical_target = (
            runtime.target_registry.target_for_balloon_detection(request.detection_id)
            if request.kind != "body"
            else None
        )
        if logical_target is not None and not logical_target.engagement_allowed:
            raise HTTPException(status_code=409, detail="FRIEND_BALLOON_NOT_TRACKABLE")
        if logical_target is None and request.kind != "body":
            # The first click can arrive before the registry snapshot catches
            # up with the current frame.  Resolve the live association directly
            # so a friendly balloon cannot start a misleading tracking state.
            event = runtime.vision_pipeline.latest()
            if event is not None:
                track = next(
                    (
                        item
                        for item in runtime.auto_tracker.multi_target_tracker.status().tracks
                        if item.detection_id == request.detection_id
                    ),
                    None,
                )
                if track is not None:
                    association = next(
                        (item for item in runtime.association.status().associations if item.balloon_track_id == track.track_id),
                        None,
                    )
                    body = next(
                        (item for item in event.body_detections if association is not None and item.id == association.body_detection_id),
                        None,
                    )
                    if body is not None and str(body.target_team).lower() in {"friend", "dost"}:
                        raise HTTPException(status_code=409, detail="FRIEND_BALLOON_NOT_TRACKABLE")
    status = runtime.auto_tracker.select_target(
        x=request.x,
        y=request.y,
        detection_id=request.detection_id,
        frame_id=request.frame_id,
        kind=request.kind,
    )
    if not runtime.auto_tracker.tracking_active:
        status = runtime.auto_tracker.start_tracking()
    await runtime.tracking_loop.start()
    runtime.last_motion_event = ("tracking.target_selected", status.model_dump(mode="json"))
    return status
