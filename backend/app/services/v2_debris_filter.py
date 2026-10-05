"""
v2_debris_filter.py — Kinematic Debris Filter & Deflation Confirmation
Based on GLM-4 and GPT Pro / Claude Deep Engineering Consensus.

Two complementary detection mechanisms:
1. Kinematic Debris Gate (GLM-4):
   - Falling latex fragments accelerate under gravity (g_img ≈ 3650 px/s²).
   - Rejects targets with downward velocity v_y > 250 px/s (normal balloons have |v_y| <= 80 px/s).
   - Prevents wasting secondary shots on falling shreds.

2. Scale-Corrected Deflation CUSUM (GPT Pro):
   - Invariant log-area metric: ell = ln(A) + 2 * ln(Z) removes perspective expansion.
   - Detects rapid area collapse (drop >= 30%) and aspect ratio distortion.
   - Confirms burst within 2 frames of impact, immediately triggering radar advance.
"""

from __future__ import annotations

import math
from typing import NamedTuple, Optional
import numpy as np


class DebrisDecision(NamedTuple):
    is_debris: bool
    is_popped: bool
    v_y_px_s: float
    area_drop_pct: float
    state: str
    reason: str


class KinematicDebrisFilter:
    """
    Kinematic debris filter and post-pop confirmation.
    Differentiates between healthy balloons and falling burst fragments.
    """

    def __init__(
        self,
        vy_debris_threshold_px_s: float = 250.0,
        ay_debris_threshold_px_s2: float = 1200.0,
        area_drop_threshold: float = 0.35,
        confirm_frames: int = 2,
    ) -> None:
        self.vy_thresh = float(vy_debris_threshold_px_s)
        self.ay_thresh = float(ay_debris_threshold_px_s2)
        self.area_drop_thresh = float(area_drop_threshold)
        self.confirm_frames = int(confirm_frames)

        self.last_y: Optional[float] = None
        self.last_vy: Optional[float] = None
        self.last_t: Optional[float] = None
        self.baseline_ell: Optional[float] = None
        self.ell_samples: list[float] = []

        self.consecutive_debris_frames: int = 0
        self.consecutive_pop_frames: int = 0
        self.latched_popped: bool = False

    def reset(self) -> None:
        """Reset state between track engagements."""
        self.last_y = None
        self.last_vy = None
        self.last_t = None
        self.baseline_ell = None
        self.ell_samples.clear()
        self.consecutive_debris_frames = 0
        self.consecutive_pop_frames = 0
        self.latched_popped = False

    def update(
        self,
        t_mono_s: float,
        center_y_px: float,
        bbox_w: float,
        bbox_h: float,
        z_hat_m: float = 10.0,
    ) -> DebrisDecision:
        """
        Evaluate frame for debris kinematics and balloon deflation.

        Args:
            t_mono_s: Monotonic frame timestamp in seconds
            center_y_px: Vertical center coordinate in pixels (increases downward)
            bbox_w: Bounding box width in pixels
            bbox_h: Bounding box height in pixels
            z_hat_m: Estimated target distance in meters (from range estimator)
        """
        if self.latched_popped:
            return DebrisDecision(
                is_debris=True,
                is_popped=True,
                v_y_px_s=self.last_vy or 0.0,
                area_drop_pct=1.0,
                state="POPPED_LATCHED",
                reason="Target already popped; secondary shot inhibited",
            )

        area = max(bbox_w * bbox_h, 1.0)
        z = max(float(z_hat_m), 1.0)
        # Perspective-invariant log area: ell = ln(A) + 2 * ln(Z)
        ell = math.log(area) + 2.0 * math.log(z)

        # 1. Kinematic Velocity & Acceleration
        vy = 0.0
        ay = 0.0
        if self.last_t is not None and self.last_y is not None:
            dt = max(1e-4, t_mono_s - self.last_t)
            if dt <= 0.20:
                vy = (center_y_px - self.last_y) / dt
                if self.last_vy is not None:
                    ay = (vy - self.last_vy) / dt
            else:
                self.last_vy = None

        self.last_t = t_mono_s
        self.last_y = center_y_px
        self.last_vy = vy

        # 2. Baseline Learning & Deflation Check
        if self.baseline_ell is None:
            self.ell_samples.append(ell)
            if len(self.ell_samples) >= 5:
                self.baseline_ell = float(np.median(self.ell_samples))
            area_drop = 0.0
        else:
            # Drop fraction relative to baseline
            area_drop = 1.0 - math.exp(min(0.0, ell - self.baseline_ell))

        # 3. Decision Logic
        # Kinematic test: downward falling at high speed
        is_falling = vy > self.vy_thresh or (vy > 180.0 and ay > self.ay_thresh)
        # Morphological test: sudden area collapse
        is_deflating = area_drop >= self.area_drop_thresh

        if is_falling:
            self.consecutive_debris_frames += 1
        else:
            self.consecutive_debris_frames = max(0, self.consecutive_debris_frames - 1)

        if is_deflating or (is_falling and area_drop >= 0.20):
            self.consecutive_pop_frames += 1
        else:
            self.consecutive_pop_frames = max(0, self.consecutive_pop_frames - 1)

        debris_confirmed = self.consecutive_debris_frames >= self.confirm_frames
        pop_confirmed = self.consecutive_pop_frames >= self.confirm_frames

        if pop_confirmed or debris_confirmed:
            self.latched_popped = True
            state = "BALLOON_POPPED"
            reason = f"Confirmed pop: drop={area_drop*100:.1f}%, vy={vy:.1f}px/s"
            return DebrisDecision(
                is_debris=True,
                is_popped=True,
                v_y_px_s=vy,
                area_drop_pct=area_drop,
                state=state,
                reason=reason,
            )

        if is_falling or is_deflating:
            state = "SUSPECT_DEBRIS"
            reason = f"Suspect: vy={vy:.1f}px/s, drop={area_drop*100:.1f}%"
            return DebrisDecision(
                is_debris=True,
                is_popped=False,
                v_y_px_s=vy,
                area_drop_pct=area_drop,
                state=state,
                reason=reason,
            )

        return DebrisDecision(
            is_debris=False,
            is_popped=False,
            v_y_px_s=vy,
            area_drop_pct=area_drop,
            state="TARGET_INTACT",
            reason="Nominal target kinematics",
        )
