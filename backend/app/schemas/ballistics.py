"""
Ballistics & Range Calibration Schemas — 5m / 10m / 15m İstasyonları ve Aşama 3 Mesafe Kestirimi.
"""

from __future__ import annotations

import time
from pydantic import BaseModel, Field


class BallisticsStation(BaseModel):
    """Tek bir mesafe istasyonuna ait kalibrasyon parametreleri."""

    distance_m: float
    offset_y_px: float = 0.0
    offset_x_px: float = 0.0
    flight_time_ms: float = 0.0
    reference_balloon_w_px: float = 28.0
    notes: str = ""


class BallisticsProfile(BaseModel):
    """Tüm mesafe istasyonları ve Aşama 3 yaklaşma modeli."""

    enabled: bool = False
    start_distance_m: float = 15.0
    approach_speed_m_s: float = 0.30
    enable_hybrid_vision: bool = True
    stations: dict[str, BallisticsStation] = Field(default_factory=dict)
    updated_at: float = Field(default_factory=time.time)


class BallisticsLiveState(BaseModel):
    """Anlık mesafe ve hesaplanan ofset durumu."""

    active: bool = False
    elapsed_s: float = 0.0
    estimated_distance_m: float = 15.0
    time_distance_m: float = 15.0
    vision_distance_m: float | None = None
    offset_x_px: float = 0.0
    offset_y_px: float = 0.0
    lead_x_px: float = 0.0
    total_offset_x_px: float = 0.0
    total_offset_y_px: float = 0.0
    flight_time_ms: float = 0.0
    station_nearest: float = 15.0
    updated_at: float = Field(default_factory=time.time)


class StationUpdateRequest(BaseModel):
    """Tek istasyon için ofset güncelleme isteği."""

    distance_m: float
    offset_x_px: float
    offset_y_px: float
    flight_time_ms: float | None = None
    reference_balloon_w_px: float | None = None
    notes: str | None = None


class Stage3SimulationRequest(BaseModel):
    """Aşama 3 yaklaşma zamanlayıcısını başlatma/durdurma isteği."""

    start_distance_m: float = 15.0
    approach_speed_m_s: float = 0.30
    enable_hybrid_vision: bool = True
