from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace
from pathlib import Path

import pytest

from app.schemas.tracking import TrackingConfigUpdate, TrackingState, TrackingUpdate
from app.schemas.vision import BBox, BalloonDetection, VisionEvent
from app.services.light_tracking_controller import LightControllerParams, LightTrackingController


def _balloon_event(frame_id: int = 1, x: int = 250, y: int = 200) -> VisionEvent:
    return VisionEvent(
        frame_id=frame_id,
        timestamp_ms=int(time.time() * 1000) + frame_id * 33,
        source="light-transfer-test",
        frame_width=640,
        frame_height=480,
        fps=30,
        preprocess_ms=0,
        inference_ms=1,
        postprocess_ms=0,
        total_latency_ms=1,
        body_detections=[],
        balloon_detections=[
            BalloonDetection(
                id=7,
                confidence=0.99,
                bbox=BBox(x=x, y=y, w=100, h=100),
                center_x=x + 50,
                center_y=y + 50,
                source="light-transfer-test",
            )
        ],
    )


def test_light_a2_json_pid_and_raw_axis_parity() -> None:
    controller = LightTrackingController("A2")

    first = controller.update(300, 250, 640, 480, 0.1, 100, 100)
    second = controller.update(300, 250, 640, 480, 0.1, 100, 100)

    # A2 JSON: kp_x=11, kd_x=1.1, kp_y=8, kd_y=2.0.  Derivative kick fix:
    # ilk kare tohumlanır (D=0) → yalnızca P terimi. Eski davranış -440/+280
    # darbesiydi (kd × Δe/dt, previous_error=0 tuzağı).
    assert first.raw_speed_y == -220.0
    assert first.raw_speed_x == 120.0
    assert (first.speed_x, first.speed_y) == (-220, -120)
    assert (second.speed_x, second.speed_y) == (-220, -120)


def test_light_a3_uses_ema_champion_filter_and_a3_min_speed() -> None:
    controller = LightTrackingController("A3")

    centered = controller.update(320, 240, 640, 480, 0.1, 40, 40)
    offset = controller.update(340, 240, 640, 480, 0.1, 40, 40)

    assert centered.using_kalman is False
    assert centered.locked is True
    assert offset.filtered_x < 340
    assert offset.speed_x >= 70


def test_light_a3_scalar_kalman_matches_reference_updates() -> None:
    controller = LightTrackingController("A3")
    controller.params = LightControllerParams(**{**controller.params.__dict__, "use_kalman_filter": True})

    controller.update(320, 240, 640, 480, 0.1, 40, 40)
    second = controller.update(330, 240, 640, 480, 0.1, 40, 40)
    third = controller.update(350, 250, 640, 480, 0.1, 40, 40)

    # These values are the checked-in asama3_otonom.py _kalman_guncelle()
    # sequence with kalman_q=0.05 and kalman_r=2.50. The scalar form must
    # stay equivalent to its diagonal 4D matrix implementation.
    assert second.using_kalman is True
    assert abs(second.filtered_x - 329.9507437494813) < 1e-9
    assert abs(third.filtered_x - 345.03128655682417) < 1e-9
    assert abs(third.filtered_y - 243.7547548362724) < 1e-9


def test_tracking_loop_maps_stage_and_policy_to_light_mode() -> None:
    from app.services.tracking_loop import TrackingLoop

    assert TrackingLoop._controller_mode_for_stage("stage2", "BALLOON_AIRCRAFT") == "A2"
    assert TrackingLoop._controller_mode_for_stage("stage3", "BALLOON") == "A3"
    assert TrackingLoop._controller_mode_for_stage("stage1", "BALLOON") == "A2"
    assert TrackingLoop._controller_mode_for_stage("stage1", "BALLOON_AIRCRAFT") == "A2"
    assert TrackingLoop._controller_mode_for_stage("stage1", "AIRCRAFT") == "legacy"


