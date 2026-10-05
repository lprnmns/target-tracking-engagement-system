from typing import Annotated, Literal

from pydantic import BaseModel, Field, model_validator

from app.schemas.calibration import LensProfile
from app.schemas.color import ColorSpace, HSVRange
from app.schemas.system import FirePolicy, MissionMode


class SystemConfig(BaseModel):
    name: str = "ISTIKLAL Command Center"
    mode: MissionMode = MissionMode.AUTONOMOUS
    default_fire_policy: FirePolicy = FirePolicy.FIRE_ALLOWED
    dry_run: bool = False
    hardware_enabled: bool = True


class CameraConfig(BaseModel):
    source: int | str = 0
    width: Annotated[int, Field(gt=0)] = 1920
    height: Annotated[int, Field(gt=0)] = 1080
    fps: Annotated[int, Field(gt=0)] = 60
    mock: bool = False
    camera_mode: str = "webcam"
    camera_source: int | str | None = None
    stream_enabled: bool = True
    stream_width: Annotated[int, Field(gt=0)] = 640
    stream_height: Annotated[int, Field(gt=0)] = 360
    stream_fps: Annotated[int, Field(gt=1)] = 15

    @model_validator(mode="after")
    def validate_camera_mode(self) -> "CameraConfig":
        if self.camera_mode not in {"mock", "image", "webcam", "live"}:
            raise ValueError("camera.camera_mode must be mock, image, webcam or live")
        return self


class VisionConfig(BaseModel):
    body_model: str
    balloon_model: str
    imgsz: Annotated[int, Field(gt=0)] = 960
    body_conf: Annotated[float, Field(ge=0, le=1)] = 0.35
    balloon_conf: Annotated[float, Field(ge=0, le=1)] = 0.40
    iou: Annotated[float, Field(ge=0, le=1)] = 0.50
    tracker: str = "bytetrack.yaml"
    stable_frames_required: Annotated[int, Field(ge=1)] = 5
    max_lost_frames: Annotated[int, Field(ge=1)] = 8
    mock: bool = False
    vision_mode: str = "ultralytics_yolo"
    model_loading_required: bool = False
    body_model_path: str | None = None
    balloon_model_path: str | None = None
    body_conf_threshold: Annotated[float, Field(ge=0, le=1)] = 0.35
    balloon_conf_threshold: Annotated[float, Field(ge=0, le=1)] = 0.35
    overlay_coordinate_format: str = "pixel"

    @model_validator(mode="after")
    def validate_vision_mode(self) -> "VisionConfig":
        if self.vision_mode not in {"mock", "yolo", "ultralytics_yolo"}:
            raise ValueError("vision.vision_mode must be mock, yolo or ultralytics_yolo")
        return self


class PicoConfig(BaseModel):
    port: str | None = None
    baudrate: Annotated[int, Field(gt=0)] = 115200
    heartbeat_timeout_ms: Annotated[int, Field(gt=0)] = 500
    protocol: str = "json-line"
    mock: bool = False

    @model_validator(mode="after")
    def validate_protocol(self) -> "PicoConfig":
        # raw format (SPD,x,y\n) ve json-line destekleniyor
        if self.protocol not in {"json-line", "raw"}:
            raise ValueError("pico.protocol must be json-line or raw")
        return self


class SerialConfig(BaseModel):
    protocol_mode: str = "json-line"
    raw_protocol_variant: str = "arduino"
    transport_mode: str = "real_write"
    port: str | None = None
    baudrate: Annotated[int, Field(gt=0)] = 115200
    ack_timeout_ms: Annotated[int, Field(gt=0)] = 300
    heartbeat_timeout_ms: Annotated[int, Field(gt=0)] = 750
    real_serial_enabled: bool = True
    real_serial_readonly: bool = False
    auto_connect: bool = True
    serial_tx_enabled: bool = False

    @model_validator(mode="after")
    def validate_serial_safety(self) -> "SerialConfig":
        if self.protocol_mode not in {"json-line", "binary", "raw"}:
            raise ValueError("serial.protocol_mode must be json-line, binary or raw")
        if self.transport_mode not in {"mock", "real_readonly", "real_write"}:
            raise ValueError("serial.transport_mode must be mock, real_readonly or real_write")
        if self.raw_protocol_variant not in {"arduino", "light"}:
            raise ValueError("serial.raw_protocol_variant must be arduino or light")
        # Ateş/trigger komutları SerialService seviyesinde her zaman engelli.
        return self


