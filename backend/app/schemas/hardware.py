import time
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, model_validator


class HardwareTransportMode(StrEnum):
    MOCK = "mock"
    REAL_READONLY = "real_readonly"
    REAL_COMMAND_DISABLED = "real_command_disabled"
    REAL_COMMAND = "real_command"


class HardwareConnectionState(StrEnum):
    DISCONNECTED = "DISCONNECTED"
    PORT_OPEN_NO_TELEMETRY = "PORT_OPEN_NO_TELEMETRY"
    READONLY_CONNECTED_UNVERIFIED = "READONLY_CONNECTED_UNVERIFIED"
    PICO_READONLY_VERIFIED = "PICO_READONLY_VERIFIED"
    PICO_VERIFIED = "PICO_VERIFIED"
    MOCK_READONLY_CONNECTED = "MOCK_READONLY_CONNECTED"
    FAULT = "FAULT"


class HardwareSerialPort(BaseModel):
    device: str
    description: str
    hwid: str
    manufacturer: str | None = None
    is_candidate_pico: bool = False
    warning: str | None = None


class HardwareCapabilities(BaseModel):
    hardware_discovery_enabled: bool
    allow_real_serial_readonly: bool
    physical_command_enabled: bool = False
    allow_physical_motion: bool = False
    allow_physical_fire: bool = False
    supported_transport_modes: list[str] = Field(default_factory=lambda: ["mock", "real_readonly"])
    risky_command_blocker_enabled: bool = True
    no_physical_command_generated: bool = True


class HardwareTelemetry(BaseModel):
    connection_state: HardwareConnectionState = HardwareConnectionState.DISCONNECTED
    transport_mode: HardwareTransportMode = HardwareTransportMode.MOCK
    port: str | None = None
    baudrate: int = 115200
    heartbeat_age_ms: int | None = None
    device: str | None = None
    firmware_version: str | None = None
    estop_state: bool | None = None
    driver_enabled: bool = False
    pan_position_steps: int = 0
    tilt_position_steps: int = 0
    pan_limit_left: bool = False
    pan_limit_right: bool = False
    tilt_limit_up: bool = False
    tilt_limit_down: bool = False
    safe_state: bool | None = None
    physical_outputs_enabled: bool | None = None
    telemetry_timestamp_ms: int | None = None
    port_open: bool = False
    telemetry_received: bool = False
    pico_verified: bool = False
    telemetry_firmware_detected: bool = False
    physical_commands_disabled: bool = True
    last_raw_message: str | None = None
    last_error: str | None = None
    parse_errors: list[str] = Field(default_factory=list)
    updated_at: float = Field(default_factory=time.time)
    no_physical_command_generated: bool = True


class HardwareStatus(BaseModel):
    connection_state: HardwareConnectionState
    mock_pico_active: bool
    physical_pico: str
    transport_mode: HardwareTransportMode
    readonly: bool
    hardware_discovery_enabled: bool
    physical_command_enabled: bool
    telemetry_available: bool
    port_open: bool = False
    telemetry_received: bool = False
    pico_verified: bool = False
    telemetry_firmware_detected: bool = False
    physical_commands_disabled: bool = True
    transport_source: str = "mock"
    telemetry: HardwareTelemetry
    warnings: list[str] = Field(default_factory=list)
    no_physical_command_generated: bool = True


class HardwareConnectReadOnlyRequest(BaseModel):
    port: str
    baudrate: int = 115200


class HardwareCommandBlockResult(BaseModel):
    accepted: bool = False
    reason: str = "physical_commands_disabled_in_phase12_readonly"
    command_type: str
    no_physical_command_generated: bool = True


class HardwareRiskyCommandRequest(BaseModel):
    command_type: str


class HardwareConnectResult(BaseModel):
    accepted: bool
    reason: str
    status: HardwareStatus
    no_physical_command_generated: bool = True


class HardwareTelemetryParseResult(BaseModel):
    accepted: bool
    event_type: str
    warning: str | None = None
    telemetry: HardwareTelemetry
    parsed_message: dict[str, Any] | None = None

class HardwareTestJogRequest(BaseModel):
    speed_x: int
    speed_y: int
    duration_ms: int = 500

class HardwareServoTuneRequest(BaseModel):
    release_deg: int = Field(default=35, ge=0, le=180)
    fire_deg: int = Field(default=175, ge=0, le=180)
    pulse_s: float = Field(default=1.0, ge=0.1, le=5.0)


class HardwarePicoDiscoveryResult(BaseModel):
    found: bool
    port: str | None = None
    baudrate: int = 460800
    reason_code: str
    detail: str


class HardwareHomeResult(BaseModel):
    accepted: bool
    command: str = "HOME"
    phase: str = "IDLE"
    homed: bool = False
    reason_codes: list[str] = Field(default_factory=list)
    detail: str = ""
    pan_deg: float | None = None
    tilt_deg: float | None = None
    pan_steps: int | None = None
    tilt_steps: int | None = None
    limit_x_active: bool = False
    limit_y_active: bool = False
    limit_reason_code: str | None = None


class HardwareMotionEnvelope(BaseModel):
    pan_min_deg: float = Field(default=0.0, ge=0.0, le=270.0)
    pan_max_deg: float = Field(default=270.0, ge=0.0, le=270.0)
    tilt_min_deg: float = Field(default=0.0, ge=0.0, le=60.0)
    tilt_max_deg: float = Field(default=60.0, ge=0.0, le=60.0)

    @model_validator(mode="after")
    def validate_envelope(self) -> "HardwareMotionEnvelope":
        if self.pan_min_deg >= self.pan_max_deg:
            raise ValueError("pan_min_deg must be < pan_max_deg")
        if self.tilt_min_deg >= self.tilt_max_deg:
            raise ValueError("tilt_min_deg must be < tilt_max_deg")
        return self


class HardwareMotionEnvelopeResult(BaseModel):
    accepted: bool
    envelope: HardwareMotionEnvelope
    reason_codes: list[str] = Field(default_factory=list)
    detail: str = ""

class HardwareTestCommandResult(BaseModel):
    accepted: bool
    message: str
    command: str | None = None
    command_sent: bool = False
    pico_response: str | None = None
    driver_ack: str | None = None
    safe_stop_response: str | None = None
    reason_codes: list[str] = Field(default_factory=list)