def test_light_mode_accepts_explicit_live_pid_updates(client) -> None:
    tracker = client.app.state.runtime.auto_tracker
    tracker.set_controller_mode("A2")

    tracker.update_config(
        TrackingConfigUpdate(
            pid_kp_x=12.0,
            pid_kd_x=1.25,
            smoothing_alpha=0.20,
            max_speed=9000,
        )
    )

    assert tracker.status().pid_kp_x == 12.0
    assert tracker.status().pid_kd_x == 1.25
    assert tracker.status().smoothing_alpha == 0.20
    assert tracker.status().max_speed == 9000


def test_general_mode_uses_saved_pid_profile_and_no_a2_layers(client, tmp_path: Path) -> None:
    tracker = client.app.state.runtime.auto_tracker
    status = tracker.set_controller_mode("GENERAL")
    tracker._light_controller.persisted_path = tmp_path / "light_general_pid.json"

    assert status.controller_mode == "GENERAL"
    assert status.pid_kp_x == 11.0
    assert status.pid_ki_x == 0.0
    assert status.pid_kd_x == 1.1
    assert status.pid_kp_y == 8.0
    assert status.pid_ki_y == 0.0
    assert status.pid_kd_y == 4.0
    assert status.max_speed == 12000
    assert status.command_rate_hz == 50.0
    assert status.smoothing_alpha == 1.0
    assert status.lead_enabled is False

    tracker.update_config(
        TrackingConfigUpdate(
            controller_mode="GENERAL",
            pid_kp_x=12.0,
            pid_kd_y=4.5,
            max_speed=11000,
            invert_x=True,
        )
    )
    updated = tracker.status()
    assert updated.controller_mode == "GENERAL"
    assert updated.pid_kp_x == 12.0
    assert updated.pid_kd_y == 4.5
    assert updated.max_speed == 11000
    assert updated.invert_x is True

    reloaded = LightTrackingController("GENERAL", persisted_path=tracker._light_controller.persisted_path)
    assert reloaded.params.kp_x == 12.0
    assert reloaded.params.kd_y == 4.5
    assert reloaded.params.invert_x is True


def test_general_controller_matches_light_reference_for_100_steps_and_loss() -> None:
    reference_root = Path(__file__).resolve().parents[2].parent / "Users" / "mehme" / "Desktop" / "balon takip yarışması"
    if not reference_root.exists():
        reference_root = Path(__file__).resolve().parents[2] / "light_reference"
    pid = json.loads((reference_root / "pid_ayarlar.json").read_text(encoding="utf-8"))
    controller = LightTrackingController("GENERAL")

    integral_x = integral_y = 0.0
    previous_error_x = previous_error_y = 0.0
    previous_speed_x = previous_speed_y = 0.0
    has_previous_speed = False
    seed_derivative = True
    sequence = [
        (320.0 + ((index * 47) % 520) - 260.0, 240.0 + ((index * 29) % 360) - 180.0, 0.017 + (index % 7) * 0.002)
        for index in range(100)
    ]

    for index, (target_x, target_y, dt) in enumerate(sequence):
        if index == 57:
            controller.reset_target_loss()
            integral_x = integral_y = 0.0
            previous_error_x = previous_error_y = 0.0
            seed_derivative = True

        error_x = target_x - 320.0
        error_y = target_y - 240.0
        # Derivative kick fix: ilk karede (ve kayıp sonrası ilk karede)
        # önceki hata cari hatayla tohumlanır → D terimi o karede 0'dır.
        if seed_derivative:
            previous_error_x, previous_error_y = error_x, error_y
            seed_derivative = False
        integral_x = max(-5000.0, min(5000.0, integral_x + error_x * dt))
        integral_y = max(-5000.0, min(5000.0, integral_y + error_y * dt))
        raw_x = pid["kp_x"] * error_x + pid["ki_x"] * integral_x + pid["kd_x"] * ((error_x - previous_error_x) / dt)
        raw_y = pid["kp_y"] * error_y + pid["ki_y"] * integral_y + pid["kd_y"] * ((error_y - previous_error_y) / dt)
        previous_error_x, previous_error_y = error_x, error_y
        desired_x = max(-12000.0, min(12000.0, -raw_x if pid["invert_x"] else raw_x))
        desired_y = max(-12000.0, min(12000.0, -raw_y if pid["invert_y"] else raw_y))
        if has_previous_speed:
            max_delta = 5000.0 * dt
            if abs(desired_x - previous_speed_x) > max_delta:
                desired_x = previous_speed_x + max_delta * (1.0 if desired_x > previous_speed_x else -1.0)
            if abs(desired_y - previous_speed_y) > max_delta:
                desired_y = previous_speed_y + max_delta * (1.0 if desired_y > previous_speed_y else -1.0)
        previous_speed_x, previous_speed_y = desired_x, desired_y
        has_previous_speed = True

        output = controller.update(target_x, target_y, 640, 480, dt, 100, 80)
        assert output.raw_pid_x == raw_x
        assert output.raw_speed_x == desired_y
        assert output.raw_speed_y == desired_x
        assert output.speed_x == round(desired_x)
        assert output.speed_y == round(-desired_y)
        assert output.raw_pid_y == raw_y


