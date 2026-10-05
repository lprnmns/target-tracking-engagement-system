from __future__ import annotations

import glob
import base64
import json
import os
import stat
import subprocess
import time
import uuid
from pathlib import Path

try:  # POSIX-only account/group database
    import grp
    import pwd
except ImportError:  # pragma: no cover - Windows host
    grp = None
    pwd = None

from app.schemas.config import AppConfig
from app.schemas.log import LogLevel
from app.schemas.pico import (
    EstopState,
    PicoConnectRequest,
    PicoConnectionEvent,
    PicoConnectionStatus,
    PicoDiscoveryPort,
    PicoDiscoveryPortsResponse,
    PicoPort,
    PicoReadOnlyConnectRequest,
    PicoReadOnlyEvidence,
    PicoPermissionDiagnosis,
    PicoReadOnlyStatus,
    PicoReadOnlyTelemetry,
    PicoProtocolFaultState,
    PicoProtocolLimitState,
    PicoProtocolPort,
    PicoProtocolReadSampleRequest,
    PicoProtocolReadSampleResult,
    PicoProtocolStatus,
    PicoProtocolTelemetry,
    PicoStatus,
    PicoTelemetry,
    PinAssignment,
    PinDirection,
    PinFunction,
    PinMode,
    PinProfile,
    PinValidationIssue,
    PinValidationLevel,
    PinValidationResult,
)
from app.protocols.istiklal_serial_v1 import MessageType, decode_stream, message_type_name
from app.schemas.system import SystemState
from app.services.log_service import JsonlLogService
from app.services.storage_paths import project_root

try:  # pragma: no cover - host dependent
    import serial
    from serial.tools import list_ports
except Exception:  # pragma: no cover
    serial = None
    list_ports = None

CRITICAL_UNIQUE_FUNCTIONS = {
    PinFunction.PAN_STEP,
    PinFunction.PAN_DIR,
    PinFunction.TILT_STEP,
    PinFunction.TILT_DIR,
    PinFunction.TRIGGER_SERVO_PWM,
    PinFunction.ESTOP_IN,
    PinFunction.LIMIT_LEFT,
    PinFunction.LIMIT_RIGHT,
    PinFunction.LIMIT_UP,
    PinFunction.LIMIT_DOWN,
    PinFunction.DRIVER_ENABLE,
    PinFunction.UART_TX,
    PinFunction.UART_RX,
}

REQUIRED_MOTION_FUNCTIONS = {
    PinFunction.PAN_STEP,
    PinFunction.PAN_DIR,
    PinFunction.TILT_STEP,
    PinFunction.TILT_DIR,
}

INPUT_FUNCTIONS = {
    PinFunction.ESTOP_IN,
    PinFunction.LIMIT_LEFT,
    PinFunction.LIMIT_RIGHT,
    PinFunction.LIMIT_UP,
    PinFunction.LIMIT_DOWN,
}

OUTPUT_FUNCTIONS = {
    PinFunction.PAN_STEP,
    PinFunction.PAN_DIR,
    PinFunction.TILT_STEP,
    PinFunction.TILT_DIR,
    PinFunction.DRIVER_ENABLE,
}

CONFIG_TO_FUNCTION = {
    "pan_step": PinFunction.PAN_STEP,
    "pan_dir": PinFunction.PAN_DIR,
    "tilt_step": PinFunction.TILT_STEP,
    "tilt_dir": PinFunction.TILT_DIR,
    "trigger_servo_pwm": PinFunction.TRIGGER_SERVO_PWM,
    "estop_in": PinFunction.ESTOP_IN,
    "pan_limit_left": PinFunction.LIMIT_LEFT,
    "pan_limit_right": PinFunction.LIMIT_RIGHT,
    "tilt_limit_up": PinFunction.LIMIT_UP,
    "tilt_limit_down": PinFunction.LIMIT_DOWN,
    "driver_enable": PinFunction.DRIVER_ENABLE,
}

PHYSICAL_PINS = {
    "GP0": 1,
    "GP1": 2,
    "GP2": 4,
    "GP3": 5,
    "GP4": 6,
    "GP5": 7,
    "GP6": 9,
    "GP7": 10,
    "GP8": 11,
    "GP9": 12,
    "GP10": 14,
    "GP11": 15,
    "GP12": 16,
    "GP13": 17,
    "GP14": 19,
    "GP15": 20,
    "GP16": 21,
    "GP17": 22,
    "GP18": 24,
    "GP19": 25,
    "GP20": 26,
    "GP21": 27,
    "GP22": 29,
    "GP26": 31,
    "GP27": 32,
    "GP28": 34,
}


