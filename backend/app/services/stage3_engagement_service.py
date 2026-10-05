"""Fail-closed A3 round evidence from Gateway shots and visual confirmation."""

from __future__ import annotations

import time

from app.schemas.mission import MissionSnapshot, Stage3RoundCompleteRequest
from app.schemas.stage3_engagement import Stage3EngagementStatus, Stage3FriendLink
from app.schemas.tracking import EngagementStatus, MultiTargetTrackingStatus
from app.schemas.vision import VisionEvent


class Stage3EngagementService:
    def __init__(self) -> None:
        self._status = Stage3EngagementStatus()

    def reset(self, current_round: int = 1) -> Stage3EngagementStatus:
        self._status = Stage3EngagementStatus(current_round=current_round)
        return self._status

    def register_shot(
        self,
        *,
        enemy_class: str,
        enemy_balloon_track_id: int,
        friend_links: list[Stage3FriendLink],
        current_round: int,
        shot_at: float | None = None,
    ) -> Stage3EngagementStatus:
        self._status = Stage3EngagementStatus(
            current_round=current_round,
            enemy_class=enemy_class,  # validated by the Gateway's perception path
            enemy_balloon_track_id=enemy_balloon_track_id,
            friend_links=friend_links,
            enemy_confirmation_state="PENDING_CONFIRMATION",
            reason_codes=["A3_ENEMY_CONFIRMATION_PENDING"],
            shot_at=time.time() if shot_at is None else shot_at,
        )
        return self._status

    def observe(
        self,
        event: VisionEvent | None,
        tracks: MultiTargetTrackingStatus,
        confirmations: EngagementStatus,
        current_round: int,
    ) -> Stage3EngagementStatus:
        status = self._status
        if status.enemy_balloon_track_id is None or status.enemy_class is None:
            return self._status
        record = next((item for item in confirmations.records if item.balloon_track_id == status.enemy_balloon_track_id), None)
        confirmation_state = record.state.value if record is not None else "PENDING_CONFIRMATION"
        enemy_confirmed = confirmation_state == "CONFIRMED_HIT"
        pending = confirmation_state == "PENDING_CONFIRMATION"
        fresh_balloon_ids = {track.track_id for track in tracks.tracks if track.fresh}
        visible_body_track_ids = {
            body.track_id if body.track_id is not None else body.id
            for body in (event.body_detections if event is not None else [])
        }
        friend_hit_suspected = False
        friend_safety_verified = False
        reasons: list[str] = []
        if pending:
            reasons.append("A3_ENEMY_CONFIRMATION_PENDING")
        elif confirmation_state == "REENGAGE":
            reasons.append("A3_ENEMY_REENGAGE_REQUIRED")
        if len(status.friend_links) != 2:
            reasons.append("A3_FRIEND_SAFETY_EVIDENCE_INCOMPLETE")
        else:
            missing_friend_tracks = [link for link in status.friend_links if link.balloon_track_id not in fresh_balloon_ids]
            # The same conservative visual evidence used for enemy hit
            # confirmation: a friend's body remains visible but its linked
            # balloon has disappeared.  This never becomes a false SAFE.
            friend_hit_suspected = any(link.body_track_id in visible_body_track_ids for link in missing_friend_tracks)
            friend_safety_verified = not missing_friend_tracks
            if friend_hit_suspected:
                reasons.append("A3_FRIEND_HIT_SUSPECTED")
            elif not friend_safety_verified:
                reasons.append("A3_FRIEND_SAFETY_EVIDENCE_INCOMPLETE")
        ready = not pending and (friend_safety_verified or friend_hit_suspected)
        self._status = status.model_copy(
            update={
                "current_round": current_round,
                "enemy_confirmation_state": confirmation_state,
                "enemy_hit_confirmed": enemy_confirmed,
                "friend_safety_verified": friend_safety_verified,
                "friend_hit_suspected": friend_hit_suspected,
                "ready_to_close": ready,
                "reason_codes": sorted(set(reasons)),
                "updated_at": time.time(),
            }
        )
        return self._status

    def status(self) -> Stage3EngagementStatus:
        return self._status

    def close_round(self, mission) -> MissionSnapshot:
        state = mission.state
        status = self._status
        if state.active_stage != "stage3":
            raise ValueError("STAGE3_NOT_ACTIVE")
        if status.enemy_class is None or status.enemy_balloon_track_id is None:
            raise ValueError("A3_ENGAGEMENT_EVENT_REQUIRED")
        if not status.ready_to_close:
            if "A3_ENEMY_CONFIRMATION_PENDING" in status.reason_codes:
                raise ValueError("A3_ENEMY_CONFIRMATION_PENDING")
            raise ValueError("A3_FRIEND_SAFETY_EVIDENCE_INCOMPLETE")
        snapshot = mission.complete_stage3_round(
            Stage3RoundCompleteRequest(
                enemy_class=status.enemy_class,
                enemy_hit=status.enemy_hit_confirmed,
                friend_hit=status.friend_hit_suspected,
            )
        )
        self.reset(snapshot.state.stage3_round)
        return snapshot
