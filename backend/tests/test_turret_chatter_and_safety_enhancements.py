import math
import time
from app.schemas.config import AngularSafetyZone
from app.services.patrol_radar_service import SweepGenerator
from app.services.safety_zone_service import ExclusionZoneManager


def test_dead_reckoning_and_stopping_distance():
    STEPS_PER_DEG = 133.333
    HOME_PAN_DEG = 135.0
    MAX_RETURN_SPEED_SPS = 6000.0
    MAX_RETURN_ACCEL_SPS2 = 12000.0
    HOME_TOLERANCE_DEG = 0.08

    estimated_pan_deg = 150.0  # Started 15 degrees off-center
    current_speed_sps = 0.0
    dt = 0.015

    trajectory = []
    for step in range(200):
        error_deg = HOME_PAN_DEG - estimated_pan_deg
        max_dv = MAX_RETURN_ACCEL_SPS2 * dt

        if abs(error_deg) < HOME_TOLERANCE_DEG and abs(current_speed_sps) <= max_dv:
            current_speed_sps = 0.0
            trajectory.append((step * dt, estimated_pan_deg, 0.0))
            break

        error_steps = error_deg * STEPS_PER_DEG
        v_stop = math.sqrt(2.0 * MAX_RETURN_ACCEL_SPS2 * max(0.0, abs(error_steps)))
        v_target = math.copysign(min(MAX_RETURN_SPEED_SPS, v_stop), error_steps)

        dv = max(-max_dv, min(max_dv, v_target - current_speed_sps))
        current_speed_sps += dv

        deg_delta = (current_speed_sps * dt) / STEPS_PER_DEG
        estimated_pan_deg += deg_delta
        trajectory.append((step * dt, estimated_pan_deg, current_speed_sps))

    final_time, final_pos, final_speed = trajectory[-1]
    assert abs(final_pos - HOME_PAN_DEG) <= HOME_TOLERANCE_DEG, f"Position {final_pos} not at home {HOME_PAN_DEG}"
    assert final_speed == 0.0, "Final speed not zero"
    for i in range(1, len(trajectory)):
        spd_prev = trajectory[i - 1][2]
        spd_curr = trajectory[i][2]
        assert abs(spd_curr - spd_prev) <= (MAX_RETURN_ACCEL_SPS2 * dt + 1e-3)


def test_sinusoidal_sweep_kinematics():
    gen = SweepGenerator(center_deg=135.0, sector_deg=30.0, speed_dps=7.5)
    assert gen.span_deg == 15.0
    assert gen.peak_speed_dps <= 9.375

    dt = 0.015
    speeds = []
    positions = []
    for _ in range(700):
        v = gen.velocity_dps(dt)
        pos = gen.reference_angle_deg()
        speeds.append(v)
        positions.append(pos)

    assert min(positions) >= 120.0 - 0.1
    assert max(positions) <= 150.0 + 0.1
    assert max(abs(s) for s in speeds) <= 9.375 + 0.1


def test_raised_cosine_handoff():
    gen = SweepGenerator(center_deg=135.0, sector_deg=30.0, speed_dps=7.5)
    dt = 0.015
    blend_time = 0.20
    pid_speed = 3000.0

    steps = int(blend_time / dt) + 2
    handoff_speeds = []
    for _ in range(steps):
        s = gen.handoff_speed_sps(pid_speed, dt=dt, blend_time_s=blend_time)
        handoff_speeds.append(s)

    assert handoff_speeds[-1] == int(pid_speed)
    assert len(handoff_speeds) > 5


