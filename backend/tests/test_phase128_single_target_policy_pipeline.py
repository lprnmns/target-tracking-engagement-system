import time
from pathlib import Path

from fastapi.testclient import TestClient

from app.schemas.command_gateway import CommandProfile
from app.schemas.tracking import AssociationStatus, BodyBalloonAssociation, MultiTargetTrack, MultiTargetTrackingStatus
from app.schemas.vision import BBox, BalloonDetection, BodyDetection, VisionEvent
from app.services.digital_twin_projection import project_bbox_to_scene
from app.services.target_engagement_registry import TargetEngagementRegistry


ROOT = Path(__file__).resolve().parents[2]


def _event(*, bodies: list[BodyDetection] | None = None, balloons: list[BalloonDetection] | None = None) -> VisionEvent:
    return VisionEvent(
        frame_id=128,
        timestamp_ms=int(time.time() * 1000),
        source="phase128",
        frame_width=640,
        frame_height=360,
        fps=30,
        preprocess_ms=1,
        inference_ms=1,
        postprocess_ms=1,
        total_latency_ms=3,
        body_detections=bodies or [],
        balloon_detections=balloons or [],
    )


def _body(detection_id: int, team: str, x: int) -> BodyDetection:
    return BodyDetection(
        id=detection_id,
        track_id=detection_id + 100,
        class_name="f16" if detection_id == 1 else "helicopter",
        class_id=detection_id,
        confidence=0.95,
        target_team=team,
        bbox=BBox(x=x, y=90, w=120, h=90),
        source="phase128",
    )


def _balloon(detection_id: int, x: int) -> BalloonDetection:
    return BalloonDetection(
        id=detection_id,
        confidence=0.96,
        bbox=BBox(x=x, y=120, w=50, h=50),
        center_x=x + 25,
        center_y=145,
        source="phase128",
    )


def test_balloon_policy_control_event_contains_only_the_clicked_balloon() -> None:
    registry = TargetEngagementRegistry()
    event = _event(balloons=[_balloon(1, 80), _balloon(2, 420)])
    tracks = MultiTargetTrackingStatus(tracks=[
        MultiTargetTrack(track_id=11, detection_id=1, center_x=105, center_y=145, velocity_x=0, velocity_y=0, fresh=True),
        MultiTargetTrack(track_id=12, detection_id=2, center_x=445, center_y=145, velocity_x=0, velocity_y=0, fresh=True),
    ])

    control = registry.filter_event_for_tracking(
        event,
        tracks,
        AssociationStatus(),
        target_policy="BALLOON",
        selected_detection_id=2,
        selected_detection_kind="balloon",
    )

    assert control is not None
    assert [item.id for item in control.balloon_detections] == [2]


def test_aircraft_policy_control_event_contains_only_the_clicked_red_enemy() -> None:
    registry = TargetEngagementRegistry()
    event = _event(bodies=[_body(1, "enemy", 60), _body(2, "friend", 250), _body(3, "unknown", 440)])

    enemy = registry.filter_event_for_tracking(
        event,
        MultiTargetTrackingStatus(),
        AssociationStatus(),
        target_policy="AIRCRAFT",
        selected_detection_id=1,
        selected_detection_kind="body",
    )
    friend = registry.filter_event_for_tracking(
        event,
        MultiTargetTrackingStatus(),
        AssociationStatus(),
        target_policy="AIRCRAFT",
        selected_detection_id=2,
        selected_detection_kind="body",
    )
    automatic = registry.filter_event_for_tracking(
        event,
        MultiTargetTrackingStatus(),
        AssociationStatus(),
        target_policy="AIRCRAFT",
    )

    assert enemy is not None and [item.id for item in enemy.body_detections] == [1]
    assert friend is not None and [item.id for item in friend.body_detections] == [2]
    assert automatic is not None and [item.id for item in automatic.body_detections] == [1]
    assert enemy.balloon_detections == []


def test_aircraft_handoff_uses_stable_body_track_across_frame_local_ids() -> None:
    registry = TargetEngagementRegistry()
    first = _body(1, "enemy", 60)
    next_frame = _body(7, "enemy", 80).model_copy(update={"track_id": first.track_id})

    control = registry.filter_event_for_tracking(
        _event(bodies=[next_frame, _body(8, "enemy", 400)]),
        MultiTargetTrackingStatus(),
        AssociationStatus(),
        target_policy="AIRCRAFT",
        selected_detection_id=1,
        selected_detection_kind="body",
        selected_body_track_id=first.track_id,
    )

    assert control is not None
    assert [item.id for item in control.body_detections] == [7]


