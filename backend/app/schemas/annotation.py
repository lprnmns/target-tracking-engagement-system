import time
from typing import Literal

from pydantic import BaseModel, Field, model_validator


AnnotationSource = Literal["manual", "model_prediction", "imported_yolo", "replay_review"]
BBoxFormat = Literal["xyxy_pixel", "xywh_pixel", "yolo_normalized"]


class AnnotationObject(BaseModel):
    object_id: str
    class_name: str
    class_id: int = Field(ge=0)
    bbox_format: BBoxFormat = "yolo_normalized"
    bbox: list[float]
    confidence: float | None = Field(default=None, ge=0, le=1)
    track_id: str | None = None
    is_balloon: bool = False
    team_label: str | None = None
    color_decision: dict | None = None
    verified_by_operator: bool = False

    @model_validator(mode="after")
    def validate_bbox(self) -> "AnnotationObject":
        if len(self.bbox) != 4:
            raise ValueError("bbox must contain 4 values")
        if self.bbox_format == "yolo_normalized" and any(value < 0 or value > 1 for value in self.bbox):
            raise ValueError("yolo_normalized bbox values must be in range 0..1")
        return self


class AnnotationRecord(BaseModel):
    annotation_id: str
    session_id: str
    frame_id: str
    image_path: str
    source: AnnotationSource = "manual"
    objects: list[AnnotationObject] = Field(default_factory=list)
    updated_at: float = Field(default_factory=time.time)


class AnnotationUpsertRequest(BaseModel):
    annotation_id: str | None = None
    session_id: str
    frame_id: str
    image_path: str
    source: AnnotationSource = "manual"
    objects: list[AnnotationObject] = Field(default_factory=list)


class PredictionToAnnotationRequest(BaseModel):
    session_id: str
    frame_id: str
    image_path: str
    detections: list[dict] = Field(default_factory=list)
