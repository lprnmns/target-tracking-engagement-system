import time
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.camera_runtime import CameraRuntimeProfile
from app.schemas.command_gateway import CommandProfile
from app.schemas.vision import VisionConfigUpdate
from app.schemas.vision_runtime_settings import VisionRuntimeProfile


DeviceProfileVerificationStatus = Literal[
    "not_verified",
    "mock_verified",
    "demo_verified",
    "hardware_readonly_verified",
    "hardware_pending",
    "camera_pending",
    "pico_pending",
    "model_pending",
    "competition_not_verified",
    "mismatch",
]


class DeviceProfile(BaseModel):
    profile_id: str = "default"
    display_name: str = "Varsayılan"
    schema_version: int = 2
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)
    command_profile: CommandProfile = CommandProfile.DRY_RUN
    selected_camera_id: str | None = None
    selected_camera_stable_path: str | None = None
    selected_camera_name: str | None = None
    selected_camera_backend: str = "mock"
    selected_pico_port: str | None = None
    selected_pico_baudrate: int = Field(default=460800, ge=1200)
    selected_pico_usb_vid_pid: str | None = None
    selected_pico_serial_number: str | None = None
    selected_model_id: str | None = None
    selected_runtime_profile: str | None = None
    camera_profile: CameraRuntimeProfile | None = None
    vision_config: VisionConfigUpdate | None = None
    vision_runtime_profile: VisionRuntimeProfile | None = None
    servo_release_deg: int = Field(default=35, ge=0, le=180)
    servo_fire_deg: int = Field(default=175, ge=0, le=180)
    servo_pulse_s: float = Field(default=1.0, ge=0.1, le=5.0)
    last_verified_at: float | None = None
    verification_status: DeviceProfileVerificationStatus = "not_verified"
    verification_level: str = "hardware_pending"
    camera_binding_status: str = "camera_pending"
    pico_binding_status: str = "pico_pending"
    model_binding_status: str = "model_pending"
    competition_status: str = "competition_not_verified"
    warnings: list[str] = Field(default_factory=list)
    no_physical_command_generated: bool = True


class DeviceProfileSaveRequest(BaseModel):
    profile_id: str | None = None
    display_name: str = Field(default="Varsayılan", min_length=1, max_length=80)
    command_profile: CommandProfile = CommandProfile.DRY_RUN
    servo_release_deg: int = Field(default=35, ge=0, le=180)
    servo_fire_deg: int = Field(default=175, ge=0, le=180)
    servo_pulse_s: float = Field(default=1.0, ge=0.1, le=5.0)


class DeviceProfileApplyRequest(BaseModel):
    profile_id: str = "default"
    connect_hardware: bool = False


class DeviceProfileResult(BaseModel):
    accepted: bool
    profile: DeviceProfile
    warnings: list[str] = Field(default_factory=list)
    mismatch_warnings: list[str] = Field(default_factory=list)
    similar_candidates: list[dict] = Field(default_factory=list)
    reason: str
    no_physical_command_generated: bool = True


class DeviceProfilesList(BaseModel):
    profiles: list[DeviceProfile]
    active_profile_id: str = "default"
    generated_at: float = Field(default_factory=time.time)
    no_physical_command_generated: bool = True