class HardwareConfig(BaseModel):
    hardware_discovery_enabled: bool = True
    physical_command_enabled: bool = True
    allow_real_serial_readonly: bool = False
    allow_physical_motion: bool = True
    allow_physical_fire: bool = True

    @model_validator(mode="after")
    def validate_hardware_safety(self) -> "HardwareConfig":
        # hardware.physical_command_enabled allows actual triggering algorithms to run
        # allow_physical_fire allows physical firing (servo/laser pin outputs)
        return self


class PinProfileConfig(BaseModel):
    profile_name: str
    note: str
    assignments: dict[str, str]

    @model_validator(mode="after")
    def validate_pin_assignments(self) -> "PinProfileConfig":
        assigned = [pin for pin in self.assignments.values() if pin]
        duplicates = sorted({pin for pin in assigned if assigned.count(pin) > 1})
        if duplicates:
            raise ValueError(f"pin assignments contain duplicates: {', '.join(duplicates)}")
        invalid = sorted(pin for pin in assigned if not pin.startswith("GP"))
        if invalid:
            raise ValueError(f"only GPIO pins may be assigned in phase 1: {', '.join(invalid)}")
        required = {"estop_in", "trigger_servo_pwm"}
        missing = sorted(required - set(self.assignments))
        if missing:
            raise ValueError(f"critical pin assignments missing: {', '.join(missing)}")
        return self


class MotorConfig(BaseModel):
    pan_steps_per_degree: Annotated[float, Field(gt=0)]
    tilt_steps_per_degree: Annotated[float, Field(gt=0)]
    max_speed: Annotated[int, Field(gt=0)]
    acceleration: Annotated[int, Field(gt=0)]
    backlash_compensation_steps: Annotated[int, Field(ge=0)]
    deadband_px: Annotated[int, Field(ge=0)]
    # Operator/world coordinates stay stable while these machine-specific
    # multipliers adapt the command to the installed motor wiring.
    pan_direction_multiplier: Literal[1, -1] = 1
    tilt_direction_multiplier: Literal[1, -1] = 1
    axis_swap: bool = False


class MotionConfig(BaseModel):
    dry_run: bool = False
    real_motion_enabled: bool = True
    soft_limits_enabled: bool = False
    pan_min_deg: float = -60.0
    pan_max_deg: float = 60.0
    tilt_min_deg: float = -28.0
    tilt_max_deg: float = 28.0
    pan_steps_per_degree: Annotated[float, Field(gt=0)] = 10.0
    tilt_steps_per_degree: Annotated[float, Field(gt=0)] = 10.0
    # Pico Arduino protocol accepts normalized -1000..1000 commands and maps
    # them to axis-specific physical step rates. Open-loop pose estimation must
    # use the same mapping; treating the normalized command as step/s makes the
    # digital twin 4-6x slower than the real turret.
    command_full_scale: Annotated[float, Field(gt=0)] = 1000.0
    pan_max_steps_per_second: Annotated[float, Field(gt=0)] = 4000.0
    tilt_max_steps_per_second: Annotated[float, Field(gt=0)] = 6000.0
    pan_max_speed_deg_s: Annotated[float, Field(ge=0)] = 20.0
    tilt_max_speed_deg_s: Annotated[float, Field(ge=0)] = 15.0
    pan_accel_deg_s2: Annotated[float, Field(ge=0)] = 50.0
    tilt_accel_deg_s2: Annotated[float, Field(ge=0)] = 40.0
    jog_step_deg: Annotated[float, Field(gt=0)] = 1.0
    deadband_px: Annotated[int, Field(ge=0)] = 12
    tracking_gain_x: float = 0.05
    tracking_gain_y: float = 0.05
    backlash_compensation_enabled: bool = False
    scan_enabled: bool = False
    scan_min_deg: float = -45.0
    scan_max_deg: float = 45.0
    scan_speed_deg_s: Annotated[float, Field(ge=0)] = 10.0
    motion_forbidden_zones: list["AngularSafetyZone"] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_motion_safety(self) -> "MotionConfig":
        # NOT: dry_run ve real_motion_enabled kilitleri kaldırıldı.
        # Ateş/trigger komutları SerialService seviyesinde her zaman engelli.
        if self.pan_min_deg >= self.pan_max_deg:
            raise ValueError("motion pan_min_deg must be < pan_max_deg")
        if self.tilt_min_deg >= self.tilt_max_deg:
            raise ValueError("motion tilt_min_deg must be < tilt_max_deg")
        if self.scan_min_deg >= self.scan_max_deg:
            raise ValueError("motion scan_min_deg must be < scan_max_deg")
        return self


