import base64
import io
import time
from types import SimpleNamespace
from unittest.mock import Mock

from fastapi.testclient import TestClient
from PIL import Image

from app.schemas.command_gateway import GatewayCommandResult
from app.schemas.tracking import TrackingState, TrackingUpdate
from app.schemas.vision import BBox, BalloonDetection, VisionEvent
from app.services.tracking_loop import FIRE_REQUIRED_FRAMES, TrackingLoop


def _centered_event() -> VisionEvent:
    balloon = BalloonDetection(
        id=1,
        confidence=0.95,
        bbox=BBox(x=290, y=150, w=60, h=60),
        center_x=320,
        center_y=180,
        source="test",
    )
    return VisionEvent(
        frame_id=7,
        timestamp_ms=1_900_000_000_000,
        source="test",
        frame_width=640,
        frame_height=360,
        fps=30.0,
        preprocess_ms=1.0,
        inference_ms=2.0,
        postprocess_ms=1.0,
        total_latency_ms=4.0,
        body_detections=[],
        balloon_detections=[balloon],
    )


def test_full_active_selects_live_profile_and_clears_armed_state_when_preflight_blocks(client: TestClient) -> None:
    runtime = client.app.state.runtime
    runtime.force_armed = True

    response = client.post("/api/safety/set-operational-mode", json={"mode": "full_active"})

    assert response.status_code == 200
    assert response.json()["profile"] == "LIVE_TEST"
    assert "CAMERA_STALE" in response.json()["preflight"]["reason_codes"]
    assert runtime.force_armed is False
    assert runtime.config.hardware.allow_physical_fire is True
    assert runtime.config.system.dry_run is False
    assert runtime.last_safety_event is not None
    assert runtime.last_safety_event[0] == "safety.profile_selected"


def test_operational_mode_rejects_unknown_value(client: TestClient) -> None:
    response = client.post("/api/safety/set-operational-mode", json={"mode": "unsafe_mode"})

    assert response.status_code == 422
    assert "Unsupported operational mode" in response.json()["detail"]


def test_motion_no_fire_mode_selects_live_tracking_authority_without_trigger_arm(client: TestClient) -> None:
    runtime = client.app.state.runtime

    response = client.post("/api/safety/set-operational-mode", json={"mode": "motion_no_fire"})

    assert response.status_code == 200
    assert response.json()["mode"] == "motion_no_fire"
    assert response.json()["profile"] == "LIVE_TEST"
    assert runtime.config.tracking.enabled is True
    assert runtime.config.system.dry_run is False
    assert runtime.config.motion.real_motion_enabled is True
    assert runtime.config.hardware.allow_physical_motion is True
    assert response.json()["preflight"]["physical_fire_enabled"] is False
    assert "ACTUATOR_NOT_ARMED" in response.json()["preflight"]["reason_codes"]


def test_hardware_fire_test_endpoints_stay_blocked_even_if_legacy_flag_is_set(client: TestClient) -> None:
    runtime = client.app.state.runtime
    runtime.config.hardware.allow_physical_fire = True

    trigger = client.post("/api/hardware/test-trigger")
    tune = client.post(
        "/api/hardware/test-servo-tune",
        json={"release_deg": 35, "fire_deg": 175, "pulse_s": 1.0},
    )

    assert trigger.status_code == 200
    assert trigger.json()["accepted"] is False
    assert tune.status_code == 200
    assert tune.json()["accepted"] is False
    assert runtime.serial.last_tx is None


def test_serial_service_rejects_direct_fire_even_with_legacy_flag(client: TestClient) -> None:
    runtime = client.app.state.runtime
    runtime.config.hardware.allow_physical_fire = True

    result = runtime.serial.send_fire_command(1)

    assert result.accepted is False
    assert "CommandGateway authorization is required" in result.reason
    assert runtime.serial.last_tx is None


def test_tracking_loop_emits_candidate_without_physical_fire() -> None:
    serial = Mock()
    serial.config = SimpleNamespace(hardware=SimpleNamespace(allow_physical_fire=True))
    loop = TrackingLoop(
        auto_tracker=Mock(),
        vision_pipeline=Mock(),
        serial=serial,
        gateway=Mock(runtime=None),
        logger=Mock(),
        frame_width=640,
        frame_height=360,
    )
    event = _centered_event()
    update = TrackingUpdate(
        state=TrackingState.LOCKED,
        target_center_x=320,
        target_center_y=180,
        frame_center_x=320,
        frame_center_y=180,
        frame_id=event.frame_id,
    )

    for _ in range(FIRE_REQUIRED_FRAMES):
        loop._update_fire_zone(event, update, 640, 360)

    assert serial.send_fire_command.call_count == 0
    assert loop.drain_events() == [
        (
            "tracking.fire_candidate",
            {
                "frame_id": 7,
                "distance_px": 0.0,
                "fire_radius_px": 15.0,
                "target_center_x": 320.0,
                "target_center_y": 180.0,
                "required_stable_frames": FIRE_REQUIRED_FRAMES,
                "physical_fire_generated": False,
            },
        )
    ]


