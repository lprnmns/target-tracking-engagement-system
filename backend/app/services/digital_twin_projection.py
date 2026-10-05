from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from app.schemas.digital_twin import DigitalTwinBBox, DigitalTwinTargetProjectionEstimate, DigitalTwinVector3


TARGET_REFERENCE_SIZE_MM = {
    "balloon": 140.0,
    "balloon_fixture": 140.0,
    "balloon_replay": 140.0,
    "ballistic_missile": 500.0,
    "helicopter": 583.0,
    "f16": 500.0,
    "mini_micro_uav": 375.0,
}


def project_bbox_to_scene(
    *,
    bbox: DigitalTwinBBox | Mapping[str, Any],
    frame_width: int,
    frame_height: int,
    class_name: str,
    confidence: float,
    target_id: int | None = None,
    target_team: str = "unknown",
    selected: bool = False,
    camera_fov_horizontal_deg: float = 62.0,
    camera_fov_vertical_deg: float = 38.0,
    camera_to_launcher_offset_z_mm: float = 30.0,
    camera_to_launcher_offset_y_mm: float = 0.0,
    known_target_size_mm: float | None = None,
) -> DigitalTwinTargetProjectionEstimate:
    """Map a 2D detection bbox into a deterministic 3D display estimate.

    This is not a fire solution and does not claim metric range. It projects
    frame position through configured FOV and uses bbox area as a relative
    inverse-depth cue for operator situational awareness only.
    """

    frame_w = max(float(frame_width), 1.0)
    frame_h = max(float(frame_height), 1.0)
    x, y, w, h = _resolve_bbox(bbox, frame_w, frame_h)
    x = _clamp(x, 0.0, frame_w - 1.0)
    y = _clamp(y, 0.0, frame_h - 1.0)
    w = _clamp(w, 1.0, frame_w - x)
    h = _clamp(h, 1.0, frame_h - y)

    center_x = x + w / 2.0
    center_y = y + h / 2.0
    norm_cx = _clamp(center_x / frame_w, 0.0, 1.0)
    norm_cy = _clamp(center_y / frame_h, 0.0, 1.0)
    norm_w = _clamp(w / frame_w, 0.0, 1.0)
    norm_h = _clamp(h / frame_h, 0.0, 1.0)
    screen_x = _clamp((norm_cx - 0.5) * 2.0, -1.0, 1.0)
    screen_y = _clamp((0.5 - norm_cy) * 2.0, -1.0, 1.0)
    area_ratio = _clamp((w * h) / (frame_w * frame_h), 0.0, 1.0)

    azimuth_deg = screen_x * (camera_fov_horizontal_deg / 2.0)
    elevation_deg = screen_y * (camera_fov_vertical_deg / 2.0)

    reference_size_mm = known_target_size_mm or TARGET_REFERENCE_SIZE_MM.get(_canonical_class_name(class_name))
    if reference_size_mm:
        # Monocular pinhole estimate. This is a visual situational-awareness
        # estimate, not a calibrated range or a fire solution; class pose can
        # materially change apparent span and the uncertainty is exposed.
        focal_x_px = frame_w / (2.0 * math.tan(math.radians(max(camera_fov_horizontal_deg, 1.0)) / 2.0))
        focal_y_px = frame_h / (2.0 * math.tan(math.radians(max(camera_fov_vertical_deg, 1.0)) / 2.0))
        reference_size_m = reference_size_mm / 1000.0
        depth_from_width_m = (focal_x_px * reference_size_m) / max(w, 1.0)
        depth_from_height_m = (focal_y_px * reference_size_m) / max(h, 1.0)
        # Balloon boxes describe one circular physical diameter on both axes.
        # The geometric mean rejects mild rectangular YOLO padding without
        # mixing horizontal pixels with the vertical camera FOV.
        if _canonical_class_name(class_name) == "balloon":
            raw_depth_m = math.sqrt(depth_from_width_m * depth_from_height_m)
        else:
            raw_depth_m = min(depth_from_width_m, depth_from_height_m)
        depth_m = _clamp(raw_depth_m, 0.3, 40.0)
        uncertainty_ratio = 0.25 if _canonical_class_name(class_name) == "balloon" else 0.38
        range_uncertainty_m = max(0.15, depth_m * uncertainty_ratio)
        range_source = "class_bbox_pinhole_estimate"
        relative_depth = round(_clamp((depth_m - 0.3) / 14.7, 0.0, 1.0), 4)
    else:
        # Fallback remains explicitly non-metric when a class has no measured
        # reference size.
        linear_size = math.sqrt(max(area_ratio, 1e-9))
        far_linear_size = 0.025
        near_linear_size = 0.32
        closeness = _clamp((linear_size - far_linear_size) / (near_linear_size - far_linear_size), 0.0, 1.0)
        relative_depth = round(1.0 - closeness, 4)
        depth_m = 1.35 + (relative_depth * 6.15)
        reference_size_mm = None
        range_uncertainty_m = None
        range_source = "bbox_area_relative_estimate"

    azimuth_rad = math.radians(azimuth_deg)
    elevation_rad = math.radians(elevation_deg)
    lateral_offset_m = camera_to_launcher_offset_y_mm / 1000.0
    vertical_offset_m = camera_to_launcher_offset_z_mm / 1000.0
    scene_x = math.tan(azimuth_rad) * depth_m + lateral_offset_m
    scene_y = math.tan(elevation_rad) * depth_m + vertical_offset_m
    scene_z = -depth_m

    return DigitalTwinTargetProjectionEstimate(
        target_id=target_id,
        class_name=class_name,
        target_team=target_team,
        confidence=_clamp(float(confidence), 0.0, 1.0),
        confidence_label=_confidence_label(confidence),
        bbox=DigitalTwinBBox(x=int(round(x)), y=int(round(y)), w=int(round(w)), h=int(round(h))),
        normalized_center_x=round(norm_cx, 4),
        normalized_center_y=round(norm_cy, 4),
        normalized_width=round(norm_w, 4),
        normalized_height=round(norm_h, 4),
        normalized_screen_x=round(screen_x, 4),
        normalized_screen_y=round(screen_y, 4),
        bbox_area_ratio=round(area_ratio, 6),
        azimuth_deg=round(azimuth_deg, 3),
        elevation_deg=round(elevation_deg, 3),
        relative_depth=relative_depth,
        estimated_range_band=_range_band(relative_depth),
        reference_size_m=round(reference_size_mm / 1000.0, 3) if reference_size_mm else None,
        estimated_range_m=round(depth_m, 3) if reference_size_mm else None,
        range_uncertainty_m=round(range_uncertainty_m, 3) if range_uncertainty_m is not None else None,
        range_source=range_source,
        scene_position_m=DigitalTwinVector3(x=round(scene_x, 3), y=round(scene_y, 3), z=round(scene_z, 3)),
        selected=selected,
        depth_source=range_source,
        camera_fov_horizontal_deg=round(float(camera_fov_horizontal_deg), 3),
        camera_fov_vertical_deg=round(float(camera_fov_vertical_deg), 3),
        camera_to_launcher_offset_z_mm=round(float(camera_to_launcher_offset_z_mm), 3),
        camera_to_launcher_offset_y_mm=round(float(camera_to_launcher_offset_y_mm), 3),
        no_physical_command_generated=True,
    )


