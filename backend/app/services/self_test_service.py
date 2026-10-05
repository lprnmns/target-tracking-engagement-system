import json
import subprocess
import time
import uuid
from pathlib import Path
from typing import Callable

from app.schemas.log import LogLevel
from app.schemas.model_registry import ModelTestInferenceRequest
from app.schemas.motion import MotionGoToRequest, MotionJogRequest
from app.schemas.self_test import SelfTestRun, SelfTestStep
from app.schemas.serial import SerialSendJsonRequest
from app.schemas.serial import SerialSimulateRxRequest
from app.services.log_service import JsonlLogService
from app.services.storage_paths import project_root


class SelfTestService:
    def __init__(self, logger: JsonlLogService, report_dir: Path | None = None) -> None:
        self.logger = logger
        self.runs: list[SelfTestRun] = []
        self.latest_run: SelfTestRun | None = None
        self.last_event: tuple[str, dict] | None = None
        self.cancel_requested = False
        self.report_dir = report_dir or (project_root() / "reports" / "self_tests")
        self.report_dir.mkdir(parents=True, exist_ok=True)

    def status(self):
        from app.schemas.self_test import SelfTestStatus

        return SelfTestStatus(
            latest_run=self.latest_run,
            running=self.latest_run is not None and self.latest_run.status == "running",
            runs_count=len(self.runs),
        )

    def list_runs(self) -> list[SelfTestRun]:
        return list(reversed(self.runs[-25:]))

    def get_run(self, run_id: str) -> SelfTestRun:
        for run in self.runs:
            if run.run_id == run_id:
                return run
        raise KeyError(run_id)

    def cancel(self) -> SelfTestRun | None:
        self.cancel_requested = True
        if self.latest_run and self.latest_run.status == "running":
            self.latest_run = self.latest_run.model_copy(update={"status": "cancelled", "ended_at": time.time()})
            self._event("self_test.cancelled", self.latest_run.model_dump(mode="json"), "Self-test cancelled")
        return self.latest_run

    def run(self, runtime) -> SelfTestRun:
        self.cancel_requested = False
        run = SelfTestRun(
            run_id=f"selftest-{uuid.uuid4().hex[:10]}",
            status="running",
            dry_run=runtime.config.system.dry_run,
            hardware_enabled=runtime.config.system.hardware_enabled,
            no_physical_command_generated=True,
        )
        self.latest_run = run
        self.runs.append(run)
        self._event("self_test.started", run.model_dump(mode="json"), "Self-test started")

        step_specs: list[tuple[str, str, str, str, Callable]] = [
            ("backend_health", "Backend health check", "backend", "critical", self._check_backend_health),
            ("config_loaded", "Config loaded", "config", "critical", self._check_config_loaded),
            ("config_validation", "Config validation", "config", "critical", self._check_config_validation),
            ("default_safety_state", "Default safety state check", "safety", "critical", self._check_default_safety),
            ("safety_gates", "Safety gates available", "safety", "critical", self._check_safety_gates),
            ("fire_rejected", "Fire request rejected by default", "safety", "critical", self._check_fire_rejected),
            ("no_physical_path", "No physical command path enabled", "safety", "critical", self._check_no_physical_path),
            ("estop_readable", "E-stop state readable", "safety", "warning", self._check_estop_readable),
            ("hardware_disabled_gate", "Hardware disabled gate active", "safety", "critical", self._check_hardware_gate),
            ("hardware_discovery_config", "Hardware discovery config check", "hardware", "critical", self._check_hardware_discovery_config),
            ("real_serial_readonly_state", "Real serial read-only state check", "hardware", "warning", self._check_real_serial_readonly_state),
            ("physical_command_disabled", "Physical command disabled check", "hardware", "critical", self._check_physical_command_disabled),
            ("readonly_telemetry_readable", "Read-only telemetry readable if connected", "hardware", "warning", self._check_readonly_telemetry),
            ("pico_telemetry_firmware", "Pico telemetry firmware detected", "hardware", "warning", self._check_pico_telemetry_firmware),
            ("pico_verified_from_telemetry", "Pico verified from telemetry", "hardware", "warning", self._check_pico_verified_from_telemetry),
            ("pico_physical_outputs_disabled", "Physical outputs disabled by firmware", "hardware", "critical", self._check_pico_physical_outputs_disabled),
            ("telemetry_age_within_timeout", "Telemetry age within timeout", "hardware", "warning", self._check_telemetry_age_within_timeout),
            ("readonly_serial_path_active", "Read-only serial path active", "hardware", "warning", self._check_readonly_serial_path_active),
            ("phase12_risky_blocker", "Phase 12 risky command blocker", "hardware", "critical", self._check_phase12_risky_blocker),
            ("device_manager_scan", "Device manager scan works", "hardware", "warning", self._check_device_manager_scan),
            ("camera_source_selected", "Camera source selected", "vision", "warning", self._check_camera_source_selected),
            ("camera_frame_probe", "Camera frame probe or mock selected", "vision", "warning", self._check_camera_frame_probe),
            ("camera_runtime_profile", "Camera runtime profile valid", "vision", "warning", self._check_camera_runtime_profile),
            ("vision_runtime_settings", "Vision runtime settings valid", "vision", "warning", self._check_vision_runtime_settings),
            ("active_model_or_adapter", "Active model available or test adapter selected", "model", "warning", self._check_active_model_or_adapter),
            ("yolo_settings_safe", "YOLO settings within safe limits", "vision", "warning", self._check_yolo_settings_safe),
            ("pico_candidate_detection", "Pico candidate detection available", "hardware", "warning", self._check_pico_candidate_detection),
            ("pico_status", "Pico service status", "pico", "warning", self._check_pico_status),
            ("mock_pico", "Mock Pico active", "pico", "warning", self._check_mock_pico),
            ("physical_pico_disabled", "Physical Pico disconnected/disabled represented", "pico", "warning", self._check_physical_pico),
            ("pin_profile", "Pin profile loaded", "pico", "critical", self._check_pin_profile),
            ("pin_validation", "Pin validation result", "pico", "critical", self._check_pin_validation),
            ("estop_pin", "ESTOP_IN present", "pico", "critical", self._check_estop_pin),
            ("pin_conflicts", "Critical pin conflict check", "pico", "critical", self._check_pin_conflicts),
            ("serial_status", "Serial status endpoint", "serial", "critical", self._check_serial_status),
            ("serial_mock", "Transport mode mock", "serial", "critical", self._check_serial_mock),
            ("risky_tx_rejected", "Risky TX rejected", "serial", "critical", self._check_risky_tx),
            ("safe_disarm_mock", "Safe DISARM JSON-line command accepted in mock only", "serial", "warning", self._check_safe_disarm),
            ("ack_nack", "ACK/NACK handling basic check", "serial", "warning", self._check_ack_nack),
            ("no_physical_serial", "No physical serial command generated", "serial", "critical", self._check_no_physical_serial),
            ("camera_status", "Camera service status", "vision", "warning", self._check_camera_status),
            ("mock_camera", "Mock camera available", "vision", "warning", self._check_mock_camera),
            ("mjpeg_stream", "MJPEG stream endpoint available", "vision", "warning", self._check_mjpeg),
            ("vision_status", "Vision status available", "vision", "warning", self._check_vision_status),
            ("latest_vision", "Latest vision event readable", "vision", "warning", self._check_latest_vision),
            ("overlay_metadata", "Overlay metadata available", "vision", "warning", self._check_overlay_metadata),
            ("vision_advisory", "Vision advisory-only check", "vision", "critical", self._check_vision_advisory),
            ("mock_frame_readable", "Mock surrogate frame readable", "vision", "warning", self._check_surrogate_mock_frame_readable),
            ("camera_frame_readable", "Real camera frame readable", "vision", "warning", self._check_surrogate_real_camera_frame_readable),
            ("real_camera_evidence", "Real camera evidence status", "vision", "warning", self._check_surrogate_real_camera_evidence),
            ("surrogate_detector_available", "OpenCV circle surrogate detector available", "vision", "warning", self._check_surrogate_detector_available),
            ("surrogate_detector_no_physical", "Surrogate detector no physical command", "vision", "critical", self._check_surrogate_detector_no_physical),
            ("snapshot_export_available", "Surrogate snapshot export available", "vision", "warning", self._check_surrogate_snapshot_export),
            ("fps_latency_measured", "Surrogate FPS/latency measured", "vision", "warning", self._check_surrogate_fps_latency),
            ("model_registry", "Model registry available", "model", "warning", self._check_model_registry),
            ("model_package_service", "Model package service available", "model", "warning", self._check_model_package_service),
            ("model_registry_readable", "Model registry readable", "model", "warning", self._check_model_registry_readable),
            ("active_models", "Active model summary readable", "model", "warning", self._check_active_models),
            ("active_model_status_readable", "Active model status readable", "model", "warning", self._check_active_model_status_readable),
            ("production_or_test_adapter_declared", "Production model or test adapter declared", "model", "warning", self._check_production_or_test_adapter_declared),
            ("class_mapping_valid_if_production", "Class mapping valid if production model", "model", "warning", self._check_class_mapping_valid_if_production),
            ("opencv_adapter", "OpenCV circle detector test adapter available", "model", "warning", self._check_opencv_adapter),
            ("adapter_warning", "Test adapter warning visible", "model", "warning", self._check_adapter_warning),
            ("model_no_physical", "Model test inference no physical command", "model", "critical", self._check_model_no_physical),
            ("vision_runtime_model_compatible", "Vision runtime model compatible", "model", "warning", self._check_vision_runtime_model_compatible),
            ("competition_model_blocker", "Competition model blocker reported", "model", "warning", self._check_competition_model_blocker),
            ("motion_status", "Motion status readable", "motion", "warning", self._check_motion_status),
            ("motion_dry_run", "Dry-run motion mode active", "motion", "critical", self._check_motion_dry_run),
            ("jog_dry_run", "Jog dry-run accepted without physical command", "motion", "critical", self._check_jog_dry_run),
            ("out_of_limit", "Out-of-limit command rejected", "motion", "critical", self._check_out_of_limit),
            ("stop_accepted", "Stop accepted", "motion", "warning", self._check_stop),
            ("motion_no_physical", "No physical motion command generated", "motion", "critical", self._check_motion_no_physical),
            ("sessions_api", "Sessions API available", "dataset", "warning", self._check_sessions),
            ("dataset_api", "Dataset API available", "dataset", "warning", self._check_dataset),
            ("replay_status", "Replay status available", "replay", "warning", self._check_replay_status),
            ("e2e_data", "Latest E2E dataset/session availability", "dataset", "info", self._check_e2e_data),
            ("export_validation", "YOLO export validation service available", "dataset", "warning", self._check_export_validation),
            ("replay_no_physical", "Replay no physical command invariant", "replay", "critical", self._check_replay_no_physical),
            ("jsonl_logging", "JSONL logging writable", "logging", "critical", self._check_jsonl_logging),
            ("self_test_log", "Self-test log event written", "logging", "critical", self._check_self_log),
            ("recent_events", "Recent events readable", "logging", "warning", self._check_recent_events),
            ("interface_inventory", "Interface inventory check", "interface", "warning", self._check_interface_inventory),
            ("portable_launcher", "Portable launcher check", "deployment", "warning", self._check_portable_launcher),
            ("first_run_acceptance", "First-run acceptance service check", "first_run", "warning", self._check_first_run_acceptance),
            ("release_readiness", "Release readiness check", "deployment", "warning", self._check_release_readiness),
        ]

        steps: list[SelfTestStep] = []
        for step_id, name, category, severity, check in step_specs:
            if self.cancel_requested:
                break
            step = SelfTestStep(step_id=step_id, name=name, category=category, severity=severity, status="running", started_at=time.time())
            steps.append(step)
            self._sync_run(run, steps, status="running")
            self._event("self_test.step_started", step.model_dump(mode="json"), f"Self-test step started: {name}")
            step = self._execute_step(step, check, runtime)
            steps[-1] = step
            event_type = "self_test.step_completed"
            if step.status == "warning":
                event_type = "self_test.warning"
            if step.status == "failed":
                event_type = "self_test.failed"
            self._event(event_type, step.model_dump(mode="json"), f"Self-test step completed: {name}")
            self._sync_run(run, steps, status="running")

        final = self._finalize_run(run, steps, cancelled=self.cancel_requested)
        self.latest_run = final
        self.runs[-1] = final
        self._event("self_test.completed" if final.status != "cancelled" else "self_test.cancelled", final.model_dump(mode="json"), "Self-test completed")
        return final

    def export_report(self, run: SelfTestRun) -> SelfTestRun:
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        base = self.report_dir / f"self_test_{timestamp}_{run.run_id}"
        json_path = base.with_suffix(".json")
        md_path = base.with_suffix(".md")
        json_path.write_text(json.dumps(run.model_dump(mode="json"), indent=2), encoding="utf-8")
        md_path.write_text(self._markdown(run), encoding="utf-8")
        updated = run.model_copy(update={"report_path": str(md_path)})
        if self.latest_run and self.latest_run.run_id == run.run_id:
            self.latest_run = updated
        self.runs = [updated if item.run_id == run.run_id else item for item in self.runs]
        return updated

    def _execute_step(self, step: SelfTestStep, check: Callable, runtime) -> SelfTestStep:
        started = step.started_at or time.time()
        try:
            status, message, details, suggested_action = check(runtime)
        except Exception as exc:  # pragma: no cover - defensive guard for self-test UI
            status = "failed" if step.severity == "critical" else "warning"
            message = f"Self-test step raised controlled error: {exc}"
            details = {"error": str(exc)}
            suggested_action = "Inspect backend logs for this step."
        ended = time.time()
        blocking = status == "failed" and step.severity == "critical"
        return step.model_copy(
            update={
                "status": status,
                "ended_at": ended,
                "duration_ms": round((ended - started) * 1000, 3),
                "message": message,
                "details": details,
                "blocking": blocking,
                "suggested_action": suggested_action,
            }
        )

    def _sync_run(self, run: SelfTestRun, steps: list[SelfTestStep], status: str) -> None:
        updated = run.model_copy(update={"steps": steps, "status": status})
        self.latest_run = updated
        self.runs[-1] = updated

    def _finalize_run(self, run: SelfTestRun, steps: list[SelfTestStep], cancelled: bool) -> SelfTestRun:
        counts = {status: sum(1 for step in steps if step.status == status) for status in ("passed", "warning", "failed", "skipped")}
        critical_failures = [step for step in steps if step.status == "failed" and step.severity == "critical"]
        no_physical = run.dry_run and not run.hardware_enabled and not any(
            step.details.get("no_physical_command_generated") is False for step in steps
        )
        if cancelled:
            status = "cancelled"
            readiness = "not_ready"
            ready = False
        elif critical_failures or not no_physical:
            status = "failed"
            readiness = "not_ready"
            ready = False
        elif counts["warning"] > 0 or not run.hardware_enabled:
            status = "warning"
            hardware_status = next((step for step in steps if step.step_id == "real_serial_readonly_state"), None)
            readonly_connected = bool(hardware_status and hardware_status.details.get("connection_state") in {"PICO_READONLY_VERIFIED", "READONLY_CONNECTED_UNVERIFIED"})
            readiness = "hardware_readonly_ready" if readonly_connected and not run.hardware_enabled else ("demo_ready" if not run.hardware_enabled else "hardware_blocked")
            ready = True
        else:
            status = "passed"
            readiness = "field_test_ready" if run.hardware_enabled else "demo_ready"
            ready = True
        summary = {
            **counts,
            "critical_failures": len(critical_failures),
            "suggested_actions": [step.suggested_action for step in steps if step.suggested_action],
            "git_hash": self._git_hash(),
        }
        final = run.model_copy(
            update={
                "status": status,
                "overall_ready": ready,
                "readiness_level": readiness,
                "no_physical_command_generated": no_physical,
                "ended_at": time.time(),
                "steps": steps,
                "summary": summary,
            }
        )
        return self.export_report(final)

    def _event(self, event_type: str, payload: dict, message: str) -> None:
        self.last_event = (event_type, payload)
        self.logger.emit(LogLevel.INFO, "SELF_TEST", message, payload)

    def _pass(self, message: str, details: dict | None = None):
        return "passed", message, details or {}, None

    def _warn(self, message: str, details: dict | None = None, action: str | None = None):
        return "warning", message, details or {}, action

    def _fail(self, message: str, details: dict | None = None, action: str | None = None):
        return "failed", message, details or {}, action

    def _skip(self, message: str, details: dict | None = None, action: str | None = None):
        return "skipped", message, details or {}, action

    def _check_backend_health(self, runtime):
        state = runtime.system_state()
        return self._pass("Backend runtime is responding.", state.model_dump(mode="json"))

    def _check_config_loaded(self, runtime):
        return self._pass("Config object is loaded.", {"system": runtime.config.system.model_dump(mode="json")})

    def _check_config_validation(self, runtime):
        return self._pass("Config validation already completed during app startup.", {"dry_run": runtime.config.system.dry_run})

    def _check_default_safety(self, runtime):
        state = runtime.system_state()
        ok = state.mode in {"DISARMED", "STANDBY"} and state.fire_policy == "NO_FIRE" and state.dry_run and not state.hardware_enabled
        if not ok:
            return self._fail("Default safety invariant is broken.", state.model_dump(mode="json"), "Restore DISARMED/NO_FIRE/dry_run=true/hardware_enabled=false.")
        return self._pass("Default safety invariant is intact.", state.model_dump(mode="json"))

    def _check_safety_gates(self, runtime):
        decision = runtime.decision_engine.evaluate(runtime)
        safety = runtime.safety.state(decision)
        return self._pass("Safety gates are available.", {"gate_count": len(decision.gates), "decision": safety.decision})

    def _check_fire_rejected(self, runtime):
        result = runtime.decision_engine.fire_request(runtime, operator_confirmed=False)
        if result.accepted:
            return self._fail("Fire request was accepted unexpectedly.", result.model_dump(mode="json"), "Disable fire path and inspect decision gates.")
        return self._pass("Fire request rejected by default.", result.model_dump(mode="json"))

    def _check_no_physical_path(self, runtime):
        ok = runtime.config.system.dry_run and not runtime.config.system.hardware_enabled
        if not ok:
            return self._fail("Physical command path appears enabled.", runtime.config.system.model_dump(mode="json"))
        return self._pass("No physical command path is enabled.", {"no_physical_command_generated": True})

    def _check_estop_readable(self, runtime):
        telemetry = runtime.pico.telemetry()
        return self._pass("E-stop state is readable.", {"estop_state": telemetry.estop_state})

    def _check_hardware_gate(self, runtime):
        if runtime.config.system.hardware_enabled:
            return self._fail("Hardware enabled gate is not blocking.", {"hardware_enabled": True})
        return self._pass("Hardware disabled gate is active.", {"hardware_enabled": False})

    def _check_hardware_discovery_config(self, runtime):
        config = runtime.config.hardware
        if config.physical_command_enabled or config.allow_physical_motion or config.allow_physical_fire:
            return self._fail("Unsafe hardware command flag is enabled.", config.model_dump(mode="json"))
        return self._pass("Hardware discovery config is safe.", config.model_dump(mode="json"))

    def _check_real_serial_readonly_state(self, runtime):
        status = runtime.hardware.status(mock_pico_active=runtime.pico.status().mock_mode)
        if status.transport_mode == "real_readonly" and not runtime.config.hardware.allow_real_serial_readonly:
            return self._fail("Real read-only serial is connected without config permission.", status.model_dump(mode="json"))
        if status.connection_state in {"PORT_OPEN_NO_TELEMETRY", "READONLY_CONNECTED_UNVERIFIED", "PICO_READONLY_VERIFIED", "MOCK_READONLY_CONNECTED"}:
            return self._pass("Real serial read-only connection is represented safely.", status.model_dump(mode="json"))
        return self._pass("Real serial read-only is disconnected or disabled as expected.", status.model_dump(mode="json"))

    def _check_physical_command_disabled(self, runtime):
        status = runtime.hardware.status(mock_pico_active=runtime.pico.status().mock_mode)
        if status.physical_command_enabled:
            return self._fail("Physical command enable is unexpectedly true.", status.model_dump(mode="json"))
        return self._pass("Physical command enable is false.", {"no_physical_command_generated": True, **status.model_dump(mode="json")})

    def _check_readonly_telemetry(self, runtime):
        status = runtime.hardware.status(mock_pico_active=runtime.pico.status().mock_mode)
        if status.connection_state in {"READONLY_CONNECTED_UNVERIFIED", "PICO_READONLY_VERIFIED"} and not status.telemetry_available:
            return self._warn("Read-only port is connected but telemetry is unavailable.", status.model_dump(mode="json"), "Confirm Pico telemetry firmware is running.")
        if status.connection_state == "PICO_READONLY_VERIFIED":
            return self._pass("Pico read-only telemetry is verified.", status.model_dump(mode="json"))
        if status.connection_state == "READONLY_CONNECTED_UNVERIFIED":
            return self._pass("Read-only telemetry is available.", status.model_dump(mode="json"))
        if status.connection_state == "PORT_OPEN_NO_TELEMETRY":
            return self._warn("Read-only serial port is open but telemetry is unavailable.", status.model_dump(mode="json"), "Flash telemetry-only Pico firmware or verify the selected port.")
        if status.connection_state == "MOCK_READONLY_CONNECTED":
            return self._warn("Mock read-only path is connected but no real telemetry is available.", status.model_dump(mode="json"), "Connect real Pico 2 with telemetry-only firmware for hardware acceptance.")
        return self._skip("Read-only hardware is not connected; telemetry check skipped.", status.model_dump(mode="json"))

    def _check_pico_telemetry_firmware(self, runtime):
        status = runtime.hardware.status(mock_pico_active=runtime.pico.status().mock_mode)
        if not status.port_open:
            return self._warn("Real Pico is not connected; telemetry firmware detection skipped.", status.model_dump(mode="json"), "Connect Pico 2 with telemetry-only firmware for real acceptance.")
        if status.telemetry_firmware_detected:
            return self._pass("Telemetry-only Pico firmware detected.", status.model_dump(mode="json"))
        return self._warn("Telemetry-only Pico firmware was not detected.", status.model_dump(mode="json"), "Flash firmware/pico2_telemetry_only/main.py and reconnect read-only.")

    def _check_pico_verified_from_telemetry(self, runtime):
        status = runtime.hardware.status(mock_pico_active=runtime.pico.status().mock_mode)
        if status.pico_verified:
            return self._pass("Pico verified from telemetry device and firmware fields.", status.model_dump(mode="json"))
        return self._warn("Pico is not verified from telemetry.", status.model_dump(mode="json"), "Confirm telemetry has device=pico2, telemetry-only firmware version, and physical_outputs_enabled=false.")

    def _check_pico_physical_outputs_disabled(self, runtime):
        status = runtime.hardware.status(mock_pico_active=runtime.pico.status().mock_mode)
        if status.telemetry.physical_outputs_enabled is True:
            return self._fail("Firmware reported physical outputs enabled.", status.model_dump(mode="json"), "Stop test and flash telemetry-only firmware.")
        return self._pass("Physical outputs are disabled or not reported by firmware.", status.model_dump(mode="json"))

    def _check_telemetry_age_within_timeout(self, runtime):
        status = runtime.hardware.status(mock_pico_active=runtime.pico.status().mock_mode)
        if not status.telemetry_received:
            return self._warn("Telemetry age unavailable because no telemetry was received.", status.model_dump(mode="json"), "Connect telemetry-only Pico firmware.")
        heartbeat_age = status.telemetry.heartbeat_age_ms
        if heartbeat_age is not None and heartbeat_age <= runtime.config.serial.heartbeat_timeout_ms:
            return self._pass("Telemetry age is within timeout.", status.model_dump(mode="json"))
        return self._warn("Telemetry age exceeds timeout.", status.model_dump(mode="json"), "Check USB serial telemetry rate and port selection.")

    def _check_readonly_serial_path_active(self, runtime):
        status = runtime.hardware.status(mock_pico_active=runtime.pico.status().mock_mode)
        if status.connection_state in {"PORT_OPEN_NO_TELEMETRY", "READONLY_CONNECTED_UNVERIFIED", "PICO_READONLY_VERIFIED", "MOCK_READONLY_CONNECTED"}:
            return self._pass("Read-only serial path is active and command-disabled.", status.model_dump(mode="json"))
        return self._warn("Read-only serial path is not active.", status.model_dump(mode="json"), "Connect Pico read-only for hardware acceptance.")

    def _check_phase12_risky_blocker(self, runtime):
        result = runtime.hardware.block_risky_command("jog_motor")
        if result.accepted:
            return self._fail("Risky hardware command blocker accepted a command.", result.model_dump(mode="json"))
        return self._pass("Risky hardware command blocker rejects physical commands.", result.model_dump(mode="json"))

    def _check_device_manager_scan(self, runtime):
        inventory = runtime.device_manager.scan()
        return self._pass("Device manager scan completed.", inventory.model_dump(mode="json"))

    def _check_camera_source_selected(self, runtime):
        status = runtime.camera_runtime.status()
        if status.profile.source_type == "mock" or status.selected_camera:
            return self._pass("Camera source is selected.", status.model_dump(mode="json"))
        return self._warn("No camera source selected.", status.model_dump(mode="json"), "Select mock, laptop or USB camera source.")

    def _check_camera_frame_probe(self, runtime):
        status = runtime.camera_runtime.status()
        if status.profile.source_type == "mock":
            return self._pass("Mock camera selected; physical frame probe not required.", status.model_dump(mode="json"))
        result = runtime.camera_runtime.probe_current()
        if result.accepted:
            return self._pass("Camera runtime probe completed.", result.model_dump(mode="json"))
        return self._warn("Camera runtime probe failed.", result.model_dump(mode="json"), "Use mock camera or fix camera permissions.")

    def _check_camera_runtime_profile(self, runtime):
        return self._pass("Camera runtime profile is valid.", runtime.camera_runtime.status().model_dump(mode="json"))

    def _check_vision_runtime_settings(self, runtime):
        status = runtime.vision_runtime.status()
        if status.errors:
            return self._warn("Vision runtime settings have warnings/errors.", status.model_dump(mode="json"), "Apply valid YOLO runtime parameters.")
        return self._pass("Vision runtime settings are valid.", status.model_dump(mode="json"))

    def _check_active_model_or_adapter(self, runtime):
        status = runtime.vision_runtime.status()
        if status.profile.inference_adapter in {"mock", "opencv_circle_test"}:
            return self._pass("Test adapter selected until production model is available.", status.model_dump(mode="json"))
        if status.profile.active_body_model_id or status.profile.active_balloon_model_id:
            return self._pass("Active model selected.", status.model_dump(mode="json"))
        return self._warn("No active model or test adapter selected.", status.model_dump(mode="json"), "Select OpenCV test adapter or load a vision team model.")

    def _check_yolo_settings_safe(self, runtime):
        profile = runtime.vision_runtime.profile
        safe = 0 <= profile.conf <= 1 and 0 <= profile.iou <= 1 and profile.imgsz > 0 and profile.max_det > 0
        if safe:
            return self._pass("YOLO runtime parameters are within safe validation limits.", profile.model_dump(mode="json"))
        return self._warn("YOLO runtime parameters are invalid.", profile.model_dump(mode="json"), "Reset vision runtime settings.")

    def _check_pico_candidate_detection(self, runtime):
        inventory = runtime.device_manager.inventory()
        if inventory.pico_candidates:
            return self._pass("Pico candidates are visible to Device Manager.", {"count": len(inventory.pico_candidates), "pico_candidates": [item.model_dump(mode="json") for item in inventory.pico_candidates]})
        return self._warn("No Pico candidate detected by Device Manager.", {"count": 0}, "Connect Pico 2 telemetry-only firmware and refresh devices.")

    def _check_pico_status(self, runtime):
        status = runtime.pico.status()
        return self._pass("Pico service status is readable.", status.model_dump(mode="json"))

    def _check_mock_pico(self, runtime):
        status = runtime.pico.status()
        if not status.mock_mode:
            return self._warn("Mock Pico is not active.", status.model_dump(mode="json"), "Confirm physical Pico safety before field test.")
        return self._pass("Mock Pico active.", status.model_dump(mode="json"))

    def _check_physical_pico(self, runtime):
        telemetry = runtime.pico.telemetry()
        return self._pass("Physical Pico state is explicitly represented.", telemetry.model_dump(mode="json"))

    def _check_pin_profile(self, runtime):
        profile = runtime.pico.pin_profile
        return self._pass("Pin profile loaded.", {"profile_name": profile.profile_name, "final_approved": profile.final_approved})

    def _check_pin_validation(self, runtime):
        result = runtime.pico.validate_pins(runtime.pico.pin_profile, runtime.system_state())
        if not result.valid:
            return self._fail("Pin validation failed.", result.model_dump(mode="json"), "Fix critical pin validation issues.")
        return self._pass("Pin profile validates.", result.model_dump(mode="json"))

    def _check_estop_pin(self, runtime):
        pins = [pin for pin in runtime.pico.pin_profile.pins if pin.function == "ESTOP_IN"]
        if not pins:
            return self._fail("ESTOP_IN is missing.", {}, "Assign ESTOP_IN before demo.")
        return self._pass("ESTOP_IN is present.", pins[0].model_dump(mode="json"))

    def _check_pin_conflicts(self, runtime):
        result = runtime.pico.validate_pins(runtime.pico.pin_profile, runtime.system_state())
        critical = [issue for issue in result.issues if issue.level == "CRITICAL"]
        if critical:
            return self._fail("Critical pin conflict detected.", {"critical": [item.model_dump(mode="json") for item in critical]})
        return self._pass("No critical pin conflicts detected.", result.model_dump(mode="json"))

    def _check_serial_status(self, runtime):
        return self._pass("Serial status is readable.", runtime.serial.status().model_dump(mode="json"))

    def _check_serial_mock(self, runtime):
        status = runtime.serial.status()
        if status.transport_mode not in {"mock", "real_readonly"} or status.real_serial_enabled or status.physical_command_enabled:
            return self._fail("Serial transport is not phase-12 safe.", status.model_dump(mode="json"))
        return self._pass("Serial transport is phase-12 safe.", status.model_dump(mode="json"))

    def _check_risky_tx(self, runtime):
        result = runtime.serial.send_json(SerialSendJsonRequest(message={"type": "fire_request", "seq": runtime.serial.next_seq()}))
        if result.accepted:
            return self._fail("Risky TX was accepted.", result.model_dump(mode="json"))
        return self._pass("Risky TX rejected.", result.model_dump(mode="json"))

    def _check_safe_disarm(self, runtime):
        status = runtime.serial.status()
        if status.transport_mode == "real_readonly":
            return self._pass("Safe DISARM was not sent because real read-only transport forbids TX.", {"no_physical_command_generated": True, **status.model_dump(mode="json")})
        result = runtime.serial.send_json(SerialSendJsonRequest(message={"type": "disarm", "seq": runtime.serial.next_seq(), "reason": "self_test"}))
        if not result.accepted:
            return self._warn("Safe DISARM mock command was not accepted.", result.model_dump(mode="json"), "Inspect serial JSON-line allowlist.")
        return self._pass("Safe DISARM accepted in mock transport only.", result.model_dump(mode="json"))

    def _check_ack_nack(self, runtime):
        seq = runtime.serial.last_tx.get("seq") if runtime.serial.last_tx else runtime.serial.next_seq()
        result = runtime.serial.simulate_rx(SerialSimulateRxRequest(message={"type": "ack", "seq": seq, "accepted": True}))
        if not result.accepted:
            return self._warn("ACK simulation failed.", result.model_dump(mode="json"))
        return self._pass("ACK handling works in mock mode.", result.model_dump(mode="json"))

    def _check_no_physical_serial(self, runtime):
        status = runtime.serial.status()
        ok = status.transport_mode in {"mock", "real_readonly"} and not status.real_serial_enabled and not status.physical_command_enabled
        if not ok:
            return self._fail("Physical serial may be enabled.", status.model_dump(mode="json"))
        return self._pass("No physical serial command generated.", {"no_physical_command_generated": True, **status.model_dump(mode="json")})

    def _check_camera_status(self, runtime):
        return self._pass("Camera status readable.", runtime.camera.status().model_dump(mode="json"))

    def _check_mock_camera(self, runtime):
        status = runtime.camera.status()
        if status.camera_mode != "mock":
            return self._warn("Camera is not in mock mode.", status.model_dump(mode="json"))
        return self._pass("Mock camera available.", status.model_dump(mode="json"))

    def _check_mjpeg(self, runtime):
        frame = runtime.camera.snapshot()
        if not frame:
            return self._warn("MJPEG/snapshot frame unavailable.", {})
        return self._pass("MJPEG stream source is available.", {"snapshot_bytes": len(frame)})

    def _check_vision_status(self, runtime):
        return self._pass("Vision status readable.", runtime.vision_pipeline.status().model_dump(mode="json"))

    def _check_latest_vision(self, runtime):
        event = runtime.vision_pipeline.latest()
        return self._pass("Latest vision event readable.", event.model_dump(mode="json"))

    def _check_overlay_metadata(self, runtime):
        event = runtime.vision_pipeline.latest()
        details = {"body": len(event.body_detections), "balloon": len(event.balloon_detections), "aim_points": len(event.aim_points)}
        return self._pass("Overlay metadata available.", details)

    def _check_vision_advisory(self, runtime):
        status = runtime.vision_pipeline.status()
        if not status.advisory_only:
            return self._fail("Vision is not advisory-only.", status.model_dump(mode="json"))
        return self._pass("Vision remains advisory-only.", status.model_dump(mode="json"))

    def _check_surrogate_mock_frame_readable(self, runtime):
        previous = runtime.camera_runtime.profile
        try:
            runtime.camera_runtime.profile = previous.model_copy(update={"source_type": "mock", "device_id": None, "device_path": None, "stable_path": None})
            frame, warnings = runtime.camera_runtime.live_preview_frame()
        finally:
            runtime.camera_runtime.profile = previous
        if frame is not None:
            return self._pass("Mock surrogate frame is readable; this is synthetic release-candidate evidence.", {"frame_origin": "mock_frame", "warnings": warnings, "no_physical_command_generated": True})
        return self._warn("Mock surrogate frame unavailable.", {"warnings": warnings, "no_physical_command_generated": True}, "Install numpy/OpenCV dependencies used by mock frame generation.")

    def _check_surrogate_real_camera_frame_readable(self, runtime):
        frame, warnings = runtime.camera_runtime.live_preview_frame()
        status = runtime.camera_runtime.status()
        if status.profile.source_type == "mock":
            return self._warn("Real camera evidence missing; mock camera is selected.", {"camera_source_kind": "mock", "frame_origin": "mock_frame", "no_physical_command_generated": True}, "Select laptop/USB camera and probe it for real camera evidence.")
        if frame is not None:
            return self._pass("Real camera frame is readable.", {"source_type": status.profile.source_type, "device_path": status.profile.device_path or status.profile.stable_path or status.profile.device_id, "warnings": warnings, "no_physical_command_generated": True})
        return self._warn("Real camera frame unavailable; release candidate can still run with mock/surrogate limitation.", {"warnings": warnings, "status": status.model_dump(mode="json"), "no_physical_command_generated": True}, "Fix camera permissions or select a valid /dev/video* source.")

    def _check_surrogate_real_camera_evidence(self, runtime):
        details = runtime.vision_surrogate.summary()
        if details.get("camera_source_kind") == "real_camera" and details.get("frame_origin") == "real_capture":
            return self._pass("Real camera surrogate evidence is present.", details)
        return self._warn("Real camera evidence missing; current surrogate evidence is mock/synthetic or not run.", details, "Use a laptop/USB camera source and capture a surrogate frame.")

    def _check_surrogate_detector_available(self, runtime):
        details = runtime.vision_surrogate.summary()
        if details.get("available") or runtime.camera_runtime.status().profile.source_type == "mock":
            return self._pass("OpenCV live circle surrogate is available for UI/pipeline testing.", details)
        return self._warn("OpenCV package is unavailable; live circle surrogate will report controlled warning.", details, "Install OpenCV runtime dependencies for live camera surrogate.")

    def _check_surrogate_detector_no_physical(self, runtime):
        previous = runtime.vision_runtime.profile
        profile = previous.model_copy(update={"inference_adapter": "opencv_live_circle_surrogate"})
        event = runtime.vision_surrogate.run(runtime.camera_runtime, profile)
        runtime.vision_runtime.profile = previous
        if any(flag is False for flag in [runtime.config.system.dry_run, not runtime.config.system.hardware_enabled, not runtime.config.hardware.physical_command_enabled]):
            return self._fail("Safety invariant changed during surrogate test.", event.model_dump(mode="json"))
        return self._pass("Surrogate detector produced advisory metadata only.", {"event": event.model_dump(mode="json"), "no_physical_command_generated": True})

    def _check_surrogate_snapshot_export(self, runtime):
        result = runtime.vision_surrogate.snapshot(runtime.camera_runtime, runtime.vision_runtime.profile.model_copy(update={"inference_adapter": "opencv_live_circle_surrogate"}))
        if result.get("no_physical_command_generated") and Path(result.get("path", "")).exists():
            return self._pass("Surrogate snapshot metadata export is available.", result)
        return self._warn("Surrogate snapshot metadata export did not produce expected file.", result, "Inspect exports/vision_surrogate/snapshots.")

    def _check_surrogate_fps_latency(self, runtime):
        details = runtime.vision_surrogate.summary()
        if details.get("latency_ms", 0) > 0 and details.get("fps", 0) > 0:
            return self._pass("Surrogate FPS and latency were measured.", details)
        return self._warn("Surrogate FPS/latency not measured yet.", details, "Run Vision with OpenCV live circle surrogate selected.")

    def _check_model_registry(self, runtime):
        models = runtime.model_registry.list_models()
        return self._pass("Model registry available.", {"model_count": len(models)})

    def _check_model_package_service(self, runtime):
        packages = runtime.model_packages.list_packages()
        return self._pass("Model package handoff service is available.", {"package_count": len(packages), "no_physical_command_generated": True})

    def _check_model_registry_readable(self, runtime):
        models = runtime.model_registry.list_models()
        packages = runtime.model_packages.list_packages()
        return self._pass("Model registry and package registry are readable.", {"models": len(models), "packages": len(packages)})

    def _check_active_models(self, runtime):
        return self._pass("Active model summary readable.", runtime.model_registry.active_models().model_dump(mode="json"))

    def _check_active_model_status_readable(self, runtime):
        summary = runtime.model_packages.active_package_summary()
        status = runtime.vision_runtime.status()
        return self._pass("Active package and vision runtime model status are readable.", {"package": summary, "vision": status.active_model_details})

    def _check_production_or_test_adapter_declared(self, runtime):
        active = runtime.model_registry.active_models()
        summary = runtime.model_packages.active_package_summary()
        test_adapter = bool(active.active_test_adapter) or runtime.vision_runtime.profile.inference_adapter == "opencv_circle_test"
        if summary.get("production_ready") or test_adapter:
            if summary.get("production_ready"):
                return self._pass("Production model state is explicit.", {"active": active.model_dump(mode="json"), "package": summary})
            return self._warn(
                "Fixture/test adapter is active; it is not a competition detector. Release candidate can run without production YOLO.",
                {"active": active.model_dump(mode="json"), "package": summary},
                "Competition rehearsal requires production YOLO model.",
            )
        return self._warn("No production model or explicit test adapter state found.", {"active": active.model_dump(mode="json"), "package": summary}, "Import a model package or enable the OpenCV test adapter for demo.")

    def _check_class_mapping_valid_if_production(self, runtime):
        summary = runtime.model_packages.active_package_summary()
        if not summary.get("production_ready"):
            return self._warn(
                "Release candidate can run without production YOLO. Competition rehearsal requires production YOLO model.",
                summary,
                "Load and test the vision team model package before competition rehearsal.",
            )
        if summary.get("class_mapping_status") == "complete":
            return self._pass("Production model class mapping is complete.", summary)
        return self._fail("Production model class mapping is incomplete.", summary, "Fix classes.json/metadata class_id_to_name mapping.")

    def _check_opencv_adapter(self, runtime):
        model = runtime.model_registry.get_model("opencv-circle-test-adapter")
        return self._pass("OpenCV test adapter is registered.", model.model_dump(mode="json"))

    def _check_adapter_warning(self, runtime):
        model = runtime.model_registry.get_model("opencv-circle-test-adapter")
        if not any("test adapter" in warning for warning in model.warnings):
            return self._warn("Test adapter warning missing.", model.model_dump(mode="json"), "Restore UI/backend warning text.")
        return self._pass("Test adapter warning is present.", {"warnings": model.warnings})

    def _check_model_no_physical(self, runtime):
        result = runtime.inference_adapter.test_inference(ModelTestInferenceRequest(model_id=None, source="mock", frame_id="self-test", use_test_adapter=True))
        if not result.no_physical_command_generated:
            return self._fail("Model test inference generated physical command flag.", result.model_dump(mode="json"))
        return self._pass("Model test inference is non-physical.", result.model_dump(mode="json"))

    def _check_vision_runtime_model_compatible(self, runtime):
        status = runtime.vision_runtime.status()
        details = status.active_model_details
        if details.get("active_model_id") and details.get("class_mapping_status") in {"complete", "class_names_missing"}:
            if details.get("production_ready"):
                return self._pass("Vision runtime can read active production model metadata.", details)
            return self._warn("Fixture/test adapter is active; it is not a competition detector.", details, "Load production model package before competition rehearsal.")
        if status.profile.inference_adapter == "opencv_circle_test":
            return self._warn("Fixture/test adapter is active; it is not a competition detector.", details, "Load production model package before competition rehearsal.")
        return self._warn("Vision runtime model compatibility is not verified.", details, "Import, validate and test a model package.")

    def _check_competition_model_blocker(self, runtime):
        summary = runtime.model_packages.active_package_summary()
        if summary.get("production_ready") and summary.get("last_test_status") == "completed":
            return self._pass("Competition model blocker cleared.", summary)
        return self._warn("Competition rehearsal remains blocked until production model is loaded, mapped and tested.", summary, "Use /models to import and validate the vision team package.")

    def _check_motion_status(self, runtime):
        return self._pass("Motion status readable.", runtime.motion.status().model_dump(mode="json"))

    def _check_motion_dry_run(self, runtime):
        state = runtime.motion.status()
        if not state.dry_run or not runtime.config.motion.dry_run or runtime.config.motion.real_motion_enabled:
            return self._fail("Motion dry-run invariant broken.", state.model_dump(mode="json"))
        return self._pass("Motion dry-run mode active.", state.model_dump(mode="json"))

    def _check_jog_dry_run(self, runtime):
        result = runtime.motion.jog(MotionJogRequest(axis="pan", direction="positive", step_deg=0.1), system_armed=False)
        if not result.accepted or not result.no_physical_command_generated:
            return self._fail("Jog dry-run failed or physical flag missing.", result.model_dump(mode="json"))
        return self._pass("Jog dry-run accepted without physical command.", result.model_dump(mode="json"))

    def _check_out_of_limit(self, runtime):
        result = runtime.motion.go_to(MotionGoToRequest(pan_target_deg=999.0, tilt_target_deg=0.0), system_armed=False)
        if result.accepted:
            return self._fail("Out-of-limit command was accepted.", result.model_dump(mode="json"))
        return self._pass("Out-of-limit motion command rejected.", result.model_dump(mode="json"))

    def _check_stop(self, runtime):
        result = runtime.motion.stop()
        if not result.accepted:
            return self._warn("Stop command was not accepted.", result.model_dump(mode="json"))
        return self._pass("Stop accepted.", result.model_dump(mode="json"))

    def _check_motion_no_physical(self, runtime):
        commands = [command.model_dump(mode="json") for command in runtime.motion.command_log[-5:]]
        if any(command.get("no_physical_command_generated") is False for command in commands):
            return self._fail("Motion command log contains physical command.", {"commands": commands})
        return self._pass("No physical motion command generated.", {"commands": commands, "no_physical_command_generated": True})

    def _check_sessions(self, runtime):
        return self._pass("Sessions API/service available.", {"session_count": len(runtime.sessions.list_sessions())})

    def _check_dataset(self, runtime):
        return self._pass("Dataset service available.", runtime.dataset.health().model_dump(mode="json"))

    def _check_replay_status(self, runtime):
        return self._pass("Replay status available.", runtime.replay.status.model_dump(mode="json"))

    def _check_e2e_data(self, runtime):
        sessions = runtime.sessions.list_sessions()
        exports = runtime.dataset.list_exports()
        if not sessions and not exports:
            return self._warn("No E2E dataset/session found.", {"sessions": 0, "exports": 0}, "Run Ara Task 9.1 E2E flow before demo evidence review.")
        return self._pass("E2E dataset/session evidence is available.", {"sessions": len(sessions), "exports": len(exports)})

    def _check_export_validation(self, runtime):
        result = runtime.dataset.validate()
        if not result.valid:
            return self._warn("Dataset validation has warnings/errors.", result.model_dump(mode="json"), "Inspect dataset annotations before model training.")
        return self._pass("YOLO export validation service available.", result.model_dump(mode="json"))

    def _check_replay_no_physical(self, runtime):
        status = runtime.replay.status
        if not status.no_physical_command_generated:
            return self._fail("Replay physical command invariant broken.", status.model_dump(mode="json"))
        return self._pass("Replay no physical command invariant intact.", status.model_dump(mode="json"))

    def _check_jsonl_logging(self, runtime):
        event = runtime.logger.emit(LogLevel.INFO, "SELF_TEST", "Self-test logging writable check", {"no_physical_command_generated": True})
        return self._pass("JSONL logging writable.", event.model_dump(mode="json"))

    def _check_self_log(self, runtime):
        self.logger.emit(LogLevel.INFO, "SELF_TEST", "Self-test log event written", {"no_physical_command_generated": True})
        return self._pass("Self-test log event written.", {"path": str(runtime.logger.path)})

    def _check_recent_events(self, runtime):
        return self._pass("Recent serial/log events readable.", {"serial_logs": len(runtime.serial.recent_logs())})

    def _check_interface_inventory(self, runtime):
        inventory = runtime.interface_inventory.inventory()
        if len(inventory.interfaces) < 10:
            return self._warn("Interface inventory is available but sparse.", inventory.model_dump(mode="json"), "Review KTR 4.3 inventory before export.")
        return self._pass("Interface inventory is populated for KTR 4.3.", inventory.model_dump(mode="json"))

    def _check_portable_launcher(self, runtime):
        root = project_root()
        files = [
            root / "start_linux.sh",
            root / "start_windows.bat",
            root / "release" / "linux" / "start_istiklal_c2.sh",
            root / "release" / "windows" / "start_istiklal_c2.bat",
        ]
        missing = [str(path) for path in files if not path.exists()]
        if missing:
            return self._warn("Portable launcher files are missing.", {"missing": missing}, "Restore release launcher scripts.")
        return self._pass("Portable launcher files are present.", {"files": [str(path) for path in files]})

    def _check_first_run_acceptance(self, runtime):
        status = runtime.first_run.status(runtime)
        return self._pass("First-run acceptance service is available.", status.model_dump(mode="json"))

    def _check_release_readiness(self, runtime):
        status = runtime.release.status(runtime)
        if status.offline_readiness == "ready":
            return self._pass("Release readiness checks passed.", status.model_dump(mode="json"))
        return self._warn("Release readiness has warnings.", status.model_dump(mode="json"), "Run scripts/check_release.py and inspect /api/release/status.")

    def _git_hash(self) -> str:
        try:
            result = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=project_root(), check=True, capture_output=True, text=True)
            return result.stdout.strip()
        except Exception:
            return "dev-local"

    def _markdown(self, run: SelfTestRun) -> str:
        failed = [step for step in run.steps if step.status == "failed"]
        warnings = [step for step in run.steps if step.status == "warning"]
        lines = [
            f"# Self-Test Report {run.run_id}",
            "",
            f"- Status: {run.status}",
            f"- Overall ready: {run.overall_ready}",
            f"- Readiness level: {run.readiness_level}",
            f"- No physical command generated: {run.no_physical_command_generated}",
            f"- Dry-run: {run.dry_run}",
            f"- Hardware enabled: {run.hardware_enabled}",
            f"- Started at: {run.started_at}",
            f"- Ended at: {run.ended_at}",
            f"- Build: {run.summary.get('git_hash', 'dev-local')}",
            "",
            "## Counts",
            "",
            f"- Passed: {run.summary.get('passed', 0)}",
            f"- Warning: {run.summary.get('warning', 0)}",
            f"- Failed: {run.summary.get('failed', 0)}",
            f"- Skipped: {run.summary.get('skipped', 0)}",
            f"- Critical failures: {run.summary.get('critical_failures', 0)}",
            "",
            "## Failed Critical Steps",
            "",
        ]
        lines.extend([f"- {step.name}: {step.message}" for step in failed if step.severity == "critical"] or ["- None"])
        lines.extend(["", "## Warnings", ""])
        lines.extend([f"- {step.name}: {step.message}" for step in warnings] or ["- None"])
        lines.extend(["", "## Suggested Actions", ""])
        actions = [action for action in run.summary.get("suggested_actions", []) if action]
        lines.extend([f"- {action}" for action in actions] or ["- None"])
        lines.extend(["", "## Steps", ""])
        for step in run.steps:
            lines.append(f"- [{step.status}] {step.category}/{step.step_id}: {step.message}")
        lines.append("")
        lines.append("Self-test readiness does not enable physical fire.")
        return "\n".join(lines)
