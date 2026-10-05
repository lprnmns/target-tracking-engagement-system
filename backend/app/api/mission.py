import asyncio

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException

from app.api.deps import get_runtime
from app.schemas.mission import MissionSnapshot, MissionUpdate, Stage1HitRequest, Stage1ManualMotionRequest, Stage1PlanRequest, Stage1WrongTargetRequest, Stage2RoundCompleteRequest, Stage3RoundCompleteRequest
from app.schemas.stage2_engagement import Stage2EngagementStatus
from app.schemas.stage3_engagement import Stage3EngagementStatus
from app.services.runtime_state import RuntimeState

router = APIRouter(prefix="/api/mission", tags=["mission"])


@router.get("/status", response_model=MissionSnapshot)
def mission_status(runtime: RuntimeState = Depends(get_runtime)) -> MissionSnapshot:
    return runtime.mission.snapshot()


@router.put("/status", response_model=MissionSnapshot)
async def update_mission(update: MissionUpdate, runtime: RuntimeState = Depends(get_runtime)) -> MissionSnapshot:
    try:
        previous_stage = runtime.mission.state.active_stage
        snapshot = runtime.mission.update(update)
        if snapshot.state.active_stage != previous_stage:
            # A mission-mode transition cannot inherit an autonomous velocity,
            # queued trigger pulse or selected tracker target from its prior mode.
            runtime.command_gateway.stop_motion()
            await runtime.tracking_loop.stop()
            runtime.auto_tracker.stop_tracking()
            if previous_stage == "stage2" or snapshot.state.active_stage == "stage2":
                runtime.stage2_engagement.reset(snapshot.state.stage2_round)
            if previous_stage == "stage3" or snapshot.state.active_stage == "stage3":
                runtime.stage3_engagement.reset(snapshot.state.stage3_round)
            # Target/balloon ownership is mission-scoped. A stage transition
            # must not carry a stable balloon lock or stale priority/hit state
            # into a new stage where the same numeric ids can describe other
            # aircraft. The active tracker itself is reset by the next
            # tracking start/round-close boundary.
            runtime.auto_tracker.preferred_target_x = None
            runtime.auto_tracker.preferred_target_y = None
            runtime.association.reset()
            runtime.hit_confirmation.reset()
            runtime.target_registry.reset()
            runtime.last_motion_event = ("mission.mode_transition_safed", {"from": previous_stage, "to": snapshot.state.active_stage})
        return snapshot
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/reset", response_model=MissionSnapshot)
def reset_mission(runtime: RuntimeState = Depends(get_runtime)) -> MissionSnapshot:
    snapshot = runtime.mission.reset()
    runtime.stage2_engagement.reset(snapshot.state.stage2_round)
    runtime.stage3_engagement.reset(snapshot.state.stage3_round)
    runtime.auto_tracker.preferred_target_x = None
    runtime.auto_tracker.preferred_target_y = None
    runtime.association.reset()
    runtime.hit_confirmation.reset()
    runtime.target_registry.reset()
    return snapshot


@router.put("/stage1/plan", response_model=MissionSnapshot)
def configure_stage1_plan(request: Stage1PlanRequest, runtime: RuntimeState = Depends(get_runtime)) -> MissionSnapshot:
    try:
        return runtime.mission.configure_stage1_plan(request)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/stage1/plan/lock", response_model=MissionSnapshot)
def lock_stage1_plan(runtime: RuntimeState = Depends(get_runtime)) -> MissionSnapshot:
    try:
        return runtime.mission.start_stage1()
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/stage1/start", response_model=MissionSnapshot)
def start_stage1(runtime: RuntimeState = Depends(get_runtime)) -> MissionSnapshot:
    try:
        return runtime.mission.start_stage1()
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/stage1/hit", response_model=MissionSnapshot)
def record_stage1_hit(request: Stage1HitRequest, runtime: RuntimeState = Depends(get_runtime)) -> MissionSnapshot:
    try:
        return runtime.mission.record_stage1_hit(request)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/stage1/wrong-target", response_model=MissionSnapshot)
def record_stage1_wrong_target(request: Stage1WrongTargetRequest, runtime: RuntimeState = Depends(get_runtime)) -> MissionSnapshot:
    try:
        return runtime.mission.record_stage1_wrong_target(request)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


async def _stop_bounded_manual_motion(runtime: RuntimeState, duration_ms: int, lease_id: int) -> None:
    """Stop only when this remains the newest acknowledged keyboard lease."""
    await asyncio.sleep(duration_ms / 1000.0)
    if not runtime.command_gateway.manual_motion_lease_is_current(lease_id):
        return
    runtime.command_gateway.stop_motion()


