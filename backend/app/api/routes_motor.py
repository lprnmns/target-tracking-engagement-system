from fastapi import APIRouter, Depends
from starlette.responses import JSONResponse

from app.api.deps import get_runtime
from app.schemas.safety import MotorJogRequest, SafetyCommandResult
from app.services.runtime_state import RuntimeState

router = APIRouter(prefix="/api/motor", tags=["motor"])


@router.post("/jog", response_model=SafetyCommandResult)
def motor_jog(
    request: MotorJogRequest,
    runtime: RuntimeState = Depends(get_runtime),
) -> JSONResponse:
    result = runtime.safety.reject_command("MOTOR_JOG")
    content = result.model_dump(mode="json")
    content["request"] = request.model_dump(mode="json")
    return JSONResponse(status_code=403, content=content)


@router.post("/driver/enable")
def enable_driver(runtime: RuntimeState = Depends(get_runtime)) -> dict:
    return {
        "status": "rejected",
        "reason_code": "COMMAND_GATEWAY_REQUIRED",
        "message": "Legacy driver endpoint is disabled; select a live profile and run CommandGateway preflight.",
    }


@router.post("/driver/disable")
def disable_driver(runtime: RuntimeState = Depends(get_runtime)) -> dict:
    return {
        "status": "rejected",
        "reason_code": "COMMAND_GATEWAY_REQUIRED",
        "message": "Legacy driver endpoint is disabled; Gateway safing owns physical driver disable.",
    }

