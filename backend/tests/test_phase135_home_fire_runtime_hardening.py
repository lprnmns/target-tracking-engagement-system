from __future__ import annotations

import time
from pathlib import Path

from app.schemas.command_gateway import CommandProfile
from app.schemas.vision import BBox, BalloonDetection, VisionEvent


ROOT = Path(__file__).resolve().parents[2]


def _fresh_camera(runtime) -> None:
    runtime.vision.latest_event = VisionEvent(
        frame_id=135,
        timestamp_ms=int(time.time() * 1000),
        source="phase135",
        frame_width=640,
        frame_height=360,
        fps=30,
        preprocess_ms=1,
        inference_ms=1,
        postprocess_ms=1,
        total_latency_ms=3,
        balloon_detections=[
            BalloonDetection(
                id=1,
                confidence=0.99,
                bbox=BBox(x=280, y=140, w=80, h=80),
                center_x=320,
                center_y=180,
                source="phase135",
            )
        ],
        body_detections=[],
    )


def _live(runtime, arm: bool = False) -> None:
    _fresh_camera(runtime)
    result = runtime.command_gateway.select_profile(runtime, CommandProfile.LIVE_TEST, arm)
    assert result.physical_motion_enabled


def _tx(runtime) -> list[str]:
    return [entry.raw for entry in runtime.serial.logs if entry.direction.value == "tx"]


def test_home_exclusivity_blocks_spd_before_serial_and_keeps_transport_healthy(client) -> None:
    runtime = client.app.state.runtime
    _live(runtime)
    started = runtime.command_gateway.start_home(runtime)
    assert started.accepted and runtime.command_gateway.home_in_progress
    before = _tx(runtime).count("SPD,300,0")

    motion = runtime.command_gateway.send_motion(runtime, 300, 0, origin="manual_operator")

    assert not motion.accepted
    assert motion.reason_codes == ["HOME_IN_PROGRESS"]
    assert _tx(runtime).count("SPD,300,0") == before
    assert runtime.serial.connection_state.value != "FAULT"


def test_home_can_recover_a_previous_motion_fault_without_source_edit(client) -> None:
    runtime = client.app.state.runtime
    _live(runtime)
    runtime.motion.set_fault("HOME_PAN_TIMEOUT")
    blocked = runtime.command_gateway.run_preflight(runtime, actuator_arm_requested=False)
    assert not blocked.physical_motion_enabled

    started = runtime.command_gateway.start_home(runtime)
    completed = runtime.command_gateway.home_status(runtime)

    assert started.accepted
    assert completed.accepted and completed.homed
    assert runtime.motion.status().last_error is None


def test_trigger_pulse_is_single_flight_across_duplicate_requests(client) -> None:
    runtime = client.app.state.runtime
    _live(runtime, arm=True)

    first = runtime.command_gateway.test_trigger(runtime, 0.2)
    duplicate = runtime.command_gateway.test_trigger(runtime, 0.2)

    assert first.accepted
    assert not duplicate.accepted
    assert duplicate.reason_codes == ["FIRE_PULSE_ACTIVE"]
    assert _tx(runtime).count("LZR,1") == 1


def test_phase135_ui_firmware_and_twin_contracts_are_present() -> None:
    cockpit = (ROOT / "frontend/src/views/CockpitView.vue").read_text(encoding="utf-8")
    live_camera = (ROOT / "frontend/src/components/cockpit/LiveCameraPanel.vue").read_text(encoding="utf-8")
    twin = (ROOT / "frontend/src/components/digital-twin/DigitalTwinPanel.vue").read_text(encoding="utf-8")
    firmware = (ROOT / "firmware/pico2_arduino_stable/pico2_arduino_stable.ino").read_text(encoding="utf-8")
    router = (ROOT / "frontend/src/router/index.ts").read_text(encoding="utf-8")

    assert "const deadline = Date.now() + 125_000" in cockpit
    assert "if (homingActive.value)" in cockpit
    assert "istiklal_last_manual_fire_at" in cockpit
    assert "if (!document.hidden) void digitalTwin.refreshPose()" in cockpit
    assert "angleDeg * TWIN_YAW_DIRECTION_SIGN" in twin
    assert "PICO_HEARTBEAT_STALE" in firmware
    assert "WATCHDOG_TIMEOUT_US = 1500000" in firmware
    assert "component: LandingView" in router
    assert "backendFrameFallback.value = true" in live_camera
    assert "void refreshBackendFrame()" in live_camera
    assert "backendFrameObjectUrl" in live_camera
    assert "}, 50)" in live_camera
    assert live_camera.count("object-contain") >= 3
    assert 'class="camera-live-frame h-full w-full object-cover"' not in live_camera


def test_observer_endpoints_share_bounded_camera_and_twin_work() -> None:
    camera_runtime = (ROOT / "backend/app/services/camera_runtime_service.py").read_text(encoding="utf-8")
    camera_api = (ROOT / "backend/app/api/vision.py").read_text(encoding="utf-8")
    twin_api = (ROOT / "backend/app/api/digital_twin.py").read_text(encoding="utf-8")
    twin_service = (ROOT / "backend/app/services/digital_twin_service.py").read_text(encoding="utf-8")

    assert 'cache_token=("plain", output_width, output_height, quality)' in camera_runtime
    assert 'cache_token=("plain", width, height, quality)' in camera_api
    assert "entries.clear()" not in camera_runtime.split("    def _set_last_frame", 1)[1].split("    def _frame_brightness_state", 1)[0]
    assert "state_cached(runtime)" in twin_api
    assert "def state_cached" in twin_service


def test_single_center_crosshair_and_30_percent_bbox_fire_lock_contract() -> None:
    live_camera = (ROOT / "frontend/src/components/cockpit/LiveCameraPanel.vue").read_text(encoding="utf-8")
    tracking_loop = (ROOT / "backend/app/services/tracking_loop.py").read_text(encoding="utf-8")
    config = (ROOT / "config/config.yaml").read_text(encoding="utf-8")

    assert "AIM REF" not in live_camera
    assert "props.aimX - 30" not in live_camera
    assert "props.aimY - 30" not in live_camera
    assert "boxW(target) * 0.3" in live_camera
    assert "boxX(target) + boxW(target) * 0.35" in live_camera
    assert "FIRE_INNER_BBOX_RATIO = 0.30" in tracking_loop
    assert "aim_offset_x_px: 0.0" in config
    assert "aim_offset_y_px: -56.0" in config
