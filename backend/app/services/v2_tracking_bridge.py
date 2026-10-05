"""
v2_tracking_bridge.py — Unified V2 Tracking & Engagement Bridge
Coordinates the 4 GLM-4 / Claude / GPT Pro polish modules:
1. FireOpportunityGate (56 ms predictive dynamic fire)
2. Stage3ModelRange & MonocularDepthEKF (u = 1/W and continuous white acceleration depth filter)
3. EgoRingBuffer (true target velocity decoupling)
4. KinematicDebrisFilter (pop confirmation & falling latex suppression)
"""

from __future__ import annotations

import time
from typing import NamedTuple, Optional

from app.services.v2_fire_opportunity import FireOpportunityGate, FOFDecision
from app.services.v2_range_estimator import Stage3ModelRange, MonocularDepthEKF, aim_offset_y, flight_time_s
from app.services.v2_ego_resolver import EgoRingBuffer, EgoVelocity, true_target_velocity
from app.services.v2_debris_filter import KinematicDebrisFilter, DebrisDecision


class V2EvaluationResult(NamedTuple):
    fire_decision: FOFDecision
    debris_decision: DebrisDecision
    estimated_z_m: float
    aim_offset_y_px: float
    true_target_vx_px_s: float
    ego_flow: EgoVelocity


class V2TrackingBridge:
    """Unified bridge for V2 tracking, ranging, ego-motion, and fire decision logic."""

    def __init__(
        self,
        enabled: bool = False,
        k_optic_px_m: float = 417.0,
        z_start_m: float = 15.0,
        v_approach_m_s: float = 0.30,
        fov_px_per_deg: float = 28.4,
    ) -> None:
        self.enabled = bool(enabled)
        self.fov_px_per_deg = float(fov_px_per_deg)

        self.fof_gate = FireOpportunityGate(min_frames=4, kappa=2.0, t_hold_s=0.30)
        self.range_estimator = Stage3ModelRange(
            k_optic_px_m=k_optic_px_m,
            z_start_m=z_start_m,
            v_approach_m_s=v_approach_m_s,
        )
        self.depth_ekf = MonocularDepthEKF(
            fx=1100.0,
            diameter=0.20,
            z0=z_start_m,
            v0=-v_approach_m_s,
        )
        self.ego_buffer = EgoRingBuffer(capacity=128)
        self.debris_filter = KinematicDebrisFilter(
            vy_debris_threshold_px_s=250.0,
            area_drop_threshold=0.35,
            confirm_frames=2,
        )
        self.last_t: Optional[float] = None

    def reset(self) -> None:
        """Reset internal filter states for new engagement."""
        self.fof_gate.reset()
        self.range_estimator.init_state(z_start_m=15.0, v_approach_m_s=0.30)
        self.debris_filter.reset()
        self.last_t = None

    def record_turret_pose(self, t_mono_s: float, pan_deg: float, tilt_deg: float) -> None:
        """Push hardware turret telemetry into ego buffer."""
        self.ego_buffer.push(t_mono_s, pan_deg, tilt_deg)

    def evaluate_frame(
        self,
        t_mono_s: float,
        target_cx: float,
        target_cy: float,
        crosshair_cx: float,
        crosshair_cy: float,
        bbox_w: float,
        bbox_h: float,
        v_img_x: float,
        v_img_y: float,
        tol_px: float,
        fresh: bool = True,
    ) -> V2EvaluationResult:
        """
        Execute full V2 evaluation pipeline on current video frame.
        """
        now = float(t_mono_s)
        dt = (now - self.last_t) if self.last_t is not None else 0.033
        dt = max(0.001, min(0.20, dt))
        self.last_t = now

        # 1. Ego-motion angular velocity & flow
        ego_flow = self.ego_buffer.compute_ego_flow(
            t_capture=now,
            dt_window=0.02,
            fov_px_per_deg=self.fov_px_per_deg,
        )

        # 2. Intrinsic true target horizontal velocity
        true_vx = true_target_velocity(v_img_x, ego_flow.ego_flow_x_px_s)

        # 3. Model Range & Ballistic Offset
        z_hat = self.range_estimator.update(w_px=bbox_w, dt=dt)
        # Predict & update secondary EKF
        try:
            self.depth_ekf.predict(dt)
            self.depth_ekf.update(bbox_w)
        except Exception:
            pass

        dyn_offset_y = aim_offset_y(z_hat)

        # 4. Debris / Pop Check
        debris_dec = self.debris_filter.update(
            t_mono_s=now,
            center_y_px=target_cy,
            bbox_w=bbox_w,
            bbox_h=bbox_h,
            z_hat_m=z_hat,
        )

        # 5. Dynamic Fire Opportunity Gate
        e_x = target_cx - crosshair_cx
        e_y = (target_cy + dyn_offset_y) - crosshair_cy

        # Inhibit fire gate if target popped or is falling debris
        if debris_dec.is_debris or debris_dec.is_popped:
            self.fof_gate.reset()
            fire_dec = FOFDecision(
                should_fire=False,
                consecutive_frames=0,
                image_speed_px_s=0.0,
                threshold_eps_px_s=0.0,
                error_px=math.hypot(e_x, e_y),
                reason="DEBRIS_OR_POPPED_INHIBITED",
            )
        else:
            fire_dec = self.fof_gate.update(
                e_x=e_x,
                e_y=e_y,
                v_img_x=v_img_x,
                v_img_y=v_img_y,
                tol_px=tol_px,
                fresh=fresh,
            )

        return V2EvaluationResult(
            fire_decision=fire_dec,
            debris_decision=debris_dec,
            estimated_z_m=z_hat,
            aim_offset_y_px=dyn_offset_y,
            true_target_vx_px_s=true_vx,
            ego_flow=ego_flow,
        )
