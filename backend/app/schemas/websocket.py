from typing import Any

from pydantic import BaseModel, Field


class WebSocketEnvelope(BaseModel):
    type: str
    ts: float
    seq: int = Field(ge=0)
    payload: dict[str, Any]

