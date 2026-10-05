from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_runtime
from app.schemas.annotation import AnnotationRecord, AnnotationUpsertRequest, PredictionToAnnotationRequest
from app.schemas.dataset import DatasetExportRequest, DatasetExportResult, DatasetHealth, DatasetSplitRequest, DatasetValidationResult, FrameExtractRequest
from app.schemas.session import RecordEventRequest, SessionQualityRequest, SessionRecord, SnapshotResponse, StartSessionRequest
from app.services.runtime_state import RuntimeState

session_router = APIRouter(prefix="/api/sessions", tags=["sessions"])
dataset_router = APIRouter(prefix="/api/datasets", tags=["datasets"])
annotation_router = APIRouter(prefix="/api/annotations", tags=["annotations"])


@session_router.get("", response_model=list[SessionRecord])
def list_sessions(runtime: RuntimeState = Depends(get_runtime)) -> list[SessionRecord]:
    return runtime.sessions.list_sessions()


@session_router.post("/start", response_model=SessionRecord)
def start_session(request: StartSessionRequest, runtime: RuntimeState = Depends(get_runtime)) -> SessionRecord:
    return runtime.sessions.start(request)


@session_router.post("/stop", response_model=SessionRecord)
def stop_session(runtime: RuntimeState = Depends(get_runtime)) -> SessionRecord:
    try:
        return runtime.sessions.stop()
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@session_router.get("/{session_id}", response_model=SessionRecord)
def get_session(session_id: str, runtime: RuntimeState = Depends(get_runtime)) -> SessionRecord:
    try:
        return runtime.sessions.get_session(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="session not found") from exc


@session_router.delete("/{session_id}")
def delete_session(session_id: str, runtime: RuntimeState = Depends(get_runtime)) -> dict:
    try:
        return runtime.sessions.delete(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="session not found") from exc


@session_router.post("/{session_id}/snapshot", response_model=SnapshotResponse)
def snapshot_session(session_id: str, runtime: RuntimeState = Depends(get_runtime)) -> SnapshotResponse:
    try:
        return runtime.sessions.snapshot(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="session not found") from exc


@session_router.post("/{session_id}/record-event")
def record_session_event(session_id: str, request: RecordEventRequest, runtime: RuntimeState = Depends(get_runtime)) -> dict:
    try:
        return runtime.sessions.record_event(session_id, request)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="session not found") from exc


@session_router.get("/{session_id}/frames")
def session_frames(session_id: str, runtime: RuntimeState = Depends(get_runtime)) -> list[dict]:
    return runtime.sessions.frames(session_id)


@session_router.get("/{session_id}/detections")
def session_detections(session_id: str, runtime: RuntimeState = Depends(get_runtime)) -> list[dict]:
    return runtime.sessions.jsonl_records(session_id, "detections.jsonl")


@session_router.get("/{session_id}/annotations", response_model=list[AnnotationRecord])
def session_annotations(session_id: str, runtime: RuntimeState = Depends(get_runtime)) -> list[AnnotationRecord]:
    return runtime.annotations.list_annotations(session_id)


@session_router.post("/{session_id}/quality", response_model=SessionRecord)
def set_session_quality(session_id: str, request: SessionQualityRequest, runtime: RuntimeState = Depends(get_runtime)) -> SessionRecord:
    return runtime.sessions.set_quality(session_id, request)


@annotation_router.post("", response_model=AnnotationRecord)
def upsert_annotation(request: AnnotationUpsertRequest, runtime: RuntimeState = Depends(get_runtime)) -> AnnotationRecord:
    return runtime.annotations.upsert(request)


@annotation_router.post("/from-prediction", response_model=AnnotationRecord)
def prediction_to_annotation(request: PredictionToAnnotationRequest, runtime: RuntimeState = Depends(get_runtime)) -> AnnotationRecord:
    return runtime.annotations.prediction_to_annotation(request)


@dataset_router.get("")
def list_datasets(runtime: RuntimeState = Depends(get_runtime)) -> list[dict]:
    return runtime.dataset.list_datasets()


@dataset_router.post("/export-yolo", response_model=DatasetExportResult)
def export_yolo(request: DatasetExportRequest, runtime: RuntimeState = Depends(get_runtime)) -> DatasetExportResult:
    return runtime.dataset.export_yolo(request)


@dataset_router.get("/exports")
def list_exports(runtime: RuntimeState = Depends(get_runtime)) -> list[dict]:
    return runtime.dataset.list_exports()


@dataset_router.get("/health", response_model=DatasetHealth)
def dataset_health(runtime: RuntimeState = Depends(get_runtime)) -> DatasetHealth:
    return runtime.dataset.health()


@dataset_router.get("/{dataset_id}")
def get_dataset(dataset_id: str, runtime: RuntimeState = Depends(get_runtime)) -> dict:
    try:
        return runtime.dataset.get_dataset(dataset_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="dataset not found") from exc


@dataset_router.post("/validate", response_model=DatasetValidationResult)
def validate_dataset(request: DatasetExportRequest | None = None, runtime: RuntimeState = Depends(get_runtime)) -> DatasetValidationResult:
    return runtime.dataset.validate(request)


@dataset_router.post("/split")
def split_dataset(request: DatasetSplitRequest, runtime: RuntimeState = Depends(get_runtime)) -> dict:
    return runtime.dataset.split(request.dataset_id, request.train_val_split)


@dataset_router.post("/frame-extract")
def frame_extract(request: FrameExtractRequest, runtime: RuntimeState = Depends(get_runtime)) -> dict:
    return runtime.dataset.frame_extract(request.session_id, request.every_n_frames)
