from fastapi import APIRouter, Depends

from app.api.deps import get_runtime
from app.schemas.hardware import (
    HardwareCapabilities,
    HardwareConnectReadOnlyRequest,
    HardwareConnectResult,
    HardwareCommandBlockResult,
    HardwareRiskyCommandRequest,
    HardwareSerialPort,
    HardwareStatus,
    HardwareTelemetry,
    HardwareServoTuneRequest,
    HardwarePicoDiscoveryResult,
    HardwareTestJogRequest,
    HardwareTestCommandResult,
    HardwareHomeResult,
    HardwareMotionEnvelope,
    HardwareMotionEnvelopeResult,
    HardwareConnectionState,
    HardwareTransportMode,
)
from app.schemas.log import LogLevel
from app.services.runtime_state import RuntimeState
import asyncio
import time

router = APIRouter(prefix="/api/hardware", tags=["hardware"])


@router.post("/discover-pico", response_model=HardwarePicoDiscoveryResult)
async def discover_pico(runtime: RuntimeState = Depends(get_runtime)) -> HardwarePicoDiscoveryResult:
    port, code, detected_baudrate = await asyncio.to_thread(runtime.serial.discover_gateway_pico, 5.0, 460800)
    return HardwarePicoDiscoveryResult(
        found=port is not None, port=port, baudrate=detected_baudrate or 460800, reason_code=code,
        detail=f"Pico bulundu: {port}" if port else "5 saniye içinde Pico doğrulanamadı.",
    )


@router.get("/serial/ports", response_model=list[HardwareSerialPort])
def serial_ports(runtime: RuntimeState = Depends(get_runtime)) -> list[HardwareSerialPort]:
    return runtime.hardware.ports()


@router.get("/status", response_model=HardwareStatus)
def hardware_status(runtime: RuntimeState = Depends(get_runtime)) -> HardwareStatus:
    runtime.hardware.poll_readonly()
    legacy = runtime.hardware.status(mock_pico_active=runtime.pico.status().mock_mode)
    serial = runtime.serial.status()
    # The legacy PicoService is an offline/mock evidence adapter. Once the
    # operator has selected a real raw-protocol Pico, SerialService is the only
    # live connection authority; never let the mock adapter overwrite it with
    # a simultaneous DISCONNECTED badge.
    if serial.transport_source != "real_serial" or not serial.real_serial_enabled:
        return legacy

    connection_state = (
        HardwareConnectionState.FAULT
        if serial.connection_state.value == "FAULT"
        else HardwareConnectionState.PICO_VERIFIED
        if serial.pico_verified
        else HardwareConnectionState.PORT_OPEN_NO_TELEMETRY
    )
    last_raw = str((serial.last_rx or {}).get("raw") or "") or None
    telemetry = legacy.telemetry.model_copy(
        update={
            "connection_state": connection_state,
            "transport_mode": HardwareTransportMode.REAL_COMMAND,
            "port": runtime.config.serial.port,
            "baudrate": runtime.config.serial.baudrate,
            "heartbeat_age_ms": serial.heartbeat_age_ms,
            "device": "Pico raw serial",
            "estop_state": runtime.command_gateway.pico_estop_active,
            "driver_enabled": runtime.command_gateway.driver_enabled,
            "safe_state": not runtime.command_gateway.driver_enabled,
            "physical_outputs_enabled": runtime.command_gateway.driver_enabled,
            "port_open": serial.transport_healthy,
            "telemetry_received": serial.pico_verified,
            "pico_verified": serial.pico_verified,
            "telemetry_firmware_detected": serial.pico_verified,
            "physical_commands_disabled": not serial.physical_command_enabled,
            "last_raw_message": last_raw,
            "last_error": serial.last_transport_fault,
            "updated_at": time.time(),
            "no_physical_command_generated": True,
        }
    )
    warnings = []
    if serial.last_protocol_nack:
        warnings.append(f"Son komut reddi (bağlantı kopması değil): {serial.last_protocol_nack}")
    if serial.last_query_timeout:
        warnings.append(f"Son sorgu timeout: {serial.last_query_timeout}")
    return legacy.model_copy(
        update={
            "connection_state": connection_state,
            "mock_pico_active": False,
            "physical_pico": "connected" if serial.transport_healthy else "fault",
            "transport_mode": HardwareTransportMode.REAL_COMMAND,
            "readonly": False,
            "physical_command_enabled": serial.physical_command_enabled,
            "telemetry_available": serial.pico_verified,
            "port_open": serial.transport_healthy,
            "telemetry_received": serial.pico_verified,
            "pico_verified": serial.pico_verified,
            "telemetry_firmware_detected": serial.pico_verified,
            "physical_commands_disabled": not serial.physical_command_enabled,
            "transport_source": "real_serial",
            "telemetry": telemetry,
            "warnings": warnings,
            "no_physical_command_generated": True,
        }
    )


