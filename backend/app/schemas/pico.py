from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, Field


class PicoConnectionStatus(StrEnum):
    DISCONNECTED = "DISCONNECTED"
    MOCK_CONNECTED = "MOCK_CONNECTED"
    CONNECTED = "CONNECTED"


class EstopState(StrEnum):
    UNKNOWN = "UNKNOWN"
    RELEASED = "RELEASED"
    ACTIVE = "ACTIVE"


class PinFunction(StrEnum):
    PAN_STEP = "PAN_STEP"
    PAN_DIR = "PAN_DIR"
    TILT_STEP = "TILT_STEP"
    TILT_DIR = "TILT_DIR"
    TRIGGER_SERVO_PWM = "TRIGGER_SERVO_PWM"
    ESTOP_IN = "ESTOP_IN"
    LIMIT_LEFT = "LIMIT_LEFT"
    LIMIT_RIGHT = "LIMIT_RIGHT"
    LIMIT_UP = "LIMIT_UP"
    LIMIT_DOWN = "LIMIT_DOWN"
    DRIVER_ENABLE = "DRIVER_ENABLE"
    UART_TX = "UART_TX"
    UART_RX = "UART_RX"
    UNUSED = "UNUSED"


class PinDirection(StrEnum):
    IN = "IN"
    OUT = "OUT"
    BIDIRECTIONAL = "BIDIRECTIONAL"
    UNUSED = "UNUSED"


class PinMode(StrEnum):
    GPIO = "GPIO"
    PWM = "PWM"
    UART = "UART"
    UNUSED = "UNUSED"


class PinValidationLevel(StrEnum):
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class PicoTelemetry(BaseModel):
    connection_status: PicoConnectionStatus = PicoConnectionStatus.DISCONNECTED
    port: str | None = None
    baudrate: Annotated[int, Field(gt=0)] = 115200
    heartbeat_age_ms: Annotated[int, Field(ge=0)] | None = None
    firmware_version: str = "mock-pico-0.1"
    estop_state: EstopState = EstopState.UNKNOWN
    driver_enabled: bool = False
    pan_position_steps: int = 0
    tilt_position_steps: int = 0
    pan_limit_left: bool = False
    pan_limit_right: bool = False
    tilt_limit_up: bool = False
    tilt_limit_down: bool = False
    last_error: str | None = "mock_pico_not_connected"
    updated_at: float


class PicoStatus(BaseModel):
    mock_mode: bool = True
    telemetry: PicoTelemetry
    reason: str
    blocking_reasons: list[str]


class PicoPort(BaseModel):
    device: str
    label: str
    mock: bool = False


class PicoConnectRequest(BaseModel):
    port: str
    baudrate: Annotated[int, Field(gt=0)] = 115200


class PicoConnectionEvent(BaseModel):
    connection_status: PicoConnectionStatus
    port: str | None
    baudrate: int
    reason: str


class PicoDiscoveryPort(BaseModel):
    port: str
    description: str = "not_available"
    hwid: str | None = None
    vid: str | None = None
    pid: str | None = None
    serial_number: str | None = None
    manufacturer: str | None = None
    is_candidate: bool = False
    physical_command_enabled: bool = False
    no_physical_command_generated: bool = True


class PicoDiscoveryPortsResponse(BaseModel):
    ports: list[PicoDiscoveryPort]
    candidates_count: int = 0
    physical_command_enabled: bool = False
    no_physical_command_generated: bool = True


class PicoReadOnlyConnectRequest(BaseModel):
    port: str
    baudrate: Annotated[int, Field(gt=0)] = 115200
    read_only: bool = True


class PicoReadOnlyStatus(BaseModel):
    connected: bool = False
    selected_port: str | None = None
    baudrate: int = 115200
    rx_only: bool = True
    tx_disabled: bool = True
    serial_write_enabled: bool = False
    command_tx_enabled: bool = False
    last_seen_at: float | None = None
    heartbeat_seen: bool = False
    firmware_version: str | None = None
    telemetry_frames: int = 0
    parse_errors: int = 0
    dtr_rts_reset_risk: str = "reported_not_used_for_commands"
    warnings: list[str] = Field(default_factory=list)
    physical_command_enabled: bool = False
    no_physical_command_generated: bool = True


class PicoReadOnlyTelemetry(BaseModel):
    raw_line_sample: str | None = None
    parsed: dict = Field(default_factory=dict)
    heartbeat: bool = False
    firmware_version: str | None = None
    estop_state: str | None = None
    limit_states: dict = Field(default_factory=dict)
    motor_driver_state: dict = Field(default_factory=dict)
    warning_fault_state: dict = Field(default_factory=dict)
    no_command_generated: bool = True
    serial_write_enabled: bool = False
    command_tx_enabled: bool = False
    physical_command_enabled: bool = False
    no_physical_command_generated: bool = True