class CalibrationConfig(BaseModel):
    camera_height_cm: Annotated[float, Field(gt=0)] = 60.0
    target_height_cm: Annotated[float, Field(gt=0)] = 130.0
    table_height_cm: Annotated[float, Field(ge=0)] = 60.0
    lens_profile: LensProfile = LensProfile.UNKNOWN
    hfov_deg: Annotated[float, Field(gt=0, lt=180)] = 45.0
    vfov_deg: Annotated[float | None, Field(gt=0, lt=180)] = None
    resolution_width: Annotated[int, Field(gt=0)] = 640
    resolution_height: Annotated[int, Field(gt=0)] = 360
    fps: Annotated[int, Field(gt=0)] = 15
    homography_enabled: bool = False
    distortion_enabled: bool = False


class ColorConfig(BaseModel):
    color_space: ColorSpace = ColorSpace.HSV
    balloon_mask_enabled: bool = True
    saturation_min: Annotated[int, Field(ge=0, le=255)] = 70
    value_min: Annotated[int, Field(ge=0, le=255)] = 50
    min_body_pixels: Annotated[int, Field(gt=0)] = 200
    decision_threshold: Annotated[float, Field(ge=0, le=1)] = 0.55
    temporal_window: Annotated[int, Field(ge=1)] = 5
    required_consistent_frames: Annotated[int, Field(ge=1)] = 3
    lab_enabled: bool = False
    morphology_kernel: Annotated[int, Field(ge=1)] = 3
    enemy_hsv_ranges: list[HSVRange]
    friend_hsv_ranges: list[HSVRange]
    balloon_hsv_ranges: list[HSVRange]

    @model_validator(mode="after")
    def validate_color_config(self) -> "ColorConfig":
        if not self.enemy_hsv_ranges:
            raise ValueError("color.enemy_hsv_ranges must not be empty")
        if not self.friend_hsv_ranges:
            raise ValueError("color.friend_hsv_ranges must not be empty")
        if not self.balloon_hsv_ranges:
            raise ValueError("color.balloon_hsv_ranges must not be empty")
        return self


class SafetyConfig(BaseModel):
    require_armed: bool = True
    require_estop_released: bool = True
    require_enemy: bool = True
    require_balloon: bool = True
    require_valid_range: bool = True
    require_stable_track: bool = True
    no_fire_default: bool = False
    stable_frames_required: Annotated[int, Field(ge=1)] = 5
    require_operator_confirm: bool = False
    forbidden_zone_check_enabled: bool = False
    default_fire_policy: str = "FIRE_ALLOWED"


class RangeRule(BaseModel):
    min_m: Annotated[float, Field(ge=0)]
    max_m: Annotated[float, Field(gt=0)]

    @model_validator(mode="after")
    def validate_range(self) -> "RangeRule":
        if self.max_m < self.min_m:
            raise ValueError("range rule max_m must be >= min_m")
        return self


class AngularSafetyZone(BaseModel):
    """A persistent turret-angle exclusion rectangle in degrees."""

    name: str = Field(min_length=1, max_length=64)
    pan_min_deg: float
    pan_max_deg: float
    tilt_min_deg: float
    tilt_max_deg: float
    enabled: bool = True

    @model_validator(mode="after")
    def validate_bounds(self) -> "AngularSafetyZone":
        if self.pan_min_deg >= self.pan_max_deg:
            raise ValueError("angular safety zone pan_min_deg must be < pan_max_deg")
        if self.tilt_min_deg >= self.tilt_max_deg:
            raise ValueError("angular safety zone tilt_min_deg must be < tilt_max_deg")
        return self


