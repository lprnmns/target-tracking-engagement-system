from typing import Annotated
from typing import Any

from pydantic import BaseModel, Field

from app.schemas.tracking import CockpitTargetVerdict


class BBox(BaseModel):
    x: Annotated[int, Field(ge=0)]
    y: Annotated[int, Field(ge=0)]
    w: Annotated[int, Field(gt=0)]
    h: Annotated[int, Field(gt=0)]
    format: str = "pixel"


class BodyDetection(BaseModel):
    id: int
    class_name: str
    class_id: int
    confidence: Annotated[float, Field(ge=0, le=1)]
    bbox: BBox
    source: str = "mock"
    color_hint: str | None = None
    stable_frames: int = 0
    # Detection ids are frame-local.  A real IFF decision must instead be
    # accumulated against this short-lived, vision-owned body track id.
    track_id: int | None = None
    target_team: str = "unknown"
    range_m: float | None = None
    range_uncertainty_m: float | None = None
    range_calibration_hash: str | None = None
    color_decision: dict[str, Any] | None = None


class BalloonDetection(BaseModel):
    id: int
    confidence: Annotated[float, Field(ge=0, le=1)]
    bbox: BBox
    center_x: int
    center_y: int
    source: str = "mock"


class AimPoint(BaseModel):
    id: int
    x: int
    y: int
    source: str = "mock"


class TrackPlaceholder(BaseModel):
    track_id: int
    detection_id: int
    stable_frames: int


class VisionEvent(BaseModel):
    frame_id: int
    timestamp_ms: int
    source: str
    frame_width: int | None = None
    frame_height: int | None = None
    fps: float
    camera_fps: float | None = None
    detector_fps: float | None = None
    preprocess_ms: float
    inference_ms: float
    postprocess_ms: float
    total_latency_ms: float
    total_ms: float | None = None
    camera_source_kind: str | None = None
    camera_device_path: str | None = None
    frame_origin: str | None = None
    detector_kind: str | None = None
    body_detections: list[BodyDetection]
    balloon_detections: list[BalloonDetection]
    tracks: list[TrackPlaceholder] = Field(default_factory=list)
    aim_points: list[AimPoint] = Field(default_factory=list)
    # Runtime truth for the operator HUD.  These are measured by the
    # background inference worker, not estimates fabricated from a target
    # setting or from the camera's requested FPS.
    model_count: int = 0
    model_execution: str = "none"
    model_timings_ms: dict[str, float] = Field(default_factory=dict)
    # Measured stage timings.  The legacy fields above remain stable for the
    # UI, while these fields expose where a real frame spends its budget.
    capture_ms: float = 0.0
    pipeline_overhead_ms: float = 0.0
    camera_frame_sequence: int | None = None
    camera_capture_timestamp_ms: int | None = None
    camera_frame_age_ms: float | None = None
    target_verdicts: list[CockpitTargetVerdict] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class BrowserFrameInferenceRequest(BaseModel):
    image_base64: str
    width: Annotated[int, Field(gt=0)] = 1280
    height: Annotated[int, Field(gt=0)] = 720
    device_label: str = "browser_camera"


class CameraStatus(BaseModel):
    camera_mode: str
    source: int | str | None
    connected: bool
    running: bool
    stream_enabled: bool
    width: int
    height: int
    fps: int
    last_error: str | None = None
    selected_device: str | None = None
    selected_backend: str | None = None
    source_mode: str | None = None
    input_format: str | None = None
    resolution: str | None = None
    last_frame_age_ms: int | None = None
    last_capture_error: str | None = None
    is_real_camera_evidence: bool = False
    is_external_usb_camera: bool = False
    is_laptop_camera: bool = False
    hardware_presence_note: str | None = None


class CameraSource(BaseModel):
    id: str
    label: str
    mode: str
    available: bool


class CameraSelectRequest(BaseModel):
    camera_mode: str
    camera_source: int | str | None = None


class VisionConfigUpdate(BaseModel):
    vision_mode: str
    body_model_path: str | None = None
    balloon_model_path: str | None = None
    body_conf_threshold: Annotated[float, Field(ge=0, le=1)] = 0.35
    balloon_conf_threshold: Annotated[float, Field(ge=0, le=1)] = 0.35


class VisionStatus(BaseModel):
    running: bool
    vision_mode: str
    model_loading_required: bool
    body_model_path: str | None
    balloon_model_path: str | None
    body_model_loaded: bool
    balloon_model_loaded: bool
    fps: float
    camera_fps: float | None = None
    detector_fps: float | None = None
    latest_frame_id: int
    latest_latency_ms: float
    latest_total_ms: float | None = None
    camera_source_kind: str | None = None
    frame_origin: str | None = None
    detector_kind: str | None = None
    body_count: int
    balloon_count: int
    model_count: int = 0
    model_execution: str = "none"
    model_timings_ms: dict[str, float] = Field(default_factory=dict)
    warnings: list[str]
    advisory_only: bool = True
