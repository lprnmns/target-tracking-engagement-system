"""Operator API for persistent, separated motion/fire exclusion sectors."""

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_runtime
from app.schemas.safety_zone import SafetyZoneProfile, SafetyZoneProfileUpdate
from app.services.runtime_state import RuntimeState


router = APIRouter(prefix="/api/safety-zones", tags=["safety-zones"])


@router.get("/profile", response_model=SafetyZoneProfile)
def get_safety_zone_profile(runtime: RuntimeState = Depends(get_runtime)) -> SafetyZoneProfile:
    return runtime.safety_zones.status()


@router.put("/profile", response_model=SafetyZoneProfile)
def replace_safety_zone_profile(
    update: SafetyZoneProfileUpdate,
    runtime: RuntimeState = Depends(get_runtime),
) -> SafetyZoneProfile:
    # The stage-3 competition profile is an immutable, evidenced run profile.
    if runtime.stage3_competition_profile_locked():
        raise HTTPException(status_code=409, detail="A3_PROFILE_LOCKED")
    try:
        profile = runtime.safety_zones.replace(update)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    # A sector edit can change what is physically permitted.  Stop outputs and
    # require the existing visible preflight/arm flow before any live command
    # can resume; this is not an additional approval path.
    runtime.command_gateway.invalidate_preflight(runtime, "SAFETY_ZONE_PROFILE_CHANGED")
    runtime.last_safety_event = ("safety.zone_profile_updated", profile.model_dump(mode="json"))
    return profile
