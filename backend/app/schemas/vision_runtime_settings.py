import time
from typing import Literal

from pydantic import BaseModel, Field, model_validator


InferenceAdapter = Literal["mock", "opencv_circle_test", "opencv_live_circle_surrogate", "ultralytics_yolo"]
RuntimeDevice = Literal["cpu", "cuda", "auto"]
TrackerType = Literal[
    "none",
    "current_custom",
    "bytetrack",
    "botsort_no_cmc",
    "botsort_sparse",
    "botsort_ecc",
    "ocsort",
    "norfair_custom",
]
FriendEnemyColorMode = Literal["disabled", "hsv", "lab", "model_metadata"]


class VisionRuntimeProfile(BaseModel):
    inference_adapter: InferenceAdapter = "opencv_circle_test"
    active_body_model_id: str | None = None
    active_balloon_model_id: str | None = None
    device: RuntimeDevice = "cpu"
    imgsz: int = Field(default=640, gt=0)
    conf: float = Field(default=0.25, ge=0, le=1)
    iou: float = Field(default=0.45, ge=0, le=1)
    max_det: int = Field(default=20, gt=0)
    classes: list[int] | None = None
    agnostic_nms: bool = False
    half: bool = False
    # Separate body/balloon weights share one captured frame.  On CUDA hosts
    # the worker may dispatch them concurrently; CPU hosts fall back to the
    # safer sequential path automatically.
    # On the 4 GB RTX 2050 field host two independent Ultralytics calls do
    # not overlap their CUDA kernels usefully.  Threaded submission adds
    # jitter and produced a worse p95 than deterministic sequential dispatch.
    parallel_models: bool = False
    vid_stride: int = Field(default=1, ge=1)
    stream_buffer: bool = False
    frame_skip: int = Field(default=0, ge=0)
    augment: bool = False
    retina_masks: bool | None = None
    tracker_enabled: bool = False
    tracker_type: TrackerType = "none"
    body_conf_threshold: float = Field(default=0.35, ge=0, le=1)
    balloon_conf_threshold: float = Field(default=0.01, ge=0, le=1)
    min_box_area_px: int = Field(default=0, ge=0)
    max_box_area_px: int | None = Field(default=None, gt=0)
    target_class_map: dict[str, int] = Field(default_factory=dict)
    friend_enemy_color_mode: FriendEnemyColorMode = "hsv"
    latency_budget_ms: float = Field(default=120.0, gt=0)
    target_fps: float = Field(default=60.0, gt=0)
    warmup_on_load: bool = False
    benchmark_on_apply: bool = False
    circle_min_radius: int = Field(default=8, ge=1)
    circle_max_radius: int = Field(default=90, ge=1)
    circle_blur_kernel: int = Field(default=5, ge=1)
    circle_threshold: int = Field(default=80, ge=0, le=255)
    circle_edge_param: int = Field(default=80, ge=1, le=255)
    circle_min_area: int = Field(default=80, ge=0)
    circle_circularity: float = Field(default=0.55, ge=0, le=1)
    circle_target_color_mode: Literal["any", "red", "green", "blue", "bright"] = "any"
    circle_roi_enabled: bool = False
    circle_smoothing: bool = False

    @model_validator(mode="after")
    def validate_profile(self) -> "VisionRuntimeProfile":
        if self.conf < 0.001 or self.conf > 0.99:
            raise ValueError("conf must be between 0.001 and 0.99")
        if self.iou < 0.01 or self.iou > 0.99:
            raise ValueError("iou must be between 0.01 and 0.99")
        if self.max_det > 300:
            raise ValueError("max_det must be 300 or lower")
        if self.imgsz < 160 or self.imgsz > 1536:
            raise ValueError("imgsz must be between 160 and 1536")
        if self.max_box_area_px is not None and self.max_box_area_px <= self.min_box_area_px:
            raise ValueError("max_box_area_px must be greater than min_box_area_px")
        if self.circle_max_radius < self.circle_min_radius:
            raise ValueError("circle_max_radius must be greater than or equal to circle_min_radius")
        if self.circle_blur_kernel % 2 == 0:
            raise ValueError("circle_blur_kernel must be an odd number")
        # Setup is allowed to start before a model is selected. Runtime status
        # reports the adapter as unavailable until the operator activates a
        # body or balloon model; missing weights must not crash the backend.
        return self


class VisionRuntimeStatus(BaseModel):
    profile: VisionRuntimeProfile
    active_model_summary: dict[str, str | None] = Field(default_factory=dict)
    active_model_details: dict = Field(default_factory=dict)
    selected_adapter: str = "opencv_circle_test"
    effective_adapter: str = "test_adapter"
    production_yolo_loaded: bool = False
    test_adapter_active: bool = True
    model_package_id: str | None = None
    runtime_source: str = "test_adapter"
    surrogate_source_kind: str | None = None
    frame_origin: str | None = None
    advisory_only: bool = True
    reload_required: bool = False
    adapter_available: bool = True
    requested_device: RuntimeDevice = "cpu"
    resolved_device: Literal["cpu", "cuda"] | None = "cpu"
    cuda_available: bool = False
    device_reason: str = "cpu_requested"
    cuda_fallback_active: bool = False
    cuda_fallback_reason: str | None = None
    cuda_last_failure_at: float | None = None
    cuda_last_recovery_attempt: float | None = None
    cuda_last_recovery_success_at: float | None = None
    cuda_device_name: str | None = None
    cuda_free_memory_mb: float | None = None
    cuda_total_memory_mb: float | None = None
    cuda_model_devices: list[str] = Field(default_factory=list)
    latest_parameter_version: int = 1
    current_fps: float = 0.0
    latest_latency_ms: float = 0.0
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    updated_at: float = Field(default_factory=time.time)
    no_physical_command_generated: bool = True


class VisionRuntimePreset(BaseModel):
    name: str
    capture_width: int = Field(gt=0)
    capture_height: int = Field(gt=0)
    stream_width: int = Field(gt=0)
    stream_height: int = Field(gt=0)
    inference_width: int = Field(gt=0)
    inference_height: int = Field(gt=0)
    fps: int = Field(gt=0)
    imgsz: int = Field(gt=0)
    conf: float = Field(ge=0, le=1)
    iou: float = Field(ge=0, le=1)
    max_det: int = Field(gt=0)
    frame_skip: int = Field(ge=0)
    vid_stride: int = Field(ge=1)
    tracker: TrackerType = "none"
    half: bool = False


class VisionRuntimePresetApplyRequest(BaseModel):
    preset_name: str


class VisionRuntimePresetSaveRequest(BaseModel):
    preset: VisionRuntimePreset


class VisionRuntimeVerifyResult(BaseModel):
    accepted: bool
    profile: VisionRuntimeProfile
    active_model_details: dict = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    no_physical_command_generated: bool = True


class VisionRuntimeTestResult(BaseModel):
    accepted: bool
    active_model_id: str | None = None
    adapter: str
    detections: list[dict] = Field(default_factory=list)
    latency_ms: float = 0.0
    warnings: list[str] = Field(default_factory=list)
    no_physical_command_generated: bool = True


class VisionRuntimeApplyResult(BaseModel):
    accepted: bool
    applied: bool
    rollback_performed: bool = False
    reload_required: bool = False
    profile: VisionRuntimeProfile
    status: VisionRuntimeStatus
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    suggested_action: str | None = None
    no_physical_command_generated: bool = True
