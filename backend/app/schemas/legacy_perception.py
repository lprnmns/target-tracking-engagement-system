import time
from typing import Any

from pydantic import BaseModel, Field


class LegacyPerceptionPreset(BaseModel):
    preset_id: str
    source_file: str
    camera_index: int | None = None
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    color_space: str = "HSV"
    hsv_lower: list[int] | list[list[int]] | None = None
    hsv_upper: list[int] | list[list[int]] | None = None
    blur_kernel: int | list[int] | None = None
    morphology_kernel: int | list[int] | None = None
    min_area: float | None = None
    max_area: float | None = None
    circularity_min: float | None = None
    target_selection_rule: str = "not_available"
    smoothing_enabled: bool = False
    kalman_enabled: bool = False
    notes: str = "Legacy perception audit candidate; advisory evidence only."
    risk_class: str = "low"
    advisory_only: bool = True
    no_physical_command_generated: bool = True


class LegacyPerceptionPresetList(BaseModel):
    presets: list[LegacyPerceptionPreset]
    source_reports: list[str]
    forbidden_runtime_tokens_present: bool = False
    advisory_only: bool = True
    no_physical_command_generated: bool = True


class RealCameraEvidence(BaseModel):
    evidence_id: str
    status: str
    created_at: float = Field(default_factory=time.time)
    camera_source: str = "not_available"
    camera_device_path: str | None = None
    frame_origin: str = "real_camera_not_available"
    detector: str = "legacy_opencv_perception_evidence"
    preset_id: str | None = None
    frame_width: int | None = None
    frame_height: int | None = None
    fps_estimate: float | None = None
    detections_count: int = 0
    target_center_metadata: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    advisory_only: bool = True
    no_physical_command_generated: bool = True
    physical_command_enabled: bool = False


class RealCameraEvidenceStatus(BaseModel):
    status: str
    camera_source: str
    camera_device_path: str | None = None
    frame_origin: str
    detector: str = "legacy_opencv_perception_evidence"
    preset_id: str | None = None
    frame_width: int | None = None
    frame_height: int | None = None
    fps_estimate: float | None = None
    detections_count: int = 0
    target_center_metadata: dict[str, Any] = Field(default_factory=dict)
    latest_evidence_id: str | None = None
    warnings: list[str] = Field(default_factory=list)
    advisory_only: bool = True
    no_physical_command_generated: bool = True
    physical_command_enabled: bool = False


class CameraHostCommandResult(BaseModel):
    command: str
    status: str
    exit_code: int | None = None
    output: str = ""
    error: str | None = None


class CameraDeviceGroup(BaseModel):
    camera_kind: str = "unknown_camera"
    name: str = "Unknown Camera"
    paths: list[str] = Field(default_factory=list)
    preferred_capture_path: str | None = None
    evidence_status: str = "not_evaluated"
    frame_captured: bool = False
    advisory_only: bool = True
    physical_command_enabled: bool = False
    no_physical_command_generated: bool = True


class CameraHostDiagnostic(BaseModel):
    diagnostic_id: str
    created_at: float = Field(default_factory=time.time)
    platform: str = "unknown"
    host_camera_devices_detected: bool = False
    dev_video_entries: list[str] = Field(default_factory=list)
    camera_groups: list[CameraDeviceGroup] = Field(default_factory=list)
    recommended_usb_device_path: str | None = None
    selected_camera_device: str | None = None
    selected_camera_name: str | None = None
    camera_kind: str = "unknown_camera"
    v4l2_available: bool = False
    ffmpeg_available: bool = False
    user_in_video_group: bool = False
    camera_app_not_seen_note: bool = True
    real_camera_capture_attempted: bool = False
    real_camera_frame_captured: bool = False
    camera_acceptance_status: str = "blocked_by_host_os"
    blocker_reason: str = "host camera devices not detected"
    commands: list[CameraHostCommandResult] = Field(default_factory=list)
    suggested_actions: list[str] = Field(default_factory=list)
    advisory_only: bool = True
    physical_command_enabled: bool = False
    no_physical_command_generated: bool = True


class RealCameraAcceptance(BaseModel):
    status: str
    camera_tooling_status: str
    frame_captured: bool = False
    device_path: str | None = None
    width: int | None = None
    height: int | None = None
    fps_estimate: float | None = None
    frame_hash: str | None = None
    frame_path: str | None = None
    capture_method: str | None = None
    selected_camera_device: str | None = None
    selected_camera_name: str | None = None
    camera_kind: str = "unknown_camera"
    internal_camera_passed: bool = False
    external_usb_camera_passed: bool = False
    blocker_reason: str = "not_evaluated"
    camera_host: CameraHostDiagnostic
    latest_evidence: RealCameraEvidence
    advisory_only: bool = True
    physical_command_enabled: bool = False
    no_physical_command_generated: bool = True


class RealCameraSelectRequest(BaseModel):
    device_path: str
    camera_kind: str = "unknown_camera"


class RealCameraSelection(BaseModel):
    selected_camera_device: str
    selected_camera_name: str | None = None
    camera_kind: str = "unknown_camera"
    advisory_only: bool = True
    physical_command_enabled: bool = False
    no_physical_command_generated: bool = True
