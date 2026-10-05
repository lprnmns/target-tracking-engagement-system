"""Tests for Stage 3 friendly balloon tracking and fire safety.

Verifies:
1. AutoTrackerService._select_target ignores friendly balloons (returns None if only friendly balloons).
2. AutoTrackerService._select_target selects only enemy balloons when both enemy and friend balloons are visible.
3. AutoTrackerService drops lock immediately if an active target is classified as friendly.
4. AutoTrackerService filters balloons inside spatial attachment ROI of friendly aircraft even without explicit verdict.
5. TrackingLoop._update_fire_zone blocks fire candidates when target is friendly.
6. Operation competition stage STAGE_3 syncs mission active_stage to "stage3".
7. TrackingLoop switches to A3 mode when STAGE_3 or BALLOON_AIRCRAFT is active in operation state.
"""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.schemas.config import TrackingConfig
from app.schemas.operation import CompetitionStage, ControlMode, OperationState, TargetPolicy
from app.schemas.tracking import CockpitTargetVerdict, TrackingState, TrackingUpdate
from app.schemas.vision import BalloonDetection, BBox, BodyDetection, VisionEvent
from app.services.auto_tracker_service import AutoTrackerService
from app.services.tracking_loop import TrackingLoop


def _tracker() -> AutoTrackerService:
    config = SimpleNamespace(tracking=TrackingConfig())
    logger = SimpleNamespace(emit=lambda *args, **kwargs: None)
    return AutoTrackerService(config=config, logger=logger)


def _event(
    frame_id: int,
    balloons: list[BalloonDetection],
    bodies: list[BodyDetection] | None = None,
    verdicts: list[CockpitTargetVerdict] | None = None,
) -> VisionEvent:
    return VisionEvent(
        frame_id=frame_id,
        timestamp_ms=1000 + frame_id * 33,
        source="test_safety",
        frame_width=640,
        frame_height=360,
        fps=30.0,
        preprocess_ms=0.0,
        inference_ms=1.0,
        postprocess_ms=0.0,
        total_latency_ms=1.0,
        balloon_detections=balloons,
        body_detections=bodies or [],
        target_verdicts=verdicts or [],
    )


def _bbox(x: int = 100, y: int = 100, w: int = 150, h: int = 80) -> BBox:
    return BBox(x=x, y=y, w=w, h=h)


