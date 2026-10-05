from typing import Any, Literal

from pydantic import BaseModel, Field

from app.schemas.pico import PicoProtocolTelemetry


DigitalTwinMode = Literal["fixture", "live_read_only", "replay", "degraded"]
PoseQuality = Literal["fixture", "estimated", "runtime", "unavailable"]
PoseSource = Literal["telemetry", "gateway_open_loop_estimate", "tracker_estimate", "fixture", "replay_fixture", "static_demo_pose"]
AssetStatus = Literal["planned", "placeholder", "available", "missing", "converted"]
DigitalTwinDepthBand = Literal["near", "mid", "far"]


class DigitalTwinVector3(BaseModel):
    x: float
    y: float
    z: float


class DigitalTwinBBox(BaseModel):
    x: int = Field(ge=0)
    y: int = Field(ge=0)
    w: int = Field(gt=0)
    h: int = Field(gt=0)
    format: str = "pixel"


class DigitalTwinDevicePose(BaseModel):
    pan_deg: float = 0.0
    tilt_deg: float = 0.0
    servo_angle_deg: float = 0.0
    pose_quality: PoseQuality = "fixture"
    pose_source: PoseSource = "fixture"
    pan_steps: int = 0
    tilt_steps: int = 0
    source: str = "fixture"


class DigitalTwinCameraState(BaseModel):
    selected_camera: str = "unavailable"
    selected_device: str | None = None
    device_path: str | None = None
    source_type: str = "unknown"
    running: bool = False
    real_camera_stream: bool = False
    is_real_camera_evidence: bool = False
    width: int = 640
    height: int = 360
    fps: float = 0.0
    frame_age_ms: float | None = None
    source_mode: str = "CAMERA_UNAVAILABLE"
    selected_backend: str = "fallback"
    input_format: str = "auto"
    last_capture_error: str | None = None
    is_external_usb_camera: bool = False
    is_laptop_camera: bool = False
    hardware_presence_note: str = "unknown"


class DigitalTwinTargetState(BaseModel):
    detected: bool = False
    selected_target_id: int | None = None
    track_id: int | None = None
    class_id: str = "unknown_target"
    class_label: str = "Unknown target"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    bbox: DigitalTwinBBox | None = None
    center_px: DigitalTwinVector3 | None = None
    normalized_x: float | None = None
    normalized_y: float | None = None
    estimated_scene_position_m: DigitalTwinVector3 | None = None
    source: str = "fixture"


class DigitalTwinTargetProjectionEstimate(BaseModel):
    target_id: int | None = None
    class_name: str = "unknown_target"
    target_team: str = "unknown"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    confidence_label: str = "low"
    bbox: DigitalTwinBBox
    normalized_center_x: float = Field(ge=0.0, le=1.0)
    normalized_center_y: float = Field(ge=0.0, le=1.0)
    normalized_width: float = Field(ge=0.0, le=1.0)
    normalized_height: float = Field(ge=0.0, le=1.0)
    normalized_screen_x: float = Field(ge=-1.0, le=1.0)
    normalized_screen_y: float = Field(ge=-1.0, le=1.0)
    bbox_area_ratio: float = Field(ge=0.0, le=1.0)
    azimuth_deg: float
    elevation_deg: float
    relative_depth: float = Field(ge=0.0, le=1.0)
    estimated_range_band: DigitalTwinDepthBand
    reference_size_m: float | None = Field(default=None, gt=0.0)
    estimated_range_m: float | None = Field(default=None, gt=0.0)
    range_uncertainty_m: float | None = Field(default=None, ge=0.0)
    range_source: str = "bbox_area_relative_estimate"
    scene_position_m: DigitalTwinVector3
    selected: bool = False
    mapping_source: str = "bbox_projection_estimate"
    depth_source: str = "bbox_area_relative_estimate"
    projection_is_calibrated: bool = False
    camera_fov_horizontal_deg: float
    camera_fov_vertical_deg: float
    camera_to_launcher_offset_z_mm: float = 30.0
    camera_to_launcher_offset_y_mm: float = 0.0
    no_physical_command_generated: bool = True


class DigitalTwinTrackerState(BaseModel):
    tracking_enabled: bool = False
    state: str = "IDLE"
    error_x_px: float = 0.0
    error_y_px: float = 0.0
    latency_ms: float | None = None
    command_rate_hz: float = 0.0
    max_speed: int = 0
    source: str = "runtime_read_only"


class DigitalTwinEngagementState(BaseModel):
    fire_allowed: bool = False
    fire_gate_state: str = "FIRE_BLOCKED"
    fire_blocked_reason: str = "digital_twin_read_only"
    last_event: str = "none"
    target_loss_after_engagement: bool = False
    magazine_remaining: int | None = None
    person_safety_blocked: bool = False
    person_detection_confidence: float | None = None


