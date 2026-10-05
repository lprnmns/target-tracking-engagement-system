import time
import uuid

from app.schemas.config import AppConfig
from app.schemas.log import LogLevel
from app.schemas.motion import (
    MotionCommandResponse,
    MotionGoToRequest,
    MotionJogRequest,
    MotionSettings,
    MotionState,
    MotionStateValue,
    MotionTrackDryRunRequest,
    TrackingDryRunPreview,
)
from app.services.log_service import JsonlLogService


class MotionService:
    def __init__(self, config: AppConfig, logger: JsonlLogService) -> None:
        self.config = config
        self.logger = logger
        self.settings = MotionSettings(**config.motion.model_dump())
        self.state = self._state(MotionStateValue.IDLE)
        self.command_log: list[MotionCommandResponse] = []

    def status(self) -> MotionState:
        return self.state

    def update_settings(self, settings: MotionSettings, system_armed: bool) -> MotionSettings:
        if system_armed:
            raise ValueError("motion settings can be changed only while DISARMED")
        self.settings = settings
        # CommandGateway, DecisionEngine and the open-loop pose estimator read
        # the canonical AppConfig motion block. Keep that block synchronized
        # with the validated UI settings so a saved degree limit is enforced
        # by every command boundary in the current process, not only by the
        # dry-run MotionService endpoint.
        for field in (
            "pan_min_deg",
            "pan_max_deg",
            "tilt_min_deg",
            "tilt_max_deg",
            "pan_steps_per_degree",
            "tilt_steps_per_degree",
            "pan_max_speed_deg_s",
            "tilt_max_speed_deg_s",
            "pan_accel_deg_s2",
            "tilt_accel_deg_s2",
            "jog_step_deg",
            "deadband_px",
            "tracking_gain_x",
            "tracking_gain_y",
            "backlash_compensation_enabled",
            "soft_limits_enabled",
            "scan_enabled",
            "scan_min_deg",
            "scan_max_deg",
            "scan_speed_deg_s",
        ):
            setattr(self.config.motion, field, getattr(settings, field))
        self.logger.emit(LogLevel.INFO, "MOTION", "Motion settings updated", settings.model_dump(mode="json"))
        return self.settings

    def jog(self, request: MotionJogRequest, system_armed: bool) -> MotionCommandResponse:
        step = request.step_deg or self.settings.jog_step_deg
        pan = self.state.pan_position_deg
        tilt = self.state.tilt_position_deg
        if request.axis == "pan":
            pan += step if request.direction == "positive" else -step
        elif request.axis == "tilt":
            tilt += step if request.direction == "positive" else -step
        else:
            return self._reject("jog", {"axis": request.axis, "direction": request.direction}, ["invalid_axis"], "Invalid jog axis.")
        return self._apply("jog", pan, tilt, MotionStateValue.JOGGING, system_armed, {"axis": request.axis, "direction": request.direction, "step_deg": step})

    def go_to(self, request: MotionGoToRequest, system_armed: bool) -> MotionCommandResponse:
        return self._apply("go_to", request.pan_target_deg, request.tilt_target_deg, MotionStateValue.TRACKING_DRY_RUN, system_armed, request.model_dump())

    def home(self, system_armed: bool) -> MotionCommandResponse:
        return self._apply("home", 0.0, 0.0, MotionStateValue.HOMING, system_armed, {"pan_target_deg": 0.0, "tilt_target_deg": 0.0})

    def stop(self) -> MotionCommandResponse:
        self.state = self.state.model_copy(update={"motion_state": MotionStateValue.STOPPED, "last_command": "stop", "updated_at": time.time()})
        return self._record(True, "stop", {}, [], "Motion stopped in dry-run state.", None)

    def scan_start(self, system_armed: bool) -> MotionCommandResponse:
        if not self.settings.scan_enabled:
            return self._reject("scan_start", {}, ["scan_disabled"], "Scan is disabled in settings.")
        return self._apply("scan_start", self.settings.scan_min_deg, self.state.tilt_position_deg, MotionStateValue.SCANNING, system_armed, {})

    def scan_stop(self) -> MotionCommandResponse:
        self.state = self.state.model_copy(update={"motion_state": MotionStateValue.STOPPED, "last_command": "scan_stop", "updated_at": time.time()})
        return self._record(True, "scan_stop", {}, [], "Scan stopped.", None)

    def track_dry_run(self, request: MotionTrackDryRunRequest, system_armed: bool) -> MotionCommandResponse:
        frame_center_x = request.frame_width / 2
        frame_center_y = request.frame_height / 2
        error_x = request.target_center_x - frame_center_x
        error_y = request.target_center_y - frame_center_y
        preview = TrackingDryRunPreview(
            frame_center_x=frame_center_x,
            frame_center_y=frame_center_y,
            target_center_x=request.target_center_x,
            target_center_y=request.target_center_y,
            error_x_px=error_x,
            error_y_px=error_y,
            computed_pan_delta_deg=error_x * self.settings.tracking_gain_x,
            computed_tilt_delta_deg=error_y * self.settings.tracking_gain_y,
        )
        response = self._apply(
            "track_dry_run",
            self.state.pan_position_deg + preview.computed_pan_delta_deg,
            self.state.tilt_position_deg + preview.computed_tilt_delta_deg,
            MotionStateValue.TRACKING_DRY_RUN,
            system_armed,
            request.model_dump(),
            preview=preview,
        )
        return response

    def set_fault(self, error: str) -> None:
        self.state = self.state.model_copy(update={"motion_state": MotionStateValue.FAULT, "last_error": error, "updated_at": time.time()})

    def _apply(
        self,
        command_type: str,
        pan: float,
        tilt: float,
        state: MotionStateValue,
        system_armed: bool,
        requested: dict,
        preview: TrackingDryRunPreview | None = None,
    ) -> MotionCommandResponse:
        blocking = self._blocking(pan, tilt, system_armed, command_type, requested)
        if blocking:
            return self._reject(command_type, requested, blocking, f"{command_type} rejected by motion safety validation.", preview)
        self.state = self._state(state, pan, tilt, command_type)
        generated_steps = {"pan": int(round(pan * self.settings.pan_steps_per_degree)), "tilt": int(round(tilt * self.settings.tilt_steps_per_degree))}
        return self._record(True, command_type, requested, [], f"{command_type} accepted as dry-run simulation.", generated_steps, preview)

    def _blocking(self, pan: float, tilt: float, system_armed: bool, command_type: str, requested: dict) -> list[str]:
        reasons: list[str] = []
        if self.state.motion_state == MotionStateValue.FAULT and command_type not in {"stop"}:
            reasons.append("motion_fault")
        if system_armed:
            reasons.append("system_not_test_safe")
        if self.state.estop_state:
            reasons.append("estop_active")
        if self.config.system.hardware_enabled or not self.config.motion.dry_run or self.config.motion.real_motion_enabled:
            reasons.append("real_motion_disabled_by_phase7")
        if self.settings.soft_limits_enabled and not (self.settings.pan_min_deg <= pan <= self.settings.pan_max_deg):
            reasons.append("pan_soft_limit")
        if self.settings.soft_limits_enabled and not (self.settings.tilt_min_deg <= tilt <= self.settings.tilt_max_deg):
            reasons.append("tilt_soft_limit")
        axis = requested.get("axis")
        direction = requested.get("direction")
        if axis == "pan" and direction == "negative" and self.state.pan_limit_left:
            reasons.append("pan_left_limit_active")
        if axis == "pan" and direction == "positive" and self.state.pan_limit_right:
            reasons.append("pan_right_limit_active")
        if axis == "tilt" and direction == "positive" and self.state.tilt_limit_up:
            reasons.append("tilt_up_limit_active")
        if axis == "tilt" and direction == "negative" and self.state.tilt_limit_down:
            reasons.append("tilt_down_limit_active")
        return reasons

    def _reject(self, command_type: str, requested: dict, blocking: list[str], reason: str, preview: TrackingDryRunPreview | None = None) -> MotionCommandResponse:
        self.state = self.state.model_copy(update={"last_command": command_type, "last_error": reason, "updated_at": time.time()})
        return self._record(False, command_type, requested, blocking, reason, None, preview)

    def _record(
        self,
        accepted: bool,
        command_type: str,
        requested: dict,
        blocking: list[str],
        reason: str,
        generated_steps: dict[str, int] | None,
        preview: TrackingDryRunPreview | None = None,
    ) -> MotionCommandResponse:
        response = MotionCommandResponse(
            accepted=accepted,
            dry_run=True,
            command_id=str(uuid.uuid4()),
            command_type=command_type,
            requested_target=requested,
            blocking_reasons=blocking,
            safety_gates=[{"name": item, "status": "fail"} for item in blocking],
            generated_steps=generated_steps,
            no_physical_command_generated=True,
            reason=reason,
            state=self.state,
            tracking_preview=preview,
        )
        self.command_log.append(response)
        self.command_log = self.command_log[-200:]
        self.logger.emit(LogLevel.INFO if accepted else LogLevel.WARN, "MOTION", "Motion command evaluated", response.model_dump(mode="json"))
        return response

    def _state(self, state: MotionStateValue, pan: float = 0.0, tilt: float = 0.0, command: str | None = None) -> MotionState:
        return MotionState(
            motion_state=state,
            pan_position_deg=pan,
            tilt_position_deg=tilt,
            pan_target_deg=pan,
            tilt_target_deg=tilt,
            pan_position_steps=int(round(pan * self.settings.pan_steps_per_degree)),
            tilt_position_steps=int(round(tilt * self.settings.tilt_steps_per_degree)),
            pan_error_deg=0.0,
            tilt_error_deg=0.0,
            driver_enabled=False,
            estop_state=False,
            dry_run=True,
            last_command=command,
            updated_at=time.time(),
        )
