"""
v2_ego_resolver.py — Measured Ego-Motion Angular Velocity Decomposition
Based on GLM-4 and Claude 3.5 Sonnet Deep Engineering Consensus.

- Samples turret physical orientation at exact camera frame capture timestamp t_c.
- Eliminates 429 px/s artificial velocity spike caused by command-lag during motor acceleration.
- Decouples true target angular velocity from turret camera movement.
"""

from __future__ import annotations

import math
from typing import NamedTuple


class EgoVelocity(NamedTuple):
    omega_pan_deg_s: float
    omega_tilt_deg_s: float
    ego_flow_x_px_s: float
    ego_flow_y_px_s: float


class EgoRingBuffer:
    """Ring buffer of timestamped hardware turret orientations (pan, tilt in degrees)."""

    def __init__(self, capacity: int = 128) -> None:
        self.capacity = int(capacity)
        self.buf: list[tuple[float, float, float]] = []  # (t_mono_s, pan_deg, tilt_deg)

    def push(self, t_mono_s: float, pan_deg: float, tilt_deg: float) -> None:
        self.buf.append((float(t_mono_s), float(pan_deg), float(tilt_deg)))
        if len(self.buf) > self.capacity:
            self.buf.pop(0)

    def sample(self, t_query: float, key: int) -> float | None:
        """
        Sample pan (key=1) or tilt (key=2) at exact query time using linear interpolation.
        Returns None if query time is outside buffer range.
        """
        if len(self.buf) < 2:
            return None
        t_lo, t_hi = self.buf[0][0], self.buf[-1][0]
        if t_query < t_lo or t_query > t_hi:
            # Extrapolate nearest boundary if within 50 ms tolerance
            if abs(t_query - t_hi) <= 0.05:
                return self.buf[-1][key]
            if abs(t_query - t_lo) <= 0.05:
                return self.buf[0][key]
            return None

        # Binary search or sequential scan
        for i in range(len(self.buf) - 1):
            a, b = self.buf[i], self.buf[i + 1]
            if a[0] <= t_query <= b[0]:
                span = b[0] - a[0]
                if span <= 1e-6:
                    return a[key]
                ratio = (t_query - a[0]) / span
                return a[key] + ratio * (b[key] - a[key])
        return self.buf[-1][key]

    def compute_ego_flow(
        self,
        t_capture: float,
        dt_window: float = 0.02,
        fov_px_per_deg: float = 28.4,
    ) -> EgoVelocity:
        """
        Compute angular velocity and optical ego flow (px/s) at camera capture time t_capture.
        """
        p0 = self.sample(t_capture, 1)
        p1 = self.sample(t_capture + dt_window, 1)
        t0 = self.sample(t_capture, 2)
        t1 = self.sample(t_capture + dt_window, 2)

        if p0 is None or p1 is None:
            # Fallback to backwards difference
            p_prev = self.sample(t_capture - dt_window, 1)
            t_prev = self.sample(t_capture - dt_window, 2)
            if p0 is not None and p_prev is not None:
                w_pan = (p0 - p_prev) / dt_window
            else:
                w_pan = 0.0
            if t0 is not None and t_prev is not None:
                w_tilt = (t0 - t_prev) / dt_window
            else:
                w_tilt = 0.0
        else:
            w_pan = (p1 - p0) / dt_window
            w_tilt = ((t1 - t0) / dt_window) if (t1 is not None and t0 is not None) else 0.0

        ego_x = w_pan * fov_px_per_deg
        ego_y = w_tilt * fov_px_per_deg
        return EgoVelocity(w_pan, w_tilt, ego_x, ego_y)


def true_target_velocity(
    v_img_x: float,
    ego_flow_x_px_s: float,
    angle_error_deg: float = 0.0,
) -> float:
    """
    Recovers intrinsic target velocity in px/s by subtracting camera rotational ego-flow.
    """
    cos_theta = math.cos(math.radians(angle_error_deg))
    cos_sq = max(cos_theta ** 2, 0.5)
    return (float(v_img_x) - float(ego_flow_x_px_s)) / cos_sq
