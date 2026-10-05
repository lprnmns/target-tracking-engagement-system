from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_runtime
from app.schemas.stage3_range import Stage3RangeCalibrationStatus, Stage3RangeObservationCreate
from app.services.runtime_state import RuntimeState


router = APIRouter(prefix="/api/stage3", tags=["stage3"])


def _active_body_model(runtime: RuntimeState) -> tuple[str | None, str | None]:
    model_id = runtime.vision_runtime.profile.active_body_model_id
    if not model_id:
        return None, None
    try:
        model = runtime.model_registry.get_model(model_id)
    except KeyError:
        return model_id, None
    return model_id, model.file_path


def _ensure_profile_mutable(runtime: RuntimeState) -> None:
    if runtime.stage3_competition_profile_locked():
        raise HTTPException(status_code=409, detail="A3_PROFILE_LOCKED")


@router.get("/range/status", response_model=Stage3RangeCalibrationStatus)
def range_status(runtime: RuntimeState = Depends(get_runtime)) -> Stage3RangeCalibrationStatus:
    model_id, path = _active_body_model(runtime)
    return runtime.stage3_range.status(model_id, path)


@router.post("/range/observations", response_model=Stage3RangeCalibrationStatus)
def range_add_observation(request: Stage3RangeObservationCreate, runtime: RuntimeState = Depends(get_runtime)) -> Stage3RangeCalibrationStatus:
    _ensure_profile_mutable(runtime)
    return runtime.stage3_range.add_observation(request)


@router.delete("/range/observations/{observation_id}", response_model=Stage3RangeCalibrationStatus)
def range_remove_observation(observation_id: str, runtime: RuntimeState = Depends(get_runtime)) -> Stage3RangeCalibrationStatus:
    _ensure_profile_mutable(runtime)
    try:
        return runtime.stage3_range.remove_observation(observation_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="A3_RANGE_OBSERVATION_NOT_FOUND") from exc


@router.post("/range/validate", response_model=Stage3RangeCalibrationStatus)
def range_validate(runtime: RuntimeState = Depends(get_runtime)) -> Stage3RangeCalibrationStatus:
    _ensure_profile_mutable(runtime)
    model_id, path = _active_body_model(runtime)
    return runtime.stage3_range.validate(model_id, path)


@router.post("/range/reset", response_model=Stage3RangeCalibrationStatus)
def range_reset(runtime: RuntimeState = Depends(get_runtime)) -> Stage3RangeCalibrationStatus:
    _ensure_profile_mutable(runtime)
    return runtime.stage3_range.reset()
