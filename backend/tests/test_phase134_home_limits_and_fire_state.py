import time

from app.schemas.command_gateway import CommandProfile
from app.schemas.vision import BBox, BalloonDetection, VisionEvent


def _fresh_camera(runtime) -> None:
    runtime.vision.latest_event = VisionEvent(
        frame_id=134,
        timestamp_ms=int(time.time() * 1000),
        source="phase134",
        frame_width=640,
        frame_height=360,
        fps=30.0,
        preprocess_ms=1.0,
        inference_ms=1.0,
        postprocess_ms=1.0,
        total_latency_ms=3.0,
        body_detections=[],
        balloon_detections=[
            BalloonDetection(
                id=1,
                confidence=0.99,
                bbox=BBox(x=280, y=140, w=80, h=80),
                center_x=320,
                center_y=180,
                source="phase134",
            )
        ],
    )


def _live(runtime, arm: bool = True) -> None:
    _fresh_camera(runtime)
    result = runtime.command_gateway.select_profile(runtime, CommandProfile.LIVE_TEST, arm)
    assert result.physical_motion_enabled
    assert result.physical_fire_enabled is arm


def _tx(runtime) -> list[str]:
    return [entry.raw for entry in runtime.serial.logs if entry.direction.value == "tx"]


def test_home_is_nonblocking_centres_and_restores_existing_visible_arm(client) -> None:
    runtime = client.app.state.runtime
    _live(runtime, arm=True)

    started = runtime.command_gateway.start_home(runtime)
    assert started.accepted and started.phase == "TILT_SEEK"
    assert runtime.command_gateway.actuator_armed is False
    assert runtime.operation.state().fire_permission.value == "ENABLED"

    completed = runtime.command_gateway.home_status(runtime)
    assert completed.accepted and completed.homed and completed.phase == "DONE"
    assert completed.pan_deg == 135.0
    assert completed.tilt_deg == 30.0
    assert runtime.motion.status().pan_position_deg == 0.0
    assert runtime.motion.status().tilt_position_deg == 0.0
    assert runtime.command_gateway.actuator_armed is True
    assert "CFG_LIMITS,0.000,270.000,0.000,60.000" in _tx(runtime)
    assert "HOME" in _tx(runtime)
    assert "HOME_STATUS" in _tx(runtime)


def test_engineering_envelope_is_enforced_in_pico_and_translated_for_twin(client) -> None:
    runtime = client.app.state.runtime
    _live(runtime, arm=False)
    response = client.put(
        "/api/hardware/motion-envelope",
        json={"pan_min_deg": 15, "pan_max_deg": 250, "tilt_min_deg": 5, "tilt_max_deg": 52},
    )
    assert response.status_code == 200
    assert response.json()["accepted"] is True
    assert runtime.config.motion.soft_limits_enabled is True
    assert runtime.config.motion.pan_min_deg == -120.0
    assert runtime.config.motion.pan_max_deg == 115.0
    assert runtime.config.motion.tilt_min_deg == -22.0
    assert runtime.config.motion.tilt_max_deg == 25.0
    assert "CFG_LIMITS,15.000,250.000,5.000,52.000" in _tx(runtime)


def test_camera_recovery_keeps_user_fire_intent_and_auto_rearms(client) -> None:
    runtime = client.app.state.runtime
    _live(runtime, arm=True)
    runtime.vision.latest_event = runtime.vision.latest_event.model_copy(
        update={"timestamp_ms": int((time.time() - 2.0) * 1000)}
    )
    runtime.command_gateway.tick(runtime)
    assert runtime.command_gateway.actuator_armed is False
    assert runtime.operation.state().fire_permission.value == "ENABLED"

    _fresh_camera(runtime)
    runtime.command_gateway.maintenance_tick(runtime)
    assert runtime.command_gateway.last_preflight.physical_motion_enabled is True
    assert runtime.command_gateway.last_preflight.physical_fire_enabled is True
    assert runtime.command_gateway.actuator_armed is True
    assert runtime.operation.state().fire_permission.value == "ENABLED"

    assert runtime.command_gateway.test_trigger(runtime, 0.01).accepted is True


def test_phase134_ui_and_firmware_contracts_are_present() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    firmware = (root / "firmware/pico2_arduino_stable/pico2_arduino_stable.ino").read_text(encoding="utf-8")
    cockpit = (root / "frontend/src/views/CockpitView.vue").read_text(encoding="utf-8")
    twin = (root / "frontend/src/components/digital-twin/DigitalTwinPanel.vue").read_text(encoding="utf-8")
    panel = (root / "frontend/src/components/cockpit/HomeAndLimitsPanel.vue").read_text(encoding="utf-8")

    for token in ("LIMIT_X_PIN = 22", "LIMIT_Y_PIN = 26", "HOME_TILT_SEEK", "HOME_PAN_SEEK", "HOME_CENTERING", "CFG_LIMITS"):
        assert token in firmware
    assert "ensureCockpitHomed" in cockpit
    assert "Auto-home & derece sınırları" in panel
    assert "addPanLimitWalls3d" in twin
    assert "opacity: hit ? 0.2 : 0.085" in twin
