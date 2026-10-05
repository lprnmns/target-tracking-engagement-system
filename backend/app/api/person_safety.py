from fastapi import APIRouter, Depends

from app.api.deps import get_runtime
from app.schemas.person_safety import PersonSafetyGateStatus
from app.services.runtime_state import RuntimeState

router = APIRouter(prefix="/api/person-safety", tags=["person-safety"])


@router.get("/status", response_model=PersonSafetyGateStatus)
def get_person_safety_status(runtime: RuntimeState = Depends(get_runtime)) -> PersonSafetyGateStatus:
    return runtime.person_safety.status()
