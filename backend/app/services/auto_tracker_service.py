"""AutoTrackerService - kapalı çevrim hedef takip servisi.

The standard cockpit AUTONOMOUS path uses the field-tested Light General
pixel controller. Competition stages 2/3 explicitly select Light A2/A3;
legacy callers can still request the original normalized controller.
"""

from __future__ import annotations

import math
import time

from app.schemas.config import AppConfig
from app.schemas.log import LogLevel
from app.schemas.tracking import TrackingConfigUpdate, TrackingFireResult, TrackingState, TrackingStatus, TrackingUpdate
from app.services.light_tracking_controller import LightTrackingController
from app.services import yarisma_bayraklari as yb
from app.services.multi_target_tracker_service import MultiTargetTrackerService
from app.schemas.vision import VisionEvent
from app.services.log_service import JsonlLogService
from app.services.storage_paths import project_root
from app.services import stage3_roi


# A camera measurement can jump by more than one pixel between inference
# frames (especially after a model/GPU scheduling hiccup).  Without a bound,
# the derivative term turns that one-frame jump into a full-speed reversal.
# The limit is expressed in normalized-image-error units per second.
MAX_NORMALIZED_ERROR_RATE = 1.5
# Rollback baseline: the field-tested MicroPython firmware applies the target
# speed directly (its own delay mapping is the only actuator smoothing).  The
# temporary TMC experiment added a second host-side slew limiter, which made
# the closed loop lag and oscillate.  Keep the hook for future A/B work, but
# disable it for the active rollback profile.
COMMAND_SLEW_RATE_UNITS_S = 0.0
LOCK_EXIT_MULTIPLIER = 1.5
# Stop autonomous correction as soon as the aim point enters the centered
# rectangle obtained by shrinking the detected bbox by 70% on both axes.
# In other words, the live lock area is 30% of bbox width and height.
TRACKING_INNER_BBOX_RATIO = 0.30
LIGHT_CONTROLLER_MODES = {"GENERAL", "A2", "A3", "OPT_D_FILTER", "OPT_ZONE_GAIN", "OPT_SINE_TRACK"}
GENERAL_FAMILY_MODES = {"GENERAL"}
A2_FAMILY_MODES = {"A2", "A3", "OPT_D_FILTER", "OPT_ZONE_GAIN", "OPT_SINE_TRACK"}