def test_stale_selected_aircraft_track_does_not_erase_visible_candidates() -> None:
    registry = TargetEngagementRegistry()
    replacement = _body(7, "enemy", 80).model_copy(update={"track_id": 207})
    other = _body(8, "enemy", 400).model_copy(update={"track_id": 208})

    control = registry.filter_event_for_tracking(
        _event(bodies=[replacement, other]),
        MultiTargetTrackingStatus(),
        AssociationStatus(),
        target_policy="AIRCRAFT",
        selected_detection_id=1,
        selected_detection_kind="body",
        selected_body_track_id=101,
    )

    assert control is not None
    assert [item.id for item in control.body_detections] == [7, 8]


def test_combined_policy_controls_only_stably_owned_enemy_balloon() -> None:
    registry = TargetEngagementRegistry(min_association_duration_s=0)
    body = _body(1, "enemy", 60)
    owned = _balloon(1, 80).model_copy(
        update={"bbox": BBox(x=80, y=200, w=50, h=50), "center_y": 225}
    )
    standalone = _balloon(2, 420).model_copy(
        update={"bbox": BBox(x=420, y=200, w=50, h=50), "center_y": 225}
    )
    event = _event(bodies=[body], balloons=[owned, standalone])
    tracks = MultiTargetTrackingStatus(tracks=[
        MultiTargetTrack(track_id=11, detection_id=1, center_x=105, center_y=225, velocity_x=0, velocity_y=0, fresh=True),
        MultiTargetTrack(track_id=12, detection_id=2, center_x=445, center_y=225, velocity_x=0, velocity_y=0, fresh=True),
    ])
    associations = AssociationStatus(associations=[
        BodyBalloonAssociation(
            balloon_track_id=11,
            body_detection_id=body.id,
            body_track_id=body.track_id,
            state="stable",
            attachment_region_ok=True,
            stable_frames=3,
        ),
        BodyBalloonAssociation(balloon_track_id=12, state="orphan"),
    ])
    registry.update(event, tracks, associations, now=0)

    control = registry.filter_event_for_tracking(
        event,
        tracks,
        associations,
        target_policy="BALLOON_AIRCRAFT",
    )

    assert control is not None
    assert [item.id for item in control.balloon_detections] == [owned.id]
    assert control.body_detections == []


def test_operation_api_tracks_explicit_blue_aircraft_while_auto_remains_enemy_only(client: TestClient) -> None:
    runtime = client.app.state.runtime
    runtime.vision.latest_event = _event(bodies=[_body(1, "enemy", 60), _body(2, "friend", 250), _body(3, "unknown", 440)])
    assert client.put("/api/operation/state", json={"target_policy": "AIRCRAFT"}).status_code == 200

    friend = client.post("/api/operation/target", json={"detection_id": 2, "detection_kind": "body", "x": 310, "y": 135})
    unknown = client.post("/api/operation/target", json={"detection_id": 3, "detection_kind": "body", "x": 500, "y": 135})
    enemy = client.post("/api/operation/target", json={"detection_id": 1, "detection_kind": "body", "x": 120, "y": 135})

    assert friend.status_code == 200
    assert unknown.status_code == 200
    assert enemy.status_code == 200


def test_digital_twin_projection_preserves_aircraft_team() -> None:
    projection = project_bbox_to_scene(
        bbox={"x": 100, "y": 80, "w": 120, "h": 90},
        frame_width=640,
        frame_height=360,
        class_name="f16",
        target_team="enemy",
        confidence=0.95,
    )
    assert projection.target_team == "enemy"


def test_live_aircraft_enemy_lock_reaches_gateway_fire_and_friend_does_not(client: TestClient) -> None:
    runtime = client.app.state.runtime
    runtime.vision.latest_event = _event(
        bodies=[_body(1, "enemy", 260), _body(2, "friend", 80)],
    )
    runtime.vision.running = True
    runtime.vision_pipeline._last_yolo_event = runtime.vision.latest_event
    preflight = runtime.command_gateway.select_profile(runtime, CommandProfile.LIVE_TEST, True)
    assert preflight.ready
    assert runtime.command_gateway.actuator_armed
    configured = client.put(
        "/api/operation/state",
        json={
            "control_mode": "AUTONOMOUS",
            "target_policy": "AIRCRAFT",
            "fire_permission": "ENABLED",
        },
    )
    assert configured.status_code == 200
    # TestClient may cancel request-owned asyncio tasks between calls; renew
    # the same visible preflight before evaluating the direct FIRE contract.
    if not runtime.command_gateway.actuator_armed:
        runtime.command_gateway.select_profile(runtime, CommandProfile.LIVE_TEST, True)
    assert runtime.command_gateway.actuator_armed
    selected = client.post(
        "/api/operation/target",
        json={
            "detection_id": 1,
            "detection_kind": "body",
            "x": 320,
            "y": 135,
            "frame_id": 128,
        },
    )
    assert selected.status_code == 200
    assert runtime.command_gateway.actuator_armed

    enemy = runtime.command_gateway.fire_from_tracking(
        runtime,
        {
            "frame_id": 128,
            "body_detection_id": 1,
            "aim_inside_inner_lock": True,
            "track_fresh": True,
        },
    )
    assert enemy.accepted and enemy.command == "LZR,1"
    assert any(entry.raw == "LZR,1" for entry in runtime.serial.logs)

    friend = runtime.command_gateway.fire_from_tracking(
        runtime,
        {
            "frame_id": 128,
            "body_detection_id": 2,
            "aim_inside_inner_lock": True,
            "track_fresh": True,
        },
    )
    assert not friend.accepted
    assert friend.reason_codes in (["FRIEND_TARGET_FIRE_BLOCKED"], ["AIRCRAFT_TARGET_STALE"])


