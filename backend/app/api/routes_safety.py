from fastapi import APIRouter, Depends, HTTPException, status
from starlette.responses import JSONResponse

from app.api.deps import get_runtime
from app.schemas.decision import ArmDisarmResult, DecisionState, FireEvaluationResult
from app.schemas.command_gateway import CommandProfile, GatewayPreflightResult
from app.schemas.log import LogLevel
from app.schemas.safety import FireRequest, SafetyState
from app.services.runtime_state import RuntimeState

router = APIRouter(prefix="/api/safety", tags=["safety"])


@router.get("/state", response_model=DecisionState)
def get_safety_state(runtime: RuntimeState = Depends(get_runtime)) -> DecisionState:
    return runtime.decision_engine.evaluate(runtime)


@router.get("/gates", response_model=SafetyState)
def get_safety_gates(runtime: RuntimeState = Depends(get_runtime)) -> SafetyState:
    decision = runtime.decision_engine.evaluate(runtime)
    return runtime.safety.state(decision)


@router.post("/arm", response_model=ArmDisarmResult)
def arm(runtime: RuntimeState = Depends(get_runtime)) -> ArmDisarmResult:
    decision = runtime.decision_engine.evaluate(runtime)
    blocking: list[str] = []
    serial_status = runtime.serial.status()
    if serial_status.connection_state == "FAULT":
        blocking.append("serial_fault")
    if runtime.config.system.hardware_enabled:
        blocking.append("unexpected_hardware_enabled")
    if blocking:
        result = ArmDisarmResult(accepted=False, armed=False, reason="Arm rejected by safety preconditions.", blocking_reasons=blocking, decision=decision)
    else:
        runtime.force_armed = True
        result = ArmDisarmResult(accepted=True, armed=True, reason="System armed for dry-run evaluation only.", decision=runtime.decision_engine.evaluate(runtime))
        runtime.last_safety_event = ("safety.armed", result.model_dump(mode="json"))
    runtime.logger.emit(LogLevel.INFO if result.accepted else LogLevel.WARN, "SAFETY", "Arm request evaluated", result.model_dump(mode="json"))
    return result


@router.post("/disarm", response_model=ArmDisarmResult)
def disarm(runtime: RuntimeState = Depends(get_runtime)) -> ArmDisarmResult:
    runtime.force_armed = False
    decision = runtime.decision_engine.evaluate(runtime)
    result = ArmDisarmResult(accepted=True, armed=False, reason="System disarmed. DISARM is always accepted.", decision=decision)
    runtime.last_safety_event = ("safety.disarmed", result.model_dump(mode="json"))
    runtime.logger.emit(LogLevel.INFO, "SAFETY", "System disarmed", result.model_dump(mode="json"))
    return result


@router.post("/fire-request", response_model=FireEvaluationResult)
def fire_request(
    request: FireRequest,
    runtime: RuntimeState = Depends(get_runtime),
) -> JSONResponse:
    result = runtime.decision_engine.fire_request(runtime, operator_confirmed=request.operator_confirmed)
    runtime.last_safety_event = (
        "safety.fire_request_accepted_dry_run" if result.accepted else "safety.fire_request_rejected",
        result.model_dump(mode="json"),
    )
    status_code = 200 if result.accepted else 403
    return JSONResponse(status_code=status_code, content=result.model_dump(mode="json"))


from pydantic import BaseModel

class OperationalModeRequest(BaseModel):
    mode: str
    actuator_arm: bool = False


class CommandProfileRequest(BaseModel):
    profile: CommandProfile
    actuator_arm: bool = False


class PreflightRequest(BaseModel):
    actuator_arm: bool = False


class PicoConnectRequest(BaseModel):
    port: str
    baudrate: int = 460800


_OPERATIONAL_MODES = {"no_motion", "motion_no_fire", "full_active", "DRY_RUN", "LIVE_TEST", "VIDEO_DEMO", "COMPETITION"}


def _profile_for_mode(mode: str) -> CommandProfile:
    mapping = {
        "no_motion": CommandProfile.DRY_RUN,
        "motion_no_fire": CommandProfile.LIVE_TEST,
        "full_active": CommandProfile.LIVE_TEST,
        "DRY_RUN": CommandProfile.DRY_RUN,
        "LIVE_TEST": CommandProfile.LIVE_TEST,
        "VIDEO_DEMO": CommandProfile.VIDEO_DEMO,
        "COMPETITION": CommandProfile.COMPETITION,
    }
    return mapping[mode]


@router.post("/set-operational-mode")
def set_operational_mode(
    request: OperationalModeRequest,
    runtime: RuntimeState = Depends(get_runtime),
) -> dict:
    if request.mode not in _OPERATIONAL_MODES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Unsupported operational mode: {request.mode}",
        )
    profile = _profile_for_mode(request.mode)
    preflight = runtime.command_gateway.select_profile(runtime, profile, actuator_arm_requested=request.actuator_arm)
    runtime.last_safety_event = ("safety.profile_selected", preflight.model_dump(mode="json"))
    return {
        "status": "success",
        "mode": request.mode,
        "profile": profile,
        "tracking_enabled": runtime.config.tracking.enabled,
        "allow_physical_motion": runtime.config.hardware.allow_physical_motion,
        "allow_physical_fire": runtime.config.hardware.allow_physical_fire,
        "dry_run": runtime.config.system.dry_run,
        "preflight": preflight.model_dump(mode="json"),
    }


@router.post("/command-profile", response_model=GatewayPreflightResult)
def select_command_profile(
    request: CommandProfileRequest,
    runtime: RuntimeState = Depends(get_runtime),
) -> GatewayPreflightResult:
    result = runtime.command_gateway.select_profile(runtime, request.profile, actuator_arm_requested=request.actuator_arm)
    runtime.last_safety_event = ("safety.profile_selected", result.model_dump(mode="json"))
    return result


@router.post("/preflight", response_model=GatewayPreflightResult)
def run_preflight(
    request: PreflightRequest,
    runtime: RuntimeState = Depends(get_runtime),
) -> GatewayPreflightResult:
    result = runtime.command_gateway.run_preflight(runtime, actuator_arm_requested=request.actuator_arm)
    runtime.last_safety_event = ("safety.preflight", result.model_dump(mode="json"))
    return result


@router.post("/pico-connect")
def connect_pico(request: PicoConnectRequest, runtime: RuntimeState = Depends(get_runtime)) -> dict:
    connected, reason_code = runtime.command_gateway.connect_pico(request.port, request.baudrate)
    return {"connected": connected, "reason_code": reason_code, "preflight": runtime.command_gateway.last_preflight.model_dump(mode="json")}


@router.get("/command-profile", response_model=GatewayPreflightResult)
def get_command_profile(runtime: RuntimeState = Depends(get_runtime)) -> GatewayPreflightResult:
    return runtime.command_gateway.last_preflight


@router.get("/operational-mode")
def get_operational_mode(runtime: RuntimeState = Depends(get_runtime)) -> dict:
    return {
        "mode": runtime.command_gateway.profile,
        "tracking_enabled": runtime.config.tracking.enabled,
        "allow_physical_motion": runtime.config.hardware.allow_physical_motion,
        "allow_physical_fire": runtime.config.hardware.allow_physical_fire,
        "dry_run": runtime.config.system.dry_run,
        "preflight": runtime.command_gateway.last_preflight.model_dump(mode="json"),
    }