class DigitalTwinLatencyMetrics(BaseModel):
    camera_frame_age_ms: float | None = None
    inference_ms: float | None = None
    tracking_loop_ms: float | None = None
    serial_ack_rtt_ms: float | None = None
    total_pipeline_ms: float | None = None


class DigitalTwinRuntimeState(BaseModel):
    queue_length: int = 0
    camera_mode: str = "unknown"
    pico_connection_state: str = "DISCONNECTED"
    selected_target_id: int | None = None
    latency: DigitalTwinLatencyMetrics = Field(default_factory=DigitalTwinLatencyMetrics)


class DigitalTwinSafetyState(BaseModel):
    e_stop: str = "unknown"
    fire_policy: str = "NO_FIRE"
    hardware_enabled: bool = False
    physical_command_enabled: bool = False
    digital_twin_read_only: bool = True
    digital_twin_command_authority: bool = False
    hardware_acceptance_required: bool = True
    no_physical_command_generated: bool = True
    forbidden_actions: list[str] = Field(default_factory=list)


class DigitalTwinSceneNode(BaseModel):
    id: str
    label: str
    kind: str
    parent: str | None = None
    transform_source: str = "static"


class DigitalTwinState(BaseModel):
    schema_version: str = "phase35.1"
    timestamp_ms: int
    mode: DigitalTwinMode = "fixture"
    feature_enabled: bool = False
    source: str = "fixture_deterministic_mock"
    camera_fov_horizontal_deg: float = 62.0
    camera_fov_vertical_deg: float = 38.0
    camera_to_launcher_offset_z_mm: float = 30.0
    camera_to_launcher_offset_y_mm: float = 0.0
    projection_is_calibrated: bool = False
    depth_source: str = "bbox_area_relative_estimate"
    device_pose: DigitalTwinDevicePose
    camera: DigitalTwinCameraState
    target: DigitalTwinTargetState
    target_projection_estimates: list[DigitalTwinTargetProjectionEstimate] = Field(default_factory=list)
    tracker: DigitalTwinTrackerState
    engagement: DigitalTwinEngagementState
    runtime: DigitalTwinRuntimeState = Field(default_factory=DigitalTwinRuntimeState)
    telemetry_protocol: PicoProtocolTelemetry = Field(default_factory=PicoProtocolTelemetry)
    safety: DigitalTwinSafetyState
    scene_nodes: list[DigitalTwinSceneNode]
    evidence: dict[str, str | bool] = Field(default_factory=dict)
    no_physical_command_generated: bool = True


class DigitalTwinAsset(BaseModel):
    class_id: str
    label: str
    model_path: str
    source_file: str | None = None
    source_sha256: str | None = None
    source_size_bytes: int | None = None
    scale: DigitalTwinVector3 = Field(default_factory=lambda: DigitalTwinVector3(x=1.0, y=1.0, z=1.0))
    rotation_offset_deg: DigitalTwinVector3 = Field(default_factory=lambda: DigitalTwinVector3(x=0.0, y=0.0, z=0.0))
    position_offset_m: DigitalTwinVector3 = Field(default_factory=lambda: DigitalTwinVector3(x=0.0, y=0.0, z=0.0))
    confidence_min: float = Field(default=0.0, ge=0.0, le=1.0)
    status: AssetStatus = "planned"
    notes: str = ""


class DigitalTwinAssetsResponse(BaseModel):
    schema_version: str = "phase41.0"
    device_model: DigitalTwinAsset
    target_assets: list[DigitalTwinAsset]
    available_model_files: list[str] = Field(default_factory=list)
    preferred_browser_asset: str | None = None
    selected_asset_type: str = "PROCEDURAL_FALLBACK"
    selected_asset_path: str | None = None
    source_cad_path: str | None = None
    conversion_status: str = "not_evaluated"
    scale_units: str = "scene_units"
    coordinate_notes: str = ""
    asset_transform: dict[str, Any] = Field(default_factory=dict)
    camera_mount_reference_available: bool = False
    launcher_axis_reference_available: bool = False
    asset_fallback_reason: str = "not_evaluated"
    no_physical_command_generated: bool = True
    digital_twin_read_only: bool = True


class DigitalTwinReplayEvent(BaseModel):
    t_ms: int
    target: DigitalTwinTargetState
    target_projection_estimates: list[DigitalTwinTargetProjectionEstimate] = Field(default_factory=list)
    device_pose: DigitalTwinDevicePose
    tracker: DigitalTwinTrackerState
    note: str = ""
    no_physical_command_generated: bool = True


class DigitalTwinReplaySummary(BaseModel):
    run_id: str
    source: str
    mode: str = "replay"
    duration_ms: int
    event_count: int
    events: list[DigitalTwinReplayEvent]
    no_physical_command_generated: bool = True


class DigitalTwinReplayGenerateResult(BaseModel):
    accepted: bool = True
    run_id: str
    report_path: str
    event_count: int
    no_physical_command_generated: bool = True
    digital_twin_read_only: bool = True
