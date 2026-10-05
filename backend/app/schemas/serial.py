from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class SerialConnectionState(StrEnum):
    DISCONNECTED = "DISCONNECTED"
    MOCK_CONNECTED = "MOCK_CONNECTED"
    PORT_OPEN_NO_TELEMETRY = "PORT_OPEN_NO_TELEMETRY"
    READONLY_CONNECTED_UNVERIFIED = "READONLY_CONNECTED_UNVERIFIED"
    PICO_READONLY_VERIFIED = "PICO_READONLY_VERIFIED"
    MOCK_READONLY_CONNECTED = "MOCK_READONLY_CONNECTED"
    FAULT = "FAULT"


class SerialDirection(StrEnum):
    TX = "tx"
    RX = "rx"
    SYSTEM = "system"


class SerialLogKind(StrEnum):
    TX = "tx"
    RX = "rx"
    ACK = "ack"
    NACK = "nack"
    ERROR = "error"
    TIMEOUT = "timeout"
    STATUS = "status"


class MagazineState(StrEnum):
    READY = "READY"
    LOW = "LOW"
    EMPTY = "EMPTY"
    INVALID = "INVALID"


class SerialStatus(BaseModel):
    connection_state: SerialConnectionState
    transport_mode: str
    transport_source: str = "mock"
    protocol_mode: str
    real_serial_enabled: bool
    real_serial_readonly: bool = True
    readonly: bool = False
    telemetry_received: bool = False
    pico_verified: bool = False
    transport_healthy: bool = False
    consecutive_ping_failures: int = 0
    transport_reconnect_count: int = 0
    last_protocol_nack: str | None = None
    last_query_timeout: str | None = None
    last_transport_fault: str | None = None
    physical_command_enabled: bool = False
    last_tx: dict[str, Any] | None = None
    last_rx: dict[str, Any] | None = None
    pending_ack_count: int = 0
    command_queue_depth: int = 0
    last_command_age_ms: int | None = None
    last_command_kind: str | None = None
    last_command_raw: str | None = None
    last_command_ack_state: str = "unknown"
    last_command_rtt_ms: int | None = None
    last_command_error: str | None = None
    magazine_capacity: int = 8
    magazine_remaining: int = 8
    magazine_empty: bool = False
    magazine_state: MagazineState = MagazineState.READY
    acknowledged_shot_count: int = 0
    magazine_reload_count: int = 0
    magazine_updated_at: float | None = None
    heartbeat_age_ms: int | None = None
    ack_timeout_ms: int
    heartbeat_timeout_ms: int
    last_error: str | None = None


class SerialLogEntry(BaseModel):
    id: int
    ts: float
    direction: SerialDirection
    kind: SerialLogKind
    message: dict[str, Any]
    raw: str | None = None
    error: str | None = None


class SerialSendJsonRequest(BaseModel):
    message: dict[str, Any]


class SerialSimulateRxRequest(BaseModel):
    message: dict[str, Any]


class SerialCommandResult(BaseModel):
    accepted: bool
    reason: str
    status: SerialStatus
    log_entry: SerialLogEntry | None = None
    no_physical_command_generated: bool = True