class PicoReadOnlyEvidence(BaseModel):
    evidence_id: str
    status: str
    created_at: float
    status_snapshot: PicoReadOnlyStatus
    latest_telemetry: PicoReadOnlyTelemetry
    port_inventory: list[PicoDiscoveryPort] = Field(default_factory=list)
    advisory_only: bool = True
    serial_write_enabled: bool = False
    command_tx_enabled: bool = False
    physical_command_enabled: bool = False
    no_physical_command_generated: bool = True


class PicoPermissionDiagnosis(BaseModel):
    port: str | None = None
    status: str = "not_available"
    blocker_class: str = "device_missing"
    user: str = "unknown"
    groups: list[str] = Field(default_factory=list)
    user_in_dialout: bool = False
    device_exists: bool = False
    device_mode: str | None = None
    device_owner: str | None = None
    device_group: str | None = None
    id_output: str = ""
    groups_output: str = ""
    ls_output: str = ""
    udevadm_output: str = ""
    dmesg_output: str = ""
    manual_recommendations: list[str] = Field(default_factory=list)
    serial_write_enabled: bool = False
    command_tx_enabled: bool = False
    physical_command_enabled: bool = False
    no_physical_command_generated: bool = True


class PicoProtocolLimitState(BaseModel):
    pan_left: bool = False
    pan_right: bool = False
    tilt_up: bool = False
    tilt_down: bool = False


class PicoProtocolFaultState(BaseModel):
    active: bool = False
    code: str | None = None
    message: str | None = None


class PicoProtocolTelemetry(BaseModel):
    protocol_name: str = "ISTIKLAL Serial Packet Protocol"
    protocol_version: int = 1
    pico_connected: bool = False
    telemetry_fresh: bool = False
    telemetry_missing: bool = True
    port: str | None = None
    last_heartbeat_age_ms: int | None = None
    last_packet_type: str | None = None
    last_packet_seq_id: int | None = None
    pan_deg: float | None = None
    tilt_deg: float | None = None
    x_steps: int | None = None
    y_steps: int | None = None
    driver_enabled: bool = False
    limit_state: PicoProtocolLimitState = Field(default_factory=PicoProtocolLimitState)
    fault_state: PicoProtocolFaultState = Field(default_factory=PicoProtocolFaultState)
    pose_source: str = "tracker_estimate"
    packet_parse_status: str = "no_packet"
    crc_status: str = "not_checked"
    physical_tx_disabled: bool = True
    serial_tx_enabled: bool = False
    physical_command_enabled: bool = False
    no_physical_command_generated: bool = True
    updated_at: float | None = None


class PicoProtocolPort(BaseModel):
    port: str
    description: str = "not_available"
    hwid: str | None = None
    is_candidate: bool = False
    no_physical_command_generated: bool = True


class PicoProtocolStatus(BaseModel):
    protocol_name: str = "ISTIKLAL Serial Packet Protocol"
    protocol_version: int = 1
    selected_port: str | None = None
    baudrate: int = 115200
    pico_connected: bool = False
    telemetry_fresh: bool = False
    telemetry_missing: bool = True
    latest_telemetry: PicoProtocolTelemetry = Field(default_factory=PicoProtocolTelemetry)
    discovered_ports: list[PicoProtocolPort] = Field(default_factory=list)
    packet_parse_status: str = "no_packet"
    crc_status: str = "not_checked"
    parse_errors: int = 0
    crc_failures: int = 0
    serial_tx_enabled: bool = False
    physical_tx_disabled: bool = True
    physical_command_enabled: bool = False
    no_physical_command_generated: bool = True


class PicoProtocolReadSampleRequest(BaseModel):
    sample_hex: str | None = None
    sample_base64: str | None = None
    sample_text: str | None = None


class PicoProtocolReadSampleResult(BaseModel):
    accepted: bool = True
    packets_parsed: int = 0
    errors: list[str] = Field(default_factory=list)
    remainder_len: int = 0
    latest_telemetry: PicoProtocolTelemetry = Field(default_factory=PicoProtocolTelemetry)
    physical_tx_disabled: bool = True
    physical_command_enabled: bool = False
    no_physical_command_generated: bool = True


class PinAssignment(BaseModel):
    pin_name: str
    physical_pin: Annotated[int, Field(gt=0)]
    function: PinFunction = PinFunction.UNUSED
    direction: PinDirection = PinDirection.UNUSED
    mode: PinMode = PinMode.UNUSED
    pwm_capable: bool = False
    uart_capable: bool = False
    note: str | None = None


class PinProfile(BaseModel):
    profile_name: str
    note: str
    final_approved: bool = False
    pins: list[PinAssignment]


class PinValidationIssue(BaseModel):
    level: PinValidationLevel
    code: str
    message: str
    pin_name: str | None = None
    function: PinFunction | None = None


class PinValidationResult(BaseModel):
    valid: bool
    can_apply: bool
    system_mode: str
    system_armed: bool
    issues: list[PinValidationIssue] = Field(default_factory=list)
