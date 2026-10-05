"""
Tracking Pydantic Schemas — AutoTrackerService veri modelleri.
"""

from __future__ import annotations

import time
from enum import StrEnum

from pydantic import BaseModel, Field


class TrackingState(StrEnum):
    """Takip döngüsü durumları."""

    IDLE = "IDLE"                     # Takip kapalı
    SEARCHING = "SEARCHING"           # Hedef aranıyor
    TRACKING = "TRACKING"             # Aktif takip
    LOCKED = "LOCKED"                 # Hedef merkezde (deadband içinde)
    TARGET_LOST = "TARGET_LOST"       # Hedef kayboldu; son gerçek kimlik kısa süre korunur
    STOPPED = "STOPPED"               # Manuel durduruldu
    ERROR = "ERROR"                   # Hata durumu


from typing import Any


class CockpitTargetVerdict(BaseModel):
    """Her frame için otoriter hedef kararı ve Türkçe HUD etiketi."""

    track_id: int | None = None
    detection_id: int | None = None
    kind: str = "body"  # "body" | "balloon"
    bbox: dict[str, Any] = Field(default_factory=dict)
    label_tr: str = ""
    verdict_state: str = "CLASSIFYING"  # CLASSIFYING | FRIEND_LOCKED | RANGE_WAIT | FIRE_AUTHORIZED
    target_class: str = "HAVA HEDEFİ"
    target_team: str = "unknown"
    range_m: float | None = None
    range_source: str = "none"  # "body_bbox" | "balloon_size" | "none"
    range_age_ms: float = 0.0
    fire_authorized: bool = False


class TrackingUpdate(BaseModel):
    """Her tracking frame'inde üretilen güncelleme."""

    state: TrackingState = TrackingState.IDLE
    speed_x: int = 0
    speed_y: int = 0
    error_x_px: float = 0.0
    error_y_px: float = 0.0
    raw_pid_x: float = 0.0
    raw_pid_y: float = 0.0
    target_center_x: float | None = None
    target_center_y: float | None = None
    frame_center_x: float = 0.0
    frame_center_y: float = 0.0
    aim_offset_x_px: float = 0.0
    aim_offset_y_px: float = 0.0
    target_lost_frames: int = 0
    distance_to_center: float = 0.0
    deadband_zone: str = "none"       # none / locked / slow / medium / full
    bbox_lock_enabled: bool = True
    lock_zone_center_x: float | None = None
    lock_zone_center_y: float | None = None
    lock_zone_width: float = 0.0
    lock_zone_height: float = 0.0
    target_velocity_x_px_s: float = 0.0
    target_velocity_y_px_s: float = 0.0
    momentum_lock_active: bool = False
    momentum_shift_x_px: float = 0.0
    momentum_shift_y_px: float = 0.0
    using_kalman_prediction: bool = False
    lead_horizon_ms: float = 0.0
    predicted_target_center_x: float | None = None
    predicted_target_center_y: float | None = None
    # Read-only AUTO diagnostics.  These fields expose which stable vision
    # identity produced the image-space error without granting command
    # authority to the UI or the digital twin.
    selected_track_id: int | None = None
    selected_detection_id: int | None = None
    selected_target_kind: str | None = None
    selected_target_confidence: float | None = None
    selected_bbox_x: float | None = None
    selected_bbox_y: float | None = None
    selected_bbox_w: float | None = None
    selected_bbox_h: float | None = None
    selected_track_age_frames: int = 0
    selected_track_misses: int = 0
    selected_track_last_seen_at: float | None = None
    selected_track_last_seen_age_ms: float | None = None
    selected_track_occluded: bool = False
    vision_timestamp_ms: int | None = None
    vision_age_ms: float | None = None
    measurement_status: str = "MISSING"  # NEW / REUSED / STALE_REJECTED / MISSING
    frame_id: int = 0
    dt: float = 0.0
    controller_mode: str = "legacy"
    target_verdicts: list[CockpitTargetVerdict] = Field(default_factory=list)
    updated_at: float = Field(default_factory=time.time)


class TrackingFireResult(BaseModel):
    accepted: bool
    command: str = "FIRE"
    reason_codes: list[str] = Field(default_factory=list)
    detail: str
    physical_command_generated: bool = False
    updated_at: float = Field(default_factory=time.time)


class MultiTargetTrack(BaseModel):
    track_id: int
    detection_id: int | None = None
    center_x: float
    center_y: float
    velocity_x: float
    velocity_y: float
    age_frames: int = 0
    hits: int = 0
    misses: int = 0
    confidence: float = 0.0
    bbox_x: float = 0.0
    bbox_y: float = 0.0
    bbox_w: float = 0.0
    bbox_h: float = 0.0
    predicted: bool = False
    fresh: bool = False
    last_seen_at: float | None = None
    last_seen_age_ms: float | None = None
    updated_at: float = Field(default_factory=time.time)


