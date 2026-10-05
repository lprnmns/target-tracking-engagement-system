"""Persistent, operator-visible turret exclusion-zone profile."""

from __future__ import annotations

import time

from pydantic import BaseModel, Field, model_validator

from app.schemas.config import AngularSafetyZone


class SafetyZoneProfileUpdate(BaseModel):
    """The complete replacement profile; partial silent edits are avoided."""

    motion_zones: list[AngularSafetyZone] = Field(default_factory=list)
    fire_zones: list[AngularSafetyZone] = Field(default_factory=list)

    @model_validator(mode="after")
    def zone_names_are_unique_per_scope(self) -> "SafetyZoneProfileUpdate":
        for scope, zones in (("motion", self.motion_zones), ("fire", self.fire_zones)):
            names = [item.name for item in zones]
            if len(names) != len(set(names)):
                raise ValueError(f"duplicate {scope} safety-zone name")
        return self


class SafetyZoneProfile(SafetyZoneProfileUpdate):
    """Runtime profile plus its reproducible identity and provenance."""

    profile_hash: str
    source: str = "config_baseline"
    updated_at: float = Field(default_factory=time.time)
