import time

from app.schemas.vision import BBox, BalloonDetection, VisionEvent
from app.schemas.command_gateway import CommandProfile


def _fresh_event() -> VisionEvent:
    return VisionEvent(
        frame_id=88,
        timestamp_ms=int(time.time() * 1000),
        source="phase88",
        frame_width=640,
        frame_height=360,
        fps=30.0,
        preprocess_ms=1.0,
        inference_ms=1.0,
        postprocess_ms=1.0,
        total_latency_ms=3.0,
        body_detections=[],
        balloon_detections=[BalloonDetection(id=1, confidence=.98, bbox=BBox(x=1, y=1, w=20, h=20), center_x=11, center_y=11, source="phase88")],
    )


def _arm_live(runtime) -> None:
    runtime.vision.latest_event = _fresh_event()
    preflight = runtime.command_gateway.select_profile(runtime, CommandProfile.LIVE_TEST, True)
    assert preflight.ready


def _commands(runtime) -> list[str]:
    return [entry.raw for entry in runtime.serial.logs if entry.direction.value == "tx"]


def test_servo_config_and_empty_chamber_trigger_are_gateway_only(client) -> None:
    runtime = client.app.state.runtime
    _arm_live(runtime)
    before = runtime.serial.magazine_remaining

    configured = runtime.command_gateway.configure_trigger_servo(runtime, 35, 175)
    tested = runtime.command_gateway.test_trigger(runtime, .1)

    assert configured.accepted and configured.command == "CFG_SERVO,35,175"
    assert tested.accepted and tested.command == "LZR,1"
    assert runtime.serial.magazine_remaining == before
    commands = _commands(runtime)
    assert "CFG_SERVO,35,175" in commands
    assert "LZR,1" in commands
    time.sleep(.15)
    assert "LZR,0" in _commands(runtime)


def test_trigger_test_is_rejected_by_estop_without_servo_command(client) -> None:
    runtime = client.app.state.runtime
    _arm_live(runtime)
    runtime.serial.set_mock_estop(True)
    runtime.command_gateway.run_preflight(runtime, actuator_arm_requested=True)

    result = runtime.command_gateway.test_trigger(runtime, .1)

    assert not result.accepted
    assert "ESTOP_ACTIVE" in result.reason_codes
    assert "LZR,1" not in _commands(runtime)
