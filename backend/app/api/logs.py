import json
import time
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.api.deps import get_runtime
from app.schemas.log import LogLevel
from app.services.runtime_state import RuntimeState
from app.services.storage_paths import project_root

router = APIRouter(prefix="/api/logs", tags=["logs"])


class ClientLogsExportRequest(BaseModel):
    events: list[dict[str, Any]] = Field(default_factory=list)
    source: str = "logs_ui"


class ClientLogsExportResponse(BaseModel):
    accepted: bool
    path: str
    count: int
    no_physical_command_generated: bool = True


@router.post("/export-client-events", response_model=ClientLogsExportResponse)
def export_client_events(
    request: ClientLogsExportRequest,
    runtime: RuntimeState = Depends(get_runtime),
) -> ClientLogsExportResponse:
    output_dir = project_root() / "exports" / "logs"
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"client_events_{time.strftime('%Y%m%d_%H%M%S')}.jsonl"
    with path.open("w", encoding="utf-8") as handle:
        for event in request.events:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")
    response = ClientLogsExportResponse(accepted=True, path=str(path), count=len(request.events))
    runtime.logger.emit(
        LogLevel.INFO,
        "LOGS",
        "Client logs exported to JSONL",
        response.model_dump(mode="json"),
    )
    return response