class PicoService:
    def __init__(self, config: AppConfig, logger: JsonlLogService) -> None:
        self.config = config
        self.logger = logger
        self.connection_status = PicoConnectionStatus.DISCONNECTED
        self.port: str | None = config.pico.port
        self.baudrate = config.pico.baudrate
        self.last_heartbeat_at: float | None = None
        self.last_connection_event = PicoConnectionEvent(
            connection_status=self.connection_status,
            port=self.port,
            baudrate=self.baudrate,
            reason="Mock Pico starts disconnected; no serial command is produced.",
        )
        self.last_validation = PinValidationResult(
            valid=False,
            can_apply=False,
            system_mode="DISARMED",
            system_armed=False,
            issues=[
                PinValidationIssue(
                    level=PinValidationLevel.INFO,
                    code="NOT_VALIDATED",
                    message="Pin profile has not been validated yet.",
                )
            ],
        )
        self.pin_profile = self._profile_from_config(config)
        self._readonly_handle = None
        self._readonly_status = PicoReadOnlyStatus(baudrate=config.pico.baudrate)
        self._latest_readonly_telemetry = PicoReadOnlyTelemetry()
        self._latest_readonly_evidence: PicoReadOnlyEvidence | None = None
        self._readonly_ports: list[PicoDiscoveryPort] = []
        self._protocol_buffer = b""
        self._protocol_parse_errors = 0
        self._protocol_crc_failures = 0
        self._protocol_handle = None
        self._protocol_latest_telemetry = PicoProtocolTelemetry(
            port=config.serial.port or config.pico.port,
            serial_tx_enabled=bool(config.serial.serial_tx_enabled),
            physical_command_enabled=bool(config.hardware.physical_command_enabled),
            no_physical_command_generated=True,
        )
        self._readonly_root = project_root() / "exports" / "pico_readonly"
        self._readonly_root.mkdir(parents=True, exist_ok=True)

    def telemetry(self) -> PicoTelemetry:
        now = time.time()
        heartbeat_age_ms = None
        if self.last_heartbeat_at is not None:
            heartbeat_age_ms = int((now - self.last_heartbeat_at) * 1000)
        return PicoTelemetry(
            connection_status=self.connection_status,
            port=self.port,
            baudrate=self.baudrate,
            heartbeat_age_ms=heartbeat_age_ms,
            firmware_version="mock-pico-0.1",
            estop_state=EstopState.UNKNOWN,
            driver_enabled=False,
            pan_position_steps=0,
            tilt_position_steps=0,
            pan_limit_left=False,
            pan_limit_right=False,
            tilt_limit_up=False,
            tilt_limit_down=False,
            last_error=None if self.connection_status == PicoConnectionStatus.MOCK_CONNECTED else "mock_pico_disconnected",
            updated_at=now,
        )

    def status(self) -> PicoStatus:
        return PicoStatus(
            mock_mode=True,
            telemetry=self.telemetry(),
            reason="Mock Pico service is active. No physical serial command is produced.",
            blocking_reasons=["mock_mode", "hardware_disabled"],
        )

    def ports(self) -> list[PicoPort]:
        devices = sorted(
            set(
                glob.glob("/dev/ttyACM*")
                + glob.glob("/dev/ttyUSB*")
                + glob.glob("/dev/serial/by-id/*")
                + glob.glob("/dev/cu.*")
            )
        )
        ports = [PicoPort(device=device, label=device, mock=False) for device in devices]
        ports.insert(0, PicoPort(device="MOCK_PICO", label="Mock Pico 2 (dry-run)", mock=True))
        return ports

    def readonly_ports(self) -> PicoDiscoveryPortsResponse:
        ports: list[PicoDiscoveryPort] = []
        if list_ports is not None:
            for item in list_ports.comports():
                vid = f"{item.vid:04x}" if item.vid is not None else None
                pid = f"{item.pid:04x}" if item.pid is not None else None
                text = " ".join(str(value or "") for value in [item.device, item.description, item.hwid, item.manufacturer]).lower()
                ports.append(
                    PicoDiscoveryPort(
                        port=item.device,
                        description=item.description or item.device,
                        hwid=item.hwid,
                        vid=vid,
                        pid=pid,
                        serial_number=item.serial_number,
                        manufacturer=item.manufacturer,
                        is_candidate=any(token in text for token in ("pico", "rp2040", "rp2350", "arduino", "ttyacm", "ttyusb")),
                        physical_command_enabled=False,
                        no_physical_command_generated=True,
                    )
                )
        known = {item.port for item in ports}
        for device in sorted(set(glob.glob("/dev/ttyACM*") + glob.glob("/dev/ttyUSB*") + glob.glob("/dev/serial/by-id/*"))):
            if device in known:
                continue
            ports.append(
                PicoDiscoveryPort(
                    port=device,
                    description=device,
                    is_candidate=any(token in device.lower() for token in ("ttyacm", "ttyusb", "pico", "arduino")),
                    physical_command_enabled=False,
                    no_physical_command_generated=True,
                )
            )
        self._readonly_ports = ports
        response = PicoDiscoveryPortsResponse(
            ports=ports,
            candidates_count=sum(1 for item in ports if item.is_candidate),
            physical_command_enabled=False,
            no_physical_command_generated=True,
        )
        summary = f"Pico read-only ports discovered; ports={len(ports)}; no_physical_command_generated=true."
        self._readonly_event("pico.readonly_ports_discovered", {**response.model_dump(mode="json"), "summary": summary}, summary)
        self._readonly_event("pico.real_port_discovered", {**response.model_dump(mode="json"), "summary": summary}, summary)
        return response

    def readonly_connect(self, request: PicoReadOnlyConnectRequest) -> PicoReadOnlyStatus:
        warnings: list[str] = ["DTR/RTS reset behavior may reboot microcontroller on some adapters; no command bytes are sent."]
        self.readonly_disconnect(emit=False)
        self._readonly_status = PicoReadOnlyStatus(
            connected=False,
            selected_port=request.port,
            baudrate=request.baudrate,
            rx_only=True,
            tx_disabled=True,
            serial_write_enabled=False,
            command_tx_enabled=False,
            warnings=warnings,
            physical_command_enabled=False,
            no_physical_command_generated=True,
        )
        if not request.read_only:
            self._readonly_status.warnings.append("read_only flag must remain true; connection kept closed.")
            return self._readonly_status
        if serial is None:
            self._readonly_status.warnings.append("pyserial_not_available")
        else:
            try:
                self._readonly_handle = serial.Serial(
                    port=request.port,
                    baudrate=request.baudrate,
                    timeout=0,
                    write_timeout=0,
                    rtscts=False,
                    dsrdtr=False,
                    xonxoff=False,
                )
                for method in ("setDTR", "setRTS"):
                    try:
                        getattr(self._readonly_handle, method)(False)
                    except Exception:
                        pass
                try:
                    self._readonly_handle.reset_input_buffer()
                except Exception:
                    pass
                self._readonly_status.connected = True
            except Exception as exc:  # pragma: no cover - host dependent
                self._readonly_handle = None
                self._readonly_status.warnings.append(f"readonly_open_failed:{exc}")
        self._poll_readonly()
        summary = f"Pico read-only connection evaluated; connected={self._readonly_status.connected}; no_physical_command_generated=true."
        self._readonly_event("pico.readonly_connected", {**self._readonly_status.model_dump(mode="json"), "summary": summary}, summary)
        self._readonly_event("pico.real_rxonly_connected", {**self._readonly_status.model_dump(mode="json"), "summary": summary}, summary)
        return self._readonly_status

    def readonly_disconnect(self, emit: bool = True) -> PicoReadOnlyStatus:
        if self._readonly_handle is not None:
            try:
                self._readonly_handle.close()
            except Exception:
                pass
        self._readonly_handle = None
        self._readonly_status.connected = False
        if emit:
            summary = "Pico read-only disconnected; no_physical_command_generated=true."
            self._readonly_event("pico.readonly_disconnected", {**self._readonly_status.model_dump(mode="json"), "summary": summary}, summary)
            self._readonly_event("pico.real_rxonly_disconnected", {**self._readonly_status.model_dump(mode="json"), "summary": summary}, summary)
        return self._readonly_status

    def readonly_status(self) -> PicoReadOnlyStatus:
        self._poll_readonly()
        summary = f"Pico read-only status checked; connected={self._readonly_status.connected}; no_physical_command_generated=true."
        self._readonly_event("pico.readonly_status_checked", {**self._readonly_status.model_dump(mode="json"), "summary": summary}, summary)
        return self._readonly_status

    def readonly_latest_telemetry(self) -> PicoReadOnlyTelemetry:
        self._poll_readonly()
        return self._latest_readonly_telemetry

    def readonly_capture_evidence(self) -> PicoReadOnlyEvidence:
        self._poll_readonly()
        status = "recorded" if self._readonly_status.connected and self._readonly_status.telemetry_frames > 0 else "not_available"
        evidence = PicoReadOnlyEvidence(
            evidence_id=f"pico_readonly_evidence_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}",
            status=status,
            created_at=time.time(),
            status_snapshot=self._readonly_status,
            latest_telemetry=self._latest_readonly_telemetry,
            port_inventory=self._readonly_ports or self.readonly_ports().ports,
            advisory_only=True,
            serial_write_enabled=False,
            command_tx_enabled=False,
            physical_command_enabled=False,
            no_physical_command_generated=True,
        )
        self._latest_readonly_evidence = evidence
        path = self._readonly_root / "pico_readonly_latest_evidence.json"
        path.write_text(json.dumps(evidence.model_dump(mode="json"), indent=2), encoding="utf-8")
        summary = f"Pico read-only evidence recorded; status={status}; no_physical_command_generated=true."
        self._readonly_event("pico.readonly_evidence_recorded", {**evidence.model_dump(mode="json"), "summary": summary}, summary)
        self._readonly_event("pico.real_rxonly_evidence_captured", {**evidence.model_dump(mode="json"), "summary": summary}, summary)
        return evidence

    def readonly_latest_evidence(self) -> PicoReadOnlyEvidence:
        if self._latest_readonly_evidence is None:
            return PicoReadOnlyEvidence(
                evidence_id="none",
                status="not_recorded",
                created_at=time.time(),
                status_snapshot=self._readonly_status,
                latest_telemetry=self._latest_readonly_telemetry,
                port_inventory=self._readonly_ports,
                advisory_only=True,
                serial_write_enabled=False,
                command_tx_enabled=False,
                physical_command_enabled=False,
                no_physical_command_generated=True,
            )
        return self._latest_readonly_evidence

    def readonly_permission_status(self, port: str | None = None) -> PicoPermissionDiagnosis:
        selected = port or self._readonly_status.selected_port
        if selected is None:
            ports = self.readonly_ports().ports
            selected = next((item.port for item in ports if item.is_candidate), ports[0].port if ports else None)
        if os.name == "nt":
            available_ports = {item.device for item in list_ports.comports()} if list_ports is not None else set()
            device_exists = bool(selected and selected in available_ports)
            serial_busy = bool(
                self._readonly_status.warnings
                and any("busy" in warning.lower() or "access is denied" in warning.lower() for warning in self._readonly_status.warnings)
            )
            if not selected or not device_exists:
                blocker = "device_missing"
                status = "not_available"
            elif serial_busy:
                blocker = "serial_busy"
                status = "partial"
            else:
                blocker = "none"
                status = "passed"
            diagnosis = PicoPermissionDiagnosis(
                port=selected,
                status=status,
                blocker_class=blocker,
                user=os.environ.get("USERNAME") or os.environ.get("USER") or "windows_user",
                groups=[],
                user_in_dialout=False,
                device_exists=device_exists,
                device_mode="Windows COM device" if device_exists else None,
                device_owner=None,
                device_group=None,
                id_output="Windows does not use POSIX uid/dialout permissions.",
                groups_output="",
                ls_output=f"Detected serial ports: {', '.join(sorted(available_ports))}",
                udevadm_output="Not applicable on Windows.",
                dmesg_output="Not applicable on Windows.",
                manual_recommendations=[
                    "Close any application that already owns the selected COM port.",
                    "Confirm the Pico is present in Windows Device Manager under Ports (COM & LPT).",
                ],
                serial_write_enabled=False,
                command_tx_enabled=False,
                physical_command_enabled=False,
                no_physical_command_generated=True,
            )
            summary = f"Pico read-only Windows device status checked; blocker={blocker}; no_physical_command_generated=true."
            self._readonly_event("pico.readonly_permission_checked", {**diagnosis.model_dump(mode="json"), "summary": summary}, summary)
            return diagnosis

        id_output = self._run_text(["id"])
        groups_output = self._run_text(["groups"])
        groups = groups_output.strip().split()
        user = pwd.getpwuid(os.getuid()).pw_name if pwd is not None else os.environ.get("USER", "unknown")
        device_exists = bool(selected and Path(selected).exists())
        ls_output = self._run_text(["bash", "-lc", f"ls -l {selected}"], enabled=bool(selected))
        udevadm_output = self._run_text(["bash", "-lc", f"udevadm info -a -n {selected} | head -80 || true"], enabled=bool(selected))
        dmesg_output = self._run_text(["bash", "-lc", 'dmesg | grep -iE "ttyACM|cdc_acm|usb" | tail -80 || true'])
        device_mode = None
        device_owner = None
        device_group = None
        can_read = False
        can_write = False
        if selected and device_exists:
            try:
                info = Path(selected).stat()
                device_mode = stat.filemode(info.st_mode)
                device_owner = pwd.getpwuid(info.st_uid).pw_name if pwd is not None else None
                device_group = grp.getgrgid(info.st_gid).gr_name if grp is not None else None
                can_read = os.access(selected, os.R_OK)
                can_write = os.access(selected, os.W_OK)
            except Exception:
                pass
        if not selected or not device_exists:
            blocker = "device_missing"
            status = "not_available"
        elif not can_read or not can_write:
            blocker = "device_permission_denied"
            status = "partial"
        elif self._readonly_status.warnings and any("busy" in warning.lower() for warning in self._readonly_status.warnings):
            blocker = "serial_busy"
            status = "partial"
        else:
            blocker = "none"
            status = "passed"
        if "dialout" not in groups and blocker == "device_permission_denied":
            blocker = "user_not_in_dialout"
        recommendations = [
            "sudo usermod -aG dialout $USER",
            "Group change requires logout/login or reboot before it affects new sessions.",
            "Temporary manual test only, not permanent: sudo chmod a+rw /dev/ttyACM0",
            "Do not run this software with sudo as a workaround for acceptance evidence.",
        ]
        diagnosis = PicoPermissionDiagnosis(
            port=selected,
            status=status,
            blocker_class=blocker,
            user=user,
            groups=groups,
            user_in_dialout="dialout" in groups,
            device_exists=device_exists,
            device_mode=device_mode,
            device_owner=device_owner,
            device_group=device_group,
            id_output=id_output,
            groups_output=groups_output,
            ls_output=ls_output,
            udevadm_output=udevadm_output,
            dmesg_output=dmesg_output,
            manual_recommendations=recommendations,
            serial_write_enabled=False,
            command_tx_enabled=False,
            physical_command_enabled=False,
            no_physical_command_generated=True,
        )
        summary = f"Pico read-only permission status checked; blocker={blocker}; no_physical_command_generated=true."
        self._readonly_event("pico.readonly_permission_checked", {**diagnosis.model_dump(mode="json"), "summary": summary}, summary)
        return diagnosis

    def readonly_status_json(self) -> str:
        return json.dumps(self.readonly_status().model_dump(mode="json"), indent=2)

    def readonly_permission_status_json(self) -> str:
        return json.dumps(self.readonly_permission_status().model_dump(mode="json"), indent=2)

    def readonly_permission_acceptance_json(self) -> str:
        diagnosis = self.readonly_permission_status()
        status = self.readonly_status()
        acceptance = "passed" if diagnosis.status == "passed" else diagnosis.status
        payload = {
            "acceptance": acceptance,
            "permission_status": diagnosis.status,
            "blocker_class": diagnosis.blocker_class,
            "selected_port": diagnosis.port,
            "rx_only": status.rx_only,
            "tx_disabled": status.tx_disabled,
            "serial_write_enabled": status.serial_write_enabled,
            "command_tx_enabled": status.command_tx_enabled,
            "physical_command_enabled": False,
            "manual_recommendations": diagnosis.manual_recommendations,
            "dtr_rts_reset_risk": "Opening CDC ACM serial ports may toggle DTR/RTS and reset some microcontrollers; no firmware reset command is sent.",
            "no_physical_command_generated": True,
        }
        return json.dumps(payload, indent=2)

    def readonly_latest_telemetry_json(self) -> str:
        return json.dumps(self.readonly_latest_telemetry().model_dump(mode="json"), indent=2)

    def readonly_port_inventory_json(self) -> str:
        return json.dumps(self.readonly_ports().model_dump(mode="json"), indent=2)

    def readonly_evidence_summary_markdown(self) -> str:
        evidence = self.readonly_latest_evidence()
        return f"""# Pico Read-only Evidence Summary

- Evidence ID: {evidence.evidence_id}
- Status: {evidence.status}
- Connected: {evidence.status_snapshot.connected}
- Selected port: {evidence.status_snapshot.selected_port or 'not_available'}
- RX only: {evidence.status_snapshot.rx_only}
- TX disabled: {evidence.status_snapshot.tx_disabled}
- Firmware version: {evidence.status_snapshot.firmware_version or 'not_available'}
- Telemetry frames: {evidence.status_snapshot.telemetry_frames}
- Parse errors: {evidence.status_snapshot.parse_errors}
- physical_command_enabled=false
- no_physical_command_generated=true

This evidence layer only reads telemetry that the device publishes by itself. It does not send serial write, motor jog, STEP/DIR/PWM/GPIO, TMC current, hardware enable or fire/trigger commands.
"""

    def readonly_safety_boundary_markdown(self) -> str:
        return """# Pico Read-only Safety Boundary

- serial write: disabled
- Pico command TX: disabled
- motor jog: disabled
- STEP/DIR/PWM/GPIO output: disabled
- TMC current write: disabled
- hardware enable: disabled
- fire/trigger/shoot: disabled
- physical_command_enabled=false
- no_physical_command_generated=true
- Safety invariant: DISARMED + NO_FIRE + dry_run=true + hardware_enabled=false + physical_command_enabled=false
"""

    def protocol_status(self) -> PicoProtocolStatus:
        self._poll_protocol_rx()
        ports = self._protocol_ports()
        latest = self.protocol_latest_telemetry()
        status = PicoProtocolStatus(
            selected_port=self.config.serial.port or self.config.pico.port,
            baudrate=self.config.serial.baudrate,
            pico_connected=latest.pico_connected,
            telemetry_fresh=latest.telemetry_fresh,
            telemetry_missing=latest.telemetry_missing,
            latest_telemetry=latest,
            discovered_ports=ports,
            packet_parse_status=latest.packet_parse_status,
            crc_status=latest.crc_status,
            parse_errors=self._protocol_parse_errors,
            crc_failures=self._protocol_crc_failures,
            serial_tx_enabled=bool(self.config.serial.serial_tx_enabled),
            physical_tx_disabled=not bool(self.config.serial.serial_tx_enabled),
            physical_command_enabled=bool(self.config.hardware.physical_command_enabled),
            no_physical_command_generated=True,
        )
        self._protocol_event(
            "pico.protocol_contract_loaded",
            {**status.model_dump(mode="json"), "summary": "Pico protocol v1 contract/status loaded; no_physical_command_generated=true."},
        )
        return status

    def protocol_latest_telemetry(self) -> PicoProtocolTelemetry:
        self._poll_protocol_rx()
        latest = self._protocol_latest_telemetry
        if latest.updated_at is None:
            latest = latest.model_copy(update={
                "telemetry_missing": True,
                "telemetry_fresh": False,
                "pose_source": "tracker_estimate",
                "packet_parse_status": latest.packet_parse_status or "no_packet",
                "crc_status": latest.crc_status or "not_checked",
                "physical_tx_disabled": not bool(self.config.serial.serial_tx_enabled),
                "serial_tx_enabled": bool(self.config.serial.serial_tx_enabled),
                "physical_command_enabled": bool(self.config.hardware.physical_command_enabled),
                "no_physical_command_generated": True,
            })
            self._protocol_latest_telemetry = latest
        return latest

    def protocol_contract(self) -> dict:
        return _protocol_contract_payload(
            serial_tx_enabled=bool(self.config.serial.serial_tx_enabled),
            physical_command_enabled=bool(self.config.hardware.physical_command_enabled),
        )

    def protocol_read_sample(self, request: PicoProtocolReadSampleRequest) -> PicoProtocolReadSampleResult:
        sample = self._sample_bytes(request)
        errors: list[str] = []
        packets_parsed = 0
        if sample:
            result = decode_stream(sample)
            errors.extend(result.errors)
            packets_parsed = len(result.packets)
            self._protocol_parse_errors += len([item for item in result.errors if item != "crc_mismatch"])
            self._protocol_crc_failures += len([item for item in result.errors if item == "crc_mismatch"])
            for error in result.errors:
                if error == "crc_mismatch":
                    self._protocol_event("pico.protocol_crc_failed", {"summary": "Pico protocol CRC failed; no_physical_command_generated=true.", "error": error})
            for packet in result.packets:
                self._ingest_protocol_packet(packet.msg_type, packet.seq_id, packet.timestamp_ms, packet.payload)
        else:
            self._poll_protocol_rx()
        latest = self.protocol_latest_telemetry()
        return PicoProtocolReadSampleResult(
            accepted=True,
            packets_parsed=packets_parsed,
            errors=errors,
            remainder_len=0,
            latest_telemetry=latest,
            physical_tx_disabled=True,
            physical_command_enabled=bool(self.config.hardware.physical_command_enabled),
            no_physical_command_generated=True,
        )

    def protocol_status_json(self) -> str:
        return json.dumps(self.protocol_status().model_dump(mode="json"), indent=2)

    def protocol_latest_telemetry_json(self) -> str:
        return json.dumps(self.protocol_latest_telemetry().model_dump(mode="json"), indent=2)

    def protocol_contract_json(self) -> str:
        return json.dumps(self.protocol_contract(), indent=2)

    def protocol_safety_boundary_markdown(self) -> str:
        return """# ISTIKLAL Serial Protocol v1 Safety Boundary

- Protocol mode: telemetry/read-only first
- Serial TX enabled: false by default
- Legacy physical commands disabled: SPD, LZR, STP
- Motor movement commands: not implemented in Phase 36
- Fire/trigger/servo commands: not implemented in Phase 36
- GPIO/PWM/STEP/DIR/hardware-enable paths: not implemented in Phase 36
- physical_command_enabled=false
- no_physical_command_generated=true
"""

    def _protocol_ports(self) -> list[PicoProtocolPort]:
        discovered = self.readonly_ports().ports
        ports = [
            PicoProtocolPort(
                port=item.port,
                description=item.description,
                hwid=item.hwid,
                is_candidate=item.is_candidate,
                no_physical_command_generated=True,
            )
            for item in discovered
        ]
        for item in ports:
            self._protocol_event(
                "pico.protocol_port_discovered",
                {**item.model_dump(mode="json"), "summary": "Pico protocol port discovered; no_physical_command_generated=true."},
            )
        return ports

    def _poll_protocol_rx(self) -> None:
        # Phase 36 does not auto-open writable serial. RX polling is only
        # attempted for an already configured read-only transport.
        if not (self.config.serial.transport_mode == "real_readonly" and self.config.serial.real_serial_readonly):
            return
        if serial is None:
            return
        port = self.config.serial.port or self.config.pico.port
        if not port:
            return
        if self._protocol_handle is None:
            try:
                self._protocol_handle = serial.Serial(
                    port=port,
                    baudrate=self.config.serial.baudrate,
                    timeout=0,
                    write_timeout=0,
                    rtscts=False,
                    dsrdtr=False,
                    xonxoff=False,
                )
                for method in ("setDTR", "setRTS"):
                    try:
                        getattr(self._protocol_handle, method)(False)
                    except Exception:
                        pass
            except Exception:
                self._protocol_handle = None
                return
        try:
            waiting = int(getattr(self._protocol_handle, "in_waiting", 0))
            chunk = bytes(self._protocol_handle.read(min(max(waiting, 0), 4096))) if waiting > 0 else b""
        except Exception:
            chunk = b""
        if not chunk:
            return
        result = decode_stream(self._protocol_buffer + chunk)
        self._protocol_buffer = result.remainder
        self._protocol_parse_errors += len([item for item in result.errors if item != "crc_mismatch"])
        self._protocol_crc_failures += len([item for item in result.errors if item == "crc_mismatch"])
        for error in result.errors:
            if error == "crc_mismatch":
                self._protocol_event("pico.protocol_crc_failed", {"summary": "Pico protocol CRC failed; no_physical_command_generated=true.", "error": error})
        for packet in result.packets:
            self._ingest_protocol_packet(packet.msg_type, packet.seq_id, packet.timestamp_ms, packet.payload)

    def _ingest_protocol_packet(self, msg_type: int, seq_id: int, timestamp_ms: int, payload: bytes) -> None:
        decoded = self._decode_protocol_payload(payload)
        now = time.time()
        packet_type = message_type_name(msg_type)
        previous = self._protocol_latest_telemetry
        heartbeat = msg_type == MessageType.HEARTBEAT or packet_type == "HEARTBEAT"
        telemetry_like = msg_type in {
            int(MessageType.HELLO),
            int(MessageType.HEARTBEAT),
            int(MessageType.TELEMETRY),
            int(MessageType.DRIVER_STATE),
            int(MessageType.LIMIT_STATE),
            int(MessageType.FAULT),
            int(MessageType.CONFIG_REPORT),
        }
        limit_payload = decoded.get("limit_state") if isinstance(decoded.get("limit_state"), dict) else decoded.get("limits")
        if not isinstance(limit_payload, dict):
            limit_payload = {}
        fault_payload = decoded.get("fault_state") if isinstance(decoded.get("fault_state"), dict) else {}
        if not fault_payload and msg_type == MessageType.FAULT:
            fault_payload = decoded
        fault_active = bool(fault_payload.get("active") or decoded.get("fault") or msg_type == MessageType.FAULT)
        latest = previous.model_copy(update={
            "pico_connected": telemetry_like,
            "telemetry_fresh": telemetry_like,
            "telemetry_missing": not telemetry_like,
            "port": self.config.serial.port or self.config.pico.port,
            "last_heartbeat_age_ms": 0 if heartbeat else previous.last_heartbeat_age_ms,
            "last_packet_type": packet_type,
            "last_packet_seq_id": seq_id,
            "pan_deg": _float_or_none(decoded.get("pan_deg"), previous.pan_deg),
            "tilt_deg": _float_or_none(decoded.get("tilt_deg"), previous.tilt_deg),
            "x_steps": _int_or_none(decoded.get("x_steps") if "x_steps" in decoded else decoded.get("pan_steps"), previous.x_steps),
            "y_steps": _int_or_none(decoded.get("y_steps") if "y_steps" in decoded else decoded.get("tilt_steps"), previous.y_steps),
            "driver_enabled": bool(decoded.get("driver_enabled", previous.driver_enabled)),
            "limit_state": PicoProtocolLimitState(
                pan_left=bool(limit_payload.get("pan_left") or limit_payload.get("pan_limit_left")),
                pan_right=bool(limit_payload.get("pan_right") or limit_payload.get("pan_limit_right")),
                tilt_up=bool(limit_payload.get("tilt_up") or limit_payload.get("tilt_limit_up")),
                tilt_down=bool(limit_payload.get("tilt_down") or limit_payload.get("tilt_limit_down")),
            ),
            "fault_state": PicoProtocolFaultState(
                active=fault_active,
                code=str(fault_payload.get("code")) if fault_payload.get("code") is not None else None,
                message=str(fault_payload.get("message")) if fault_payload.get("message") is not None else None,
            ),
            "pose_source": "telemetry" if telemetry_like and ("pan_deg" in decoded or "x_steps" in decoded or "pan_steps" in decoded) else previous.pose_source,
            "packet_parse_status": "parsed",
            "crc_status": "passed",
            "physical_tx_disabled": True,
            "serial_tx_enabled": bool(self.config.serial.serial_tx_enabled),
            "physical_command_enabled": bool(self.config.hardware.physical_command_enabled),
            "no_physical_command_generated": True,
            "updated_at": now,
        })
        self._protocol_latest_telemetry = latest
        self._protocol_event(
            "pico.protocol_telemetry_parsed",
            {**latest.model_dump(mode="json"), "summary": "Pico protocol telemetry parsed; no_physical_command_generated=true."},
        )
        if latest.fault_state.active:
            self._protocol_event(
                "pico.protocol_fault_reported",
                {**latest.fault_state.model_dump(mode="json"), "summary": "Pico protocol fault reported; no_physical_command_generated=true."},
            )

    @staticmethod
    def _decode_protocol_payload(payload: bytes) -> dict:
        if not payload:
            return {}
        try:
            loaded = json.loads(payload.decode("utf-8"))
            return loaded if isinstance(loaded, dict) else {"value": loaded}
        except Exception:
            return {"raw_hex": payload.hex()}

    def _sample_bytes(self, request: PicoProtocolReadSampleRequest) -> bytes:
        if request.sample_hex:
            return bytes.fromhex(request.sample_hex.replace(" ", ""))
        if request.sample_base64:
            return base64.b64decode(request.sample_base64)
        if request.sample_text:
            return request.sample_text.encode("utf-8")
        return b""

    def _protocol_event(self, event_type: str, payload: dict) -> None:
        safe_payload = {
            **payload,
            "type": event_type,
            "physical_command_enabled": False,
            "no_physical_command_generated": True,
        }
        self.logger.emit(LogLevel.INFO, "PICO", str(safe_payload.get("summary", event_type)), safe_payload)

    def connect(self, request: PicoConnectRequest) -> PicoConnectionEvent:
        self.port = request.port
        self.baudrate = request.baudrate
        self.connection_status = PicoConnectionStatus.MOCK_CONNECTED
        self.last_heartbeat_at = time.time()
        self.last_connection_event = PicoConnectionEvent(
            connection_status=self.connection_status,
            port=self.port,
            baudrate=self.baudrate,
            reason="Mock Pico connected; no serial port was opened.",
        )
        self.logger.emit(
            LogLevel.INFO,
            "PICO",
            "Mock Pico connected",
            self.last_connection_event.model_dump(mode="json"),
        )
        return self.last_connection_event

    def disconnect(self) -> PicoConnectionEvent:
        self.connection_status = PicoConnectionStatus.DISCONNECTED
        self.last_heartbeat_at = None
        self.last_connection_event = PicoConnectionEvent(
            connection_status=self.connection_status,
            port=self.port,
            baudrate=self.baudrate,
            reason="Mock Pico disconnected.",
        )
        self.logger.emit(
            LogLevel.INFO,
            "PICO",
            "Mock Pico disconnected",
            self.last_connection_event.model_dump(mode="json"),
        )
        return self.last_connection_event

    def pins(self) -> PinProfile:
        return self.pin_profile

    def validate_pins(self, profile: PinProfile, system_state: SystemState) -> PinValidationResult:
        issues: list[PinValidationIssue] = []

        if system_state.mode != "DISARMED" or system_state.armed:
            issues.append(
                PinValidationIssue(
                    level=PinValidationLevel.CRITICAL,
                    code="SYSTEM_NOT_DISARMED",
                    message="Pin changes require system mode DISARMED.",
                )
            )

        active = [pin for pin in profile.pins if pin.function != PinFunction.UNUSED]
        for function in CRITICAL_UNIQUE_FUNCTIONS:
            matches = [pin for pin in active if pin.function == function]
            if len(matches) > 1:
                issues.append(
                    PinValidationIssue(
                        level=PinValidationLevel.ERROR,
                        code="DUPLICATE_FUNCTION",
                        message=f"{function} is assigned to multiple pins.",
                        function=function,
                    )
                )

        assigned_functions = {pin.function for pin in active}
        for function in sorted(REQUIRED_MOTION_FUNCTIONS - assigned_functions):
            issues.append(
                PinValidationIssue(
                    level=PinValidationLevel.ERROR,
                    code="MISSING_MOTION_PIN",
                    message=f"{function} must be assigned before motion interface is valid.",
                    function=function,
                )
            )

        if PinFunction.ESTOP_IN not in assigned_functions:
            issues.append(
                PinValidationIssue(
                    level=PinValidationLevel.CRITICAL,
                    code="MISSING_ESTOP",
                    message="ESTOP_IN is required. System cannot be armed without it.",
                    function=PinFunction.ESTOP_IN,
                )
            )

        for pin in active:
            if pin.function in INPUT_FUNCTIONS and pin.direction != PinDirection.IN:
                issues.append(
                    PinValidationIssue(
                        level=PinValidationLevel.ERROR,
                        code="DIRECTION_MISMATCH",
                        message=f"{pin.function} must be configured as input.",
                        pin_name=pin.pin_name,
                        function=pin.function,
                    )
                )
            if pin.function in OUTPUT_FUNCTIONS and pin.direction != PinDirection.OUT:
                issues.append(
                    PinValidationIssue(
                        level=PinValidationLevel.ERROR,
                        code="DIRECTION_MISMATCH",
                        message=f"{pin.function} must be configured as output.",
                        pin_name=pin.pin_name,
                        function=pin.function,
                    )
                )
            if pin.function == PinFunction.TRIGGER_SERVO_PWM and (
                pin.direction != PinDirection.OUT or pin.mode != PinMode.PWM or not pin.pwm_capable
            ):
                issues.append(
                    PinValidationIssue(
                        level=PinValidationLevel.ERROR,
                        code="PWM_CAPABILITY_MISMATCH",
                        message="TRIGGER_SERVO_PWM requires a PWM-capable output pin.",
                        pin_name=pin.pin_name,
                        function=pin.function,
                    )
                )

        uart_tx = [pin for pin in active if pin.function == PinFunction.UART_TX]
        uart_rx = [pin for pin in active if pin.function == PinFunction.UART_RX]
        if uart_tx and uart_rx and uart_tx[0].pin_name == uart_rx[0].pin_name:
            issues.append(
                PinValidationIssue(
                    level=PinValidationLevel.ERROR,
                    code="UART_TX_RX_SAME_PIN",
                    message="UART_TX and UART_RX cannot use the same pin.",
                    pin_name=uart_tx[0].pin_name,
                )
            )

        has_error = any(issue.level in {PinValidationLevel.ERROR, PinValidationLevel.CRITICAL} for issue in issues)
        result = PinValidationResult(
            valid=not has_error,
            can_apply=not has_error and system_state.mode == "DISARMED" and not system_state.armed,
            system_mode=system_state.mode,
            system_armed=system_state.armed,
            issues=issues
            or [
                PinValidationIssue(
                    level=PinValidationLevel.INFO,
                    code="PIN_PROFILE_VALID",
                    message="Pin profile is valid for mock dry-run configuration.",
                )
            ],
        )
        self.last_validation = result
        self.logger.emit(
            LogLevel.INFO if result.valid else LogLevel.WARN,
            "PICO",
            "Pin profile validated",
            result.model_dump(mode="json"),
        )
        return result

    def update_pins(self, profile: PinProfile, system_state: SystemState) -> PinValidationResult:
        result = self.validate_pins(profile, system_state)
        if result.can_apply:
            self.pin_profile = profile
            self.logger.emit(
                LogLevel.INFO,
                "PICO",
                "Pin profile updated in memory",
                {"profile_name": profile.profile_name, "final_approved": profile.final_approved},
            )
        return result

    def _poll_readonly(self) -> None:
        handle = self._readonly_handle
        if handle is None:
            return
        try:
            waiting = getattr(handle, "in_waiting", 0)
        except Exception:
            waiting = 0
        lines: list[str] = []
        for _ in range(min(max(int(waiting), 1), 5)):
            try:
                raw = handle.readline()
            except Exception:
                break
            if not raw:
                break
            try:
                lines.append(raw.decode("utf-8", errors="replace").strip())
            except AttributeError:
                lines.append(str(raw).strip())
        for line in lines:
            self._parse_readonly_line(line)

    def _parse_readonly_line(self, line: str) -> None:
        if not line:
            return
        parsed: dict = {}
        try:
            loaded = json.loads(line)
            parsed = loaded if isinstance(loaded, dict) else {"value": loaded}
        except json.JSONDecodeError:
            self._readonly_status.parse_errors += 1
            parsed = {"raw": line, "parse_error": True}
        now = time.time()
        firmware = parsed.get("firmware_version") or parsed.get("firmware") or self._readonly_status.firmware_version
        heartbeat = bool(parsed.get("heartbeat") or parsed.get("type") == "telemetry" or parsed.get("device"))
        limits = parsed.get("limits") if isinstance(parsed.get("limits"), dict) else {}
        motor_driver_state = {
            "driver_enabled": parsed.get("driver_enabled"),
            "physical_outputs_enabled": parsed.get("physical_outputs_enabled"),
        }
        warning_fault_state = {
            "warning": parsed.get("warning"),
            "fault": parsed.get("fault"),
            "safe_state": parsed.get("safe_state"),
        }
        self._readonly_status.last_seen_at = now
        self._readonly_status.heartbeat_seen = self._readonly_status.heartbeat_seen or heartbeat
        self._readonly_status.firmware_version = firmware
        self._readonly_status.telemetry_frames += 1
        self._latest_readonly_telemetry = PicoReadOnlyTelemetry(
            raw_line_sample=line,
            parsed=parsed,
            heartbeat=heartbeat,
            firmware_version=firmware,
            estop_state=parsed.get("estop_state"),
            limit_states=limits,
            motor_driver_state=motor_driver_state,
            warning_fault_state=warning_fault_state,
            no_command_generated=True,
            serial_write_enabled=False,
            command_tx_enabled=False,
            physical_command_enabled=False,
            no_physical_command_generated=True,
        )
        summary = f"Pico read-only telemetry received; frames={self._readonly_status.telemetry_frames}; no_physical_command_generated=true."
        self._readonly_event("pico.readonly_telemetry_received", {**self._latest_readonly_telemetry.model_dump(mode="json"), "summary": summary}, summary)
        self._readonly_event("pico.real_telemetry_sampled", {**self._latest_readonly_telemetry.model_dump(mode="json"), "summary": summary}, summary)

    def _readonly_event(self, event_type: str, payload: dict, message: str) -> None:
        safe_payload = {
            **payload,
            "type": event_type,
            "summary": message,
            "physical_command_enabled": False,
            "no_physical_command_generated": True,
        }
        self.logger.emit(LogLevel.INFO, "PICO", message, safe_payload)

    def _run_text(self, args: list[str], enabled: bool = True) -> str:
        if not enabled:
            return ""
        try:
            completed = subprocess.run(args, capture_output=True, text=True, timeout=3, check=False)
        except Exception as exc:
            return str(exc)
        return ((completed.stdout or "") + (completed.stderr or ""))[-6000:]

    def _profile_from_config(self, config: AppConfig) -> PinProfile:
        pins = [
            PinAssignment(
                pin_name=pin_name,
                physical_pin=physical_pin,
                pwm_capable=True,
                uart_capable=pin_name in {"GP0", "GP1", "GP4", "GP5", "GP8", "GP9", "GP12", "GP13", "GP16", "GP17"},
            )
            for pin_name, physical_pin in PHYSICAL_PINS.items()
        ]
        by_name = {pin.pin_name: pin for pin in pins}
        for config_key, pin_name in config.pins.assignments.items():
            function = CONFIG_TO_FUNCTION.get(config_key)
            if function is None or pin_name not in by_name:
                continue
            by_name[pin_name].function = function
            by_name[pin_name].direction = self._direction_for_function(function)
            by_name[pin_name].mode = self._mode_for_function(function)

        return PinProfile(
            profile_name=config.pins.profile_name,
            note=config.pins.note,
            final_approved=False,
            pins=pins,
        )

    @staticmethod
    def _direction_for_function(function: PinFunction) -> PinDirection:
        if function in INPUT_FUNCTIONS:
            return PinDirection.IN
        if function == PinFunction.UNUSED:
            return PinDirection.UNUSED
        return PinDirection.OUT

    @staticmethod
    def _mode_for_function(function: PinFunction) -> PinMode:
        if function == PinFunction.TRIGGER_SERVO_PWM:
            return PinMode.PWM
        if function in {PinFunction.UART_TX, PinFunction.UART_RX}:
            return PinMode.UART
        if function == PinFunction.UNUSED:
            return PinMode.UNUSED
        return PinMode.GPIO


