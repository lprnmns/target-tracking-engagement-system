from fastapi import APIRouter, Depends

from app.api.deps import get_runtime
from app.schemas.system import HealthResponse
from app.services.runtime_state import RuntimeState

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health", response_model=HealthResponse)
def health(runtime: RuntimeState = Depends(get_runtime)) -> HealthResponse:
    return HealthResponse(ok=True, version="0.1.0", uptime_s=runtime.uptime_s())

