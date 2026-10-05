from fastapi import APIRouter, Depends

from app.api.deps import get_runtime
from app.schemas.calibration import (
    CalibrationConfigModel,
    CalibrationPointCreate,
    CalibrationStatus,
    CameraCalibrationConfig,
    DirectionCalibrationProfile,
    DirectionCalibrationStatus,
    DirectionObservationRequest,
    DirectionObservationResult,
    DirectionSimulationRequest,
    DirectionSimulationResult,
    FovEstimateRequest,
    FovEstimateResponse,
)
from app.services.runtime_state import RuntimeState

router = APIRouter(prefix="/api/calibration", tags=["calibration"])


@router.get("/status", response_model=CalibrationStatus)
def get_calibration_status(runtime: RuntimeState = Depends(get_runtime)) -> CalibrationStatus:
    return runtime.calibration.status()


@router.get("/config", response_model=CameraCalibrationConfig)
def get_calibration_config(runtime: RuntimeState = Depends(get_runtime)) -> CameraCalibrationConfig:
    return runtime.calibration.config_model()


@router.put("/config", response_model=CameraCalibrationConfig)
def update_calibration_config(
    update: CalibrationConfigModel,
    runtime: RuntimeState = Depends(get_runtime),
) -> CameraCalibrationConfig:
    return runtime.calibration.update_config(update)


@router.post("/points", response_model=CalibrationStatus)
def add_calibration_point(
    point: CalibrationPointCreate,
    runtime: RuntimeState = Depends(get_runtime),
) -> CalibrationStatus:
    return runtime.calibration.add_point(point)


@router.delete("/points/{point_id}", response_model=CalibrationStatus)
def delete_calibration_point(
    point_id: str,
    runtime: RuntimeState = Depends(get_runtime),
) -> CalibrationStatus:
    return runtime.calibration.delete_point(point_id)


@router.post("/compute", response_model=CalibrationStatus)
def compute_calibration(runtime: RuntimeState = Depends(get_runtime)) -> CalibrationStatus:
    runtime.calibration.compute()
    return runtime.calibration.status()


@router.post("/fov-estimate", response_model=FovEstimateResponse)
def estimate_fov(
    request: FovEstimateRequest,
    runtime: RuntimeState = Depends(get_runtime),
) -> FovEstimateResponse:
    return runtime.calibration.fov_estimate(request)


@router.post("/reset", response_model=CalibrationStatus)
def reset_calibration(runtime: RuntimeState = Depends(get_runtime)) -> CalibrationStatus:
    return runtime.calibration.reset()


@router.get("/direction/status", response_model=DirectionCalibrationStatus)
def direction_status(runtime: RuntimeState = Depends(get_runtime)) -> DirectionCalibrationStatus:
    return runtime.calibration.direction_status()


@router.post("/direction/simulate", response_model=DirectionSimulationResult)
def direction_simulate(
    request: DirectionSimulationRequest,
    runtime: RuntimeState = Depends(get_runtime),
) -> DirectionSimulationResult:
    return runtime.calibration.direction_simulate(request)


@router.post("/direction/record-observation", response_model=DirectionObservationResult)
def direction_record_observation(
    request: DirectionObservationRequest,
    runtime: RuntimeState = Depends(get_runtime),
) -> DirectionObservationResult:
    return runtime.calibration.direction_record_observation(request)


@router.post("/direction/save-profile", response_model=DirectionCalibrationProfile)
def direction_save_profile(runtime: RuntimeState = Depends(get_runtime)) -> DirectionCalibrationProfile:
    return runtime.calibration.direction_save_profile()


@router.post("/direction/reset", response_model=DirectionCalibrationStatus)
def direction_reset(runtime: RuntimeState = Depends(get_runtime)) -> DirectionCalibrationStatus:
    return runtime.calibration.direction_reset()


@router.get("/direction/latest", response_model=DirectionCalibrationProfile)
def direction_latest(runtime: RuntimeState = Depends(get_runtime)) -> DirectionCalibrationProfile:
    return runtime.calibration.direction_latest()


# ---- Boresight & Zeroing Calibration (720p Sensor Math) ----

