import time

from fastapi.testclient import TestClient

from app.schemas.command_gateway import CommandProfile
from app.schemas.vision import BBox, BalloonDetection, VisionEvent


def _fresh_balloon_event() -> VisionEvent:
    balloon = BalloonDetection(
        id=1, confidence=0.96, bbox=BBox(x=290, y=150, w=60, h=60),
        center_x=320, center_y=180, source="mock_pico_contract",
    )
    return VisionEvent(
        frame_id=1, timestamp_ms=int(time.time() * 1000), source="mock_pico_contract",
        frame_width=640, frame_height=360, fps=30.0, preprocess_ms=1.0,
        inference_ms=2.0, postprocess_ms=1.0, total_latency_ms=4.0,
        body_detections=[], balloon_detections=[balloon],
    )


def _arm_live_test(client: TestClient):
    runtime = client.app.state.runtime
    client.post("/api/mission/reset")
    runtime.vision.latest_event = _fresh_balloon_event()
    result = client.post("/api/safety/command-profile", json={"profile": "LIVE_TEST", "actuator_arm": True})
    assert result.status_code == 200
    assert result.json()["ready"] is True
    return runtime


def _raw_commands(runtime) -> list[str]:
    return [entry.raw for entry in runtime.serial.logs if entry.direction.value == "tx"]


def test_mock_pico_arm_fire_ack_contract(client: TestClient) -> None:
    runtime = _arm_live_test(client)

    result = runtime.command_gateway.fire_from_tracking(runtime, {"frame_id": 1})

    assert result.accepted is True
    assert result.command == "LZR,1"
    assert "ARM,1" in _raw_commands(runtime)
    assert "LZR,1" in _raw_commands(runtime)
    assert "OK,LASER_1" in (result.pico_ack or "")


def test_visible_test_mode_allows_real_motion_but_never_fire(client: TestClient) -> None:
    runtime = client.app.state.runtime
    client.post("/api/mission/reset")
    runtime.vision.latest_event = _fresh_balloon_event()

    preflight = client.post(
        "/api/safety/command-profile",
        json={"profile": "LIVE_TEST", "actuator_arm": False},
    )

    assert preflight.status_code == 200
    assert preflight.json()["physical_motion_enabled"] is True
    assert preflight.json()["physical_fire_enabled"] is False
    assert preflight.json()["actuator_armed"] is False
    assert "ACTUATOR_NOT_ARMED" in preflight.json()["reason_codes"]
    assert "ARM,0" in _raw_commands(runtime)
    assert runtime.command_gateway.send_motion(runtime, 100, 0).accepted is True
    fire = runtime.command_gateway.fire_from_tracking(runtime, {"frame_id": 1})
    assert fire.accepted is False
    assert "ACTUATOR_NOT_ARMED" in fire.reason_codes
    assert "LZR,1" not in _raw_commands(runtime)


def test_estop_rejects_fire_and_safes_outputs(client: TestClient) -> None:
    runtime = _arm_live_test(client)
    runtime.serial.set_mock_estop(True)

    # A new explicit preflight reads the actual E-stop state before any fire.
    preflight = client.post("/api/safety/preflight", json={"actuator_arm": True})
    result = runtime.command_gateway.fire_from_tracking(runtime, {"frame_id": 1})

    assert "ESTOP_ACTIVE" in preflight.json()["reason_codes"]
    assert result.accepted is False
    assert "ESTOP_ACTIVE" in result.reason_codes
    runtime.command_gateway.tick(runtime)
    commands = _raw_commands(runtime)
    assert "LZR,0" in commands and "STP" in commands and "DRV,0" in commands


def test_connection_loss_produces_no_fire(client: TestClient) -> None:
    runtime = _arm_live_test(client)
    from app.schemas.serial import SerialConnectionState
    runtime.serial.connection_state = SerialConnectionState.FAULT

    result = runtime.command_gateway.fire_from_tracking(runtime, {"frame_id": 1})

    assert result.accepted is False
    assert "PICO_CONNECTION_FAULT" in result.reason_codes
    assert "LZR,1" not in _raw_commands(runtime)


def test_successful_ping_recovers_command_fault_for_visible_repreflight(client: TestClient) -> None:
    runtime = client.app.state.runtime
    from app.schemas.serial import SerialConnectionState

    runtime.serial.connection_state = SerialConnectionState.FAULT
    runtime.serial.last_error = "PICO_UNEXPECTED_ACK:ERR,TRIGGER_NOT_ARMED"

    ping = runtime.serial.gateway_exchange("PING", ("OK,PONG", "PONG"))

    assert ping.accepted is True
    assert runtime.serial.connection_state != SerialConnectionState.FAULT
    assert runtime.serial.last_error is None


