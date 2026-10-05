from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_runtime
from app.schemas.pico import (
    PicoConnectRequest,
    PicoConnectionEvent,
    PicoDiscoveryPortsResponse,
    PicoPermissionDiagnosis,
    PicoPort,
    PicoProtocolReadSampleRequest,
    PicoProtocolReadSampleResult,
    PicoProtocolStatus,
    PicoProtocolTelemetry,
    PicoReadOnlyConnectRequest,
    PicoReadOnlyEvidence,
    PicoReadOnlyStatus,
    PicoReadOnlyTelemetry,
    PicoStatus,
    PinProfile,
    PinValidationResult,
)
from app.services.runtime_state import RuntimeState

router = APIRouter(prefix="/api/pico", tags=["pico"])


@router.get("/status", response_model=PicoStatus)
def get_pico_status(runtime: RuntimeState = Depends(get_runtime)) -> PicoStatus:
    return runtime.pico.status()


@router.get("/ports", response_model=list[PicoPort])
def get_pico_ports(runtime: RuntimeState = Depends(get_runtime)) -> list[PicoPort]:
    return runtime.pico.ports()


@router.get("/discovery/ports", response_model=PicoDiscoveryPortsResponse)
def get_pico_discovery_ports(runtime: RuntimeState = Depends(get_runtime)) -> PicoDiscoveryPortsResponse:
    return runtime.pico.readonly_ports()


@router.post("/read-only/connect", response_model=PicoReadOnlyStatus)
def pico_readonly_connect(
    request: PicoReadOnlyConnectRequest,
    runtime: RuntimeState = Depends(get_runtime),
) -> PicoReadOnlyStatus:
    return runtime.pico.readonly_connect(request)


@router.post("/read-only/disconnect", response_model=PicoReadOnlyStatus)
def pico_readonly_disconnect(runtime: RuntimeState = Depends(get_runtime)) -> PicoReadOnlyStatus:
    return runtime.pico.readonly_disconnect()


@router.get("/read-only/status", response_model=PicoReadOnlyStatus)
def pico_readonly_status(runtime: RuntimeState = Depends(get_runtime)) -> PicoReadOnlyStatus:
    return runtime.pico.readonly_status()


@router.get("/read-only/permission-status", response_model=PicoPermissionDiagnosis)
def pico_readonly_permission_status(runtime: RuntimeState = Depends(get_runtime), port: str | None = None) -> PicoPermissionDiagnosis:
    return runtime.pico.readonly_permission_status(port=port)


@router.get("/read-only/latest-telemetry", response_model=PicoReadOnlyTelemetry)
def pico_readonly_latest_telemetry(runtime: RuntimeState = Depends(get_runtime)) -> PicoReadOnlyTelemetry:
    return runtime.pico.readonly_latest_telemetry()


@router.post("/read-only/capture-evidence", response_model=PicoReadOnlyEvidence)
def pico_readonly_capture_evidence(runtime: RuntimeState = Depends(get_runtime)) -> PicoReadOnlyEvidence:
    return runtime.pico.readonly_capture_evidence()


@router.get("/read-only/latest-evidence", response_model=PicoReadOnlyEvidence)
def pico_readonly_latest_evidence(runtime: RuntimeState = Depends(get_runtime)) -> PicoReadOnlyEvidence:
    return runtime.pico.readonly_latest_evidence()


@router.get("/protocol/status", response_model=PicoProtocolStatus)
def pico_protocol_status(runtime: RuntimeState = Depends(get_runtime)) -> PicoProtocolStatus:
    return runtime.pico.protocol_status()


@router.get("/protocol/latest-telemetry", response_model=PicoProtocolTelemetry)
def pico_protocol_latest_telemetry(runtime: RuntimeState = Depends(get_runtime)) -> PicoProtocolTelemetry:
    return runtime.pico.protocol_latest_telemetry()


@router.get("/protocol/contract")
def pico_protocol_contract(runtime: RuntimeState = Depends(get_runtime)) -> dict:
    return runtime.pico.protocol_contract()


@router.post("/protocol/read-sample", response_model=PicoProtocolReadSampleResult)
def pico_protocol_read_sample(
    request: PicoProtocolReadSampleRequest,
    runtime: RuntimeState = Depends(get_runtime),
) -> PicoProtocolReadSampleResult:
    return runtime.pico.protocol_read_sample(request)


@router.post("/connect", response_model=PicoConnectionEvent)
def connect_pico(
    request: PicoConnectRequest,
    runtime: RuntimeState = Depends(get_runtime),
) -> PicoConnectionEvent:
    return runtime.pico.connect(request)


@router.post("/disconnect", response_model=PicoConnectionEvent)
def disconnect_pico(runtime: RuntimeState = Depends(get_runtime)) -> PicoConnectionEvent:
    return runtime.pico.disconnect()


@router.get("/pins", response_model=PinProfile)
def get_pico_pins(runtime: RuntimeState = Depends(get_runtime)) -> PinProfile:
    return runtime.pico.pins()


@router.put("/pins", response_model=PinValidationResult)
def update_pico_pins(
    profile: PinProfile,
    runtime: RuntimeState = Depends(get_runtime),
) -> PinValidationResult:
    result = runtime.pico.update_pins(profile, runtime.system_state())
    if not result.can_apply:
        status_code = 409 if any(issue.code == "SYSTEM_NOT_DISARMED" for issue in result.issues) else 422
        raise HTTPException(status_code=status_code, detail=result.model_dump(mode="json"))
    return result


@router.post("/pins/validate", response_model=PinValidationResult)
def validate_pico_pins(
    profile: PinProfile,
    runtime: RuntimeState = Depends(get_runtime),
) -> PinValidationResult:
    return runtime.pico.validate_pins(profile, runtime.system_state())
