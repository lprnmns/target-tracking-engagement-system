import math
import time
from unittest.mock import MagicMock
from app.schemas.vision import BBox, BalloonDetection, BodyDetection, VisionEvent
from app.schemas.tracking import TrackingUpdate
from app.services.tracking_loop import TrackingLoop, FIRE_INNER_BBOX_RATIO


def _make_loop():
    auto_tracker = MagicMock()
    auto_tracker.controller_mode = "A3"
    vision_pipeline = MagicMock()
    serial = MagicMock()
    serial.light_protocol_enabled = True
    serial.magazine_remaining = 30
    gateway = MagicMock()
    gateway.serial = serial
    gateway.runtime = None
    logger = MagicMock()
    return TrackingLoop(
        auto_tracker=auto_tracker,
        vision_pipeline=vision_pipeline,
        serial=serial,
        gateway=gateway,
        logger=logger,
    )


def _make_event(balloons=None, bodies=None):
    return VisionEvent(
        frame_id=1,
        timestamp_ms=1000,
        source="test",
        fps=30.0,
        preprocess_ms=1.0,
        inference_ms=10.0,
        postprocess_ms=1.0,
        total_latency_ms=12.0,
        balloon_detections=balloons or [],
        body_detections=bodies or [],
    )


def test_directional_fire_gate_blocks_trailing_crosshair():
    loop = _make_loop()
    
    # Target moving right (vx = +20 px/s)
    # Crosshair at (960, 540)
    # Target at (975, 540) -> crosshair is at 960 (15 px to the LEFT = TRAILING behind right-moving target)
    update = TrackingUpdate(
        frame_center_x=960.0,
        frame_center_y=540.0,
        target_center_x=975.0,
        target_center_y=540.0,
        target_velocity_x_px_s=20.0,
        target_velocity_y_px_s=0.0,
    )
    balloon = BalloonDetection(
        id=1,
        color="red",
        confidence=0.95,
        center_x=975.0,
        center_y=540.0,
        bbox=BBox(x=955.0, y=520.0, w=40.0, h=40.0),
    )
    vision_event = _make_event(balloons=[balloon])
    
    loop._update_fire_zone(vision_event, update, 1920, 1080)
    # Since crosshair is trailing behind the moving target, fire candidate must NOT be active
    assert loop.fire_target_frames == 0
    assert not loop._fire_candidate_active


def test_directional_fire_gate_allows_leading_or_centered_crosshair():
    loop = _make_loop()
    
    # Target moving right (vx = +20 px/s)
    # Crosshair at (960, 540)
    # Target at (955, 540) -> crosshair is at 960 (5 px to the RIGHT = LEADING/AHEAD of right-moving target)
    update = TrackingUpdate(
        frame_center_x=960.0,
        frame_center_y=540.0,
        target_center_x=955.0,
        target_center_y=540.0,
        target_velocity_x_px_s=20.0,
        target_velocity_y_px_s=0.0,
    )
    balloon = BalloonDetection(
        id=2,
        color="red",
        confidence=0.95,
        center_x=955.0,
        center_y=540.0,
        bbox=BBox(x=935.0, y=520.0, w=40.0, h=40.0),
    )
    vision_event = _make_event(balloons=[balloon])
    
    loop._update_fire_zone(vision_event, update, 1920, 1080)
    # Crosshair is within inner radius AND leading the target -> lock frames must increment
    assert loop.fire_target_frames == 1


def test_directional_fire_gate_allows_apex_stop():
    loop = _make_loop()
    
    # Target at apex turn (vx = 1.0 px/s, speed < 5 px/s)
    # Distance is 12 px (within 14-18 px inner radius)
    update = TrackingUpdate(
        frame_center_x=960.0,
        frame_center_y=540.0,
        target_center_x=972.0,
        target_center_y=540.0,
        target_velocity_x_px_s=1.0,
        target_velocity_y_px_s=0.0,
    )
    balloon = BalloonDetection(
        id=3,
        color="red",
        confidence=0.95,
        center_x=972.0,
        center_y=540.0,
        bbox=BBox(x=952.0, y=520.0, w=40.0, h=40.0),
    )
    vision_event = _make_event(balloons=[balloon])
    
    loop._update_fire_zone(vision_event, update, 1920, 1080)
    # At apex turn (speed < 5), directional gate allows firing
    assert loop.fire_target_frames == 1


def test_friendly_shield_hysteresis_blocks_fire():
    loop = _make_loop()
    
    # Frame 1: Friendly helicopter seen at (960, 480) with w=100, h=40
    friendly_body = BodyDetection(
        id=101,
        class_id=1,
        class_name="helicopter",
        target_type="helicopter",
        target_team="dost",
        confidence=0.9,
        bbox=BBox(x=910.0, y=460.0, w=100.0, h=40.0),
        center_x=960.0,
        center_y=480.0,
    )
    # Target balloon is right under the helicopter at (960, 530)
    balloon = BalloonDetection(
        id=4,
        color="blue",
        confidence=0.95,
        center_x=960.0,
        center_y=530.0,
        bbox=BBox(x=940.0, y=510.0, w=40.0, h=40.0),
    )
    event1 = _make_event(balloons=[balloon], bodies=[friendly_body])
    
    update = TrackingUpdate(
        frame_center_x=960.0,
        frame_center_y=530.0,
        target_center_x=960.0,
        target_center_y=530.0,
        target_velocity_x_px_s=0.0,
        target_velocity_y_px_s=0.0,
    )
    loop._update_fire_zone(event1, update, 1920, 1080)
    assert loop.fire_target_frames == 0
    
    # Frame 2: Friendly helicopter detection FLICKERED OUT (temporarily absent in vision_event)
    event2_flicker = _make_event(balloons=[balloon], bodies=[])
    loop._update_fire_zone(event2_flicker, update, 1920, 1080)
    # The 0.50s hysteresis shield must STILL BLOCK THE FIRE!
    assert loop.fire_target_frames == 0
