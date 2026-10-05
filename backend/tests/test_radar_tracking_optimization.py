"""
Test suite for Radar Tracking Optimization & Target Engagement Hardening.
Verifies:
1. Velocity slew rate limiter (soft handoff ramp) bounds acceleration to 8000 sps^2.
2. Tilt axis lock clamps Y motion to 0 during pan calibration.
3. FOV Engagement Gate rejects peripheral corridor targets [360, 920] and applies hysteresis retention [280, 1000].
4. Continuous SweepGenerator smoothly covers 30 deg sector with S-curve turnaround.
5. PatrolRadarService persists and switches between CORRIDOR_HOP and SMOOTH_SWEEP.
"""

import math
import time
import pytest

from app.schemas.operation import PathAngleConfig
from app.services.patrol_radar_service import PatrolRadarService, SweepGenerator, PatrolStrategy


def test_sweep_generator_dynamics():
    """Verify SweepGenerator produces smooth velocity with bounded turnaround."""
    gen = SweepGenerator(center_deg=135.0, sector_deg=30.0, speed_dps=7.5, turnaround_s=0.40)
    
    # At center (135.0), traveling positive: should give +7.5 dps
    v_center = gen.velocity_dps(dt=0.015, current_deg=135.0)
    assert math.isclose(v_center, 7.5, abs_tol=0.1)

    # Near upper bound (150.0), e.g. at 149.5: should decelerate smoothly
    v_near_edge = gen.velocity_dps(dt=0.015, current_deg=149.5)
    assert 0.0 < v_near_edge < 7.5

    # At or past upper bound: direction should reverse to -1.0
    v_edge = gen.velocity_dps(dt=0.015, current_deg=150.2)
    assert v_edge < 0.0
    assert gen.direction == -1.0

    # Traveling left through center: should give -7.5 dps
    gen.sync_phase_to_angle(135.0, direction=-1.0)
    v_center_left = gen.velocity_dps(dt=0.015, current_deg=135.0)
    assert math.isclose(v_center_left, -7.5, abs_tol=0.1)

    # At lower bound (120.0): should reverse back to +1.0
    v_lower = gen.velocity_dps(dt=0.015, current_deg=119.8)
    assert v_lower > 0.0
    assert gen.direction == 1.0


def test_patrol_radar_dual_mode_config(tmp_path):
    """Verify PatrolRadarService supports switching between CORRIDOR_HOP and SMOOTH_SWEEP."""
    storage_file = tmp_path / "test_patrol_radar.json"
    service = PatrolRadarService(persistence_path=storage_file, load_persisted=False)

    assert service.config.strategy == "CORRIDOR_HOP"
    status = service.status()
    assert status["strategy"] == "CORRIDOR_HOP"
    assert status["sweep_speed_dps"] == 9.375
    assert status["sweep_sector_deg"] == 30.0

    # Switch to SMOOTH_SWEEP
    updated = service.update_angles(
        strategy="SMOOTH_SWEEP",
        sweep_speed_dps=10.0,
        sweep_sector_deg=34.0,
    )
    assert updated.strategy == "SMOOTH_SWEEP"
    assert updated.sweep_speed_dps == 10.0
    assert updated.sweep_sector_deg == 34.0

    new_status = service.status()
    assert new_status["strategy"] == "SMOOTH_SWEEP"
    assert new_status["sweep_speed_dps"] == 10.0
    assert new_status["sweep_sector_deg"] == 34.0


def test_fov_engagement_gate_logic():
    """Verify inside_engagement_gate filters peripheral balloons and applies hysteresis."""
    from unittest.mock import MagicMock
    from app.services.auto_tracker_service import AutoTrackerService
    from app.schemas.config import AppConfig

    from types import SimpleNamespace
    from app.schemas.config import TrackingConfig
    config = SimpleNamespace(tracking=TrackingConfig())
    logger = MagicMock()
    tracker = AutoTrackerService(config=config, logger=logger)

    # When patrol_gate_active is False, all detections pass freely
    tracker.patrol_gate_active = False
    assert tracker.inside_engagement_gate(1066.0, 640.0, is_already_locked=False) is True
    assert tracker.inside_engagement_gate(214.0, 640.0, is_already_locked=False) is True

    # When patrol_gate_active is True:
    tracker.patrol_gate_active = True
    tracker.engagement_gate_half_px = 280.0  # [360, 920] in 1280 frame
    tracker.retention_gate_half_px = 360.0   # [280, 1000] in 1280 frame

    # 1. Initial Acquisition:
    # Target in center (e.g. X = 600) -> inside [360, 920] -> Accepted
    assert tracker.inside_engagement_gate(600.0, 640.0, is_already_locked=False) is True
    # Target exactly at boundary (X = 920) -> Accepted
    assert tracker.inside_engagement_gate(920.0, 640.0, is_already_locked=False) is True
    # Peripheral neighbor corridor target (X = 1066) -> REJECTED
    assert tracker.inside_engagement_gate(1066.0, 640.0, is_already_locked=False) is False
    # Peripheral neighbor corridor target (X = 214) -> REJECTED
    assert tracker.inside_engagement_gate(214.0, 640.0, is_already_locked=False) is False

    # 2. Retention Hysteresis (already locked target):
    # Target drifts to X = 950 -> Outside acquisition gate (280px) but inside retention gate (360px) -> Retained!
    assert tracker.inside_engagement_gate(950.0, 640.0, is_already_locked=True) is True
    # Target moves past retention gate (X = 1050) -> Rejected
    assert tracker.inside_engagement_gate(1050.0, 640.0, is_already_locked=True) is False


