from __future__ import annotations

import time
from pathlib import Path

from app.schemas.tracking import TrackingState
from app.schemas.vision import BBox, BalloonDetection, VisionEvent


REPO_ROOT = Path(__file__).resolve().parents[2]


def _event(frame_id: int, timestamp_ms: int, center_x: int | None, center_y: int = 180) -> VisionEvent:
    balloons = []
    if center_x is not None:
        balloons = [
            BalloonDetection(
                id=41,
                confidence=0.93,
                bbox=BBox(x=center_x - 24, y=center_y - 20, w=48, h=40),
                center_x=center_x,
                center_y=center_y,
                source="phase139_noop",
            )
        ]
    return VisionEvent(
        frame_id=frame_id,
        timestamp_ms=timestamp_ms,
        source="phase139_noop",
        frame_width=640,
        frame_height=360,
        fps=30,
        preprocess_ms=1,
        inference_ms=2,
        postprocess_ms=1,
        total_latency_ms=4,
        body_detections=[],
        balloon_detections=balloons,
    )


def test_manual_auto_transition_20_cycles_preserves_track_and_rejects_stale_state(client) -> None:
    """MANUAL preview -> AUTO control is identity-stable and hardware no-op."""
    runtime = client.app.state.runtime
    tracker = runtime.auto_tracker
    serial_log_count = len(runtime.serial.logs)
    base_ms = int(time.time() * 1000)

    first = _event(1, base_ms, 380)
    preview = tracker.multi_target_tracker.update(first)
    stable_track_id = preview.tracks[0].track_id

    for cycle in range(20):
        # MANUAL mode keeps perception identities current while virtual/manual
        # pose changes remain outside the image-space tracker.
        frame_id = cycle * 2 + 2
        current_x = 382 + cycle
        current = _event(frame_id, base_ms + frame_id * 33, current_x)
        tracker.multi_target_tracker.update(current)

        tracker.start_tracking()
        update = tracker.update(current, 640, 360)

        assert update.state in {TrackingState.TRACKING, TrackingState.LOCKED}
        assert update.measurement_status == "NEW"
        assert update.selected_track_id == stable_track_id
        assert update.selected_detection_id == 41
        assert update.selected_target_kind == "balloon"
        assert update.target_center_x == current_x
        assert update.selected_bbox_x == current_x - 24
        assert update.selected_bbox_w == 48
        assert update.selected_target_confidence == 0.93
        assert update.selected_track_occluded is False
        # Each AUTO entry is a bumpless controller handoff.
        assert tracker._derivative_x == 0.0
        assert tracker._derivative_y == 0.0
        assert tracker._integral_x == 0.0
        assert tracker._integral_y == 0.0

        # A duplicate start can arrive through two API surfaces. It must be an
        # idempotent read, not a tracker reset.
        tracker.start_tracking()
        assert tracker.multi_target_tracker.status().tracks[0].track_id == stable_track_id

        stale = _event(frame_id - 1, base_ms + (frame_id - 1) * 33, 90)
        stale_update = tracker.update(stale, 640, 360)
        assert stale_update.measurement_status == "STALE_REJECTED"
        assert stale_update.speed_x == 0
        assert stale_update.speed_y == 0
        assert stale_update.target_center_x == current_x
        assert stale_update.frame_id == frame_id

        tracker.stop_tracking()
        assert tracker.multi_target_tracker.status().tracks[0].track_id == stable_track_id

    status = tracker.multi_target_tracker.status()
    assert status.id_switch_count == 0
    assert status.active_track_count == 1
    # Direct service-level regression harness never starts TrackingLoop and
    # therefore cannot emit motor, Pico or FIRE traffic.
    assert len(runtime.serial.logs) == serial_log_count


def test_short_detection_miss_keeps_selected_track_diagnostics_without_control(client) -> None:
    runtime = client.app.state.runtime
    tracker = runtime.auto_tracker
    serial_log_count = len(runtime.serial.logs)
    base_ms = int(time.time() * 1000)

    visible = _event(100, base_ms, 420)
    tracker.multi_target_tracker.update(visible)
    tracker.start_tracking()
    acquired = tracker.update(visible, 640, 360)
    stable_track_id = acquired.selected_track_id

    missing = _event(101, base_ms + 33, None)
    missed = tracker.update(missing, 640, 360)

    assert missed.state == TrackingState.SEARCHING
    assert missed.speed_x == 0 and missed.speed_y == 0
    assert missed.selected_track_id == stable_track_id
    assert missed.selected_track_occluded is True
    assert missed.selected_track_misses == 1
    assert missed.selected_track_last_seen_at is not None

    reacquired_event = _event(102, base_ms + 66, 423)
    reacquired = tracker.update(reacquired_event, 640, 360)
    assert reacquired.selected_track_id == stable_track_id
    assert reacquired.selected_track_occluded is False
    assert reacquired.target_center_x == 423
    assert tracker.multi_target_tracker.status().id_switch_count == 0
    assert len(runtime.serial.logs) == serial_log_count


def test_camera_and_digital_twin_render_the_same_read_only_tracking_truth() -> None:
    cockpit = (REPO_ROOT / "frontend/src/views/CockpitView.vue").read_text(encoding="utf-8")
    camera = (REPO_ROOT / "frontend/src/components/cockpit/LiveCameraPanel.vue").read_text(encoding="utf-8")
    twin = (REPO_ROOT / "frontend/src/components/digital-twin/DigitalTwinPanel.vue").read_text(encoding="utf-8")

    assert cockpit.count(':tracking-update="motion.trackingUpdate ?? motion.trackingStatus.last_update"') == 2
    assert "IMAGE" in camera and "OBJECT" in camera and "ERROR" in camera
    assert "TRACK ID" in camera and "ID SWITCH" in camera
    assert "selected_track_last_seen_age_ms" in camera
    assert "AUTO VISION SIM · NO ACTUATION" in twin
    assert "autoSelectedDetectionId" in twin
    # The Phase 137 held overlay remains visual-only and cannot be clicked into
    # the tracking/motion/FIRE path.
    assert "if (!target.visual_live) return false" in camera