def test_autotracker_reuses_same_fresh_frame_at_light_control_cadence(client) -> None:
    tracker = client.app.state.runtime.auto_tracker
    tracker.set_controller_mode("A2")
    tracker.set_target_policy("BALLOON")
    tracker.start_tracking()
    tracker._last_time = time.time() - 0.1

    event = _balloon_event()
    first = tracker.update(event, 640, 480)
    tracker._last_time = time.time() - 0.1
    # Aynı kare kimliğiyle (REUSED yolu) kaymış balon: yeniden işlenen kare
    # PID durumunu ilerletir — tohumlamadan sonra D terimi gerçek hareketle
    # fark üretir, ölçüm sıfırlanmış/stale sayılmış olmaz.
    moved = _balloon_event(frame_id=1, x=265, y=200)
    moved.timestamp_ms = event.timestamp_ms
    reused = tracker.update(moved, 640, 480)

    assert first.controller_mode == "A2"
    assert reused.measurement_status == "REUSED"
    assert reused.controller_mode == "A2"
    # Reusing the fresh detection advances Light's PID/acceleration state;
    # it must not be treated as a stale or zeroed measurement.
    assert reused.speed_x != first.speed_x


@pytest.mark.parametrize(
    ("controller_mode", "expected_sleep"),
    [("A2", 0.015), ("GENERAL", 0.020)],
)
def test_tracking_loop_light_mode_uses_reference_fixed_cadence(monkeypatch, controller_mode, expected_sleep) -> None:
    from app.services.tracking_loop import TrackingLoop

    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr("app.services.tracking_loop.asyncio.sleep", fake_sleep)
    event = VisionEvent(
        frame_id=1,
        timestamp_ms=int(time.time() * 1000) - 20,
        source="light-cadence-test",
        frame_width=640,
        frame_height=480,
        fps=30,
        preprocess_ms=0,
        inference_ms=1,
        postprocess_ms=0,
        total_latency_ms=1,
        body_detections=[],
        balloon_detections=[],
    )
    holder: dict[str, TrackingLoop] = {}

    class FakeTracker:
        tracking_active = True
        command_rate_hz = 66.67 if controller_mode == "A2" else 50.0

        def __init__(self) -> None:
            self.controller_mode = controller_mode

        def update(self, *_args, **_kwargs) -> TrackingUpdate:
            holder["loop"]._running = False
            return TrackingUpdate(state=TrackingState.IDLE, frame_id=1)

    tracker = FakeTracker()
    loop = TrackingLoop(
        auto_tracker=tracker,
        vision_pipeline=SimpleNamespace(latest=lambda: event),
        serial=None,
        gateway=SimpleNamespace(runtime=None),
        logger=SimpleNamespace(emit=lambda *_args, **_kwargs: None),
        interval_ms=12.0,
    )
    holder["loop"] = loop
    loop._running = True

    asyncio.run(loop._run())

    assert sleeps == [expected_sleep]


