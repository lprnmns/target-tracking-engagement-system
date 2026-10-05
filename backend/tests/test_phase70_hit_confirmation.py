from app.schemas.tracking import EngagementState, MultiTargetTrack, MultiTargetTrackingStatus
from app.schemas.vision import BBox, BodyDetection, VisionEvent
from app.services.hit_confirmation_service import HitConfirmationService


def _event(timestamp_ms: int, bodies: list[BodyDetection]) -> VisionEvent:
    return VisionEvent(
        frame_id=1, timestamp_ms=timestamp_ms, source="hit_confirmation_test", frame_width=640, frame_height=360,
        fps=30, preprocess_ms=1, inference_ms=1, postprocess_ms=1, total_latency_ms=3,
        body_detections=bodies, balloon_detections=[],
    )


def _body(identifier: int) -> BodyDetection:
    return BodyDetection(id=identifier, class_name="target", class_id=0, confidence=0.9, bbox=BBox(x=100, y=100, w=40, h=40))


def _fresh_track(track_id: int) -> MultiTargetTrackingStatus:
    return MultiTargetTrackingStatus(tracks=[MultiTargetTrack(track_id=track_id, detection_id=track_id, center_x=120, center_y=120, velocity_x=0, velocity_y=0, confidence=0.9, fresh=True)])


def test_shot_requires_stable_linked_body_visible_balloon_loss() -> None:
    service = HitConfirmationService(confirmation_timeout_s=1.0, missing_frames_required=4, missing_duration_s=0.15)
    service.register_shot(7, 101, shot_at=10.0)
    pending = service.update(_event(10_100, [_body(101)]), _fresh_track(7))
    service.update(_event(10_350, [_body(101)]), MultiTargetTrackingStatus())
    service.update(_event(10_420, [_body(101)]), MultiTargetTrackingStatus())
    service.update(_event(10_490, [_body(101)]), MultiTargetTrackingStatus())
    service.update(_event(10_800, [_body(101)]), MultiTargetTrackingStatus())
    confirmed = service.update(_event(11_050, [_body(101)]), MultiTargetTrackingStatus())

    assert pending.records[0].state == EngagementState.PENDING_CONFIRMATION
    assert confirmed.records[0].state == EngagementState.CONFIRMED_HIT
    assert confirmed.records[0].reason == "LINKED_BODY_VISIBLE_BALLOON_LOST_STABLE"
    assert confirmed.records[0].outcome.value == "HIT_CONFIRMED"


def test_missing_confirmation_becomes_reengage_not_false_hit() -> None:
    service = HitConfirmationService(confirmation_timeout_s=1.0)
    service.register_shot(7, 101, shot_at=10.0)

    status = service.update(_event(11_100, []), MultiTargetTrackingStatus())

    assert status.records[0].state == EngagementState.REENGAGE
    assert status.confirmed_hit_count == 0
    assert status.records[0].outcome.value == "UNCONFIRMED"


def test_single_missing_frame_is_not_a_false_hit() -> None:
    service = HitConfirmationService(confirmation_timeout_s=1.0, missing_frames_required=4, missing_duration_s=0.15)
    service.register_shot(7, 101, shot_at=10.0)

    status = service.update(_event(10_400, [_body(101)]), MultiTargetTrackingStatus())

    assert status.records[0].state == EngagementState.PENDING_CONFIRMATION
    assert status.records[0].outcome.value == "PENDING"


def test_balloon_still_visible_in_primary_window_becomes_confirmed_miss() -> None:
    service = HitConfirmationService(confirmation_timeout_s=1.5, primary_evidence_window_s=0.8)
    service.register_shot(7, 101, shot_at=10.0)

    status = service.update(_event(10_850, [_body(101)]), _fresh_track(7))

    assert status.records[0].state == EngagementState.REENGAGE
    assert status.records[0].outcome.value == "MISS_CONFIRMED"
    assert status.records[0].reason == "BALLOON_STILL_VISIBLE_MISS_CONFIRMED"


def test_standalone_balloon_missing_for_one_second_confirms_hit() -> None:
    service = HitConfirmationService(missing_frames_required=3, missing_duration_s=0.15)
    service.register_shot(7, None, body_track_id=None, shot_at=10.0)

    service.update(_event(10_350, []), MultiTargetTrackingStatus())
    service.update(_event(10_650, []), MultiTargetTrackingStatus())
    status = service.update(_event(11_050, []), MultiTargetTrackingStatus())

    assert status.records[0].state == EngagementState.CONFIRMED_HIT
    assert status.records[0].reason == "STANDALONE_BALLOON_LOST_STABLE"
