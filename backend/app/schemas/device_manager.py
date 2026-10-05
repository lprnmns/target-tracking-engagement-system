import time
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class DeviceKind(StrEnum):
    CAMERA = "camera"
    SERIAL = "serial"
    PICO_CANDIDATE = "pico_candidate"
    UNKNOWN = "unknown"


class ManagedDevice(BaseModel):
    device_id: str
    device_path: str
    stable_path: str | None = None
    kind: DeviceKind
    name: str
    description: str
    manufacturer: str | None = None
    vid: str | None = None
    pid: str | None = None
    serial_number: str | None = None
    bus_path: str | None = None
    driver: str | None = None
    permissions_ok: bool = True
    busy: bool = False
    connected: bool = True
    candidate_score: int = 0
    recommendation_score: int = 0
    warnings: list[str] = Field(default_factory=list)
    suggested_action: str | None = None
    # Camera identity is deliberately separated from the volatile DirectShow
    # ordinal.  ``device_path`` is still exposed for diagnostics/capture, but
    # the UI should use the durable PnP identity and these truth fields when
    # choosing a physical camera.
    identity_verified: bool = True
    identity_source: str | None = None
    is_external_camera: bool = False
    is_laptop_camera: bool = False


class CameraCapability(BaseModel):
    device_id: str
    device_path: str
    stable_path: str | None = None
    supported_resolutions: list[str] = Field(default_factory=list)
    supported_fps: list[int] = Field(default_factory=list)
    supported_pixel_formats: list[str] = Field(default_factory=list)
    open_ok: bool = False
    frame_grab_ok: bool = False
    actual_width: int | None = None
    actual_height: int | None = None
    actual_fps: float | None = None
    latency_ms: float | None = None
    warnings: list[str] = Field(default_factory=list)
    suggested_action: str | None = None


class DeviceInventory(BaseModel):
    devices: list[ManagedDevice]
    cameras: list[ManagedDevice]
    serial: list[ManagedDevice]
    pico_candidates: list[ManagedDevice]
    scanned_at: float = Field(default_factory=time.time)
    warnings: list[str] = Field(default_factory=list)
    no_physical_command_generated: bool = True


class CameraProbeResult(BaseModel):
    accepted: bool
    device: ManagedDevice | None = None
    capabilities: CameraCapability | None = None
    warnings: list[str] = Field(default_factory=list)
    suggested_action: str | None = None
    no_physical_command_generated: bool = True


class DeviceEvent(BaseModel):
    event_type: str
    payload: dict[str, Any]
