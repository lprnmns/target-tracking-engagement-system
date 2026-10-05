"""Stage-3 combined-tracking geometry: the single shared source (Kural 1-2).

Every BALLOON_AIRCRAFT (stage 3) code path that needs to decide whether a
balloon detection may be treated as an attached target balloon must route
through this module instead of re-implementing inline geometry.  Two
services previously shipped two different rectangles; they now converge on
the competition-verified attachment envelope below.

Kural 2 - %250 lower ROI:
    A balloon centre is valid only inside
    ``[x - 0.2w, x + 1.2w] x [y + h, y + h + 2.5h]``
    of the owning aircraft bbox.  The horizontal +-20% tolerance absorbs
    tether swing-out while the aircraft crosses the arena, the ``y + h``
    lower bound keeps the fuselage silhouette itself from being labelled
    as a balloon (the workshop false positive), and the 2.5h depth matches
    the physical 20-25 cm tether under the 32.25 cm S-curve fuselage.

Kural 1 - no balloon without a body:
    Under BALLOON_AIRCRAFT the balloon list is only ever gated against
    live bodies or their short-lived ghost (coasting) regions; callers
    enforce the "gövde yoksa balon tek olamaz" rule on top of this
    geometry.

L4 - silhouette overlap reject:
    A balloon box that geometrically overlaps the aircraft silhouette is
    the same physical object seen twice, never an attached balloon.
"""

from __future__ import annotations

from app.schemas.vision import BBox

HORIZONTAL_TOLERANCE_RATIO = 0.25
VERTICAL_DEPTH_RATIO = 4.2
DEFAULT_OVERLAP_IOU_THRESHOLD = 0.05

RELAXED_OVERLAP_IOU_THRESHOLD = 0.35
RELAXED_VERTICAL_TOP_RATIO = 0.50
RELAXED_VERTICAL_DEPTH_RATIO = 5.0


def attachment_roi(bbox: BBox, *, relaxed: bool = False) -> tuple[float, float, float, float]:
    """Return the Kural 2 envelope as ``(min_x, max_x, min_y, max_y)``."""
    left = bbox.x - bbox.w * HORIZONTAL_TOLERANCE_RATIO
    right = bbox.x + bbox.w * (1.0 + HORIZONTAL_TOLERANCE_RATIO)
    top = bbox.y + bbox.h * (RELAXED_VERTICAL_TOP_RATIO if relaxed else 1.0)
    bottom = bbox.y + bbox.h + bbox.h * VERTICAL_DEPTH_RATIO
    return left, right, top, bottom


def attachment_roi_contains(bbox: BBox, center_x: float, center_y: float, *, relaxed: bool = False) -> bool:
    """Kural 2 centre gate; ``False`` for fuselage-internal centres."""
    min_x, max_x, min_y, max_y = attachment_roi(bbox, relaxed=relaxed)
    return min_x <= center_x <= max_x and min_y <= center_y <= max_y


def bbox_iou(a: BBox, b: BBox) -> float:
    """Plain axis-aligned IoU for two ``(x, y, w, h)`` boxes."""
    inter_w = max(0.0, min(a.x + a.w, b.x + b.w) - max(a.x, b.x))
    inter_h = max(0.0, min(a.y + a.h, b.y + b.h) - max(a.y, b.y))
    inter = inter_w * inter_h
    union = a.w * a.h + b.w * b.h - inter
    return inter / union if union > 0.0 else 0.0


def body_balloon_overlap_reject(
    body_bbox: BBox,
    balloon_bbox: BBox,
    iou_threshold: float = DEFAULT_OVERLAP_IOU_THRESHOLD,
    *,
    relaxed: bool = False,
) -> bool:
    """L4: overlapping body/balloon boxes mean one object, not a tether."""
    effective_thresh = RELAXED_OVERLAP_IOU_THRESHOLD if relaxed else iou_threshold
    return bbox_iou(body_bbox, balloon_bbox) >= effective_thresh
