"""
CockpitVerdictService — Her frame için otoriter hedef kararları (CockpitTargetVerdict) ve HUD etiketleri.

4 yarışma sınıfı (F-16, Helikopter, İHA/Drone, Füze), IFF takımı (Dost/Düşman/Sınıflandırılıyor),
menzil ve ateş serbest/kapalı yetkisini tek bir kaynaktan hesaplar.
"""

from __future__ import annotations

import math
from typing import Any

from app.schemas.tracking import CockpitTargetVerdict
from app.schemas.vision import BalloonDetection, BodyDetection
from app.services import stage3_roi

CLASS_TR_MAP: dict[str, str] = {
    "f16": "F-16",
    "f-16": "F-16",
    "helicopter": "HELİKOPTER",
    "helikopter": "HELİKOPTER",
    "mini_micro_uav": "İHA / DRONE",
    "drone": "İHA / DRONE",
    "siha": "İHA / DRONE",
    "iha": "İHA / DRONE",
    "ballistic_missile": "FÜZE",
    "balistic_missile": "FÜZE",
    "missile": "FÜZE",
    "fuze": "FÜZE",
    "generic_target": "HAVA HEDEFİ",
    "balloon": "BALON",
}

DEFAULT_RANGE_LIMITS: dict[str, tuple[float, float]] = {
    "f16": (10.0, 15.0),
    "f-16": (10.0, 15.0),
    "helicopter": (5.0, 15.0),
    "helikopter": (5.0, 15.0),
    "mini_micro_uav": (0.0, 15.0),
    "drone": (0.0, 15.0),
    "siha": (0.0, 15.0),
    "iha": (0.0, 15.0),
    "ballistic_missile": (5.0, 15.0),
    "balistic_missile": (5.0, 15.0),
    "missile": (5.0, 15.0),
    "fuze": (5.0, 15.0),
}


def normalize_class_name(raw_name: str | None) -> str:
    if not raw_name:
        return "HAVA HEDEFİ"
    key = str(raw_name).strip().lower()
    return CLASS_TR_MAP.get(key, key.upper())