class DecisionConfig(BaseModel):
    stable_frames_required: Annotated[int, Field(ge=1)] = 5
    require_enemy: bool = True
    require_balloon: bool = True
    require_valid_range: bool = True
    require_operator_confirm: bool = False
    forbidden_zone_check_enabled: bool = False
    fire_forbidden_zones: list[AngularSafetyZone] = Field(default_factory=list)
    default_fire_policy: str = "FIRE_ALLOWED"
    range_rules: dict[str, RangeRule]

    @model_validator(mode="after")
    def validate_decision_defaults(self) -> "DecisionConfig":
        if self.default_fire_policy != "FIRE_ALLOWED":
            raise ValueError("decision.default_fire_policy must be FIRE_ALLOWED")
        required = {"f16", "helicopter", "ballistic_missile", "mini_micro_uav"}
        missing = required - set(self.range_rules)
        if missing:
            raise ValueError(f"decision.range_rules missing: {', '.join(sorted(missing))}")
        return self


class LoggingConfig(BaseModel):
    path: str = "logs"
    jsonl: bool = True
    save_overlay_video: bool = False


class ModelsConfig(BaseModel):
    root_dir: str = "models"
    allowed_extensions: list[str] = Field(default_factory=lambda: [".pt", ".onnx", ".yaml"])
    max_upload_size_mb: Annotated[int, Field(gt=0)] = 500
    active_models_file: str = "models/active/active_models.json"
    default_adapter: str = "mock"

    @model_validator(mode="after")
    def validate_models_config(self) -> "ModelsConfig":
        allowed = {extension.lower() for extension in self.allowed_extensions}
        required = {".pt", ".onnx", ".yaml"}
        missing = required - allowed
        if missing:
            raise ValueError(f"models.allowed_extensions missing: {', '.join(sorted(missing))}")
        if self.default_adapter not in {"mock", "opencv_stub", "ultralytics_yolo", "opencv_circle_test"}:
            raise ValueError("models.default_adapter must be mock, opencv_stub, opencv_circle_test or ultralytics_yolo")
        return self


class DatasetConfig(BaseModel):
    root_dir: str = "data"
    default_train_val_split: Annotated[float, Field(gt=0, lt=1)] = 0.8
    require_verified_annotations_for_export: bool = True
    default_export_mode: str = "combined_body_balloon"
    snapshot_format: str = "jpg"
    save_mock_frames: bool = True
    max_preview_events: Annotated[int, Field(gt=0)] = 500

    @model_validator(mode="after")
    def validate_dataset_config(self) -> "DatasetConfig":
        if self.default_export_mode not in {
            "body_multiclass",
            "balloon_singleclass",
            "combined_body_balloon",
            "target_singleclass",
        }:
            raise ValueError("dataset.default_export_mode is unsupported")
        if self.snapshot_format not in {"jpg", "png"}:
            raise ValueError("dataset.snapshot_format must be jpg or png")
        return self


class ReportsConfig(BaseModel):
    root_dir: str = "exports/reports"
    include_screenshots: bool = True
    include_self_test_latest: bool = True
    include_dataset_summary: bool = True
    include_model_registry: bool = True
    include_safety_summary: bool = True


class RuntimeModeConfig(BaseModel):
    mode: str = "development"
    frontend_static_enabled: bool = True
    auto_open_browser: bool = True
    launcher_managed: bool = False
    offline_mode: bool = False
    dependency_check_enabled: bool = True

    @model_validator(mode="after")
    def validate_runtime_mode(self) -> "RuntimeModeConfig":
        valid = {"development", "portable", "demo", "field_dry_run", "field_live", "production"}
        if self.mode not in valid:
            raise ValueError(f"runtime_mode.mode must be one of: {', '.join(sorted(valid))}")
        return self


class DeviceManagerConfig(BaseModel):
    scan_interval_ms: Annotated[int, Field(gt=0)] = 2000
    enable_hotplug_polling: bool = True


