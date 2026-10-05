import json
import time
import uuid

from app.schemas.annotation import AnnotationObject, AnnotationRecord, AnnotationUpsertRequest, PredictionToAnnotationRequest
from app.schemas.log import LogLevel
from app.services.log_service import JsonlLogService
from app.services.session_service import SessionService


class AnnotationService:
    def __init__(self, sessions: SessionService, logger: JsonlLogService) -> None:
        self.sessions = sessions
        self.logger = logger
        self.last_event: tuple[str, dict] | None = None

    def list_annotations(self, session_id: str) -> list[AnnotationRecord]:
        records = []
        for raw in self.sessions.jsonl_records(session_id, "annotations.jsonl"):
            records.append(AnnotationRecord.model_validate(raw))
        return records

    def upsert(self, request: AnnotationUpsertRequest) -> AnnotationRecord:
        annotation = AnnotationRecord(
            annotation_id=request.annotation_id or f"ann-{uuid.uuid4().hex[:10]}",
            session_id=request.session_id,
            frame_id=request.frame_id,
            image_path=request.image_path,
            source=request.source,
            objects=request.objects,
            updated_at=time.time(),
        )
        path = self.sessions.root / request.session_id / "annotations.jsonl"
        existing = [item for item in self.list_annotations(request.session_id) if item.annotation_id != annotation.annotation_id]
        existing.append(annotation)
        path.write_text(
            "\n".join(json.dumps(item.model_dump(mode="json"), ensure_ascii=False) for item in existing) + "\n",
            encoding="utf-8",
        )
        session = self.sessions.get_session(request.session_id)
        stats = session.stats.model_copy(update={"annotation_count": len(existing)})
        self.sessions._write_session(session.model_copy(update={"stats": stats}))
        self._event("annotation.updated", annotation.model_dump(mode="json"), "Annotation updated")
        return annotation

    def prediction_to_annotation(self, request: PredictionToAnnotationRequest) -> AnnotationRecord:
        objects = []
        for index, detection in enumerate(request.detections):
            bbox = detection.get("bbox_yolo_normalized") or [0.5, 0.5, 0.2, 0.2]
            objects.append(
                AnnotationObject(
                    object_id=f"obj-{index + 1}",
                    class_name=detection.get("class_name", "target"),
                    class_id=int(detection.get("class_id", 0)),
                    bbox_format="yolo_normalized",
                    bbox=bbox,
                    confidence=detection.get("confidence"),
                    is_balloon=bool(detection.get("is_balloon", False)),
                    verified_by_operator=False,
                )
            )
        return self.upsert(
            AnnotationUpsertRequest(
                session_id=request.session_id,
                frame_id=request.frame_id,
                image_path=request.image_path,
                source="model_prediction",
                objects=objects,
            )
        )

    def _event(self, event_type: str, payload: dict, message: str) -> None:
        self.last_event = (event_type, payload)
        self.logger.emit(LogLevel.INFO, "ANNOTATION", message, payload)
