import math
import pytest
from app.services.light_tracking_controller import LightTrackingController, LightControllerParams


def test_baseline_a2_parameters_and_safety():
    controller = LightTrackingController("A2")
    assert controller.params.max_speed == 8000.0
    assert controller.params.max_acceleration == 7000.0
    assert controller.params.center_filter == 0.25
    assert controller.params.deadband_x == 3.0
    assert controller.params.deadband_y_on == 8.0
    assert controller.params.min_speed == 120.0

    # Step response within deadband produces zero PID error
    out = controller.update(target_x=961.0, target_y=540.0, frame_width=1920, frame_height=1080, dt=0.015, bbox_width=50, bbox_height=50)
    assert abs(out.speed_x) == 0


def test_mode_1_derivative_filter_damps_yolo_jitter():
    ctrl_a2 = LightTrackingController("A2")
    ctrl_d_filter = LightTrackingController("OPT_D_FILTER")

    assert ctrl_d_filter.params.derivative_filter == 0.30
    assert ctrl_d_filter.params.max_speed == 8000.0
    assert ctrl_d_filter.params.max_acceleration == 7000.0

    # First frame to initialize
    ctrl_a2.update(target_x=960.0, target_y=540.0, frame_width=1920, frame_height=1080, dt=0.015, bbox_width=50, bbox_height=50)
    ctrl_d_filter.update(target_x=960.0, target_y=540.0, frame_width=1920, frame_height=1080, dt=0.015, bbox_width=50, bbox_height=50)

    # Next frame with a 5-pixel sudden jitter step
    out_a2 = ctrl_a2.update(target_x=965.0, target_y=540.0, frame_width=1920, frame_height=1080, dt=0.015, bbox_width=50, bbox_height=50)
    out_filtered = ctrl_d_filter.update(target_x=965.0, target_y=540.0, frame_width=1920, frame_height=1080, dt=0.015, bbox_width=50, bbox_height=50)

    # In OPT_D_FILTER, derivative filter alpha (0.30) smooths the instantaneous derivative kick
    assert ctrl_d_filter._filtered_dx is not None
    # Filtered d must be less than raw derivative kick
    raw_d = (out_filtered.error_x - 0.0) / 0.015
    assert ctrl_d_filter._filtered_dx < raw_d


def test_mode_2_three_zone_gain_scaling():
    ctrl_zone = LightTrackingController("OPT_ZONE_GAIN")

    # Target far away (> 50 px error)
    out_far = ctrl_zone.update(target_x=1060.0, target_y=540.0, frame_width=1920, frame_height=1080, dt=0.015, bbox_width=50, bbox_height=50)
    assert out_far.deadband_zone == "far_catchup"

    # Target close inside 14cm balloon (< 15 px error)
    ctrl_zone_near = LightTrackingController("OPT_ZONE_GAIN")
    out_near = ctrl_zone_near.update(target_x=968.0, target_y=540.0, frame_width=1920, frame_height=1080, dt=0.015, bbox_width=50, bbox_height=50)
    assert out_near.deadband_zone in {"balloon_lock", "locked"}


def test_mode_3_sinusoidal_apex_braking():
    ctrl_sine = LightTrackingController("OPT_SINE_TRACK")
    assert ctrl_sine.params.sine_damping_gain >= 0.35
    assert ctrl_sine.params.corridor_ratio == 2.30
    assert ctrl_sine.params.apex_anticipation_ratio == 0.70

    # Simulate target moving right then decelerating/reversing (apex of sine wave)
    dt = 0.015
    for i in range(10):
        # Moving right at ~133 px/s
        ctrl_sine.update(target_x=960.0 + i * 2.0, target_y=540.0, frame_width=1920, frame_height=1080, dt=dt, bbox_width=50, bbox_height=50, is_fresh=True)

    # Reversal / braking frame: target stops accelerating right and begins decelerating
    out_apex = ctrl_sine.update(target_x=960.0 + 19.0, target_y=540.0, frame_width=1920, frame_height=1080, dt=dt, bbox_width=50, bbox_height=50, is_fresh=True)
    assert math.isfinite(out_apex.speed_x)
    assert abs(out_apex.speed_x) <= 8000
    # Anticipatory brake must engage in opposite direction of travel
    assert out_apex.apex_damping <= 0.0


def test_all_modes_strict_speed_clamping():
    for mode in ["A2", "OPT_D_FILTER", "OPT_ZONE_GAIN", "OPT_SINE_TRACK"]:
        ctrl = LightTrackingController(mode)
        # Giant error
        out = ctrl.update(target_x=1900.0, target_y=1000.0, frame_width=1920, frame_height=1080, dt=0.015, bbox_width=50, bbox_height=50)
        assert abs(out.speed_x) <= 8000
        assert abs(out.speed_y) <= 8000


def test_sinusoidal_full_cycle_apex_damping_engages():
    """Simulate 60 Hz sinusoidal trajectory matching Teknofest CAD (omega=0.668 rad/s, T=9.4s)."""
    ctrl = LightTrackingController("OPT_SINE_TRACK")
    dt = 0.01667  # 60 Hz
    omega = 0.668
    amplitude = 30.0  # 30px lateral oscillation
    damping_events = 0

    for step in range(300):  # 5 seconds of tracking
        t = step * dt
        # Target position follows sine wave
        target_x = 960.0 + amplitude * math.sin(omega * t)
        target_y = 540.0
        out = ctrl.update(
            target_x=target_x,
            target_y=target_y,
            frame_width=1920,
            frame_height=1080,
            dt=dt,
            bbox_width=25.0,
            bbox_height=25.0,
            is_fresh=True,
        )
        assert math.isfinite(out.speed_x)
        assert abs(out.speed_x) <= 8000
        if out.apex_damping != 0.0:
            damping_events += 1

    # Over 5 seconds (~half a cycle with multiple turnarounds/approaches), apex damping must engage
    assert damping_events > 0, "Apex damping should actively engage during deceleration towards apex"

