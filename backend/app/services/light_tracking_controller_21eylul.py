"""The field-tested pixel-space Light tracking controllers.

The controller deliberately has no camera, serial, or trigger dependency.  It
returns semantic horizontal/right and vertical/up speeds; the Light firmware's
raw vertical ``X`` command is exposed separately for the serial adapter.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
import time


def _sign(value: float) -> float:
    return 1.0 if value > 0 else -1.0 if value < 0 else 0.0


@dataclass(frozen=True)
class LightControllerParams:
    """Values from the checked-in Light controller profiles."""

    kp_x: float = 11.0
    ki_x: float = 0.0
    kd_x: float = 1.1
    kp_y: float = 8.0
    ki_y: float = 0.0
    kd_y: float = 2.0
    invert_x: bool = False
    invert_y: bool = False
    center_filter: float = 0.25
    deadband_x: float = 3.0
    deadband_y_on: float = 8.0
    deadband_y_off: float = 5.0
    min_speed: float = 120.0
    min_speed_ramp: float = 25.0
    max_speed: float = 8000.0
    max_acceleration: float = 7500.0
    pan_steps_per_degree: float = (200.0 * 8.0 * 30.0) / 360.0
    fov_px_per_degree: float = 52.0
    lead_share_percent: float = 60.0
    feedforward_gain: float = 0.65
    lead_threshold: float = 8.5
    lead_gain: float = 0.65
    lead_filter: float = 0.08
    box_threshold_1: float = 100.0
    box_threshold_2: float = 200.0
    box_threshold_3: float = 300.0
    box_gain_1: float = 0.05
    box_gain_2: float = 0.015
    box_gain_3: float = 0.02
    box_gain_4: float = 0.01
    integral_limit_x: float = 3000.0
    integral_limit_y: float = 1500.0
    fire_tolerance_ratio: float = 0.15
    fire_tolerance_min_px: float = 10.0
    tilt_filter: float = 0.50
    kalman_q: float = 0.05
    kalman_r: float = 2.50
    derivative_filter: float = 0.35
    zone_far_px: float = 50.0
    zone_near_px: float = 15.0
    zone_far_kp_mult: float = 1.35
    zone_far_accel_mult: float = 1.50
    zone_near_kp_mult: float = 0.68
    sine_lead_gain: float = 0.40
    sine_damping_gain: float = 0.50
    corridor_ratio: float = 2.30
    apex_anticipation_ratio: float = 0.70

    @classmethod
    def for_mode(cls, mode: str) -> "LightControllerParams":
        mode_upper = str(mode).upper()
        if mode_upper == "GENERAL":
            # Match Light reference General Tracking (otonom_takip.py / pid_ayarlar.json):
            return cls(
                kp_x=11.0,
                ki_x=0.0,
                kd_x=1.1,
                kp_y=8.0,
                ki_y=0.0,
                kd_y=4.0,
                invert_x=False,
                invert_y=False,
                deadband_x=0.0,
                deadband_y_on=0.0,
                deadband_y_off=0.0,
                min_speed=0.0,
                min_speed_ramp=0.0,
                max_speed=12000.0,
                max_acceleration=5000.0,
                integral_limit_x=5000.0,
                integral_limit_y=5000.0,
                lead_share_percent=0.0,
                feedforward_gain=0.0,
                center_filter=1.0,
            )
        if mode_upper == "A2":
            # Match Light reference Aşama 2 (asama2_otonom.py / asama2_pid_ayarlar.json) - reliable baseline:
            return cls(
                kp_x=11.0,
                ki_x=0.0,
                kd_x=1.1,
                kp_y=8.0,
                ki_y=0.0,
                kd_y=2.0,
                invert_x=False,
                invert_y=False,
                center_filter=0.25,
                deadband_x=3.0,
                deadband_y_on=8.0,
                deadband_y_off=5.0,
                min_speed=120.0,
                min_speed_ramp=25.0,
                max_speed=8000.0,
                max_acceleration=7000.0,
                lead_share_percent=60.0,
                feedforward_gain=0.65,
                integral_limit_x=3000.0,
                integral_limit_y=1500.0,
            )
        if mode_upper == "OPT_D_FILTER":
            # Mode 1: A2 base + low-pass filtered derivative to eliminate YOLO jitter
            return cls(
                kp_x=11.0,
                ki_x=0.0,
                kd_x=1.6,
                kp_y=8.0,
                ki_y=0.0,
                kd_y=3.0,
                invert_x=False,
                invert_y=False,
                center_filter=0.25,
                deadband_x=3.0,
                deadband_y_on=8.0,
                deadband_y_off=5.0,
                min_speed=120.0,
                min_speed_ramp=25.0,
                max_speed=8000.0,
                max_acceleration=7000.0,
                lead_share_percent=60.0,
                feedforward_gain=0.65,
                integral_limit_x=3000.0,
                integral_limit_y=1500.0,
                derivative_filter=0.30,
            )
        if mode_upper == "OPT_ZONE_GAIN":
            # Mode 2: Mod 1 + adaptive 3-zone proportional scaling (14cm balloon diameter)
            return cls(
                kp_x=11.0,
                ki_x=0.0,
                kd_x=1.6,
                kp_y=8.0,
                ki_y=0.0,
                kd_y=3.0,
                invert_x=False,
                invert_y=False,
                center_filter=0.25,
                deadband_x=3.0,
                deadband_y_on=8.0,
                deadband_y_off=5.0,
                min_speed=120.0,
                min_speed_ramp=25.0,
                max_speed=8000.0,
                max_acceleration=7000.0,
                lead_share_percent=60.0,
                feedforward_gain=0.65,
                integral_limit_x=3000.0,
                integral_limit_y=1500.0,
                derivative_filter=0.30,
                zone_far_px=50.0,
                zone_near_px=15.0,
                zone_far_kp_mult=1.30,
                zone_far_accel_mult=1.20,
                zone_near_kp_mult=0.75,
            )
        if mode_upper == "OPT_SINE_TRACK":
            # Mode 3: Mod 1 + Mod 2 + sinusoidal turn damping on wavy rail
            return cls(
                kp_x=11.0,
                ki_x=0.0,
                kd_x=1.6,
                kp_y=8.0,
                ki_y=0.0,
                kd_y=3.0,
                invert_x=False,
                invert_y=False,
                center_filter=0.25,
                deadband_x=3.0,
                deadband_y_on=8.0,
                deadband_y_off=5.0,
                min_speed=120.0,
                min_speed_ramp=25.0,
                max_speed=8000.0,
                max_acceleration=7000.0,
                lead_share_percent=60.0,
                feedforward_gain=0.65,
                integral_limit_x=3000.0,
                integral_limit_y=1500.0,
                derivative_filter=0.30,
                zone_far_px=50.0,
                zone_near_px=15.0,
                zone_far_kp_mult=1.30,
                zone_far_accel_mult=1.20,
                zone_near_kp_mult=0.75,
                sine_damping_gain=0.50,
                corridor_ratio=2.30,
                apex_anticipation_ratio=0.70,
            )
        if mode_upper == "A3":
            return cls(min_speed=70.0)
        return cls()


@dataclass(frozen=True)
class LightControlOutput:
    """One Light controller step.

    ``raw_speed_x/y`` are the exact Light firmware axis convention: raw X is
    vertical and raw Y is horizontal.  ``speed_x/y`` are the main gateway's
    semantic convention: +X camera-right and +Y camera-up.
    """

    speed_x: int
    speed_y: int
    raw_speed_x: float
    raw_speed_y: float
    raw_pid_x: float
    raw_pid_y: float
    error_x: float
    error_y: float
    filtered_x: float
    filtered_y: float
    locked: bool
    deadband_zone: str
    using_kalman: bool
    aim_x: float = 0.0
    aim_y: float = 0.0
    dynamic_offset_x: float = 0.0
    dynamic_offset_y: float = 0.0
    target_velocity_x: float = 0.0
    target_velocity_y: float = 0.0
    apex_damping: float = 0.0


class LightTrackingController:
    """Pixel-space controller ported from the Light reference."""

    def __init__(self, mode: str = "A2", persisted_path: Path | None = None) -> None:
        normalized = str(mode).upper()
        allowed = {"GENERAL", "A2", "A3", "OPT_D_FILTER", "OPT_ZONE_GAIN", "OPT_SINE_TRACK"}
        self.mode = normalized if normalized in allowed else "A2"
        self.persisted_path = Path(persisted_path) if persisted_path is not None else None
        self.params = LightControllerParams.for_mode(self.mode)
        self.reset()
        self._load_persisted_general()

    @property
    def using_kalman(self) -> bool:
        return self.mode == "A3"

    def set_mode(self, mode: str) -> None:
        requested = str(mode).upper()
        allowed = {"GENERAL", "A2", "A3", "OPT_D_FILTER", "OPT_ZONE_GAIN", "OPT_SINE_TRACK"}
        normalized = requested if requested in allowed else "A2"
        if normalized != self.mode:
            self.mode = normalized
            self.params = LightControllerParams.for_mode(normalized)
            self.reset()
            self._load_persisted_general()

    def configure(self, **values: float | bool) -> None:
        """Apply explicitly supplied control values without changing mode."""
        allowed = set(self.params.__dataclass_fields__)
        updates = {key: value for key, value in values.items() if key in allowed and value is not None}
        if updates:
            self.params = LightControllerParams(**{**self.params.__dict__, **updates})
            self._persist_general()

    def _load_persisted_general(self) -> None:
        if self.mode != "GENERAL" or self.persisted_path is None or not self.persisted_path.is_file():
            return
        try:
            data = json.loads(self.persisted_path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return
            updates: dict[str, float | bool] = {}
            for key in ("kp_x", "ki_x", "kd_x", "kp_y", "ki_y", "kd_y"):
                value = data.get(key)
                if isinstance(value, (int, float)) and math.isfinite(float(value)):
                    updates[key] = float(value)
            for key in ("invert_x", "invert_y"):
                value = data.get(key)
                if isinstance(value, bool):
                    updates[key] = value
            if updates:
                self.params = LightControllerParams(**{**self.params.__dict__, **updates})
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            # A damaged tuning file must leave the checked-in General
            # baseline usable; the next explicit knob update can replace it.
            return

    def _persist_general(self) -> None:
        if self.mode != "GENERAL" or self.persisted_path is None:
            return
        payload = {
            "kp_x": self.params.kp_x,
            "ki_x": self.params.ki_x,
            "kd_x": self.params.kd_x,
            "kp_y": self.params.kp_y,
            "ki_y": self.params.ki_y,
            "kd_y": self.params.kd_y,
            "invert_x": self.params.invert_x,
            "invert_y": self.params.invert_y,
        }
        temporary = self.persisted_path.with_name(f".{self.persisted_path.name}.tmp")
        try:
            self.persisted_path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            temporary.replace(self.persisted_path)
        except OSError:
            # Persistence is operator convenience; never turn a write error
            # into a tracking-loop failure or a physical command change.
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    def reset(self) -> None:
        self._filtered_x: float | None = None
        self._filtered_y: float | None = None
        self._previous_target_x: float | None = None
        self._previous_target_y: float | None = None
        self._target_velocity_x = 0.0
        self._target_velocity_y = 0.0
        self._prev_target_vel_x = 0.0
        self._fast_vel_x = 0.0
        self._prev_fast_vel_x = 0.0
        self._filtered_dx: float | None = None
        self._filtered_dy: float | None = None
        self._dynamic_offset_x = 0.0
        self._dynamic_offset_y = 0.0
        self._integral_x = 0.0
        self._integral_y = 0.0
        self._previous_error_x = 0.0
        self._previous_error_y = 0.0
        self._previous_speed_x = 0.0
        self._previous_speed_y = 0.0
        self._has_previous_speed = False
        self._first_error = True
        self._vertical_awake = False
        self._tilt_y: float | None = None
        self._kalman_x: list[float] | None = None
        self._kalman_y: list[float] | None = None
        self._filtered_general_x: float | None = None
        self._filtered_general_y: float | None = None
        self._last_fresh_time: float | None = None
        self._last_fresh_x: float | None = None
        self._last_fresh_y: float | None = None
        self._lead_active: bool = False

    def reset_target_loss(self) -> None:
        """Reset the original General controller's PID memory after a loss.

        ``otonom_takip.py`` clears integral and previous-error state when a
        target is missing, while retaining the previous speed so the next
        reacquisition still passes through the same acceleration limiter.
        """
        self._integral_x = 0.0
        self._integral_y = 0.0
        self._previous_error_x = 0.0
        self._previous_error_y = 0.0
        self._filtered_dx = None
        self._filtered_dy = None
        self._prev_target_vel_x = 0.0
        self._fast_vel_x = 0.0
        self._prev_fast_vel_x = 0.0
        self._first_error = True
        self._filtered_general_x = None
        self._filtered_general_y = None
        self._last_fresh_time = None
        self._last_fresh_x = None
        self._last_fresh_y = None
        self._lead_active = False

    def update(
        self,
        target_x: float,
        target_y: float,
        frame_width: int,
        frame_height: int,
        dt: float,
        bbox_width: float,
        bbox_height: float,
        is_fresh: bool = True,
    ) -> LightControlOutput:
        """Advance one control frame using the Light reference equations."""
        values = (target_x, target_y, frame_width, frame_height, bbox_width, bbox_height)
        if not all(math.isfinite(float(value)) for value in values) or bbox_width <= 0 or bbox_height <= 0:
            self.reset_target_loss() if self.mode in {"GENERAL", "OPT_D_FILTER", "OPT_ZONE_GAIN", "OPT_SINE_TRACK"} else self.reset()
            return LightControlOutput(
                0, 0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, False, "none", self.using_kalman,
                aim_x=target_x if math.isfinite(float(target_x)) else 0.0,
                aim_y=target_y if math.isfinite(float(target_y)) else 0.0,
            )

        dt = max(0.001, float(dt))
        if self.mode == "GENERAL":
            return self._update_general(
                target_x=target_x,
                target_y=target_y,
                frame_width=frame_width,
                frame_height=frame_height,
                dt=dt,
                bbox_width=bbox_width,
                bbox_height=bbox_height,
            )
        return self._update_a2(
            target_x=target_x,
            target_y=target_y,
            frame_width=frame_width,
            frame_height=frame_height,
            dt=dt,
            bbox_width=bbox_width,
            bbox_height=bbox_height,
            is_fresh=is_fresh,
        )

    def _update_a2(
        self,
        *,
        target_x: float,
        target_y: float,
        frame_width: int,
        frame_height: int,
        dt: float,
        bbox_width: float,
        bbox_height: float,
        is_fresh: bool = True,
    ) -> LightControlOutput:
        if self.using_kalman:
            filtered_x, filtered_y, kalman_vx = self._kalman_update(target_x, target_y, dt)
            if self._tilt_y is None:
                self._tilt_y = filtered_y
            else:
                alpha = max(0.05, min(1.0, self.params.tilt_filter))
                self._tilt_y = alpha * filtered_y + (1.0 - alpha) * self._tilt_y
            filtered_y = self._tilt_y
            measured_vx = kalman_vx
            measured_vy = 0.0
        else:
            alpha = max(0.05, min(1.0, self.params.center_filter))
            if self._filtered_x is None:
                filtered_x = target_x
                filtered_y = target_y
            else:
                filtered_x = alpha * target_x + (1.0 - alpha) * self._filtered_x
                filtered_y = alpha * target_y + (1.0 - alpha) * self._filtered_y

            measured_vx = 0.0
            measured_vy = 0.0
            if is_fresh:
                now = time.monotonic()
                if self._last_fresh_time is not None and self._last_fresh_x is not None:
                    dt_fresh = max(0.010, min(0.200, now - self._last_fresh_time))
                    measured_vx = (target_x - self._last_fresh_x) / dt_fresh
                    if self._last_fresh_y is not None:
                        measured_vy = (target_y - self._last_fresh_y) / dt_fresh
                elif self._previous_target_x is not None and dt > 0.0001:
                    measured_vx = (filtered_x - self._previous_target_x) / dt
                    if self._previous_target_y is not None:
                        measured_vy = (filtered_y - self._previous_target_y) / dt
                self._last_fresh_time = now
                self._last_fresh_x = target_x
                self._last_fresh_y = target_y

        self._filtered_x = filtered_x
        self._filtered_y = filtered_y
        self._previous_target_x = filtered_x
        self._previous_target_y = filtered_y

        fov_scale = float(frame_width) / 1920.0 if frame_width > 0 else 1.0
        effective_fov = max(1.0, self.params.fov_px_per_degree * fov_scale)
        threshold_on = self.params.lead_threshold
        threshold_off = max(3.0, threshold_on * 0.5)

        if is_fresh:
            turret_deg_s = self._previous_speed_x / self.params.pan_steps_per_degree
            target_vx = measured_vx + turret_deg_s * effective_fov
            alpha_v = max(0.05, min(1.0, self.params.lead_filter))
            self._target_velocity_x = alpha_v * target_vx + (1.0 - alpha_v) * self._target_velocity_x
            self._target_velocity_y = alpha_v * measured_vy + (1.0 - alpha_v) * self._target_velocity_y

            alpha_fast = 0.35 if self.mode == "OPT_SINE_TRACK" else alpha_v
            self._fast_vel_x = alpha_fast * target_vx + (1.0 - alpha_fast) * self._fast_vel_x

            # Hysteresis on lead activation to avoid chatter near threshold
            hiz_abs = abs(self._target_velocity_x)

            if not self._lead_active:
                if hiz_abs >= threshold_on:
                    self._lead_active = True
            else:
                if hiz_abs < threshold_off:
                    self._lead_active = False

            if self._lead_active:
                gain = self._box_gain(bbox_width)
                max_share = (bbox_width / 2.0) * (self.params.lead_share_percent / 100.0)
                amount = min(
                    (hiz_abs - threshold_off) * gain * 0.1,
                    1.0,
                )
                lead_offset = _sign(self._target_velocity_x) * max_share * amount
            else:
                lead_offset = 0.0

            alpha_lead = max(0.05, min(1.0, self.params.lead_filter))
            self._dynamic_offset_x = alpha_lead * lead_offset + (1.0 - alpha_lead) * self._dynamic_offset_x
            if abs(self._dynamic_offset_x) < 0.5:
                self._dynamic_offset_x = 0.0

            max_allowed_offset = bbox_width * 0.30
            self._dynamic_offset_x = max(-max_allowed_offset, min(max_allowed_offset, self._dynamic_offset_x))

        aim_x = filtered_x + self._dynamic_offset_x
        aim_y = filtered_y
        frame_cx = float(frame_width) / 2.0
        frame_cy = float(frame_height) / 2.0
        error_x = aim_x - frame_cx
        error_y = aim_y - frame_cy

        if abs(error_x) < self.params.deadband_x:
            error_x = 0.0
        if not self._vertical_awake:
            if abs(error_y) >= self.params.deadband_y_on:
                self._vertical_awake = True
        elif abs(error_y) <= self.params.deadband_y_off:
            self._vertical_awake = False
        if not self._vertical_awake:
            error_y = 0.0

        # Proportional & Acceleration zone scaling (Mod 2 & Mod 3)
        zone_name = "full"
        accel_mult = 1.0
        kp_mult_x = 1.0
        kp_mult_y = 1.0
        if self.mode in {"OPT_ZONE_GAIN", "OPT_SINE_TRACK"}:
            error_dist = math.hypot(error_x, error_y)
            if error_dist > self.params.zone_far_px:
                kp_mult_x = self.params.zone_far_kp_mult
                kp_mult_y = self.params.zone_far_kp_mult
                accel_mult = self.params.zone_far_accel_mult
                zone_name = "far_catchup"
            elif error_dist < self.params.zone_near_px:
                kp_mult_x = self.params.zone_near_kp_mult
                kp_mult_y = self.params.zone_near_kp_mult
                zone_name = "balloon_lock"
            else:
                zone_name = "mid_track"

        raw_pid_x = self._pid_axis(
            error_x,
            dt,
            axis="x",
            kp=self.params.kp_x * kp_mult_x,
            ki=self.params.ki_x,
            kd=self.params.kd_x,
            previous_error=self._previous_error_x,
        )
        raw_pid_y = self._pid_axis(
            error_y,
            dt,
            axis="y",
            kp=self.params.kp_y * kp_mult_y,
            ki=self.params.ki_y,
            kd=self.params.kd_y,
            previous_error=self._previous_error_y,
        )
        self._previous_error_x = error_x
        self._previous_error_y = error_y

        # Velocity feedforward to eliminate steady-state lag during dynamic tracking
        feedforward_x = 0.0
        if self._lead_active and abs(self._target_velocity_x) >= threshold_off and self.params.feedforward_gain > 0.0:
            target_deg_s = self._target_velocity_x / effective_fov
            feedforward_x = target_deg_s * self.params.pan_steps_per_degree * self.params.feedforward_gain

        # Sinusoidal apex braking & zero-overshoot anticipatory taper (OPT_SINE_TRACK)
        apex_damping = 0.0
        if self.mode == "OPT_SINE_TRACK" and is_fresh:
            # Physical peak speed invariant: v_peak = (0.20 m/s / 0.14 m) * bbox_width = 1.4286 * bbox_width
            v_peak_px_s = max(8.0, 1.4286 * max(8.0, bbox_width))
            fast_accel_x = (self._fast_vel_x - self._prev_fast_vel_x) / dt if dt > 0.001 else 0.0
            self._prev_fast_vel_x = self._fast_vel_x

            # Deceleration check: velocity and acceleration have opposing signs
            is_decelerating = (self._fast_vel_x * fast_accel_x < 0) and (abs(self._fast_vel_x) > 2.0)

            if is_decelerating:
                # Target is approaching apex turnaround: smoothly taper feedforward to prevent blowing past the apex
                norm_vel = abs(self._fast_vel_x) / v_peak_px_s
                feedforward_taper = max(0.0, min(1.0, norm_vel / 0.45))
                feedforward_x *= feedforward_taper

                # Active braking damping proportional to deceleration and damping gain
                decel_ratio = min(1.5, abs(fast_accel_x) / (v_peak_px_s * 1.5))
                brake_torque = decel_ratio * self.params.sine_damping_gain * self.params.pan_steps_per_degree
                apex_damping = -_sign(self._fast_vel_x) * min(abs(feedforward_x) + abs(raw_pid_x), brake_torque)

        light_speed_x = raw_pid_x + feedforward_x + apex_damping
        if abs(error_x) >= self.params.deadband_x and abs(light_speed_x) < self.params.min_speed:
            light_speed_x = self.params.min_speed * _sign(error_x)

        light_speed_y = raw_pid_y
        if abs(error_y) > 0.0:
            minimum_y = min(self.params.min_speed, abs(error_y) * self.params.min_speed_ramp)
            if abs(light_speed_y) < minimum_y:
                light_speed_y = minimum_y * _sign(error_y)

        light_speed_x = self._clamp(light_speed_x, self.params.max_speed)
        light_speed_y = self._clamp(light_speed_y, self.params.max_speed)
        max_delta = (self.params.max_acceleration * accel_mult) * dt
        light_speed_x = self._slew(light_speed_x, self._previous_speed_x, max_delta)
        light_speed_y = self._slew(light_speed_y, self._previous_speed_y, max_delta)
        self._previous_speed_x = light_speed_x
        self._previous_speed_y = light_speed_y

        tolerance_x = max(self.params.fire_tolerance_min_px, bbox_width * self.params.fire_tolerance_ratio)
        tolerance_y = max(self.params.fire_tolerance_min_px, bbox_height * self.params.fire_tolerance_ratio)
        locked = abs(filtered_x - frame_cx) <= tolerance_x and abs(filtered_y - frame_cy) <= tolerance_y

        return LightControlOutput(
            speed_x=int(round(light_speed_x)),
            # Light raw X is image-down-positive.  Gateway semantic +Y is up.
            speed_y=int(round(-light_speed_y)),
            raw_speed_x=light_speed_y,
            raw_speed_y=light_speed_x,
            raw_pid_x=raw_pid_x,
            raw_pid_y=raw_pid_y,
            error_x=aim_x - frame_cx,
            error_y=aim_y - frame_cy,
            filtered_x=filtered_x,
            filtered_y=filtered_y,
            locked=locked,
            deadband_zone="locked" if locked else zone_name,
            using_kalman=self.using_kalman,
            aim_x=aim_x,
            aim_y=aim_y,
            dynamic_offset_x=self._dynamic_offset_x,
            dynamic_offset_y=0.0,
            target_velocity_x=self._target_velocity_x,
            target_velocity_y=self._target_velocity_y,
            apex_damping=apex_damping,
        )

    def _update_general(
        self,
        *,
        target_x: float,
        target_y: float,
        frame_width: int,
        frame_height: int,
        dt: float,
        bbox_width: float = 50.0,
        bbox_height: float = 50.0,
    ) -> LightControlOutput:
        """Run Light reference General Tracking (otonom_takip.py) logic."""
        frame_cx = float(frame_width) / 2.0
        frame_cy = float(frame_height) / 2.0
        error_x = target_x - frame_cx
        error_y = target_y - frame_cy

        # Integral windup protection (max 5000)
        self._integral_x = self._clamp(
            self._integral_x + error_x * dt,
            self.params.integral_limit_x,
        )
        self._integral_y = self._clamp(
            self._integral_y + error_y * dt,
            self.params.integral_limit_y,
        )

        d_x = ((error_x - self._previous_error_x) / dt) * self.params.kd_x if dt > 0 else 0.0
        d_y = ((error_y - self._previous_error_y) / dt) * self.params.kd_y if dt > 0 else 0.0
        self._previous_error_x = error_x
        self._previous_error_y = error_y

        raw_pid_x = self.params.kp_x * error_x + self.params.ki_x * self._integral_x + d_x
        raw_pid_y = self.params.kp_y * error_y + self.params.ki_y * self._integral_y + d_y

        desired_x = -raw_pid_x if self.params.invert_x else raw_pid_x
        desired_y = -raw_pid_y if self.params.invert_y else raw_pid_y

        desired_x = self._clamp(desired_x, self.params.max_speed)
        desired_y = self._clamp(desired_y, self.params.max_speed)

        # Acceleration slew rate limiter
        if self._has_previous_speed:
            max_delta = self.params.max_acceleration * dt
            if abs(desired_x - self._previous_speed_x) > max_delta:
                desired_x = self._previous_speed_x + max_delta * (1.0 if desired_x > self._previous_speed_x else -1.0)
            if abs(desired_y - self._previous_speed_y) > max_delta:
                desired_y = self._previous_speed_y + max_delta * (1.0 if desired_y > self._previous_speed_y else -1.0)
        self._previous_speed_x = desired_x
        self._previous_speed_y = desired_y
        self._has_previous_speed = True

        measured_vx = (target_x - self._previous_target_x) / dt if self._previous_target_x is not None and dt > 0.0001 else 0.0
        measured_vy = (target_y - self._previous_target_y) / dt if self._previous_target_y is not None and dt > 0.0001 else 0.0
        self._previous_target_x = target_x
        self._previous_target_y = target_y
        self._target_velocity_x = 0.25 * measured_vx + 0.75 * self._target_velocity_x
        self._target_velocity_y = 0.25 * measured_vy + 0.75 * self._target_velocity_y

        return LightControlOutput(
            speed_x=int(round(desired_x)),
            speed_y=int(round(-desired_y)),
            raw_speed_x=desired_y,
            raw_speed_y=desired_x,
            raw_pid_x=raw_pid_x,
            raw_pid_y=raw_pid_y,
            error_x=error_x,
            error_y=error_y,
            filtered_x=target_x,
            filtered_y=target_y,
            locked=False,
            deadband_zone="full",
            using_kalman=False,
            aim_x=target_x,
            aim_y=target_y,
            dynamic_offset_x=0.0,
            dynamic_offset_y=0.0,
            target_velocity_x=self._target_velocity_x,
            target_velocity_y=self._target_velocity_y,
        )

    def _pid_axis(
        self,
        error: float,
        dt: float,
        *,
        axis: str,
        kp: float,
        ki: float,
        kd: float,
        previous_error: float,
    ) -> float:
        if axis == "x":
            self._integral_x = self._clamp(
                self._integral_x + error * dt,
                self.params.integral_limit_x,
            )
            integral = self._integral_x
        else:
            self._integral_y = self._clamp(
                self._integral_y + error * dt,
                self.params.integral_limit_y,
            )
            integral = self._integral_y
        raw_d = ((error - previous_error) / dt) if dt > 0.0001 else 0.0
        if self.mode in {"OPT_D_FILTER", "OPT_ZONE_GAIN", "OPT_SINE_TRACK"}:
            alpha_d = max(0.05, min(1.0, float(getattr(self.params, "derivative_filter", 0.30) or 0.30)))
            if axis == "x":
                if self._filtered_dx is None:
                    self._filtered_dx = raw_d
                else:
                    self._filtered_dx = alpha_d * raw_d + (1.0 - alpha_d) * self._filtered_dx
                d_term = self._filtered_dx
            else:
                if self._filtered_dy is None:
                    self._filtered_dy = raw_d
                else:
                    self._filtered_dy = alpha_d * raw_d + (1.0 - alpha_d) * self._filtered_dy
                d_term = self._filtered_dy
        else:
            d_term = raw_d
        return kp * error + ki * integral + kd * d_term

    @staticmethod
    def _clamp(value: float, limit: float) -> float:
        return max(-abs(limit), min(abs(limit), value))

    @staticmethod
    def _slew(value: float, previous: float, max_delta: float) -> float:
        delta = value - previous
        if abs(delta) <= max_delta:
            return value
        return previous + max_delta * _sign(delta)

    def _box_gain(self, width: float) -> float:
        if width <= self.params.box_threshold_1:
            return self.params.box_gain_1
        if width <= self.params.box_threshold_2:
            return self.params.box_gain_2
        if width <= self.params.box_threshold_3:
            return self.params.box_gain_3
        return self.params.box_gain_4

    def _kalman_update(self, measurement_x: float, measurement_y: float, dt: float) -> tuple[float, float, float]:
        self._kalman_x, velocity_x = self._kalman_axis(self._kalman_x, measurement_x, dt)
        self._kalman_y, _ = self._kalman_axis(self._kalman_y, measurement_y, dt)
        return self._kalman_x[0], self._kalman_y[0], velocity_x

    def _kalman_axis(self, state: list[float] | None, measurement: float, dt: float) -> tuple[list[float], float]:
        if state is None:
            return [measurement, 0.0, 500.0, 0.0, 0.0, 500.0], 0.0

        position, velocity, p00, p01, p10, p11 = state
        predicted_position = position + dt * velocity
        predicted_velocity = velocity
        predicted_p00 = p00 + dt * (p10 + p01) + dt * dt * p11 + self.params.kalman_q
        predicted_p01 = p01 + dt * p11
        predicted_p10 = p10 + dt * p11
        predicted_p11 = p11 + self.params.kalman_q

        innovation = measurement - predicted_position
        innovation_covariance = predicted_p00 + self.params.kalman_r + 1e-6
        gain_position = predicted_p00 / innovation_covariance
        gain_velocity = predicted_p10 / innovation_covariance
        position = predicted_position + gain_position * innovation
        velocity = predicted_velocity + gain_velocity * innovation
        p00 = (1.0 - gain_position) * predicted_p00
        p01 = (1.0 - gain_position) * predicted_p01
        p10 = predicted_p10 - gain_velocity * predicted_p00
        p11 = predicted_p11 - gain_velocity * predicted_p01
        return [position, velocity, p00, p01, p10, p11], velocity