class MultiTargetTrackingStatus(BaseModel):
    tracker_kind: str = "nearest_neighbor_last_observation"
    active_track_count: int = 0
    id_switch_count: int = 0
    rejected_stale_frames: int = 0
    last_frame_id: int | None = None
    last_timestamp_ms: int | None = None
    tracks: list[MultiTargetTrack] = Field(default_factory=list)
    updated_at: float = Field(default_factory=time.time)


class BodyBalloonAssociation(BaseModel):
    balloon_track_id: int
    body_detection_id: int | None = None
    body_track_id: int | None = None
    state: str  # stable / tentative / ambiguous / orphan / coasting
    distance_px: float | None = None
    confidence: float = 0.0
    stable_frames: int = 0
    attachment_region_ok: bool = False
    association_cost: float | None = None
    updated_at: float = Field(default_factory=time.time)
    coasting: bool = False


class AssociationStatus(BaseModel):
    associations: list[BodyBalloonAssociation] = Field(default_factory=list)
    stable_count: int = 0
    ambiguous_count: int = 0
    orphan_count: int = 0
    coasting_count: int = 0
    updated_at: float = Field(default_factory=time.time)


class LogicalTargetState(StrEnum):
    """Persistent perception identity state for a target and its balloon."""

    STANDALONE = "STANDALONE"
    ASSOCIATION_CANDIDATE = "ASSOCIATION_CANDIDATE"
    READY = "READY"
    REENGAGE = "REENGAGE"
    PENDING_CONFIRMATION = "PENDING_CONFIRMATION"
    DESTROYED = "DESTROYED"


class LogicalTarget(BaseModel):
    """Stable human/machine identity for one body and at most one balloon."""

    target_id: str
    display_name: str
    balloon_target_id: str
    balloon_display_name: str
    target_class: str = "balloon"
    target_team: str = "unknown"
    body_track_id: int | None = None
    body_detection_id: int | None = None
    balloon_track_id: int | None = None
    # ``balloon_track_id`` belongs to the temporal tracker, while this is the
    # frame-local detector id currently painted by the camera overlay.  They
    # are deliberately separate: a tracker can keep one identity while the
    # detector assigns a different id on the next frame.
    balloon_detection_id: int | None = None
    # Friendly aircraft and their attached balloon remain visible for IFF and
    # evidence, but are never selectable by the engagement/tracking path.
    engagement_allowed: bool = True
    state: LogicalTargetState = LogicalTargetState.STANDALONE
    association_started_at: float | None = None
    association_duration_s: float = 0.0
    last_seen_at: float | None = None
    last_body_seen_at: float | None = None
    last_balloon_seen_at: float | None = None
    dropout_until: float | None = None
    consumed: bool = False
    destroyed_at: float | None = None
    reason_code: str | None = None
    updated_at: float = Field(default_factory=time.time)


class TargetRegistryStatus(BaseModel):
    """Read-only registry snapshot exposed to UI, evidence and gateway."""

    targets: list[LogicalTarget] = Field(default_factory=list)
    selected_target_id: str | None = None
    selected_balloon_track_id: int | None = None
    destroyed_target_ids: list[str] = Field(default_factory=list)
    consumed_balloon_track_ids: list[int] = Field(default_factory=list)
    association_min_duration_s: float = 1.0
    dropout_grace_s: float = 0.75
    updated_at: float = Field(default_factory=time.time)


class TargetPriorityCandidate(BaseModel):
    balloon_track_id: int
    body_detection_id: int
    body_track_id: int | None = None
    score: float
    time_to_exit_s: float | None = None
    solution_quality: float
    return_cost: float
    logical_target_id: str | None = None
    logical_target_state: LogicalTargetState | None = None
    reasons: list[str] = Field(default_factory=list)


class TargetPriorityStatus(BaseModel):
    selected_track_id: int | None = None
    ranked_candidates: list[TargetPriorityCandidate] = Field(default_factory=list)
    excluded_track_ids: list[int] = Field(default_factory=list)
    updated_at: float = Field(default_factory=time.time)


class EngagementState(StrEnum):
    PENDING_CONFIRMATION = "PENDING_CONFIRMATION"
    CONFIRMED_HIT = "CONFIRMED_HIT"
    REENGAGE = "REENGAGE"


class EngagementOutcome(StrEnum):
    PENDING = "PENDING"
    HIT_CONFIRMED = "HIT_CONFIRMED"
    MISS_CONFIRMED = "MISS_CONFIRMED"
    UNCONFIRMED = "UNCONFIRMED"