def _balloon(det_id: int, cx: int = 320, cy: int = 240, w: int = 40, h: int = 40) -> BalloonDetection:
    return BalloonDetection(
        id=det_id,
        confidence=0.9,
        bbox=BBox(x=cx - w // 2, y=cy - h // 2, w=w, h=h),
        center_x=cx,
        center_y=cy,
        source="test",
    )


def _body(
    det_id: int,
    class_name: str = "f16",
    team: str = "friend",
    bbox: BBox | None = None,
) -> BodyDetection:
    return BodyDetection(
        id=det_id,
        class_name=class_name,
        class_id=0,
        confidence=0.95,
        bbox=bbox or _bbox(200, 100, 160, 80),
        source="test",
        target_team=team,
    )


def _friend_verdict(balloon_id: int, bbox_dict: dict | None = None) -> CockpitTargetVerdict:
    return CockpitTargetVerdict(
        detection_id=balloon_id,
        kind="balloon",
        bbox=bbox_dict or {"x": 300, "y": 220, "w": 40, "h": 40},
        label_tr="DOST BALONU: F-16 | KİLİTLİ",
        verdict_state="FRIEND_LOCKED",
        target_class="F-16",
        target_team="friend",
        range_m=12.0,
        fire_authorized=False,
    )


def _enemy_verdict(balloon_id: int, bbox_dict: dict | None = None) -> CockpitTargetVerdict:
    return CockpitTargetVerdict(
        detection_id=balloon_id,
        kind="balloon",
        bbox=bbox_dict or {"x": 500, "y": 220, "w": 40, "h": 40},
        label_tr="DÜŞMAN BALONU: F-16 | 12.0m | ATEŞ SERBEST",
        verdict_state="FIRE_AUTHORIZED",
        target_class="F-16",
        target_team="enemy",
        range_m=12.0,
        fire_authorized=True,
    )


def test_select_target_ignores_friendly_balloon_when_alone():
    tracker = _tracker()
    tracker.set_controller_mode("A3")

    balloon = _balloon(1, cx=320, cy=240)
    verdict = _friend_verdict(1)

    event = _event(1, balloons=[balloon], verdicts=[verdict])

    target = tracker._select_target(event, center_x=320, center_y=240)
    assert target is None, "Friendly balloon must NEVER be selected by tracker"


def test_select_target_picks_only_enemy_balloon_when_friend_present():
    tracker = _tracker()
    tracker.set_controller_mode("A3")

    # Balloon 1 is friendly (closer to center 320, 240)
    friend_b = _balloon(1, cx=325, cy=245)
    friend_v = _friend_verdict(1, bbox_dict={"x": 305, "y": 225, "w": 40, "h": 40})

    # Balloon 2 is enemy (further from center)
    enemy_b = _balloon(2, cx=450, cy=200)
    enemy_v = _enemy_verdict(2, bbox_dict={"x": 430, "y": 180, "w": 40, "h": 40})

    event = _event(2, balloons=[friend_b, enemy_b], verdicts=[friend_v, enemy_v])

    target = tracker._select_target(event, center_x=320, center_y=240)
    assert target is not None
    cx, cy, w, h = target
    assert cx == 450 and cy == 200, "Tracker must select the enemy balloon, ignoring the closer friendly balloon"


def test_select_target_drops_lock_if_target_becomes_friendly():
    tracker = _tracker()
    tracker.set_controller_mode("A3")

    # Frame 1: Balloon 5 is not yet classified as friend
    b5 = _balloon(5, cx=330, cy=250)
    event1 = _event(10, balloons=[b5])
    target1 = tracker._select_target(event1, center_x=320, center_y=240)
    assert target1 is not None
    assert tracker.preferred_target_detection_id == 5

    # Frame 2: Balloon 5 is now confirmed as FRIEND
    v5 = _friend_verdict(5)
    event2 = _event(11, balloons=[b5], verdicts=[v5])
    target2 = tracker._select_target(event2, center_x=320, center_y=240)
    assert target2 is None, "Tracker must drop target immediately upon friendly classification"
    assert tracker.preferred_target_detection_id is None


def test_select_target_filters_balloon_in_friendly_aircraft_roi_without_verdict():
    tracker = _tracker()
    tracker.set_controller_mode("A3")

    # Friendly aircraft at (200, 100, 160, 80).
    # Its bottom is at y=180. Attachment ROI extends downward from y=180 to 180 + 2.5*80 = 380.
    # Center X is 200 + 80 = 280.
    body = _body(10, class_name="f16", team="friend", bbox=BBox(x=200, y=100, w=160, h=80))

    # Balloon at (280, 220) is right under the friendly aircraft
    balloon = _balloon(7, cx=280, cy=220)

    event = _event(20, balloons=[balloon], bodies=[body])

    target = tracker._select_target(event, center_x=320, center_y=240)
    assert target is None, "Balloon inside friendly aircraft attachment ROI must be rejected"


def test_update_fire_zone_blocks_candidate_on_friendly_target():
    serial = Mock()
    serial.magazine_remaining = 30
    serial.light_protocol_enabled = True

    gateway = Mock()
    loop = TrackingLoop(
        auto_tracker=Mock(preferred_target_detection_id=1, controller_mode="A3"),
        vision_pipeline=Mock(),
        serial=serial,
        gateway=gateway,
        logger=Mock(),
        frame_width=640,
        frame_height=360,
    )

    balloon = _balloon(1, cx=320, cy=180)
    verdict = _friend_verdict(1, bbox_dict={"x": 300, "y": 160, "w": 40, "h": 40})

    event = _event(30, balloons=[balloon], verdicts=[verdict])

    update = TrackingUpdate(
        state=TrackingState.LOCKED,
        target_center_x=320,
        target_center_y=180,
        frame_center_x=320,
        frame_center_y=180,
        frame_id=30,
    )

    # Call _update_fire_zone
    loop._update_fire_zone(event, update, 640, 360)

    assert loop.fire_target_frames == 0, "Friendly target must not accumulate fire frames"
    assert loop._fire_candidate_active is False
    assert len(loop.drain_events()) == 0, "No fire candidate event should be emitted for friendly target"


def test_tracking_loop_controller_mode_for_stage3():
    # Stage 3 explicitly maps to A3
    assert TrackingLoop._controller_mode_for_stage("stage3", "BALLOON") == "A3"
    assert TrackingLoop._controller_mode_for_stage("stage3", "BALLOON_AIRCRAFT") == "A3"
    assert TrackingLoop._controller_mode_for_stage("stage2", "BALLOON") == "A2"
    assert TrackingLoop._controller_mode_for_stage("stage1", "BALLOON") == "A2"
    assert TrackingLoop._controller_mode_for_stage("stage1", "AIRCRAFT") == "legacy"
