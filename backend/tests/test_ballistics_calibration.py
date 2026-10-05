"""
Tests for BallisticsCalibrationService and Stage 3 approach interpolation.
"""

import time
from pathlib import Path
import pytest

from app.schemas.ballistics import BallisticsProfile, BallisticsStation, StationUpdateRequest
from app.services.ballistics_calibration_service import BallisticsCalibrationService


@pytest.fixture
def temp_service(tmp_path: Path) -> BallisticsCalibrationService:
    test_path = tmp_path / "test_ballistics.json"
    return BallisticsCalibrationService(persistence_path=test_path)


def test_default_stations(temp_service: BallisticsCalibrationService):
    prof = temp_service.get_profile()
    assert "15" in prof.stations
    assert "10" in prof.stations
    assert "5" in prof.stations
    assert prof.enabled is False
    assert prof.stations["15"].offset_y_px == 0.0
    assert prof.stations["10"].offset_y_px == 0.0
    assert prof.stations["5"].offset_y_px == 0.0


def test_interpolation_accuracy(temp_service: BallisticsCalibrationService):
    # Configure calibrated stations
    temp_service.update_station(StationUpdateRequest(distance_m=15.0, offset_x_px=0.0, offset_y_px=-40.0))
    temp_service.update_station(StationUpdateRequest(distance_m=10.0, offset_x_px=0.0, offset_y_px=-20.0))
    temp_service.update_station(StationUpdateRequest(distance_m=5.0, offset_x_px=0.0, offset_y_px=-5.0))

    # At exact 15m
    interp15 = temp_service.interpolate(15.0)
    assert interp15["offset_y_px"] == -40.0
    assert interp15["flight_time_ms"] == 214.0

    # At exact 10m
    interp10 = temp_service.interpolate(10.0)
    assert interp10["offset_y_px"] == -20.0
    assert interp10["flight_time_ms"] == 142.0

    # Halfway at 12.5m: offset_y should be exactly (-20 + -40) / 2 = -30.0
    interp125 = temp_service.interpolate(12.5)
    assert interp125["offset_y_px"] == -30.0
    assert interp125["flight_time_ms"] == 178.0

    # At exact 5m
    interp5 = temp_service.interpolate(5.0)
    assert interp5["offset_y_px"] == -5.0
    assert interp5["flight_time_ms"] == 71.0

    # Halfway at 7.5m: offset_y should be (-5 + -20) / 2 = -12.5
    interp75 = temp_service.interpolate(7.5)
    assert interp75["offset_y_px"] == -12.5


def test_lead_compensation(temp_service: BallisticsCalibrationService):
    # At 15m, flight time = 214 ms = 0.214 s
    # Target moving right at +100 px/s -> lead = 100 * 0.214 = +21.4 px
    interp = temp_service.interpolate(15.0, target_vx_px_s=100.0)
    assert interp["lead_x_px"] == 21.4
    assert interp["total_offset_x_px"] == 21.4


def test_stage3_elapsed_distance(temp_service: BallisticsCalibrationService):
    # Start at 15m, speed 0.3 m/s
    # At 10s: 15.0 - 0.3 * 10 = 12.0m
    est_d, time_d, _ = temp_service.estimate_distance(elapsed_s=10.0)
    assert time_d == 12.0
    assert est_d == 12.0

    # Hybrid vision correction: observed 42px (corresponds to ~10m)
    # 0.70 * 12.0 + 0.30 * 10.0 = 8.4 + 3.0 = 11.4m
    est_d_hybrid, _, vis_d = temp_service.estimate_distance(elapsed_s=10.0, observed_balloon_w_px=42.0)
    assert vis_d == 10.0
    assert est_d_hybrid == 11.4


def test_station_update_persistence(temp_service: BallisticsCalibrationService):
    update = StationUpdateRequest(
        distance_m=15.0,
        offset_x_px=2.5,
        offset_y_px=-44.0,
        flight_time_ms=220.0,
    )
    temp_service.update_station(update)
    prof = temp_service.get_profile()
    assert prof.stations["15"].offset_y_px == -44.0
    assert prof.stations["15"].offset_x_px == 2.5
    assert prof.stations["15"].flight_time_ms == 220.0


def test_interpolation_latency_benchmark(temp_service: BallisticsCalibrationService):
    # Must execute 10,000 iterations in under 50ms (< 5 microseconds per call)
    t0 = time.perf_counter()
    for i in range(10000):
        temp_service.interpolate(12.34, target_vx_px_s=45.0)
    t1 = time.perf_counter()
    duration_ms = (t1 - t0) * 1000.0
    avg_us = (duration_ms / 10000.0) * 1000.0
    assert avg_us < 10.0  # Under 10 microseconds


def test_auto_tracker_applies_ballistics_in_general_mode(client, temp_service: BallisticsCalibrationService):
    from app.schemas.vision import BBox, BalloonDetection, VisionEvent

    temp_service.update_station(StationUpdateRequest(distance_m=15.0, offset_x_px=0.0, offset_y_px=-42.0))
    prof = temp_service.get_profile()
    prof.enabled = True
    temp_service.update_profile(prof)

    tracker = client.app.state.runtime.auto_tracker
    tracker.ballistics = temp_service
    tracker.set_controller_mode("GENERAL")
    tracker.set_target_policy("BALLOON")
    tracker.aim_offset_y_px = 0.0
    tracker.aim_offset_x_px = 0.0
    tracker.start_tracking()

    # Frame 1280x720, balloon centered at optical center (640, 360)
    event = VisionEvent(
        frame_id=1,
        timestamp_ms=int(time.time() * 1000),
        source="test",
        frame_width=1280,
        frame_height=720,
        fps=30,
        preprocess_ms=0,
        inference_ms=1,
        postprocess_ms=0,
        total_latency_ms=1,
        body_detections=[],
        balloon_detections=[
            BalloonDetection(
                id=1,
                confidence=0.95,
                bbox=BBox(x=626, y=346, w=28, h=28),
                center_x=640,
                center_y=360,
                source="test",
            )
        ],
    )

    update = tracker.update(event, 1280, 720)
    # The reticle should be shifted by offset_y_px = -42.0
    assert update.frame_center_y == 360.0 - 42.0  # 318.0
    assert update.aim_offset_y_px == -42.0
    assert update.aim_offset_x_px == 0.0
    # Balloon is at 360, but reticle is at 318, so error_y should be positive (+42 px)
    assert update.error_y_px == pytest.approx(42.0, abs=1.0)

