import time

from app.schemas.vision import BBox, BalloonDetection, VisionEvent
from app.services.serial_service import SerialService


def _event() -> VisionEvent:
    return VisionEvent(
        frame_id=82,
        timestamp_ms=int(time.time() * 1000),
        source="shot-budget-contract",
        frame_width=640,
        frame_height=360,
        fps=30,
        preprocess_ms=1,
        inference_ms=2,
        postprocess_ms=1,
        total_latency_ms=4,
        body_detections=[],
        balloon_detections=[
            BalloonDetection(id=1, confidence=0.95, bbox=BBox(x=290, y=150, w=60, h=60), center_x=320, center_y=180)
        ],
    )


def _live_armed_runtime(client):
    runtime = client.app.state.runtime
    runtime.serial.reset_magazine(2)
    runtime.vision.latest_event = _event()
    profile = client.post("/api/safety/command-profile", json={"profile": "LIVE_TEST", "actuator_arm": True})
    assert profile.status_code == 200 and profile.json()["ready"] is True
    return runtime


def _fire_command_count(runtime) -> int:
    return sum(entry.raw == "LZR,1" for entry in runtime.serial.logs if entry.direction.value == "tx")


def test_only_pico_acknowledged_gateway_fire_consumes_persisted_shot_budget(client) -> None:
    runtime = _live_armed_runtime(client)

    first = runtime.command_gateway.fire_from_tracking(runtime, {"frame_id": 82})
    # A physical trigger pulse is single-flight.  Releasing it models the
    # next distinct operator pull instead of treating three same-instant API
    # calls as three rounds.
    runtime.command_gateway._release_fire_output(force=True)
    second = runtime.command_gateway.fire_from_tracking(runtime, {"frame_id": 82})
    runtime.command_gateway._release_fire_output(force=True)
    third = runtime.command_gateway.fire_from_tracking(runtime, {"frame_id": 82})

    assert first.accepted is True and second.accepted is True
    assert third.accepted is False
    assert "MAGAZINE_EMPTY" in third.reason_codes
    assert _fire_command_count(runtime) == 2
    status = runtime.serial.status()
    assert status.magazine_remaining == 0
    assert status.acknowledged_shot_count == 2
    assert runtime.serial.magazine_state_path is not None and runtime.serial.magazine_state_path.exists()

    reloaded = SerialService(runtime.config, runtime.logger, magazine_state_path=runtime.serial.magazine_state_path)
    assert reloaded.status().magazine_remaining == 0
    assert reloaded.status().acknowledged_shot_count == 2


def test_corrupt_shot_budget_fails_closed(client, tmp_path) -> None:
    runtime = client.app.state.runtime
    path = tmp_path / "corrupt-shot-budget.json"
    path.write_text("not-json", encoding="utf-8")

    restored = SerialService(runtime.config, runtime.logger, magazine_state_path=path)

    assert restored.status().magazine_empty is True
    assert restored.status().last_error == "SHOT_BUDGET_STATE_INVALID"