def build_cockpit_verdicts(
    bodies: list[BodyDetection],
    balloons: list[BalloonDetection],
    associations: list[Any] | None = None,
    target_policy: str = "BALLOON_AIRCRAFT",
    active_stage: str = "stage3",
    range_rules: dict[str, Any] | None = None,
    focal_length_px: float = 790.0,
    reference_balloon_diameter_m: float = 0.14,
) -> list[CockpitTargetVerdict]:
    """Her frame için gövde ve balon tespitlerini analiz edip otoriter karar listesi üretir."""
    verdicts: list[CockpitTargetVerdict] = []
    body_verdicts_map: dict[int, CockpitTargetVerdict] = {}
    body_track_map: dict[int, CockpitTargetVerdict] = {}

    # 1. Hava Araçları / Gövde Tespitleri (Bodies)
    for body in bodies:
        raw_class = (body.class_name or "").strip().lower()
        norm_class = normalize_class_name(raw_class)
        team = (body.target_team or "unknown").strip().lower()

        range_m = round(float(body.range_m), 1) if body.range_m is not None and body.range_m > 0 else None
        range_source = "body_bbox" if range_m is not None else "none"
        range_str = f" | {range_m:.1f}m" if range_m is not None else ""

        if active_stage in {"stage2", "STAGE_2"}:
            # Aşama 2: Parkurda yalnızca düşman hedefler vardır.
            # Model dost veya bilinmeyen tespit etse dahi kesinlikle DÜŞMAN ve ATEŞ SERBEST verilir.
            team = "enemy"
            verdict_state = "FIRE_AUTHORIZED"
            fire_authorized = True
            label_tr = f"DÜŞMAN: {norm_class}{range_str} | ATEŞ SERBEST"
        elif team in {"friend", "dost"}:
            verdict_state = "FRIEND_LOCKED"
            fire_authorized = False
            label_tr = f"DOST: {norm_class}{range_str} | KİLİTLİ"
        elif team in {"unknown", "belirsiz"}:
            verdict_state = "CLASSIFYING"
            fire_authorized = False
            label_tr = f"SINIFLANDIRILIYOR: {norm_class}{range_str}"
        else:  # enemy
            # Menzil kontrolü
            rule = range_rules.get(raw_class) if range_rules else None
            if rule is not None and hasattr(rule, "min_m") and hasattr(rule, "max_m"):
                min_m, max_m = float(rule.min_m), float(rule.max_m)
            else:
                min_m, max_m = DEFAULT_RANGE_LIMITS.get(raw_class, (5.0, 15.0))

            if range_m is not None:
                if min_m <= range_m <= max_m:
                    verdict_state = "FIRE_AUTHORIZED"
                    fire_authorized = True
                    label_tr = f"DÜŞMAN: {norm_class} | {range_m:.1f}m | ATEŞ SERBEST"
                else:
                    verdict_state = "RANGE_WAIT"
                    fire_authorized = False
                    label_tr = f"DÜŞMAN: {norm_class} | {range_m:.1f}m | MENZİL KAPALI"
            else:
                verdict_state = "FIRE_AUTHORIZED"
                fire_authorized = True
                label_tr = f"DÜŞMAN: {norm_class} | ATEŞ SERBEST"

        bbox_dict = (
            body.bbox.model_dump()
            if hasattr(body.bbox, "model_dump")
            else dict(body.bbox)
            if isinstance(body.bbox, dict)
            else {"x": body.bbox.x, "y": body.bbox.y, "w": body.bbox.w, "h": body.bbox.h}
        )
        bv = CockpitTargetVerdict(
            track_id=body.track_id,
            detection_id=body.id,
            kind="body",
            bbox=bbox_dict,
            label_tr=label_tr,
            verdict_state=verdict_state,
            target_class=norm_class,
            target_team=team,
            range_m=range_m,
            range_source=range_source,
            range_age_ms=0.0,
            fire_authorized=fire_authorized,
        )
        verdicts.append(bv)
        body_verdicts_map[body.id] = bv
        if body.track_id is not None:
            body_track_map[body.track_id] = bv

    # 2. Balon Tespitleri (Balloons)
    for balloon in balloons:
        balloon_bbox_dict = (
            balloon.bbox.model_dump()
            if hasattr(balloon.bbox, "model_dump")
            else dict(balloon.bbox)
            if isinstance(balloon.bbox, dict)
            else {"x": balloon.bbox.x, "y": balloon.bbox.y, "w": balloon.bbox.w, "h": balloon.bbox.h}
        )

        is_stage3_engagement = (
            target_policy in {"BALLOON_AIRCRAFT", "STAGE_3"}
            or (active_stage == "stage3" and target_policy not in {"BALLOON", "STAGE1_INDEPENDENT"})
        )
        if is_stage3_engagement:
            # Balon bir gövdeyle ilişkili mi kontrol et
            parent_verdict: CockpitTargetVerdict | None = None
            is_coasting = False

            if associations:
                for assoc in associations:
                    assoc_balloon_id = getattr(assoc, "balloon_id", None)
                    if assoc_balloon_id == balloon.id:
                        assoc_body_id = getattr(assoc, "body_id", None)
                        assoc_body_track_id = getattr(assoc, "body_track_id", None)
                        is_coasting = bool(getattr(assoc, "coasting", False) or getattr(assoc, "state", "") == "coasting")
                        if assoc_body_id is not None and assoc_body_id in body_verdicts_map:
                            parent_verdict = body_verdicts_map[assoc_body_id]
                        elif assoc_body_track_id is not None and assoc_body_track_id in body_track_map:
                            parent_verdict = body_track_map[assoc_body_track_id]
                        break

            # Eğer association listesinde yoksa mekânsal olarak Kural 2 (%250 alt ROI) zarfında mı bak
            if parent_verdict is None and bodies:
                bcx = float(getattr(balloon, "center_x", None) or (balloon.bbox.x + balloon.bbox.w / 2))
                bcy = float(getattr(balloon, "center_y", None) or (balloon.bbox.y + balloon.bbox.h / 2))
                for body in bodies:
                    if stage3_roi.attachment_roi_contains(body.bbox, bcx, bcy):
                        parent_verdict = body_verdicts_map.get(body.id)
                        break

            if parent_verdict is not None:
                target_class = parent_verdict.target_class
                target_team = parent_verdict.target_team
                range_m = parent_verdict.range_m
                range_source = parent_verdict.range_source
                range_str = f" | {range_m:.1f}m" if range_m is not None else ""

                if parent_verdict.verdict_state == "FIRE_AUTHORIZED":
                    if is_coasting:
                        verdict_state = "RANGE_WAIT"
                        fire_authorized = False
                        label_tr = f"DÜŞMAN BALONU: {target_class}{range_str} | HAFİF KAYIP (COAST)"
                    else:
                        verdict_state = "FIRE_AUTHORIZED"
                        fire_authorized = True
                        label_tr = f"DÜŞMAN BALONU: {target_class}{range_str} | ATEŞ SERBEST"
                elif parent_verdict.verdict_state == "RANGE_WAIT":
                    verdict_state = "RANGE_WAIT"
                    fire_authorized = False
                    label_tr = f"DÜŞMAN BALONU: {target_class}{range_str} | MENZİL KAPALI"
                elif parent_verdict.verdict_state == "FRIEND_LOCKED":
                    verdict_state = "FRIEND_LOCKED"
                    fire_authorized = False
                    label_tr = f"DOST BALONU: {target_class} | KİLİTLİ"
                else:
                    verdict_state = "CLASSIFYING"
                    fire_authorized = False
                    label_tr = f"SINIFLANDIRILIYOR: {target_class} BALONU"
            else:
                # Sahipsiz / Bağımsız balon
                target_class = "BALON"
                target_team = "unknown"
                range_m = None
                range_source = "none"
                verdict_state = "CLASSIFYING"
                fire_authorized = False
                label_tr = f"SERBEST BALON #{balloon.id}"

            verdicts.append(
                CockpitTargetVerdict(
                    track_id=getattr(balloon, "track_id", None),
                    detection_id=balloon.id,
                    kind="balloon",
                    bbox=balloon_bbox_dict,
                    label_tr=label_tr,
                    verdict_state=verdict_state,
                    target_class=target_class,
                    target_team=target_team,
                    range_m=range_m,
                    range_source=range_source,
                    range_age_ms=0.0,
                    fire_authorized=fire_authorized,
                )
            )
        else:
            # Standart / Aşama 1 / Aşama 2 Serbest Balon Takibi
            target_class = "BALON"
            target_team = "enemy"
            est_range = (
                round(focal_length_px * reference_balloon_diameter_m / max(balloon.bbox.w, 1), 1)
                if balloon.bbox.w > 0
                else None
            )
            range_str = f" | {est_range:.1f}m" if est_range is not None else ""
            verdict_state = "FIRE_AUTHORIZED"
            fire_authorized = True
            label_tr = f"HEDEF BALONU #{balloon.id}{range_str} | ATEŞ SERBEST"

            verdicts.append(
                CockpitTargetVerdict(
                    track_id=getattr(balloon, "track_id", None),
                    detection_id=balloon.id,
                    kind="balloon",
                    bbox=balloon_bbox_dict,
                    label_tr=label_tr,
                    verdict_state=verdict_state,
                    target_class=target_class,
                    target_team=target_team,
                    range_m=est_range,
                    range_source="balloon_size" if est_range is not None else "none",
                    range_age_ms=0.0,
                    fire_authorized=fire_authorized,
                )
            )

    return verdicts
