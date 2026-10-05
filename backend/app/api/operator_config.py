from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field


router = APIRouter(prefix="/api", tags=["operator-config"])


class PerceptionConfig(BaseModel):
    confidence_threshold: float = Field(default=0.01, ge=0.0, le=1.0)
    yolo_enabled: bool = True


class CameraConfig(BaseModel):
    brightness: int = Field(default=0, ge=-100, le=100)
    contrast: int = Field(default=0, ge=-100, le=100)
    saturation: int = Field(default=0, ge=-100, le=100)
    exposure_auto: bool = True
    exposure: int = Field(default=0, ge=-100, le=100)
    preview_filter_only: bool = True


class MotionConfig(BaseModel):
    motion_mode: str = "Virtual Preview Only"
    yaw_max_speed: float = Field(default=30.0, ge=0.0, le=360.0)
    pitch_max_speed: float = Field(default=20.0, ge=0.0, le=360.0)
    acceleration_limit: float = Field(default=80.0, ge=0.0, le=1000.0)
    deadzone: float = Field(default=1.5, ge=0.0, le=50.0)
    smoothing: float = Field(default=0.35, ge=0.0, le=1.0)
    yaw_kp: float = Field(default=0.8, ge=0.0, le=20.0)
    yaw_ki: float = Field(default=0.0, ge=0.0, le=20.0)
    yaw_kd: float = Field(default=0.08, ge=0.0, le=20.0)
    pitch_kp: float = Field(default=0.8, ge=0.0, le=20.0)
    pitch_ki: float = Field(default=0.0, ge=0.0, le=20.0)
    pitch_kd: float = Field(default=0.08, ge=0.0, le=20.0)


def _operator_config_state(request: Request) -> dict[str, Any]:
    state = getattr(request.app.state, "operator_config", None)
    if state is None:
        state = {
            "perception": PerceptionConfig().model_dump(),
            "camera": CameraConfig().model_dump(),
            "motion": MotionConfig().model_dump(),
        }
        request.app.state.operator_config = state
    return state


def _response(config: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": True,
        "applied": True,
        "config": config,
        "visualization_only": True,
        "physical_command_enabled": False,
        "serial_tx_enabled": False,
        "no_physical_command_generated": True,
    }


@router.get("/perception/config")
def get_perception_config(request: Request) -> dict[str, Any]:
    return _response(_operator_config_state(request)["perception"])


@router.post("/perception/config")
def set_perception_config(config: PerceptionConfig, request: Request) -> dict[str, Any]:
    state = _operator_config_state(request)
    state["perception"] = config.model_dump()
    return _response(state["perception"])


@router.get("/camera/config")
def get_camera_config(request: Request) -> dict[str, Any]:
    return _response(_operator_config_state(request)["camera"])


@router.post("/camera/config")
def set_camera_config(config: CameraConfig, request: Request) -> dict[str, Any]:
    state = _operator_config_state(request)
    state["camera"] = config.model_dump()
    return _response(state["camera"])


@router.get("/motion/config")
def get_motion_config(request: Request) -> dict[str, Any]:
    return _response(_operator_config_state(request)["motion"])


@router.post("/motion/config")
def set_motion_config(config: MotionConfig, request: Request) -> dict[str, Any]:
    state = _operator_config_state(request)
    state["motion"] = config.model_dump()
    return _response(state["motion"])