def test_general_mode_centroid_tracking_continuity(client) -> None:
    tracker = client.app.state.runtime.auto_tracker
    tracker.set_controller_mode("GENERAL")
    tracker.start_tracking()

    # Frame 1: Initial balloon detection at (230, 890)
    b1 = BalloonDetection(id=101, class_name="balloon", confidence=0.88, bbox=BBox(x=205, y=865, w=50, h=50), center_x=230, center_y=890)
    ev1 = VisionEvent(source="test", fps=30.0, preprocess_ms=1.0, inference_ms=1.0, postprocess_ms=1.0, total_latency_ms=3.0, frame_id=1, timestamp_ms=1000, detections=[], balloon_detections=[b1], body_detections=[])
    up1 = tracker.update(ev1, 1920, 1080)
    assert up1.state == TrackingState.TRACKING
    assert up1.target_center_x == 230
    assert up1.target_center_y == 890

    # Frame 2: YOLO produces a new detection id (102) at (300, 850) (78 px move)
    b2 = BalloonDetection(id=102, class_name="balloon", confidence=0.89, bbox=BBox(x=275, y=825, w=50, h=50), center_x=300, center_y=850)
    ev2 = VisionEvent(source="test", fps=30.0, preprocess_ms=1.0, inference_ms=1.0, postprocess_ms=1.0, total_latency_ms=3.0, frame_id=2, timestamp_ms=1040, detections=[], balloon_detections=[b2], body_detections=[])
    up2 = tracker.update(ev2, 1920, 1080)
    assert up2.state == TrackingState.TRACKING
    assert up2.target_center_x == 300
    assert up2.target_center_y == 850

    # Frame 3: Another new detection id (103) at (400, 800)
    b3 = BalloonDetection(id=103, class_name="balloon", confidence=0.91, bbox=BBox(x=375, y=775, w=50, h=50), center_x=400, center_y=800)
    ev3 = VisionEvent(source="test", fps=30.0, preprocess_ms=1.0, inference_ms=1.0, postprocess_ms=1.0, total_latency_ms=3.0, frame_id=3, timestamp_ms=1080, detections=[], balloon_detections=[b3], body_detections=[])
    up3 = tracker.update(ev3, 1920, 1080)
    assert up3.state == TrackingState.TRACKING
    assert up3.target_center_x == 400
    assert up3.target_center_y == 800


def test_light_a2_corner_target_soft_saturation_and_loss_recovery() -> None:
    controller = LightTrackingController("A2")

    # 1. Target appears at corner x=1265 in 1280x720 frame (cx=640, error_x = +625px)
    out1 = controller.update(1265.0, 360.0, 1280, 720, 0.05, 50, 50)
    # Derivative kick is suppressed on first frame
    # P-term is soft-saturated: error_x_pid <= 220px, raw_pid_x <= 11 * 220 = 2420
    assert out1.target_velocity_x == 0.0
    assert abs(out1.raw_pid_x) <= 2450.0
    assert out1.raw_pid_x > 2000.0  # Still assertive, not paralyzed

    # 2. Target lost
    controller.reset_target_loss()
    assert controller._previous_target_x is None
    assert controller._filtered_x is None
    assert controller._target_velocity_x == 0.0

    # 3. New target reacquired at opposite corner x=15 (error_x = -625px)
    out2 = controller.update(15.0, 360.0, 1280, 720, 0.05, 50, 50)
    # Must NOT compute a bogus delta from 1265 to 15 (which would be 25,000 px/s)
    assert out2.target_velocity_x == 0.0
    assert out2.raw_pid_x < -2000.0
    assert out2.raw_pid_x >= -2450.0


