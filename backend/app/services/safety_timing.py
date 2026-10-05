"""Shared fail-closed timing limits for perception-driven safety decisions."""

# A perception event older than this cannot authorize movement or fire.
# The value is deliberately shorter than the browser upload cadence observed
# during laptop-camera tests; external frames are advisory-only unless fresh.
MAX_VISION_EVENT_AGE_S = 0.5
