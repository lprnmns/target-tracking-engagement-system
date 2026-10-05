from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from app.api.deps import get_runtime
from app.schemas.engagement_evidence import EngagementEvidenceManifest, EngagementEvidenceRecordList, EngagementEvidenceStatus, EngagementEvidenceSummary
from app.schemas.digital_twin import DigitalTwinReplaySummary
from app.services.runtime_state import RuntimeState

router = APIRouter(prefix="/api/engagement-evidence", tags=["engagement-evidence"])


@router.get("/status", response_model=EngagementEvidenceStatus)
def evidence_status(runtime: RuntimeState = Depends(get_runtime)) -> EngagementEvidenceStatus:
    return runtime.engagement_evidence.status()


@router.get("/latest", response_model=EngagementEvidenceSummary)
def evidence_latest(runtime: RuntimeState = Depends(get_runtime)) -> EngagementEvidenceSummary:
    status = runtime.engagement_evidence.status()
    if status.active is not None:
        return status.active
    if status.recent:
        return status.recent[0]
    raise HTTPException(status_code=404, detail="engagement_evidence_not_found")


@router.get("/records", response_model=EngagementEvidenceRecordList)
def evidence_records(limit: int = 50, runtime: RuntimeState = Depends(get_runtime)) -> EngagementEvidenceRecordList:
    return EngagementEvidenceRecordList(records=runtime.engagement_evidence.records(limit=min(max(limit, 1), 200)))


@router.get("/records/{engagement_id}", response_model=EngagementEvidenceManifest)
def evidence_manifest(engagement_id: str, runtime: RuntimeState = Depends(get_runtime)) -> EngagementEvidenceManifest:
    try:
        return runtime.engagement_evidence.manifest(engagement_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="engagement_evidence_not_found") from exc


@router.get("/records/{engagement_id}/digital-twin-replay", response_model=DigitalTwinReplaySummary)
def evidence_digital_twin_replay(engagement_id: str, runtime: RuntimeState = Depends(get_runtime)) -> DigitalTwinReplaySummary:
    try:
        return runtime.engagement_evidence.digital_twin_replay(engagement_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="engagement_evidence_not_found") from exc


@router.get("/records/{engagement_id}/media/{filename}")
def evidence_media(engagement_id: str, filename: str, runtime: RuntimeState = Depends(get_runtime)) -> FileResponse:
    try:
        path = runtime.engagement_evidence.media_path(engagement_id, filename)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="engagement_evidence_media_not_found") from exc
    media_type = "video/mp4" if filename.endswith(".mp4") else "application/json"
    return FileResponse(path, media_type=media_type, filename=filename)
