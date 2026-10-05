import math
import time

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.api.deps import get_runtime
from app.schemas.operation import (
    CompetitionStage,
    ControlMode,
    FirePermission,
    OperationState,
    OperationTargetSelection,
    OperationUpdate,
    PathAngleConfig,
    PathAngleUpdate,
    TargetPolicy,
)
from app.schemas.mission import MissionUpdate
from app.services.runtime_state import RuntimeState
from app.services import yarisma_bayraklari as yb
from app.services.safety_timing import MAX_VISION_EVENT_AGE_S

router = APIRouter(prefix="/api/operation", tags=["operation"])


def _selection_event_max_age_s(event) -> float:
    """Allow one detector interval without weakening live control freshness."""
    detector_fps = float(getattr(event, "detector_fps", None) or getattr(event, "fps", 0.0) or 0.0)
    detector_interval = 1.0 / detector_fps if detector_fps > 0 else MAX_VISION_EVENT_AGE_S
    return min(2.0, max(MAX_VISION_EVENT_AGE_S, detector_interval * 2.5))


@router.get("/state", response_model=OperationState)
def get_operation_state(runtime: RuntimeState = Depends(get_runtime)) -> OperationState:
    return runtime.operation.state()


@router.put("/state", response_model=OperationState)
async def update_operation_state(
    update: OperationUpdate,
    runtime: RuntimeState = Depends(get_runtime),
) -> OperationState:
    previous = runtime.operation.state()
    state = runtime.operation.update(update)
    normal_stop_applied = False

    async def stop_for_operation_handoff(reason: str) -> None:
        nonlocal normal_stop_applied
        if normal_stop_applied:
            return
        await runtime.tracking_loop.stop(preserve_actuator_arm=True, reason=reason)
        runtime.auto_tracker.stop_tracking()
        normal_stop_applied = True

    if state.control_mode != previous.control_mode:
        runtime.tracking_loop.reset_fire_candidate()
        if state.control_mode.value == "MANUAL":
            await stop_for_operation_handoff("operator_mode_manual")
    if state.target_policy != previous.target_policy:
        await stop_for_operation_handoff("operator_target_policy_change")
        runtime.tracking_loop.reset_fire_candidate()
        runtime.target_priority.reset()
        runtime.association.reset()
        runtime.target_registry.reset()
        runtime.auto_tracker.set_target_policy(state.target_policy.value)
        runtime.auto_tracker.clear_target()
    if state.fire_permission != previous.fire_permission:
        # Enabling FIRE while an old target has remained centred must require
        # a fresh stable inner-lock window. Disabling it also cancels a pending
        # candidate without stopping tracking.
        runtime.tracking_loop.reset_fire_candidate()
        if state.fire_permission.value == "ENABLED":
            runtime.command_gateway.actuator_armed = True
            runtime.command_gateway.actuator_arm_requested = True
            runtime.command_gateway.last_preflight = runtime.command_gateway.last_preflight.model_copy(
                update={"physical_fire_enabled": True, "actuator_armed": True, "ready": True}
            )
            runtime.config.hardware.allow_physical_fire = True
            runtime.force_armed = True
        elif state.fire_permission.value == "DISABLED":
            runtime.command_gateway.actuator_armed = False
            runtime.command_gateway.actuator_arm_requested = False
            runtime.command_gateway.last_preflight = runtime.command_gateway.last_preflight.model_copy(
                update={"physical_fire_enabled": False, "actuator_armed": False}
            )
            runtime.command_gateway._release_fire_output(force=True)
    # Operation state is authoritative: autonomous mode acquires the first
    # eligible policy target immediately; pausing/manual mode stops it.  This
    # also restarts acquisition after a policy switch without a second click.
    if state.control_mode.value == "AUTONOMOUS" and state.tracking_requested:
        runtime.auto_tracker.start_tracking()
        await runtime.tracking_loop.start()
        if state.competition_stage in (CompetitionStage.STAGE_2, CompetitionStage.STAGE_3):
            # Y11: duraklatma/devam (ya da ilk baslatma) sonrasi radar once MERKEZE doner
            runtime.patrol_radar.start_patrol(force_reset=bool(yb.Y11_RADAR_MERKEZ and not previous.tracking_requested))
    elif previous.tracking_requested and not state.tracking_requested:
        await stop_for_operation_handoff("operator_tracking_paused")
        if state.control_mode.value == "MANUAL" or state.competition_stage == CompetitionStage.STAGE_1:
            runtime.patrol_radar.stop_patrol()
    elif state.control_mode.value == "MANUAL" or state.competition_stage == CompetitionStage.STAGE_1:
        runtime.patrol_radar.stop_patrol()
    if state.competition_stage is not None and hasattr(runtime, "mission"):
        stg_map = {
            CompetitionStage.STAGE_1: "stage1",
            CompetitionStage.STAGE_2: "stage2",
            CompetitionStage.STAGE_3: "stage3",
        }
        target_stage = stg_map.get(state.competition_stage)
        if target_stage and getattr(runtime.mission.state, "active_stage", "") != target_stage:
            runtime.mission.update(MissionUpdate(active_stage=target_stage))
    runtime.last_motion_event = ("operation.state_updated", state.model_dump(mode="json"))
    return state