class CameraRuntimeConfig(BaseModel):
    default_source_type: str = "usb"
    default_device_path: str | None = "/dev/video2"
    default_width: Annotated[int, Field(gt=0)] = 640
    default_height: Annotated[int, Field(gt=0)] = 360
    default_fps: Annotated[int, Field(gt=0)] = 15
    default_fourcc: str = "auto"
    default_lens_profile: str = "unknown"
    inference_width: Annotated[int, Field(gt=0)] = 640
    inference_height: Annotated[int, Field(gt=0)] = 360

    @model_validator(mode="after")
    def validate_camera_runtime(self) -> "CameraRuntimeConfig":
        if self.default_source_type not in {"laptop", "usb", "video_file", "replay", "mock"}:
            raise ValueError("camera_runtime.default_source_type is unsupported")
        if self.default_fourcc not in {"MJPG", "YUYV", "auto"}:
            raise ValueError("camera_runtime.default_fourcc must be MJPG, YUYV or auto")
        return self


class VisionRuntimeConfig(BaseModel):
    default_adapter: str = "ultralytics_yolo"
    default_imgsz: Annotated[int, Field(gt=0)] = 640
    default_conf: Annotated[float, Field(ge=0, le=1)] = 0.25
    default_iou: Annotated[float, Field(ge=0, le=1)] = 0.45
    default_max_det: Annotated[int, Field(gt=0)] = 20
    default_device: str = "cpu"
    allow_cuda: bool = False

    @model_validator(mode="after")
    def validate_vision_runtime(self) -> "VisionRuntimeConfig":
        if self.default_adapter not in {"mock", "opencv_circle_test", "opencv_live_circle_surrogate", "ultralytics_yolo"}:
            raise ValueError("vision_runtime.default_adapter is unsupported")
        if self.default_device not in {"cpu", "cuda", "auto"}:
            raise ValueError("vision_runtime.default_device must be cpu, cuda or auto")
        if self.default_device == "cuda" and not self.allow_cuda:
            raise ValueError("vision_runtime.default_device=cuda requires allow_cuda=true")
        return self


class TrackingConfig(BaseModel):
    enabled: bool = True
    pid_kp_x: float = 1200.0
    pid_ki_x: float = 0.0
    pid_kd_x: float = 120.0
    pid_kp_y: float = 800.0
    pid_ki_y: float = 0.0
    pid_kd_y: float = 130.0
    smoothing_alpha: Annotated[float, Field(ge=0.0, le=1.0)] = 0.90
    command_rate_hz: Annotated[float, Field(gt=0)] = 30.0
    max_speed: Annotated[int, Field(gt=0, le=10000)] = 1000
    # Axis-specific live limits. ``None`` preserves legacy profiles that only
    # contain max_speed and resolves both axes to that common value.
    max_speed_x: Annotated[int, Field(gt=0, le=10000)] | None = None
    max_speed_y: Annotated[int, Field(gt=0, le=10000)] | None = None
    min_move_speed: float = 0.0
    output_min: float = -1000.0
    output_max: float = 1000.0
    integral_max: float = 25000.0
    deadband_lock_ratio: float = 0.0
    deadband_slow_ratio: float = 0.0
    deadband_medium_ratio: float = 0.0
    bbox_lock_enabled: bool = True
    momentum_lock_enabled: bool = True
    momentum_velocity_threshold_px_s: Annotated[float, Field(ge=0.0, le=2000.0)] = 12.0
    max_lost_frames: Annotated[int, Field(ge=1)] = 10
    aim_offset_x_px: float = 0.0
    aim_offset_y_px: float = 0.0
    invert_x: bool = False
    invert_y: bool = True
    lead_enabled: bool = False
    lead_latency_multiplier: Annotated[float, Field(ge=0.0, le=3.0)] = 1.0
    lead_max_horizon_ms: Annotated[float, Field(ge=0.0, le=300.0)] = 120.0