def test_exclusion_zone_manager():
    mgr = ExclusionZoneManager()
    zone = AngularSafetyZone(
        name="test_restricted",
        pan_min_deg=110.0,
        pan_max_deg=125.0,
        tilt_min_deg=20.0,
        tilt_max_deg=40.0,
        enabled=True,
    )
    mgr.update_from_angular_zones([zone])

    t0 = 1000.0
    allowed = mgr.is_trigger_allowed(pan_deg=115.0, tilt_deg=30.0, target_x_px=640, target_y_px=360, now=t0)
    assert allowed is False
    assert mgr._last_block_reason == "ANGULAR_ZONE_HIT"

    allowed = mgr.is_trigger_allowed(pan_deg=135.0, tilt_deg=30.0, target_x_px=640, target_y_px=360, now=t0 + 1.0)
    assert allowed is True

    mgr.set_screen_zones([[(100.0, 100.0), (300.0, 100.0), (300.0, 300.0), (100.0, 300.0)]])
    allowed = mgr.is_trigger_allowed(pan_deg=135.0, tilt_deg=30.0, target_x_px=200, target_y_px=200, now=t0 + 2.0)
    assert allowed is False

    allowed = mgr.is_trigger_allowed(
        pan_deg=135.0, tilt_deg=30.0,
        target_x_px=50.0, target_y_px=200.0,
        vx_px_s=400.0, vy_px_s=0.0,
        now=t0 + 4.0,
    )
    assert allowed is False, "Fast target approaching forbidden zone must be blocked by lookahead!"

    allowed_soon = mgr.is_trigger_allowed(
        pan_deg=135.0, tilt_deg=30.0,
        target_x_px=500.0, target_y_px=500.0,
        now=t0 + 4.2,
    )
    assert allowed_soon is False, "Must remain blocked during 0.5s debounce period"

    allowed_later = mgr.is_trigger_allowed(
        pan_deg=135.0, tilt_deg=30.0,
        target_x_px=500.0, target_y_px=500.0,
        now=t0 + 4.8,
    )
    assert allowed_later is True, "Must release trigger once debounce expires"


def test_stage2_smooth_sweep_immediate_scan():
    """Verify that in Stage 2/3, SMOOTH_SWEEP is active and sweeps immediately when no target is seen."""
    from app.services.patrol_radar_service import PatrolRadarService
    from app.schemas.operation import PathAngleConfig

    radar = PatrolRadarService(PathAngleConfig(strategy="SMOOTH_SWEEP", sweep_sector_deg=30.0, sweep_speed_dps=9.375))
    res = radar.start_patrol(force_reset=True, strategy="SMOOTH_SWEEP")

    assert res["state"] in {"SLEWING_TO_CENTER", "SWEEPING"}
    assert res["strategy"] == "SMOOTH_SWEEP"
    assert res["patrol_enabled"] is True

    # Once centered, transitions to SWEEPING
    radar.notify_centered()

    # No target visible (e.g. camera covered by hand):
    st = radar.tick(has_active_target=False)
    assert st["state"] == "SWEEPING"
    assert radar.is_patrolling is True

    # Sweep generator produces valid oscillating velocities
    v1 = radar.sweep_generator.velocity_dps(dt=0.015, current_deg=135.0)
    assert abs(v1) > 0.0
    assert abs(v1) <= 9.375 + 1e-3


def test_sweep_generator_sync_phase():
    """Verify that sync_phase_to_angle aligns phase with actual physical angle."""
    gen = SweepGenerator(center_deg=135.0, sector_deg=30.0, speed_dps=9.375)
    # Sync at 135.0 (center) -> phase should be 0
    gen.sync_phase_to_angle(135.0, direction=1.0)
    assert abs(gen.reference_angle_deg() - 135.0) < 1e-4

    # Sync at 150.0 (top peak) -> phase should be pi/2
    gen.sync_phase_to_angle(150.0, direction=1.0)
    assert abs(gen.reference_angle_deg() - 150.0) < 1e-4

    # Sync at 120.0 (bottom trough) -> phase should be 3pi/2
    gen.sync_phase_to_angle(120.0, direction=1.0)
    assert abs(gen.reference_angle_deg() - 120.0) < 1e-4

