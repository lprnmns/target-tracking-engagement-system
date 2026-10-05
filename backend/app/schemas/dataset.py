import time
from typing import Literal

from pydantic import BaseModel, Field, model_validator


ExportMode = Literal["body_multiclass", "balloon_singleclass", "combined_body_balloon", "target_singleclass"]


DATASET_CLASS_MAPS: dict[str, dict[int, str]] = {
    "body_multiclass": {0: "f16", 1: "helicopter", 2: "ballistic_missile", 3: "mini_micro_uav"},
    "balloon_singleclass": {0: "balloon"},
    "combined_body_balloon": {0: "f16", 1: "helicopter", 2: "ballistic_missile", 3: "mini_micro_uav", 4: "balloon"},
    "target_singleclass": {0: "target"},
}


class DatasetExportRequest(BaseModel):
    dataset_name: str
    version: str = "v1"
    export_mode: ExportMode = "combined_body_balloon"
    train_val_split: float = Field(default=0.8, gt=0, lt=1)
    include_unverified_annotations: bool = False
    include_model_predictions: bool = False
    min_confidence: float | None = Field(default=None, ge=0, le=1)
    selected_sessions: list[str] = Field(default_factory=list)
    selected_target_types: list[str] = Field(default_factory=list)
    selected_distances: list[str] = Field(default_factory=list)
    selected_lens_profiles: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_name(self) -> "DatasetExportRequest":
        if "/" in self.dataset_name or "\\" in self.dataset_name:
            raise ValueError("dataset_name must not include path separators")
        if "/" in self.version or "\\" in self.version:
            raise ValueError("version must not include path separators")
        return self


class DatasetExportResult(BaseModel):
    dataset_id: str
    output_path: str
    data_yaml_path: str
    image_count: int
    label_count: int
    train_count: int
    val_count: int
    warnings: list[str] = Field(default_factory=list)
    no_physical_command_generated: bool = True


class DatasetValidationResult(BaseModel):
    valid: bool
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    checked_items: int = 0


class DatasetHealth(BaseModel):
    total_sessions: int
    total_images: int
    total_annotations: int
    class_distribution: dict[str, int] = Field(default_factory=dict)
    distance_distribution: dict[str, int] = Field(default_factory=dict)
    team_distribution: dict[str, int] = Field(default_factory=dict)
    lens_distribution: dict[str, int] = Field(default_factory=dict)
    model_distribution: dict[str, int] = Field(default_factory=dict)
    missing_metadata_warnings: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    updated_at: float = Field(default_factory=time.time)


class DatasetSplitRequest(BaseModel):
    dataset_id: str
    train_val_split: float = Field(default=0.8, gt=0, lt=1)


class FrameExtractRequest(BaseModel):
    session_id: str
    every_n_frames: int = Field(default=1, ge=1)
