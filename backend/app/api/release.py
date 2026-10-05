from fastapi import APIRouter, Depends

from app.api.deps import get_runtime
from app.schemas.release import CleanroomVerificationRecord, ReleasePackageRecord, ReleaseStatus
from app.services.runtime_state import RuntimeState

router = APIRouter(prefix="/api/release", tags=["release"])


@router.get("/status", response_model=ReleaseStatus)
def status(runtime: RuntimeState = Depends(get_runtime)) -> ReleaseStatus:
    return runtime.release.status(runtime)


@router.get("/preflight", response_model=ReleaseStatus)
def preflight(runtime: RuntimeState = Depends(get_runtime)) -> ReleaseStatus:
    return runtime.release.preflight(runtime)


@router.post("/check", response_model=ReleaseStatus)
def check(runtime: RuntimeState = Depends(get_runtime)) -> ReleaseStatus:
    return runtime.release.check(runtime)


@router.get("/cold-start-check", response_model=ReleaseStatus)
def cold_start_check(runtime: RuntimeState = Depends(get_runtime)) -> ReleaseStatus:
    return runtime.release.cold_start_check(runtime)


@router.post("/cold-start-check", response_model=ReleaseStatus)
def cold_start_check_post(runtime: RuntimeState = Depends(get_runtime)) -> ReleaseStatus:
    return runtime.release.cold_start_check(runtime)


@router.get("/check", response_model=ReleaseStatus)
def check_get(runtime: RuntimeState = Depends(get_runtime)) -> ReleaseStatus:
    return runtime.release.preflight(runtime)


@router.get("/package/latest", response_model=ReleasePackageRecord | None)
def latest_package(runtime: RuntimeState = Depends(get_runtime)) -> ReleasePackageRecord | None:
    return runtime.release.latest_package()


@router.post("/package/build", response_model=ReleasePackageRecord)
def build_package(runtime: RuntimeState = Depends(get_runtime)) -> ReleasePackageRecord:
    return runtime.release.build_package(runtime)


@router.get("/clean-room/latest", response_model=CleanroomVerificationRecord | None)
def latest_cleanroom(runtime: RuntimeState = Depends(get_runtime)) -> CleanroomVerificationRecord | None:
    return runtime.release.latest_cleanroom_verification()


@router.post("/clean-room/run", response_model=CleanroomVerificationRecord)
def run_cleanroom(runtime: RuntimeState = Depends(get_runtime)) -> CleanroomVerificationRecord:
    return runtime.release.run_cleanroom_verification(runtime)