class EngagementRecord(BaseModel):
    balloon_track_id: int
    body_detection_id: int | None = None
    body_track_id: int | None = None
    state: EngagementState
    shot_count: int = 1
    reason: str
    shot_at: float
    outcome: EngagementOutcome = EngagementOutcome.PENDING
    balloon_missing_frames: int = 0
    balloon_missing_since: float | None = None
    balloon_visible_after_grace: bool = False
    body_lost_during_confirmation: bool = False
    updated_at: float = Field(default_factory=time.time)


class EngagementStatus(BaseModel):
    records: list[EngagementRecord] = Field(default_factory=list)
    pending_count: int = 0
    confirmed_hit_count: int = 0
    reengage_count: int = 0
    updated_at: float = Field(default_factory=time.time)


class TrackingStatus(BaseModel):
    """Takip sistemi genel durumu."""

    active: bool = False
    state: TrackingState = TrackingState.IDLE
    target_count: int = 0
    lost_count: int = 0
    total_frames: int = 0
    pid_kp_x: float = 1200.0
    pid_ki_x: float = 0.0
    pid_kd_x: float = 120.0
    pid_kp_y: float = 800.0
    pid_ki_y: float = 0.0
    pid_kd_y: float = 130.0
    config_revision: int = 0
    config_applied_at: float = Field(default_factory=time.time)
    smoothing_alpha: float = 0.90
    command_rate_hz: float = 30.0
    max_speed: int = 1000
    max_speed_x: int = 1000
    max_speed_y: int = 1000
    bbox_lock_enabled: bool = True
    momentum_lock_enabled: bool = True
    momentum_velocity_threshold_px_s: float = 12.0
    aim_offset_x_px: float = 0.0
    aim_offset_y_px: float = 0.0
    invert_x: bool = False
    invert_y: bool = True
    lead_enabled: bool = False
    lead_latency_multiplier: float = 1.0
    lead_max_horizon_ms: float = 120.0
    preferred_target_x: float | None = None
    preferred_target_y: float | None = None
    preferred_target_detection_id: int | None = None
    preferred_target_kind: str | None = None
    target_policy: str = "BALLOON"
    last_update: TrackingUpdate | None = None
    last_fire_result: TrackingFireResult | None = None
    controller_mode: str = "legacy"
    multi_target_tracker: MultiTargetTrackingStatus = Field(default_factory=MultiTargetTrackingStatus)
    updated_at: float = Field(default_factory=time.time)


class TrackingConfigUpdate(BaseModel):
    """PID ve tracking parametrelerini güncellemek için."""

    pid_kp_x: float | None = Field(default=None, ge=0.0, le=10000.0)
    pid_ki_x: float | None = Field(default=None, ge=0.0, le=10000.0)
    pid_kd_x: float | None = Field(default=None, ge=0.0, le=10000.0)
    pid_kp_y: float | None = Field(default=None, ge=0.0, le=10000.0)
    pid_ki_y: float | None = Field(default=None, ge=0.0, le=10000.0)
    pid_kd_y: float | None = Field(default=None, ge=0.0, le=10000.0)
    smoothing_alpha: float | None = Field(default=None, ge=0.0, le=1.0)
    command_rate_hz: float | None = Field(default=None, ge=1.0, le=60.0)
    max_speed: int | None = Field(default=None, ge=1, le=20000)
    max_speed_x: int | None = Field(default=None, ge=1, le=20000)
    max_speed_y: int | None = Field(default=None, ge=1, le=20000)
    min_move_speed: float | None = Field(default=None, ge=0.0, le=1000.0)
    deadband_lock_ratio: float | None = Field(default=None, ge=0.0, le=10.0)
    deadband_slow_ratio: float | None = Field(default=None, ge=0.0, le=10.0)
    deadband_medium_ratio: float | None = Field(default=None, ge=0.0, le=10.0)
    bbox_lock_enabled: bool | None = None
    momentum_lock_enabled: bool | None = None
    momentum_velocity_threshold_px_s: float | None = Field(default=None, ge=0.0, le=2000.0)
    aim_offset_x_px: float | None = Field(default=None, ge=-1000.0, le=1000.0)
    aim_offset_y_px: float | None = Field(default=None, ge=-1000.0, le=1000.0)
    invert_x: bool | None = None
    invert_y: bool | None = None
    lead_enabled: bool | None = None
    lead_latency_multiplier: float | None = Field(default=None, ge=0.0, le=3.0)
    lead_max_horizon_ms: float | None = Field(default=None, ge=0.0, le=300.0)
    max_lost_frames: int | None = Field(default=None, ge=1, le=120)
    controller_mode: str | None = None


class TrackingTargetSelectRequest(BaseModel):
    """Vision overlay üzerinden seçilen takip hedefi."""

    x: float
    y: float
    detection_id: int | None = None
    frame_id: int | None = None
    kind: str | None = None