def test_tracking_loop_rejects_stale_vision_event() -> None:
    loop = TrackingLoop(
        auto_tracker=Mock(),
        vision_pipeline=Mock(),
        serial=Mock(),
        gateway=Mock(runtime=None),
        logger=Mock(),
    )

    stale_event = _centered_event().model_copy(update={"timestamp_ms": 1})

    assert loop._vision_event_is_fresh(stale_event) is False


def test_tracking_target_loss_soft_stops_then_disables_driver_after_hold() -> None:
    gateway = Mock(runtime=object(), driver_enabled=True)
    gateway.stop_motion.return_value = GatewayCommandResult(
        accepted=True,
        command="STP",
        detail="OK,STOP; OK,DRIVER_DISABLED",
        physical_command_generated=True,
    )
    loop = TrackingLoop(
        auto_tracker=Mock(),
        vision_pipeline=Mock(),
        serial=Mock(),
        gateway=gateway,
        logger=Mock(),
    )
    lost = TrackingUpdate(state=TrackingState.SEARCHING, target_lost_frames=1, frame_id=42)

    assert loop._safe_stop_on_target_loss(lost) is True
    gateway.send_motion.assert_called_once_with(gateway.runtime, 0, 0, "tracking")
    gateway.stop_motion.assert_not_called()
    loop._target_loss_started_at = time.monotonic() - 1.6
    assert loop._safe_stop_on_target_loss(lost.model_copy(update={"target_lost_frames": 2})) is True
    gateway.stop_motion.assert_called_once_with()
    assert loop.drain_events()[0][0] == "tracking.target_lost_safe_stop"


def test_tracking_reacquisition_rearms_target_loss_transition() -> None:
    gateway = Mock(runtime=object(), driver_enabled=True)
    gateway.stop_motion.return_value = GatewayCommandResult(accepted=True, command="STP", detail="safe")
    loop = TrackingLoop(auto_tracker=Mock(), vision_pipeline=Mock(), serial=Mock(), gateway=gateway, logger=Mock())
    lost = TrackingUpdate(state=TrackingState.SEARCHING, target_lost_frames=1)

    loop._safe_stop_on_target_loss(lost)
    loop._target_loss_started_at = time.monotonic() - 1.6
    loop._safe_stop_on_target_loss(lost)
    assert loop._safe_stop_on_target_loss(TrackingUpdate(state=TrackingState.TRACKING)) is False
    loop._safe_stop_on_target_loss(lost)
    loop._target_loss_started_at = time.monotonic() - 1.6
    loop._safe_stop_on_target_loss(lost)

    assert gateway.stop_motion.call_count == 2


def test_live_test_tracking_keeps_fire_operator_driven() -> None:
    gateway = Mock(
        runtime=SimpleNamespace(mission=SimpleNamespace(state=SimpleNamespace(active_stage=None))),
        profile=SimpleNamespace(value="LIVE_TEST"),
    )
    loop = TrackingLoop(auto_tracker=Mock(), vision_pipeline=Mock(), serial=Mock(), gateway=gateway, logger=Mock())

    assert loop._physical_auto_fire_allowed() is False


def test_competition_stage2_and_stage3_allow_gateway_autonomous_fire() -> None:
    runtime = SimpleNamespace(mission=SimpleNamespace(state=SimpleNamespace(active_stage="stage2")))
    gateway = Mock(runtime=runtime, profile=SimpleNamespace(value="COMPETITION"))
    loop = TrackingLoop(auto_tracker=Mock(), vision_pipeline=Mock(), serial=Mock(), gateway=gateway, logger=Mock())

    assert loop._physical_auto_fire_allowed() is True
    runtime.mission.state.active_stage = "stage3"
    assert loop._physical_auto_fire_allowed() is True
    runtime.mission.state.active_stage = "stage1"
    assert loop._physical_auto_fire_allowed() is False


def test_decision_engine_rejects_stale_vision_event(client: TestClient) -> None:
    runtime = client.app.state.runtime
    event = _centered_event().model_copy(update={"timestamp_ms": int((time.time() - 1.0) * 1000)})
    runtime.vision.latest_event = event

    decision = runtime.decision_engine.evaluate(runtime)

    assert "vision_stale" in decision.blocking_reasons
    freshness_gate = next(gate for gate in decision.gates if gate.name == "vision_freshness_gate")
    assert freshness_gate.status == "fail"


def test_browser_camera_frame_is_advisory_and_never_writes_serial(client: TestClient) -> None:
    image = Image.new("RGB", (16, 16), color=(220, 20, 20))
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG")
    payload = base64.b64encode(buffer.getvalue()).decode("ascii")
    before = client.get("/api/serial/logs").json()

    response = client.post(
        "/api/vision/browser-frame",
        json={"image_base64": f"data:image/jpeg;base64,{payload}", "device_label": "laptop_camera"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["frame_origin"] == "browser_frame_upload"
    assert body["camera_source_kind"] == "browser_camera"
    assert "no_physical_command_generated=true" in body["warnings"]
    assert client.get("/api/serial/logs").json() == before
