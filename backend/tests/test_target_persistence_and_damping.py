import math
import time
import pytest
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.schemas.config import TrackingConfig
from app.schemas.vision import BalloonDetection, BBox, VisionEvent
from app.services.auto_tracker_service import AutoTrackerService
from app.services.light_tracking_controller import LightTrackingController
from app.services.tracking_loop import TrackingLoop


def test_light_tracking_controller_stopping_distance_and_deadband():
    """Verify that LightTrackingController avoids chatter near zero error and applies smooth damping."""
    controller = LightTrackingController(mode="A2")

    # 1. Target exactly in center (error = 0)
    out = controller.update(
        target_x=320.0,
        target_y=180.0,
        frame_width=640,
        frame_height=360,
        dt=0.015,
        bbox_width=40.0,
        bbox_height=40.0,
    )
    assert out.speed_x == 0
    assert out.speed_y == 0
    assert out.locked is True

    # 2. Target inside deadband (e.g. error = 2.0 px, where deadband is >= 3.5 px)
    # Crucial: Must NOT jump to min_speed (120 sps)!
    out_near = controller.update(
        target_x=322.0,
        target_y=180.0,
        frame_width=640,
        frame_height=360,
        dt=0.015,
        bbox_width=40.0,
        bbox_height=40.0,
    )
    assert abs(out_near.speed_x) == 0, f"Speed should be 0 inside deadband, got {out_near.speed_x}"

    # 3. Target outside deadband (e.g. target_x = 345, error ~ 25px)
    # Slew rate limits acceleration: max_accel * dt = 7000 * 0.015 = 105 sps
    out_err = None
    for _ in range(5):
        out_err = controller.update(
            target_x=345.0,
            target_y=180.0,
            frame_width=640,
            frame_height=360,
            dt=0.015,
            bbox_width=40.0,
            bbox_height=40.0,
        )
    assert out_err is not None
    assert abs(out_err.speed_x) > 0, "Speed should be nonzero for 25px error"
    assert abs(out_err.speed_x) <= 2000, f"Speed should be smooth and rate-limited, got {out_err.speed_x}"


def test_auto_tracker_anti_ping_pong_and_target_coasting():
    """Verify that a 1-frame drop does not ping-pong to another target, and switching is debounced."""
    config = SimpleNamespace(tracking=TrackingConfig())
    logger = SimpleNamespace(emit=lambda *args, **kwargs: None)
    tracker = AutoTrackerService(config=config, logger=logger)
    tracker.set_controller_mode("A2")
    tracker.target_switch_grace_s = 1.0
    tracker.target_switch_debounce_frames = 3

    balloon_a = BalloonDetection(
        id=1,
        center_x=320.0,
        center_y=180.0,
        bbox=BBox(x=300.0, y=160.0, w=40.0, h=40.0),
        confidence=0.90,
        color="red",
    )
    balloon_b = BalloonDetection(
        id=2,
        center_x=550.0,
        center_y=180.0,
        bbox=BBox(x=530.0, y=160.0, w=40.0, h=40.0),
        confidence=0.88,
        color="red",
    )

    event_both = VisionEvent(
        frame_id=1,
        timestamp_ms=1000,
        source="test",
        frame_width=640,
        frame_height=360,
        fps=30.0,
        preprocess_ms=1.0,
        inference_ms=10.0,
        postprocess_ms=1.0,
        total_latency_ms=12.0,
        body_detections=[],
        balloon_detections=[balloon_a, balloon_b],
    )

    # Frame 1: Initial acquisition - locks on balloon_a (center of frame)
    out1 = tracker._select_target(event_both, 320.0, 180.0)
    assert out1 is not None
    assert out1[0] == 320.0
    assert tracker.preferred_target_detection_id == 1

    # Frame 2: Balloon A momentarily drops (detector miss). Only Balloon B is visible.
    # CRITICAL: During grace period (1.0s), system must COAST on Balloon A's coordinates and NOT flip to Balloon B!
    event_only_b = VisionEvent(
        frame_id=2,
        timestamp_ms=1033,
        source="test",
        frame_width=640,
        frame_height=360,
        fps=30.0,
        preprocess_ms=1.0,
        inference_ms=10.0,
        postprocess_ms=1.0,
        total_latency_ms=12.0,
        body_detections=[],
        balloon_detections=[balloon_b],
    )
    out2 = tracker._select_target(event_only_b, 320.0, 180.0)
    assert out2 is not None
    assert out2[0] == 320.0, f"Expected coasting on Balloon A (320.0), got {out2[0]}"
    assert tracker.preferred_target_detection_id == 1, "Lock on A must be retained during coasting"

    # Now simulate that target grace period has expired (e.g. Balloon A was destroyed or gone for > 1.0s)
    tracker._locked_target_last_seen_at = time.monotonic() - 1.5

    # Frame 3: Candidate Balloon B appears -> Debounce frame 1, returns previous coords
    out3 = tracker._select_target(event_only_b, 320.0, 180.0)
    assert out3[0] == 320.0, "Debounce frame 1: should hold coords"

    # Frame 4: Candidate Balloon B appears -> Debounce frame 2, returns previous coords
    out4 = tracker._select_target(event_only_b, 320.0, 180.0)
    assert out4[0] == 320.0, "Debounce frame 2: should hold coords"

    # Frame 5: Candidate Balloon B appears -> Debounce frame 3 satisfied! Now commits to Balloon B
    out5 = tracker._select_target(event_only_b, 320.0, 180.0)
    assert out5[0] == 550.0, f"Expected switch to Balloon B after 3 debounced frames, got {out5[0]}"
    assert tracker.preferred_target_detection_id == 2


def test_homing_calibrates_dead_reckoning_at_rest():
    """Verify that _calibrate_dead_reckoning sets ground truth when axis is at rest."""
    tl = TrackingLoop(
        auto_tracker=MagicMock(),
        vision_pipeline=MagicMock(),
        serial=MagicMock(),
        gateway=MagicMock(),
        logger=MagicMock(),
    )

    # Initially at rest with speed == 0
    tl._current_pan_speed_sps = 0.0
    tl._dead_reckon_pan_deg = 0.0  # simulated wrong position after limit switch

    # Calibrate to 135.0
    tl._calibrate_dead_reckoning(135.0)
    assert tl._dead_reckon_pan_deg == 135.0

    # If axis is moving fast (|speed| >= 50), calibration must refuse to avoid race conditions
    tl._current_pan_speed_sps = 500.0
    tl._calibrate_dead_reckoning(140.0)
    assert tl._dead_reckon_pan_deg == 135.0, "Should refuse calibration while moving fast"
