from __future__ import annotations

import time

from fastapi.testclient import TestClient

from app.schemas.command_gateway import CommandProfile
from app.schemas.tracking import AssociationStatus, BodyBalloonAssociation
from app.schemas.vision import BBox, BalloonDetection, BodyDetection, VisionEvent


def _event() -> VisionEvent:
    return VisionEvent(
        frame_id=1,
        timestamp_ms=int(time.time() * 1000),
        source="stage2-gateway-contract",
        frame_width=640,
        frame_height=360,
        fps=30,
        preprocess_ms=1,
        inference_ms=1,
        postprocess_ms=1,
        total_latency_ms=3,
        body_detections=[
            BodyDetection(
                id=11,
                track_id=101,
                class_name="generic_target",
                class_id=0,
                confidence=0.95,
                bbox=BBox(x=290, y=150, w=60, h=60),
            )
        ],
        balloon_detections=[
            BalloonDetection(
                id=21,
                confidence=0.96,
                bbox=BBox(x=300, y=160, w=30, h=30),
                center_x=315,
                center_y=175,
            )
        ],
    )


def _prepared_stage2(client: TestClient):
    runtime = client.app.state.runtime
    event = _event()
    runtime.vision.latest_event = event
    assert client.put("/api/mission/status", json={"active_stage": "stage2"}).status_code == 200
    preflight = client.post("/api/safety/command-profile", json={"profile": "COMPETITION", "actuator_arm": True})
    assert preflight.status_code == 200
    assert preflight.json()["ready"] is True
    runtime.auto_tracker.start_tracking()
    for _ in range(3):
        runtime.auto_tracker.multi_target_tracker.update(event)
        tracks = runtime.auto_tracker.status().multi_target_tracker
        runtime.association.update(event, tracks)
        runtime.target_priority.update(tracks, runtime.association.status(), 640, 360, 320, 180)
        runtime.hit_confirmation.update(event, tracks)
    return runtime


def _candidate() -> dict:
    return {
        "frame_id": 1,
        "balloon_detection_id": 21,
        "balloon_track_id": 1,
        "body_detection_id": 11,
        "body_track_id": 101,
        "association_state": "stable",
    }


def _tx(runtime) -> list[str]:
    return [entry.raw for entry in runtime.serial.logs if entry.direction.value == "tx"]


def test_stage2_stable_priority_link_reaches_pico_and_registers_pending_confirmation(client: TestClient) -> None:
    runtime = _prepared_stage2(client)
    assert runtime.command_gateway.profile == CommandProfile.COMPETITION
    assert runtime.target_priority.status().selected_track_id == 1
    assert runtime.association.status().associations[0].state == "stable"

    result = runtime.command_gateway.fire_from_tracking(runtime, _candidate())

    assert result.accepted is True
    assert result.command == "LZR,1"
    assert "LZR,1" in _tx(runtime)
    record = runtime.hit_confirmation.status().records[0]
    assert record.balloon_track_id == 1
    assert record.body_track_id == 101
    assert record.state.value == "PENDING_CONFIRMATION"

    duplicate = runtime.command_gateway.fire_from_tracking(runtime, _candidate())
    assert duplicate.accepted is False
    assert duplicate.reason_codes == ["A2_HIT_CONFIRMATION_PENDING"]
    assert _tx(runtime).count("LZR,1") == 1


def test_stage2_ambiguous_or_missing_link_is_no_fire(client: TestClient) -> None:
    runtime = _prepared_stage2(client)
    runtime.association._status = AssociationStatus(
        associations=[BodyBalloonAssociation(balloon_track_id=1, body_detection_id=11, body_track_id=101, state="ambiguous")]
    )

    result = runtime.command_gateway.fire_from_tracking(runtime, _candidate())

    assert result.accepted is False
    assert result.reason_codes == ["A2_ASSOCIATION_NOT_STABLE"]
    assert "LZR,1" not in _tx(runtime)