def test_heartbeat_loss_safes_motion_and_requires_new_preflight(client: TestClient) -> None:
    runtime = _arm_live_test(client)
    assert runtime.command_gateway.send_motion(runtime, 100, 0).accepted is True
    runtime.serial.gateway_last_heartbeat_at = time.time() - 10

    runtime.command_gateway.tick(runtime)

    commands = _raw_commands(runtime)
    assert "LZR,0" in commands and "STP" in commands and "DRV,0" in commands
    assert runtime.command_gateway.actuator_armed is False
    assert runtime.command_gateway.driver_enabled is False
    assert runtime.force_armed is False
    fire = runtime.command_gateway.fire_from_tracking(runtime, {"frame_id": 1})
    assert fire.accepted is False
    assert "PREFLIGHT_NOT_READY" in fire.reason_codes


def test_stale_camera_blocks_then_repreflight_recovers(client: TestClient) -> None:
    runtime = client.app.state.runtime
    runtime.vision.latest_event = _fresh_balloon_event().model_copy(update={"timestamp_ms": int((time.time() - 1) * 1000)})
    blocked = client.post("/api/safety/command-profile", json={"profile": "LIVE_TEST", "actuator_arm": True})
    assert "CAMERA_STALE" in blocked.json()["reason_codes"]

    runtime.vision.latest_event = _fresh_balloon_event()
    recovered = client.post("/api/safety/preflight", json={"actuator_arm": True})

    assert recovered.status_code == 200
    assert recovered.json()["ready"] is True


def test_runtime_camera_stale_safing_updates_visible_gate(client: TestClient) -> None:
    runtime = _arm_live_test(client)
    runtime.vision.latest_event = _fresh_balloon_event().model_copy(
        update={"timestamp_ms": int((time.time() - 2) * 1000)}
    )

    runtime.command_gateway.tick(runtime)

    assert runtime.command_gateway.last_preflight.ready is False
    assert "CAMERA_STALE" in runtime.command_gateway.last_preflight.reason_codes
    camera_gate = next(g for g in runtime.command_gateway.last_preflight.gates if g.code == "CAMERA_STALE")
    assert camera_gate.ready is False


def test_camera_recovery_restores_explicit_trigger_intent(client: TestClient) -> None:
    runtime = _arm_live_test(client)
    runtime.vision.latest_event = _fresh_balloon_event().model_copy(
        update={"timestamp_ms": int((time.time() - 2) * 1000)}
    )
    runtime.command_gateway.tick(runtime)
    assert runtime.command_gateway.last_preflight.physical_motion_enabled is False

    runtime.vision.latest_event = _fresh_balloon_event()
    runtime.command_gateway.maintenance_tick(runtime)

    assert runtime.command_gateway.last_preflight.physical_motion_enabled is True
    assert runtime.command_gateway.last_preflight.physical_fire_enabled is True
    assert runtime.command_gateway.actuator_armed is True
    assert runtime.command_gateway.send_motion(runtime, 100, 0).accepted is True


def test_fresh_raw_camera_allows_manual_live_test_without_detector_event(client: TestClient) -> None:
    runtime = client.app.state.runtime
    runtime.vision.latest_event = None
    runtime.camera_runtime.profile = runtime.camera_runtime.profile.model_copy(
        update={
            "source_type": "laptop",
            "device_id": "camera_index_1",
            "device_path": "camera-index:1",
        }
    )
    runtime.camera_runtime.capture_paused = False
    runtime.camera_runtime.last_frame_at = time.time()

    preflight = client.post(
        "/api/safety/command-profile",
        json={"profile": "LIVE_TEST", "actuator_arm": True},
    )

    assert preflight.status_code == 200
    assert preflight.json()["ready"] is True
    assert "CAMERA_STALE" not in preflight.json()["reason_codes"]


def test_live_test_motion_reaches_gateway_pico_contract(client: TestClient) -> None:
    runtime = _arm_live_test(client)

    result = runtime.command_gateway.send_motion(runtime, 100, -100)

    assert result.accepted is True
    assert result.command == "SPD"
    commands = _raw_commands(runtime)
    assert "DRV,1" in commands
    assert "SPD,100,100" in commands

    stopped = runtime.command_gateway.stop_motion()
    assert stopped.accepted is True
    assert "STP" in _raw_commands(runtime)
    assert "DRV,0" in _raw_commands(runtime)


