from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_runtime
from app.schemas.data_lab import (
    DataLabAnnotationCandidate,
    DataLabAnnotationReviewRequest,
    DataLabDatasetHealth,
    DataLabExportResponse,
    DataLabRecordResponse,
    DataLabReplayResult,
    DataLabSessionSummary,
    DataLabStatus,
)
from app.services.runtime_state import RuntimeState

router = APIRouter(prefix="/api/data-lab", tags=["data-lab"])


@router.get("/status", response_model=DataLabStatus)
def status(runtime: RuntimeState = Depends(get_runtime)) -> DataLabStatus:
    return runtime.data_lab.status(runtime)


@router.get("/sessions", response_model=list[DataLabSessionSummary])
def sessions(runtime: RuntimeState = Depends(get_runtime)) -> list[DataLabSessionSummary]:
    return runtime.data_lab.list_sessions()


@router.get("/sessions/latest", response_model=DataLabSessionSummary | None)
def latest_session(runtime: RuntimeState = Depends(get_runtime)) -> DataLabSessionSummary | None:
    return runtime.data_lab.latest_session()


@router.post("/sessions/record-latest", response_model=DataLabRecordResponse)
def record_latest(runtime: RuntimeState = Depends(get_runtime)) -> DataLabRecordResponse:
    try:
        return runtime.data_lab.record_latest_detection(runtime)
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/detection-events-sample")
def detection_events_sample(runtime: RuntimeState = Depends(get_runtime)) -> list[dict]:
    return runtime.data_lab.detection_events_sample()


@router.post("/export", response_model=DataLabExportResponse)
def export(runtime: RuntimeState = Depends(get_runtime)) -> DataLabExportResponse:
    return runtime.data_lab.export_evidence(runtime)


@router.get("/replay/status", response_model=DataLabReplayResult)
def replay_status(runtime: RuntimeState = Depends(get_runtime)) -> DataLabReplayResult:
    return runtime.data_lab.replay_status()


@router.post("/replay/run", response_model=DataLabReplayResult)
def replay_run(session_id: str | None = None, runtime: RuntimeState = Depends(get_runtime)) -> DataLabReplayResult:
    return runtime.data_lab.run_replay(session_id)


@router.get("/replay/latest", response_model=DataLabReplayResult)
def replay_latest(runtime: RuntimeState = Depends(get_runtime)) -> DataLabReplayResult:
    return runtime.data_lab.replay_status()


@router.get("/annotations/candidates", response_model=list[DataLabAnnotationCandidate])
def annotation_candidates(runtime: RuntimeState = Depends(get_runtime)) -> list[DataLabAnnotationCandidate]:
    return runtime.data_lab.annotation_candidates()


@router.post("/annotations/review", response_model=DataLabAnnotationCandidate)
def annotation_review(request: DataLabAnnotationReviewRequest, runtime: RuntimeState = Depends(get_runtime)) -> DataLabAnnotationCandidate:
    try:
        return runtime.data_lab.review_annotation(request)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="annotation candidate not found") from exc


@router.get("/dataset-health", response_model=DataLabDatasetHealth)
def dataset_health(runtime: RuntimeState = Depends(get_runtime)) -> DataLabDatasetHealth:
    return runtime.data_lab.dataset_health()