@router.post("/manual-motion")
async def manual_motion(
    request: Stage1ManualMotionRequest,
    background_tasks: BackgroundTasks,
    runtime: RuntimeState = Depends(get_runtime),
) -> dict:
    if runtime.command_gateway.profile.value == "DRY_RUN":
        runtime.command_gateway.profile = runtime.command_gateway.profile.__class__.LIVE_TEST
        runtime.config.system.dry_run = False
        runtime.config.system.hardware_enabled = True
        runtime.config.hardware.physical_command_enabled = True
        runtime.config.hardware.allow_physical_motion = True
        runtime.config.motion.real_motion_enabled = True
        runtime.force_armed = True
    result = runtime.command_gateway.send_motion(runtime, request.speed_x, request.speed_y, origin="manual_operator")
    if not result.accepted:
        return {"accepted": False, "reason_codes": result.reason_codes, "detail": result.detail}
    # The Pico has already ACKed DRV/SPD here. A held key renews this lease
    # before it expires, so superseded timers do not cycle DRV,0/DRV,1 between
    # pulses. Releasing the key sends an explicit Gateway stop; loss of the
    # browser still expires the newest lease server-side.
    lease_id = runtime.command_gateway.renew_manual_motion_lease()
    background_tasks.add_task(_stop_bounded_manual_motion, runtime, request.duration_ms, lease_id)
    return {
        "accepted": True,
        "reason_codes": [],
        "detail": "Manual motion accepted by CommandGateway; renewable bounded lease active.",
        "pico_ack": result.pico_ack,
        "stop_scheduled": True,
        "lease_id": lease_id,
    }


@router.post("/stage2/round/complete", response_model=MissionSnapshot)
def complete_stage2_round(request: Stage2RoundCompleteRequest, runtime: RuntimeState = Depends(get_runtime)) -> MissionSnapshot:
    # Dry-run/replay scoring remains available for deterministic scoring unit
    # tests.  A live competition cannot supply a free-form hit count.
    if runtime.command_gateway.profile.value != "DRY_RUN":
        raise HTTPException(status_code=409, detail="A2_ENGAGEMENT_EVENT_API_REQUIRED")
    try:
        return runtime.mission.complete_stage2_round(request)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/stage2/engagement", response_model=Stage2EngagementStatus)
def stage2_engagement_status(runtime: RuntimeState = Depends(get_runtime)) -> Stage2EngagementStatus:
    return runtime.stage2_engagement.status()


@router.post("/stage2/round/close", response_model=MissionSnapshot)
def close_stage2_round(runtime: RuntimeState = Depends(get_runtime)) -> MissionSnapshot:
    try:
        snapshot = runtime.stage2_engagement.close_round(runtime.mission)
        # Official round boundary: inherit neither velocity, association nor
        # confirmation state into the next three-target pass.
        runtime.command_gateway.stop_motion()
        runtime.auto_tracker.multi_target_tracker.reset()
        runtime.auto_tracker.preferred_target_x = None
        runtime.auto_tracker.preferred_target_y = None
        runtime.association.reset()
        runtime.target_priority.reset()
        runtime.hit_confirmation.reset()
        runtime.target_registry.reset()
        runtime.last_motion_event = ("mission.stage2_round_safed", {"next_round": snapshot.state.stage2_round})
        return snapshot
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/stage3/round/complete", response_model=MissionSnapshot)
def complete_stage3_round(request: Stage3RoundCompleteRequest, runtime: RuntimeState = Depends(get_runtime)) -> MissionSnapshot:
    if runtime.command_gateway.profile.value != "DRY_RUN":
        raise HTTPException(status_code=409, detail="A3_ENGAGEMENT_EVENT_API_REQUIRED")
    try:
        return runtime.mission.complete_stage3_round(request)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/stage3/engagement", response_model=Stage3EngagementStatus)
def stage3_engagement_status(runtime: RuntimeState = Depends(get_runtime)) -> Stage3EngagementStatus:
    return runtime.stage3_engagement.status()


@router.post("/stage3/round/close", response_model=MissionSnapshot)
def close_stage3_round(runtime: RuntimeState = Depends(get_runtime)) -> MissionSnapshot:
    try:
        snapshot = runtime.stage3_engagement.close_round(runtime.mission)
        runtime.command_gateway.stop_motion()
        runtime.auto_tracker.multi_target_tracker.reset()
        runtime.auto_tracker.preferred_target_x = None
        runtime.auto_tracker.preferred_target_y = None
        runtime.association.reset()
        runtime.target_priority.reset()
        runtime.hit_confirmation.reset()
        runtime.target_registry.reset()
        runtime.last_motion_event = ("mission.stage3_round_safed", {"next_round": snapshot.state.stage3_round})
        return snapshot
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
