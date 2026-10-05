import time
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


ModelType = Literal[
    "body_detector",
    "balloon_detector",
    "combined_detector",
    "color_classifier_adapter",
    "test_stub",
]
ModelFramework = Literal["ultralytics", "onnx", "opencv_stub", "external_adapter"]
ModelStatus = Literal["uploaded", "validated", "active", "inactive", "invalid", "missing_file"]
ProvidedBy = Literal["vision_team", "test_stub", "imported"]
InferenceSource = Literal["live_camera", "snapshot", "replay", "mock", "uploaded_image"]
AdapterName = Literal["ultralytics", "onnx", "opencv_stub", "mock"]


class ModelMetadata(BaseModel):
    model_id: str
    name: str
    version: str = "0.1.0"
    model_type: ModelType
    framework: ModelFramework
    file_path: str | None = None
    file_name: str | None = None
    file_size_bytes: int = 0
    class_names: list[str] = Field(default_factory=list)
    input_size: int = 640
    confidence_threshold: float = Field(default=0.35, ge=0, le=1)
    iou_threshold: float = Field(default=0.50, ge=0, le=1)
    status: ModelStatus = "uploaded"
    provided_by: ProvidedBy = "imported"
    created_at: float = Field(default_factory=time.time)
    last_validated_at: float | None = None
    last_test_result: dict | None = None
    notes: str | None = None
    warnings: list[str] = Field(default_factory=list)


class ModelUploadRequest(BaseModel):
    name: str
    version: str = "0.1.0"
    model_type: ModelType
    framework: ModelFramework
    file_name: str
    file_size_bytes: int = Field(default=0, ge=0)
    class_names: list[str] = Field(default_factory=list)
    input_size: int = Field(default=640, gt=0)
    confidence_threshold: float = Field(default=0.35, ge=0, le=1)
    iou_threshold: float = Field(default=0.50, ge=0, le=1)
    provided_by: ProvidedBy = "vision_team"
    notes: str | None = None

    @field_validator("file_name")
    @classmethod
    def validate_file_name(cls, value: str) -> str:
        if "/" in value or "\\" in value:
            raise ValueError("file_name must not include path separators")
        return value


class ModelMetadataUpdate(BaseModel):
    name: str | None = None
    version: str | None = None
    model_type: ModelType | None = None
    framework: ModelFramework | None = None
    class_names: list[str] | None = None
    input_size: int | None = Field(default=None, gt=0)
    confidence_threshold: float | None = Field(default=None, ge=0, le=1)
    iou_threshold: float | None = Field(default=None, ge=0, le=1)
    provided_by: ProvidedBy | None = None
    notes: str | None = None


class ModelValidationResult(BaseModel):
    model_id: str
    valid: bool
    status: ModelStatus
    checks: dict[str, bool]
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class ActiveModels(BaseModel):
    active_body_model_id: str | None = None
    active_balloon_model_id: str | None = None
    active_combined_model_id: str | None = None
    active_test_adapter: str | None = "opencv-circle-test-adapter"
    updated_at: float = Field(default_factory=time.time)


class ModelActivationRequest(BaseModel):
    slot: Literal["body", "balloon", "combined", "test_adapter"] | None = None


class BBoxFormats(BaseModel):
    bbox_xyxy_pixel: list[float]
    bbox_xywh_pixel: list[float]
    bbox_yolo_normalized: list[float]

    @model_validator(mode="after")
    def validate_lengths(self) -> "BBoxFormats":
        for name in ("bbox_xyxy_pixel", "bbox_xywh_pixel", "bbox_yolo_normalized"):
            if len(getattr(self, name)) != 4:
                raise ValueError(f"{name} must contain 4 numbers")
        return self


class InferenceDetection(BBoxFormats):
    detection_id: str
    class_id: int
    class_name: str
    confidence: float = Field(ge=0, le=1)
    source: Literal["yolo", "onnx", "opencv_stub", "mock"]
    is_balloon: bool = False
    track_id: str | None = None
    color_decision: dict | None = None


class InferenceResult(BaseModel):
    frame_id: str
    source: InferenceSource
    model_id: str | None
    adapter: AdapterName
    detections: list[InferenceDetection] = Field(default_factory=list)
    latency_ms: float = 0.0
    preprocess_ms: float = 0.0
    inference_ms: float = 0.0
    postprocess_ms: float = 0.0
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    no_physical_command_generated: bool = True


class ModelTestInferenceRequest(BaseModel):
    model_id: str | None = None
    source: InferenceSource = "mock"
    frame_id: str = "mock-frame"
    use_test_adapter: bool = True


class OpenCVCircleTestRequest(BaseModel):
    source: InferenceSource = "mock"
    frame_id: str = "opencv-circle-test-frame"
    width: int = Field(default=640, gt=0)
    height: int = Field(default=360, gt=0)