def _resolve_bbox(bbox: DigitalTwinBBox | Mapping[str, Any], frame_w: float, frame_h: float) -> tuple[float, float, float, float]:
    if isinstance(bbox, DigitalTwinBBox):
        return float(bbox.x), float(bbox.y), float(bbox.w), float(bbox.h)
    data = dict(bbox)
    if {"x", "y", "w", "h"}.issubset(data):
        return float(data["x"]), float(data["y"]), float(data["w"]), float(data["h"])
    if {"x1", "y1", "x2", "y2"}.issubset(data):
        x1 = float(data["x1"])
        y1 = float(data["y1"])
        x2 = float(data["x2"])
        y2 = float(data["y2"])
        if max(abs(x1), abs(y1), abs(x2), abs(y2)) <= 1.0:
            x1 *= frame_w
            x2 *= frame_w
            y1 *= frame_h
            y2 *= frame_h
        return min(x1, x2), min(y1, y2), abs(x2 - x1), abs(y2 - y1)
    if {"normalized_center_x", "normalized_center_y", "normalized_width", "normalized_height"}.issubset(data):
        cx = float(data["normalized_center_x"]) * frame_w
        cy = float(data["normalized_center_y"]) * frame_h
        w = float(data["normalized_width"]) * frame_w
        h = float(data["normalized_height"]) * frame_h
        return cx - w / 2.0, cy - h / 2.0, w, h
    if {"center_x", "center_y", "width", "height"}.issubset(data):
        cx = float(data["center_x"])
        cy = float(data["center_y"])
        w = float(data["width"])
        h = float(data["height"])
        if max(abs(cx), abs(cy), abs(w), abs(h)) <= 1.0:
            cx *= frame_w
            cy *= frame_h
            w *= frame_w
            h *= frame_h
        return cx - w / 2.0, cy - h / 2.0, w, h
    raise ValueError("bbox must contain x/y/w/h, x1/y1/x2/y2, or normalized center/size fields")


def _confidence_label(confidence: float) -> str:
    if confidence >= 0.8:
        return "high"
    if confidence >= 0.5:
        return "medium"
    return "low"


def _range_band(relative_depth: float) -> str:
    if relative_depth <= 0.33:
        return "near"
    if relative_depth <= 0.66:
        return "mid"
    return "far"


def _canonical_class_name(value: str) -> str:
    normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
    if normalized in {"balon", "balloon_fixture", "balloon_replay"}:
        return "balloon" if normalized == "balon" else normalized
    return normalized


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))