def test_live_motion_updates_digital_twin_with_gateway_open_loop_pose(client: TestClient, monkeypatch) -> None:
    runtime = _arm_live_test(client)
    runtime.config.motion.soft_limits_enabled = False
    clock = {"value": 100.0}
    monkeypatch.setattr(runtime.command_gateway, "_monotonic", lambda: clock["value"])
    runtime.command_gateway._pose_updated_at = clock["value"]

    moved = runtime.command_gateway.send_motion(runtime, 400, 200)
    assert moved.accepted is True
    clock["value"] += 1.0
    runtime.command_gateway.refresh_motion_estimate(runtime)

    state = runtime.motion.status()
    # Active rollback is the field-tested MicroPython 1/8 path:
    # pan 400/1000*4000, tilt 200/1000*6000.
    assert state.pan_position_steps == 1600
    assert state.tilt_position_steps == 1200
    assert state.pan_position_deg > 0
    assert state.tilt_position_deg > 0
    assert state.last_command == "gateway_open_loop_estimate"

    twin = client.get("/api/digital-twin/state").json()
    assert twin["device_pose"]["pan_deg"] == state.pan_position_deg
    assert twin["device_pose"]["tilt_deg"] == state.tilt_position_deg
    assert twin["device_pose"]["pose_quality"] == "estimated"
    assert twin["device_pose"]["pose_source"] == "gateway_open_loop_estimate"

    stopped = runtime.command_gateway.stop_motion()
    assert stopped.accepted is True
    assert runtime.motion.status().motion_state == "STOPPED"


def test_gateway_motor_direction_mapping_preserves_semantic_limits(client: TestClient) -> None:
    runtime = _arm_live_test(client)
    runtime.config.motor.pan_direction_multiplier = 1
    runtime.config.motor.tilt_direction_multiplier = -1
    runtime.config.motor.axis_swap = True

    up = runtime.command_gateway.send_motion(runtime, 200, 200)

    assert up.accepted is True
    assert "SPD,-200,200" in _raw_commands(runtime)

    # The limit is evaluated before the wiring transform: semantic +Y is UP.
    runtime.motion.state = runtime.motion.state.model_copy(update={"tilt_limit_up": True})
    blocked = runtime.command_gateway.send_motion(runtime, 0, 200)
    assert blocked.accepted is False
    assert "TILT_UP_LIMIT_ACTIVE" in blocked.reason_codes


def test_balloon_parser_rejects_edge_fragment_and_non_round_false_positive() -> None:
    from types import SimpleNamespace
    import numpy as np
    from app.services.vision_pipeline import VisionPipeline

    class Box:
        xyxy = np.asarray([[217, 466, 256, 480]], dtype=float)
        conf = np.asarray([0.39], dtype=float)
        cls = np.asarray([1], dtype=float)

    result = SimpleNamespace(boxes=[Box()], names={0: "dost", 1: "dusman"})
    pipeline = object.__new__(VisionPipeline)
    pipeline.vision_runtime = SimpleNamespace(profile=SimpleNamespace(target_class_map={}))

    _bodies, balloons, warnings = pipeline._detections_from_results(
        [result], 640, 480, model_id="setup_balloon_path", role="balloon", class_names=[]
    )
    assert balloons == []
    assert "balloon_geometry_rejected_edge:setup_balloon_path:dusman" in warnings


def test_gateway_rejects_speed_and_limit_boundary_violations(client: TestClient) -> None:
    runtime = _arm_live_test(client)
    runtime.motion.state = runtime.motion.state.model_copy(update={"pan_limit_right": True})

    limit = runtime.command_gateway.send_motion(runtime, 1, 0)
    speed = runtime.command_gateway.send_motion(runtime, runtime.config.motor.max_speed + 1, 0)

    assert limit.accepted is False
    assert "PAN_RIGHT_LIMIT_ACTIVE" in limit.reason_codes
    assert speed.accepted is False
    assert "MOTION_SPEED_LIMIT" in speed.reason_codes


def test_gateway_enforces_separate_motion_and_fire_forbidden_zones(client: TestClient) -> None:
    runtime = _arm_live_test(client)
    from app.schemas.config import AngularSafetyZone
    zone = AngularSafetyZone(name="operator_sector", pan_min_deg=-2, pan_max_deg=2, tilt_min_deg=-2, tilt_max_deg=2)
    runtime.config.motion.motion_forbidden_zones = [zone]
    runtime.config.decision.fire_forbidden_zones = [zone]
    runtime.config.decision.forbidden_zone_check_enabled = True

    motion = runtime.command_gateway.send_motion(runtime, 10, 0)
    fire = runtime.command_gateway.fire_from_tracking(runtime, {"frame_id": 1})

    assert motion.accepted is False
    assert "MOTION_FORBIDDEN_ZONE" in motion.reason_codes
    assert fire.accepted is False
    assert "FIRE_FORBIDDEN_ZONE" in fire.reason_codes

    runtime.command_gateway.select_profile(runtime, CommandProfile.COMPETITION, actuator_arm_requested=True)
    decision = runtime.decision_engine.evaluate(runtime)
    assert "fire_forbidden_zone" in decision.blocking_reasons


def test_profile_is_selectable_over_api_without_config_change(client: TestClient) -> None:
    runtime = client.app.state.runtime
    runtime.vision.latest_event = _fresh_balloon_event()

    response = client.post("/api/safety/command-profile", json={"profile": CommandProfile.LIVE_TEST, "actuator_arm": True})

    assert response.status_code == 200
    assert response.json()["profile"] == "LIVE_TEST"
    assert runtime.config.system.dry_run is False
