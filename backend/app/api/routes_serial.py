from fastapi import APIRouter, Depends

from app.api.deps import get_runtime
from pydantic import BaseModel, Field

from app.schemas.serial import (
    SerialCommandResult,
    SerialLogEntry,
    SerialSendJsonRequest,
    SerialSimulateRxRequest,
    SerialStatus,
)
from app.services.runtime_state import RuntimeState

router = APIRouter(prefix="/api/serial", tags=["serial"])


class MagazineResetRequest(BaseModel):
    capacity: int = Field(default=8, ge=8, le=8)


@router.get("/status", response_model=SerialStatus)
def get_serial_status(runtime: RuntimeState = Depends(get_runtime)) -> SerialStatus:
    return runtime.serial.status()


@router.get("/logs", response_model=list[SerialLogEntry])
def get_serial_logs(runtime: RuntimeState = Depends(get_runtime)) -> list[SerialLogEntry]:
    return runtime.serial.recent_logs()


@router.post("/send-json", response_model=SerialCommandResult)
def send_json(
    request: SerialSendJsonRequest,
    runtime: RuntimeState = Depends(get_runtime),
) -> SerialCommandResult:
    return runtime.serial.send_json(request)


@router.post("/clear-logs", response_model=SerialCommandResult)
def clear_logs(runtime: RuntimeState = Depends(get_runtime)) -> SerialCommandResult:
    return runtime.serial.clear_logs()


@router.post("/magazine/reset", response_model=SerialCommandResult)
def reset_magazine(
    request: MagazineResetRequest,
    runtime: RuntimeState = Depends(get_runtime),
) -> SerialCommandResult:
    return runtime.serial.reset_magazine(request.capacity)


@router.post("/simulate-rx", response_model=SerialCommandResult)
def simulate_rx(
    request: SerialSimulateRxRequest,
    runtime: RuntimeState = Depends(get_runtime),
) -> SerialCommandResult:
    return runtime.serial.simulate_rx(request)


class SerialExchangeRequest(BaseModel):
    command: str
    expected_ack: list[str] = []


@router.post("/exchange")
def serial_exchange(
    request: SerialExchangeRequest,
    runtime: RuntimeState = Depends(get_runtime),
) -> dict:
    expected = tuple(request.expected_ack) if request.expected_ack else ()
    resp = runtime.serial.gateway_exchange(request.command, expected)
    return {
        "accepted": resp.accepted,
        "reason": resp.reason,
        "last_rx": runtime.serial.last_rx,
    }


@router.get("/light-stats")
def get_light_stats(runtime: RuntimeState = Depends(get_runtime)) -> dict:
    thread = getattr(runtime.serial, "_light_tx_thread", None)
    q = getattr(runtime.serial, "_light_tx_queue", None)
    return {
        "thread_alive": thread.is_alive() if thread else False,
        "queue_size": q.qsize() if q else -1,
        "written": getattr(runtime.serial, "light_tx_written", 0),
        "last_cmd": getattr(runtime.serial, "light_tx_last_cmd", ""),
        "last_err": getattr(runtime.serial, "light_tx_last_err", ""),
        "driver_enabled": runtime.command_gateway.driver_enabled,
        "real_transport_open": runtime.serial._real_transport.is_open if runtime.serial._real_transport else False,
    }


