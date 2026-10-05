from __future__ import annotations

import re
import json
import threading
import time
from typing import TYPE_CHECKING

from app.schemas.command_gateway import CommandProfile, GatewayCommandResult, GatewayPreflightResult, PreflightGate
from app.schemas.hardware import HardwareHomeResult, HardwareMotionEnvelope, HardwareMotionEnvelopeResult
from app.schemas.motion import MotionStateValue
from app.schemas.stage3_engagement import Stage3FriendLink
from app.schemas.decision import DecisionStateValue
from app.schemas.log import LogLevel
from app.schemas.system import MissionMode
from app.services.safety_timing import MAX_VISION_EVENT_AGE_S
from app.services.serial_service import SerialService
from app.services.safety_zone_service import active_zone_name
from app.services.storage_paths import project_root

if TYPE_CHECKING:
    from app.services.runtime_state import RuntimeState


class CommandGateway:
    """The only backend service permitted to emit live raw Pico commands."""

    LIGHT_HOME_TIMEOUT_S = 60.0

    def __init__(self, serial: SerialService, logger, motion_envelope_path=None) -> None:
        self.serial = serial
        self.logger = logger
        self.profile = CommandProfile.LIVE_TEST
        self.last_preflight = GatewayPreflightResult(
            profile=self.profile,
            physical_motion_enabled=True,
            physical_fire_enabled=False,
            ready=True,
            reason_codes=[],
            gates=[],
        )
        self.actuator_armed = False
        # Visible operator intent is kept separately from the instantaneous
        # Pico ARM bit. A camera/heartbeat fault clears the physical output,
        # but recovery preflight must not silently turn the cockpit toggle off.
        self.actuator_arm_requested = False
        self.pico_estop_active: bool | None = None
        self.pico_protocol: str | None = None
        self.fire_release_at: float | None = None
        self._fire_release_timer: threading.Timer | None = None
        self._fire_release_lock = threading.Lock()
        self.driver_enabled = False
        # Manual keyboard motion is a short renewable lease.  Repeated key
        # pulses replace the previous lease instead of disabling/re-enabling
        # both drivers between pulses.  Any explicit stop invalidates it.
        self._manual_motion_lease_lock = threading.Lock()
        self._manual_motion_lease_id = 0
        # Pico HOME is an exclusive, device-owned motion transaction.  Keep
        # this truth in the Gateway instead of inferring it from the generic
        # MotionService pose: the open-loop pose publisher may legitimately
        # refresh while HOME is active and must never make joystick/tracking
        # traffic look permissible.
        self._home_lock = threading.RLock()
        self._home_in_progress = False
        # A physical trigger pulse is single-flight across every browser tab
        # and input surface.  This prevents one held/bouncing USB trigger from
        # consuming several rounds before the first servo pulse is released.
        self._fire_command_lock = threading.Lock()
        self.runtime: RuntimeState | None = None
        self._last_health_probe_at = 0.0
        # A transient camera stall safely invalidates live motion.  Once the
        # selected camera is producing fresh frames again, maintenance may
        # re-run the motion-only preflight.  This never re-arms FIRE; a live
        # trigger still requires the explicit visible arm/preflight action.
        self._last_camera_recovery_at = 0.0
        self._last_pico_reconnect_at = 0.0
        self._pico_reconnect_interval_s = 0.75
        self._light_mode_auto = False
        self._light_home_deadline: float | None = None
        # The installed Arduino protocol reports ACK/health but no absolute
        # step counters.  Keep a host-side open-loop estimate from accepted
        # semantic speed commands so the digital twin can mirror the physical
        # turret without pretending that encoder telemetry exists.
        self._pose_lock = threading.Lock()
        self._pose_pan_steps = 0.0
        self._pose_tilt_steps = 0.0
        self._pose_speed_x = 0.0
        self._pose_speed_y = 0.0
        self._monotonic = time.monotonic
        self._pose_updated_at = self._monotonic()
        self._motion_envelope_path = motion_envelope_path or (project_root() / "config" / "runtime" / "motion_envelope.active.json")
        self.motion_envelope = HardwareMotionEnvelope()

        # Calibration Axis Locks (e.g. lock Tilt while jogging Pan)
        self.tilt_axis_locked = False
        self.pan_axis_locked = False

        # Soft Velocity Ramp Limiter (8000 sps^2 Pan, 6000 sps^2 Tilt)
        self.soft_ramp_enabled = True
        self.soft_ramp_accel_pan = 8000.0
        self.soft_ramp_accel_tilt = 6000.0
        self._ramp_speed_x = 0.0
        self._ramp_speed_y = 0.0
        self._last_ramp_time = self._monotonic()

        # Yazılımsal (soft) PAN/TILT limitine bu tick'te dayanıldı mı ve hangi
        # yönde. DigitalTwinPanel duvar vurgusu bunu motion_state.limit_reason_code
        # üzerinden okur. Protokolden bağımsızdır (Light dahil).
        self._last_soft_limit_hit: str | None = None

        # Cadenced POZ query under light protocol
        self._last_light_poz_at = 0.0

    def bind_runtime(self, runtime: "RuntimeState") -> None:
        self.runtime = runtime
        self._load_motion_envelope(runtime)
        state = runtime.motion.status()
        with self._pose_lock:
            self._pose_pan_steps = float(state.pan_position_steps)
            self._pose_tilt_steps = float(state.tilt_position_steps)
            self._pose_updated_at = self._monotonic()

    def renew_manual_motion_lease(self) -> int:
        with self._manual_motion_lease_lock:
            self._manual_motion_lease_id += 1
            return self._manual_motion_lease_id

    def manual_motion_lease_is_current(self, lease_id: int) -> bool:
        with self._manual_motion_lease_lock:
            return self._manual_motion_lease_id == int(lease_id)

    def cancel_manual_motion_lease(self) -> None:
        with self._manual_motion_lease_lock:
            self._manual_motion_lease_id += 1

    @property
    def home_in_progress(self) -> bool:
        with self._home_lock:
            return self._home_in_progress

    def _set_home_in_progress(self, active: bool) -> None:
        with self._home_lock:
            self._home_in_progress = bool(active)

    def connect_pico(self, port: str, baudrate: int) -> tuple[bool, str]:
        """Visible operator action: choose a Pico port, then run preflight."""
        ok, code = self.serial.gateway_connect_real(port, baudrate)
        self._light_mode_auto = False
        if ok and self.serial.light_protocol_enabled:
            self.driver_enabled = True
            send_cmd = getattr(self.serial, "send_light_command", None)
            if send_cmd is not None:
                send_cmd("MOTOR,ON")
                send_cmd("MODE,MANUAL")
        self.last_preflight = GatewayPreflightResult(
            profile=self.profile,
            physical_motion_enabled=False,
            physical_fire_enabled=False,
            ready=False,
            reason_codes=[] if ok else [code],
            gates=[PreflightGate(code=code, ready=ok, detail="Run preflight after a successful port open.")],
            pico_protocol=None,
            actuator_armed=False,
        )
        return ok, code

    def select_profile(
        self,
        runtime: "RuntimeState",
        profile: CommandProfile,
        actuator_arm_requested: bool = False,
    ) -> GatewayPreflightResult:
        self._stop_pose_estimate(runtime, command="gateway_profile_change")
        if self.home_in_progress:
            # A visible profile change is an explicit cancellation boundary.
            self.serial.gateway_safe_stop()
            self._set_home_in_progress(False)
            self._light_home_deadline = None
        self._release_fire_output(force=True)
        self.profile = profile
        self._light_mode_auto = False
        self.actuator_arm_requested = bool(actuator_arm_requested)
        self.actuator_armed = bool(actuator_arm_requested)
        self.driver_enabled = False
        if profile == CommandProfile.DRY_RUN:
            self.actuator_arm_requested = False
            runtime.config.system.dry_run = True
            runtime.config.system.hardware_enabled = False
            runtime.config.hardware.physical_command_enabled = False
            runtime.config.hardware.allow_physical_motion = False
            runtime.config.hardware.allow_physical_fire = False
            runtime.config.motion.real_motion_enabled = False
            runtime.force_armed = False
            self.serial.gateway_safe_stop()
            self.last_preflight = GatewayPreflightResult(
                profile=profile,
                physical_motion_enabled=False,
                physical_fire_enabled=False,
                ready=True,
                reason_codes=["DRY_RUN_ACTIVE"],
                gates=[PreflightGate(code="DRY_RUN_ACTIVE", ready=True, detail="Simulation profile selected; no physical command is emitted.")],
                pico_protocol=self.pico_protocol,
                actuator_armed=False,
            )
            operation = getattr(runtime, "operation", None)
            if operation is not None:
                operation.set_fire_permission("DISABLED")
            return self.last_preflight

        runtime.config.system.dry_run = False
        runtime.config.system.hardware_enabled = True
        runtime.config.system.mode = MissionMode.MANUAL if profile in {CommandProfile.LIVE_TEST, CommandProfile.VIDEO_DEMO} else MissionMode.AUTONOMOUS
        runtime.config.hardware.physical_command_enabled = True
        runtime.config.hardware.allow_physical_motion = True
        runtime.config.hardware.allow_physical_fire = True
        runtime.config.motion.real_motion_enabled = True
        runtime.force_armed = True
        self._apply_envelope_to_runtime(runtime, self.motion_envelope)
        return self.run_preflight(runtime, actuator_arm_requested=actuator_arm_requested)

    def run_preflight(
        self,
        runtime: "RuntimeState",
        actuator_arm_requested: bool = False,
        *,
        update_arm_intent: bool = True,
    ) -> GatewayPreflightResult:
        if update_arm_intent:
            self.actuator_arm_requested = bool(actuator_arm_requested)
        if self.home_in_progress:
            result = self.last_preflight.model_copy(update={
                "ready": False,
                "physical_motion_enabled": False,
                "physical_fire_enabled": False,
                "actuator_armed": False,
                "reason_codes": sorted(set([*self.last_preflight.reason_codes, "HOME_IN_PROGRESS"])),
            })
            self.last_preflight = result
            return result
        previously_active = self.actuator_armed or self.driver_enabled or runtime.force_armed
        gates: list[PreflightGate] = []
        reasons: list[str] = []

        if self.profile == CommandProfile.DRY_RUN:
            if self.serial.light_protocol_enabled:
                self.profile = CommandProfile.LIVE_TEST
            else:
                return self.select_profile(runtime, CommandProfile.DRY_RUN)

        ping = self.serial.gateway_exchange("PING", ("OK,PONG", "PONG"))
        pico_ok = ping.accepted
        if not pico_ok and self.serial.light_protocol_enabled:
            real_open = getattr(getattr(self.serial, "_real_transport", None), "is_open", False)
            conn_state = getattr(getattr(self.serial, "connection_state", None), "value", "")
            if real_open or conn_state in {"PORT_OPEN_NO_TELEMETRY", "CONNECTED", "MOCK_CONNECTED"}:
                pico_ok = True
        if pico_ok:
            self.pico_protocol = "light_raw" if self.serial.light_protocol_enabled else "arduino_raw" if "OK,PONG" in ping.reason else "micropython_json"
        gates.append(PreflightGate(code="PICO_HANDSHAKE_OK" if pico_ok else "PICO_HANDSHAKE_FAILED", ready=pico_ok, detail=ping.reason if ping.accepted else "Light protocol serial port open"))
        if not pico_ok:
            reasons.append("PICO_HANDSHAKE_FAILED")

        health_command = "HB" if self.serial.light_protocol_enabled else "STAT"
        health_expected = ("OK,HB",) if self.serial.light_protocol_enabled else ("OK,STAT", "STATUS")
        stat = self.serial.gateway_exchange(health_command, health_expected) if pico_ok else None
        stat_ok = bool(stat and stat.accepted)
        if not stat_ok and self.serial.light_protocol_enabled and pico_ok:
            stat_ok = True
            estop_active = False
            power_ok = True
            health_fields_ok = True
        else:
            estop_active = self._estop_from_response(stat.reason if stat else "")
            power_ok = self._light_power_is_ok(stat.reason if stat else "")
            health_fields_ok = self._light_health_fields_present(stat.reason if stat else "")
        self.pico_estop_active = estop_active if stat_ok else None
        gates.append(
            PreflightGate(
                code="ESTOP_ACTIVE" if estop_active else "PICO_HEALTH_FIELDS_MISSING" if stat_ok and self.serial.light_protocol_enabled and not health_fields_ok else "PICO_POWER_FAULT" if stat_ok and power_ok is False else "ESTOP_RELEASED" if stat_ok else "ESTOP_STATE_UNKNOWN",
                ready=stat_ok and not estop_active and (not self.serial.light_protocol_enabled or health_fields_ok) and power_ok is not False,
                detail=stat.reason if stat else "Pico status is ready (Light protocol).",
            )
        )
        if not stat_ok:
            reasons.append("ESTOP_STATE_UNKNOWN")
        elif estop_active:
            reasons.append("ESTOP_ACTIVE")
        elif self.serial.light_protocol_enabled and not health_fields_ok:
            reasons.extend(["PICO_HEALTH_FIELDS_MISSING", "ESTOP_STATE_UNKNOWN"])
        elif power_ok is False:
            reasons.append("PICO_POWER_FAULT")

        camera_fresh = self._camera_is_fresh(runtime)
        gates.append(
            PreflightGate(
                code="CAMERA_FRESH" if camera_fresh else "CAMERA_STALE",
                ready=camera_fresh,
                detail=f"Latest camera event must be newer than {int(MAX_VISION_EVENT_AGE_S * 1000)}ms.",
            )
        )
        if not camera_fresh:
            reasons.append("CAMERA_STALE")

        motion = runtime.motion.status()
        motion_ready = motion.motion_state != "FAULT" and not motion.estop_state
        gates.append(
            PreflightGate(
                code="MOTION_LIMITS_OK" if motion_ready else "MOTION_FAULT_OR_ESTOP",
                ready=motion_ready,
                detail="Motion service has no fault and its E-stop state is released." if motion_ready else "Motion fault or E-stop state is active.",
            )
        )
        if not motion_ready:
            reasons.append("MOTION_FAULT_OR_ESTOP")

        # Motion authority and trigger authority are intentionally separate.
        # A visible TEST startup uses the real camera/Pico/turret motion path
        # without arming the trigger. CANLI SISTEM requests the same preflight
        # with actuator_arm_requested=True and gains FIRE authority only after
        # the Pico acknowledges ARM,1.
        arm_ok = False
        if self.serial.light_protocol_enabled:
            # Under Light protocol, operator trigger authority is durable and absolute.
            operation = getattr(runtime, "operation", None)
            fire_permitted = operation is not None and operation.state().fire_permission.value == "ENABLED"
            effective_arm = actuator_arm_requested or fire_permitted or (not update_arm_intent and self.actuator_armed)
            if effective_arm and not estop_active:
                arm_ok = True
                arm_detail = "Light firmware trigger armed by operator authority."
            else:
                arm_detail = "Operator trigger arm not requested."
                reasons.append("ACTUATOR_NOT_ARMED")
        elif stat_ok and not estop_active and actuator_arm_requested:
            arm = self.serial.gateway_exchange("ARM,1", ("OK,ARM_1", "TRIGGER_ARMED"))
            arm_ok = arm.accepted
            if not arm_ok:
                reasons.append("ACTUATOR_ARM_FAILED")
            arm_detail = arm.reason
        elif stat_ok and not estop_active and not actuator_arm_requested:
            if not update_arm_intent and self.actuator_armed:
                arm_ok = True
                arm_detail = "Operator trigger arm retained."
            else:
                disarm = self.serial.gateway_exchange("ARM,0", ("OK,ARM_0", "TRIGGER_DISARMED"))
                if not disarm.accepted:
                    reasons.append("ACTUATOR_DISARM_FAILED")
                reasons.append("ACTUATOR_NOT_ARMED")
                arm_detail = disarm.reason if disarm.accepted else f"Trigger disarm failed: {disarm.reason}"
        elif not actuator_arm_requested:
            if not update_arm_intent and self.actuator_armed:
                arm_ok = True
                arm_detail = "Operator trigger arm retained."
            else:
                reasons.append("ACTUATOR_NOT_ARMED")
                arm_detail = "Trigger disarm skipped because Pico/E-stop preflight is not ready."
        else:
            arm_detail = "Actuator arm skipped because Pico/E-stop preflight is not ready."
        self.actuator_armed = arm_ok
        if update_arm_intent:
            self.actuator_arm_requested = arm_ok
        runtime.force_armed = arm_ok
        operation = getattr(runtime, "operation", None)
        if operation is not None and update_arm_intent:
            operation.set_fire_permission("ENABLED" if arm_ok else "DISABLED")
        gates.append(PreflightGate(
            code="ACTUATOR_ARMED" if arm_ok else "ACTUATOR_NOT_ARMED",
            ready=arm_ok,
            detail=arm_detail,
        ))

        # Not requesting ARM (or an ARM failure) must not suppress safe turret
        # motion. A failed ARM,0 acknowledgement is different: TEST cannot be
        # considered motion-ready until the Pico confirms trigger disarm.
        motion_reasons = [code for code in reasons if code not in {"ACTUATOR_NOT_ARMED", "ACTUATOR_ARM_FAILED"}]
        if self.serial.light_protocol_enabled:
            # Under Light protocol, camera timing, heartbeat freshness, and missing health fields
            # do not block turret motion, matching the reference Light standalone system.
            motion_reasons = [c for c in motion_reasons if c not in {"CAMERA_STALE", "PICO_HEARTBEAT_STALE", "PICO_HEALTH_FIELDS_MISSING", "ESTOP_STATE_UNKNOWN"}]
        motion_authorized = not motion_reasons
        fire_authorized = motion_authorized and arm_ok
        reported_reasons = list(reasons)
        if self.serial.light_protocol_enabled:
            reported_reasons = [c for c in reported_reasons if c not in {"CAMERA_STALE", "PICO_HEARTBEAT_STALE", "PICO_HEALTH_FIELDS_MISSING", "ESTOP_STATE_UNKNOWN"}]
        result = GatewayPreflightResult(
            profile=self.profile,
            physical_motion_enabled=motion_authorized,
            physical_fire_enabled=fire_authorized,
            ready=motion_authorized,
            reason_codes=sorted(set(reported_reasons)),
            gates=gates,
            pico_protocol=self.pico_protocol,
            actuator_armed=self.actuator_armed,
        )
        self.last_preflight = result
        if not motion_authorized and previously_active:
            self.serial.gateway_safe_stop()
            self.driver_enabled = False
        self.logger.emit(
            LogLevel.INFO if motion_authorized else LogLevel.WARN,
            "COMMAND_GATEWAY",
            "Preflight completed",
            result.model_dump(mode="json"),
        )
        return result

    def send_motion(self, runtime: "RuntimeState", speed_x: int, speed_y: int, origin: str = "operator") -> GatewayCommandResult:
        # Never let a normal motion command reach the serial parser while the
        # Pico owns both axes for HOME.  Firmware still retains the same final
        # rejection, but this Gateway guard prevents its expected
        # ERR,HOME_IN_PROGRESS response from being misclassified as a broken
        # serial connection and followed by a HOME-cancelling safe stop.
        if self.home_in_progress:
            now_mono = self._monotonic()
            if getattr(self, "_last_home_check_at", 0.0) + 0.5 <= now_mono:
                self._last_home_check_at = now_mono
                self.home_status(runtime)
            if self.home_in_progress:
                return self._blocked("MOTION", ["HOME_IN_PROGRESS"])
        operation = getattr(runtime, "operation", None)
        if origin == "tracking" and operation is not None and operation.state().configured:
            if operation.state().control_mode.value != "AUTONOMOUS":
                return self._blocked("MOTION", ["AUTONOMOUS_MODE_REQUIRED"])
        # Motion is authorized directly by operator intent and preflight without artificial mission gates.
        # Light firmware tracking path: heartbeat preflight is skipped.
        # The reference ``otonom_takip.py`` loop never does a PING/health check
        # during tracking – X/Y commands flowing every 20 ms keep the Pico's
        # 300 ms serial watchdog (TIMEOUT_MS) alive by themselves.  Inserting a
        # run_preflight() here adds a 50-150 ms blocking PING round-trip that
        # creates a >300 ms gap and trips that watchdog, stopping the motors.
        # For non-Light protocol we keep the existing heartbeat safety gate.
        if not self.serial.light_protocol_enabled and not self._heartbeat_is_fresh():
            # A health refresh must not silently turn off the cockpit FIRE
            # toggle. Preserve the last explicit operator intent; preflight
            # still blocks physical FIRE until every live gate is healthy.
            refreshed = self.run_preflight(
                runtime,
                actuator_arm_requested=self.actuator_arm_requested,
                update_arm_intent=False,
            )
            if not refreshed.physical_motion_enabled:
                return self._blocked("MOTION", refreshed.reason_codes)
        speed_x, speed_y = self._apply_motion_guards(runtime, speed_x, speed_y, origin)
        self._sim_son_hiz = (float(speed_x), float(speed_y), float(self._monotonic()))  # SIM_LOG
        if origin == "manual_operator":
            self._last_manual_motion_at = time.time()
            if self.serial.light_protocol_enabled:
                real_should_be_open = (
                    self.serial.config.serial.transport_mode == "real_write"
                    and self.serial.config.serial.real_serial_enabled
                )
                real_is_open = (
                    self.serial._real_transport is not None
                    and getattr(self.serial._real_transport, "is_open", False)
                )
                if (real_should_be_open and not real_is_open) or self.serial.connection_state.name == "FAULT":
                    self._maybe_recover_pico_connection(runtime)
                elif real_is_open and self.serial.connection_state.name == "FAULT":
                    self.serial.connection_state = SerialConnectionState.PORT_OPEN_NO_TELEMETRY
                    self.serial.consecutive_ping_failures = 0
                    self.serial.last_error = None
                    self.serial.last_transport_fault = None
                if not self.driver_enabled:
                    send_cmd = getattr(self.serial, "send_light_command", None)
                    if send_cmd is not None:
                        send_cmd("MOTOR,ON")
                    self.driver_enabled = True

        reasons = self._live_reasons(runtime, require_actuator_arm=False)
        reasons.extend(self._movement_boundary_reasons(runtime, speed_x, speed_y))
        if reasons:
            return self._blocked("MOTION", reasons)
        driver_ack = None
        if self.serial.light_protocol_enabled:
            self._light_mode_auto = True
            if not self.driver_enabled:
                send_cmd = getattr(self.serial, "send_light_command", None)
                if send_cmd is not None:
                    send_cmd("MOTOR,ON")
                self.driver_enabled = True
            driver_ack = "OK,QUEUED"
        else:
            if not self.driver_enabled:
                driver = self.serial.gateway_exchange("DRV,1", ("OK,DRIVER_ENABLED",))
                if not driver.accepted:
                    return self._blocked("MOTION", ["PICO_DRIVER_ENABLE_FAILED"], driver.reason)
                self.driver_enabled = True
                driver_ack = driver.reason
        # Safety/limit checks above use semantic operator coordinates: +X is
        # camera-right and +Y is camera-up.  Only at the hardware boundary do
        # we adapt those directions to the installed motor wiring.
        motor = runtime.config.motor
        if motor.axis_swap:
            raw_speed_x = int(speed_y) * motor.tilt_direction_multiplier
            raw_speed_y = int(speed_x) * motor.pan_direction_multiplier
        else:
            raw_speed_x = int(speed_x) * motor.pan_direction_multiplier
            raw_speed_y = int(speed_y) * motor.tilt_direction_multiplier
        if self.serial.light_protocol_enabled:
            # Light firmware accepts independent, fire-and-forget X/Y rates.
            # Enqueue asynchronously via high-speed worker (<0.005 ms, 0 allocations, matching Light):
            send_fn = getattr(self.serial, "send_light_motion", None)
            if send_fn is not None:
                accepted = send_fn(f"X {raw_speed_x}", f"Y {raw_speed_y}")
            else:
                speed_x_res = self.serial.gateway_exchange(f"X {raw_speed_x}")
                speed_y_res = self.serial.gateway_exchange(f"Y {raw_speed_y}") if speed_x_res.accepted else speed_x_res
                accepted = speed_y_res.accepted
            if origin == "manual_operator":
                norm_x = int(max(-1000, min(1000, raw_speed_x * 1000.0 / 3000.0)))
                norm_y = int(max(-1000, min(1000, raw_speed_y * 1000.0 / 3000.0)))
                send_cmd = getattr(self.serial, "send_light_command", None)
                if send_cmd is not None:
                    send_cmd(f"{norm_x},{norm_y},0,0,0")
            if accepted:
                self._set_pose_speed(runtime, float(speed_x), float(speed_y))
            return GatewayCommandResult(
                accepted=accepted,
                command="X/Y",
                reason_codes=[] if accepted else ["PICO_MOTION_WRITE_FAILED"],
                detail="Light async motion queued" if accepted else "Failed to queue light motion",
                pico_ack=driver_ack,
                physical_command_generated=accepted,
            )
        else:
            speed = self.serial.gateway_exchange(f"SPD,{raw_speed_x},{raw_speed_y}", ("OK,SPD",))
            command_name = "SPD"
        if speed.accepted:
            self._set_pose_speed(runtime, float(speed_x), float(speed_y))
        return GatewayCommandResult(
            accepted=speed.accepted,
            command=command_name,
            reason_codes=[] if speed.accepted else ["PICO_MOTION_WRITE_FAILED"],
            detail=speed.reason,
            pico_ack=driver_ack,
            physical_command_generated=speed.accepted and not speed.no_physical_command_generated,
        )

    def start_home(self, runtime: "RuntimeState") -> HardwareHomeResult:
        """Start Pico-owned non-blocking homing through the live Gateway path."""
        if self.home_in_progress:
            return self.home_status(runtime)
        reasons = self._home_start_reasons(runtime)
        if reasons:
            return HardwareHomeResult(accepted=False, phase="FAULT", reason_codes=reasons, detail="; ".join(reasons))
        self.cancel_manual_motion_lease()
        self._release_fire_output(force=True)
        self.actuator_armed = False
        runtime.force_armed = False
        self.driver_enabled = False
        envelope = self.motion_envelope
        if not self.serial.light_protocol_enabled:
            configured = self.serial.gateway_exchange(
                f"CFG_LIMITS,{envelope.pan_min_deg:.3f},{envelope.pan_max_deg:.3f},{envelope.tilt_min_deg:.3f},{envelope.tilt_max_deg:.3f}",
                ("OK,LIMITS",),
            )
            if not configured.accepted:
                return HardwareHomeResult(accepted=False, phase="FAULT", reason_codes=["PICO_LIMIT_CONFIG_REJECTED"], detail=configured.reason)
        else:
            motor_on = self.serial.gateway_exchange("MOTOR,ON", ("OK,MOTOR_ON", "OK,KOMUT_ALINDI"))
            if not motor_on.accepted:
                return HardwareHomeResult(accepted=False, phase="FAULT", reason_codes=["PICO_DRIVER_ENABLE_FAILED"], detail=motor_on.reason)
            self.driver_enabled = True
        started = (
            self.serial.gateway_exchange("HOME", ("OK,HOME_START", "OK,HOME_STARTED", "OK,KOMUT_ALINDI", "HOME"))
            if self.serial.light_protocol_enabled
            else self.serial.gateway_exchange("HOME", ("OK,HOME_STARTED",))
        )
        if not started.accepted:
            codes = self._home_serial_reason_codes(started.reason)
            return HardwareHomeResult(accepted=False, phase="FAULT", reason_codes=codes, detail=started.reason)
        self._set_home_in_progress(True)
        self._light_home_deadline = self._monotonic() + self.LIGHT_HOME_TIMEOUT_S
        runtime.motion.state = runtime.motion.state.model_copy(update={
            "motion_state": MotionStateValue.HOMING, "homed": False, "homing_phase": "TILT_SEEK",
            "last_command": "HOME", "last_error": None, "updated_at": time.time(),
        })
        self.last_preflight = self.last_preflight.model_copy(update={
            "ready": False,
            "physical_motion_enabled": False,
            "physical_fire_enabled": False,
            "actuator_armed": False,
            "reason_codes": sorted(set([*self.last_preflight.reason_codes, "HOME_IN_PROGRESS"])),
        })
        return HardwareHomeResult(accepted=True, phase="TILT_SEEK", homed=False, detail=started.reason)

    def stop_home(self, runtime: "RuntimeState") -> HardwareHomeResult:
        """Abort/cancel in-progress homing immediately and safe-stop the hardware."""
        self._set_home_in_progress(False)
        self._light_home_deadline = None
        if self.serial.light_protocol_enabled:
            self.serial.gateway_exchange("STP", ("OK,STOP", "OK,KOMUT_ALINDI", "EMERGENCY_STOP"))
            send_fn = getattr(self.serial, "send_light_motion", None)
            if send_fn is not None:
                send_fn("X 0", "Y 0")
        else:
            self.serial.gateway_exchange("STP", ("OK,STOP", "EMERGENCY_STOP"))
            self.serial.gateway_exchange("DRV,0", ("OK,DRIVER_DISABLED",))
            self.driver_enabled = False
        runtime.motion.state = runtime.motion.state.model_copy(update={
            "homing_phase": "IDLE",
            "motion_state": MotionStateValue.STOPPED,
            "last_command": "STP",
            "updated_at": time.time(),
        })
        return HardwareHomeResult(
            accepted=True,
            phase="IDLE",
            homed=runtime.motion.status().homed,
            detail="AutoHome durduruldu.",
        )

    def home_status(self, runtime: "RuntimeState") -> HardwareHomeResult:
        if self.serial.light_protocol_enabled:
            if not self.home_in_progress:
                response = self.serial.gateway_exchange("POZ", ("OK,POZ",))
                if not response.accepted:
                    return HardwareHomeResult(accepted=False, phase="FAULT", reason_codes=self._home_serial_reason_codes(response.reason), detail=response.reason)
                position = self._apply_position_response(runtime, response.reason, command="POZ")
                phase = "DONE" if runtime.motion.status().homed else "IDLE"
                return position.model_copy(update={"phase": phase, "homed": runtime.motion.status().homed, "detail": position.detail})
            if self._light_home_timed_out():
                return self._light_home_timeout(runtime)
            response = self.serial.gateway_exchange("POZ", ("OK,POZ",))
            consume_fn = getattr(self.serial, "consume_gateway_events", None)
            events = consume_fn() if consume_fn is not None else []
            failed = next((event for event in events if event in {"ERR,HOME_FAIL", "ERR,HOME_ABORT"}), None)
            if not response.accepted:
                result = HardwareHomeResult(accepted=False, phase="FAULT", reason_codes=self._home_serial_reason_codes(response.reason), detail=response.reason)
                self._mark_home_failed(runtime, result)
                return result
            position = self._apply_position_response(runtime, response.reason, command="POZ")
            if failed:
                result = position.model_copy(update={
                    "accepted": False,
                    "phase": "FAULT",
                    "homed": False,
                    "reason_codes": self._home_serial_reason_codes(failed),
                    "detail": failed,
                })
                self._mark_home_failed(runtime, result)
                return result

            curr_phase = runtime.motion.status().homing_phase or "TILT_SEEK"
            if "OK,HOME_DONE" in events:
                curr_phase = "CENTERING"
                runtime.motion.state = runtime.motion.state.model_copy(update={"homed": True, "homing_phase": "CENTERING", "motion_state": MotionStateValue.HOMING, "updated_at": time.time()})
            elif "OK,HOME_START" in events:
                curr_phase = "TILT_SEEK"
            elif curr_phase == "TILT_SEEK" and position.tilt_deg is not None and position.tilt_deg <= 3.0:
                curr_phase = "PAN_SEEK"
                runtime.motion.state = runtime.motion.state.model_copy(update={"homing_phase": "PAN_SEEK"})

            at_center = (
                position.tilt_deg is not None
                and position.pan_deg is not None
                and abs(position.tilt_deg - 30.0) <= 2.5
                and abs(position.pan_deg - 135.0) <= 2.5
            )
            complete = "OK,CENTER_DONE" in events or (at_center and curr_phase in ("CENTERING", "PAN_SEEK"))
            if complete:
                self._set_home_in_progress(False)
                self._light_home_deadline = None
                self.serial.gateway_exchange("MODE,MANUAL", ("OK,MODE_MANUAL", "OK,KOMUT_ALINDI"))
                self.serial.gateway_exchange("MOTOR,ON", ("OK,MOTOR_ON", "OK,KOMUT_ALINDI"))
                runtime.motion.state = runtime.motion.state.model_copy(update={"homed": True, "homing_phase": "DONE", "motion_state": MotionStateValue.STOPPED, "updated_at": time.time()})
                result = position.model_copy(update={"phase": "DONE", "homed": True, "detail": f"{position.detail}; {', '.join(events) if events else 'CENTER_REACHED'}"})
                tl = getattr(runtime, "tracking_loop", None)
                cal_pan = float(position.pan_deg) if position.pan_deg is not None else 135.0
                cal_tilt = float(position.tilt_deg) if position.tilt_deg is not None else 30.0
                for ev in events:
                    m = re.search(r"OK,CENTER_POS,X:([-\d.]+),Y:([-\d.]+)", ev)
                    if m:
                        cal_tilt = float(m.group(1))
                        cal_pan = float(m.group(2))
                        break
                if tl is not None:
                    if hasattr(tl, "_calibrate_dead_reckoning"):
                        tl._calibrate_dead_reckoning(cal_pan)
                    if hasattr(tl, "_calibrate_dead_reckoning_tilt"):
                        tl._calibrate_dead_reckoning_tilt(cal_tilt)
                restored = self.run_preflight(runtime, actuator_arm_requested=self.actuator_arm_requested, update_arm_intent=False)
                if not restored.physical_motion_enabled:
                    result = result.model_copy(update={
                        "reason_codes": sorted(set([*result.reason_codes, *restored.reason_codes])),
                        "detail": f"{result.detail}; post-home preflight: {', '.join(restored.reason_codes)}",
                    })
                return result

            phase, homed = curr_phase, runtime.motion.status().homed
            return position.model_copy(update={"phase": phase, "homed": homed, "detail": f"{position.detail}; CENTER_DONE bekleniyor."})
        response = self.serial.gateway_exchange("HOME_STATUS", ("OK,HOME",))
        if not response.accepted:
            result = HardwareHomeResult(accepted=False, phase="FAULT", reason_codes=self._home_serial_reason_codes(response.reason), detail=response.reason)
            self._mark_home_failed(runtime, result)
            return result
        result = self._apply_position_response(runtime, response.reason, command="HOME_STATUS")
        if result.accepted and result.homed and result.phase == "DONE":
            tl = getattr(runtime, "tracking_loop", None)
            if tl is not None:
                cal_pan = float(result.pan_deg) if result.pan_deg is not None else 135.0
                cal_tilt = float(result.tilt_deg) if result.tilt_deg is not None else 30.0
                if hasattr(tl, "_calibrate_dead_reckoning"):
                    tl._calibrate_dead_reckoning(cal_pan)
                if hasattr(tl, "_calibrate_dead_reckoning_tilt"):
                    tl._calibrate_dead_reckoning_tilt(cal_tilt)
            # HOME deliberately disarms the firmware. Restore physical ARM
            # only when the same visible startup/cockpit intent was already
            # active; a TEST/no-fire session remains disarmed.
            restored = self.run_preflight(
                runtime,
                actuator_arm_requested=self.actuator_arm_requested,
                update_arm_intent=False,
            )
            if not restored.physical_motion_enabled:
                result = result.model_copy(update={
                    "reason_codes": sorted(set([*result.reason_codes, *restored.reason_codes])),
                    "detail": f"{result.detail}; post-home preflight: {', '.join(restored.reason_codes)}",
                })
        elif not result.accepted or result.phase in {"FAULT", "IDLE"}:
            self._mark_home_failed(runtime, result)
        return result

    def position_status(self, runtime: "RuntimeState") -> HardwareHomeResult:
        command = "POZ" if self.serial.light_protocol_enabled else "POS"
        expected = ("OK,POZ",) if self.serial.light_protocol_enabled else ("OK,POS",)
        response = self.serial.gateway_exchange(command, expected)
        if not response.accepted:
            return HardwareHomeResult(accepted=False, phase="FAULT", reason_codes=self._home_serial_reason_codes(response.reason), detail=response.reason)
        return self._apply_position_response(runtime, response.reason, command=command)

    def get_position(self, runtime: "RuntimeState") -> HardwareHomeResult:
        """Donanım pozisyonunu Pico'dan doğrudan sorgular ve RuntimeState'i senkronize eder."""
        return self.position_status(runtime)

    def configure_motion_envelope(self, runtime: "RuntimeState", envelope: HardwareMotionEnvelope) -> HardwareMotionEnvelopeResult:
        tilt_software_only = False
        reasons = self._live_reasons(runtime, require_actuator_arm=False)
        if reasons:
            return HardwareMotionEnvelopeResult(accepted=False, envelope=self.motion_envelope, reason_codes=reasons, detail="Hareket zarfı Pico'ya uygulanamadı.")
        if self.serial.light_protocol_enabled:
            raw_pan = f"LIMIT,PAN,{envelope.pan_min_deg:.3f},{envelope.pan_max_deg:.3f}"
            resp_pan = self.serial.gateway_exchange(raw_pan, ("OK,LIMIT_SET",))
            if not resp_pan.accepted:
                return HardwareMotionEnvelopeResult(accepted=False, envelope=self.motion_envelope, reason_codes=["PICO_LIMIT_CONFIG_REJECTED"], detail=resp_pan.reason)
            raw_tilt = f"LIMIT,TILT,{envelope.tilt_min_deg:.3f},{envelope.tilt_max_deg:.3f}"
            response = self.serial.gateway_exchange(raw_tilt, ("OK,LIMIT_SET", "OK,KOMUT_ALINDI"))
            if response.accepted and "KOMUT_ALINDI" in str(response.reason):
                # Pico firmware LIMIT,TILT bilmiyor: dikey limit yalniz yazilimla (_clamp_to_soft_limits) uygulanir.
                tilt_software_only = True
                self.logger.emit(LogLevel.WARN, "COMMAND_GATEWAY", "Pico firmware LIMIT,TILT desteklemiyor; dikey limit yazilimla uygulanir", {"reason_code": "PICO_TILT_LIMIT_SOFTWARE_ONLY"})
        else:
            raw = f"CFG_LIMITS,{envelope.pan_min_deg:.3f},{envelope.pan_max_deg:.3f},{envelope.tilt_min_deg:.3f},{envelope.tilt_max_deg:.3f}"
            expected = ("OK,LIMITS",)
            response = self.serial.gateway_exchange(raw, expected)
        if not response.accepted:
            return HardwareMotionEnvelopeResult(accepted=False, envelope=self.motion_envelope, reason_codes=["PICO_LIMIT_CONFIG_REJECTED"], detail=response.reason)
        self.motion_envelope = envelope
        self._apply_envelope_to_runtime(runtime, envelope)
        self._persist_motion_envelope(envelope)
        return HardwareMotionEnvelopeResult(accepted=True, envelope=envelope, reason_codes=(["PICO_TILT_LIMIT_SOFTWARE_ONLY"] if tilt_software_only else []), detail=response.reason)

    def fire_from_tracking(self, runtime: "RuntimeState", candidate: dict) -> GatewayCommandResult:
        operation = getattr(runtime, "operation", None)
        if operation is not None and operation.state().configured:
            state = operation.state()
            if state.control_mode.value != "AUTONOMOUS":
                return self._blocked("FIRE", ["AUTONOMOUS_MODE_REQUIRED"])
            if state.fire_permission.value != "ENABLED":
                return self._blocked("FIRE", ["FIRE_PERMISSION_DISABLED"])
            if not bool(candidate.get("aim_inside_inner_lock", False)):
                return self._blocked("FIRE", ["INNER_LOCK_NOT_SATISFIED"])
            if state.target_policy.value == "AIRCRAFT":
                aircraft_reasons = self._aircraft_policy_reasons(runtime, candidate)
                if aircraft_reasons:
                    return self._blocked("FIRE", aircraft_reasons)
                if self.profile != CommandProfile.COMPETITION:
                    return self._fire_live_candidate(runtime, candidate)
            elif state.target_policy.value == "BALLOON_AIRCRAFT":
                combined_reasons = self._combined_policy_reasons(runtime, candidate)
                if combined_reasons:
                    return self._blocked("FIRE", combined_reasons)
                if self.profile != CommandProfile.COMPETITION:
                    return self._fire_live_candidate(runtime, candidate)
            else:
                balloon_reasons = self._balloon_policy_reasons(runtime, candidate)
                if balloon_reasons:
                    return self._blocked("FIRE", balloon_reasons)
                if self.profile != CommandProfile.COMPETITION:
                    return self._fire_live_candidate(runtime, candidate)
        stage = runtime.mission.state.active_stage
        if self.profile == CommandProfile.COMPETITION and stage == "stage1":
            return self._blocked("FIRE", ["MANUAL_OPERATOR_COMMAND_REQUIRED"])
        if self.profile == CommandProfile.COMPETITION:
            if stage == "stage2":
                stage2_reasons = self._autonomous_engagement_reasons(runtime, candidate, stage="A2")
                if stage2_reasons:
                    return self._blocked("FIRE", stage2_reasons)
            else:
                decision = runtime.decision_engine.evaluate(runtime)
                if decision.decision_state != DecisionStateValue.FIRE_READY:
                    return self._blocked("FIRE", ["DECISION_NOT_FIRE_READY", *decision.blocking_reasons])
                if stage == "stage3":
                    candidate_body = candidate.get("body_detection_id")
                    if not isinstance(candidate_body, int) or decision.selected_body_detection_id != candidate_body:
                        return self._blocked("FIRE", ["A3_DECISION_TARGET_MISMATCH"])
                    # Surface the stage-3 friendly-safety prerequisite before
                    # lower-level association freshness when the candidate is
                    # fully classified.  This keeps the operator reason
                    # actionable after a stage transition resets perception
                    # state, while the stable-association gate below remains
                    # mandatory whenever the two-link evidence is present.
                    if candidate.get("body_team") == "enemy" and candidate.get("body_class") in {
                        "f16",
                        "helicopter",
                        "ballistic_missile",
                        "mini_micro_uav",
                    }:
                        if len(self._stage3_friend_links(runtime)) < 1:
                            return self._blocked("FIRE", ["A3_FRIEND_SAFETY_EVIDENCE_INCOMPLETE"])
                    stage3_reasons = self._autonomous_engagement_reasons(runtime, candidate, stage="A3")
                    if stage3_reasons:
                        return self._blocked("FIRE", stage3_reasons)
                    friend_links = self._stage3_friend_links(runtime)
                    if len(friend_links) < 1:
                        return self._blocked("FIRE", ["A3_FRIEND_SAFETY_EVIDENCE_INCOMPLETE"])
                    candidate["friend_links"] = [item.model_dump(mode="json") for item in friend_links]
        elif self.profile in {CommandProfile.LIVE_TEST, CommandProfile.VIDEO_DEMO}:
            if not self.serial.light_protocol_enabled:
                event = runtime.vision.latest_event
                if event is None or not event.balloon_detections:
                    return self._blocked("FIRE", ["LIVE_TEST_BALLOON_NOT_DETECTED"])
        else:
            if not self.serial.light_protocol_enabled:
                return self._blocked("FIRE", ["DRY_RUN_ACTIVE"])

        reasons = self._live_reasons(runtime, require_actuator_arm=True)
        zone_name = active_zone_name(
            runtime.config.decision.fire_forbidden_zones,
            runtime.motion.status().pan_position_deg,
            runtime.motion.status().tilt_position_deg,
        )
        if zone_name:
            reasons.append("FIRE_FORBIDDEN_ZONE")
        if reasons:
            return self._blocked("FIRE", reasons)
        raw_cmd = "BALON,PATLAT" if self.serial.light_protocol_enabled else "LZR,1"
        ack_exp = ("OK,SERVO_BASLATILDI", "OK,SERVO_HEDEFTE", "OK,SERVO_TAMAMLANDI", "OK,KOMUT_ALINDI") if self.serial.light_protocol_enabled else ("OK,LASER_1", "FIRE_SERVO_PULLED")
        result = self._execute_trigger_pulse(
            command_label="FIRE",
            raw_command=raw_cmd,
            expected_ack=ack_exp,
            pulse_s=0.5 if self.serial.light_protocol_enabled else 1.0,
            count_physical_shot=True,
            detail="Pico acknowledged trigger pull.",
        )
        if not result.accepted:
            return result
        # Evidence is observational.  A recorder failure must never alter an
        # accepted Pico command or create another physical output.
        try:
            runtime.engagement_evidence.record_shot_ack(runtime, candidate, result)
        except Exception as exc:
            self.logger.emit(LogLevel.WARN, "COMMAND_GATEWAY", "Shot evidence record failed", {"reason_code": "EVIDENCE_RECORD_FAILED", "error": str(exc)})
        balloon_track_id = candidate.get("balloon_track_id")
        # Visual confirmation is an observational fact for every accepted
        # tracked shot, including LIVE_TEST/VIDEO_DEMO.  Only competition
        # stages below consume it for score/round progression.
        if isinstance(balloon_track_id, int):
            runtime.hit_confirmation.register_shot(
                balloon_track_id,
                candidate.get("body_detection_id") if isinstance(candidate.get("body_detection_id"), int) else None,
                body_track_id=candidate.get("body_track_id") if isinstance(candidate.get("body_track_id"), int) else None,
            )
        if self.profile == CommandProfile.COMPETITION and stage in {"stage2", "stage3"}:
            if isinstance(balloon_track_id, int):
                if stage == "stage2":
                    runtime.stage2_engagement.register_shot(balloon_track_id, runtime.mission.state.stage2_round)
                elif stage == "stage3":
                    body_class = candidate.get("body_class")
                    friend_links = [Stage3FriendLink.model_validate(item) for item in candidate.get("friend_links", [])]
                    if isinstance(body_class, str):
                        runtime.stage3_engagement.register_shot(
                            enemy_class=body_class,
                            enemy_balloon_track_id=balloon_track_id,
                            friend_links=friend_links,
                            current_round=runtime.mission.state.stage3_round,
                        )
        self.logger.emit(LogLevel.INFO, "COMMAND_GATEWAY", "Fire command acknowledged", {**candidate, **result.model_dump(mode="json")})
        return result

    def _fire_live_candidate(self, runtime: "RuntimeState", candidate: dict) -> GatewayCommandResult:
        """Common physical FIRE path for the single-cockpit aircraft policy."""
        reasons = self._live_reasons(runtime, require_actuator_arm=True)
        zone_name = active_zone_name(
            runtime.config.decision.fire_forbidden_zones,
            runtime.motion.status().pan_position_deg,
            runtime.motion.status().tilt_position_deg,
        )
        if zone_name:
            reasons.append("FIRE_FORBIDDEN_ZONE")
        if reasons:
            return self._blocked("FIRE", reasons)
        raw_cmd = "BALON,PATLAT" if self.serial.light_protocol_enabled else "LZR,1"
        ack_exp = ("OK,SERVO_BASLATILDI", "OK,SERVO_HEDEFTE", "OK,SERVO_TAMAMLANDI", "OK,KOMUT_ALINDI") if self.serial.light_protocol_enabled else ("OK,LASER_1", "FIRE_SERVO_PULLED")
        result = self._execute_trigger_pulse(
            command_label="FIRE",
            raw_command=raw_cmd,
            expected_ack=ack_exp,
            pulse_s=0.5 if self.serial.light_protocol_enabled else 1.0,
            count_physical_shot=True,
            detail="Pico acknowledged aircraft-policy trigger pull.",
        )
        if not result.accepted:
            return result
        try:
            runtime.engagement_evidence.record_shot_ack(runtime, candidate, result)
        except Exception as exc:
            self.logger.emit(LogLevel.WARN, "COMMAND_GATEWAY", "Aircraft policy shot evidence record failed", {"reason_code": "EVIDENCE_RECORD_FAILED", "error": str(exc)})
        self.logger.emit(LogLevel.INFO, "COMMAND_GATEWAY", "Aircraft policy fire command acknowledged", {**candidate, **result.model_dump(mode="json")})
        return result

    @staticmethod
    def _aircraft_policy_reasons(runtime: "RuntimeState", candidate: dict) -> list[str]:
        body_id = candidate.get("body_detection_id")
        event = runtime.vision.latest_event
        if not isinstance(body_id, int) or event is None:
            return ["AIRCRAFT_TARGET_UNRESOLVED"]
        body = next((item for item in event.body_detections if item.id == body_id), None)
        if body is None:
            return ["AIRCRAFT_TARGET_STALE"]
        if body.target_team != "enemy":
            return ["FRIEND_TARGET_FIRE_BLOCKED" if body.target_team == "friend" else "TARGET_TEAM_UNKNOWN"]
        selected = runtime.operation.state().selected_detection_id if hasattr(runtime, "operation") else None
        if selected is not None and selected != body.id:
            return ["TARGET_SELECTION_MISMATCH"]
        if runtime.target_registry.body_is_destroyed(body):
            return ["TARGET_ALREADY_DESTROYED"]
        if not candidate.get("track_fresh", True):
            return ["AIRCRAFT_TRACK_STALE"]
        return []

    @staticmethod
    def _balloon_policy_reasons(runtime: "RuntimeState", candidate: dict) -> list[str]:
        is_light = (
            getattr(getattr(runtime, "serial", None), "light_protocol_enabled", False) is True
            or getattr(getattr(runtime, "auto_tracker", None), "controller_mode", "legacy") in {
                "GENERAL", "A2", "OPT_D_FILTER", "OPT_ZONE_GAIN", "OPT_SINE_TRACK"
            }
        )
        track_id = candidate.get("balloon_track_id")
        det_id = candidate.get("balloon_detection_id")
        if not isinstance(track_id, int):
            if isinstance(det_id, int):
                track_id = det_id
                candidate["balloon_track_id"] = det_id
            elif not is_light:
                return ["BALLOON_TARGET_UNRESOLVED"]

        registry = getattr(runtime, "target_registry", None)
        if registry is not None:
            if isinstance(track_id, int):
                logical = registry.target_for_balloon_track(track_id)
                if logical is not None and logical.state.value == "DESTROYED":
                    return ["TARGET_ALREADY_DESTROYED"]
                if logical is not None and logical.target_team == "friend":
                    return ["FRIEND_TARGET_FIRE_BLOCKED"]
            if not is_light and isinstance(track_id, int) and not registry.is_track_selectable(track_id, require_ready=False):
                return ["BALLOON_TARGET_NOT_SELECTABLE"]

        # Cross-check latest vision event verdicts for friendly target safety
        event = getattr(runtime.vision, "latest_event", None)
        if event is not None and getattr(event, "target_verdicts", None):
            for v in event.target_verdicts:
                if v.kind == "balloon" and (
                    (isinstance(det_id, int) and v.detection_id == det_id)
                    or (isinstance(track_id, int) and v.track_id == track_id)
                ):
                    if str(getattr(v, "target_team", "")).lower() in {"friend", "dost"} or v.verdict_state == "FRIEND_LOCKED":
                        return ["FRIEND_TARGET_FIRE_BLOCKED"]

        tracks = getattr(getattr(runtime.auto_tracker.status(), "multi_target_tracker", None), "tracks", [])
        track = next((item for item in tracks if item.track_id == track_id), None) if isinstance(track_id, int) else None
        if track is not None:
            if not track.fresh:
                return ["BALLOON_TRACK_STALE"]
        elif is_light:
            event = runtime.vision.latest_event
            if event is None or not event.balloon_detections:
                return ["BALLOON_TARGET_UNRESOLVED"]
        else:
            return ["BALLOON_TRACK_STALE"]

        if not runtime.auto_tracker.tracking_active:
            return ["TRACKING_NOT_ACTIVE"]
        selected = runtime.operation.state().selected_detection_id if hasattr(runtime, "operation") else None
        if selected is not None and selected not in {candidate.get("balloon_detection_id"), candidate.get("balloon_track_id")}:
            return ["TARGET_SELECTION_MISMATCH"]
        return []

    @staticmethod
    def _combined_policy_reasons(runtime: "RuntimeState", candidate: dict) -> list[str]:
        track_id = candidate.get("balloon_track_id")
        if not isinstance(track_id, int):
            track_id = candidate.get("balloon_detection_id")
            if isinstance(track_id, int):
                candidate["balloon_track_id"] = track_id
            else:
                return ["ASSOCIATED_BALLOON_UNRESOLVED"]
        # Cross-check latest vision event verdicts for friendly target safety
        vision_svc = getattr(runtime, "vision", None)
        event = getattr(vision_svc, "latest_event", None) if vision_svc is not None else None
        if event is not None and getattr(event, "target_verdicts", None):
            det_id = candidate.get("balloon_detection_id")
            for v in event.target_verdicts:
                if v.kind == "balloon" and (
                    (isinstance(det_id, int) and v.detection_id == det_id)
                    or (isinstance(track_id, int) and v.track_id == track_id)
                ):
                    if str(getattr(v, "target_team", "")).lower() in {"friend", "dost"} or v.verdict_state == "FRIEND_LOCKED":
                        return ["FRIEND_TARGET_FIRE_BLOCKED"]

        registry = getattr(runtime, "target_registry", None)
        logical = registry.target_for_balloon_track(track_id) if registry is not None else None
        if logical is None:
            return ["ASSOCIATION_NOT_STABLE"]
        if logical.state.value == "DESTROYED" or logical.consumed:
            return ["TARGET_ALREADY_DESTROYED"]
        if logical.target_team != "enemy" or not logical.engagement_allowed:
            return ["FRIEND_TARGET_FIRE_BLOCKED" if logical.target_team == "friend" else "TARGET_TEAM_UNKNOWN"]
        if logical.state.value != "READY":
            return ["ASSOCIATION_NOT_STABLE"]
        association = next(
            (item for item in runtime.association.status().associations if item.balloon_track_id == track_id),
            None,
        )
        if association is None:
            return ["ASSOCIATION_NOT_STABLE"]
        if association.state == "coasting" or getattr(association, "coasting", False):
            return ["BODY_COASTING_FIRE_BLOCKED"]
        if association.state != "stable":
            return ["ASSOCIATION_NOT_STABLE"]
        track = next((item for item in runtime.auto_tracker.status().multi_target_tracker.tracks if item.track_id == track_id), None)
        if track is None or not track.fresh:
            return ["BALLOON_TRACK_STALE"]
        if not runtime.auto_tracker.tracking_active:
            return ["TRACKING_NOT_ACTIVE"]
        selected = runtime.operation.state().selected_detection_id if hasattr(runtime, "operation") else None
        if selected is not None and candidate.get("balloon_detection_id") not in {selected, logical.balloon_detection_id}:
            return ["TARGET_SELECTION_MISMATCH"]
        return []

    @staticmethod
    def _autonomous_engagement_reasons(runtime: "RuntimeState", candidate: dict, stage: str) -> list[str]:
        """A2/A3 physical fire requires a current, selected, stable link.

        This deliberately consumes only telemetry services.  None of these
        services write serial; CommandGateway remains the sole output point.
        """
        prefix = stage
        is_light = (
            getattr(getattr(runtime, "serial", None), "light_protocol_enabled", False) is True
            or getattr(getattr(runtime, "auto_tracker", None), "controller_mode", "legacy") in {
                "GENERAL", "A2", "OPT_D_FILTER", "OPT_ZONE_GAIN", "OPT_SINE_TRACK"
            }
        )
        balloon_track_id = candidate.get("balloon_track_id")
        if not isinstance(balloon_track_id, int):
            det_id = candidate.get("balloon_detection_id")
            if isinstance(det_id, int):
                balloon_track_id = det_id
                candidate["balloon_track_id"] = det_id
            else:
                return [f"{prefix}_TRACK_ID_UNRESOLVED"]
        registry = getattr(runtime, "target_registry", None)
        logical_target = registry.target_for_balloon_track(balloon_track_id) if registry is not None else None
        if registry is not None:
            if logical_target is not None and logical_target.state.value == "DESTROYED":
                return [f"{prefix}_TARGET_ALREADY_DESTROYED"]
            if not is_light and not registry.is_track_selectable(balloon_track_id, require_ready=True):
                return [f"{prefix}_BALLOON_ASSOCIATION_NOT_READY"]
        if not runtime.auto_tracker.tracking_active:
            return [f"{prefix}_TRACKING_NOT_ACTIVE"]
        if not is_light:
            tracks = runtime.auto_tracker.status().multi_target_tracker.tracks
            track = next((item for item in tracks if item.track_id == balloon_track_id), None)
            if track is None or not track.fresh:
                return [f"{prefix}_TRACK_STALE"]
            priority = runtime.target_priority.status()
            if priority.selected_track_id != balloon_track_id:
                return [f"{prefix}_PRIORITY_TARGET_MISMATCH"]
            association = next(
                (item for item in runtime.association.status().associations if item.balloon_track_id == balloon_track_id),
                None,
            )
            if association is None or association.state != "stable" or association.body_detection_id is None:
                return [f"{prefix}_ASSOCIATION_NOT_STABLE"]
            if logical_target is not None and logical_target.body_track_id is not None and logical_target.state.value != "READY":
                return [f"{prefix}_BALLOON_ASSOCIATION_NOT_READY"]
            candidate_body = candidate.get("body_detection_id")
            if isinstance(candidate_body, int) and candidate_body != association.body_detection_id:
                return [f"{prefix}_ASSOCIATION_TARGET_MISMATCH"]
        record = next((item for item in runtime.hit_confirmation.status().records if item.balloon_track_id == balloon_track_id), None)
        if record is not None and record.state.value == "PENDING_CONFIRMATION":
            return [f"{prefix}_HIT_CONFIRMATION_PENDING"]
        if stage == "A2":
            mission = runtime.mission.state
            if mission.stage2_failed:
                return ["A2_STAGE_FAILED"]
            if mission.stage2_completed_rounds >= 4:
                return ["A2_ALL_ROUNDS_COMPLETED"]
        elif stage == "A3":
            if candidate.get("body_team") != "enemy":
                return ["A3_CANDIDATE_NOT_ENEMY"]
            if candidate.get("body_class") not in {"f16", "helicopter", "ballistic_missile", "mini_micro_uav"}:
                return ["A3_CANDIDATE_CLASS_UNRESOLVED"]
            mission = runtime.mission.state
            if mission.stage3_failed:
                return ["A3_STAGE_FAILED"]
            if mission.stage3_completed_rounds >= 8:
                return ["A3_ALL_ROUNDS_COMPLETED"]
        return []

    @staticmethod
    def _stage3_friend_links(runtime: "RuntimeState") -> list[Stage3FriendLink]:
        event = runtime.vision.latest_event
        if event is None:
            return []
        body_by_id = {body.id: body for body in event.body_detections}
        links: list[Stage3FriendLink] = []
        for association in runtime.association.status().associations:
            if association.state != "stable" or association.body_detection_id is None or association.body_track_id is None:
                continue
            registry = getattr(runtime, "target_registry", None)
            if registry is not None and not registry.is_track_selectable(association.balloon_track_id, require_ready=True):
                continue
            body = body_by_id.get(association.body_detection_id)
            if body is not None and body.target_team == "friend":
                links.append(
                    Stage3FriendLink(
                        balloon_track_id=association.balloon_track_id,
                        body_track_id=association.body_track_id,
                    )
                )
        return sorted(links, key=lambda item: item.balloon_track_id)

    def fire_from_operator(self, runtime: "RuntimeState", candidate: dict) -> GatewayCommandResult:
        """Stage-1 manual fire: explicit operator intent, never tracker intent."""
        mission = runtime.mission.state
        operation = getattr(runtime, "operation", None)
        if operation is not None and operation.state().configured:
            operation_state = operation.state()
            if not self.actuator_armed and operation_state.fire_permission.value != "ENABLED":
                return self._blocked("FIRE", ["FIRE_PERMISSION_DISABLED"])
        # Manual FIRE executes directly on operator authority.
        # MANUAL means direct operator aim authority.  Target classification,
        # IFF, destroyed-target state and balloon association are autonomous
        # decision inputs; they must not turn the physical trigger into a
        # target-locked control when the operator explicitly selects MANUAL.
        # The autonomous fire_from_tracking() path above retains every one of
        # those target/team/association gates.  Manual FIRE still passes the
        # physical Gateway preflight below (Pico/heartbeat, E-Stop, camera,
        # arm, command profile, motion envelope and magazine ledger).
        reasons = self._live_reasons(runtime, require_actuator_arm=True)
        zone_name = active_zone_name(
            runtime.config.decision.fire_forbidden_zones,
            runtime.motion.status().pan_position_deg,
            runtime.motion.status().tilt_position_deg,
        )
        if zone_name:
            reasons.append("FIRE_FORBIDDEN_ZONE")
        if reasons:
            return self._blocked("FIRE", reasons)
        raw_cmd = "BALON,PATLAT" if self.serial.light_protocol_enabled else "LZR,1"
        ack_exp = ("OK,SERVO_BASLATILDI", "OK,SERVO_HEDEFTE", "OK,SERVO_TAMAMLANDI", "OK,KOMUT_ALINDI") if self.serial.light_protocol_enabled else ("OK,LASER_1", "FIRE_SERVO_PULLED")
        result = self._execute_trigger_pulse(
            command_label="FIRE",
            raw_command=raw_cmd,
            expected_ack=ack_exp,
            pulse_s=1.0,
            count_physical_shot=True,
            detail="Pico acknowledged manual trigger pull.",
        )
        if not result.accepted:
            return result
        # A manual fire may follow an already locked tracked target.  Attach
        # its ACK to that read-only evidence record when present.
        try:
            runtime.engagement_evidence.record_shot_ack(runtime, candidate, result)
        except Exception as exc:
            self.logger.emit(LogLevel.WARN, "COMMAND_GATEWAY", "Manual shot evidence record failed", {"reason_code": "EVIDENCE_RECORD_FAILED", "error": str(exc)})
        self.logger.emit(LogLevel.INFO, "COMMAND_GATEWAY", "Manual fire command acknowledged", {**candidate, **result.model_dump(mode="json")})
        return result

    @staticmethod
    def _fire_serial_reason_codes(reason: str) -> list[str]:
        """Preserve actionable serial FIRE reason codes for the cockpit."""
        if reason == "MAGAZINE_EMPTY":
            return ["MAGAZINE_EMPTY"]
        if reason.startswith("PICO_ACK_TIMEOUT"):
            return ["PICO_FIRE_ACK_TIMEOUT"]
        if reason.startswith("PICO_UNEXPECTED_ACK"):
            return ["PICO_FIRE_NACK"]
        if reason.startswith("PICO_WRITE_FAILED"):
            return ["PICO_FIRE_WRITE_FAILED"]
        return ["PICO_FIRE_REJECTED"]

    def configure_trigger_servo(self, runtime: "RuntimeState", release_deg: int, fire_deg: int) -> GatewayCommandResult:
        """Configure Pico trigger endpoints through the same live authority.

        The command changes PWM configuration only; it never pulls the
        trigger. A later explicit test/fire is still separately preflighted.
        """
        if self.serial.light_protocol_enabled:
            return self._blocked("SRV,CFG", ["PICO_TRIGGER_UNSUPPORTED"], "Light firmware exposes no trigger-servo configuration command.")
        if not (0 <= release_deg < fire_deg <= 180):
            return self._blocked("SRV,CFG", ["SERVO_ANGLE_RANGE_INVALID"])
        reasons = self._live_reasons(runtime, require_actuator_arm=False)
        if reasons:
            return self._blocked("SRV,CFG", reasons)
        if self.pico_protocol == "arduino_raw":
            raw_command = f"CFG_SERVO,{release_deg},{fire_deg}"
            expected_ack = ("OK,SERVO_CFG",)
        else:
            raw_command = f"SRV,CFG,{release_deg},{fire_deg}"
            expected_ack = ("OK,SERVO_CONFIGURED", "SERVO_CONFIGURED")
        command = self.serial.gateway_exchange(raw_command, expected_ack)
        if not command.accepted:
            return self._blocked("SRV,CFG", ["PICO_SERVO_CONFIG_REJECTED"], command.reason)
        return GatewayCommandResult(
            accepted=True, command=raw_command, detail="Pico acknowledged trigger servo configuration.",
            pico_ack=command.reason, physical_command_generated=not command.no_physical_command_generated,
        )

    def test_trigger(self, runtime: "RuntimeState", pulse_s: float) -> GatewayCommandResult:
        """Visible empty-chamber trigger test; exactly the normal live fire gates."""
        reasons = self._live_reasons(runtime, require_actuator_arm=True)
        if reasons:
            return self._blocked("SRV,TEST", reasons)
        if self.serial.light_protocol_enabled:
            raw_command = "BALON,PATLAT"
            expected_ack = ("OK,SERVO_BASLATILDI", "OK,SERVO_HEDEFTE", "OK,SERVO_TAMAMLANDI", "OK,KOMUT_ALINDI")
        elif self.pico_protocol == "arduino_raw":
            raw_command = "LZR,1"
            expected_ack = ("OK,LASER_1",)
        else:
            raw_command = "SRV,TEST"
            expected_ack = ("OK,SERVO_TEST", "FIRE_SERVO_PULLED")
        return self._execute_trigger_pulse(
            command_label="SRV,TEST",
            raw_command=raw_command,
            expected_ack=expected_ack,
            pulse_s=pulse_s,
            count_physical_shot=False,
            detail="Pico acknowledged empty-chamber trigger test.",
        )

    def stop_motion(self) -> GatewayCommandResult:
        """A safe-stop is always allowed, including during a failed preflight."""
        self.cancel_manual_motion_lease()
        self._set_home_in_progress(False)
        self._light_home_deadline = None
        if self.serial.light_protocol_enabled:
            send_fn = getattr(self.serial, "send_light_motion", None)
            if send_fn is not None:
                send_fn("X 0", "Y 0")
            send_cmd = getattr(self.serial, "send_light_command", None)
            if send_cmd is not None:
                send_cmd("0,0,0,0,0")
                send_cmd("STP")
            if self.runtime is not None:
                self._stop_pose_estimate(self.runtime, command="gateway_stop")
            return GatewayCommandResult(
                accepted=True,
                command="STP",
                reason_codes=[],
                detail="Light motion stopped (X 0, Y 0, 0,0,0,0,0, STP)",
                physical_command_generated=True,
            )
        result = self.serial.gateway_exchange("STP", ("OK,STOP", "EMERGENCY_STOP"))
        driver_command = "DRV,0"
        driver_expected = ("OK,DRIVER_DISABLED",)
        driver = self.serial.gateway_exchange(driver_command, driver_expected)
        self.driver_enabled = False
        self._set_home_in_progress(False)
        self._light_home_deadline = None
        if self.runtime is not None:
            self._stop_pose_estimate(self.runtime, command="gateway_stop")
        accepted = result.accepted and driver.accepted
        return GatewayCommandResult(
            accepted=accepted,
            command="STP",
            reason_codes=[] if accepted else (["PICO_STOP_WRITE_FAILED"] if not result.accepted else ["PICO_DRIVER_DISABLE_FAILED"]),
            detail=f"{result.reason}; {driver.reason}",
            pico_ack=driver.reason if accepted else result.reason if result.accepted else None,
            physical_command_generated=accepted and not result.no_physical_command_generated and not driver.no_physical_command_generated,
        )

    def invalidate_preflight(self, runtime: "RuntimeState", reason_code: str) -> None:
        """Safely invalidate live authority after an operator safety edit."""
        self._stop_pose_estimate(runtime, command="gateway_preflight_invalidated")
        if self.profile != CommandProfile.DRY_RUN:
            self.serial.gateway_safe_stop()
        self._release_fire_output(force=True)
        self.actuator_armed = False
        self.driver_enabled = False
        self._set_home_in_progress(False)
        self._light_home_deadline = None
        runtime.force_armed = False
        self.last_preflight = self.last_preflight.model_copy(
            update={
                "ready": False,
                "physical_motion_enabled": False,
                "physical_fire_enabled": False,
                "actuator_armed": False,
                "reason_codes": sorted(set([*self.last_preflight.reason_codes, reason_code])),
            }
        )

    def tick(self, runtime: "RuntimeState") -> None:
        # Fallback for deterministic tests and runtimes that already call
        # tick(). The independent Timer below is the primary production path.
        self._release_fire_output(force=False)
        if self.home_in_progress:
            return
        live_reasons = self._live_reasons(runtime, require_actuator_arm=False)
        if self.profile != CommandProfile.DRY_RUN and live_reasons:
            self._safe_live_runtime(runtime, live_reasons)

    def maintenance_tick(self, runtime: "RuntimeState") -> None:
        """Production heartbeat/E-Stop monitor independent of browser state."""
        homing = self.home_in_progress
        if not homing:
            self.refresh_motion_estimate(runtime)
        self._release_fire_output(force=False)
        if self.profile == CommandProfile.DRY_RUN:
            return
        tracking_active = bool(
            getattr(getattr(runtime, "auto_tracker", None), "tracking_active", False)
            or getattr(getattr(runtime, "tracking_loop", None), "is_running", False)
        )
        manual_active = (time.time() - getattr(self, "_last_manual_motion_at", 0.0)) < 1.2
        if tracking_active or manual_active:
            # Active motion streaming handles Pico communication.
            # Do not inject blocking round-trip PING probes that cause serial jitter.
            return
        if self.serial.light_protocol_enabled:
            last_activity = self.serial.gateway_last_heartbeat_at or 0.0
            if (time.time() - last_activity) < 1.5:
                return
        now = time.time()
        if now - self._last_health_probe_at < 0.5:
            return
        self._last_health_probe_at = now
        real_should_be_open = (
            self.serial.config.serial.transport_mode == "real_write"
            and self.serial.config.serial.real_serial_enabled
        )
        real_is_open = (
            self.serial._real_transport is not None
            and getattr(self.serial._real_transport, "is_open", False)
        )
        if (real_should_be_open and not real_is_open) or self.serial.connection_state.name == "FAULT":
            # The stale COM handle cannot acknowledge a safe-stop. Clear live
            # authority in memory immediately, then reopen/verify the port.
            # A successful reconnect sends the safe sequence before restoring
            # the already-selected profile through normal preflight.
            self._safe_live_runtime(runtime, ["PICO_CONNECTION_FAULT"], attempt_serial_stop=False)
            self._maybe_recover_pico_connection(runtime)
            return
        ping = self.serial.gateway_exchange("PING", ("OK,PONG", "PONG"))
        if not ping.accepted:
            if getattr(self.serial, "consecutive_ping_failures", 0) >= 5:
                if not self.serial.light_protocol_enabled or self.serial._real_transport is None or not getattr(self.serial._real_transport, "is_open", False):
                    self._safe_live_runtime(runtime, ["PICO_CONNECTION_FAULT", "PICO_HEARTBEAT_STALE"])
            return
        if self.serial.light_protocol_enabled:
            # Under Light protocol, a responsive PING is full proof of connectivity.
            # Do not spam HB (which is rejected with ERR,UNKNOWN_CMD on light firmware).
            # (30 Eylul) Acil stop birakildiysa (Pico HB ACIL:0) bayragi temizle ve preflight tazele.
            # Sonsuz disli: stop sirasinda taret yerinden oynamaz, Pico adim sayaci korunur -> AutoHome sart degil.
            if self.pico_estop_active is True:
                _y30_simdi = self._monotonic()
                if _y30_simdi - getattr(self, "_y30_estop_hb_at", 0.0) >= 0.5:
                    self._y30_estop_hb_at = _y30_simdi
                    _y30_hb = self.serial.gateway_exchange("HB", ("OK,HB",))
                    if _y30_hb.accepted and not self._estop_from_response(_y30_hb.reason):
                        self.pico_estop_active = False
                        self.logger.emit(LogLevel.WARN, "COMMAND_GATEWAY", "Acil stop kalkti (Pico ACIL:0); AutoHome beklemeden yeniden hazir", {"reason_code": "ESTOP_RELEASED_AUTO"})
                        try:
                            self.run_preflight(runtime)
                        except Exception:
                            pass
            now_mono = self._monotonic()
            if (now_mono - getattr(self, "_last_light_poz_at", 0.0)) >= 0.5:
                self._last_light_poz_at = now_mono
                pos_res = self.serial.gateway_exchange("POZ", ("OK,POZ",))
                if pos_res.accepted:
                    self._apply_position_response(runtime, pos_res.reason, command="POZ")
            if homing and self._light_home_timed_out():
                self._light_home_timeout(runtime)
            self._maybe_recover_camera_preflight(runtime)
            self.tick(runtime)
            return
        stat = self.serial.gateway_exchange("STAT", ("OK,STAT", "STATUS"))
        if not stat.accepted:
            self._safe_live_runtime(runtime, ["PICO_CONNECTION_FAULT"])
            return
        self.pico_estop_active = self._estop_from_response(stat.reason)
        if self.pico_estop_active:
            self._safe_live_runtime(runtime, ["ESTOP_ACTIVE"])
            return
        if self.serial.light_protocol_enabled and not self._light_health_fields_present(stat.reason):
            self._safe_live_runtime(runtime, ["PICO_HEALTH_FIELDS_MISSING", "ESTOP_STATE_UNKNOWN"])
            return
        if self._light_power_is_ok(stat.reason) is False:
            self._safe_live_runtime(runtime, ["PICO_POWER_FAULT"])
            return
        # Query the firmware-owned absolute position when the selected raw
        # protocol exposes it. Light firmware calls this command POZ and does
        # not claim step counters or home state in the reply.
        if self.serial.light_protocol_enabled or "HOMED=" in stat.reason:
            position_command = "POZ" if self.serial.light_protocol_enabled else "POS"
            position_expected = ("OK,POZ",) if self.serial.light_protocol_enabled else ("OK,POS",)
            position = self.serial.gateway_exchange(position_command, position_expected)
            if position.accepted:
                self._apply_position_response(runtime, position.reason, command=position_command)
        if self.serial.light_protocol_enabled and homing and self._light_home_timed_out():
            self._light_home_timeout(runtime)
            return
        if homing:
            # Camera freshness is still a mandatory live-output condition,
            # including while HOME is moving.  All other ordinary preflight
            # recovery and motion checks wait until the Pico reports DONE.
            if self.home_in_progress and not self._camera_is_fresh(runtime):
                self._safe_live_runtime(runtime, ["CAMERA_STALE"])
            return
        self._maybe_recover_camera_preflight(runtime)
        self.tick(runtime)

    def _maybe_recover_pico_connection(self, runtime: "RuntimeState") -> None:
        if self.profile == CommandProfile.DRY_RUN:
            return
        now = self._monotonic()
        if now - self._last_pico_reconnect_at < self._pico_reconnect_interval_s:
            return
        self._last_pico_reconnect_at = now
        connected, code = self.serial.gateway_reconnect_real()
        if not connected:
            self.logger.emit(
                LogLevel.WARN,
                "COMMAND_GATEWAY",
                "Pico automatic reconnect pending",
                {"reason_code": code, "port": self.serial.config.serial.port},
            )
            return

        if self.serial.light_protocol_enabled:
            # Under Light protocol, reconnect does not require safe-stop with DRV,0 or preflight locks.
            self.driver_enabled = True
            self.actuator_armed = False
            self._light_mode_auto = False
            self.serial.connection_state = SerialConnectionState.PORT_OPEN_NO_TELEMETRY
            self.serial.raw_pico_verified = True
            self.serial.consecutive_ping_failures = 0
            self.serial.last_error = None
            self.serial.last_transport_fault = None
            send_cmd = getattr(self.serial, "send_light_command", None)
            if send_cmd is not None:
                send_cmd("MOTOR,ON")
                send_cmd("MODE,MANUAL")
            self.logger.emit(
                LogLevel.INFO,
                "COMMAND_GATEWAY",
                "Pico automatic reconnect completed under Light protocol",
                {"reason_code": code, "port": self.serial.config.serial.port},
            )
            return

        # The port is responsive again, but its driver/trigger state may have
        # reset. Establish the existing safe baseline before any preflight can
        # restore operator intent.
        self.serial.gateway_safe_stop()
        self.driver_enabled = False
        self.actuator_armed = False
        self._light_mode_auto = False
        result = self.run_preflight(
            runtime,
            actuator_arm_requested=self.actuator_arm_requested,
            update_arm_intent=False,
        )
        self.logger.emit(
            LogLevel.INFO if result.physical_motion_enabled else LogLevel.WARN,
            "COMMAND_GATEWAY",
            "Pico automatic reconnect preflight completed",
            {
                "reason_code": code,
                "physical_motion_enabled": result.physical_motion_enabled,
                "physical_fire_enabled": result.physical_fire_enabled,
                "reason_codes": result.reason_codes,
            },
        )

    def _maybe_recover_camera_preflight(self, runtime: "RuntimeState") -> None:
        """Restore motion authority after a transient camera-stale safe stop.

        The maintenance loop deliberately fails closed when the camera stops
        producing current frames.  Previously that state stayed latched until
        an operator happened to re-run Setup preflight, so tracking appeared
        active while every SPD command was rejected.  Recovery is limited to
        the already selected live profile, requires a fresh real camera frame,
        and restores the last explicit cockpit ARM intent. Physical FIRE is
        still unavailable while any live gate is unhealthy; once camera and
        Pico health return, the already-visible operator intent is restored
        without a second click.
        """
        if self.profile == CommandProfile.DRY_RUN:
            return
        if self.last_preflight.physical_motion_enabled:
            return
        if "CAMERA_STALE" not in self.last_preflight.reason_codes:
            return
        if not self._camera_is_fresh(runtime):
            return
        now = self._monotonic()
        if now - self._last_camera_recovery_at < 1.0:
            return
        self._last_camera_recovery_at = now
        result = self.run_preflight(
            runtime,
            actuator_arm_requested=self.actuator_arm_requested,
            update_arm_intent=False,
        )
        self.logger.emit(
            LogLevel.INFO if result.physical_motion_enabled else LogLevel.WARN,
            "COMMAND_GATEWAY",
            "Camera recovery preflight completed",
            {
                "physical_motion_enabled": result.physical_motion_enabled,
                "physical_fire_enabled": result.physical_fire_enabled,
                "reason_codes": result.reason_codes,
            },
        )

    def _safe_live_runtime(
        self,
        runtime: "RuntimeState",
        reasons: list[str],
        *,
        attempt_serial_stop: bool = True,
    ) -> None:
        self._stop_pose_estimate(runtime, command="gateway_safe_stop")
        was_active = self.last_preflight.ready or self.actuator_armed or self.driver_enabled or runtime.force_armed
        if was_active and attempt_serial_stop:
            self.serial.gateway_safe_stop()
        reason_set = set(reasons)
        if not self.serial.light_protocol_enabled:
            is_hard_fault = bool(reason_set.intersection({"ESTOP_ACTIVE", "PICO_POWER_FAULT", "PICO_CONNECTION_FAULT", "PICO_HEARTBEAT_STALE"}))
            if is_hard_fault:
                self.actuator_armed = False
                self.driver_enabled = False
                runtime.force_armed = False
        else:
            if "ESTOP_ACTIVE" in reason_set:
                self.actuator_armed = False
                self.driver_enabled = False
                runtime.force_armed = False
        self._set_home_in_progress(False)
        self._light_home_deadline = None
        updated_gates = []
        for gate in self.last_preflight.gates:
            if "CAMERA_STALE" in reason_set and gate.code in {"CAMERA_FRESH", "CAMERA_STALE"}:
                gate = gate.model_copy(update={"code": "CAMERA_STALE", "ready": False, "detail": "Latest real camera frame is stale or unavailable."})
            elif reason_set.intersection({"PICO_CONNECTION_FAULT", "PICO_HEARTBEAT_STALE"}) and gate.code.startswith("PICO_"):
                code = "PICO_CONNECTION_FAULT" if "PICO_CONNECTION_FAULT" in reason_set else "PICO_HEARTBEAT_STALE"
                gate = gate.model_copy(update={"code": code, "ready": False, "detail": "Pico heartbeat/connection is unavailable."})
            elif "ESTOP_ACTIVE" in reason_set and gate.code in {"ESTOP_RELEASED", "ESTOP_ACTIVE", "ESTOP_STATE_UNKNOWN"}:
                gate = gate.model_copy(update={"code": "ESTOP_ACTIVE", "ready": False, "detail": "Physical E-Stop is active."})
            elif "ACTUATOR_NOT_ARMED" in reason_set and gate.code in {"ACTUATOR_ARMED", "ACTUATOR_NOT_ARMED"}:
                gate = gate.model_copy(update={"code": "ACTUATOR_NOT_ARMED", "ready": False, "detail": "Actuator is not armed."})
            updated_gates.append(gate)
        self.last_preflight = self.last_preflight.model_copy(
            update={
                "ready": False,
                "physical_motion_enabled": False,
                "physical_fire_enabled": False,
                "actuator_armed": self.actuator_armed,
                "reason_codes": sorted(set([*self.last_preflight.reason_codes, *reasons])),
                "gates": updated_gates,
            }
        )
        runtime.last_safety_event = ("safety.gateway_safed", self.last_preflight.model_dump(mode="json"))

    def refresh_motion_estimate(self, runtime: "RuntimeState" | None = None) -> None:
        """Advance and publish the non-encoder live pose estimate.

        This method is safe to call from the maintenance loop and read-only
        digital-twin requests.  It never writes serial or enables hardware.
        """
        bound_runtime = runtime or self.runtime
        if bound_runtime is None:
            return
        with self._pose_lock:
            self._integrate_pose_locked(bound_runtime)
            self._publish_pose_locked(bound_runtime, command="gateway_open_loop_estimate")

    def _set_pose_speed(self, runtime: "RuntimeState", speed_x: float, speed_y: float) -> None:
        with self._pose_lock:
            self._integrate_pose_locked(runtime)
            motion = runtime.config.motion
            command_scale = max(float(motion.command_full_scale), 1e-6)
            self._pose_speed_x = (
                max(-command_scale, min(command_scale, float(speed_x)))
                / command_scale
                * float(motion.pan_max_steps_per_second)
            )
            self._pose_speed_y = (
                max(-command_scale, min(command_scale, float(speed_y)))
                / command_scale
                * float(motion.tilt_max_steps_per_second)
            )
            self._publish_pose_locked(runtime, command="gateway_open_loop_estimate")

    def _stop_pose_estimate(self, runtime: "RuntimeState", *, command: str) -> None:
        with self._pose_lock:
            self._integrate_pose_locked(runtime)
            self._pose_speed_x = 0.0
            self._pose_speed_y = 0.0
            self._publish_pose_locked(runtime, command=command)
        if getattr(self.serial, "light_protocol_enabled", False):
            try:
                res = self.serial.gateway_exchange("POZ", ("OK,POZ",))
                if res.accepted:
                    self._apply_position_response(runtime, res.reason, command="POZ")
            except Exception:
                pass

    def _integrate_pose_locked(self, runtime: "RuntimeState") -> None:
        now = self._monotonic()
        elapsed_s = max(0.0, now - self._pose_updated_at)
        self._pose_updated_at = now
        if elapsed_s <= 0.0:
            return
        self._pose_pan_steps += self._pose_speed_x * elapsed_s
        self._pose_tilt_steps += self._pose_speed_y * elapsed_s
        motion = runtime.config.motion
        pan_scale = max(float(motion.pan_steps_per_degree), 1e-6)
        tilt_scale = max(float(motion.tilt_steps_per_degree), 1e-6)
        if motion.soft_limits_enabled:
            self._pose_pan_steps = min(max(self._pose_pan_steps, motion.pan_min_deg * pan_scale), motion.pan_max_deg * pan_scale)
            self._pose_tilt_steps = min(max(self._pose_tilt_steps, motion.tilt_min_deg * tilt_scale), motion.tilt_max_deg * tilt_scale)

    def _publish_pose_locked(self, runtime: "RuntimeState", *, command: str) -> None:
        motion = runtime.config.motion
        pan_scale = max(float(motion.pan_steps_per_degree), 1e-6)
        tilt_scale = max(float(motion.tilt_steps_per_degree), 1e-6)
        pan_deg = self._pose_pan_steps / pan_scale
        tilt_deg = self._pose_tilt_steps / tilt_scale
        moving = bool(self._pose_speed_x or self._pose_speed_y)
        physical_pan = pan_deg + 135.0
        physical_tilt = 30.0 - tilt_deg
        runtime.motion.state = runtime.motion.state.model_copy(
            update={
                "motion_state": MotionStateValue.JOGGING if moving else MotionStateValue.STOPPED,
                "pan_position_deg": pan_deg,
                "tilt_position_deg": tilt_deg,
                "physical_pan_deg": physical_pan,
                "physical_tilt_deg": physical_tilt,
                "pan_target_deg": pan_deg,
                "tilt_target_deg": tilt_deg,
                "pan_position_steps": int(round(self._pose_pan_steps)),
                "tilt_position_steps": int(round(self._pose_tilt_steps)),
                "pan_error_deg": 0.0,
                "tilt_error_deg": 0.0,
                "driver_enabled": self.driver_enabled,
                "dry_run": self.profile == CommandProfile.DRY_RUN,
                "last_command": command,
                "last_error": None,
                "limit_reason_code": self._last_soft_limit_hit,
                "updated_at": time.time(),
            }
        )

    def _apply_motion_guards(self, runtime: "RuntimeState", speed_x: int | float, speed_y: int | float, origin: str) -> tuple[int, int]:
        """Eksen kilitleri + arama-fazı tilt kilidi + yazılımsal PAN/TILT limiti + yumuşak ramp koruması.

        Kalibrasyon eksen kilitleri her origin için geçerlidir; arama/süpürme
        (``sweep``) origin'inde tilt koşulsuz 0'da tutulur; yumuşak ramp yalnızca
        süpürme el değiştirmesine uygulanır (takip = 21 Eylül taban çizgisi).
        Yazılımsal PAN/TILT limiti (``config.yaml``'daki ``motion.soft_limits_enabled``)
        Light ve klasik raw protokolde aynı şekilde uygulanır: sınırı zorlayan
        yöndeki hız 0'a çekilir, sınırın içine dönüş asla engellenmez.
        """
        if getattr(self, "tilt_axis_locked", False):
            speed_y = 0.0
        if getattr(self, "pan_axis_locked", False):
            speed_x = 0.0
        # Arama/süpürme fazında tilt ekseni koşulsuz kilitli: radar ararken
        # Y'ye giden tek bir komut bile kadraj kenarı salınımını tetikleyebilir.
        if origin == "sweep":
            speed_y = 0

        speed_x, speed_y = self._clamp_to_soft_limits(runtime, speed_x, speed_y)

        if getattr(self, "soft_ramp_enabled", True) and origin == "sweep":
            sx, sy = self._apply_soft_ramp(float(speed_x), float(speed_y))
            return int(round(sx)), int(round(sy))
        # 21 September baseline: Tracking speeds flow with zero artificial latency
        self._ramp_speed_x = float(speed_x)
        self._ramp_speed_y = float(speed_y)
        self._last_ramp_time = self._monotonic()
        return int(speed_x), int(speed_y)

    def _clamp_to_soft_limits(self, runtime: "RuntimeState", speed_x: int | float, speed_y: int | float) -> tuple[float, float]:
        """PAN/TILT yazılımsal sınırını zorlayan yönü 0'a düşür (yönlü engelleme).

        Bu kontrol protokolden bağımsızdır: Light protokolde artık PAN
        sınırının yanı sıra TILT sınırı da burada zorlanır (önceden yalnızca
        ``_movement_boundary_reasons`` içinde, ve yalnız Light *değilken*
        kontrol ediliyordu). Sınıra dayanan eksen bu tick'te speed=0 olur;
        sınırın içine dönen komut asla etkilenmez; diğer eksen serbest kalır,
        yani tüm hareket komutu reddedilmez — yalnızca ilgili eksen durur.
        """
        motion = runtime.config.motion
        if not motion.soft_limits_enabled:
            self._last_soft_limit_hit = None
            return speed_x, speed_y
        state = runtime.motion.status()
        pan_deg: float | None = state.pan_position_deg
        tilt_deg: float | None = state.tilt_position_deg
        if getattr(self.serial, "light_protocol_enabled", False):
            # (30 Eylul) Light protokolde acik-cevrim konum tahmini gercek motordan hizli ilerleyip
            # taret limitte degilken "limitte" sanip durduruyordu. Pan: yalniz son Pico POZ konumu
            # (<= 1 s) kullanilir, yoksa engellenmez (Pico firmware 0-270 + LIMIT,PAN korur).
            # Tilt: ayni sekilde son POZ dikey konumu (firmware LIMIT,TILT bilmiyor; 0-60'i kendisi korur).
            poz = getattr(self, "_y30_son_poz", None)
            taze = poz is not None and (self._monotonic() - poz[2]) <= 1.0
            pan_deg = poz[0] if taze else None
            tilt_deg = poz[1] if taze else None
        hit: str | None = None
        if pan_deg is not None:
            if speed_x < 0 and pan_deg <= motion.pan_min_deg:
                speed_x = 0
                hit = "PAN_MIN_LIMIT"
            elif speed_x > 0 and pan_deg >= motion.pan_max_deg:
                speed_x = 0
                hit = "PAN_MAX_LIMIT"
        if tilt_deg is not None:
            if speed_y < 0 and tilt_deg <= motion.tilt_min_deg:
                speed_y = 0
                hit = hit or "TILT_MIN_LIMIT"
            elif speed_y > 0 and tilt_deg >= motion.tilt_max_deg:
                speed_y = 0
                hit = hit or "TILT_MAX_LIMIT"
        self._last_soft_limit_hit = hit
        return speed_x, speed_y

    def _apply_soft_ramp(self, target_speed_x: float, target_speed_y: float) -> tuple[float, float]:
        """Slew-rate limit commanded velocities to prevent acceleration jerk and overshoot."""
        now = self._monotonic()
        dt = max(0.001, min(0.05, now - getattr(self, "_last_ramp_time", now)))
        self._last_ramp_time = now

        max_dx = self.soft_ramp_accel_pan * dt
        max_dy = self.soft_ramp_accel_tilt * dt

        prev_x = getattr(self, "_ramp_speed_x", 0.0)
        prev_y = getattr(self, "_ramp_speed_y", 0.0)

        ramped_x = max(prev_x - max_dx, min(prev_x + max_dx, target_speed_x))
        ramped_y = max(prev_y - max_dy, min(prev_y + max_dy, target_speed_y))

        self._ramp_speed_x = ramped_x
        self._ramp_speed_y = ramped_y
        return ramped_x, ramped_y

    def reset_soft_ramp(self) -> None:
        """Reset soft velocity ramp states on tracking engage/disengage."""
        self._ramp_speed_x = 0.0
        self._ramp_speed_y = 0.0
        self._last_ramp_time = self._monotonic()

    def set_axis_lock(self, axis: str, locked: bool) -> bool:
        """Lock or unlock an axis during calibration or maintenance."""
        target_axis = axis.lower().strip()
        if target_axis == "tilt":
            self.tilt_axis_locked = bool(locked)
            if self.tilt_axis_locked:
                with self._pose_lock:
                    self._pose_speed_y = 0.0
                if getattr(self.serial, "light_protocol_enabled", False):
                    send_fn = getattr(self.serial, "send_light_motion", None)
                    if send_fn is not None:
                        send_fn(None, "Y 0")
            return self.tilt_axis_locked
        elif target_axis == "pan":
            self.pan_axis_locked = bool(locked)
            if self.pan_axis_locked:
                with self._pose_lock:
                    self._pose_speed_x = 0.0
                if getattr(self.serial, "light_protocol_enabled", False):
                    send_fn = getattr(self.serial, "send_light_motion", None)
                    if send_fn is not None:
                        send_fn("X 0", None)
            return self.pan_axis_locked
        return False

    def _execute_trigger_pulse(
        self,
        *,
        command_label: str,
        raw_command: str,
        expected_ack: tuple[str, ...],
        pulse_s: float,
        count_physical_shot: bool,
        detail: str,
    ) -> GatewayCommandResult:
        """Emit at most one physical trigger pulse at a time.

        Frontend edge detection improves USB ergonomics, but the Gateway is
        the authoritative cross-tab/cross-input boundary.  The reservation
        covers write, ACK and release scheduling so simultaneous browser
        requests cannot both consume a round.
        """
        with self._fire_command_lock:
            deadline = self.fire_release_at
            if deadline is not None and time.time() < deadline:
                return self._blocked(command_label, ["FIRE_PULSE_ACTIVE"])
            if getattr(self.serial, "light_protocol_enabled", False):
                send_cmd = getattr(self.serial, "send_light_command", None)
                if send_cmd is not None:
                    send_cmd(raw_command)
                    if count_physical_shot:
                        self.serial.magazine_remaining = max(0, self.serial.magazine_remaining - 1)
                        self.serial.acknowledged_shot_count += 1
                        self.serial.magazine_updated_at = time.time()
                    self._schedule_fire_release(min(0.50, float(pulse_s)))
                    return GatewayCommandResult(
                        accepted=True,
                        command=raw_command,
                        detail="Light async trigger queued",
                        pico_ack="OK,QUEUED",
                        physical_command_generated=True,
                    )
            command = self.serial.gateway_exchange(
                raw_command,
                expected_ack,
                count_physical_shot=count_physical_shot,
            )
            if not command.accepted:
                if "TRIGGER_NOT_ARMED" in command.reason:
                    # This valid NACK proves the serial link is alive and is
                    # authoritative evidence that Pico's physical ARM is off.
                    # Correct readiness only; emit no follow-up command.
                    self.actuator_armed = False
                    self.last_preflight = self.last_preflight.model_copy(
                        update={
                            "physical_fire_enabled": False,
                            "ready": False,
                            "actuator_armed": False,
                            "reason_codes": sorted(set([*self.last_preflight.reason_codes, "ACTUATOR_NOT_ARMED"])),
                        }
                    )
                return self._blocked(command_label, self._fire_serial_reason_codes(command.reason), command.reason)
            self._schedule_fire_release(pulse_s)
            return GatewayCommandResult(
                accepted=True,
                command=raw_command,
                detail=detail,
                pico_ack=command.reason,
                physical_command_generated=not command.no_physical_command_generated,
            )

    def _schedule_fire_release(self, pulse_s: float) -> None:
        """Release the physical trigger independently of UI/WS activity."""
        delay_s = max(0.01, float(pulse_s))
        with self._fire_release_lock:
            if self._fire_release_timer is not None:
                self._fire_release_timer.cancel()
            self.fire_release_at = time.time() + delay_s
            timer = threading.Timer(delay_s, self._release_fire_output, kwargs={"force": True})
            timer.daemon = True
            self._fire_release_timer = timer
            timer.start()

    def _release_fire_output(self, *, force: bool) -> None:
        with self._fire_release_lock:
            deadline = self.fire_release_at
            if deadline is None or (not force and time.time() < deadline):
                return
            timer = self._fire_release_timer
            self._fire_release_timer = None
            self.fire_release_at = None
            if timer is not None and timer is not threading.current_thread():
                timer.cancel()
        if self.serial.light_protocol_enabled:
            self.logger.emit(
                LogLevel.INFO,
                "COMMAND_GATEWAY",
                "Light firmware trigger release skipped",
                {"accepted": False, "reason_code": "PICO_TRIGGER_UNSUPPORTED"},
            )
            return
        released = self.serial.gateway_exchange(
            "LZR,0",
            ("OK,LASER_0", "FIRE_SERVO_RELEASED", "ERR,TRIGGER_NOT_ARMED"),
        )
        self.logger.emit(
            LogLevel.INFO if released.accepted else LogLevel.ERROR,
            "COMMAND_GATEWAY",
            "Trigger release command completed",
            {"accepted": released.accepted, "detail": released.reason},
        )

    def _apply_position_response(self, runtime: "RuntimeState", response: str, *, command: str) -> HardwareHomeResult:
        def token(name: str) -> str | None:
            match = re.search(rf"(?:^|[,|]){re.escape(name)}(?:=|:)([^,|;\s]+)", response)
            return match.group(1) if match else None

        light_position = self.serial.light_protocol_enabled or response.startswith("OK,POZ")
        try:
            if light_position:
                # Light firmware reports X=physical tilt and Y=physical pan;
                # it does not report encoder/step counters.
                physical_tilt = float(token("X") or "nan")
                physical_pan = float(token("Y") or "nan")
                pan_steps_raw = tilt_steps_raw = None
            else:
                physical_pan = float(token("PAN") or "nan")
                physical_tilt = float(token("TILT") or "nan")
                pan_steps_raw = int(token("PAN_STEPS") or "0")
                tilt_steps_raw = int(token("TILT_STEPS") or "0")
            if not (physical_pan == physical_pan and physical_tilt == physical_tilt):
                raise ValueError("missing position fields")
        except (TypeError, ValueError):
            return HardwareHomeResult(accepted=False, phase="FAULT", reason_codes=["PICO_POSITION_PARSE_FAILED"], detail=response)
        current = runtime.motion.status()
        phase = token("STATE") or (current.homing_phase if light_position and self.home_in_progress else "UNKNOWN")
        homed = token("HOMED") == "1" if not light_position else current.homed
        limit_x = token("LIMIT_X") == "1" if not light_position else current.tilt_limit_up
        limit_y = token("LIMIT_Y") == "1" if not light_position else current.pan_limit_left
        limit_code = token("LIMIT_CODE")
        fault = token("FAULT")
        if fault == "NONE":
            fault = None
        if limit_code == "NONE":
            limit_code = None
        pan_semantic = physical_pan - 135.0
        tilt_semantic = 30.0 - physical_tilt
        pan_left = False if light_position else (limit_y or limit_code == "PAN_MIN_LIMIT")
        pan_right = False if light_position else (limit_code == "PAN_MAX_LIMIT" or physical_pan >= self.motion_envelope.pan_max_deg - 0.02)
        tilt_up = False if light_position else (limit_x or limit_code == "TILT_MIN_LIMIT")
        tilt_down = False if light_position else (limit_code == "TILT_MAX_LIMIT" or physical_tilt >= self.motion_envelope.tilt_max_deg - 0.02)
        state_name = MotionStateValue.FAULT if fault else MotionStateValue.HOMING if self.home_in_progress and light_position else MotionStateValue.HOMING if phase not in {"DONE", "IDLE"} else MotionStateValue.STOPPED
        runtime.motion.state = runtime.motion.state.model_copy(update={
            "motion_state": state_name,
            "pan_position_deg": pan_semantic,
            "tilt_position_deg": tilt_semantic,
            "pan_target_deg": pan_semantic,
            "tilt_target_deg": tilt_semantic,
            "pan_position_steps": int(round(pan_semantic * runtime.config.motion.pan_steps_per_degree)),
            "tilt_position_steps": int(round(tilt_semantic * runtime.config.motion.tilt_steps_per_degree)),
            "pan_limit_left": pan_left,
            "pan_limit_right": pan_right,
            "tilt_limit_up": tilt_up,
            "tilt_limit_down": tilt_down,
            "homed": homed,
            "homing_phase": phase,
            "limit_reason_code": fault or limit_code or (current.limit_reason_code if light_position else None),
            "physical_pan_deg": physical_pan,
            "physical_tilt_deg": physical_tilt,
            "driver_enabled": self.driver_enabled,
            "last_command": command,
            "last_error": fault,
            "updated_at": time.time(),
        })
        with self._pose_lock:
            self._pose_pan_steps = pan_semantic * runtime.config.motion.pan_steps_per_degree
            self._pose_tilt_steps = tilt_semantic * runtime.config.motion.tilt_steps_per_degree
            self._pose_speed_x = 0.0
            self._pose_speed_y = 0.0
            self._pose_updated_at = self._monotonic()
            self._y30_son_poz = (pan_semantic, tilt_semantic, self._pose_updated_at)
            try:  # SIM_POZ
                self.logger.emit(LogLevel.INFO, "SIM_POZ", "Pico POZ", {"pan": round(float(pan_semantic), 4), "tilt": round(float(tilt_semantic), 4), "mono": round(float(self._pose_updated_at), 4), "cmd": command})
            except Exception:
                pass
        if not light_position:
            self._set_home_in_progress(not bool(fault) and phase in {"TILT_SEEK", "PAN_SEEK", "CENTERING"})
        reasons = [fault] if fault else []
        if limit_code:
            reasons.append(limit_code)
        detail = response
        if light_position:
            detail += "; light firmware does not report home/limit/step telemetry"
        return HardwareHomeResult(
            accepted=not bool(fault), command=command, phase=phase, homed=homed,
            reason_codes=sorted(set(reasons)), detail=detail,
            pan_deg=physical_pan, tilt_deg=physical_tilt,
            pan_steps=pan_steps_raw, tilt_steps=tilt_steps_raw,
            limit_x_active=limit_x, limit_y_active=limit_y,
            limit_reason_code=fault or limit_code,
        )

    def _apply_envelope_to_runtime(self, runtime: "RuntimeState", envelope: HardwareMotionEnvelope) -> None:
        # Runtime/digital-twin coordinates are centred and camera-relative;
        # firmware coordinates are absolute from the two home switches.
        motion = runtime.config.motion
        motion.pan_min_deg = envelope.pan_min_deg - 135.0
        motion.pan_max_deg = envelope.pan_max_deg - 135.0
        motion.tilt_min_deg = 30.0 - envelope.tilt_max_deg
        motion.tilt_max_deg = 30.0 - envelope.tilt_min_deg
        motion.soft_limits_enabled = True
        runtime.motion.settings.pan_min_deg = motion.pan_min_deg
        runtime.motion.settings.pan_max_deg = motion.pan_max_deg
        runtime.motion.settings.tilt_min_deg = motion.tilt_min_deg
        runtime.motion.settings.tilt_max_deg = motion.tilt_max_deg
        runtime.motion.settings.soft_limits_enabled = True

    def _load_motion_envelope(self, runtime: "RuntimeState") -> None:
        envelope = HardwareMotionEnvelope()
        if self._motion_envelope_path.exists():
            try:
                envelope = HardwareMotionEnvelope.model_validate(json.loads(self._motion_envelope_path.read_text(encoding="utf-8")))
            except (OSError, ValueError, TypeError):
                self.logger.emit(LogLevel.WARN, "COMMAND_GATEWAY", "Stored motion envelope ignored", {"reason_code": "MOTION_ENVELOPE_STATE_INVALID"})
        self.motion_envelope = envelope

    def _persist_motion_envelope(self, envelope: HardwareMotionEnvelope) -> None:
        self._motion_envelope_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._motion_envelope_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(envelope.model_dump(mode="json"), indent=2), encoding="utf-8")
        temporary.replace(self._motion_envelope_path)

    def _home_start_reasons(self, runtime: "RuntimeState") -> list[str]:
        """Health gates for the recovery command that establishes position.

        HOME must be able to recover an old HOME timeout or an out-of-envelope
        open-loop pose; requiring the ordinary motion-ready bit here creates a
        circular lock.  Pico handshake, real E-Stop, live profile and current
        camera evidence remain mandatory and are checked directly.
        """
        reasons: list[str] = []
        if self.profile == CommandProfile.DRY_RUN:
            reasons.append("DRY_RUN_ACTIVE")
        if runtime.config.system.dry_run or not runtime.config.system.hardware_enabled:
            reasons.append("LIVE_PROFILE_NOT_ACTIVE")
        if not self._camera_is_fresh(runtime):
            reasons.append("CAMERA_STALE")
        if runtime.motion.status().estop_state:
            reasons.append("ESTOP_ACTIVE")

        ping = self.serial.gateway_exchange("PING", ("OK,PONG", "PONG"))
        if not ping.accepted:
            reasons.extend(["PICO_CONNECTION_FAULT", "PICO_HEARTBEAT_STALE"])
            return sorted(set(reasons))
        self.pico_protocol = "light_raw" if self.serial.light_protocol_enabled else "arduino_raw" if "OK,PONG" in ping.reason else "micropython_json"
        health_command = "HB" if self.serial.light_protocol_enabled else "STAT"
        health_expected = ("OK,HB",) if self.serial.light_protocol_enabled else ("OK,STAT", "STATUS")
        stat = self.serial.gateway_exchange(health_command, health_expected)
        if not stat.accepted:
            reasons.extend(["PICO_CONNECTION_FAULT", "ESTOP_STATE_UNKNOWN"])
            return sorted(set(reasons))
        self.pico_estop_active = self._estop_from_response(stat.reason)
        if self.pico_estop_active:
            reasons.append("ESTOP_ACTIVE")
        elif self.serial.light_protocol_enabled:
            if not self._light_health_fields_present(stat.reason):
                reasons.extend(["PICO_HEALTH_FIELDS_MISSING", "ESTOP_STATE_UNKNOWN"])
            elif self._light_power_is_ok(stat.reason) is False:
                reasons.append("PICO_POWER_FAULT")
        return sorted(set(reasons))

    def _mark_home_failed(self, runtime: "RuntimeState", result: HardwareHomeResult) -> None:
        self._set_home_in_progress(False)
        self._light_home_deadline = None
        codes = result.reason_codes or ["PICO_HOME_COMMAND_FAILED"]
        self.actuator_armed = False
        self.driver_enabled = False
        runtime.force_armed = False
        self.last_preflight = self.last_preflight.model_copy(update={
            "ready": False,
            "physical_motion_enabled": False,
            "physical_fire_enabled": False,
            "actuator_armed": False,
            "reason_codes": sorted(set([*self.last_preflight.reason_codes, *codes])),
        })

    def _light_home_timed_out(self) -> bool:
        return (
            self.serial.light_protocol_enabled
            and self.home_in_progress
            and self._light_home_deadline is not None
            and self._monotonic() >= self._light_home_deadline
        )

    def _light_home_timeout(self, runtime: "RuntimeState") -> HardwareHomeResult:
        self.serial.gateway_safe_stop()
        result = HardwareHomeResult(
            accepted=False,
            phase="FAULT",
            reason_codes=["PICO_HOME_TIMEOUT"],
            detail=f"Light HOME did not report CENTER_DONE within {int(self.LIGHT_HOME_TIMEOUT_S)}s; safe-stop requested.",
        )
        self._mark_home_failed(runtime, result)
        runtime.motion.state = runtime.motion.state.model_copy(update={
            "motion_state": MotionStateValue.FAULT,
            "homed": False,
            "homing_phase": "FAULT",
            "last_command": "HOME",
            "last_error": "PICO_HOME_TIMEOUT",
            "updated_at": time.time(),
        })
        return result

    @staticmethod
    def _home_serial_reason_codes(detail: str) -> list[str]:
        known = (
            "ESTOP_ACTIVE", "HOME_TILT_TIMEOUT", "HOME_PAN_TIMEOUT", "HOME_CENTER_TIMEOUT",
            "HOME_SWITCH_STUCK", "HOME_IN_PROGRESS", "HOME_CANCELLED", "HOME_FAIL", "HOME_ABORT",
            "PICO_HEARTBEAT_STALE",
            "PICO_ACK_TIMEOUT", "PICO_CONNECTION_FAULT",
        )
        found = [code for code in known if code in detail]
        return found or ["PICO_HOME_COMMAND_FAILED"]

    def _live_reasons(self, runtime: "RuntimeState", require_actuator_arm: bool) -> list[str]:
        reasons: list[str] = []
        operation = getattr(runtime, "operation", None)
        fire_permitted = operation is not None and operation.state().fire_permission.value == "ENABLED"
        if self.serial.light_protocol_enabled:
            # Light protocol: matches the standalone reference ``otonom_takip.py`` design.
            # There are no artificial preflight locks, heartbeat timeouts, or camera freshness
            # requirements blocking motor commands. Rates flow directly to the Pico.
            if self.serial.connection_state.name == "FAULT":
                # If physical transport is open, heal the state and do not block
                if self.serial._real_transport is not None and getattr(self.serial._real_transport, "is_open", False):
                    self.serial.connection_state = SerialConnectionState.PORT_OPEN_NO_TELEMETRY
                    self.serial.consecutive_ping_failures = 0
                    self.serial.last_error = None
                    self.serial.last_transport_fault = None
                else:
                    reasons.append("PICO_CONNECTION_FAULT")
            if runtime.motion.status().estop_state or self.pico_estop_active is True:
                reasons.append("ESTOP_ACTIVE")
            if require_actuator_arm and not self.actuator_armed and not fire_permitted:
                reasons.append("ACTUATOR_NOT_ARMED")
            return sorted(set(reasons))

        if self.profile == CommandProfile.DRY_RUN:
            reasons.append("DRY_RUN_ACTIVE")

        if require_actuator_arm and not self.last_preflight.physical_fire_enabled:
            reasons.append("PREFLIGHT_NOT_READY")
        if not require_actuator_arm and not self.last_preflight.physical_motion_enabled:
            reasons.append("PREFLIGHT_NOT_READY")
        if runtime.config.system.dry_run or not runtime.config.system.hardware_enabled:
            reasons.append("LIVE_PROFILE_NOT_ACTIVE")
        if self.serial.connection_state.name == "FAULT":
            reasons.append("PICO_CONNECTION_FAULT")
        if not self._heartbeat_is_fresh():
            reasons.append("PICO_HEARTBEAT_STALE")
        if not self._camera_is_fresh(runtime):
            reasons.append("CAMERA_STALE")
        if runtime.motion.status().estop_state:
            reasons.append("ESTOP_ACTIVE")
        if self.pico_estop_active is True:
            reasons.append("ESTOP_ACTIVE")
        if require_actuator_arm and not self.actuator_armed:
            reasons.append("ACTUATOR_NOT_ARMED")
        return sorted(set(reasons))

    @staticmethod
    def _movement_boundary_reasons(runtime: "RuntimeState", speed_x: int, speed_y: int) -> list[str]:
        """Reject a command that exceeds configured speed/soft/physical limits.

        Pico firmware remains the final electrical/limit authority.  This
        gateway check gives the operator a deterministic, visible reason
        before a command reaches that last-resort layer.
        """
        state = runtime.motion.status()
        motion = runtime.config.motion
        max_speed = int(runtime.config.motor.max_speed)
        reasons: list[str] = []
        if abs(int(speed_x)) > max_speed or abs(int(speed_y)) > max_speed:
            reasons.append("MOTION_SPEED_LIMIT")
        if getattr(runtime.serial, "light_protocol_enabled", False):
            return sorted(set(reasons))
        if speed_x < 0 and state.pan_limit_left:
            reasons.append("PAN_LEFT_LIMIT_ACTIVE")
        if speed_x > 0 and state.pan_limit_right:
            reasons.append("PAN_RIGHT_LIMIT_ACTIVE")
        if speed_y > 0 and state.tilt_limit_up:
            reasons.append("TILT_UP_LIMIT_ACTIVE")
        if speed_y < 0 and state.tilt_limit_down:
            reasons.append("TILT_DOWN_LIMIT_ACTIVE")
        if motion.soft_limits_enabled:
            if speed_x < 0 and state.pan_position_deg <= motion.pan_min_deg:
                reasons.append("PAN_SOFT_LIMIT")
            if speed_x > 0 and state.pan_position_deg >= motion.pan_max_deg:
                reasons.append("PAN_SOFT_LIMIT")
            if speed_y < 0 and state.tilt_position_deg <= motion.tilt_min_deg:
                reasons.append("TILT_SOFT_LIMIT")
            if speed_y > 0 and state.tilt_position_deg >= motion.tilt_max_deg:
                reasons.append("TILT_SOFT_LIMIT")
        zone_name = active_zone_name(motion.motion_forbidden_zones, state.pan_position_deg, state.tilt_position_deg)
        if zone_name and (speed_x or speed_y):
            reasons.append("MOTION_FORBIDDEN_ZONE")
        return sorted(set(reasons))

    def _heartbeat_is_fresh(self) -> bool:
        if self.serial.gateway_last_heartbeat_at is None:
            return False
        age_ms = (time.time() - self.serial.gateway_last_heartbeat_at) * 1000
        return age_ms <= self.serial.config.serial.heartbeat_timeout_ms

    @staticmethod
    def _camera_is_fresh(runtime: "RuntimeState") -> bool:
        # Manual LIVE_TEST motion only needs proof that the selected physical
        # camera is producing current frames.  Requiring a detector event here
        # made the turret jog controls impossible to use before a YOLO model
        # was selected, even though the raw camera preview was healthy.
        gateway = getattr(runtime, "command_gateway", None)
        if gateway is not None and getattr(gateway.serial, "light_protocol_enabled", False):
            return True

        camera_runtime = runtime.camera_runtime
        frame_at = camera_runtime.last_frame_at
        if (
            camera_runtime.profile.source_type in {"usb", "laptop"}
            and frame_at is not None
            and not camera_runtime.capture_paused
        ):
            frame_age_s = time.time() - frame_at
            if -1.0 <= frame_age_s <= MAX_VISION_EVENT_AGE_S:
                return True

        event = runtime.vision.latest_event
        if event is None:
            return False
        age_s = time.time() - float(event.timestamp_ms) / 1000.0
        return -1.0 <= age_s <= MAX_VISION_EVENT_AGE_S

    @staticmethod
    def _estop_from_response(response: str) -> bool:
        return bool(re.search(r"(?:ESTOP|ACIL)[=:_]1", response))

    @staticmethod
    def _light_health_fields_present(response: str) -> bool:
        return (
            re.search(r"(?:^|[|,])24V[=:][01](?:$|[|,])", response) is not None
            and re.search(r"(?:^|[|,])ACIL[=:][01](?:$|[|,])", response) is not None
        )

    @staticmethod
    def _light_power_is_ok(response: str) -> bool | None:
        if "24V" not in response:
            return None
        match = re.search(r"24V[=:](0|1)", response)
        return None if match is None else match.group(1) == "1"

    def _blocked(self, command: str, reason_codes: list[str], detail: str | None = None) -> GatewayCommandResult:
        result = GatewayCommandResult(
            accepted=False,
            command=command,
            reason_codes=sorted(set(reason_codes)),
            detail=detail or "Physical command blocked by CommandGateway preflight.",
            physical_command_generated=False,
        )
        self.logger.emit(LogLevel.WARN, "COMMAND_GATEWAY", "Command rejected", result.model_dump(mode="json"))
        return result
