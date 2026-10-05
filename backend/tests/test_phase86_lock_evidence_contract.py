import json
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from app.schemas.tracking import AssociationStatus, BodyBalloonAssociation, EngagementOutcome, EngagementRecord, EngagementState, EngagementStatus, MultiTargetTrack, MultiTargetTrackingStatus, TrackingState, TrackingUpdate
from app.schemas.vision import BBox, BalloonDetection, BodyDetection, VisionEvent
from app.services.engagement_evidence_service import EngagementEvidenceService


class _Logger:
    def emit(self, *_args, **_kwargs):
        return None


def _event() -> VisionEvent:
    return VisionEvent(
        frame_id=44, timestamp_ms=int(time.time() * 1000), source="test", frame_width=640, frame_height=360,
        fps=30, preprocess_ms=1, inference_ms=2, postprocess_ms=1, total_latency_ms=4,
        balloon_detections=[BalloonDetection(id=8, confidence=0.95, bbox=BBox(x=290, y=150, w=40, h=40), center_x=310, center_y=170)],
        body_detections=[BodyDetection(id=4, track_id=77, class_name="f16", class_id=1, confidence=0.9, target_team="enemy", bbox=BBox(x=220, y=90, w=180, h=170))],
    )


def _update(state: TrackingState) -> TrackingUpdate:
    return TrackingUpdate(state=state, frame_id=44, frame_center_x=320, frame_center_y=180, target_center_x=310, target_center_y=170)


def _tracks() -> MultiTargetTrackingStatus:
    return MultiTargetTrackingStatus(tracks=[MultiTargetTrack(track_id=12, detection_id=8, center_x=310, center_y=170, velocity_x=0, velocity_y=0, age_frames=5, hits=5, confidence=0.95, fresh=True)])


def _associations() -> AssociationStatus:
    return AssociationStatus(associations=[BodyBalloonAssociation(balloon_track_id=12, body_detection_id=4, body_track_id=77, state="stable", confidence=0.9, stable_frames=5)])


def _aircraft_event(frame_id: int, *, track_id: int | None = 77, detection_id: int = 4) -> VisionEvent:
    return VisionEvent(
        frame_id=frame_id, timestamp_ms=int(time.time() * 1000) + frame_id, source="test", frame_width=640, frame_height=360,
        fps=30, preprocess_ms=1, inference_ms=2, postprocess_ms=1, total_latency_ms=4,
        balloon_detections=[],
        body_detections=[BodyDetection(id=detection_id, track_id=track_id, class_name="f16", class_id=1, confidence=0.9, target_team="enemy", bbox=BBox(x=220, y=90, w=180, h=170))],
    )


def _aircraft_update(frame_id: int, *, track_id: int | None = 77, detection_id: int = 4) -> TrackingUpdate:
    return TrackingUpdate(
        state=TrackingState.LOCKED,
        frame_id=frame_id,
        frame_center_x=320,
        frame_center_y=180,
        target_center_x=310,
        target_center_y=175,
        selected_target_kind="body",
        selected_track_id=track_id,
        selected_detection_id=detection_id,
    )


def test_lock_starts_evidence_with_preroll_and_no_command(tmp_path) -> None:
    service = EngagementEvidenceService(logger=_Logger(), root=tmp_path)
    service.observe_frame(_event(), _update(TrackingState.TRACKING), _tracks(), _associations(), mission_stage="stage2", command_profile="DRY_RUN")
    status = service.observe_frame(_event(), _update(TrackingState.LOCKED), _tracks(), _associations(), mission_stage="stage2", command_profile="DRY_RUN")
    service.flush()

    assert status.active is not None
    assert status.active.state == "LOCKED_RECORDING"
    assert status.active.balloon_track_id == 12
    assert status.active.body_track_id == 77
    assert status.active.no_physical_command_generated is True
    manifest = tmp_path / status.active.evidence_path / "manifest.json"
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert payload["summary"]["reason_codes"][0] == "TARGET_LOCKED_RECORDING_STARTED"
    assert len(payload["lock_snapshot"]["pre_roll"]) == 2


def test_aircraft_only_lock_keeps_one_stable_evidence_record_across_frames(tmp_path) -> None:
    service = EngagementEvidenceService(logger=_Logger(), root=tmp_path)

    for frame_id in range(100, 120):
        service.observe_frame(
            _aircraft_event(frame_id),
            _aircraft_update(frame_id),
            MultiTargetTrackingStatus(),
            AssociationStatus(),
            mission_stage="stage1",
            command_profile="LIVE_TEST",
        )
    service.flush()

    status = service.status()
    assert status.active is not None
    assert status.active.body_track_id == 77
    assert status.active.balloon_track_id is None
    assert status.recent == []
    assert len(list(tmp_path.glob("*/*/manifest.json"))) == 1


def test_aircraft_lock_without_stable_body_track_does_not_create_per_frame_records(tmp_path) -> None:
    service = EngagementEvidenceService(logger=_Logger(), root=tmp_path)

    for frame_id in range(200, 210):
        service.observe_frame(
            _aircraft_event(frame_id, track_id=None, detection_id=frame_id),
            _aircraft_update(frame_id, track_id=None, detection_id=frame_id),
            MultiTargetTrackingStatus(),
            AssociationStatus(),
            mission_stage="stage1",
            command_profile="LIVE_TEST",
        )

    assert service.status().active is None
    assert list(tmp_path.glob("*/*/manifest.json")) == []