class DigitalTwinConfig(BaseModel):
    enabled: bool = False
    replay_enabled: bool = True
    command_authority: bool = False
    fixture_path: str = "fixtures/digital_twin/balloon_tracking_run_001.json"
    asset_registry_path: str = "reports/digital_twin_asset_registry.json"
    state_refresh_hz: Annotated[float, Field(gt=0, le=30)] = 10.0
    camera_fov_horizontal_deg: Annotated[float, Field(gt=1.0, le=179.0)] = 62.0
    camera_fov_vertical_deg: Annotated[float, Field(gt=1.0, le=179.0)] = 38.0
    camera_to_launcher_offset_z_mm: float = 30.0
    camera_to_launcher_offset_y_mm: float = 0.0
    balloon_diameter_mm: Annotated[float, Field(gt=0.0, le=1000.0)] = 140.0

    @model_validator(mode="after")
    def validate_read_only_contract(self) -> "DigitalTwinConfig":
        if self.command_authority:
            raise ValueError("digital_twin.command_authority must remain false")
        return self


class PersonSafetyConfig(BaseModel):
    enabled: bool = True
    confidence_threshold: Annotated[float, Field(ge=0.0, le=1.0)] = 0.55
    hold_ms: Annotated[int, Field(ge=0)] = 800
    clear_after_ms: Annotated[int, Field(gt=0)] = 1200

    @model_validator(mode="after")
    def validate_person_safety_timing(self) -> "PersonSafetyConfig":
        if self.clear_after_ms < self.hold_ms:
            raise ValueError("person_safety.clear_after_ms must be >= hold_ms")
        return self


class AppConfig(BaseModel):
    system: SystemConfig
    hardware: HardwareConfig
    camera: CameraConfig
    vision: VisionConfig
    pico: PicoConfig
    serial: SerialConfig
    pins: PinProfileConfig
    motor: MotorConfig
    motion: MotionConfig
    calibration: CalibrationConfig
    color: ColorConfig
    safety: SafetyConfig
    decision: DecisionConfig
    models: ModelsConfig
    dataset: DatasetConfig
    reports: ReportsConfig
    runtime_mode: RuntimeModeConfig = Field(default_factory=RuntimeModeConfig)
    device_manager: DeviceManagerConfig = Field(default_factory=DeviceManagerConfig)
    camera_runtime: CameraRuntimeConfig = Field(default_factory=CameraRuntimeConfig)
    vision_runtime: VisionRuntimeConfig = Field(default_factory=VisionRuntimeConfig)
    logging: LoggingConfig
    tracking: TrackingConfig = Field(default_factory=TrackingConfig)
    digital_twin: DigitalTwinConfig = Field(default_factory=DigitalTwinConfig)
    person_safety: PersonSafetyConfig = Field(default_factory=PersonSafetyConfig)

    @model_validator(mode="after")
    def validate_phase12_serial_gates(self) -> "AppConfig":
        live_modes = {"production", "field_live"}
        if self.runtime_mode.mode not in live_modes:
            if self.system.mode != MissionMode.DISARMED:
                raise ValueError("system.mode must be DISARMED outside production/field_live")
            if self.system.default_fire_policy != FirePolicy.NO_FIRE:
                raise ValueError("system.default_fire_policy must be NO_FIRE outside production/field_live")
            if not self.system.dry_run:
                raise ValueError("system.dry_run must be true outside production/field_live")
            if self.system.hardware_enabled:
                raise ValueError("system.hardware_enabled must be false outside production/field_live")
            if self.serial.real_serial_enabled:
                raise ValueError("serial.real_serial_enabled must be false outside production/field_live")
            if self.hardware.physical_command_enabled:
                raise ValueError("hardware.physical_command_enabled must be false outside production/field_live")
            if self.hardware.allow_physical_motion:
                raise ValueError("hardware.allow_physical_motion must be false outside production/field_live")
            if self.hardware.allow_physical_fire:
                raise ValueError("hardware.allow_physical_fire must be false outside production/field_live")
            if self.motion.real_motion_enabled:
                raise ValueError("motion.real_motion_enabled must be false outside production/field_live")
        if self.serial.transport_mode == "real_readonly" and not self.hardware.allow_real_serial_readonly:
            raise ValueError("serial.transport_mode=real_readonly requires hardware.allow_real_serial_readonly=true")
        return self
