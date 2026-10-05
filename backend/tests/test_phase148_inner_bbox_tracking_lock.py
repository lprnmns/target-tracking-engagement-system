from __future__ import annotations

import time

from app.schemas.tracking import TrackingConfigUpdate, TrackingState
from app.schemas.vision import BBox, BodyDetection, VisionEvent


def _event(frame_id: int, center_x: int, center_y: int) -> VisionEvent:
    return VisionEvent(
        frame_id=frame_id,
        timestamp_ms=int(time.time() * 1000) + frame_id * 34,
        source="phase148",
        frame_width=640,
        frame_height=360,
        fps=30,
        preprocess_ms=1,
        inference_ms=2,
        postprocess_ms=1,
        total_latency_ms=4,
        body_detections=[
            BodyDetection(
                id=1,
                track_id=48,
                class_name="mini_micro_uav",
                class_id=0,
                confidence=0.9,
                bbox=BBox(x=center_x - 50, y=center_y - 40, w=100, h=80),
                target_team="enemy",
            )
        ],
        balloon_detections=[],
    )


def test_tracking_stops_inside_30_percent_inner_bbox_and_resumes_outside(client) -> None:
    tracker = client.app.state.runtime.auto_tracker
    tracker.stop_tracking()
    tracker.set_target_policy("AIRCRAFT")
    tracker.update_config(TrackingConfigUpdate(
        pid_kp_x=1200,
        pid_ki_x=0,
        pid_kd_x=120,
        pid_kp_y=800,
        pid_ki_y=0,
        pid_kd_y=130,
        smoothing_alpha=1,
        max_speed_x=1000,
        max_speed_y=500,
        aim_offset_x_px=0,
        aim_offset_y_px=0,
        invert_x=False,
        invert_y=False,
        deadband_lock_ratio=0,
        deadband_slow_ratio=0,
        deadband_medium_ratio=0,
        bbox_lock_enabled=True,
    ))
    tracker.start_tracking()

    # 100x80 bbox -> 30x24 lock rectangle -> half extents 15x12 px.
    inside = tracker.update(_event(1, 334, 191), 640, 360)
    assert inside.error_x_px == 14
    assert inside.error_y_px == 11
    assert inside.state == TrackingState.LOCKED
    assert inside.deadband_zone == "locked"
    assert inside.speed_x == 0 and inside.speed_y == 0

    outside = tracker.update(_event(2, 336, 193), 640, 360)
    assert outside.error_x_px == 16
    assert outside.error_y_px == 13
    assert outside.state == TrackingState.TRACKING
    assert outside.deadband_zone == "full"
    assert outside.speed_x != 0 and outside.speed_y != 0


def test_selected_target_overlay_draws_same_30_percent_lock_rectangle() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    source = (root / "frontend/src/components/cockpit/LiveCameraPanel.vue").read_text(encoding="utf-8")
    assert 'boxX(target) + boxW(target) * 0.35' in source
    assert 'boxY(target) + boxH(target) * 0.35' in source
    assert 'boxW(target) * 0.3' in source
    assert 'boxH(target) * 0.3' in source


def test_auto_fire_uses_same_inner_region_on_first_entry_frame() -> None:
    from app.services.tracking_loop import FIRE_INNER_BBOX_RATIO, FIRE_REQUIRED_FRAMES

    assert FIRE_INNER_BBOX_RATIO == 0.30
    assert FIRE_REQUIRED_FRAMES == 1


def test_bbox_lock_can_be_disabled_live_without_stopping_tracking(client) -> None:
    tracker = client.app.state.runtime.auto_tracker
    tracker.stop_tracking()
    tracker.clear_target()
    tracker.set_target_policy("AIRCRAFT")
    tracker.update_config(TrackingConfigUpdate(
        bbox_lock_enabled=True,
        aim_offset_x_px=0,
        aim_offset_y_px=0,
        invert_x=False,
        invert_y=False,
    ))
    tracker.start_tracking()

    locked = tracker.update(_event(1, 320, 180), 640, 360)
    assert locked.state == TrackingState.LOCKED
    assert locked.bbox_lock_enabled is True

    tracker.update_config(TrackingConfigUpdate(bbox_lock_enabled=False))
    unlocked = tracker.update(_event(2, 320, 180), 640, 360)
    assert unlocked.state == TrackingState.TRACKING
    assert unlocked.deadband_zone == "bbox_lock_off"
    assert unlocked.bbox_lock_enabled is False
    assert tracker.status().bbox_lock_enabled is False


def test_operator_has_live_30_percent_bbox_lock_toggle() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    bar = (root / "frontend/src/components/cockpit/OperationalControlBar.vue").read_text(encoding="utf-8")
    cockpit = (root / "frontend/src/views/CockpitView.vue").read_text(encoding="utf-8")
    assert "%30 KİLİT" in bar
    assert "toggleBboxLock" in bar
    assert "bbox_lock_enabled: enabled" in cockpit
    assert '@toggle-bbox-lock="toggleBboxLock"' in cockpit


def test_aircraft_track_id_churn_hands_off_to_unique_continuous_body(client) -> None:
    tracker = client.app.state.runtime.auto_tracker
    tracker.stop_tracking()
    tracker.clear_target()
    tracker.set_target_policy("AIRCRAFT")
    tracker.select_target(x=420, y=180, detection_id=1, frame_id=1, kind="body")
    tracker.start_tracking()

    first = _event(1, 420, 180)
    acquired = tracker.update(first, 640, 360)
    assert acquired.selected_track_id == 48

    replacement = first.body_detections[0].model_copy(
        update={
            "id": 2,
            "track_id": 99,
            "bbox": BBox(x=380, y=140, w=100, h=80),
        }
    )
    distractor = first.body_detections[0].model_copy(
        update={
            "id": 3,
            "track_id": 100,
            "bbox": BBox(x=50, y=140, w=100, h=80),
        }
    )
    changed = first.model_copy(
        update={
            "frame_id": 2,
            "timestamp_ms": first.timestamp_ms + 34,
            "body_detections": [replacement, distractor],
        }
    )

    handoff = tracker.update(changed, 640, 360)
    assert handoff.state != TrackingState.SEARCHING
    assert handoff.selected_track_id == 99
    assert handoff.target_center_x == 430


def test_last_approach_keeps_minimum_useful_speed_until_30_percent_lock(client) -> None:
    tracker = client.app.state.runtime.auto_tracker
    tracker.stop_tracking()
    tracker.clear_target()
    tracker.set_target_policy("AIRCRAFT")
    tracker.start_tracking()

    # 100 px bbox gives a 15 px half-lock. At 16 px error the target is just
    # outside lock and must not crawl at a near-zero step frequency.
    update = tracker.update(_event(1, 336, 180), 640, 360)

    assert update.state == TrackingState.TRACKING
    assert abs(update.speed_x) >= 90
