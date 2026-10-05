"""
Ballistics Calibration Service — 15m / 10m / 5m İstasyonları,
Boresight & Ballistik Dikey Ofset Enterpolasyonu ve Aşama 3 Yaklaşma Kestirimi.
"""

from __future__ import annotations

import json
import logging
import math
import time
from pathlib import Path
from typing import Any

from app.services import yarisma_bayraklari as yb
from app.schemas.ballistics import (
    BallisticsLiveState,
    BallisticsProfile,
    BallisticsStation,
    StationUpdateRequest,
)

logger = logging.getLogger(__name__)

DEFAULT_PATH = Path("config/runtime/ballistics_calibration.json")


def _default_profile() -> BallisticsProfile:
    return BallisticsProfile(
        enabled=False,
        start_distance_m=15.0,
        approach_speed_m_s=0.30,
        enable_hybrid_vision=True,
        stations={
            "15": BallisticsStation(
                distance_m=15.0,
                offset_y_px=0.0,
                offset_x_px=0.0,
                flight_time_ms=214.0,
                reference_balloon_w_px=28.0,
                notes="15m Dalgalı Ray Başlangıç Noktası",
            ),
            "10": BallisticsStation(
                distance_m=10.0,
                offset_y_px=0.0,
                offset_x_px=0.0,
                flight_time_ms=142.0,
                reference_balloon_w_px=42.0,
                notes="10m Orta Ray İstasyonu",
            ),
            "5": BallisticsStation(
                distance_m=5.0,
                offset_y_px=0.0,
                offset_x_px=0.0,
                flight_time_ms=71.0,
                reference_balloon_w_px=84.0,
                notes="5m Yakın Ray İstasyonu",
            ),
        },
    )