def _float_or_none(value, fallback: float | None = None) -> float | None:
    if value is None:
        return fallback
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _int_or_none(value, fallback: int | None = None) -> int | None:
    if value is None:
        return fallback
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _protocol_contract_payload(*, serial_tx_enabled: bool, physical_command_enabled: bool) -> dict:
    return {
        "protocol_name": "ISTIKLAL Serial Packet Protocol",
        "protocol_version": 1,
        "frame": {
            "sof_1": "0xA5",
            "sof_2": "0x5A",
            "version": "uint8",
            "msg_type": "uint8",
            "seq_id": "uint16_le",
            "timestamp_ms": "uint32_le",
            "flags": "uint16_le",
            "payload_len": "uint16_le",
            "payload": "bytes",
            "crc32": "uint32_le over version..payload",
        },
        "message_types": {
            "0x01": "HELLO",
            "0x02": "HEARTBEAT",
            "0x03": "TELEMETRY",
            "0x04": "DRIVER_STATE",
            "0x05": "LIMIT_STATE",
            "0x06": "FAULT",
            "0x07": "ACK",
            "0x08": "NACK",
            "0x09": "CONFIG_REPORT",
            "0xFF": "UNKNOWN",
        },
        "phase36_enabled_capabilities": [
            "port_discovery",
            "heartbeat_parse",
            "telemetry_parse",
            "driver_state_parse",
            "limit_state_parse",
            "fault_parse",
            "ack_nack_parse",
            "config_report_parse",
            "digital_twin_pose_ingest",
        ],
        "explicitly_disabled_commands": [
            "SPD",
            "LZR",
            "STP",
            "motor movement",
            "fire",
            "trigger",
            "servo actuation",
            "GPIO output",
            "PWM output",
            "STEP/DIR output",
            "hardware enable",
            "legacy raw physical command TX",
        ],
        "serial_tx_enabled": serial_tx_enabled,
        "physical_command_enabled": physical_command_enabled,
        "no_physical_command_generated": True,
        "safety_boundary": "Phase 36 is telemetry/read-only first. It does not transmit physical control commands.",
    }