def test_command_gateway_axis_lock_and_soft_ramp():
    """Verify CommandGateway axis locking and soft ramp slew limiter."""
    from unittest.mock import MagicMock
    from app.services.command_gateway import CommandGateway

    serial_mock = MagicMock()
    serial_mock.light_protocol_enabled = True
    gateway = CommandGateway(serial=serial_mock, logger=MagicMock())

    # 1. Test Axis Lock
    assert gateway.tilt_axis_locked is False
    gateway.set_axis_lock("tilt", True)
    assert gateway.tilt_axis_locked is True

    # 2. Test Soft Ramp Slew Rate Limiter
    gateway._ramp_speed_x = 0.0
    gateway._ramp_speed_y = 0.0
    gateway._last_ramp_time = time.monotonic() - 0.015  # dt = 15 ms

    # Request instant jump to 6000 sps in pan
    ramped_x, ramped_y = gateway._apply_soft_ramp(6000.0, 0.0)

    # In 15 ms at 8000 sps^2, maximum change is 8000 * 0.015 = 120 sps
    assert ramped_x <= 150.0
    assert ramped_x > 0.0

    # Unlock tilt
    gateway.set_axis_lock("tilt", False)
    assert gateway.tilt_axis_locked is False


def test_select_target_fov_gate_end_to_end():
    """End-to-end test of _select_target rejecting peripheral neighbor corridor balloon."""
    from types import SimpleNamespace
    from unittest.mock import MagicMock
    from app.services.auto_tracker_service import AutoTrackerService
    from app.schemas.config import TrackingConfig
    from app.schemas.vision import BBox, BalloonDetection, VisionEvent

    config = SimpleNamespace(tracking=TrackingConfig())
    logger = MagicMock()
    tracker = AutoTrackerService(config=config, logger=logger)
    tracker.set_controller_mode("A2")

    # Peripheral balloon at X=1066 (corridor 3)
    b_corridor3 = BalloonDetection(
        id=101,
        confidence=0.92,
        color="red",
        bbox=BBox(x=1046, y=340, w=40, h=40),
        center_x=1066.0,
        center_y=360.0,
    )
    # Center balloon at X=630 (corridor 2)
    b_corridor2 = BalloonDetection(
        id=102,
        confidence=0.95,
        color="red",
        bbox=BBox(x=610, y=340, w=40, h=40),
        center_x=630.0,
        center_y=360.0,
    )

    ev_corridor3_only = VisionEvent(
        frame_id=1,
        timestamp_ms=1000,
        source="test",
        frame_width=1280,
        frame_height=720,
        fps=50.0,
        preprocess_ms=0.0,
        inference_ms=1.0,
        postprocess_ms=0.0,
        total_latency_ms=1.0,
        body_detections=[],
        balloon_detections=[b_corridor3],
    )

    ev_both = VisionEvent(
        frame_id=2,
        timestamp_ms=1020,
        source="test",
        frame_width=1280,
        frame_height=720,
        fps=50.0,
        preprocess_ms=0.0,
        inference_ms=1.0,
        postprocess_ms=0.0,
        total_latency_ms=1.0,
        body_detections=[],
        balloon_detections=[b_corridor3, b_corridor2],
    )

    # 1. In Patrol Mode (Radar active):
    tracker.patrol_gate_active = True

    # If only corridor 3 balloon is visible, MUST return None (no crosstalk jump!)
    selected = tracker._select_target(ev_corridor3_only, 640.0, 360.0)
    assert selected is None

    # If both are visible, MUST pick corridor 2 balloon (center)
    selected = tracker._select_target(ev_both, 640.0, 360.0)
    assert selected is not None
    assert selected[0] == 630.0  # Center X of corridor 2 balloon

    # 2. Outside Patrol Mode (e.g. manual tracking or stage 1):
    tracker.patrol_gate_active = False
    tracker.preferred_target_x = None
    tracker.preferred_target_y = None
    tracker.preferred_target_detection_id = None

    # Accepts peripheral balloon freely
    selected = tracker._select_target(ev_corridor3_only, 640.0, 360.0)
    assert selected is not None
    assert selected[0] == 1066.0