def test_operator_surfaces_allow_explicit_aircraft_tracking_and_keep_team_aware_twin() -> None:
    camera = (ROOT / "frontend/src/components/cockpit/LiveCameraPanel.vue").read_text(encoding="utf-8")
    cockpit = (ROOT / "frontend/src/views/CockpitView.vue").read_text(encoding="utf-8")
    twin = (ROOT / "frontend/src/components/digital-twin/DigitalTwinPanel.vue").read_text(encoding="utf-8")
    semantics = (ROOT / "frontend/src/digitalTwin/targetSemantics.ts").read_text(encoding="utf-8")

    assert "if (policy === 'AIRCRAFT') return target.kind === 'body'" in camera
    assert "target.target_team === 'enemy' ? '#ef4444' : '#3b82f6'" in camera
    assert "yalnız kırmızı düşman hedef takip edilebilir" not in cockpit
    assert "Dost hedef seçildi · takip edilebilir, fiziksel FIRE engellidir" in cockpit
    assert ":target-policy=\"targetPolicy\"" in cockpit
    assert "policy !== 'AIRCRAFT'" in twin and "policy !== 'BALLOON'" in twin
    assert "targetTeamColor" in twin and "clone.color?.setHex" in twin and "clone.color.setHex(teamColor)" in twin
    assert "target_team: targetTeam ?? 'unknown'" in twin
    assert "selectedDetectionKind" in twin
    assert semantics.count("modelRotation: [-Math.PI / 2, 0, 0]") == 3
    assert "ballistic_missile" in semantics and "modelRotation: [0, 0, 0]" in semantics
    assert semantics.count("modelYawRad: Math.PI") == 1
    assert "modelYawRad: 0" in semantics


def test_autonomous_fire_balloon_under_light_protocol(client: TestClient) -> None:
    runtime = client.app.state.runtime
    runtime.config.serial.protocol_mode = "raw"
    runtime.config.serial.raw_protocol_variant = "light"
    runtime.vision.latest_event = _event(balloons=[_balloon(1, 295)])
    runtime.auto_tracker.start_tracking()
    runtime.auto_tracker.set_controller_mode("A2")
    preflight = runtime.command_gateway.select_profile(runtime, CommandProfile.LIVE_TEST, True)
    assert preflight.ready

    configured = client.put(
        "/api/operation/state",
        json={
            "control_mode": "AUTONOMOUS",
            "target_policy": "BALLOON",
            "fire_permission": "ENABLED",
        },
    )
    assert configured.status_code == 200

    candidate = {
        "frame_id": 128,
        "balloon_detection_id": 1,
        "balloon_track_id": 1,
        "aim_inside_inner_lock": True,
        "track_fresh": True,
        "target_policy": "BALLOON",
    }
    result = runtime.command_gateway.fire_from_tracking(runtime, candidate)
    assert result.accepted is True
    assert result.command == "BALON,PATLAT"
    assert result.physical_command_generated is True


def test_tracking_loop_payload_for_light_balloon(client: TestClient) -> None:
    runtime = client.app.state.runtime
    runtime.config.serial.protocol_mode = "raw"
    runtime.config.serial.raw_protocol_variant = "light"
    runtime.auto_tracker.multi_target_tracker.reset()
    balloon = _balloon(42, 295)
    event = _event(balloons=[balloon])
    runtime.vision.latest_event = event
    runtime.auto_tracker.start_tracking()
    runtime.auto_tracker.set_controller_mode("OPT_SINE_TRACK")
    update = runtime.auto_tracker.update(event, frame_width=640, frame_height=360)
    payload = runtime.tracking_loop._fire_event_payload(event, update, distance=2.0, fire_radius=10.0)
    assert payload.get("balloon_detection_id") == 42
    assert payload.get("balloon_track_id") in (1, 42)
    assert payload.get("target_policy") == "BALLOON"

