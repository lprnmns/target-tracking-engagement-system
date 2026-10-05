import time
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


ModelPackageFormat = Literal["pt", "onnx", "engine", "other"]
ModelPackageStatus = Literal["imported", "validated", "active", "inactive", "invalid", "missing_file"]
PackageValidationStatus = Literal["passed", "warning", "failed"]


REQUIRED_COMPETITION_CLASSES = ["f16", "helicopter", "ballistic_missile", "mini_micro_uav", "balloon"]


class ModelPackageMetadata(BaseModel):
    model_id: str
    model_name: str
    version: str
    created_by: str
    created_at: str
    model_format: ModelPackageFormat
    task_type: Literal["detection"] = "detection"
    input_size: int = Field(gt=0)
    expected_classes: list[str] = Field(min_length=1)
    class_id_to_name: dict[str, str] = Field(min_length=1)
    recommended_conf: float = Field(ge=0.01, le=0.99)
    recommended_iou: float = Field(ge=0.01, le=0.99)
    recommended_imgsz: int = Field(gt=0)
    recommended_device: Literal["cpu", "cuda", "auto"] = "cpu"
    notes: str | None = None
    safety_note: Literal["advisory_only"] = "advisory_only"
    provided_by: Literal["vision_team", "test_fixture", "imported"] = "imported"
    production_ready: bool = False


class ModelPackageThresholds(BaseModel):
    default_conf: float = Field(ge=0.01, le=0.99)
    default_iou: float = Field(ge=0.01, le=0.99)
    max_det: int = Field(gt=0, le=300)
    per_class_thresholds: dict[str, float] = Field(default_factory=dict)
    recommended_runtime_preset: str = "balanced"

    @field_validator("per_class_thresholds")
    @classmethod
    def validate_per_class_thresholds(cls, value: dict[str, float]) -> dict[str, float]:
        for threshold in value.values():
            if threshold < 0.01 or threshold > 0.99:
                raise ValueError("per_class_thresholds must be between 0.01 and 0.99")
        return value


class ClassMappingReviewItem(BaseModel):
    class_id: int
    class_name: str
    mapped_role: Literal["body_target", "balloon_target", "unknown"]
    required: bool = False
    status: Literal["mapped", "missing", "optional"] = "mapped"

    @field_validator("mapped_role", mode="before")
    @classmethod
    def normalize_legacy_mapped_role(cls, value: object) -> object:
        if value == "body":
            return "body_target"
        if value == "balloon":
            return "balloon_target"
        return value


class ModelPackageValidationResult(BaseModel):
    model_id: str
    version: str
    status: PackageValidationStatus
    valid: bool
    can_activate: bool
    production_ready: bool
    class_mapping_status: Literal["complete", "missing_required_classes", "metadata_missing", "invalid"]
    checks: dict[str, bool]
    class_mapping: list[ClassMappingReviewItem] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    no_physical_command_generated: bool = True


class ModelPackageRecord(BaseModel):
    model_id: str
    version: str
    package_name: str
    package_path: str
    model_file: str | None = None
    checksum_sha256: str | None = None
    metadata: ModelPackageMetadata | None = None
    thresholds: ModelPackageThresholds | None = None
    status: ModelPackageStatus = "imported"
    active: bool = False
    validation: ModelPackageValidationResult | None = None
    last_test_result: dict | None = None
    last_benchmark_result: dict | None = None
    imported_at: float = Field(default_factory=time.time)
    activated_at: float | None = None
    warnings: list[str] = Field(default_factory=list)
    no_physical_command_generated: bool = True


class ActiveModelSemanticState(BaseModel):
    active_model_id: str | None = None
    package_id: str | None = None
    package_kind: Literal["none", "fixture", "test_adapter", "production"] = "none"
    adapter_mode: str = "none"
    model_format: str | None = None
    active_model_state: Literal["no_model", "test_adapter_active", "fixture_model_active", "production_model_active"] = "no_model"
    package_schema_validation: Literal["not_available", "passed", "failed"] = "not_available"
    runtime_validation: Literal["not_available", "passed", "failed"] = "not_available"
    production_readiness: Literal[
        "missing_model",
        "missing_class_mapping",
        "test_adapter_only",
        "production_model_loaded",
        "production_model_test_passed",
        "production_ready",
    ] = "missing_model"
    competition_readiness: Literal["blocked", "limited_demo_only", "rehearsal_ready"] = "blocked"
    package_schema_valid: bool = False
    runtime_valid: bool = False
    class_mapping_valid: bool = False
    production_model: bool = False
    production_ready: bool = False
    competition_ready: bool = False
    advisory_only: bool = True
    no_physical_command_generated: bool = True
    warnings: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)


class ModelPackageImportRequest(BaseModel):
    source_path: str | None = None
    package_name: str | None = None

    @model_validator(mode="after")
    def require_source(self) -> "ModelPackageImportRequest":
        if not self.source_path and not self.package_name:
            raise ValueError("source_path or package_name is required")
        return self


class ModelPackageActivateRequest(BaseModel):
    slot: Literal["body", "balloon", "combined"] = "combined"


class ModelPackageTestRequest(BaseModel):
    source: Literal["mock", "sample_input", "snapshot", "replay"] = "mock"
    frame_id: str = "model-package-test-frame"


class ModelPackageTestResult(BaseModel):
    model_id: str
    accepted: bool
    source: str
    detections: list[dict] = Field(default_factory=list)
    latency_ms: float = 0.0
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    evidence_kind: Literal["fixture_synthetic", "golden_inference", "not_executed"] = "not_executed"
    class_mapping_verified: bool = False
    advisory_only: bool = True
    no_physical_command_generated: bool = True
    timestamp: float = Field(default_factory=time.time)


class ModelPackageBenchmarkResult(BaseModel):
    model_id: str
    accepted: bool
    estimated_fps: float
    estimated_latency_ms: float
    device: str
    warnings: list[str] = Field(default_factory=list)
    advisory_only: bool = True
    no_physical_command_generated: bool = True
    timestamp: float = Field(default_factory=time.time)


class RecommendedSettingsApplyResult(BaseModel):
    accepted: bool
    applied: bool
    model_id: str
    recommended_settings: dict
    runtime_result: dict | None = None
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    no_physical_command_generated: bool = True
