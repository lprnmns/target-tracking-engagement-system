from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class LogLevel(StrEnum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARN = "WARN"
    ERROR = "ERROR"


class LogEvent(BaseModel):
    ts: float
    level: LogLevel
    subsystem: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)

