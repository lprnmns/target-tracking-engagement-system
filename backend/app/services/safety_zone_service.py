"""Geometry and persistence for distinct turret motion/fire exclusion zones."""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

from app.schemas.config import AngularSafetyZone, AppConfig
from app.schemas.log import LogLevel
from app.schemas.safety_zone import SafetyZoneProfile, SafetyZoneProfileUpdate


def active_zone_name(zones: list[AngularSafetyZone], pan_deg: float, tilt_deg: float) -> str | None:
    """Return the first enabled exclusion sector containing turret state."""
    for zone in zones:
        if zone.enabled and zone.pan_min_deg <= pan_deg <= zone.pan_max_deg and zone.tilt_min_deg <= tilt_deg <= zone.tilt_max_deg:
            return zone.name
    return None


class SafetyZoneProfileService:
    """Own the active zone profile and mirror it into the canonical config.

    The YAML configuration remains the immutable baseline.  Field changes are
    persisted separately under ``config/runtime`` so an operator never has to
    edit source or an environment file, and the profile hash can be attached
    to a run record.
    """

    def __init__(self, config: AppConfig, logger, path: Path) -> None:
        self.config = config
        self.logger = logger
        self.path = path
        self._baseline = SafetyZoneProfileUpdate(
            motion_zones=list(config.motion.motion_forbidden_zones),
            fire_zones=list(config.decision.fire_forbidden_zones),
        )
        self._active = self._baseline
        self._source = "config_baseline"
        self._updated_at = time.time()
        self._load()

    def status(self) -> SafetyZoneProfile:
        payload = self._active.model_dump(mode="json")
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return SafetyZoneProfile(
            **payload,
            profile_hash=hashlib.sha256(encoded).hexdigest(),
            source=self._source,
            updated_at=self._updated_at,
        )

    def replace(self, update: SafetyZoneProfileUpdate) -> SafetyZoneProfile:
        self._validate_within_motion_limits(update)
        self._active = update
        self._source = "runtime_persisted"
        self._updated_at = time.time()
        self._apply_to_config(update)
        self._persist()
        profile = self.status()
        self.logger.emit(LogLevel.INFO, "SAFETY_ZONE", "Safety-zone profile updated", profile.model_dump(mode="json"))
        return profile

    def _validate_within_motion_limits(self, update: SafetyZoneProfileUpdate) -> None:
        motion = self.config.motion
        for zone in [*update.motion_zones, *update.fire_zones]:
            if (
                zone.pan_min_deg < motion.pan_min_deg
                or zone.pan_max_deg > motion.pan_max_deg
                or zone.tilt_min_deg < motion.tilt_min_deg
                or zone.tilt_max_deg > motion.tilt_max_deg
            ):
                raise ValueError(f"SAFETY_ZONE_OUTSIDE_SOFT_LIMITS:{zone.name}")

    def _apply_to_config(self, profile: SafetyZoneProfileUpdate) -> None:
        self.config.motion.motion_forbidden_zones = list(profile.motion_zones)
        self.config.decision.fire_forbidden_zones = list(profile.fire_zones)
        # A non-empty fire sector must be evaluated by DecisionEngine as well
        # as by CommandGateway.  Empty profiles keep the explicit config value.
        if profile.fire_zones:
            self.config.decision.forbidden_zone_check_enabled = True

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            candidate = SafetyZoneProfileUpdate.model_validate(raw)
            self._validate_within_motion_limits(candidate)
            self._active = candidate
            self._source = "runtime_persisted"
            self._updated_at = float(raw.get("updated_at", time.time())) if isinstance(raw, dict) else time.time()
            self._apply_to_config(candidate)
        except (OSError, ValueError, TypeError):
            # An invalid persisted profile may never silently widen authority:
            # retain the reviewed YAML baseline and leave no live side effect.
            self._active = self._baseline
            self._source = "config_baseline"
            self._apply_to_config(self._baseline)

    def _persist(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(self.status().model_dump_json(indent=2), encoding="utf-8")


class ExclusionZoneManager:
    """
    Exclusion / Masked ROI Zone Manager for Turret Safety.
    Prevents fire trigger when camera points towards restricted coordinates or targets enter forbidden areas.

    Design: Angular zones are the authoritative hard interlock (turret physical position).
    Screen-space polygons are an optical layer for masking specific pixel regions.
    Trajectory lookahead samples points along the predicted velocity vector to prevent fast targets
    from jumping through thin zones, and release debounce prevents boundary chatter.
    """

    LOOKAHEAD_S: float = 0.25         # Predictive horizon for moving targets (seconds)
    PATH_SAMPLES: int = 4             # Intermediate sample points along path
    RELEASE_DEBOUNCE_S: float = 0.50  # Debounce hold duration upon exiting a zone

    def __init__(self) -> None:
        # list of (pan_min, pan_max, tilt_min, tilt_max)
        self.angular_zones: list[tuple[float, float, float, float]] = []
        # list of polygons: list of [(x1, y1), (x2, y2), ...]
        self.screen_zones: list[list[tuple[float, float]]] = []
        self._blocked_until: float = 0.0
        self._last_block_reason: str | None = None

    def update_from_angular_zones(self, zones: list[AngularSafetyZone]) -> None:
        """Syncs active angular zones from configuration or safety profile."""
        self.angular_zones = [
            (z.pan_min_deg, z.pan_max_deg, z.tilt_min_deg, z.tilt_max_deg)
            for z in zones if z.enabled
        ]

    def set_screen_zones(self, polygons: list[list[tuple[float, float]]]) -> None:
        """Sets screen-space exclusion polygons."""
        self.screen_zones = list(polygons)

    @staticmethod
    def _point_in_polygon(x: float, y: float, polygon: list[tuple[float, float]]) -> bool:
        """Standard even-odd ray-casting point-in-polygon test."""
        inside = False
        n = len(polygon)
        if n < 3:
            return False
        for i in range(n):
            x1, y1 = polygon[i]
            x2, y2 = polygon[(i + 1) % n]
            if (y1 > y) != (y2 > y):
                x_intersect = (x2 - x1) * (y - y1) / (y2 - y1 + 1e-9) + x1
                if x < x_intersect:
                    inside = not inside
        return inside

    def _angular_hit(self, pan_deg: float, tilt_deg: float) -> bool:
        # Support both physical (0-270 Pan, 0-60 Tilt) and relative (-60..60 Pan, -30..30 Tilt) coordinate frames
        rel_pan = pan_deg - 135.0 if pan_deg > 65.0 else pan_deg
        phys_pan = pan_deg if pan_deg > 65.0 else pan_deg + 135.0

        rel_tilt = 30.0 - tilt_deg if tilt_deg > 0.0 else tilt_deg
        phys_tilt = tilt_deg if tilt_deg >= 0.0 else 30.0 - tilt_deg

        for (p_min, p_max, t_min, t_max) in self.angular_zones:
            if (p_min <= phys_pan <= p_max and t_min <= phys_tilt <= t_max) or \
               (p_min <= rel_pan <= p_max and t_min <= rel_tilt <= t_max):
                return True
        return False

    def _screen_hit(self, x_px: float, y_px: float) -> bool:
        return any(self._point_in_polygon(x_px, y_px, poly) for poly in self.screen_zones)

    def is_trigger_allowed(
        self,
        pan_deg: float,
        tilt_deg: float,
        target_x_px: float,
        target_y_px: float,
        vx_px_s: float = 0.0,
        vy_px_s: float = 0.0,
        now: float | None = None,
    ) -> bool:
        """
        Returns False (firing blocked) if:
          1. Current turret Pan/Tilt is inside an angular exclusion zone (hard interlock);
          2. Target's current or projected screen position (over LOOKAHEAD_S) hits a screen zone;
          3. A zone was exited less than RELEASE_DEBOUNCE_S ago (prevents boundary chatter).
        """
        now = now if now is not None else time.time()

        if self._angular_hit(pan_deg, tilt_deg):
            self._blocked_until = now + self.RELEASE_DEBOUNCE_S
            self._last_block_reason = "ANGULAR_ZONE_HIT"
            return False

        if self.screen_zones:
            for i in range(self.PATH_SAMPLES + 1):
                t = self.LOOKAHEAD_S * i / self.PATH_SAMPLES
                px = target_x_px + vx_px_s * t
                py = target_y_px + vy_px_s * t
                if self._screen_hit(px, py):
                    self._blocked_until = now + self.RELEASE_DEBOUNCE_S
                    self._last_block_reason = f"SCREEN_ZONE_HIT_T{i}"
                    return False

        if now < self._blocked_until:
            return False

        self._last_block_reason = None
        return True
