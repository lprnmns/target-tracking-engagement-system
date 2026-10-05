import time
from typing import Literal

from pydantic import BaseModel, Field, model_validator


SourceType = Literal["laptop", "usb", "video_file", "replay", "mock"]
PixelFormat = Literal["MJPG", "YUYV", "auto"]
LensProfileRuntime = Literal["unknown", "3.6mm", "8mm", "12mm"]


class RoiProfile(BaseModel):
    enabled: bool = False
    x: int = Field(default=0, ge=0)
    y: int = Field(default=0, ge=0)
    w: int = Field(default=0, ge=0)
    h: int = Field(default=0, ge=0)


class CameraRuntimeProfile(BaseModel):
    source_type: SourceType = "mock"
    device_id: str | None = None
    device_path: str | None = None
    stable_path: str | None = None
    width: int = Field(default=640, gt=0)
    height: int = Field(default=360, gt=0)
    fps: int = Field(default=15, gt=0)
    pixel_format: PixelFormat = "auto"
    exposure_auto: bool = True
    exposure_value: float | None = None
    gain: float | None = None
    focus_auto: bool | None = None
    focus_value: float | None = None
    white_balance_auto: bool | None = None
    white_balance_value: float | None = None
    brightness: float | None = None
    contrast: float | None = None
    saturation: float | None = None
    sharpness: float | None = None
    flip_horizontal: bool = False
    flip_vertical: bool = False
    rotate_deg: int = 0
    lens_profile: LensProfileRuntime = "unknown"
    stream_width: int = Field(default=640, gt=0)
    stream_height: int = Field(default=360, gt=0)
    inference_width: int = Field(default=640, gt=0)
    inference_height: int = Field(default=360, gt=0)
    roi: RoiProfile = Field(default_factory=RoiProfile)

    @model_validator(mode="after")
    def validate_runtime_profile(self) -> "CameraRuntimeProfile":
        if self.rotate_deg not in {0, 90, 180, 270}:
            raise ValueError("rotate_deg must be 0, 90, 180 or 270")
        if self.source_type in {"laptop", "usb"} and not (self.device_id or self.device_path):
            raise ValueError("real camera source requires device_id or device_path")
        if self.roi.enabled and (self.roi.w <= 0 or self.roi.h <= 0):
            raise ValueError("enabled ROI requires positive width and height")
        return self


class CameraRuntimeStatus(BaseModel):
    profile: CameraRuntimeProfile
    running: bool = False
    selected_camera: str = "mock"
    requested_width: int = 640
    requested_height: int = 360
    requested_fps: int = 15
    requested_pixel_format: PixelFormat = "auto"
    actual_width: int = 640
    actual_height: int = 360
    actual_fps: float = 15.0
    actual_fps_measured: float = 15.0
    actual_pixel_format: str = "auto"
    backend_api: str = "mock"
    warmup_ms: float = 0.0
    dropped_frames: int = 0
    last_probe_result: dict | None = None
    recommendation_score: int = 0
    last_apply_ok: bool = True
    last_error: str | None = None
    warnings: list[str] = Field(default_factory=list)
    selected_device: str | None = None
    selected_backend: str = "fallback"
    source_mode: str = "MOCK_OR_FIXTURE"
    input_format: str = "auto"
    resolution: str = "640x360"
    last_frame_age_ms: int | None = None
    last_capture_error: str | None = None
    frame_mean_luma: float | None = None
    frame_brightness_state: str = "NO_FRAME"
    frame_sequence: int = 0
    is_real_camera_evidence: bool = False
    is_external_usb_camera: bool = False
    is_laptop_camera: bool = False
    hardware_presence_note: str = "MOCK_OR_FIXTURE"
    updated_at: float = Field(default_factory=time.time)
    no_physical_command_generated: bool = True


class CameraRuntimeApplyResult(BaseModel):
    accepted: bool
    applied: bool
    rollback_performed: bool = False
    profile: CameraRuntimeProfile
    actual_width: int
    actual_height: int
    actual_fps: float
    actual_fps_measured: float | None = None
    actual_pixel_format: str | None = None
    backend_api: str = "mock"
    warmup_ms: float = 0.0
    dropped_frames: int = 0
    last_probe_result: dict | None = None
    warnings: list[str] = Field(default_factory=list)
    suggested_action: str | None = None
    no_physical_command_generated: bool = True


class CameraRuntimeControlsUpdate(BaseModel):
    exposure_auto: bool | None = None
    exposure_value: float | None = None
    gain: float | None = None
    white_balance_auto: bool | None = None
    white_balance_value: float | None = None
    brightness: float | None = None
    contrast: float | None = None
    saturation: float | None = None
    sharpness: float | None = None
