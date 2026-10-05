"""Single-cockpit operation state shared by the UI and CommandGateway.

The legacy mission/stage services remain available for scoring and evidence,
but the operator-facing control contract is intentionally stage-independent.
"""

from __future__ import annotations

import time
from enum import StrEnum

from pydantic import BaseModel, Field


class ControlMode(StrEnum):
    MANUAL = "MANUAL"
    AUTONOMOUS = "AUTONOMOUS"


class TargetPolicy(StrEnum):
    BALLOON = "BALLOON"
    AIRCRAFT = "AIRCRAFT"
    BALLOON_AIRCRAFT = "BALLOON_AIRCRAFT"
    STAGE1_INDEPENDENT = "STAGE1_INDEPENDENT"


class CompetitionStage(StrEnum):
    STAGE_1 = "STAGE_1"
    STAGE_2 = "STAGE_2"
    STAGE_3 = "STAGE_3"


class PathAngleConfig(BaseModel):
    path1_deg: float = 120.0
    path2_deg: float = 135.0
    path3_deg: float = 150.0
    dwell_time_s: float = 1.0
    active_path: int = 2
    patrol_enabled: bool = False
    strategy: str = "CORRIDOR_HOP"  # Default schema value; runtime active config overrides to SMOOTH_SWEEP
    sweep_speed_dps: float = 9.375  # ~1250 sps, max blur-safe speed (<= 3px blur @ 10ms exposure)
    sweep_sector_deg: float = 30.0  # 135 +- 15 deg
    # Devriye/arama sırasında hedeflenecek FIZIKSEL tilt (derece). Mekanik
    # AutoHome merkezinden (HOME_TILT_DEG=30.0, tracking_loop.py) BAĞIMSIZ;
    # sahada gözlemlenen gerçek hedef irtifasına (22-24 derece) göre
    # kalibre edilir. Bkz. tracking_loop.py:_scan_tilt_deg().
    scan_tilt_deg: float | None = 30.0


class PathAngleUpdate(BaseModel):
    path1_deg: float | None = None
    path2_deg: float | None = None
    path3_deg: float | None = None
    dwell_time_s: float | None = None
    strategy: str | None = None
    sweep_speed_dps: float | None = None
    sweep_sector_deg: float | None = None
    # NOT: Burada varsayilan None (23.0 DEGIL). PathAngleUpdate kismi
    # (PATCH-stili) bir modeldir; digestor tum alanlari None = "operator
    # bu alani gondermedi, dokunma" anlamina gelir - tipki yukaridaki diger
    # alanlar gibi. patrol_radar_service.update_angles() de scan_tilt_deg'i
    # ayni sekilde "None=degistirme" olarak isler (bkz. patrol_radar_service.py).
    # Varsayilani 23.0 yapmak, operatorun sadece path1_deg gibi TEK bir aciyi
    # guncelledigi HER cagrida sahada ayarlanmis scan_tilt_deg'i sessizce
    # 23.0'a resetler, bu da Gorev 3'teki saha kalibrasyonunu bozar.
    scan_tilt_deg: float | None = None


class FirePermission(StrEnum):
    DISABLED = "DISABLED"
    ENABLED = "ENABLED"


class SelectedDetectionKind(StrEnum):
    BODY = "body"
    BALLOON = "balloon"


class OperationState(BaseModel):
    control_mode: ControlMode = ControlMode.MANUAL
    target_policy: TargetPolicy = TargetPolicy.BALLOON
    competition_stage: CompetitionStage | None = None
    fire_permission: FirePermission = FirePermission.DISABLED
    selected_logical_target_id: str | None = None
    selected_detection_id: int | None = None
    selected_detection_kind: SelectedDetectionKind | None = None
    # Body detection ids are frame-local.  Keep the vision-owned body track
    # id so an operator handoff remains attached to the same aircraft.
    selected_body_track_id: int | None = None
    tracking_requested: bool = False
    # False preserves compatibility for non-UI legacy callers/tests. Once the
    # cockpit writes the state, all new mode/policy gates are authoritative.
    configured: bool = False
    updated_at: float = Field(default_factory=time.time)


class OperationUpdate(BaseModel):
    control_mode: ControlMode | None = None
    target_policy: TargetPolicy | None = None
    competition_stage: CompetitionStage | None = None
    fire_permission: FirePermission | None = None
    selected_logical_target_id: str | None = None
    selected_detection_id: int | None = None
    selected_detection_kind: SelectedDetectionKind | None = None
    selected_body_track_id: int | None = None
    tracking_requested: bool | None = None


class OperationTargetSelection(BaseModel):
    logical_target_id: str | None = None
    detection_id: int
    detection_kind: SelectedDetectionKind
    body_track_id: int | None = None
    x: float
    y: float
    frame_id: int | None = None