def test_nonlocked_target_does_not_create_evidence(tmp_path) -> None:
    service = EngagementEvidenceService(logger=_Logger(), root=tmp_path)
    status = service.observe_frame(_event(), _update(TrackingState.TRACKING), _tracks(), _associations(), mission_stage="stage2", command_profile="DRY_RUN")
    assert status.active is None


def test_locked_evidence_saves_actual_camera_jpeg_sequence(tmp_path) -> None:
    class _Camera:
        def evidence_frame_copy(self):
            return np.full((24, 32, 3), 127, dtype=np.uint8), time.time()

    service = EngagementEvidenceService(logger=_Logger(), root=tmp_path)
    service.observe_frame(_event(), _update(TrackingState.LOCKED), _tracks(), _associations(), mission_stage="stage2", command_profile="DRY_RUN")
    service.capture_active_camera_frame(_Camera())
    service.flush()

    active = service.status().active
    assert active is not None
    assert active.camera_capture_status == "JPEG_SEQUENCE"
    event_dir = tmp_path / active.evidence_path
    images = list((event_dir / "camera_frames").glob("*.jpg"))
    assert len(images) == 1
    assert images[0].read_bytes()[:2] == b"\xff\xd8"
    manifest = json.loads((event_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["camera_capture"]["status"] == "JPEG_SEQUENCE"


def test_digital_twin_projectile_contract_is_ack_only() -> None:
    root = Path(__file__).resolve().parents[2]
    panel = (root / "frontend/src/components/digital-twin/DigitalTwinPanel.vue").read_text(encoding="utf-8")
    gateway = (root / "backend/app/services/command_gateway.py").read_text(encoding="utf-8")

    assert "PICO ACK · visual trajectory" in panel
    assert "acknowledged_visual_projectile" in panel
    assert "props.replayControl.positionMs" in panel
    assert "record_shot_ack(runtime, candidate, result)" in gateway
    assert "fire_from_tracking(" not in panel
    replay_panel = (root / "frontend/src/components/cockpit/EngagementEvidenceReplayPanel.vue").read_text(encoding="utf-8")
    assert "@timeupdate=\"publishReplayControl\"" in replay_panel
    assert "0.25×" in replay_panel


def test_evidence_status_api_is_read_only_and_available(client) -> None:
    response = client.get("/api/engagement-evidence/status")
    assert response.status_code == 200
    payload = response.json()
    assert payload["active"] is None
    assert payload["no_physical_command_generated"] is True
    records = client.get("/api/engagement-evidence/records")
    assert records.status_code == 200
    assert records.json()["records"] == []
    assert records.json()["no_physical_command_generated"] is True


def test_terminal_hit_outcome_is_persisted_to_the_same_locked_engagement(tmp_path) -> None:
    class _Result:
        accepted = True

        def model_dump(self, **_kwargs):
            return {"accepted": True, "command": "LZR,1", "physical_command_generated": False}

    service = EngagementEvidenceService(logger=_Logger(), root=tmp_path)
    service.observe_frame(_event(), _update(TrackingState.LOCKED), _tracks(), _associations(), mission_stage="stage2", command_profile="DRY_RUN")
    service.capture_active_camera_frame(SimpleNamespace(evidence_frame_copy=lambda: (np.full((24, 32, 3), 127, dtype=np.uint8), time.time())))
    runtime = SimpleNamespace(motion=SimpleNamespace(status=lambda: SimpleNamespace(model_dump=lambda **_kwargs: {})))
    shot = service.record_shot_ack(runtime, {"balloon_track_id": 12}, _Result())
    assert shot is not None
    confirmations = EngagementStatus(records=[EngagementRecord(
        balloon_track_id=12,
        state=EngagementState.CONFIRMED_HIT,
        outcome=EngagementOutcome.HIT_CONFIRMED,
        reason="LINKED_BODY_VISIBLE_BALLOON_LOST_STABLE",
        shot_at=time.time(),
    )])
    committed = service.record_confirmation_status(confirmations)
    service._post_roll_until = 0.0  # controlled clock advance for the writer contract
    service.finalize_due_recording()
    service.flush()

    assert committed is not None
    assert committed.outcome == "HIT_CONFIRMED"
    manifest = json.loads((tmp_path / committed.evidence_path / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["outcome"]["outcome"] == "HIT_CONFIRMED"
    assert service.records()[0].engagement_id == committed.engagement_id
    review_status = json.loads((tmp_path / committed.evidence_path / "camera_review_status.json").read_text(encoding="utf-8"))
    assert review_status["status"] in {"READY", "UNAVAILABLE"}


def test_engagement_captures_read_only_digital_twin_replay(client) -> None:
    runtime = client.app.state.runtime
    service = runtime.engagement_evidence
    service.observe_frame(_event(), _update(TrackingState.LOCKED), _tracks(), _associations(), mission_stage="stage2", command_profile="DRY_RUN")
    service.capture_active_digital_twin_state(lambda: runtime.digital_twin.state(runtime))
    service.flush()

    active = service.status().active
    assert active is not None
    replay = service.digital_twin_replay(active.engagement_id)
    assert replay.source == "engagement_evidence_read_only"
    assert replay.event_count == 1
    assert replay.no_physical_command_generated is True
    response = client.get(f"/api/engagement-evidence/records/{active.engagement_id}/digital-twin-replay")
    assert response.status_code == 200
    assert response.json()["event_count"] == 1
