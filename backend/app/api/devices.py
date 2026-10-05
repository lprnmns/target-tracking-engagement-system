from fastapi import APIRouter, Depends

from app.api.deps import get_runtime
from app.schemas.device_manager import CameraCapability, CameraProbeResult, DeviceInventory, ManagedDevice
from app.services.runtime_state import RuntimeState

router = APIRouter(prefix="/api/devices", tags=["devices"])


@router.get("", response_model=DeviceInventory)
def get_devices(runtime: RuntimeState = Depends(get_runtime)) -> DeviceInventory:
    return runtime.device_manager.inventory()


@router.post("/refresh", response_model=DeviceInventory)
def refresh_devices(runtime: RuntimeState = Depends(get_runtime)) -> DeviceInventory:
    return runtime.device_manager.scan()


@router.get("/serial", response_model=list[ManagedDevice])
def serial_devices(runtime: RuntimeState = Depends(get_runtime)) -> list[ManagedDevice]:
    return runtime.device_manager.serial_devices()


@router.get("/cameras", response_model=list[ManagedDevice])
def camera_devices(runtime: RuntimeState = Depends(get_runtime)) -> list[ManagedDevice]:
    return runtime.device_manager.cameras()


@router.get("/cameras/{device_id}/capabilities", response_model=CameraCapability)
def camera_capabilities(device_id: str, runtime: RuntimeState = Depends(get_runtime)) -> CameraCapability:
    return runtime.device_manager.camera_capabilities(device_id)


@router.post("/cameras/{device_id}/probe", response_model=CameraProbeResult)
def probe_camera(device_id: str, runtime: RuntimeState = Depends(get_runtime)) -> CameraProbeResult:
    return runtime.device_manager.probe_camera(device_id)


@router.get("/pico-candidates", response_model=list[ManagedDevice])
def pico_candidates(runtime: RuntimeState = Depends(get_runtime)) -> list[ManagedDevice]:
    return runtime.device_manager.pico_candidates()