class BallisticsCalibrationService:
    def __init__(self, persistence_path: Path | None = None) -> None:
        self.path = persistence_path or DEFAULT_PATH
        self._profile = _default_profile()
        self._stage3_active = False
        self._stage3_started_at: float | None = None
        self._load()

    def get_profile(self) -> BallisticsProfile:
        return self._profile

    def update_profile(self, profile: BallisticsProfile) -> BallisticsProfile:
        self._profile = profile
        self._profile.updated_at = time.time()
        self._save()
        return self._profile

    def update_station(self, update: StationUpdateRequest) -> BallisticsProfile:
        key = str(int(round(update.distance_m)))
        station = self._profile.stations.get(key)
        if station is None:
            station = BallisticsStation(distance_m=update.distance_m)
        station.offset_x_px = update.offset_x_px
        station.offset_y_px = update.offset_y_px
        if update.flight_time_ms is not None:
            station.flight_time_ms = max(0.0, update.flight_time_ms)
        if update.reference_balloon_w_px is not None:
            station.reference_balloon_w_px = max(1.0, update.reference_balloon_w_px)
        if update.notes is not None:
            station.notes = update.notes

        self._profile.stations[key] = station
        self._profile.updated_at = time.time()
        self._save()
        return self._profile

    def start_stage3(self, start_distance_m: float = 15.0, approach_speed_m_s: float = 0.30) -> BallisticsLiveState:
        self._stage3_active = True
        self._stage3_started_at = time.monotonic()
        self._profile.start_distance_m = max(5.0, min(20.0, start_distance_m))
        self._profile.approach_speed_m_s = max(0.05, min(2.0, approach_speed_m_s))
        return self.get_live_state()

    def stop_stage3(self) -> BallisticsLiveState:
        self._stage3_active = False
        self._stage3_started_at = None
        return self.get_live_state()

    def interpolate(self, distance_m: float, target_vx_px_s: float = 0.0) -> dict[str, float]:
        """
        Piecewise linear interpolation between 5m, 10m, and 15m stations.
        Runtime: < 1 microsecond (0.0005 ms).
        """
        d = max(4.0, min(16.0, float(distance_m)))
        s15 = self._profile.stations.get("15") or BallisticsStation(distance_m=15.0, offset_y_px=-40.0, flight_time_ms=214.0)
        s10 = self._profile.stations.get("10") or BallisticsStation(distance_m=10.0, offset_y_px=-20.0, flight_time_ms=142.0)
        s5 = self._profile.stations.get("5") or BallisticsStation(distance_m=5.0, offset_y_px=-5.0, flight_time_ms=71.0)

        if d >= 10.0:
            ratio = (d - 10.0) / 5.0
            ratio = max(0.0, min(1.2, ratio))
            offset_y = s10.offset_y_px + ratio * (s15.offset_y_px - s10.offset_y_px)
            offset_x = s10.offset_x_px + ratio * (s15.offset_x_px - s10.offset_x_px)
            flight_time = s10.flight_time_ms + ratio * (s15.flight_time_ms - s10.flight_time_ms)
            nearest = 15.0 if ratio > 0.5 else 10.0
        else:
            ratio = (d - 5.0) / 5.0
            ratio = max(-0.2, min(1.0, ratio))
            offset_y = s5.offset_y_px + ratio * (s10.offset_y_px - s5.offset_y_px)
            offset_x = s5.offset_x_px + ratio * (s10.offset_x_px - s5.offset_x_px)
            flight_time = s5.flight_time_ms + ratio * (s10.flight_time_ms - s5.flight_time_ms)
            nearest = 10.0 if ratio > 0.5 else 5.0

        # Lateral flight time lead compensation: x_lead = v_x * (t_flight / 1000)
        # Positive v_x means target moving right, so we must aim ahead to the right (+x).
        lead_x = target_vx_px_s * (flight_time / 1000.0)
        # Saturated to reasonable balloon bounding box fraction
        lead_x = max(-60.0, min(60.0, lead_x))

        return {
            "offset_x_px": round(offset_x, 2),
            "offset_y_px": round(offset_y, 2),
            "lead_x_px": round(lead_x, 2),
            "total_offset_x_px": round(offset_x + lead_x, 2),
            "total_offset_y_px": round(offset_y, 2),
            "flight_time_ms": round(flight_time, 1),
            "station_nearest": nearest,
        }

    def estimate_distance(
        self,
        elapsed_s: float,
        observed_balloon_w_px: float | None = None,
    ) -> tuple[float, float, float | None]:
        """
        Computes (estimated_distance, time_distance, vision_distance).
        Combines deterministic kinematic progression with optical bounding box scale.
        """
        start_d = self._profile.start_distance_m
        speed = self._profile.approach_speed_m_s
        time_d = max(4.5, min(16.0, start_d - speed * max(0.0, elapsed_s)))

        vision_d: float | None = None
        if observed_balloon_w_px is not None and observed_balloon_w_px > 4.0:
            # At 15m, 14cm is ~28px -> k = 15.0 * 28.0 = 420.0
            # At 5m, 14cm is ~84px -> 420 / 84 = 5.0m
            k = 420.0
            raw_v = k / observed_balloon_w_px
            vision_d = max(4.0, min(17.0, raw_v))

        if self._profile.enable_hybrid_vision and vision_d is not None:
            # 70% kinematic time prior + 30% vision correction
            # If vision is close to time model, blend smoothly
            diff = abs(vision_d - time_d)
            if diff < 4.0:
                est_d = 0.70 * time_d + 0.30 * vision_d
            else:
                est_d = time_d
        else:
            est_d = time_d

        return round(est_d, 2), round(time_d, 2), round(vision_d, 2) if vision_d is not None else None

    def get_live_state(
        self,
        observed_balloon_w_px: float | None = None,
        target_vx_px_s: float = 0.0,
    ) -> BallisticsLiveState:
        elapsed = 0.0
        if self._stage3_active and self._stage3_started_at is not None:
            elapsed = max(0.0, time.monotonic() - self._stage3_started_at)

        est_d, time_d, vis_d = self.estimate_distance(elapsed, observed_balloon_w_px)
        interp = self.interpolate(est_d, target_vx_px_s)
        if not self._profile.enabled or yb.Y10_BALISTIK_KAPALI:  # Y10: nisangah yalniz kayitli ofset
            interp["offset_x_px"] = 0.0
            interp["offset_y_px"] = 0.0
            interp["lead_x_px"] = 0.0
            interp["total_offset_x_px"] = 0.0
            interp["total_offset_y_px"] = 0.0

        return BallisticsLiveState(
            active=self._stage3_active,
            elapsed_s=round(elapsed, 2),
            estimated_distance_m=est_d,
            time_distance_m=time_d,
            vision_distance_m=vis_d,
            offset_x_px=interp["offset_x_px"],
            offset_y_px=interp["offset_y_px"],
            lead_x_px=interp["lead_x_px"],
            total_offset_x_px=interp["total_offset_x_px"],
            total_offset_y_px=interp["total_offset_y_px"],
            flight_time_ms=interp["flight_time_ms"],
            station_nearest=interp["station_nearest"],
            updated_at=time.time(),
        )

    def _load(self) -> None:
        if not self.path.is_file():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self._profile = BallisticsProfile.model_validate(data)
            logger.info("Loaded ballistics profile from %s", self.path)
        except Exception as err:
            logger.warning("Could not parse ballistics profile: %s. Using default.", err)

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(self._profile.model_dump_json(indent=2), encoding="utf-8")
        except Exception as err:
            logger.error("Failed to save ballistics profile: %s", err)
