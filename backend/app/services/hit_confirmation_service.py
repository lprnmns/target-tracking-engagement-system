"""Shot → visual confirmation lifecycle; never equates a shot with a hit."""

from __future__ import annotations

import time

from app.schemas.tracking import EngagementOutcome, EngagementRecord, EngagementState, EngagementStatus, MultiTargetTrackingStatus
from app.schemas.vision import VisionEvent


class HitConfirmationService:
    def __init__(
        self,
        confirmation_timeout_s: float = 1.5,
        grace_period_s: float = 0.3,
        primary_evidence_window_s: float = 1.0,
        missing_frames_required: int = 4,
        missing_duration_s: float = 0.15,
    ) -> None:
        self.confirmation_timeout_s = confirmation_timeout_s
        self.grace_period_s = grace_period_s
        self.primary_evidence_window_s = primary_evidence_window_s
        self.missing_frames_required = missing_frames_required
        self.missing_duration_s = missing_duration_s
        self._records: dict[int, EngagementRecord] = {}

    def reset(self) -> None:
        self._records.clear()

    def register_shot(
        self,
        balloon_track_id: int,
        body_detection_id: int | None,
        body_track_id: int | None = None,
        shot_at: float | None = None,
    ) -> EngagementRecord:
        now = time.time() if shot_at is None else shot_at
        existing = self._records.get(balloon_track_id)
        record = EngagementRecord(
            balloon_track_id=balloon_track_id,
            body_detection_id=body_detection_id,
            body_track_id=body_track_id,
            state=EngagementState.PENDING_CONFIRMATION,
            shot_count=(existing.shot_count + 1) if existing else 1,
            reason="SHOT_PENDING_VISUAL_CONFIRMATION",
            shot_at=now,
            updated_at=now,
        )
        self._records[balloon_track_id] = record
        return record

    def update(self, event: VisionEvent | None, tracks: MultiTargetTrackingStatus) -> EngagementStatus:
        now = time.time() if event is None else float(event.timestamp_ms) / 1000.0
        fresh_track_ids = {track.track_id for track in tracks.tracks if track.fresh}
        visible_body_ids = {body.id for body in event.body_detections} if event is not None else set()
        visible_body_track_ids = {(body.track_id if body.track_id is not None else body.id) for body in event.body_detections} if event is not None else set()
        for track_id, record in list(self._records.items()):
            if record.state != EngagementState.PENDING_CONFIRMATION:
                continue
            elapsed = max(0.0, now - record.shot_at)
            if elapsed < self.grace_period_s:
                self._records[track_id] = record.model_copy(update={"updated_at": now})
                continue
            # A linked body remains visible but its balloon track has vanished:
            # this is the conservative visual-loss evidence supported today.
            body_still_visible = (
                record.body_track_id is not None and record.body_track_id in visible_body_track_ids
            ) or (
                record.body_track_id is None and record.body_detection_id is not None and record.body_detection_id in visible_body_ids
            )
            balloon_visible = track_id in fresh_track_ids
            if balloon_visible:
                if elapsed >= self.primary_evidence_window_s:
                    self._records[track_id] = record.model_copy(
                        update={
                            "state": EngagementState.REENGAGE,
                            "outcome": EngagementOutcome.MISS_CONFIRMED,
                            "balloon_visible_after_grace": True,
                            "reason": "BALLOON_STILL_VISIBLE_MISS_CONFIRMED",
                            "updated_at": now,
                        }
                    )
                    continue
                self._records[track_id] = record.model_copy(
                    update={
                        "balloon_visible_after_grace": True,
                        "balloon_missing_frames": 0,
                        "balloon_missing_since": None,
                        "updated_at": now,
                    }
                )
                continue
            standalone_balloon = record.body_track_id is None and record.body_detection_id is None
            if body_still_visible or standalone_balloon:
                missing_since = record.balloon_missing_since if record.balloon_missing_since is not None else now
                missing_frames = record.balloon_missing_frames + 1
                missing_duration = max(0.0, now - missing_since)
                if (
                    elapsed >= self.primary_evidence_window_s
                    and missing_frames >= self.missing_frames_required
                    and missing_duration >= self.missing_duration_s
                ):
                    self._records[track_id] = record.model_copy(
                        update={
                            "state": EngagementState.CONFIRMED_HIT,
                            "outcome": EngagementOutcome.HIT_CONFIRMED,
                            "balloon_missing_frames": missing_frames,
                            "balloon_missing_since": missing_since,
                            "reason": (
                                "STANDALONE_BALLOON_LOST_STABLE"
                                if standalone_balloon
                                else "LINKED_BODY_VISIBLE_BALLOON_LOST_STABLE"
                            ),
                            "updated_at": now,
                        }
                    )
                else:
                    if elapsed >= self.confirmation_timeout_s:
                        self._records[track_id] = record.model_copy(
                            update={
                                "state": EngagementState.REENGAGE,
                                "outcome": EngagementOutcome.UNCONFIRMED,
                                "balloon_missing_frames": missing_frames,
                                "balloon_missing_since": missing_since,
                                "reason": "BALLOON_LOSS_INSUFFICIENT_EVIDENCE",
                                "updated_at": now,
                            }
                        )
                        continue
                    self._records[track_id] = record.model_copy(
                        update={
                            "balloon_missing_frames": missing_frames,
                            "balloon_missing_since": missing_since,
                            "updated_at": now,
                        }
                    )
                continue
            self._records[track_id] = record.model_copy(
                update={"body_lost_during_confirmation": True, "updated_at": now}
            )
            if elapsed >= self.confirmation_timeout_s:
                outcome = EngagementOutcome.MISS_CONFIRMED if record.balloon_visible_after_grace else EngagementOutcome.UNCONFIRMED
                reason = "BALLOON_STILL_VISIBLE_MISS_CONFIRMED" if outcome == EngagementOutcome.MISS_CONFIRMED else "BODY_OR_CAMERA_EVIDENCE_LOST_UNCONFIRMED"
                self._records[track_id] = record.model_copy(
                    update={
                        "state": EngagementState.REENGAGE,
                        "outcome": outcome,
                        "reason": reason,
                        "updated_at": now,
                    }
                )
        return self.status(now)

    def status(self, now: float | None = None) -> EngagementStatus:
        records = sorted(self._records.values(), key=lambda item: item.balloon_track_id)
        return EngagementStatus(
            records=records,
            pending_count=sum(item.state == EngagementState.PENDING_CONFIRMATION for item in records),
            confirmed_hit_count=sum(item.state == EngagementState.CONFIRMED_HIT for item in records),
            reengage_count=sum(item.state == EngagementState.REENGAGE for item in records),
            updated_at=time.time() if now is None else now,
        )