@router.post("/connect-readonly", response_model=HardwareConnectResult)
def connect_readonly(
    request: HardwareConnectReadOnlyRequest,
    runtime: RuntimeState = Depends(get_runtime),
) -> HardwareConnectResult:
    result = runtime.hardware.connect_readonly(request, mock_pico_active=runtime.pico.status().mock_mode)
    if result.accepted:
        runtime.serial.mark_real_readonly_connected(result.status.connection_state)
    return result


@router.post("/disconnect", response_model=HardwareConnectResult)
def disconnect(runtime: RuntimeState = Depends(get_runtime)) -> HardwareConnectResult:
    result = runtime.hardware.disconnect(mock_pico_active=runtime.pico.status().mock_mode)
    runtime.serial.mark_real_readonly_disconnected()
    return result


@router.get("/telemetry", response_model=HardwareTelemetry)
def telemetry(runtime: RuntimeState = Depends(get_runtime)) -> HardwareTelemetry:
    return runtime.hardware.poll_readonly()


@router.get("/capabilities", response_model=HardwareCapabilities)
def capabilities(runtime: RuntimeState = Depends(get_runtime)) -> HardwareCapabilities:
    return runtime.hardware.capabilities()


@router.post("/block-risky-command", response_model=HardwareCommandBlockResult)
def block_risky_command(
    request: HardwareRiskyCommandRequest,
    runtime: RuntimeState = Depends(get_runtime),
) -> HardwareCommandBlockResult:
    return runtime.hardware.block_risky_command(request)

@router.post("/test-trigger", response_model=HardwareTestCommandResult)
async def test_trigger(runtime: RuntimeState = Depends(get_runtime)) -> HardwareTestCommandResult:
    result = runtime.command_gateway.test_trigger(runtime, pulse_s=1.0)
    return HardwareTestCommandResult(
        accepted=result.accepted,
        message="Boş hazne tetik testi Gateway üzerinden kabul edildi." if result.accepted else f"Tetik testi engellendi: {', '.join(result.reason_codes)}",
        command=result.command, command_sent=result.physical_command_generated, pico_response=result.detail,
        driver_ack=result.pico_ack, reason_codes=result.reason_codes,
    )

@router.post("/test-servo-tune", response_model=HardwareTestCommandResult)
async def test_servo_tune(
    request: HardwareServoTuneRequest,
    runtime: RuntimeState = Depends(get_runtime),
) -> HardwareTestCommandResult:
    result = runtime.command_gateway.configure_trigger_servo(runtime, request.release_deg, request.fire_deg)
    return HardwareTestCommandResult(
        accepted=result.accepted,
        message="Servo başlangıç ve ateş açıları Pico'ya uygulandı." if result.accepted else f"Servo ayarı engellendi: {', '.join(result.reason_codes)}",
        command=result.command, command_sent=result.physical_command_generated, pico_response=result.detail,
        driver_ack=result.pico_ack, reason_codes=result.reason_codes,
    )