class AutoTrackerService:
    """Kapalı çevrim takip kontrolcüsü.

    Each update selects a target and produces a safe semantic speed vector.
    Stage 2/3 use the Light pixel-space controller; other callers use the
    existing normalized controller.
    """

    def __init__(self, config: AppConfig, logger: JsonlLogService, ballistics: Any = None) -> None:
        self.config = config
        self.logger = logger
        self.ballistics = ballistics

        # PID katsayıları normalize görüntü hatasından hız birimi üretir.
        tracking_config = config.tracking
        self.pid_kp_x: float = tracking_config.pid_kp_x
        self.pid_ki_x: float = tracking_config.pid_ki_x
        self.pid_kd_x: float = tracking_config.pid_kd_x
        self.pid_kp_y: float = tracking_config.pid_kp_y
        self.pid_ki_y: float = tracking_config.pid_ki_y
        self.pid_kd_y: float = tracking_config.pid_kd_y
        self.output_min: float = tracking_config.output_min
        self.output_max: float = tracking_config.output_max
        self.integral_max: float = tracking_config.integral_max
        self._legacy_pid_values = {
            "pid_kp_x": self.pid_kp_x,
            "pid_ki_x": self.pid_ki_x,
            "pid_kd_x": self.pid_kd_x,
            "pid_kp_y": self.pid_kp_y,
            "pid_ki_y": self.pid_ki_y,
            "pid_kd_y": self.pid_kd_y,
        }

        # --- Smoothing & deadband ---
        self.smoothing_alpha: float = tracking_config.smoothing_alpha
        self.command_rate_hz: float = tracking_config.command_rate_hz
        self.max_speed: int = tracking_config.max_speed
        self.max_speed_x: int = tracking_config.max_speed_x or tracking_config.max_speed
        self.max_speed_y: int = tracking_config.max_speed_y or tracking_config.max_speed
        self.min_move_speed: float = tracking_config.min_move_speed
        self.deadband_lock_ratio: float = tracking_config.deadband_lock_ratio
        self.deadband_slow_ratio: float = tracking_config.deadband_slow_ratio
        self.deadband_medium_ratio: float = tracking_config.deadband_medium_ratio
        self.bbox_lock_enabled: bool = getattr(tracking_config, "bbox_lock_enabled", True)
        self.max_lost_frames: int = tracking_config.max_lost_frames
        self.aim_offset_x_px: float = tracking_config.aim_offset_x_px
        self.aim_offset_y_px: float = tracking_config.aim_offset_y_px
        self.invert_x: bool = tracking_config.invert_x
        self.invert_y: bool = tracking_config.invert_y
        self.lead_enabled: bool = tracking_config.lead_enabled
        self.lead_latency_multiplier: float = tracking_config.lead_latency_multiplier
        self.lead_max_horizon_ms: float = tracking_config.lead_max_horizon_ms
        self._config_revision = 0
        self._config_applied_at = time.time()

        # --- Internal state ---
        self._smooth_x: float = 0.0
        self._smooth_y: float = 0.0
        self._prev_norm_error_x: float = 0.0
        self._prev_norm_error_y: float = 0.0
        self._derivative_x: float = 0.0
        self._derivative_y: float = 0.0
        self._integral_x: float = 0.0
        self._integral_y: float = 0.0
        self._last_output_x: float = 0.0
        self._last_output_y: float = 0.0
        self._lock_hysteresis_active: bool = False
        self._target_lost_frames: int = 0
        self._total_frames: int = 0
        self._target_count: int = 0
        self._lost_count: int = 0
        self._last_time: float = time.time()
        # TrackingLoop ticks faster than the detector.  Keep derivative state
        # tied to detector frame ids/timestamps instead of differentiating the
        # same measurement 4-7 times while waiting for the next frame.
        self._last_measurement_frame_id: int | None = None
        self._last_measurement_timestamp_s: float | None = None
        # Input ordering is a stream-level concern and deliberately survives a
        # MANUAL/AUTO handoff.  PID history is reset at the handoff, but an old
        # detector result must not become "new" merely because AUTO restarted.
        self._last_accepted_frame_id: int | None = None
        self._last_accepted_timestamp_ms: int | None = None

        # --- FOV Engagement Gate (Eliminates Adjacent Corridor Crosstalk) ---
        self.patrol_gate_active: bool = False
        self.engagement_gate_half_px: float = 240.0   # [400, 880] in 1280x720 (+-8.45 deg, neighbor barrier 5.85 deg)
        self.retention_gate_half_px: float = 260.0    # [380, 900] in 1280x720 (+-9.15 deg hysteresis)

        # --- Tracking lifecycle ---
        self.tracking_active: bool = False
        self._state: TrackingState = TrackingState.IDLE
        self._last_update: TrackingUpdate | None = None
        self._last_fire_result: TrackingFireResult | None = None
        self.preferred_target_x: float | None = None
        self.preferred_target_y: float | None = None
        self.preferred_target_detection_id: int | None = None
        self.preferred_target_kind: str | None = None
        # A detector may miss the actively followed object for one or more
        # frames. Keep its stable identity beyond the one-second association
        # gate so a neighbouring target cannot steal control at the boundary.
        self.target_switch_grace_s: float = 1.0
        self.target_switch_debounce_frames: int = 3
        self._pending_switch_candidate: Any | None = None
        self._pending_switch_frames: int = 0
        self._locked_balloon_track_id: int | None = None
        self._locked_balloon_bbox_w: float | None = None
        self._locked_balloon_bbox_h: float | None = None
        self._locked_body_track_id: int | None = None
        self._locked_body_class_name: str | None = None
        self._locked_body_team: str | None = None
        self._locked_body_bbox_w: float | None = None
        self._locked_body_bbox_h: float | None = None
        self._locked_target_last_seen_at: float | None = None
        self._last_fire_at: float | None = None
        self._last_fire_target_id: int | None = None
        self.target_policy: str = "BALLOON"
        # SORUN #3 MIMARI KARAR: Asama 2 hedefleri (README: "hareketli,
        # ray uzerinde zigzag yapan veya salinim yapan balonlar") icin
        # OPT_D_FILTER yerine OPT_SINE_TRACK varsayilan yapildi. OPT_D_FILTER
        # ile ayni Kd/turev-filtre/feedforward retune'unu tasir (bkz.
        # LightControllerParams.for_mode SORUN #3 yorumu) AMA ayrica tam da
        # bu senaryo icin yazilmis zaten var olan sinusoidal apex-anticipation
        # / donus sonumleme mantigini da devreye sokar. OPT_D_FILTER, bu
        # sonumleme olmadan feedforward+yuksek Kd birlesimini kullandigi icin
        # zigzag donuslerinde titreme/kacirma sikligini artiriyordu.
        # Stage 3 switches to A3; legacy remains available for aircraft.
        self.controller_mode: str = "OPT_SINE_TRACK"
        self._general_pid_path = project_root() / "config" / "runtime" / "light_general_pid.json"
        self._asama2_pid_path = project_root() / "config" / "runtime" / "asama2_pid_ayarlar.json"
        self._light_controller = yb.kontrolcu_olustur(
            self.controller_mode,
            persisted_path=self._asama2_pid_path if self._asama2_pid_path.exists() else None,
        )
        self.command_rate_hz = 1000.0 / 15.0
        self.multi_target_tracker = MultiTargetTrackerService()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start_tracking(self) -> TrackingStatus:
        """Takibi başlat."""
        if self.tracking_active:
            # Operation state and the legacy tracking endpoint may both see the
            # same UI intent.  Treat the second call as an idempotent read so it
            # cannot erase stable track identities or PID initialization.
            return self.status()
        self.tracking_active = True
        self._state = TrackingState.SEARCHING
        self._reset_internals()
        self.logger.emit(LogLevel.INFO, "TRACKING", "Tracking started")
        return self.status()

    def stop_tracking(self) -> TrackingStatus:
        """Takibi durdur."""
        self.tracking_active = False
        self._state = TrackingState.STOPPED
        self._reset_control_state()
        self.logger.emit(LogLevel.INFO, "TRACKING", "Tracking stopped")
        return self.status()

    def status(self) -> TrackingStatus:
        """Mevcut takip durumunu döner."""
        pid = self._public_pid_values()
        speeds = self._public_speed_values()
        light_mode = self.controller_mode in LIGHT_CONTROLLER_MODES
        light_params = self._light_controller.params
        return TrackingStatus(
            active=self.tracking_active,
            state=self._state,
            target_count=self._target_count,
            lost_count=self._lost_count,
            total_frames=self._total_frames,
            pid_kp_x=pid["pid_kp_x"],
            pid_ki_x=pid["pid_ki_x"],
            pid_kd_x=pid["pid_kd_x"],
            pid_kp_y=pid["pid_kp_y"],
            pid_ki_y=pid["pid_ki_y"],
            pid_kd_y=pid["pid_kd_y"],
            config_revision=self._config_revision,
            config_applied_at=self._config_applied_at,
            smoothing_alpha=light_params.center_filter if light_mode else self.smoothing_alpha,
            command_rate_hz=self.command_rate_hz,
            max_speed=speeds["max_speed"],
            max_speed_x=speeds["max_speed_x"],
            max_speed_y=speeds["max_speed_y"],
            aim_offset_x_px=self.aim_offset_x_px,
            aim_offset_y_px=self.aim_offset_y_px,
            invert_x=light_params.invert_x if light_mode else self.invert_x,
            invert_y=light_params.invert_y if light_mode else self.invert_y,
            lead_enabled=self.controller_mode in A2_FAMILY_MODES if light_mode else self.lead_enabled,
            lead_latency_multiplier=0.0 if light_mode else self.lead_latency_multiplier,
            lead_max_horizon_ms=0.0 if light_mode else self.lead_max_horizon_ms,
            bbox_lock_enabled=False if light_mode else getattr(self.config.tracking, "bbox_lock_enabled", False),
            momentum_lock_enabled=False if light_mode else getattr(self.config.tracking, "momentum_lock_enabled", False),
            preferred_target_x=self.preferred_target_x,
            preferred_target_y=self.preferred_target_y,
            preferred_target_detection_id=self.preferred_target_detection_id,
            preferred_target_kind=self.preferred_target_kind,
            target_policy=self.target_policy,
            controller_mode=self.controller_mode,
            last_update=self._last_update,
            last_fire_result=self._last_fire_result,
            multi_target_tracker=self.multi_target_tracker.status(),
        )

    def set_controller_mode(self, mode: str) -> TrackingStatus:
        """Select General, Optimization modes, or an explicit Light A2/A3 profile."""
        normalized = str(mode or "legacy").upper()
        normalized = normalized if normalized in LIGHT_CONTROLLER_MODES else "legacy"
        if normalized == self.controller_mode:
            return self.status()
        self.controller_mode = normalized
        if normalized in A2_FAMILY_MODES:
            # A stage change starts a fresh Light profile, so values from a
            # previous stage cannot silently carry across the handoff.
            self._light_controller = yb.kontrolcu_olustur(
                normalized,
                persisted_path=self._asama2_pid_path if self._asama2_pid_path.exists() else None,
            )
            self._sync_light_pid_fields()
            # Light's standalone worker sleeps for 15 ms between control
            # steps. TrackingLoop may poll faster, but must not reintroduce
            # the legacy 30 Hz ceiling for this controller.
            self.command_rate_hz = 1000.0 / 15.0
        elif normalized in GENERAL_FAMILY_MODES:
            self._light_controller = LightTrackingController(
                normalized,
                persisted_path=self._general_pid_path,
            )
            self._sync_light_pid_fields()
            self.command_rate_hz = 1000.0 / 20.0
        else:
            self._restore_legacy_pid_fields()
            self._restore_legacy_runtime_fields()
        self._reset_control_state()
        return self.status()

    def _public_pid_values(self) -> dict[str, float]:
        if self.controller_mode in LIGHT_CONTROLLER_MODES:
            params = self._light_controller.params
            return {
                "pid_kp_x": params.kp_x,
                "pid_ki_x": params.ki_x,
                "pid_kd_x": params.kd_x,
                "pid_kp_y": params.kp_y,
                "pid_ki_y": params.ki_y,
                "pid_kd_y": params.kd_y,
            }
        return {
            "pid_kp_x": self.pid_kp_x,
            "pid_ki_x": self.pid_ki_x,
            "pid_kd_x": self.pid_kd_x,
            "pid_kp_y": self.pid_kp_y,
            "pid_ki_y": self.pid_ki_y,
            "pid_kd_y": self.pid_kd_y,
        }

    def _public_speed_values(self) -> dict[str, int]:
        if self.controller_mode in LIGHT_CONTROLLER_MODES:
            maximum = int(round(self._light_controller.params.max_speed))
            return {"max_speed": maximum, "max_speed_x": maximum, "max_speed_y": maximum}
        return {
            "max_speed": self.max_speed,
            "max_speed_x": self.max_speed_x,
            "max_speed_y": self.max_speed_y,
        }

    def _sync_light_pid_fields(self) -> None:
        params = self._light_controller.params
        self.pid_kp_x = params.kp_x
        self.pid_ki_x = params.ki_x
        self.pid_kd_x = params.kd_x
        self.pid_kp_y = params.kp_y
        self.pid_ki_y = params.ki_y
        self.pid_kd_y = params.kd_y

    def _restore_legacy_pid_fields(self) -> None:
        for name, value in self._legacy_pid_values.items():
            setattr(self, name, value)

    def _restore_legacy_runtime_fields(self) -> None:
        tracking = self.config.tracking
        for name in (
            "smoothing_alpha", "command_rate_hz", "max_speed", "min_move_speed",
            "deadband_lock_ratio", "deadband_slow_ratio", "deadband_medium_ratio",
            "max_lost_frames", "aim_offset_x_px", "aim_offset_y_px", "invert_x",
            "invert_y", "lead_enabled", "lead_latency_multiplier", "lead_max_horizon_ms",
        ):
            if hasattr(tracking, name):
                setattr(self, name, getattr(tracking, name))
        self.max_speed_x = getattr(tracking, "max_speed_x", None) or self.max_speed
        self.max_speed_y = getattr(tracking, "max_speed_y", None) or self.max_speed

    def update_config(self, update: TrackingConfigUpdate) -> TrackingStatus:
        """PID ve tracking parametrelerini tek kontrol-frame sınırında uygula."""
        changed = update.model_dump(mode="python", exclude_none=True)
        if not changed:
            return self.status()
        if update.controller_mode is not None:
            self.set_controller_mode(update.controller_mode)
        reset_integral = any(key in changed for key in ("pid_ki_x", "pid_ki_y"))
        reset_derivative = any(key in changed for key in ("pid_kd_x", "pid_kd_y"))
        for field in ("pid_kp_x", "pid_ki_x", "pid_kd_x", "pid_kp_y", "pid_ki_y", "pid_kd_y"):
            value = getattr(update, field)
            if value is not None:
                setattr(self, field, value)
                if self.controller_mode == "legacy":
                    self._legacy_pid_values[field] = value
        if update.smoothing_alpha is not None:
            self.smoothing_alpha = update.smoothing_alpha
        if update.command_rate_hz is not None and self.controller_mode == "legacy":
            self.command_rate_hz = max(1.0, min(60.0, update.command_rate_hz))
        if update.max_speed is not None:
            self.max_speed = update.max_speed
            # Legacy clients still send one common speed. It remains a full
            # two-axis live update unless an axis override is in the packet.
            if update.max_speed_x is None:
                self.max_speed_x = update.max_speed
            if update.max_speed_y is None:
                self.max_speed_y = update.max_speed
        if update.max_speed_x is not None:
            self.max_speed_x = update.max_speed_x
        if update.max_speed_y is not None:
            self.max_speed_y = update.max_speed_y
        if update.max_speed_x is not None or update.max_speed_y is not None:
            self.max_speed = max(self.max_speed_x, self.max_speed_y)
        if update.min_move_speed is not None:
            self.min_move_speed = max(0.0, update.min_move_speed)
        if update.deadband_lock_ratio is not None:
            self.deadband_lock_ratio = max(0.0, update.deadband_lock_ratio)
        if update.deadband_slow_ratio is not None:
            self.deadband_slow_ratio = max(0.0, update.deadband_slow_ratio)
        if update.deadband_medium_ratio is not None:
            self.deadband_medium_ratio = max(0.0, update.deadband_medium_ratio)
        if getattr(update, "bbox_lock_enabled", None) is not None:
            self.bbox_lock_enabled = bool(update.bbox_lock_enabled)
        if update.aim_offset_x_px is not None:
            self.aim_offset_x_px = update.aim_offset_x_px
        if update.aim_offset_y_px is not None:
            self.aim_offset_y_px = update.aim_offset_y_px
        if update.invert_x is not None:
            self.invert_x = update.invert_x
        if update.invert_y is not None:
            self.invert_y = update.invert_y
        if update.lead_enabled is not None:
            self.lead_enabled = update.lead_enabled
        if update.lead_latency_multiplier is not None:
            self.lead_latency_multiplier = max(0.0, min(3.0, update.lead_latency_multiplier))
        if update.lead_max_horizon_ms is not None:
            self.lead_max_horizon_ms = max(0.0, min(300.0, update.lead_max_horizon_ms))
        if update.max_lost_frames is not None:
            self.max_lost_frames = update.max_lost_frames
        if self.controller_mode in LIGHT_CONTROLLER_MODES:
            # The public names remain compatible with the cockpit API. Only
            # fields explicitly sent by the operator alter the Light profile;
            # omitted values keep the checked-in A2/A3 JSON baseline.
            light_values: dict[str, float | bool | None] = {
                "kp_x": update.pid_kp_x,
                "ki_x": update.pid_ki_x,
                "kd_x": update.pid_kd_x,
                "kp_y": update.pid_kp_y,
                "ki_y": update.pid_ki_y,
                "kd_y": update.pid_kd_y,
                "max_speed": update.max_speed,
                "invert_x": update.invert_x,
                "invert_y": update.invert_y,
            }
            if self.controller_mode in {"A2", "A3"}:
                light_values.update(
                    center_filter=update.smoothing_alpha,
                    min_speed=update.min_move_speed,
                )
            self._light_controller.configure(**light_values)
            if update.max_speed_x is not None or update.max_speed_y is not None:
                self._light_controller.configure(
                    max_speed=min(
                        value for value in (update.max_speed_x, update.max_speed_y)
                        if value is not None
                    )
                )
            self._sync_light_pid_fields()
        # Keep the legacy runtime profile independent from temporary Light
        # tuning. Light values live in the controller until a dedicated Light
        # profile is added to AppConfig.
        light_fields = {
            "pid_kp_x", "pid_ki_x", "pid_kd_x", "pid_kp_y", "pid_ki_y", "pid_kd_y",
            "smoothing_alpha", "min_move_speed", "max_speed", "max_speed_x", "max_speed_y",
            "invert_x", "invert_y",
        }
        for key, value in changed.items():
            if self.controller_mode in LIGHT_CONTROLLER_MODES and key in light_fields:
                continue
            if hasattr(self.config.tracking, key):
                setattr(self.config.tracking, key, value)
        if self.controller_mode == "legacy":
            self.config.tracking.max_speed = self.max_speed
            self.config.tracking.max_speed_x = self.max_speed_x
            self.config.tracking.max_speed_y = self.max_speed_y
        if reset_integral:
            self._integral_x = 0.0
            self._integral_y = 0.0
        if reset_derivative:
            self._derivative_x = 0.0
            self._derivative_y = 0.0
            self._last_measurement_frame_id = None
            self._last_measurement_timestamp_s = None
        self._config_revision += 1
        self._config_applied_at = time.time()
        self.logger.emit(
            LogLevel.INFO,
            "TRACKING",
            "Tracking config updated live",
            {**update.model_dump(mode="json", exclude_none=True), "config_revision": self._config_revision, "tracking_active": self.tracking_active},
        )
        return self.status()

    def set_target_policy(self, policy: str) -> TrackingStatus:
        normalized = str(policy or "BALLOON").upper()
        if normalized not in {"BALLOON", "AIRCRAFT", "BALLOON_AIRCRAFT"}:
            normalized = "BALLOON"
        if normalized != self.target_policy:
            self.target_policy = normalized
            # SORUN #3 MIMARI KARAR: Asama/politika gecisinde kontrolcu modu
            # ARTIK OTOMATIK olarak o asamanin sahada kanitlanmis profiline
            # alinir. Onceden bu baglanti yoktu: operator Asama 3'e (veya
            # Asama 2'ye) gecince manuel set_controller_mode cagirmayi
            # unutursa taret bir onceki asamanin PID/feedforward
            # profiliyle (or. Asama 3'te hala Asama 2'nin feedforward'lu
            # ayari) calismaya devam edebiliyordu. Operator yine de
            # set_controller_mode()'u bu cagridan SONRA elle cagirarak
            # asama icinde farkli bir profile (or. OPT_ZONE_GAIN) gecebilir;
            # burada garanti edilen sadece "asama degisince dogru aile"dir.
            if normalized == "AIRCRAFT":
                self.set_controller_mode("legacy")
            elif normalized == "BALLOON_AIRCRAFT":
                self.set_controller_mode("A3")
            elif normalized == "BALLOON":
                self.set_controller_mode("OPT_SINE_TRACK")
            self.clear_target()
        return self.status()

    def clear_target(self) -> TrackingStatus:
        """Atomically release the selected target and all PID carry-over."""
        self.preferred_target_x = None
        self.preferred_target_y = None
        self.preferred_target_detection_id = None
        self.preferred_target_kind = None
        self._pending_switch_candidate = None
        self._pending_switch_frames = 0
        self._clear_target_identity_lock()
        self._reset_control_state()
        return self.status()

    def select_target(
        self,
        x: float,
        y: float,
        detection_id: int | None = None,
        frame_id: int | None = None,
        kind: str | None = None,
    ) -> TrackingStatus:
        """Select a target without inheriting the previous PID/lead state."""
        previous = (self.preferred_target_detection_id, self.preferred_target_kind)
        self.preferred_target_x = x
        self.preferred_target_y = y
        self.preferred_target_detection_id = detection_id
        self.preferred_target_kind = kind if kind in {"body", "balloon"} else None
        self._pending_switch_candidate = None
        self._pending_switch_frames = 0
        if previous != (detection_id, self.preferred_target_kind):
            # Explicit operator clicks are immediate handoffs and therefore
            # intentionally replace the previous one-second reservation.
            self._clear_target_identity_lock()
            self._locked_target_last_seen_at = time.monotonic()
            self._reset_control_state()
        self.logger.emit(
            LogLevel.INFO,
            "TRACKING",
            "Tracking target selected",
            {"x": x, "y": y, "detection_id": detection_id, "frame_id": frame_id, "kind": self.preferred_target_kind, "target_policy": self.target_policy},
        )
        return self.status()

    def record_fire_result(self, result) -> TrackingStatus:
        self._last_fire_result = TrackingFireResult(
            accepted=bool(result.accepted),
            command=result.command,
            reason_codes=list(result.reason_codes),
            detail=result.detail,
            physical_command_generated=bool(result.physical_command_generated),
        )
        if bool(result.accepted) or bool(result.physical_command_generated):
            self._last_fire_at = time.monotonic()
            self._last_fire_target_id = self._locked_balloon_track_id or self.preferred_target_detection_id
        return self.status()

    # ------------------------------------------------------------------
    # Ana takip döngüsü (her frame'de çağrılır)
    # ------------------------------------------------------------------

    def update(
        self,
        vision_event: VisionEvent | None,
        frame_width: int,
        frame_height: int,
        *,
        selection_event: VisionEvent | None = None,
        update_multi_target: bool = True,
    ) -> TrackingUpdate:
        """
        Tek frame guncelleme.

        Parameters
        ----------
        vision_event : Son VisionEvent (None ise hedef yok)
        frame_width  : Kamera çözünürlük genişliği
        frame_height : Kamera çözünürlük yüksekliği

        Returns
        -------
        TrackingUpdate : Motor hız komutu + telemetri
        """
        now = time.time()
        dt = now - self._last_time
        if dt <= 0:
            dt = 0.033  # Fallback 30 FPS
        self._last_time = now
        self._total_frames += 1
        frame_id = vision_event.frame_id if vision_event else 0

        ballistic_offset_x = 0.0
        ballistic_offset_y = 0.0
        if self.ballistics is not None:
            try:
                prof = self.ballistics.get_profile()
                if prof.enabled:
                    balloon_w = None
                    if self.preferred_target_detection_id is not None and vision_event is not None:
                        b = next((item for item in vision_event.balloon_detections if item.id == self.preferred_target_detection_id), None)
                        if b is not None:
                            balloon_w = float(b.bbox.w)
                    target_vx = float(getattr(self._light_controller, "target_velocity_x", 0.0))
                    live_b = self.ballistics.get_live_state(
                        observed_balloon_w_px=balloon_w,
                        target_vx_px_s=target_vx,
                    )
                    ballistic_offset_x = float(live_b.offset_x_px if yb.Y9_BALISTIK_LEAD_YOK else live_b.total_offset_x_px)
                    ballistic_offset_y = float(live_b.total_offset_y_px)
            except Exception:
                pass

        offset_x = self.aim_offset_x_px + ballistic_offset_x
        offset_y = self.aim_offset_y_px + ballistic_offset_y

        frame_cx = frame_width / 2.0 + offset_x
        frame_cy = frame_height / 2.0 + offset_y

        if not self.tracking_active:
            self._last_update = TrackingUpdate(
                state=TrackingState.IDLE,
                frame_center_x=frame_cx, frame_center_y=frame_cy,
                aim_offset_x_px=offset_x,
                aim_offset_y_px=offset_y,
                controller_mode=self.controller_mode,
                frame_id=frame_id, dt=dt,
            )
            return self._last_update

        measurement_status = self._measurement_status(vision_event)
        if measurement_status == "NEW" and vision_event is not None:
            self._last_accepted_frame_id = int(vision_event.frame_id)
            self._last_accepted_timestamp_ms = int(vision_event.timestamp_ms)
        measurement_metadata = self._measurement_metadata(vision_event, measurement_status, now)

        if measurement_status == "REUSED" and self.controller_mode in LIGHT_CONTROLLER_MODES and vision_event is not None:
            # Light's worker advances the controller on every ~15 ms poll and
            # reuses the newest detection between model frames.  Keep the
            # freshness gate above, but preserve that control cadence here.
            event = selection_event if selection_event is not None else vision_event
            target = self._select_target(event, frame_cx, frame_cy)
            diagnostics = self._selection_diagnostics(event)
            if target is not None:
                self._target_lost_frames = 0
                self._state = TrackingState.TRACKING
                self._last_update = self._update_light_controller(
                    target=target,
                    frame_width=frame_width,
                    frame_height=frame_height,
                    dt=dt,
                    frame_id=int(vision_event.frame_id),
                    measurement_metadata=measurement_metadata,
                    selection_diagnostics=diagnostics,
                    is_fresh=False,
                    offset_x=offset_x,
                    offset_y=offset_y,
                )
                return self._last_update
            elif (
                self._target_lost_frames < 4
                and self.preferred_target_x is not None
                and self.preferred_target_y is not None
                and self._last_update is not None
            ):
                # Transient 1-3 frame dropout on REUSED ticks:
                # coast on last known target coordinates instead of instantly
                # hard-braking to speed=0 and oscillating.
                self._target_lost_frames += 1
                target = (float(self.preferred_target_x), float(self.preferred_target_y), 40.0, 40.0)
                self._state = TrackingState.TRACKING
                self._last_update = self._update_light_controller(
                    target=target,
                    frame_width=frame_width,
                    frame_height=frame_height,
                    dt=dt,
                    frame_id=int(vision_event.frame_id),
                    measurement_metadata=measurement_metadata,
                    selection_diagnostics=diagnostics,
                    is_fresh=False,
                    offset_x=offset_x,
                    offset_y=offset_y,
                )
                return self._last_update
            self._target_lost_frames += 1
            self._state = TrackingState.SEARCHING
            self._reset_control_state(preserve_general_speed=True)
            self._last_update = TrackingUpdate(
                state=self._state,
                frame_center_x=frame_width / 2.0,
                frame_center_y=frame_height / 2.0,
                frame_id=int(vision_event.frame_id),
                dt=dt,
                target_lost_frames=self._target_lost_frames,
                controller_mode=self.controller_mode,
                **measurement_metadata,
                **diagnostics,
            )
            return self._last_update

        if measurement_status == "REUSED":
            # TrackingLoop intentionally runs faster than YOLO. Seeing the
            # same event again is a control-clock hold, not another camera
            # measurement: do not re-run target selection, refresh identity
            # history, integrate PID state or advance smoothing a second time.
            # The last output is held unchanged until a genuinely new detector
            # result arrives. A MANUAL -> AUTO handoff has reset measurement
            # history, so it stays at zero rather than reviving an old command.
            if self._last_update is not None:
                first_measurement_pending = self._last_measurement_frame_id is None
                updates: dict[str, object] = {
                    "dt": dt,
                    "updated_at": now,
                    "controller_mode": self.controller_mode,
                    **measurement_metadata,
                }
                if first_measurement_pending:
                    self._state = TrackingState.SEARCHING
                    updates.update(
                        {
                            "state": TrackingState.SEARCHING,
                            "speed_x": 0,
                            "speed_y": 0,
                            "raw_pid_x": 0.0,
                            "raw_pid_y": 0.0,
                        }
                    )
                self._last_update = self._last_update.model_copy(update=updates)
            else:
                self._state = TrackingState.SEARCHING
                self._last_update = TrackingUpdate(
                    state=TrackingState.SEARCHING,
                    frame_center_x=frame_cx,
                    frame_center_y=frame_cy,
                    aim_offset_x_px=offset_x,
                    aim_offset_y_px=offset_y,
                    controller_mode=self.controller_mode,
                    frame_id=self._last_accepted_frame_id or 0,
                    dt=dt,
                    **measurement_metadata,
                )
            return self._last_update

        if measurement_status == "STALE_REJECTED":
            # Keep the last accepted image-space truth for diagnostics only.
            # Zero outputs guarantee that an out-of-order result cannot become
            # a control setpoint or a digital-twin jump.
            diagnostics = self._selection_diagnostics(None)
            if self._last_update is not None:
                self._last_update = self._last_update.model_copy(
                    update={
                        "state": TrackingState.TARGET_LOST,
                        "speed_x": 0,
                        "speed_y": 0,
                        "raw_pid_x": 0.0,
                        "raw_pid_y": 0.0,
                        "dt": dt,
                        "updated_at": now,
                        "controller_mode": self.controller_mode,
                        **measurement_metadata,
                        **diagnostics,
                    }
                )
            else:
                self._last_update = TrackingUpdate(
                    state=TrackingState.TARGET_LOST,
                    frame_center_x=frame_cx,
                    frame_center_y=frame_cy,
                    aim_offset_x_px=self.aim_offset_x_px,
                    aim_offset_y_px=self.aim_offset_y_px,
                    controller_mode=self.controller_mode,
                    frame_id=self._last_accepted_frame_id or 0,
                    dt=dt,
                    **measurement_metadata,
                    **diagnostics,
                )
            return self._last_update

        # Aşama 2 için kalıcı çoklu-track telemetrisi. Bu katman tek başına
        # motor/ateş seçimi yapmaz; fiziksel komut yine Gateway'den geçer.
        # TrackingLoop may pre-update the multi-target tracker so the logical
        # target registry can filter a control event before PID selection.  The
        # default remains the original single-call behaviour for API/tests.
        if update_multi_target:
            self.multi_target_tracker.update(vision_event)

        # ---- 1. Hedef sec ----
        target = self._select_target(selection_event if selection_event is not None else vision_event, frame_cx, frame_cy)
        selection_diagnostics = self._selection_diagnostics(
            selection_event if selection_event is not None else vision_event
        )

        target_x: float
        target_y: float
        target_w: float = 0.0
        target_h: float = 0.0
        using_kalman = False  # legacy telemetry field; prediction is disabled
        lead_horizon_ms = 0.0
        predicted_target_x: float | None = None
        predicted_target_y: float | None = None

        if target is not None:
            target_x, target_y = target[0], target[1]
            target_w, target_h = target[2], target[3]
            self._target_lost_frames = 0
            self._target_count += 1
            self._state = TrackingState.TRACKING
        elif (
            self.controller_mode in LIGHT_CONTROLLER_MODES
            and self._target_lost_frames < 4
            and self.preferred_target_x is not None
            and self.preferred_target_y is not None
            and self._last_update is not None
        ):
            # Transient 1-3 frame dropout: coast on last known target coordinates
            # instead of instantly hard-braking to speed=0 and oscillating.
            self._target_lost_frames += 1
            target = (float(self.preferred_target_x), float(self.preferred_target_y), 40.0, 40.0)
            target_x, target_y, target_w, target_h = target
            self._state = TrackingState.TRACKING
        else:
            self._target_lost_frames += 1
            self._lost_count += 1
            self._state = TrackingState.SEARCHING
            self._reset_control_state(preserve_general_speed=True)
            self._last_update = TrackingUpdate(
                state=TrackingState.SEARCHING,
                frame_center_x=frame_cx, frame_center_y=frame_cy,
                aim_offset_x_px=offset_x,
                aim_offset_y_px=offset_y,
                controller_mode=self.controller_mode,
                frame_id=frame_id, dt=dt,
                target_lost_frames=self._target_lost_frames,
                **measurement_metadata,
                **selection_diagnostics,
            )
            return self._last_update

        if self.controller_mode in LIGHT_CONTROLLER_MODES:
            self._last_update = self._update_light_controller(
                target=target,
                frame_width=frame_width,
                frame_height=frame_height,
                dt=dt,
                frame_id=frame_id,
                measurement_metadata=measurement_metadata,
                selection_diagnostics=selection_diagnostics,
                is_fresh=True,
                offset_x=offset_x,
                offset_y=offset_y,
            )
            return self._last_update

        # ---- 2. Hata hesabi ----
        error_x = target_x - frame_cx
        error_y = target_y - frame_cy

        norm_x = error_x / max(frame_cx, 1.0)
        norm_y = error_y / max(frame_cy, 1.0)
        norm_x = max(-1.0, min(1.0, norm_x))
        norm_y = max(-1.0, min(1.0, norm_y))

        frame_is_new = vision_event is not None and frame_id > 0 and measurement_status == "NEW"
        if frame_is_new:
            measurement_timestamp_s = float(vision_event.timestamp_ms) / 1000.0
            if self._last_measurement_timestamp_s is None:
                # Bumpless MANUAL -> AUTO transfer: the first current
                # measurement establishes the error baseline; it is not a
                # derivative impulse from an imaginary zero-error frame.
                derivative_x = 0.0
                derivative_y = 0.0
            elif measurement_timestamp_s > self._last_measurement_timestamp_s:
                # Detector timestamps are wall-clock values. Clamp the lower
                # bound so a burst of equal/near-equal timestamps cannot
                # create an artificial derivative spike; cap the upper bound
                # so a short inference pause does not erase damping.
                measurement_dt = min(
                    0.25,
                    max(0.01, measurement_timestamp_s - self._last_measurement_timestamp_s),
                )
                derivative_x = (norm_x - self._prev_norm_error_x) / measurement_dt
                derivative_y = (norm_y - self._prev_norm_error_y) / measurement_dt
                derivative_x = max(-MAX_NORMALIZED_ERROR_RATE, min(MAX_NORMALIZED_ERROR_RATE, derivative_x))
                derivative_y = max(-MAX_NORMALIZED_ERROR_RATE, min(MAX_NORMALIZED_ERROR_RATE, derivative_y))
            else:
                derivative_x = 0.0
                derivative_y = 0.0
            self._prev_norm_error_x = norm_x
            self._prev_norm_error_y = norm_y
            self._last_measurement_frame_id = frame_id
            self._last_measurement_timestamp_s = measurement_timestamp_s
        else:
            # No new detector measurement: keep the proportional command but
            # let the filtered derivative decay rather than differentiating a
            # stale frame at the faster control-loop frequency.
            derivative_x = 0.0
            derivative_y = 0.0

        # A detector measurement is already the result of a model pass; a
        # second derivative filter is intentional here. Lower alpha keeps
        # bbox quantisation from becoming a motor direction reversal.
        derivative_alpha = 0.20
        self._derivative_x = derivative_alpha * derivative_x + (1 - derivative_alpha) * self._derivative_x
        self._derivative_y = derivative_alpha * derivative_y + (1 - derivative_alpha) * self._derivative_y

        # ---- 3. Integral + anti-windup ----
        integral_dt = min(0.25, max(0.0, dt))
        if self.pid_ki_x > 0:
            integral_limit_x = max(0.01, self.max_speed_x / self.pid_ki_x)
            self._integral_x = max(-integral_limit_x, min(integral_limit_x, self._integral_x + norm_x * integral_dt))
        else:
            self._integral_x = 0.0
        if self.pid_ki_y > 0:
            integral_limit_y = max(0.01, self.max_speed_y / self.pid_ki_y)
            self._integral_y = max(-integral_limit_y, min(integral_limit_y, self._integral_y + norm_y * integral_dt))
        else:
            self._integral_y = 0.0

        raw_x = self.pid_kp_x * norm_x + self.pid_ki_x * self._integral_x + self.pid_kd_x * self._derivative_x
        raw_y = self.pid_kp_y * norm_y + self.pid_ki_y * self._integral_y + self.pid_kd_y * self._derivative_y

        # ---- 4. Exponential smoothing ----
        alpha = max(0.0, min(1.0, self.smoothing_alpha))
        self._smooth_x = alpha * raw_x + (1 - alpha) * self._smooth_x
        self._smooth_y = alpha * raw_y + (1 - alpha) * self._smooth_y

        speed_x = self._smooth_x
        speed_y = self._smooth_y

        # ---- 5. Bbox olcekli kilit bolgesi ----
        distance_to_center = math.sqrt(error_x ** 2 + error_y ** 2)
        deadband_zone = "full"

        deadband_enabled = (
            self.deadband_lock_ratio > 0
            or self.deadband_slow_ratio > 0
            or self.deadband_medium_ratio > 0
        )

        if target_w > 0 and target_h > 0:
            inner_half_width = max(4.0, target_w * TRACKING_INNER_BBOX_RATIO / 2.0)
            inner_half_height = max(4.0, target_h * TRACKING_INNER_BBOX_RATIO / 2.0)
            inside_inner_lock = abs(error_x) <= inner_half_width and abs(error_y) <= inner_half_height

            if inside_inner_lock and self.bbox_lock_enabled:
                deadband_zone = "locked"
                if not (self.controller_mode in LIGHT_CONTROLLER_MODES or getattr(self, "light_mode", False)):
                    speed_x = 0
                    speed_y = 0
                    self._smooth_x = 0.0
                    self._smooth_y = 0.0
                    self._integral_x = 0.0
                    self._integral_y = 0.0
                self._state = TrackingState.LOCKED
                self._lock_hysteresis_active = True
            elif not self.bbox_lock_enabled:
                deadband_zone = "bbox_lock_off"
                self._lock_hysteresis_active = False
            else:
                # The operator requested immediate reacquisition when the aim
                # leaves the 30% inner rectangle; no extra exit hysteresis is
                # applied to this boundary.
                self._lock_hysteresis_active = False

        if deadband_zone != "locked" and deadband_enabled and target_w > 0 and target_h > 0:
            target_radius = min(target_w, target_h) / 2.0
            lock_threshold = max(8.0, target_radius * TRACKING_INNER_BBOX_RATIO)
            slow_threshold = max(lock_threshold + 1.0, target_radius * self.deadband_slow_ratio)
            medium_threshold = max(slow_threshold + 1.0, target_radius * self.deadband_medium_ratio)
            if distance_to_center < slow_threshold:
                deadband_zone = "slow"
                speed_x *= 0.35
                speed_y *= 0.35
            elif distance_to_center < medium_threshold:
                deadband_zone = "medium"
                speed_x *= 0.70
                speed_y *= 0.70

        # ---- 6. Yon inversiyon ----
        if self.invert_x:
            speed_x *= -1
        if self.invert_y:
            speed_y *= -1

        # ---- 7. Minimum hareket telafisi ----
        min_speed = max(0.0, self.min_move_speed)
        if deadband_zone not in ("locked", "slow"):
            if 0 < abs(speed_x) < min_speed:
                speed_x = math.copysign(min_speed, speed_x)
            if 0 < abs(speed_y) < min_speed:
                speed_y = math.copysign(min_speed, speed_y)
        elif deadband_zone == "slow":
            if abs(error_x) < 5 or abs(speed_x) < min_speed * 0.35:
                speed_x = 0
            if abs(error_y) < 5 or abs(speed_y) < min_speed * 0.35:
                speed_y = 0

        # ---- 8. Clamp ----
        speed_x = max(-self.max_speed_x, min(self.max_speed_x, int(speed_x)))
        speed_y = max(-self.max_speed_y, min(self.max_speed_y, int(speed_y)))

        # The rollback actuator already owns the only speed shaping layer.
        # A positive value keeps the old experimental host slew limiter
        # available for a later, explicitly measured A/B profile.
        if COMMAND_SLEW_RATE_UNITS_S > 0:
            max_step_x = max(1.0, COMMAND_SLEW_RATE_UNITS_S * min(0.25, max(dt, 0.001)))
            max_step_y = max_step_x
            speed_x = int(round(max(self._last_output_x - max_step_x, min(self._last_output_x + max_step_x, speed_x))))
            speed_y = int(round(max(self._last_output_y - max_step_y, min(self._last_output_y + max_step_y, speed_y))))
        self._last_output_x = float(speed_x)
        self._last_output_y = float(speed_y)

        result = TrackingUpdate(
            state=self._state,
            speed_x=speed_x,
            speed_y=speed_y,
            error_x_px=error_x,
            error_y_px=error_y,
            raw_pid_x=raw_x,
            raw_pid_y=raw_y,
            target_center_x=target_x,
            target_center_y=target_y,
            frame_center_x=frame_cx,
            frame_center_y=frame_cy,
            aim_offset_x_px=round(offset_x, 2),
            aim_offset_y_px=round(offset_y, 2),
            target_lost_frames=self._target_lost_frames,
            distance_to_center=distance_to_center,
            deadband_zone=deadband_zone,
            bbox_lock_enabled=self.bbox_lock_enabled,
            lock_zone_center_x=predicted_target_x,
            lock_zone_center_y=predicted_target_y,
            lock_zone_width=max(1.0, target_w * 0.25),
            lock_zone_height=max(1.0, target_h * 0.25),
            using_kalman_prediction=using_kalman,
            lead_horizon_ms=round(lead_horizon_ms, 3),
            predicted_target_center_x=predicted_target_x,
            predicted_target_center_y=predicted_target_y,
            **measurement_metadata,
            **selection_diagnostics,
            frame_id=frame_id,
            dt=dt,
        )
        self._last_update = result
        return result

    def _update_light_controller(
        self,
        *,
        target: tuple[float, float, float, float],
        frame_width: int,
        frame_height: int,
        dt: float,
        frame_id: int,
        measurement_metadata: dict[str, object],
        selection_diagnostics: dict[str, object],
        is_fresh: bool = True,
        offset_x: float = 0.0,
        offset_y: float = 0.0,
    ) -> TrackingUpdate:
        target_x, target_y, target_w, target_h = target
        if target_w <= 2 or target_h <= 2:
            self._target_lost_frames += 1
            self._state = TrackingState.SEARCHING
            self._reset_control_state(preserve_general_speed=True)
            self._last_update = TrackingUpdate(
                state=self._state,
                frame_center_x=frame_width / 2.0 + offset_x,
                frame_center_y=frame_height / 2.0 + offset_y,
                aim_offset_x_px=round(offset_x, 2),
                aim_offset_y_px=round(offset_y, 2),
                frame_id=frame_id,
                dt=dt,
                target_lost_frames=self._target_lost_frames,
                controller_mode=self.controller_mode,
                **measurement_metadata,
                **selection_diagnostics,
            )
            return self._last_update

        effective_target_x = target_x - offset_x
        effective_target_y = target_y - offset_y

        control = self._light_controller.update(
            target_x=effective_target_x,
            target_y=effective_target_y,
            frame_width=frame_width,
            frame_height=frame_height,
            dt=dt,
            bbox_width=target_w,
            bbox_height=target_h,
            is_fresh=is_fresh,
        )
        if yb.Y15_KAYIP_FREN:
            _y15_c = bool(getattr(self, "_y15_coast", False))
            if _y15_c:
                try:
                    import dataclasses as _y15_dc
                    if not getattr(self, "_y15_coast_onceki", False):
                        self._light_controller.reset()
                    _y15_px = float(getattr(self._last_update, "speed_x", 0) or 0)
                    _y15_py = float(getattr(self._last_update, "speed_y", 0) or 0)
                    control = _y15_dc.replace(control, speed_x=int(_y15_px * 0.6), speed_y=int(_y15_py * 0.6), locked=False)
                except Exception:
                    pass
            self._y15_coast_onceki = _y15_c
        self._state = TrackingState.LOCKED if control.locked else TrackingState.TRACKING
        inner_w = max(1.0, target_w * 0.35)
        inner_h = max(1.0, target_h * 0.35)
        self._last_update = TrackingUpdate(
            state=self._state,
            speed_x=control.speed_x,
            speed_y=control.speed_y,
            error_x_px=control.error_x,
            error_y_px=control.error_y,
            raw_pid_x=control.raw_pid_x,
            raw_pid_y=control.raw_pid_y,
            target_center_x=target_x,
            target_center_y=target_y,
            frame_center_x=frame_width / 2.0 + offset_x,
            frame_center_y=frame_height / 2.0 + offset_y,
            aim_offset_x_px=round(offset_x + control.dynamic_offset_x, 2),
            aim_offset_y_px=round(offset_y + control.dynamic_offset_y, 2),
            target_lost_frames=self._target_lost_frames,
            distance_to_center=math.hypot(control.error_x, control.error_y),
            deadband_zone=control.deadband_zone,
            using_kalman_prediction=control.using_kalman,
            predicted_target_center_x=control.aim_x + offset_x,
            predicted_target_center_y=control.aim_y + offset_y,
            target_velocity_x_px_s=round(control.target_velocity_x, 2),
            target_velocity_y_px_s=round(control.target_velocity_y, 2),
            momentum_lock_active=bool(abs(control.dynamic_offset_x) > 0.5 or abs(control.target_velocity_x) > self._light_controller.params.lead_threshold),
            momentum_shift_x_px=round(control.dynamic_offset_x, 2),
            momentum_shift_y_px=round(control.dynamic_offset_y, 2),
            lock_zone_center_x=control.aim_x + offset_x,
            lock_zone_center_y=control.aim_y + offset_y,
            lock_zone_width=inner_w,
            lock_zone_height=inner_h,
            controller_mode=self.controller_mode,
            bbox_lock_enabled=True,
            **measurement_metadata,
            **selection_diagnostics,
            frame_id=frame_id,
            dt=dt,
        )
        return self._last_update

    # ------------------------------------------------------------------
    # Hedef seçimi (eski select_target())
    # ------------------------------------------------------------------

    def inside_engagement_gate(self, target_cx: float, center_x: float, is_already_locked: bool = False) -> bool:
        """Filter peripheral targets during autonomous corridor radar search to eliminate crosstalk."""
        if not getattr(self, "patrol_gate_active", False):
            return True
        half = self.retention_gate_half_px if is_already_locked else self.engagement_gate_half_px
        return abs(float(target_cx) - float(center_x)) <= half

    def _select_target(
        self,
        event: VisionEvent | None,
        center_x: float,
        center_y: float,
    ) -> tuple[float, float, float, float] | None:
        """
        En yakın balon bbox merkezini seç.

        Eski sistemdeki ``get_closest_target()`` fonksiyonunun karşılığı.
        Balon yoksa body detection kullanılır.

        Returns
        -------
        (center_x, center_y, width, height) veya None
        """
        self._y15_coast = False
        if event is None:
            return None

        preferred_x = self.preferred_target_x if self.preferred_target_x is not None else center_x
        preferred_y = self.preferred_target_y if self.preferred_target_y is not None else center_y

        selected_kind = self.preferred_target_kind
        if self.target_policy == "AIRCRAFT":
            selected_kind = "body"
        elif self.target_policy in {"BALLOON", "BALLOON_AIRCRAFT", "STAGE1_INDEPENDENT"}:
            selected_kind = "balloon"

        if selected_kind == "body":
            bodies = list(event.body_detections)
            # Friendly bodies are observation-only when explicitly clicked; autonomous scan never targets them
            if self._locked_body_track_id is None and self.preferred_target_detection_id is None:
                bodies = [b for b in bodies if str(getattr(b, "target_team", "")).lower() not in {"friend", "dost"}]
            if self._locked_body_track_id is not None:
                selected = next((item for item in bodies if item.track_id == self._locked_body_track_id), None)
                if selected is not None:
                    self._remember_body(selected)
                    return (
                        selected.bbox.x + selected.bbox.w / 2,
                        selected.bbox.y + selected.bbox.h / 2,
                        selected.bbox.w,
                        selected.bbox.h,
                    )
                # Body trackers may allocate a new id after a short detector
                # miss.  Do not turn that harmless id churn into an indefinite
                # SEARCHING state.  A unique, spatially continuous candidate
                # with the same class/team inherits the control identity.
                selected = self._body_continuity_candidate(bodies)
                if selected is not None:
                    self._remember_body(selected)
                    return (
                        selected.bbox.x + selected.bbox.w / 2,
                        selected.bbox.y + selected.bbox.h / 2,
                        selected.bbox.w,
                        selected.bbox.h,
                    )
                if self._target_identity_reserved():
                    return None
                self._clear_target_identity_lock()
                self.preferred_target_detection_id = None
            if self.preferred_target_detection_id is not None:
                selected = next((item for item in bodies if item.id == self.preferred_target_detection_id), None)
                if selected is not None:
                    self._remember_body(selected)
                    return (
                        selected.bbox.x + selected.bbox.w / 2,
                        selected.bbox.y + selected.bbox.h / 2,
                        selected.bbox.w,
                        selected.bbox.h,
                    )
                if self._target_identity_reserved():
                    return None
                self.preferred_target_detection_id = None
            if bodies:
                best = min(
                    bodies,
                    key=lambda b: (b.bbox.x + b.bbox.w / 2 - preferred_x) ** 2
                    + (b.bbox.y + b.bbox.h / 2 - preferred_y) ** 2,
                )
                self._remember_body(best)
                return (best.bbox.x + best.bbox.w / 2, best.bbox.y + best.bbox.h / 2, best.bbox.w, best.bbox.h)
            return None

        balloons = list(event.balloon_detections)

        # Defense-in-depth: Friend balloon exclusion.
        # Under NO circumstances may the turret track, lock onto, or pursue a friendly balloon.
        friend_detection_ids: set[int] = set()
        if hasattr(event, "target_verdicts") and event.target_verdicts:
            for v in event.target_verdicts:
                if v.kind == "balloon" and (
                    str(getattr(v, "target_team", "")).lower() in {"friend", "dost"}
                    or getattr(v, "verdict_state", "") == "FRIEND_LOCKED"
                    or (hasattr(v, "label_tr") and "DOST" in str(v.label_tr).upper())
                ):
                    if v.detection_id is not None:
                        friend_detection_ids.add(v.detection_id)

        friendly_bodies = [
            b for b in getattr(event, "body_detections", [])
            if str(getattr(b, "target_team", "")).lower() in {"friend", "dost"}
        ]

        is_stage2_or_light = (
            self.controller_mode in LIGHT_CONTROLLER_MODES
            or getattr(self, "light_mode", False)
            or self.target_policy in {"BALLOON", "STAGE1_INDEPENDENT"}
        )
        if yb.Y6_DOST_FILTRE_A3 and (
            self.controller_mode == "A3" or self.target_policy in {"BALLOON_AIRCRAFT", "STAGE_3"}
        ):
            # Y6: A3, LIGHT_CONTROLLER_MODES içinde olduğu için dost-balon filtresi atlanıyordu
            is_stage2_or_light = False
        eligible_balloons = []
        for b in balloons:
            if not is_stage2_or_light:
                if b.id in friend_detection_ids:
                    continue
                bcx = float(getattr(b, "center_x", None) or (b.bbox.x + b.bbox.w / 2))
                bcy = float(getattr(b, "center_y", None) or (b.bbox.y + b.bbox.h / 2))
                if any(stage3_roi.attachment_roi_contains(fb.bbox, bcx, bcy) for fb in friendly_bodies):
                    continue
            eligible_balloons.append(b)
        balloons = eligible_balloons

        # If previous lock target is now recognized as friendly, break the lock immediately
        if self.preferred_target_detection_id is not None and self.preferred_target_detection_id in friend_detection_ids:
            self.preferred_target_detection_id = None
            self.preferred_target_x = None
            self.preferred_target_y = None
            self._clear_target_identity_lock()

        if self._locked_balloon_track_id is not None:
            locked_track = next(
                (item for item in self.multi_target_tracker.status().tracks if item.track_id == self._locked_balloon_track_id),
                None,
            )
            if locked_track is not None and locked_track.detection_id in friend_detection_ids:
                self._clear_target_identity_lock()
                self.preferred_target_detection_id = None

        if not balloons:
            return None

        if self.controller_mode in LIGHT_CONTROLLER_MODES or getattr(self, "light_mode", False):
            # Light reference centroid tracking with anti-ping-pong lock persistence and debounce:
            # 1. Track-id continuity: check if previously locked track is still reported by tracker.
            if self._locked_balloon_track_id is not None:
                locked_track = next(
                    (item for item in self.multi_target_tracker.status().tracks if item.track_id == self._locked_balloon_track_id),
                    None,
                ) if self.multi_target_tracker is not None else None
                selected = next(
                    (item for item in balloons if locked_track is not None and locked_track.fresh and item.id == locked_track.detection_id),
                    None,
                )
                if selected is not None:
                    self._pending_switch_candidate = None
                    self._pending_switch_frames = 0
                    self._remember_balloon(selected)
                    return (selected.center_x, selected.center_y, selected.bbox.w, selected.bbox.h)
                # 2. Track missing this frame: do NOT drop lock instantly. Coast on last known coordinates
                # within target_switch_grace_s (0.50-1.5s).
                if yb.Y15_KAYIP_FREN and self.preferred_target_x is not None and self.preferred_target_y is not None:
                    _y15_r = max(120.0, 3.0 * float(self._locked_balloon_bbox_w or 40.0))
                    _y15_d = lambda b: math.hypot(b.center_x - self.preferred_target_x, b.center_y - self.preferred_target_y)
                    _y15_yakin = [b for b in balloons if _y15_d(b) <= _y15_r]
                    if _y15_yakin:
                        _y15_sec = min(_y15_yakin, key=_y15_d)
                        self._pending_switch_candidate = None
                        self._pending_switch_frames = 0
                        self._remember_balloon(_y15_sec)
                        return (_y15_sec.center_x, _y15_sec.center_y, _y15_sec.bbox.w, _y15_sec.bbox.h)
                if self._target_identity_reserved() and self.preferred_target_x is not None and self.preferred_target_y is not None:
                    self._y15_coast = True
                    return (
                        self.preferred_target_x,
                        self.preferred_target_y,
                        self._locked_balloon_bbox_w or 40.0,
                        self._locked_balloon_bbox_h or 40.0,
                    )
                self._clear_target_identity_lock()
                self.preferred_target_detection_id = None

            # 3. Detection ID reacquisition if matching balloon is present
            if self.preferred_target_detection_id is not None:
                selected = next((item for item in balloons if item.id == self.preferred_target_detection_id), None)
                if selected is not None:
                    self._pending_switch_candidate = None
                    self._pending_switch_frames = 0
                    self._remember_balloon(selected)
                    return (selected.center_x, selected.center_y, selected.bbox.w, selected.bbox.h)
                # Coast if within grace period
                if self._target_identity_reserved() and self.preferred_target_x is not None and self.preferred_target_y is not None:
                    self._y15_coast = True
                    return (
                        self.preferred_target_x,
                        self.preferred_target_y,
                        self._locked_balloon_bbox_w or 40.0,
                        self._locked_balloon_bbox_h or 40.0,
                    )
                self.preferred_target_detection_id = None

            # 4. Target candidate selection with debounce (prevents flipping to nearby balloon on 1-frame jitter)
            if self.preferred_target_x is not None and self.preferred_target_y is not None:
                best = min(
                    balloons,
                    key=lambda b: (b.center_x - self.preferred_target_x) ** 2 + (b.center_y - self.preferred_target_y) ** 2,
                )
                dist = math.hypot(best.center_x - self.preferred_target_x, best.center_y - self.preferred_target_y)
                if dist > 400.0:
                    eligible = [b for b in balloons if self.inside_engagement_gate(b.center_x, center_x, is_already_locked=False)]
                    best = min(eligible or balloons, key=lambda b: (b.center_x - center_x) ** 2 + (b.center_y - center_y) ** 2)
                else:
                    cand_key = getattr(best, "id", None) or (round(best.center_x / 40), round(best.center_y / 40))
                    if self._pending_switch_candidate == cand_key:
                        self._pending_switch_frames += 1
                    else:
                        self._pending_switch_candidate, self._pending_switch_frames = cand_key, 1
                    debounce_req = getattr(self, "target_switch_debounce_frames", 3)
                    if self._pending_switch_frames < debounce_req:
                        self._y15_coast = True
                        return (
                            self.preferred_target_x,
                            self.preferred_target_y,
                            self._locked_balloon_bbox_w or best.bbox.w,
                            self._locked_balloon_bbox_h or best.bbox.h,
                        )
            else:
                eligible = [b for b in balloons if self.inside_engagement_gate(b.center_x, center_x, is_already_locked=False)]
                if not eligible:
                    return None
                best = min(eligible, key=lambda b: (b.center_x - center_x) ** 2 + (b.center_y - center_y) ** 2)

            self._pending_switch_candidate = None
            self._pending_switch_frames = 0
            self._remember_balloon(best)
            return (best.center_x, best.center_y, best.bbox.w, best.bbox.h)

        if self._locked_balloon_track_id is not None:
            locked_track = next(
                (item for item in self.multi_target_tracker.status().tracks if item.track_id == self._locked_balloon_track_id),
                None,
            )
            selected = next(
                (item for item in balloons if locked_track is not None and locked_track.fresh and item.id == locked_track.detection_id),
                None,
            )
            if selected is not None:
                self._pending_switch_candidate = None
                self._pending_switch_frames = 0
                self._remember_balloon(selected)
                return (selected.center_x, selected.center_y, selected.bbox.w, selected.bbox.h)
            if self._target_identity_reserved():
                return None
            self._clear_target_identity_lock()
            self.preferred_target_detection_id = None
        if self.preferred_target_detection_id is not None:
            selected = next((item for item in balloons if item.id == self.preferred_target_detection_id), None)
            if selected is not None:
                self._pending_switch_candidate = None
                self._pending_switch_frames = 0
                self._remember_balloon(selected)
                return (selected.center_x, selected.center_y, selected.bbox.w, selected.bbox.h)
            if self._target_identity_reserved():
                return None
            self.preferred_target_detection_id = None

        # Öncelik 1: Balon detection (seçili hedefe, yoksa merkeze en yakın)
        if balloons:
            best = min(
                balloons,
                key=lambda b: (b.center_x - preferred_x) ** 2 + (b.center_y - preferred_y) ** 2,
            )
            if self.preferred_target_x is not None and self.preferred_target_y is not None:
                continuity_dist = max(120.0, float(getattr(event, "frame_width", 640) or 640) * 0.12)
                dist = math.hypot(best.center_x - self.preferred_target_x, best.center_y - self.preferred_target_y)
                if dist > continuity_dist:
                    cand_key = getattr(best, "id", None) or (round(best.center_x / 40), round(best.center_y / 40))
                    if self._pending_switch_candidate == cand_key:
                        self._pending_switch_frames += 1
                    else:
                        self._pending_switch_candidate = cand_key
                        self._pending_switch_frames = 1
                    if self._pending_switch_frames < self.target_switch_debounce_frames:
                        return (self.preferred_target_x, self.preferred_target_y, best.bbox.w, best.bbox.h)
            self._pending_switch_candidate = None
            self._pending_switch_frames = 0
            self._remember_balloon(best)
            return (best.center_x, best.center_y, best.bbox.w, best.bbox.h)

        # Neither BALLOON nor BALLOON_AIRCRAFT policy jumps to an aircraft body
        # when a balloon drops out. The turret's primary objective is popping the
        # balloon. Jumping to the aircraft body causes violent hunting oscillations
        # and risks shooting the carrier aircraft (penalized in competition).
        if self.target_policy in {"BALLOON", "BALLOON_AIRCRAFT", "STAGE1_INDEPENDENT"}:
            return None

        # Öncelik 2: Body detection (en yakın) — yalnız AIRCRAFT politikasında
        if self.target_policy == "AIRCRAFT" and event.body_detections:
            best = min(
                event.body_detections,
                key=lambda b: (b.bbox.x + b.bbox.w / 2 - center_x) ** 2 + (b.bbox.y + b.bbox.h / 2 - center_y) ** 2,
            )
            self._remember_body(best)
            cx = best.bbox.x + best.bbox.w / 2
            cy = best.bbox.y + best.bbox.h / 2
            return (cx, cy, best.bbox.w, best.bbox.h)

        return None

    def _target_identity_reserved(self) -> bool:
        # If we fired on this target within the last 2.0s, do NOT reserve identity
        # once the target disappears from the frame — target was destroyed! 0 ms drop!
        if self._last_fire_at is not None and (time.monotonic() - self._last_fire_at < 2.0):
            return False

        locked_track_alive = (
            self._locked_balloon_track_id is not None
            and any(
                item.track_id == self._locked_balloon_track_id
                for item in self.multi_target_tracker.status().tracks
            )
        )
        return (
            locked_track_alive
            or (
                self._locked_target_last_seen_at is not None
                and time.monotonic() - self._locked_target_last_seen_at < self.target_switch_grace_s
            )
        )

    def _clear_target_identity_lock(self) -> None:
        self._locked_balloon_track_id = None
        self._locked_balloon_bbox_w = None
        self._locked_balloon_bbox_h = None
        self._locked_body_track_id = None
        self._locked_body_class_name = None
        self._locked_body_team = None
        self._locked_body_bbox_w = None
        self._locked_body_bbox_h = None
        self._locked_target_last_seen_at = None
        self.preferred_target_x = None
        self.preferred_target_y = None
        self._pending_switch_candidate = None
        self._pending_switch_frames = 0
        self._last_fire_at = None
        self._last_fire_target_id = None

    def _body_continuity_candidate(self, bodies):
        """Return only an unambiguous replacement for a churned body track id."""
        if not bodies or self.preferred_target_x is None or self.preferred_target_y is None:
            return None
        compatible = [
            body
            for body in bodies
            if (
                self._locked_body_class_name is None
                or body.class_name == self._locked_body_class_name
            )
            and (
                self._locked_body_team is None
                or body.target_team == self._locked_body_team
            )
        ]
        if not compatible:
            return None
        ranked = sorted(
            compatible,
            key=lambda body: (
                body.bbox.x + body.bbox.w / 2 - self.preferred_target_x
            ) ** 2 + (
                body.bbox.y + body.bbox.h / 2 - self.preferred_target_y
            ) ** 2,
        )
        best = ranked[0]
        best_distance = (
            (best.bbox.x + best.bbox.w / 2 - self.preferred_target_x) ** 2
            + (best.bbox.y + best.bbox.h / 2 - self.preferred_target_y) ** 2
        ) ** 0.5
        previous_diagonal = (
            (self._locked_body_bbox_w or best.bbox.w) ** 2
            + (self._locked_body_bbox_h or best.bbox.h) ** 2
        ) ** 0.5
        if best_distance > max(48.0, previous_diagonal * 2.0):
            return None
        if len(ranked) > 1:
            second = ranked[1]
            second_distance = (
                (second.bbox.x + second.bbox.w / 2 - self.preferred_target_x) ** 2
                + (second.bbox.y + second.bbox.h / 2 - self.preferred_target_y) ** 2
            ) ** 0.5
            if best_distance > second_distance * 0.65:
                return None
        return best

    def _remember_balloon(self, balloon) -> None:
        self.preferred_target_x = float(balloon.center_x)
        self.preferred_target_y = float(balloon.center_y)
        self.preferred_target_detection_id = int(balloon.id)
        self.preferred_target_kind = "balloon"
        self._locked_balloon_bbox_w = float(balloon.bbox.w) if getattr(balloon, "bbox", None) else 40.0
        self._locked_balloon_bbox_h = float(balloon.bbox.h) if getattr(balloon, "bbox", None) else 40.0
        track = next(
            (
                item
                for item in self.multi_target_tracker.status().tracks
                if item.fresh and item.detection_id == balloon.id
            ),
            None,
        ) if self.multi_target_tracker is not None else None
        if track is not None:
            self._locked_balloon_track_id = int(track.track_id)
        elif getattr(balloon, "track_id", None) is not None:
            self._locked_balloon_track_id = int(balloon.track_id)
        self._locked_body_track_id = None
        self._locked_target_last_seen_at = time.monotonic()

    def _remember_body(self, body) -> None:
        self.preferred_target_x = float(body.bbox.x + body.bbox.w / 2)
        self.preferred_target_y = float(body.bbox.y + body.bbox.h / 2)
        self.preferred_target_detection_id = int(body.id)
        self.preferred_target_kind = "body"
        self._locked_body_track_id = int(body.track_id) if body.track_id is not None else None
        self._locked_body_class_name = str(body.class_name)
        self._locked_body_team = str(body.target_team)
        self._locked_body_bbox_w = float(body.bbox.w)
        self._locked_body_bbox_h = float(body.bbox.h)
        self._locked_balloon_track_id = None
        self._locked_target_last_seen_at = time.monotonic()

    def _measurement_status(self, event: VisionEvent | None) -> str:
        if event is None:
            return "MISSING"
        frame_id = int(event.frame_id)
        timestamp_ms = int(event.timestamp_ms)
        if self._last_accepted_frame_id is None or self._last_accepted_timestamp_ms is None:
            return "NEW"
        if frame_id == self._last_accepted_frame_id and timestamp_ms == self._last_accepted_timestamp_ms:
            return "REUSED"
        if frame_id <= self._last_accepted_frame_id or timestamp_ms <= self._last_accepted_timestamp_ms:
            return "STALE_REJECTED"
        return "NEW"

    def _measurement_metadata(self, event: VisionEvent | None, status: str, now_s: float) -> dict[str, object]:
        if status == "STALE_REJECTED":
            timestamp_ms = self._last_accepted_timestamp_ms
        else:
            timestamp_ms = int(event.timestamp_ms) if event is not None else None
        return {
            "vision_timestamp_ms": timestamp_ms,
            "vision_age_ms": (
                max(0.0, now_s * 1000.0 - timestamp_ms)
                if timestamp_ms is not None
                else None
            ),
            "measurement_status": status,
        }

    def _selection_diagnostics(self, event: VisionEvent | None) -> dict[str, object]:
        """Expose selected identity state without using held data for control."""
        track = None
        if self._locked_balloon_track_id is not None:
            track = next(
                (
                    item
                    for item in self.multi_target_tracker.status().tracks
                    if item.track_id == self._locked_balloon_track_id
                ),
                None,
            )
        if track is not None:
            return {
                "selected_track_id": track.track_id,
                "selected_detection_id": track.detection_id if track.detection_id is not None else self.preferred_target_detection_id,
                "selected_target_kind": "balloon",
                "selected_target_confidence": track.confidence,
                "selected_bbox_x": track.bbox_x,
                "selected_bbox_y": track.bbox_y,
                "selected_bbox_w": track.bbox_w,
                "selected_bbox_h": track.bbox_h,
                "selected_track_age_frames": track.age_frames,
                "selected_track_misses": track.misses,
                "selected_track_last_seen_at": track.last_seen_at,
                "selected_track_last_seen_age_ms": track.last_seen_age_ms,
                "selected_track_occluded": not track.fresh,
            }

        body = None
        if event is not None and self.preferred_target_kind == "body":
            body = next(
                (
                    item
                    for item in event.body_detections
                    if (
                        self._locked_body_track_id is not None
                        and item.track_id == self._locked_body_track_id
                    )
                    or (
                        self._locked_body_track_id is None
                        and self.preferred_target_detection_id is not None
                        and item.id == self.preferred_target_detection_id
                    )
                ),
                None,
            )
        if body is not None:
            return {
                "selected_track_id": body.track_id,
                "selected_detection_id": body.id,
                "selected_target_kind": "body",
                "selected_target_confidence": body.confidence,
                "selected_bbox_x": float(body.bbox.x),
                "selected_bbox_y": float(body.bbox.y),
                "selected_bbox_w": float(body.bbox.w),
                "selected_bbox_h": float(body.bbox.h),
                "selected_track_age_frames": max(0, int(body.stable_frames)),
                "selected_track_misses": 0,
                "selected_track_occluded": False,
            }
        return {
            "selected_track_id": self._locked_body_track_id or self._locked_balloon_track_id,
            "selected_detection_id": self.preferred_target_detection_id,
            "selected_target_kind": self.preferred_target_kind,
            "selected_track_occluded": bool(self._locked_body_track_id or self._locked_balloon_track_id),
        }

    def _lead_horizon_ms(self, event: VisionEvent) -> float:
        measured_latency = max(0.0, float(event.total_latency_ms or event.total_ms or 0.0))
        control_period = 1000.0 / max(1.0, self.command_rate_hz)
        return min(self.lead_max_horizon_ms, (measured_latency + control_period) * self.lead_latency_multiplier)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _reset_internals(self) -> None:
        """Tüm iç durumu sıfırla."""
        self._reset_control_state()
        self._target_lost_frames = 0
        self._total_frames = 0
        self._target_count = 0
        self._lost_count = 0
        self._last_fire_result = None
        self._last_time = time.time()
        self._last_measurement_frame_id = None
        self._last_measurement_timestamp_s = None
        self._last_accepted_frame_id = None
        self._last_accepted_timestamp_ms = None
        self.preferred_target_x = None
        self.preferred_target_y = None
        self.preferred_target_detection_id = None
        self.preferred_target_kind = None
        self._pending_switch_candidate = None
        self._pending_switch_frames = 0
        self._locked_balloon_track_id = None
        self._locked_body_track_id = None
        self._locked_target_last_seen_at = None

    def _reset_control_state(self, *, preserve_general_speed: bool = False) -> None:
        if self.controller_mode in LIGHT_CONTROLLER_MODES and preserve_general_speed:
            self._light_controller.reset_target_loss()
        else:
            self._light_controller.reset()
        self._smooth_x = 0.0
        self._smooth_y = 0.0
        self._prev_norm_error_x = 0.0
        self._prev_norm_error_y = 0.0
        self._derivative_x = 0.0
        self._derivative_y = 0.0
        self._integral_x = 0.0
        self._integral_y = 0.0
        self._last_output_x = 0.0
        self._last_output_y = 0.0
        self._lock_hysteresis_active = False
        self._last_measurement_frame_id = None
        self._last_measurement_timestamp_s = None
        self._last_accepted_frame_id = None
        self._last_accepted_timestamp_ms = None
