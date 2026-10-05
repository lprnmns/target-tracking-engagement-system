from fastapi import APIRouter, Depends

from app.api.deps import get_runtime
from app.schemas.decision import DecisionState
from app.services.runtime_state import RuntimeState

router = APIRouter(prefix="/api/decision", tags=["decision"])


@router.get("/state", response_model=DecisionState)
def get_decision_state(runtime: RuntimeState = Depends(get_runtime)) -> DecisionState:
    return runtime.decision_engine.evaluate(runtime)
