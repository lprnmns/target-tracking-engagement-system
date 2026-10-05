from fastapi import APIRouter, Depends

from app.api.deps import get_runtime
from app.schemas.system import SystemState
from app.services.runtime_state import RuntimeState

router = APIRouter(prefix="/api/system", tags=["system"])


@router.get("/state", response_model=SystemState)
def get_system_state(runtime: RuntimeState = Depends(get_runtime)) -> SystemState:
    return runtime.system_state()

