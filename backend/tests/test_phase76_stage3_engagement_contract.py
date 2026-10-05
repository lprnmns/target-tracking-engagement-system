from __future__ import annotations

from app.schemas.mission import MissionState
from app.schemas.stage3_engagement import Stage3FriendLink
from app.schemas.tracking import EngagementRecord, EngagementState, EngagementStatus, MultiTargetTrack, MultiTargetTrackingStatus
from app.schemas.vision import BBox, BodyDetection, VisionEvent
from app.services.mission_service import MissionService
from app.services.stage3_engagement_service import Stage3EngagementService


def _event(body_tracks: list[int]) -> VisionEvent:
    return VisionEvent(
        frame_id=1,
        timestamp_ms=10_100,
        source="stage3-engagement-contract",
        frame_width=640,
        frame_height=360,
        fps=30,
        preprocess_ms=1,
        inference_ms=1,
        postprocess_ms=1,
        total_latency_ms=3,
        body_detections=[
            BodyDetection(
                id=track + 100,
                track_id=track,
                class_name="helicopter",
                class_id=1,
                confidence=0.9,
                bbox=BBox(x=10, y=10, w=30, h=30),
                target_team="friend",
            )
            for track in body_tracks
        ],
        balloon_detections=[],
    )


def _tracks(*ids: int) -> MultiTargetTrackingStatus:
    return MultiTargetTrackingStatus(
        tracks=[
            MultiTargetTrack(
                track_id=track_id,
                detection_id=track_id,
                center_x=100,
                center_y=100,
                velocity_x=0,
                velocity_y=0,
                confidence=0.9,
                fresh=True,
            )
            for track_id in ids
        ]
    )


def _confirmed_enemy() -> EngagementStatus:
    return EngagementStatus(
        records=[
            EngagementRecord(
                balloon_track_id=1,
                body_detection_id=10,
                body_track_id=100,
                state=EngagementState.CONFIRMED_HIT,
                reason="LINKED_BODY_VISIBLE_BALLOON_LOST",
                shot_at=10,
            )
        ]
    )


def _stage3_mission(tmp_path) -> MissionService:
    mission = MissionService(path=tmp_path / "mission.json")
    mission.state = MissionState(active_stage="stage3")
    return mission


def test_confirmed_enemy_and_two_visible_friend_links_produce_canonical_stage3_score(tmp_path) -> None:
    service = Stage3EngagementService()
    service.register_shot(
        enemy_class="f16",
        enemy_balloon_track_id=1,
        friend_links=[Stage3FriendLink(balloon_track_id=2, body_track_id=102), Stage3FriendLink(balloon_track_id=3, body_track_id=103)],
        current_round=1,
        shot_at=10,
    )
    status = service.observe(_event([102, 103]), _tracks(2, 3), _confirmed_enemy(), current_round=1)

    assert status.enemy_hit_confirmed is True
    assert status.friend_safety_verified is True
    assert status.ready_to_close is True
    snapshot = service.close_round(_stage3_mission(tmp_path))
    assert snapshot.state.stage3_round_events[-1].enemy_class == "f16"
    assert snapshot.state.stage3_round_events[-1].enemy_hit is True
    assert snapshot.state.stage3_round_events[-1].friend_hit is False
    assert snapshot.score.stage3_score == 30


def test_friend_body_visible_with_linked_balloon_loss_is_penalized_not_safe(tmp_path) -> None:
    service = Stage3EngagementService()
    service.register_shot(
        enemy_class="helicopter",
        enemy_balloon_track_id=1,
        friend_links=[Stage3FriendLink(balloon_track_id=2, body_track_id=102), Stage3FriendLink(balloon_track_id=3, body_track_id=103)],
        current_round=1,
        shot_at=10,
    )
    status = service.observe(_event([102, 103]), _tracks(3), _confirmed_enemy(), current_round=1)

    assert status.friend_hit_suspected is True
    assert "A3_FRIEND_HIT_SUSPECTED" in status.reason_codes
    assert status.ready_to_close is True
    snapshot = service.close_round(_stage3_mission(tmp_path))
    event = snapshot.state.stage3_round_events[-1]
    assert event.enemy_hit is True
    assert event.friend_hit is True
    assert event.points - event.penalty == 10


def test_pending_enemy_or_missing_friend_safety_evidence_cannot_close_stage3_round(tmp_path) -> None:
    service = Stage3EngagementService()
    service.register_shot(
        enemy_class="mini_micro_uav",
        enemy_balloon_track_id=1,
        friend_links=[Stage3FriendLink(balloon_track_id=2, body_track_id=102), Stage3FriendLink(balloon_track_id=3, body_track_id=103)],
        current_round=1,
        shot_at=10,
    )
    pending = EngagementStatus(
        records=[EngagementRecord(balloon_track_id=1, state=EngagementState.PENDING_CONFIRMATION, reason="pending", shot_at=10)]
    )
    status = service.observe(_event([102, 103]), _tracks(1, 2, 3), pending, current_round=1)
    assert status.ready_to_close is False
    try:
        service.close_round(_stage3_mission(tmp_path))
    except ValueError as exc:
        assert str(exc) == "A3_ENEMY_CONFIRMATION_PENDING"
    else:  # pragma: no cover
        raise AssertionError("pending enemy confirmation closed a stage3 round")

    status = service.observe(_event([]), _tracks(2), _confirmed_enemy(), current_round=1)
    assert status.ready_to_close is False
    assert "A3_FRIEND_SAFETY_EVIDENCE_INCOMPLETE" in status.reason_codes
