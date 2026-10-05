from fastapi import APIRouter, Depends

from app.api.deps import get_runtime
from app.schemas.device_profile import DeviceProfile, DeviceProfileApplyRequest, DeviceProfileResult, DeviceProfileSaveRequest, DeviceProfilesList
from app.services.runtime_state import RuntimeState

router = APIRouter(prefix="/api/device-profiles", tags=["device-profiles"])


@router.get("", response_model=DeviceProfilesList)
def list_profiles(runtime: RuntimeState = Depends(get_runtime)) -> DeviceProfilesList:
    return runtime.device_profiles.list_profiles()


@router.get("/active", response_model=DeviceProfile)
def active(runtime: RuntimeState = Depends(get_runtime)) -> DeviceProfile:
    return runtime.device_profiles.active()


@router.post("/save", response_model=DeviceProfileResult)
def save(request: DeviceProfileSaveRequest | None = None, runtime: RuntimeState = Depends(get_runtime)) -> DeviceProfileResult:
    return runtime.device_profiles.save(runtime, request or DeviceProfileSaveRequest())


@router.post("/apply", response_model=DeviceProfileResult)
def apply(request: DeviceProfileApplyRequest | None = None, runtime: RuntimeState = Depends(get_runtime)) -> DeviceProfileResult:
    selected = request or DeviceProfileApplyRequest()
    return runtime.device_profiles.apply(runtime, selected.profile_id, connect_hardware=selected.connect_hardware)


@router.post("/verify", response_model=DeviceProfileResult)
def verify(request: DeviceProfileApplyRequest | None = None, runtime: RuntimeState = Depends(get_runtime)) -> DeviceProfileResult:
    return runtime.device_profiles.verify(runtime, (request or DeviceProfileApplyRequest()).profile_id)


@router.post("/reset", response_model=DeviceProfileResult)
def reset(runtime: RuntimeState = Depends(get_runtime)) -> DeviceProfileResult:
    return runtime.device_profiles.reset()
