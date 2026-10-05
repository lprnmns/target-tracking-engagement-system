from pydantic import BaseModel, Field


class PerformanceMetric(BaseModel):
    value: float | int | None
    unit: str
    green_max: float
    yellow_max: float
    tone: str
    label: str


class PerformanceStatus(BaseModel):
    cpu_percent: float | None = None
    process_cpu_percent: float | None = None
    memory_percent: float | None = None
    process_rss_mb: float | None = None
    load_avg_1m: float | None = None
    gpu_util_percent: float | None = None
    gpu_memory_percent: float | None = None
    camera_frame_age_ms: int | None = None
    camera_fps: float | None = None
    dropped_frames: int = 0
    preprocess_ms: float | None = None
    inference_ms: float | None = None
    postprocess_ms: float | None = None
    vision_total_ms: float | None = None
    tracking_loop_ms: float | None = None
    serial_ack_rtt_ms: int | None = None
    serial_pending_ack_count: int = 0
    serial_queue_depth: int = 0
    pico_heartbeat_age_ms: int | None = None
    total_pipeline_ms: float | None = None
    metrics: dict[str, PerformanceMetric] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    primary_bottleneck: str = "none"
    bottleneck_summary: str = "Akış normal."
    recommended_actions: list[str] = Field(default_factory=list)
    updated_at: float
