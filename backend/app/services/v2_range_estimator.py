"""
v2_range_estimator.py — Stage 3 Range Estimators & Ballistics Drop
Consensus of GLM-4, Claude 3.5 Sonnet, and GPT Pro.

Provides two estimators:
1. Stage3ModelRange:
   - Inverse-width (u = 1/W) model filter with Jensen bias correction and outlier gate.
2. MonocularDepthEKF:
   - Full 2-state [Z, v_Z] continuous white acceleration EKF with Joseph-form covariance update
     and 4-sigma normalized innovation gate (NIS <= 16.0).
3. aim_offset_y & flight_time_s:
   - Physical vertical drop compensation: dy(Z) = -2.2 * Z + 11.0 px.
"""

from __future__ import annotations

import math
from typing import Optional
import numpy as np


class Stage3ModelRange:
    """Model-based range filter in u = 1/W inverse-width space."""

    def __init__(
        self,
        k_optic_px_m: float = 417.0,
        z_start_m: float = 15.0,
        v_approach_m_s: float = 0.30,
        alpha: float = 0.15,
        sigma_w_px: float = 2.0,
        gate_gamma: float = 3.0,
    ) -> None:
        self.K = float(k_optic_px_m)
        self.u = float(z_start_m) / self.K
        self.v = -float(v_approach_m_s) / self.K
        self.alpha = float(alpha)
        self.sigma_w = float(sigma_w_px)
        self.gamma = float(gate_gamma)
        self.rejected_count: int = 0
        self.total_frames: int = 0

    def init_state(self, z_start_m: float, v_approach_m_s: float = 0.30) -> None:
        self.u = float(z_start_m) / self.K
        self.v = -float(v_approach_m_s) / self.K
        self.rejected_count = 0
        self.total_frames = 0

    def update(self, w_px: float, dt: float) -> float:
        """
        Update range filter with measured bounding-box width W (pixels) and timestep dt (s).
        Returns estimated distance Z in meters.
        """
        self.total_frames += 1
        dt = max(0.001, min(0.10, float(dt)))

        # Free-run model prediction
        u_pred = self.u + self.v * dt
        w_pred = 1.0 / max(u_pred, 1e-6)

        # Handle invalid or collapsed bounding box
        if w_px < 5.0:
            self.rejected_count += 1
            self.u = u_pred
            return self.K * self.u

        # Occlusion / outlier gate against predicted width
        if abs(w_px - w_pred) > self.gamma * self.sigma_w:
            self.rejected_count += 1
            self.u = u_pred
            return self.K * self.u

        # Jensen bias correction: E[1/W] correction for 1/x nonlinearity
        jensen_factor = max(0.90, 1.0 - (self.sigma_w / max(w_px, 1.0)) ** 2)
        z_u = (1.0 / w_px) * jensen_factor

        # Alpha measurement update
        self.u = u_pred + self.alpha * (z_u - u_pred)
        return self.K * self.u

    @property
    def estimated_z_m(self) -> float:
        return self.K * self.u

    @property
    def estimated_width_px(self) -> float:
        return 1.0 / max(self.u, 1e-6)


class MonocularDepthEKF:
    """
    2-state Extended Kalman Filter [Z, v_Z] for optical-axis depth tracking.
    Uses Joseph-form covariance update and normalized innovation squared (NIS) gate.
    """

    def __init__(
        self,
        fx: float = 1100.0,
        diameter: float = 0.20,
        z0: float = 15.0,
        v0: float = -0.30,
        sigma_z0: float = 1.0,
        sigma_v0: float = 0.10,
        sigma_width: float = 3.5,
        q_accel: float = 1e-3,
        z_min: float = 0.5,
        nis_gate: float = 16.0,
    ) -> None:
        self.c = float(fx * diameter)
        self.x = np.array([float(z0), float(v0)], dtype=float)
        self.P = np.diag([sigma_z0**2, sigma_v0**2])
        self.sigma_width = float(sigma_width)
        self.q_accel = float(q_accel)
        self.z_min = float(z_min)
        self.nis_gate = float(nis_gate)
        self.last_nis = math.nan
        self.status = "initialized"

    def predict(self, dt: float) -> np.ndarray:
        dt = max(0.001, min(0.20, float(dt)))
        F = np.array([[1.0, dt], [0.0, 1.0]])
        Q = self.q_accel * np.array([
            [dt**3 / 3.0, dt**2 / 2.0],
            [dt**2 / 2.0, dt]
        ])
        x_new = F @ self.x
        P_new = F @ self.P @ F.T + Q
        if not np.all(np.isfinite(x_new)) or x_new[0] <= self.z_min or not np.all(np.isfinite(P_new)):
            self.status = "invalid_prediction"
            return self.x.copy()

        self.x = x_new
        self.P = 0.5 * (P_new + P_new.T)
        self.status = "predicted"
        return self.x.copy()

    def update(self, w_meas: Optional[float], sigma_width: Optional[float] = None) -> bool:
        self.last_nis = math.nan
        if w_meas is None or not math.isfinite(w_meas) or w_meas <= 0:
            self.status = "invalid_measurement"
            return False

        sigma = self.sigma_width if sigma_width is None else float(sigma_width)
        z = self.x[0]
        if z <= self.z_min:
            return False

        H = np.array([-self.c / (z**2), 0.0])
        innovation = float(w_meas - self.c / z)
        R = sigma**2
        PH = self.P @ H
        S = float(H @ PH + R)
        if not math.isfinite(S) or S <= 0:
            return False

        self.last_nis = float((innovation**2) / S)
        if not math.isfinite(self.last_nis) or self.last_nis > self.nis_gate:
            self.status = "innovation_rejected"
            return False

        K = PH / S
        x_new = self.x + K * innovation
        if not np.all(np.isfinite(x_new)) or x_new[0] <= self.z_min:
            self.status = "nonphysical_update_rejected"
            return False

        # Joseph-form covariance update guarantees positive-definiteness
        M = np.eye(2) - np.outer(K, H)
        P_new = M @ self.P @ M.T + R * np.outer(K, K)
        if not np.all(np.isfinite(P_new)):
            return False

        self.x = x_new
        self.P = 0.5 * (P_new + P_new.T)
        self.status = "updated"
        return True

    @property
    def estimated_z_m(self) -> float:
        return float(self.x[0])

    @property
    def estimated_vz_m_s(self) -> float:
        return float(self.x[1])


def aim_offset_y(
    z_hat_m: float,
    a_px_per_m: float = -2.0,
    b_px: float = 10.0,
) -> float:
    """
    Physical vertical aim offset curve in pixels (negative = aim higher to compensate gravity).
    At 15m: -2.0 * 15 + 10 = -20.0 px
    At 10m: -2.0 * 10 + 10 = -10.0 px
    At 5m:  -2.0 * 5  + 10 =   0.0 px
    """
    return a_px_per_m * float(z_hat_m) + b_px


def flight_time_s(z_hat_m: float, v0_m_s: float = 70.0) -> float:
    """Estimated pellet flight time from muzzle velocity (default 70 m/s)."""
    return max(0.01, float(z_hat_m) / max(v0_m_s, 1.0))
