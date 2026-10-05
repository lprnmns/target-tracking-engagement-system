"""Small bridge from the production perception loop to Vision Lab trackers.

The Lab stays perception-only.  This module imports only its tracker adapters
and schemas; it never imports the Lab web server, CommandGateway, serial, motor
or trigger code.  Both aircraft and balloon streams receive independent
adapter instances so identities can never cross semantic streams.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

from app.services.storage_paths import project_root


TRACKER_PROFILES: dict[str, dict[str, Any]] = {
    "current_custom": {
        "label": "Mevcut Custom",
        "family": "current_custom",
        "description": "Sahada kullanılan son-gözlem / yakın-komşu tabanı.",
    },
    "bytetrack": {
        "label": "ByteTrack",
        "family": "bytetrack",
        "track_buffer": 30,
        "match_thresh": 0.80,
        "description": "Ultralytics ByteTrack; düşük güvenli kutuları ikinci eşlemede kullanır.",
    },
    "botsort_no_cmc": {
        "label": "BoT-SORT · CMC Kapalı",
        "family": "botsort",
        "with_reid": False,
        "gmc_method": "none",
        "track_buffer": 30,
        "match_thresh": 0.80,
        "description": "ReID ve kamera hareket telafisi kapalı BoT-SORT.",
    },
    "botsort_sparse": {
        "label": "BoT-SORT · Sparse Flow",
        "family": "botsort",
        "with_reid": False,
        "gmc_method": "sparseOptFlow",
        "track_buffer": 30,
        "match_thresh": 0.80,
        "description": "Sparse optical-flow kamera hareket telafili BoT-SORT.",
    },
    "botsort_ecc": {
        "label": "BoT-SORT · ECC",
        "family": "botsort",
        "with_reid": False,
        "gmc_method": "ecc",
        "track_buffer": 30,
        "match_thresh": 0.80,
        "description": "ECC kamera hareket telafili BoT-SORT.",
    },
    "ocsort": {
        "label": "OC-SORT",
        "family": "ocsort",
        "det_thresh": 0.25,
        "max_age": 30,
        "min_hits": 1,
        "iou_threshold": 0.30,
        "description": "Projede vendored MIT OC-SORT adapterı.",
    },
    "norfair_custom": {
        "label": "Norfair Custom",
        "family": "norfair",
        "distance_threshold": 0.75,
        "hit_counter_max": 30,
        "initialization_delay": 1,
        "description": "IoU + merkez + boyut maliyetli Norfair adapterı.",
    },
}


def normalize_tracker_profile(value: str | None, enabled: bool = True) -> str:
    # Historical profiles persisted tracker_enabled=false / tracker_type=none
    # while the custom production trackers still ran. Preserve that exact
    # behaviour and expose it honestly as current_custom.
    if not enabled or not value or value == "none":
        return "current_custom"
    if value not in TRACKER_PROFILES:
        raise ValueError(f"unknown_tracker_profile:{value}")
    return value


def _ensure_lab_import_path() -> Path:
    source = project_root() / "vision_lab" / "src"
    if not source.is_dir():
        raise RuntimeError(f"vision_lab_source_missing:{source}")
    normalized = str(source)
    if normalized not in sys.path:
        sys.path.insert(0, normalized)
    return source


def tracker_profile_catalog() -> list[dict[str, Any]]:
    source_exists = (project_root() / "vision_lab" / "src" / "vision_lab").is_dir()
    results: list[dict[str, Any]] = []
    for profile_id, config in TRACKER_PROFILES.items():
        family = str(config["family"])
        dependency = None
        if family in {"bytetrack", "botsort"}:
            dependency = "ultralytics"
        elif family == "ocsort":
            dependency = "filterpy"
        elif family == "norfair":
            dependency = "norfair"
        available = source_exists and (dependency is None or importlib.util.find_spec(dependency) is not None)
        results.append(
            {
                "profile_id": profile_id,
                "label": config["label"],
                "family": family,
                "description": config["description"],
                "available": available,
                "reason": None if available else ("VISION_LAB_SOURCE_MISSING" if not source_exists else f"DEPENDENCY_MISSING:{dependency}"),
            }
        )
    return results


def create_live_tracker(stream: str, profile: str):
    profile_id = normalize_tracker_profile(profile)
    catalog = {item["profile_id"]: item for item in tracker_profile_catalog()}
    detail = catalog[profile_id]
    if not detail["available"]:
        raise RuntimeError(detail["reason"] or f"tracker_unavailable:{profile_id}")
    _ensure_lab_import_path()
    from vision_lab.schemas import StreamKind
    from vision_lab.trackers.factory import create_tracker

    stream_kind = StreamKind.AIRCRAFT if stream == "aircraft" else StreamKind.BALLOON
    return create_tracker(stream_kind, profile_id, dict(TRACKER_PROFILES[profile_id]))


def lab_detection(
    *,
    stream: str,
    detection_id: int,
    x: float,
    y: float,
    w: float,
    h: float,
    confidence: float,
    class_id: int,
    class_name: str,
    model_id: str,
):
    _ensure_lab_import_path()
    from vision_lab.schemas import BBox, Detection, StreamKind

    stream_kind = StreamKind.AIRCRAFT if stream == "aircraft" else StreamKind.BALLOON
    return Detection(
        detection_id=int(detection_id),
        stream=stream_kind,
        bbox=BBox(x1=float(x), y1=float(y), x2=float(x + w), y2=float(y + h)),
        confidence=float(confidence),
        class_id=int(class_id),
        class_name=str(class_name),
        raw_class_name=str(class_name),
        model_id=str(model_id),
        model_sha256="live-runtime-not-rehashed",
    )
