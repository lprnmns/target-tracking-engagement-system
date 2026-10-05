import glob
import json
import time
from typing import Any

from app.schemas.config import AppConfig
from app.schemas.hardware import (
    HardwareCapabilities,
    HardwareCommandBlockResult,
    HardwareConnectReadOnlyRequest,
    HardwareConnectResult,
    HardwareConnectionState,
    HardwareRiskyCommandRequest,
    HardwareSerialPort,
    HardwareStatus,
    HardwareTelemetry,
    HardwareTelemetryParseResult,
    HardwareTransportMode,
)
from app.schemas.log import LogLevel
from app.services.log_service import JsonlLogService
from app.transports.mock_serial_transport import MockSerialTransport
from app.transports.pyserial_transport import PySerialTransport
from app.transports.serial_transport import SerialTransport

RISKY_COMMAND_TYPES = {
    "fire_request",
    "jog_motor",
    "set_motor_target",
    "set_servo_position",
    "set_servo",
    "enable_driver",
    "set_pin",
    "pwm_write",
    "step_pulse",
}


class HardwareDiscoveryService:
    def __init__(self, config: AppConfig, logger: JsonlLogService) -> None:
        self.config = config
        self.logger = logger
        self.transport: SerialTransport | None = None
        self.telemetry = HardwareTelemetry(
            transport_mode=HardwareTransportMode.MOCK,
            baudrate=config.serial.baudrate,
            last_error="hardware_discovery_disabled" if not config.hardware.hardware_discovery_enabled else None,
        )
        self.last_event: tuple[str, dict] | None = None
        self.warnings: list[str] = []

    def ports(self) -> list[HardwareSerialPort]:
        ports = self._list_pyserial_ports()
        if not ports:
            ports = self._list_glob_ports()
        self._log_event("hardware.port_scan", {"count": len(ports)}, "Hardware serial port scan")
        return ports

    def capabilities(self) -> HardwareCapabilities:
        return HardwareCapabilities(
            hardware_discovery_enabled=self.config.hardware.hardware_discovery_enabled,
            allow_real_serial_readonly=self.config.hardware.allow_real_serial_readonly,
            physical_command_enabled=self.config.hardware.physical_command_enabled,
            allow_physical_motion=self.config.hardware.allow_physical_motion,
            allow_physical_fire=self.config.hardware.allow_physical_fire,
        )

    def status(self, mock_pico_active: bool = True) -> HardwareStatus:
        telemetry = self._with_heartbeat_age(self.telemetry)
        warnings = list(dict.fromkeys(self.warnings[-10:]))
        if not self.config.hardware.hardware_discovery_enabled:
            warnings.append("Hardware discovery disabled by config.")
        if telemetry.connection_state == HardwareConnectionState.PORT_OPEN_NO_TELEMETRY:
            warnings.append("Serial port open but telemetry unavailable.")
        if telemetry.physical_outputs_enabled is True:
            warnings.append("Unexpected physical output enabled flag from firmware.")
        physical_pico = "disconnected"
        if telemetry.connection_state == HardwareConnectionState.PICO_READONLY_VERIFIED:
            physical_pico = "read-only verified"
        elif telemetry.connection_state == HardwareConnectionState.READONLY_CONNECTED_UNVERIFIED:
            physical_pico = "read-only unverified"
        elif telemetry.connection_state == HardwareConnectionState.PORT_OPEN_NO_TELEMETRY:
            physical_pico = "port open, no telemetry"
        elif telemetry.connection_state == HardwareConnectionState.MOCK_READONLY_CONNECTED:
            physical_pico = "mock read-only"
        return HardwareStatus(
            connection_state=telemetry.connection_state,
            mock_pico_active=mock_pico_active,
            physical_pico=physical_pico,
            transport_mode=telemetry.transport_mode,
            readonly=telemetry.transport_mode == HardwareTransportMode.REAL_READONLY,
            hardware_discovery_enabled=self.config.hardware.hardware_discovery_enabled,
            physical_command_enabled=False,
            telemetry_available=telemetry.telemetry_received,
            port_open=telemetry.port_open,
            telemetry_received=telemetry.telemetry_received,
            pico_verified=telemetry.pico_verified,
            telemetry_firmware_detected=telemetry.telemetry_firmware_detected,
            physical_commands_disabled=telemetry.physical_commands_disabled,
            transport_source="mock" if telemetry.connection_state == HardwareConnectionState.MOCK_READONLY_CONNECTED or telemetry.transport_mode == HardwareTransportMode.MOCK else "real_serial",
            telemetry=telemetry,
            warnings=warnings,
        )

    def connect_readonly(self, request: HardwareConnectReadOnlyRequest, mock_pico_active: bool = True) -> HardwareConnectResult:
        self._log_event("hardware.readonly_connect_attempt", request.model_dump(mode="json"), "Read-only hardware connect attempt")
        if not self.config.hardware.hardware_discovery_enabled:
            return HardwareConnectResult(accepted=False, reason="hardware_discovery_disabled", status=self.status(mock_pico_active))
        if not self.config.hardware.allow_real_serial_readonly:
            return HardwareConnectResult(accepted=False, reason="allow_real_serial_readonly_required", status=self.status(mock_pico_active))
        if self.config.hardware.physical_command_enabled:
            return HardwareConnectResult(accepted=False, reason="physical_commands_forbidden_in_phase12", status=self.status(mock_pico_active))

        self.disconnect(mock_pico_active)
        try:
            if request.port == "MOCK_READONLY":
                self.transport = MockSerialTransport()
            else:
                self.transport = PySerialTransport(request.port, request.baudrate)
            self.transport.open()
            connection_state = (
                HardwareConnectionState.MOCK_READONLY_CONNECTED
                if request.port == "MOCK_READONLY"
                else HardwareConnectionState.PORT_OPEN_NO_TELEMETRY
            )
            self.telemetry = self.telemetry.model_copy(
                update={
                    "connection_state": connection_state,
                    "transport_mode": HardwareTransportMode.REAL_READONLY,
                    "port": request.port,
                    "baudrate": request.baudrate,
                    "port_open": True,
                    "telemetry_received": False,
                    "pico_verified": False,
                    "telemetry_firmware_detected": False,
                    "physical_outputs_enabled": None,
                    "physical_commands_disabled": True,
                    "last_error": "telemetry_unavailable",
                    "updated_at": time.time(),
                    "no_physical_command_generated": True,
                }
            )
            self.poll_readonly()
            self._log_event("hardware.readonly_connected", self.telemetry.model_dump(mode="json"), "Read-only hardware connected")
            return HardwareConnectResult(accepted=True, reason="read_only_serial_opened", status=self.status(mock_pico_active))
        except Exception as exc:
            self.telemetry = self.telemetry.model_copy(
                update={
                    "connection_state": HardwareConnectionState.FAULT,
                    "transport_mode": HardwareTransportMode.REAL_READONLY,
                    "port": request.port,
                    "baudrate": request.baudrate,
                    "last_error": str(exc),
                    "updated_at": time.time(),
                }
            )
            self._log_event("hardware.error", self.telemetry.model_dump(mode="json"), "Read-only hardware connect failed", level=LogLevel.ERROR)
            return HardwareConnectResult(accepted=False, reason=f"connect_readonly_failed:{exc}", status=self.status(mock_pico_active))

    def disconnect(self, mock_pico_active: bool = True) -> HardwareConnectResult:
        if self.transport is not None:
            try:
                self.transport.close()
            except Exception as exc:
                self.warnings.append(f"disconnect_error:{exc}")
        self.transport = None
        self.telemetry = self.telemetry.model_copy(
            update={
                "connection_state": HardwareConnectionState.DISCONNECTED,
                "transport_mode": HardwareTransportMode.MOCK,
                "heartbeat_age_ms": None,
                "port_open": False,
                "telemetry_received": False,
                "pico_verified": False,
                "telemetry_firmware_detected": False,
                "physical_outputs_enabled": None,
                "physical_commands_disabled": True,
                "last_error": None,
                "updated_at": time.time(),
            }
        )
        self._log_event("hardware.readonly_disconnected", self.telemetry.model_dump(mode="json"), "Read-only hardware disconnected")
        return HardwareConnectResult(accepted=True, reason="read_only_disconnected", status=self.status(mock_pico_active))

    def poll_readonly(self) -> HardwareTelemetry:
        if self.transport is None or not self.transport.is_open:
            return self.telemetry
        for _ in range(10):
            raw = self.transport.readline()
            if not raw:
                break
            self.parse_line(raw.decode("utf-8", errors="replace").strip())
        return self.telemetry

    def parse_line(self, raw_line: str) -> HardwareTelemetryParseResult:
        if not raw_line:
            return HardwareTelemetryParseResult(accepted=False, event_type="hardware.warning", warning="empty_line", telemetry=self.telemetry)
        try:
            message = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            error = f"invalid_json:{exc.msg}"
            self._record_parse_error(error, raw_line)
            return HardwareTelemetryParseResult(accepted=False, event_type="hardware.error", warning=error, telemetry=self.telemetry)

        if not isinstance(message, dict):
            error = "invalid_message_shape"
            self._record_parse_error(error, raw_line)
            return HardwareTelemetryParseResult(accepted=False, event_type="hardware.error", warning=error, telemetry=self.telemetry)

        message_type = str(message.get("type", ""))
        if message_type not in {"telemetry", "heartbeat"}:
            warning = f"unknown_message_type:{message_type or 'missing'}"
            self.warnings.append(warning)
            self.telemetry = self.telemetry.model_copy(update={"last_raw_message": raw_line, "updated_at": time.time()})
            self._log_event("hardware.warning", {"warning": warning, "raw": raw_line}, "Unknown read-only hardware message", level=LogLevel.WARN)
            return HardwareTelemetryParseResult(
                accepted=False,
                event_type="hardware.warning",
                warning=warning,
                telemetry=self.telemetry,
                parsed_message=message,
            )

        expected_fields = {"firmware_version", "safe_state", "physical_outputs_enabled", "timestamp_ms", "limits", "estop_state", "driver_enabled", "pan_position_steps", "tilt_position_steps"}
        missing_fields = sorted(expected_fields.difference(message.keys()))
        if missing_fields and message_type == "telemetry":
            warning = f"telemetry_missing_fields:{','.join(missing_fields)}"
            self.warnings.append(warning)
            self._log_event("hardware.warning", {"warning": warning, "missing_fields": missing_fields}, "Telemetry message missing optional acceptance fields", level=LogLevel.WARN)

        limits = message.get("limits") if isinstance(message.get("limits"), dict) else {}
        device = str(message.get("device", self.telemetry.device) or "")
        firmware_version = str(message.get("firmware_version", self.telemetry.firmware_version) or "")
        physical_outputs_enabled = message.get("physical_outputs_enabled", self.telemetry.physical_outputs_enabled)
        telemetry_firmware_detected = firmware_version.startswith("telemetry-only")
        pico_verified = device.lower() == "pico2" and telemetry_firmware_detected and physical_outputs_enabled is False
        connection_state = (
            HardwareConnectionState.PICO_READONLY_VERIFIED
            if pico_verified
            else HardwareConnectionState.READONLY_CONNECTED_UNVERIFIED
        )
        def int_or_current(value: Any, current: int) -> int:
            try:
                return int(value)
            except (TypeError, ValueError):
                return current

        updates: dict[str, Any] = {
            "connection_state": connection_state,
            "transport_mode": HardwareTransportMode.REAL_READONLY,
            "device": device or None,
            "firmware_version": firmware_version or None,
            "estop_state": message.get("estop_state", self.telemetry.estop_state),
            "driver_enabled": bool(message.get("driver_enabled", self.telemetry.driver_enabled)),
            "pan_position_steps": int_or_current(message.get("pan_position_steps", self.telemetry.pan_position_steps), self.telemetry.pan_position_steps),
            "tilt_position_steps": int_or_current(message.get("tilt_position_steps", self.telemetry.tilt_position_steps), self.telemetry.tilt_position_steps),
            "pan_limit_left": bool(limits.get("pan_left", self.telemetry.pan_limit_left)),
            "pan_limit_right": bool(limits.get("pan_right", self.telemetry.pan_limit_right)),
            "tilt_limit_up": bool(limits.get("tilt_up", self.telemetry.tilt_limit_up)),
            "tilt_limit_down": bool(limits.get("tilt_down", self.telemetry.tilt_limit_down)),
            "safe_state": message.get("safe_state", self.telemetry.safe_state),
            "physical_outputs_enabled": physical_outputs_enabled,
            "telemetry_timestamp_ms": message.get("timestamp_ms", self.telemetry.telemetry_timestamp_ms),
            "port_open": True,
            "telemetry_received": True,
            "pico_verified": pico_verified,
            "telemetry_firmware_detected": telemetry_firmware_detected,
            "physical_commands_disabled": True,
            "last_raw_message": raw_line,
            "last_error": None,
            "updated_at": time.time(),
            "no_physical_command_generated": True,
        }
        self.telemetry = self.telemetry.model_copy(update=updates)
        self._log_event("hardware.telemetry_received", self.telemetry.model_dump(mode="json"), "Read-only telemetry received")
        if pico_verified:
            self._log_event("hardware.pico_verified", self.telemetry.model_dump(mode="json"), "Pico telemetry-only firmware verified")
        if physical_outputs_enabled is True:
            self.warnings.append("unexpected_physical_outputs_enabled")
            self._log_event("hardware.warning", self.telemetry.model_dump(mode="json"), "Unexpected physical output enabled flag from firmware", level=LogLevel.ERROR)
        return HardwareTelemetryParseResult(
            accepted=True,
            event_type="hardware.telemetry",
            telemetry=self.telemetry,
            parsed_message=message,
        )

    def block_risky_command(self, command_type: str | HardwareRiskyCommandRequest) -> HardwareCommandBlockResult:
        if isinstance(command_type, HardwareRiskyCommandRequest):
            command_value = command_type.command_type
        else:
            command_value = command_type
        result = HardwareCommandBlockResult(command_type=command_value)
        self._log_event("hardware.risky_command_blocked", result.model_dump(mode="json"), "Risky hardware command blocked", level=LogLevel.WARN)
        return result

    def _record_parse_error(self, error: str, raw_line: str) -> None:
        errors = [*self.telemetry.parse_errors, error][-20:]
        self.telemetry = self.telemetry.model_copy(
            update={"last_error": error, "last_raw_message": raw_line, "parse_errors": errors, "updated_at": time.time()}
        )
        self._log_event("hardware.error", {"error": error, "raw": raw_line}, "Read-only hardware parse error", level=LogLevel.WARN)

    def _with_heartbeat_age(self, telemetry: HardwareTelemetry) -> HardwareTelemetry:
        if telemetry.telemetry_received:
            return telemetry.model_copy(update={"heartbeat_age_ms": int((time.time() - telemetry.updated_at) * 1000)})
        return telemetry.model_copy(update={"heartbeat_age_ms": None})

    def _list_pyserial_ports(self) -> list[HardwareSerialPort]:
        try:
            from serial.tools import list_ports  # type: ignore[import-not-found]
        except ImportError:
            return []
        ports: list[HardwareSerialPort] = []
        for port in list_ports.comports():
            text = " ".join(str(item or "") for item in (port.device, port.description, port.hwid, port.manufacturer)).lower()
            is_candidate = any(token in text for token in ("pico", "rp2040", "rp2350", "raspberry pi", "2e8a"))
            ports.append(
                HardwareSerialPort(
                    device=port.device,
                    description=port.description or port.device,
                    hwid=port.hwid or "",
                    manufacturer=port.manufacturer,
                    is_candidate_pico=is_candidate,
                    warning=None if is_candidate else "Not recognized as Pico; use read-only only if verified.",
                )
            )
        return ports

    def _list_glob_ports(self) -> list[HardwareSerialPort]:
        devices = sorted(set(glob.glob("/dev/ttyACM*") + glob.glob("/dev/ttyUSB*") + glob.glob("/dev/serial/by-id/*") + glob.glob("/dev/cu.*")))
        ports = []
        for device in devices:
            is_candidate = "ttyACM" in device or "pico" in device.lower()
            ports.append(
                HardwareSerialPort(
                    device=device,
                    description=device,
                    hwid="glob-detected",
                    is_candidate_pico=is_candidate,
                    warning=None if is_candidate else "Serial device is not identified as Pico.",
                )
            )
        return ports

    def _log_event(self, event_type: str, payload: dict, message: str, level: LogLevel = LogLevel.INFO) -> None:
        payload = {**payload, "no_physical_command_generated": True}
        self.last_event = (event_type, payload)
        self.logger.emit(level, "HARDWARE", message, payload)