@router.post("/target", response_model=OperationState)
async def select_operation_target(
    selection: OperationTargetSelection,
    runtime: RuntimeState = Depends(get_runtime),
) -> OperationState:
    event = runtime.vision.latest_event
    if event is None or not (-1.0 <= time.time() - float(event.timestamp_ms) / 1000.0 <= _selection_event_max_age_s(event)):
        raise HTTPException(status_code=409, detail="TARGET_DETECTION_NOT_FRESH")
    policy = runtime.operation.state().target_policy.value
    if selection.detection_kind.value == "body":
        detection = next((item for item in event.body_detections if item.id == selection.detection_id), None)
        if detection is None:
            raise HTTPException(status_code=409, detail="TARGET_DETECTION_NOT_FRESH")
        if policy == "BALLOON":
            raise HTTPException(status_code=409, detail="TARGET_POLICY_BALLOON_ONLY")
        # Automatic AIRCRAFT acquisition remains enemy-only in the tracking
        # registry. An explicit operator click may follow a friendly/unknown
        # aircraft for observation; CommandGateway keeps FIRE enemy-only.
        if selection.body_track_id is not None and detection.track_id != selection.body_track_id:
            raise HTTPException(status_code=409, detail="TARGET_TRACK_NOT_FRESH")
        selection = selection.model_copy(update={"body_track_id": detection.track_id})
    else:
        detection = next((item for item in event.balloon_detections if item.id == selection.detection_id), None)
        if detection is None:
            raise HTTPException(status_code=409, detail="TARGET_DETECTION_NOT_FRESH")
        if policy == "AIRCRAFT":
            raise HTTPException(status_code=409, detail="TARGET_POLICY_AIRCRAFT_ONLY")
    if selection.logical_target_id:
        logical = next((item for item in runtime.target_registry.status().targets if item.target_id == selection.logical_target_id), None)
        if logical is not None and (logical.state.value == "DESTROYED" or logical.consumed):
            raise HTTPException(status_code=409, detail="TARGET_ALREADY_DESTROYED")
    previous = runtime.operation.state()
    keep_tracking = bool(
        previous.control_mode.value == "AUTONOMOUS"
        and (previous.tracking_requested or runtime.auto_tracker.tracking_active)
    )
    # Cancel the previous physical intent before swapping ids. The tracker
    # remains alive during an autonomous handoff but starts the new target
    # with cleared PID/lead/lock state.
    runtime.command_gateway.stop_motion()
    runtime.tracking_loop.reset_fire_candidate()
    state = runtime.operation.select_target(selection, keep_tracking=keep_tracking)
    runtime.auto_tracker.select_target(
        x=selection.x,
        y=selection.y,
        detection_id=selection.detection_id,
        frame_id=selection.frame_id,
        kind=selection.detection_kind.value,
    )
    if state.control_mode.value == "AUTONOMOUS" and state.tracking_requested:
        runtime.auto_tracker.start_tracking()
        await runtime.tracking_loop.start()
    runtime.last_motion_event = ("operation.target_selected", state.model_dump(mode="json"))
    return state


@router.post("/target/clear", response_model=OperationState)
async def clear_operation_target(runtime: RuntimeState = Depends(get_runtime)) -> OperationState:
    state = runtime.operation.clear_target()
    runtime.auto_tracker.clear_target()
    runtime.tracking_loop.reset_fire_candidate()
    runtime.target_priority.reset()
    await runtime.tracking_loop.stop(preserve_actuator_arm=True, reason="operator_target_cleared")
    runtime.auto_tracker.stop_tracking()
    runtime.last_motion_event = ("operation.target_cleared", state.model_dump(mode="json"))
    return state