def test_stage3_gateway_keeps_stable_association_gate_after_decision_passes(client: TestClient) -> None:
    from app.schemas.decision import DecisionStateValue
    from types import SimpleNamespace

    runtime = _prepared_stage2(client)
    assert client.put("/api/mission/status", json={"active_stage": "stage3"}).status_code == 200
    runtime.auto_tracker.tracking_active = True
    # This isolates the post-decision Gateway boundary.  A real DecisionEngine
    # must pass class/IFF/range first; even then an ambiguous link cannot fire.
    runtime.decision_engine.evaluate = lambda _runtime: SimpleNamespace(decision_state=DecisionStateValue.FIRE_READY, blocking_reasons=[], selected_body_detection_id=11)
    runtime.association._status = AssociationStatus(
        associations=[BodyBalloonAssociation(balloon_track_id=1, body_detection_id=11, body_track_id=101, state="ambiguous")]
    )

    result = runtime.command_gateway.fire_from_tracking(runtime, _candidate())

    assert result.accepted is False
    assert result.reason_codes == ["A3_ASSOCIATION_NOT_STABLE"]
    assert "LZR,1" not in _tx(runtime)


def test_stage3_gateway_requires_two_stable_friend_links_after_decision_passes(client: TestClient) -> None:
    from app.schemas.decision import DecisionStateValue
    from types import SimpleNamespace

    runtime = _prepared_stage2(client)
    assert client.put("/api/mission/status", json={"active_stage": "stage3"}).status_code == 200
    runtime.auto_tracker.tracking_active = True
    runtime.decision_engine.evaluate = lambda _runtime: SimpleNamespace(decision_state=DecisionStateValue.FIRE_READY, blocking_reasons=[], selected_body_detection_id=11)
    candidate = {**_candidate(), "body_class": "f16", "body_team": "enemy"}

    result = runtime.command_gateway.fire_from_tracking(runtime, candidate)

    assert result.accepted is False
    assert result.reason_codes == ["A3_FRIEND_SAFETY_EVIDENCE_INCOMPLETE"]
    assert "LZR,1" not in _tx(runtime)


def test_live_stage2_round_score_is_derived_from_confirmed_hit_not_operator_number(client: TestClient) -> None:
    from app.schemas.tracking import MultiTargetTrackingStatus

    runtime = _prepared_stage2(client)
    assert runtime.command_gateway.fire_from_tracking(runtime, _candidate()).accepted is True

    manual = client.post("/api/mission/stage2/round/complete", json={"confirmed_hits": 3})
    pending_close = client.post("/api/mission/stage2/round/close")
    assert manual.status_code == 409
    assert "A2_ENGAGEMENT_EVENT_API_REQUIRED" in manual.text
    assert pending_close.status_code == 409
    assert "A2_ROUND_CONFIRMATION_PENDING" in pending_close.text

    shot_at = runtime.hit_confirmation.status().records[0].shot_at
    confirmations = None
    for offset_s in (0.35, 0.42, 0.49, 0.56):
        confirmed_event = _event().model_copy(
            update={
                "timestamp_ms": int((shot_at + offset_s) * 1000),
                "body_detections": _event().body_detections,
                "balloon_detections": [],
            }
        )
        confirmations = runtime.hit_confirmation.update(confirmed_event, MultiTargetTrackingStatus())
    assert confirmations is not None
    runtime.stage2_engagement.observe(confirmations, runtime.mission.state.stage2_round)
    status = client.get("/api/mission/stage2/engagement").json()
    assert status["confirmed_track_ids"] == [1]
    assert status["ready_to_close"] is True

    closed = client.post("/api/mission/stage2/round/close")
    assert closed.status_code == 200
    assert closed.json()["state"]["stage2_round_events"][-1]["confirmed_hits"] == 1
    assert closed.json()["score"]["stage2_score"] == 5
    assert runtime.hit_confirmation.status().records == []
    assert runtime.auto_tracker.status().multi_target_tracker.active_track_count == 0


def test_confirmation_uses_stable_body_track_not_frame_local_detection_id() -> None:
    from app.schemas.tracking import MultiTargetTrackingStatus
    from app.services.hit_confirmation_service import HitConfirmationService

    service = HitConfirmationService(confirmation_timeout_s=1.0)
    service.register_shot(1, body_detection_id=11, body_track_id=101, shot_at=10.0)
    body_in_next_frame = BodyDetection(
        id=99,
        track_id=101,
        class_name="generic_target",
        class_id=0,
        confidence=0.9,
        bbox=BBox(x=10, y=10, w=20, h=20),
    )
    status = None
    for timestamp_ms in (10_350, 10_420, 10_490, 10_560):
        event = _event().model_copy(update={"timestamp_ms": timestamp_ms, "body_detections": [body_in_next_frame], "balloon_detections": []})
        status = service.update(event, MultiTargetTrackingStatus())
    assert status is not None
    assert status.records[0].state.value == "CONFIRMED_HIT"