@router.post("/test-jog", response_model=HardwareTestCommandResult)
async def test_jog(request: HardwareTestJogRequest, runtime: RuntimeState = Depends(get_runtime)) -> HardwareTestCommandResult:
    result = runtime.command_gateway.send_motion(runtime, request.speed_x, request.speed_y)
    if not result.accepted:
        return HardwareTestCommandResult(
            accepted=False,
            message=f"Motion blocked by CommandGateway: {', '.join(result.reason_codes) or result.detail}",
            command=result.command,
            command_sent=False,
            pico_response=result.detail,
            driver_ack=result.pico_ack,
            reason_codes=result.reason_codes,
        )
    await asyncio.sleep(request.duration_ms / 1000.0)
    stop = runtime.command_gateway.stop_motion()
    accepted = result.accepted and stop.accepted
    reason_codes = [*result.reason_codes, *stop.reason_codes]
    # `send_motion` returns the DRV,1 acknowledgement in pico_ack only when
    # the driver had to be enabled.  The SPD response is the command ACK on
    # subsequent tests, so expose both in the same visible result instead of
    # making the UI guess that a missing driver_ack means no Pico response.
    acknowledgements = [item for item in (result.pico_ack, result.detail if result.accepted else None) if item]

    return HardwareTestCommandResult(
        accepted=accepted,
        message=(
            f"Jog tested: {request.speed_x}, {request.speed_y} for {request.duration_ms}ms."
            if accepted
            else f"Jog test failed or safe-stop was not acknowledged: {', '.join(reason_codes) or result.detail}"
        ),
        command=result.command,
        command_sent=result.physical_command_generated,
        pico_response=result.detail,
        driver_ack="; ".join(acknowledgements) if acknowledgements else None,
        safe_stop_response=stop.detail,
        reason_codes=reason_codes,
    )


@router.post("/manual-stop", response_model=HardwareTestCommandResult)
async def manual_stop(runtime: RuntimeState = Depends(get_runtime)) -> HardwareTestCommandResult:
    result = runtime.command_gateway.stop_motion()
    return HardwareTestCommandResult(
        accepted=result.accepted,
        message="Manual motion safe-stop sent through CommandGateway." if result.accepted else f"Safe-stop failed: {', '.join(result.reason_codes)}",
    )


@router.post("/home", response_model=HardwareHomeResult)
async def start_home(runtime: RuntimeState = Depends(get_runtime)) -> HardwareHomeResult:
    return await asyncio.to_thread(runtime.command_gateway.start_home, runtime)


@router.post("/home/stop", response_model=HardwareHomeResult)
async def stop_home(runtime: RuntimeState = Depends(get_runtime)) -> HardwareHomeResult:
    return await asyncio.to_thread(runtime.command_gateway.stop_home, runtime)


@router.get("/home/status", response_model=HardwareHomeResult)
async def home_status(runtime: RuntimeState = Depends(get_runtime)) -> HardwareHomeResult:
    return await asyncio.to_thread(runtime.command_gateway.home_status, runtime)


@router.get("/position", response_model=HardwareHomeResult)
async def position_status(runtime: RuntimeState = Depends(get_runtime)) -> HardwareHomeResult:
    return await asyncio.to_thread(runtime.command_gateway.position_status, runtime)


@router.get("/motion-envelope", response_model=HardwareMotionEnvelopeResult)
def get_motion_envelope(runtime: RuntimeState = Depends(get_runtime)) -> HardwareMotionEnvelopeResult:
    return HardwareMotionEnvelopeResult(accepted=True, envelope=runtime.command_gateway.motion_envelope, detail="Aktif izinli hareket zarfı.")


@router.put("/motion-envelope", response_model=HardwareMotionEnvelopeResult)
async def update_motion_envelope(
    envelope: HardwareMotionEnvelope,
    runtime: RuntimeState = Depends(get_runtime),
) -> HardwareMotionEnvelopeResult:
    return await asyncio.to_thread(runtime.command_gateway.configure_motion_envelope, runtime, envelope)