@router.get("/patrol/status")
def get_patrol_status(runtime: RuntimeState = Depends(get_runtime)) -> dict:
    return runtime.patrol_radar.status()


@router.post("/patrol/start")
def start_patrol(runtime: RuntimeState = Depends(get_runtime)) -> dict:
    return runtime.patrol_radar.start_patrol()


@router.post("/patrol/stop")
def stop_patrol(runtime: RuntimeState = Depends(get_runtime)) -> dict:
    return runtime.patrol_radar.stop_patrol()


@router.post("/patrol/angles")
def update_patrol_angles(
    config: PathAngleUpdate, runtime: RuntimeState = Depends(get_runtime)
) -> dict:
    runtime.patrol_radar.update_angles(
        path1_deg=config.path1_deg,
        path2_deg=config.path2_deg,
        path3_deg=config.path3_deg,
        dwell_time_s=config.dwell_time_s,
        strategy=config.strategy,
        sweep_speed_dps=config.sweep_speed_dps,
        sweep_sector_deg=config.sweep_sector_deg,
        scan_tilt_deg=config.scan_tilt_deg,
    )
    return runtime.patrol_radar.status()


@router.post("/patrol/goto-path")
def goto_patrol_path(
    path_number: int = Query(..., ge=1, le=3),
    runtime: RuntimeState = Depends(get_runtime),
) -> dict:
    status = runtime.patrol_radar.goto_path(path_number)
    return {
        "ok": True,
        "path_number": path_number,
        "status": status,
    }


@router.post("/patrol/capture-angle")
def capture_patrol_angle(
    path_number: int = Query(..., ge=1, le=3),
    runtime: RuntimeState = Depends(get_runtime),
) -> dict:
    if hasattr(runtime.command_gateway, "get_position"):
        try:
            runtime.command_gateway.get_position(runtime)
        except Exception:
            pass
    motion_state = runtime.motion.status()
    current_pan = getattr(motion_state, "physical_pan_deg", None)
    if current_pan is None or math.isnan(current_pan):
        current_pan = motion_state.pan_position_deg + 135.0
    captured = runtime.patrol_radar.capture_angle(path_number, current_pan)
    return {
        "ok": True,
        "path_number": path_number,
        "captured_angle_deg": captured,
        "status": runtime.patrol_radar.status(),
    }


class AxisLockRequest(BaseModel):
    axis: str = "tilt"
    locked: bool = True


@router.post("/patrol/axis-lock")
def set_axis_lock(
    req: AxisLockRequest,
    runtime: RuntimeState = Depends(get_runtime),
) -> dict:
    locked = False
    if hasattr(runtime.command_gateway, "set_axis_lock"):
        locked = runtime.command_gateway.set_axis_lock(req.axis, req.locked)
    return {
        "ok": True,
        "axis": req.axis,
        "locked": locked,
    }


@router.post("/stage/{stage_name}", response_model=OperationState)
async def set_competition_stage(
    stage_name: str,
    runtime: RuntimeState = Depends(get_runtime),
) -> OperationState:
    stage = stage_name.upper().strip()
    if stage in {"STAGE_1", "STAGE1", "ASAMA1", "AŞAMA1"}:
        update = OperationUpdate(
            control_mode=ControlMode.MANUAL,
            target_policy=TargetPolicy.STAGE1_INDEPENDENT,
            competition_stage=CompetitionStage.STAGE_1,
        )
    elif stage in {"STAGE_2", "STAGE2", "ASAMA2", "AŞAMA2"}:
        runtime.patrol_radar.config.strategy = "SMOOTH_SWEEP"
        update = OperationUpdate(
            control_mode=ControlMode.AUTONOMOUS,
            target_policy=TargetPolicy.BALLOON,
            competition_stage=CompetitionStage.STAGE_2,
        )
    elif stage in {"STAGE_3", "STAGE3", "ASAMA3", "AŞAMA3"}:
        runtime.patrol_radar.config.strategy = "SMOOTH_SWEEP"
        update = OperationUpdate(
            control_mode=ControlMode.AUTONOMOUS,
            target_policy=TargetPolicy.BALLOON_AIRCRAFT,
            competition_stage=CompetitionStage.STAGE_3,
        )
    else:
        raise HTTPException(status_code=400, detail=f"Bilinmeyen aşama: {stage_name}")

    return await update_operation_state(update, runtime=runtime)