import re
from pathlib import Path
from pydantic import BaseModel, Field
from app.services.config_service import default_config_path


class ZeroingCalibrationRequest(BaseModel):
    aim_offset_x_px: float = Field(default=0.0, description="Horizontal boresight offset in pixels")
    aim_offset_y_px: float = Field(default=0.0, description="Vertical drop boresight offset in pixels")
    distance_m: float = Field(default=15.0, ge=1.0, le=50.0, description="Target calibration distance in meters")
    persist: bool = Field(default=True, description="Save to config.yaml on disk")


class ZeroingCalibrationStatus(BaseModel):
    aim_offset_x_px: float
    aim_offset_y_px: float
    distance_m: float
    drop_cm: float
    px_per_cm: float
    fov_mode: str
    persisted: bool


def _persist_zeroing_to_yaml(offset_x: float, offset_y: float) -> bool:
    try:
        cfg_path = default_config_path()
        if not cfg_path.exists():
            return False
        text = cfg_path.read_text(encoding="utf-8")
        lines = text.splitlines(keepends=True)
        in_tracking = False
        new_lines = []
        for line in lines:
            if line.startswith("tracking:"):
                in_tracking = True
            elif in_tracking and line and not line.startswith(" ") and not line.startswith("\t"):
                in_tracking = False

            if in_tracking and re.match(r"^\s+aim_offset_x_px:", line):
                indent = re.match(r"^\s+", line).group(0)
                new_lines.append(f"{indent}aim_offset_x_px: {offset_x}\n")
            elif in_tracking and re.match(r"^\s+aim_offset_y_px:", line):
                indent = re.match(r"^\s+", line).group(0)
                new_lines.append(f"{indent}aim_offset_y_px: {offset_y}\n")
            else:
                new_lines.append(line)
        cfg_path.write_text("".join(new_lines), encoding="utf-8")
        return True
    except Exception:
        return False


@router.get("/zeroing", response_model=ZeroingCalibrationStatus)
def get_zeroing_status(distance_m: float = 15.0, runtime: RuntimeState = Depends(get_runtime)) -> ZeroingCalibrationStatus:
    offset_x = float(getattr(runtime.auto_tracker, "aim_offset_x_px", 0.0))
    offset_y = float(getattr(runtime.auto_tracker, "aim_offset_y_px", 0.0))
    distance = max(1.0, distance_m)
    px_per_cm = 20.0 / distance
    drop_cm = round(offset_y / px_per_cm, 2)
    return ZeroingCalibrationStatus(
        aim_offset_x_px=round(offset_x, 2),
        aim_offset_y_px=round(offset_y, 2),
        distance_m=distance,
        drop_cm=drop_cm,
        px_per_cm=round(px_per_cm, 3),
        fov_mode="720p_center_crop",
        persisted=True,
    )


@router.post("/zeroing", response_model=ZeroingCalibrationStatus)
def apply_zeroing_calibration(
    request: ZeroingCalibrationRequest,
    runtime: RuntimeState = Depends(get_runtime),
) -> ZeroingCalibrationStatus:
    offset_x = float(request.aim_offset_x_px)
    offset_y = float(request.aim_offset_y_px)
    distance = max(1.0, float(request.distance_m))

    runtime.auto_tracker.aim_offset_x_px = offset_x
    runtime.auto_tracker.aim_offset_y_px = offset_y
    if hasattr(runtime.config.tracking, "aim_offset_x_px"):
        runtime.config.tracking.aim_offset_x_px = offset_x
    if hasattr(runtime.config.tracking, "aim_offset_y_px"):
        runtime.config.tracking.aim_offset_y_px = offset_y

    persisted = False
    if request.persist:
        persisted = _persist_zeroing_to_yaml(offset_x, offset_y)

    px_per_cm = 20.0 / distance
    drop_cm = round(offset_y / px_per_cm, 2)

    return ZeroingCalibrationStatus(
        aim_offset_x_px=round(offset_x, 2),
        aim_offset_y_px=round(offset_y, 2),
        distance_m=distance,
        drop_cm=drop_cm,
        px_per_cm=round(px_per_cm, 3),
        fov_mode="720p_center_crop",
        persisted=persisted,
    )

