"""
Ballistics & Range Calibration API — /api/ballistics endpoints.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.api.deps import get_runtime
from app.schemas.ballistics import (
    BallisticsLiveState,
    BallisticsProfile,
    Stage3SimulationRequest,
    StationUpdateRequest,
)
from app.services.runtime_state import RuntimeState

router = APIRouter(prefix="/api/ballistics", tags=["ballistics"])


@router.get("/profile", response_model=BallisticsProfile)
def get_ballistics_profile(runtime: RuntimeState = Depends(get_runtime)) -> BallisticsProfile:
    """Mevcut balistik ve mesafe kalibrasyon profilini döner."""
    return runtime.ballistics.get_profile()


@router.put("/profile", response_model=BallisticsProfile)
def update_ballistics_profile(
    profile: BallisticsProfile,
    runtime: RuntimeState = Depends(get_runtime),
) -> BallisticsProfile:
    """Balistik profilini tamamen günceller."""
    return runtime.ballistics.update_profile(profile)


@router.post("/station", response_model=BallisticsProfile)
def update_station(
    request: StationUpdateRequest,
    runtime: RuntimeState = Depends(get_runtime),
) -> BallisticsProfile:
    """5m, 10m veya 15m istasyonunun ofsetini günceller ve kaydeder."""
    return runtime.ballistics.update_station(request)


@router.post("/stage3/start", response_model=BallisticsLiveState)
def start_stage3_simulation(
    request: Stage3SimulationRequest | None = None,
    runtime: RuntimeState = Depends(get_runtime),
) -> BallisticsLiveState:
    """Aşama 3 yaklaşma zamanlayıcısını başlatır (15m'den 5m'ye)."""
    start_d = request.start_distance_m if request else 15.0
    speed = request.approach_speed_m_s if request else 0.30
    return runtime.ballistics.start_stage3(start_distance_m=start_d, approach_speed_m_s=speed)


@router.post("/stage3/stop", response_model=BallisticsLiveState)
def stop_stage3_simulation(runtime: RuntimeState = Depends(get_runtime)) -> BallisticsLiveState:
    """Aşama 3 yaklaşma zamanlayıcısını durdurur."""
    return runtime.ballistics.stop_stage3()


@router.get("/live", response_model=BallisticsLiveState)
def get_live_state(
    balloon_w_px: float | None = Query(None, description="Gözlemlenen balon piksel genişliği"),
    target_vx_px_s: float = Query(0.0, description="Hedefin yatay hızı (piksel/saniye)"),
    runtime: RuntimeState = Depends(get_runtime),
) -> BallisticsLiveState:
    """Anlık hesaplanan mesafe ve enterpolasyon ofsetini döner."""
    return runtime.ballistics.get_live_state(
        observed_balloon_w_px=balloon_w_px,
        target_vx_px_s=target_vx_px_s,
    )