@router.post("/stage2/start", response_model=OperationState)
async def start_stage2_mission(runtime: RuntimeState = Depends(get_runtime)) -> OperationState:
    """Aşama 2: Otonom tekil balon avlama görevini başlatır ve radar devriyesini aktif eder."""
    if hasattr(runtime.command_gateway, "get_position"):
        try:
            runtime.command_gateway.get_position(runtime)
        except Exception:
            pass
    runtime.patrol_radar.start_patrol(force_reset=True, strategy="SMOOTH_SWEEP")
    update = OperationUpdate(
        control_mode=ControlMode.AUTONOMOUS,
        target_policy=TargetPolicy.BALLOON,
        competition_stage=CompetitionStage.STAGE_2,
        fire_permission=FirePermission.ENABLED,
        tracking_requested=True,
    )
    return await update_operation_state(update, runtime=runtime)


@router.post("/stage2/stop", response_model=OperationState)
async def stop_stage2_mission(runtime: RuntimeState = Depends(get_runtime)) -> OperationState:
    """Aşama 2: Otonom görevi durdurur ve ateş iznini kapatır."""
    runtime.patrol_radar.stop_patrol()
    update = OperationUpdate(
        control_mode=ControlMode.MANUAL,
        fire_permission=FirePermission.DISABLED,
        tracking_requested=False,
    )
    return await update_operation_state(update, runtime=runtime)


@router.post("/stage3/start-round")
async def start_stage3_round(
    round_number: int | None = Query(default=None, ge=1, le=8),
    runtime: RuntimeState = Depends(get_runtime),
) -> dict:
    """Aşama 3: Verilen turu başlatır, radar devriyesini ve otonom düşman aramasını açar."""
    current_round = round_number or getattr(runtime.stage3_engagement.status(), "current_round", 1) or 1
    runtime.stage3_engagement.reset(current_round=current_round)
    if hasattr(runtime.command_gateway, "get_position"):
        try:
            runtime.command_gateway.get_position(runtime)
        except Exception:
            pass
    runtime.patrol_radar.start_patrol(force_reset=True, strategy="SMOOTH_SWEEP")
    update = OperationUpdate(
        control_mode=ControlMode.AUTONOMOUS,
        target_policy=TargetPolicy.BALLOON_AIRCRAFT,
        competition_stage=CompetitionStage.STAGE_3,
        fire_permission=FirePermission.ENABLED,
        tracking_requested=True,
    )
    op_state = await update_operation_state(update, runtime=runtime)
    return {
        "ok": True,
        "current_round": current_round,
        "max_rounds": 8,
        "operation_state": op_state,
        "patrol": runtime.patrol_radar.status(),
        "stage3_status": runtime.stage3_engagement.status(),
    }


@router.post("/stage3/next-round")
async def next_stage3_round(runtime: RuntimeState = Depends(get_runtime)) -> dict:
    """Aşama 3: Mevcut tur tamamlandığında bir sonraki tura geçer (1/8 -> 2/8 vb.)."""
    curr = getattr(runtime.stage3_engagement.status(), "current_round", 1) or 1
    next_round = min(8, curr + 1)
    runtime.stage3_engagement.reset(current_round=next_round)
    runtime.patrol_radar.start_patrol(force_reset=bool(yb.Y11_RADAR_MERKEZ), strategy="SMOOTH_SWEEP")  # Y11: yeni turda merkezden basla
    return {
        "ok": True,
        "current_round": next_round,
        "max_rounds": 8,
        "completed": next_round == 8 and curr == 8,
        "patrol": runtime.patrol_radar.status(),
        "stage3_status": runtime.stage3_engagement.status(),
    }


@router.post("/stage3/reset-rounds")
async def reset_stage3_rounds(runtime: RuntimeState = Depends(get_runtime)) -> dict:
    """Aşama 3: Tur sayacını 1. tura sıfırlar."""
    runtime.stage3_engagement.reset(current_round=1)
    runtime.patrol_radar.stop_patrol()
    return {
        "ok": True,
        "current_round": 1,
        "max_rounds": 8,
        "stage3_status": runtime.stage3_engagement.status(),
    }


