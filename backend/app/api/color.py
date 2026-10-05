from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_runtime
from app.schemas.color import (
    ColorClassifierConfig,
    ColorCalibrationReferenceRequest,
    ColorCalibrationStatus,
    ColorClassifySampleRequest,
    ColorDecisionResult,
    MaskPreviewResult,
)
from app.services.runtime_state import RuntimeState

router = APIRouter(prefix="/api/color", tags=["color"])


@router.get("/config", response_model=ColorClassifierConfig)
def get_color_config(runtime: RuntimeState = Depends(get_runtime)) -> ColorClassifierConfig:
    return runtime.color_classifier.get_config()


@router.put("/config", response_model=ColorClassifierConfig)
def update_color_config(
    update: ColorClassifierConfig,
    runtime: RuntimeState = Depends(get_runtime),
) -> ColorClassifierConfig:
    if runtime.stage3_competition_profile_locked():
        raise HTTPException(status_code=409, detail="A3_PROFILE_LOCKED")
    return runtime.color_classifier.update_config(update)


@router.get("/calibration", response_model=ColorCalibrationStatus)
def color_calibration_status(runtime: RuntimeState = Depends(get_runtime)) -> ColorCalibrationStatus:
    return runtime.color_classifier.calibration_status()


@router.post("/calibration/references", response_model=ColorCalibrationStatus)
def add_color_calibration_reference(
    request: ColorCalibrationReferenceRequest,
    runtime: RuntimeState = Depends(get_runtime),
) -> ColorCalibrationStatus:
    if runtime.stage3_competition_profile_locked():
        raise HTTPException(status_code=409, detail="A3_PROFILE_LOCKED")
    try:
        return runtime.color_classifier.record_calibration_reference(request)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/calibration/reset", response_model=ColorCalibrationStatus)
def reset_color_calibration(runtime: RuntimeState = Depends(get_runtime)) -> ColorCalibrationStatus:
    if runtime.stage3_competition_profile_locked():
        raise HTTPException(status_code=409, detail="A3_PROFILE_LOCKED")
    return runtime.color_classifier.reset_calibration()


@router.post("/classify-sample", response_model=ColorDecisionResult)
def classify_sample(
    request: ColorClassifySampleRequest,
    runtime: RuntimeState = Depends(get_runtime),
) -> ColorDecisionResult:
    return runtime.color_classifier.classify_sample(request)


@router.get("/latest", response_model=ColorDecisionResult | None)
def get_latest_color_decision(runtime: RuntimeState = Depends(get_runtime)) -> ColorDecisionResult | None:
    return runtime.color_classifier.latest()


@router.post("/reset")
def reset_color(runtime: RuntimeState = Depends(get_runtime)) -> dict[str, bool]:
    return runtime.color_classifier.reset()


@router.post("/preview-mask", response_model=MaskPreviewResult)
def preview_mask(
    request: ColorClassifySampleRequest,
    runtime: RuntimeState = Depends(get_runtime),
) -> MaskPreviewResult:
    return runtime.color_classifier.preview_mask(request)
