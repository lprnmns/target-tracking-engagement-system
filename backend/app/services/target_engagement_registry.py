"""Persistent target/balloon identity and one-shot engagement ledger.

The vision detector emits frame-local ids.  This service is the small stateful
layer that turns those ids into a competition identity:

* an aircraft may own exactly one balloon;
* a body/balloon pair must be observed for a real elapsed second before it is
  READY for autonomous engagement;
* short detector dropouts keep the same identity;
* a confirmed balloon loss consumes both the balloon and its aircraft forever
  for the current mission/round.

It is perception state only.  It never writes serial and never bypasses the
CommandGateway safety gates.
"""

from __future__ import annotations

import math
import re
import time
from dataclasses import dataclass
from typing import Iterable

from app.schemas.tracking import (
    AssociationStatus,
    EngagementState,
    EngagementStatus,
    LogicalTarget,
    LogicalTargetState,
    MultiTargetTrackingStatus,
    TargetRegistryStatus,
)
from app.schemas.vision import BodyDetection, BalloonDetection, VisionEvent
from app.services import stage3_roi


AIRCRAFT_CLASSES = frozenset({"f16", "helicopter", "ballistic_missile", "mini_micro_uav"})


@dataclass
class _Candidate:
    body_key: str
    body_track_id: int | None
    body_detection_id: int
    balloon_track_id: int
    balloon_detection_id: int | None
    started_at: float
    last_seen_at: float
    distance_px: float | None = None


class TargetEngagementRegistry:
    """Mission-scoped logical identity registry.

    The registry is deliberately in-memory.  A mission/round boundary calls
    :meth:`reset`, so a new run gets new sequence numbers and cannot inherit a
    previously destroyed target.
    """

    def __init__(
        self,
        min_association_duration_s: float = 1.0,
        dropout_grace_s: float = 0.75,
        body_reacquire_distance_px: float = 180.0,
    ) -> None:
        self.min_association_duration_s = max(0.0, float(min_association_duration_s))
        self.dropout_grace_s = max(0.1, float(dropout_grace_s))
        self.body_reacquire_distance_px = max(1.0, float(body_reacquire_distance_px))
        self.reset()

    def reset(self) -> None:
        self._targets: dict[str, LogicalTarget] = {}
        self._target_meta: dict[str, dict] = {}
        self._body_target_by_key: dict[str, str] = {}
        self._candidates: dict[tuple[str, int], _Candidate] = {}
        self._sequence_by_kind: dict[tuple[str, str], int] = {}
        self._consumed_balloon_track_ids: set[int] = set()
        self._destroyed_body_keys: set[str] = set()
        self._destroyed_body_track_ids: set[int] = set()
        self._engagement_state_seen: dict[int, EngagementState] = {}
        self._status = TargetRegistryStatus(
            association_min_duration_s=self.min_association_duration_s,
            dropout_grace_s=self.dropout_grace_s,
        )

    # ------------------------------------------------------------------
    # Public state/update API
    # ------------------------------------------------------------------

    def update(
        self,
        event: VisionEvent | None,
        tracks: MultiTargetTrackingStatus,
        associations: AssociationStatus,
        confirmations: EngagementStatus | None = None,
        now: float | None = None,
        target_policy: str | None = None,
    ) -> TargetRegistryStatus:
        """Consume one perception frame and return the immutable snapshot."""

        timestamp = self._timestamp(event, now)
        if confirmations is not None:
            self._apply_confirmations(confirmations, timestamp)

        bodies = list(event.body_detections) if event is not None else []
        bodies_by_id = {body.id: body for body in bodies}
        fresh_tracks = {track.track_id: track for track in tracks.tracks if track.fresh}
        associations_by_track = {item.balloon_track_id: item for item in associations.associations}

        observations = self._observations(
            bodies_by_id=bodies_by_id,
            tracks=tracks,
            associations=associations,
            timestamp=timestamp,
        )
        observed_pairs = {(item.body_key, item.balloon_track_id) for item in observations}

        # First establish/refresh the body-owned logical targets.  An existing
        # body target never accepts a second balloon, even if another balloon
        # becomes closer later.
        for candidate in observations:
            # A balloon track is a single mission-scoped ownership record. If
            # the association service (or a stale frame) presents that same
            # tracker id next to another aircraft, do not create a second
            # logical target for it.  Without this guard one frame could leave
            # the fighter target in the registry while the next frame painted
            # its balloon under a helicopter name.
            balloon_owner = self.target_for_balloon_track(candidate.balloon_track_id)
            candidate_body = bodies_by_id.get(candidate.body_detection_id)
            if balloon_owner is not None and balloon_owner.body_track_id is not None:
                if balloon_owner.state == LogicalTargetState.DESTROYED or balloon_owner.consumed:
                    continue
                if not self._candidate_matches_target(balloon_owner, candidate, candidate_body):
                    continue
            if candidate.body_key in self._destroyed_body_keys or candidate.body_track_id in self._destroyed_body_track_ids:
                continue
            target_id = self._body_target_by_key.get(candidate.body_key)
            if target_id is None:
                # Body tracker ids can be recreated after a camera/runtime
                # restart even though the same aircraft is still in view.
                # Reuse the nearby class/team identity before allocating a
                # second sequence; otherwise the cockpit can show _1 then _2
                # for one physical target and its balloon.
                prior = self.target_for_body(bodies_by_id.get(candidate.body_detection_id))
                if prior is not None and prior.state != LogicalTargetState.DESTROYED:
                    target_id = prior.target_id
                    self._body_target_by_key[candidate.body_key] = target_id
            if target_id is not None:
                target = self._targets.get(target_id)
                if target is None or target.state == LogicalTargetState.DESTROYED:
                    continue
                if target.balloon_track_id not in {None, candidate.balloon_track_id}:
                    old_track_is_fresh = target.balloon_track_id in fresh_tracks
                    if target.state == LogicalTargetState.REENGAGE and not old_track_is_fresh:
                        # Tracker-id churn must not permanently orphan the one
                        # physical balloon under this body. Rebind only after
                        # the original ownership has actually entered
                        # REENGAGE; during the short dropout grace the lock is
                        # still immutable and no neighbouring balloon can win.
                        target.balloon_track_id = candidate.balloon_track_id
                        target.balloon_detection_id = candidate.balloon_detection_id
                    else:
                        target.reason_code = "BALLOON_ID_LOCKED"
                        target.updated_at = timestamp
                        continue
            else:
                target = self._create_body_target(candidate, bodies_by_id.get(candidate.body_detection_id), timestamp)
                target_id = target.target_id

            self._refresh_body_target(target, candidate, bodies_by_id.get(candidate.body_detection_id), timestamp)

        # Keep existing identities through short body/balloon detector gaps.
        # A long gap becomes REENGAGE, but its original balloon id remains
        # locked; it is never silently replaced by a new nearest balloon.
        for target in self._targets.values():
            if target.state == LogicalTargetState.DESTROYED:
                continue
            meta = self._target_meta.get(target.target_id, {})
            body_key = meta.get("body_key")
            if body_key is None:
                self._refresh_standalone_target(target, fresh_tracks, timestamp)
                continue
            body = self._body_for_target(target, bodies)
            if body is not None:
                target.last_body_seen_at = timestamp
                target.body_detection_id = body.id
                target.body_track_id = body.track_id or target.body_track_id
                meta["body_center"] = self._body_center(body)
                meta["body_bbox"] = body.bbox
            self._refresh_body_visibility(target, observed_pairs, fresh_tracks, timestamp)

        # Any fresh balloon that is not part of a body candidate is a normal
        # standalone target.  It remains available even when aircraft targets
        # are present.
        assigned_track_ids = {
            target.balloon_track_id
            for target in self._targets.values()
            if target.balloon_track_id is not None
        }
        destroyed_association_tracks = {
            association.balloon_track_id
            for association in associations.associations
            if self._association_body_is_destroyed(association, bodies_by_id)
        }
        allow_standalone_balloons = str(target_policy or "").upper() != "BALLOON_AIRCRAFT"
        for track_id, track in fresh_tracks.items():
            if not allow_standalone_balloons:
                continue
            if track_id in self._consumed_balloon_track_ids or track_id in assigned_track_ids:
                continue
            if track_id in destroyed_association_tracks:
                continue
            if str(target_policy or "").upper() not in {"STAGE1_INDEPENDENT", "STAGE_1"}:
                association = associations_by_track.get(track_id)
                if association is not None and association.body_detection_id is not None:
                    # Tentative/stable body links are owned by the body ledger;
                    # ambiguous/orphan tracks are allowed as standalone balloons.
                    linked_body = bodies_by_id.get(association.body_detection_id)
                    if linked_body is not None and self._normalize_team(linked_body.target_team) == "friend":
                        # Do not let an ambiguous/orphan association fall through
                        # to the generic standalone-balloon target path.
                        continue
                    if association.state in {"tentative", "stable"} and self.is_aircraft_body(linked_body):
                        continue
            self._create_standalone_target(track_id, track.detection_id, timestamp)

        if str(target_policy or "").upper() == "BALLOON_AIRCRAFT":
            self._y18_takili_temizle(fresh_tracks, timestamp)
        self._prune_candidates(observed_pairs, timestamp)
        self._update_pending_states(confirmations, timestamp)
        return self._rebuild_status(timestamp)

    Y18_TAKILI_S = 1.5

    def _y18_takili_temizle(self, fresh_tracks, timestamp: float) -> None:
        """Y18: govde izi degisince eski kimlik REENGAGE'de kilitli kalip balonu
        sonsuza dek bloke ediyordu (mod degisimi duzeltiyordu). Balon gorunurken
        1.5 sn REENGAGE kalan dost-olmayan govde hedefi silinir; ayni cift
        yeniden READY olabilir. Dost hedefler korunur."""
        if not hasattr(self, "_y18_since"):
            self._y18_since = {}
        for tid, target in list(self._targets.items()):
            meta = self._target_meta.get(tid, {})
            if (
                meta.get("body_key") is None
                or target.state != LogicalTargetState.REENGAGE
                or self._normalize_team(target.target_team) == "friend"
                or target.consumed
            ):
                self._y18_since.pop(tid, None)
                continue
            since = self._y18_since.setdefault(tid, timestamp)
            if timestamp - since < self.Y18_TAKILI_S or target.balloon_track_id not in fresh_tracks:
                continue
            self._targets.pop(tid, None)
            self._target_meta.pop(tid, None)
            for key, val in list(self._body_target_by_key.items()):
                if val == tid:
                    self._body_target_by_key.pop(key, None)
            self._y18_since.pop(tid, None)

    def status(self) -> TargetRegistryStatus:
        return self._status

    def target_for_balloon_track(self, balloon_track_id: int | None) -> LogicalTarget | None:
        if balloon_track_id is None:
            return None
        for target in self._targets.values():
            if target.balloon_track_id == balloon_track_id:
                return target
        return None

    def target_for_balloon_detection(self, balloon_detection_id: int | None) -> LogicalTarget | None:
        """Resolve the currently painted frame id to its logical target."""
        if balloon_detection_id is None:
            return None
        for target in self._targets.values():
            if target.balloon_detection_id == balloon_detection_id:
                return target
        return None

    def target_for_body(self, body: BodyDetection | None) -> LogicalTarget | None:
        if body is None:
            return None
        if not self.is_aircraft_body(body):
            return None
        key = self._body_key(body)
        target_id = self._body_target_by_key.get(key)
        if target_id is None:
            # Body tracker ids may be recreated after a longer camera gap.  A
            # nearby same-class body is treated as the same logical target.
            center = self._body_center(body)
            for candidate_id, meta in self._target_meta.items():
                candidate = self._targets.get(candidate_id)
                if candidate is None or candidate.state == LogicalTargetState.DESTROYED:
                    continue
                if candidate.target_class != self._class_token(body.class_name):
                    continue
                if candidate.target_team != self._normalize_team(body.target_team):
                    continue
                old_center = meta.get("body_center")
                if old_center is not None and math.hypot(center[0] - old_center[0], center[1] - old_center[1]) <= self.body_reacquire_distance_px:
                    target_id = candidate_id
                    break
        return self._targets.get(target_id) if target_id is not None else None

    def body_is_destroyed(self, body: BodyDetection | None) -> bool:
        """Match a consumed body even if the vision tracker recreated its id."""
        if body is None:
            return False
        if not self.is_aircraft_body(body):
            return False
        key = self._body_key(body)
        if key in self._destroyed_body_keys or body.track_id in self._destroyed_body_track_ids:
            return True
        center = self._body_center(body)
        for target_id, target in self._targets.items():
            if target.state != LogicalTargetState.DESTROYED:
                continue
            meta = self._target_meta.get(target_id, {})
            old_center = meta.get("body_center")
            if old_center is None or target.target_class != self._class_token(body.class_name):
                continue
            if target.target_team != self._normalize_team(body.target_team):
                continue
            if math.hypot(center[0] - old_center[0], center[1] - old_center[1]) <= self.body_reacquire_distance_px:
                return True
        return False

    def target_for_body_detection_id(self, body_detection_id: int | None, event: VisionEvent | None) -> LogicalTarget | None:
        if body_detection_id is None or event is None:
            return None
        return self.target_for_body(next((item for item in event.body_detections if item.id == body_detection_id), None))

    def is_track_blocked(self, balloon_track_id: int | None) -> bool:
        target = self.target_for_balloon_track(balloon_track_id)
        return bool(
            balloon_track_id is not None
            and (
                balloon_track_id in self._consumed_balloon_track_ids
                or (target is not None and (target.state == LogicalTargetState.DESTROYED or not target.engagement_allowed))
            )
        )

    def is_track_selectable(self, balloon_track_id: int | None, require_ready: bool = False) -> bool:
        target = self.target_for_balloon_track(balloon_track_id)
        if target is None:
            return balloon_track_id not in self._consumed_balloon_track_ids
        if target.state == LogicalTargetState.DESTROYED or target.consumed or not target.engagement_allowed:
            return False
        if require_ready and target.body_track_id is not None and target.state != LogicalTargetState.READY:
            return False
        return True

    def filter_event_for_tracking(
        self,
        event: VisionEvent | None,
        tracks: MultiTargetTrackingStatus,
        associations: AssociationStatus,
        target_policy: str = "BALLOON",
        selected_detection_id: int | None = None,
        selected_detection_kind: str | None = None,
        selected_body_track_id: int | None = None,
    ) -> VisionEvent | None:
        """Return a control-only event that cannot retarget a locked body.

        The original event is kept for evidence and association telemetry.  A
        destroyed/locked aircraft's replacement balloon is removed from this
        control view, while unrelated standalone balloons remain available.
        Aircraft body detections are included only while their own fresh,
        selectable balloon is present; this prevents AutoTracker's legacy body
        fallback from moving the turret after a balloon dropout or hit.
        """

        if event is None:
            return None
        normalized_policy = str(target_policy).upper()
        if normalized_policy == "AIRCRAFT":
            # Aircraft policy deliberately tracks the body bbox and does not
            # fall back to a nearby balloon. Automatic acquisition is enemy
            # only; an explicit operator selection may follow friend/unknown
            # bodies for observation. FIRE remains enemy-only in the Gateway.
            available = [body for body in event.body_detections if not self.body_is_destroyed(body)]
            if selected_detection_kind == "body":
                exact = [
                    body
                    for body in available
                    if (
                        selected_body_track_id is not None
                        and body.track_id == selected_body_track_id
                    )
                    or (
                        selected_body_track_id is None
                        and body.id == selected_detection_id
                    )
                ]
                # A frame-local id or tracker id can legitimately churn. If
                # the exact id vanished, preserve the candidate pool and let
                # AutoTracker's class/team/spatial continuity gate perform the
                # handoff. Returning an empty event here caused permanent
                # SEARCHING even while the selected aircraft stayed visible.
                bodies = exact if exact else available
            else:
                bodies = [
                    body
                    for body in available
                    if self._normalize_team(body.target_team) == "enemy"
                ]
            return event.model_copy(update={"body_detections": bodies, "balloon_detections": []})
        if normalized_policy == "BALLOON_AIRCRAFT":
            # Combined mode is an aircraft-owned-balloon mode, not a union of
            # two independent target lists.  Only the stable balloon belonging
            # to a visible enemy aircraft may reach control.  Aircraft boxes
            # remain in the original vision event for UI/IFF, but are removed
            # from this control-only view so AutoTracker can never fall back
            # to aiming at the aircraft body.
            tracks_by_detection = {
                track.detection_id: track
                for track in tracks.tracks
                if track.detection_id is not None and track.fresh
            }
            associations_by_track = {
                item.balloon_track_id: item
                for item in associations.associations
            }
            now_mono = time.monotonic()
            if not hasattr(self, "_recent_friendly_memory"):
                self._recent_friendly_memory = []
            for b in event.body_detections:
                if self._normalize_team(b.target_team) in {"friend", "dost"}:
                    self._recent_friendly_memory.append((now_mono, b.bbox))
            # Prune friendly shields older than 0.50s (lightweight hysteresis prevents single-frame detector flicker bypass)
            self._recent_friendly_memory = [
                (t, fb_box) for (t, fb_box) in self._recent_friendly_memory
                if (now_mono - t) <= 0.50
            ]

            enemy_bodies = [
                b for b in event.body_detections
                if self._normalize_team(b.target_team) == "enemy" and not self.body_is_destroyed(b)
            ]
            owned_enemy_balloons: list[BalloonDetection] = []
            for balloon in event.balloon_detections:
                # 1. Reject any balloon in friendly attachment envelope (strict friendly-fire block with 500ms memory)
                if any(stage3_roi.attachment_roi_contains(fb_box, balloon.center_x, balloon.center_y) for _, fb_box in self._recent_friendly_memory):
                    continue
                # 2. If directly in enemy attachment envelope, always accept
                if any(stage3_roi.attachment_roi_contains(eb.bbox, balloon.center_x, balloon.center_y) for eb in enemy_bodies):
                    owned_enemy_balloons.append(balloon)
                    continue
                # 3. Check tracking / association registry
                track = tracks_by_detection.get(balloon.id)
                if track is not None:
                    assoc = associations_by_track.get(track.track_id)
                    if assoc is not None:
                        if assoc.body_detection_id is not None:
                            body = bodies_by_id.get(assoc.body_detection_id)
                            if body is not None and self._normalize_team(body.target_team) == "enemy" and not self.body_is_destroyed(body):
                                owned_enemy_balloons.append(balloon)
                                continue
                        target = self.target_for_balloon_track(track.track_id)
                        if target is not None and self._normalize_team(target.target_team) == "enemy" and not target.consumed:
                            owned_enemy_balloons.append(balloon)
                            continue
            return event.model_copy(update={"body_detections": [], "balloon_detections": owned_enemy_balloons})
        tracks_by_detection = {
            track.detection_id: track
            for track in tracks.tracks
            if track.detection_id is not None and track.fresh
        }
        associations_by_track = {item.balloon_track_id: item for item in associations.associations}
        bodies_by_id = {body.id: body for body in event.body_detections}
        allowed: list[BalloonDetection] = []
        allowed_track_ids: set[int] = set()
        for balloon in event.balloon_detections:
            if selected_detection_kind == "balloon" and balloon.id != selected_detection_id:
                continue
            track = tracks_by_detection.get(balloon.id)
            if track is None:
                # The detector frame and tracker may be one tick apart.  Keep
                # untracked detections visible rather than inventing a block,
                # except when the registry already knows this exact frame id
                # belongs to a friendly evidence balloon.
                logical_target = self.target_for_balloon_detection(balloon.id)
                if logical_target is not None and not logical_target.engagement_allowed and not (
                    selected_detection_kind == "balloon" and balloon.id == selected_detection_id
                ):
                    continue
                bcx = float(getattr(balloon, "center_x", None) or (balloon.bbox.x + balloon.bbox.w / 2))
                bcy = float(getattr(balloon, "center_y", None) or (balloon.bbox.y + balloon.bbox.h / 2))
                if any(
                    self._normalize_team(body.target_team) == "friend" and stage3_roi.attachment_roi_contains(body.bbox, bcx, bcy)
                    for body in event.body_detections
                ):
                    continue
                if hasattr(event, "target_verdicts") and event.target_verdicts:
                    v = next((item for item in event.target_verdicts if item.kind == "balloon" and item.detection_id == balloon.id), None)
                    if v is not None and (self._normalize_team(v.target_team) == "friend" or v.verdict_state == "FRIEND_LOCKED"):
                        continue
                allowed.append(balloon)
                continue
            selected_balloon = selected_detection_kind == "balloon" and balloon.id == selected_detection_id
            if not self.is_track_selectable(track.track_id) and not selected_balloon:
                continue
            association = associations_by_track.get(track.track_id)
            if association is None or association.body_detection_id is None:
                bcx = float(getattr(balloon, "center_x", None) or (balloon.bbox.x + balloon.bbox.w / 2))
                bcy = float(getattr(balloon, "center_y", None) or (balloon.bbox.y + balloon.bbox.h / 2))
                if any(
                    self._normalize_team(body.target_team) == "friend" and stage3_roi.attachment_roi_contains(body.bbox, bcx, bcy)
                    for body in event.body_detections
                ):
                    continue
                if hasattr(event, "target_verdicts") and event.target_verdicts:
                    v = next((item for item in event.target_verdicts if item.kind == "balloon" and item.detection_id == balloon.id), None)
                    if v is not None and (self._normalize_team(v.target_team) == "friend" or v.verdict_state == "FRIEND_LOCKED"):
                        continue
                allowed.append(balloon)
                continue
            body = bodies_by_id.get(association.body_detection_id)
            # Friendly aircraft and their balloon remain visible for IFF and
            # evidence, but never enter the movement/engagement selector.
            if body is not None and self._normalize_team(body.target_team) == "friend" and not (
                (selected_detection_kind == "balloon" and balloon.id == selected_detection_id)
                or (selected_detection_kind == "body" and body.id == selected_detection_id)
            ):
                continue
            target = self.target_for_body(body)
            if target is None:
                if self.body_is_destroyed(body):
                    continue
                allowed.append(balloon)
                continue
            selected_friendly = (
                selected_balloon
                or (selected_detection_kind == "body" and body is not None and body.id == selected_detection_id)
            )
            if target.state == LogicalTargetState.DESTROYED or target.consumed or (not target.engagement_allowed and not selected_friendly):
                continue
            # Existing aircraft target owns exactly one balloon.  A new track
            # in the same body attachment region cannot replace it.
            if target.body_track_id is not None and target.balloon_track_id != track.track_id:
                continue
            allowed.append(balloon)
            allowed_track_ids.add(track.track_id)

        # AutoTrackerService intentionally has a body fallback for legacy and
        # standalone dry-run paths.  In the competition identity path that
        # fallback is unsafe: an aircraft without its own visible balloon must
        # never become a tracking target.  Keep a body only when the registry
        # still owns a selectable, fresh balloon for that exact body.  This is
        # deliberately stricter than the dropout grace period; the grace keeps
        # the logical identity for evidence/re-engage, but it must not generate
        # movement from a stale aircraft bbox.
        allowed_bodies: list[BodyDetection] = []
        for body in event.body_detections:
            if self._normalize_team(body.target_team) == "friend" and not (
                selected_detection_kind == "body" and body.id == selected_detection_id
            ):
                continue
            target = self.target_for_body(body)
            selected_friendly = selected_detection_kind == "body" and body.id == selected_detection_id
            if target is None or target.state == LogicalTargetState.DESTROYED or target.consumed or (not target.engagement_allowed and not selected_friendly):
                continue
            balloon_track_id = target.balloon_track_id
            if balloon_track_id is None or balloon_track_id not in allowed_track_ids:
                continue
            if not self.is_track_selectable(balloon_track_id) and not selected_friendly:
                continue
            allowed_bodies.append(body)

        return event.model_copy(update={"body_detections": allowed_bodies, "balloon_detections": allowed})

    # ------------------------------------------------------------------
    # Observation and state transitions
    # ------------------------------------------------------------------

    def _observations(
        self,
        bodies_by_id: dict[int, BodyDetection],
        tracks: MultiTargetTrackingStatus,
        associations: AssociationStatus,
        timestamp: float,
    ) -> list[_Candidate]:
        observations: list[_Candidate] = []
        used_bodies: set[str] = set()
        used_balloons: set[int] = set()
        track_by_id = {track.track_id: track for track in tracks.tracks}
        for association in sorted(
            associations.associations,
            key=lambda item: (item.distance_px if item.distance_px is not None else float("inf"), item.balloon_track_id),
        ):
            if association.state not in {"tentative", "stable"} or association.body_detection_id is None:
                continue
            track = track_by_id.get(association.balloon_track_id)
            body = bodies_by_id.get(association.body_detection_id)
            if track is None or body is None or not track.fresh:
                continue
            if not self.is_aircraft_body(body):
                continue
            body_key = self._canonical_body_key(body)
            if body_key in used_bodies or association.balloon_track_id in used_balloons:
                continue
            if self.body_is_destroyed(body):
                continue
            if association.balloon_track_id in self._consumed_balloon_track_ids:
                continue
            candidate_key = (body_key, association.balloon_track_id)
            candidate = self._candidates.get(candidate_key)
            if candidate is None:
                candidate = _Candidate(
                    body_key=body_key,
                    body_track_id=body.track_id,
                    body_detection_id=body.id,
                    balloon_track_id=association.balloon_track_id,
                    balloon_detection_id=track.detection_id,
                    started_at=timestamp,
                    last_seen_at=timestamp,
                    distance_px=association.distance_px,
                )
                self._candidates[candidate_key] = candidate
            else:
                candidate.last_seen_at = timestamp
                candidate.body_track_id = body.track_id or candidate.body_track_id
                candidate.body_detection_id = body.id
                candidate.balloon_detection_id = track.detection_id
                candidate.distance_px = association.distance_px
            used_bodies.add(body_key)
            used_balloons.add(association.balloon_track_id)
            observations.append(candidate)
        return observations

    def _create_body_target(self, candidate: _Candidate, body: BodyDetection | None, timestamp: float) -> LogicalTarget:
        class_token = self._class_token(body.class_name if body is not None else "target")
        team = self._normalize_team(body.target_team if body is not None and body.target_team else "unknown")
        team_token = self._team_token(team)
        sequence_key = (class_token, team_token)
        sequence = self._sequence_by_kind.get(sequence_key, 0) + 1
        self._sequence_by_kind[sequence_key] = sequence
        target_id = f"{class_token}_{team_token}_{sequence}"
        balloon_target_id = self._balloon_target_id(class_token, team, sequence)
        target = LogicalTarget(
            target_id=target_id,
            # Machine-safe names are also the visible logical names.  Human
            # labels can be derived from target_class/target_team; the stable
            # ID must remain copyable in logs, UI and evidence.
            display_name=target_id,
            balloon_target_id=balloon_target_id,
            balloon_display_name=balloon_target_id,
            target_class=class_token,
            target_team=team,
            body_track_id=candidate.body_track_id,
            body_detection_id=candidate.body_detection_id,
            balloon_track_id=candidate.balloon_track_id,
            balloon_detection_id=candidate.balloon_detection_id,
            engagement_allowed=team != "friend",
            state=LogicalTargetState.ASSOCIATION_CANDIDATE,
            association_started_at=candidate.started_at,
            last_seen_at=timestamp,
            last_body_seen_at=timestamp,
            last_balloon_seen_at=timestamp,
            dropout_until=timestamp + self.dropout_grace_s,
            reason_code="ASSOCIATION_CANDIDATE",
            updated_at=timestamp,
        )
        self._targets[target_id] = target
        self._target_meta[target_id] = {
            "body_key": candidate.body_key,
            "candidate_key": (candidate.body_key, candidate.balloon_track_id),
            "body_center": self._body_center(body) if body is not None else None,
            "body_bbox": body.bbox if body is not None else None,
        }
        self._body_target_by_key[candidate.body_key] = target_id
        return target

    def _refresh_body_target(
        self,
        target: LogicalTarget,
        candidate: _Candidate,
        body: BodyDetection | None,
        timestamp: float,
    ) -> None:
        if target.state == LogicalTargetState.DESTROYED:
            return
        meta = self._target_meta.setdefault(target.target_id, {})
        meta["body_key"] = candidate.body_key
        meta["candidate_key"] = (candidate.body_key, candidate.balloon_track_id)
        if body is not None:
            meta["body_center"] = self._body_center(body)
            meta["body_bbox"] = body.bbox
            self._retag_target_from_body(target, body, timestamp)
        duration = max(0.0, timestamp - candidate.started_at)
        ready = duration >= self.min_association_duration_s
        target.body_track_id = candidate.body_track_id or target.body_track_id
        target.body_detection_id = candidate.body_detection_id
        target.balloon_track_id = candidate.balloon_track_id
        if candidate.balloon_detection_id is not None:
            target.balloon_detection_id = candidate.balloon_detection_id
        target.association_started_at = candidate.started_at
        target.association_duration_s = round(duration, 4)
        target.last_seen_at = timestamp
        target.last_body_seen_at = timestamp
        target.last_balloon_seen_at = timestamp
        target.dropout_until = timestamp + self.dropout_grace_s
        target.state = LogicalTargetState.READY if ready else LogicalTargetState.ASSOCIATION_CANDIDATE
        target.reason_code = "ASSOCIATION_READY" if ready else "ASSOCIATION_WAIT_1S"
        target.updated_at = timestamp

    def _retag_target_from_body(self, target: LogicalTarget, body: BodyDetection, timestamp: float) -> None:
        """Upgrade an initially UNKNOWN IFF label without changing the
        aircraft's sequence number or balloon identity.

        The colour/IFF worker may need several frames before it can call a
        target enemy/friend.  The logical target is created immediately for
        tracking, then its human/machine label is corrected once a meaningful
        team arrives.  Unknown never downgrades a previously classified body.
        """
        new_team = self._normalize_team(body.target_team)
        if new_team not in {"enemy", "friend"} or new_team == target.target_team:
            return
        class_token = target.target_class
        old_match = re.search(r"_(\d+)$", target.target_id)
        sequence = int(old_match.group(1)) if old_match else 1
        team_token = self._team_token(new_team)
        new_id = f"{class_token}_{team_token}_{sequence}"
        if new_id in self._targets and new_id != target.target_id:
            sequence = self._sequence_by_kind.get((class_token, team_token), sequence) + 1
            self._sequence_by_kind[(class_token, team_token)] = sequence
            new_id = f"{class_token}_{team_token}_{sequence}"
        self._sequence_by_kind[(class_token, team_token)] = max(
            self._sequence_by_kind.get((class_token, team_token), 0),
            sequence,
        )
        old_id = target.target_id
        if new_id != old_id:
            self._targets.pop(old_id, None)
            target.target_id = new_id
            self._targets[new_id] = target
            meta = self._target_meta.pop(old_id, {})
            self._target_meta[new_id] = meta
            if meta.get("body_key") in self._body_target_by_key:
                self._body_target_by_key[meta["body_key"]] = new_id
        target.target_team = new_team
        target.display_name = new_id
        target.engagement_allowed = new_team != "friend"
        target.balloon_target_id = self._balloon_target_id(class_token, new_team, sequence)
        target.balloon_display_name = target.balloon_target_id
        target.reason_code = "IFF_TEAM_CLASSIFIED"
        target.updated_at = timestamp

    def _refresh_body_visibility(
        self,
        target: LogicalTarget,
        observed_pairs: set[tuple[str, int]],
        fresh_tracks: dict[int, object],
        timestamp: float,
    ) -> None:
        if target.balloon_track_id is None:
            return
        meta = self._target_meta.get(target.target_id, {})
        body_key = meta.get("body_key")
        pair_seen = (body_key, target.balloon_track_id) in observed_pairs
        balloon_seen = target.balloon_track_id in fresh_tracks
        if pair_seen and balloon_seen:
            return
        if target.last_balloon_seen_at is None:
            target.last_balloon_seen_at = timestamp
        if target.dropout_until is None:
            target.dropout_until = target.last_balloon_seen_at + self.dropout_grace_s
        if timestamp <= target.dropout_until:
            target.reason_code = "BALLOON_DROPOUT_HOLD"
            target.updated_at = timestamp
            return
        # Raw detector dropout is not destruction evidence.  Only the
        # HitConfirmationService may mark a target destroyed after an actual
        # shot and stable balloon absence.  Keep the immutable original
        # balloon ownership here so a neighbouring balloon cannot take over.
        target.state = LogicalTargetState.REENGAGE
        target.reason_code = "FRIEND_BALLOON_LOST" if not target.engagement_allowed else "BALLOON_DROPOUT_REENGAGE"
        target.updated_at = timestamp

    def _refresh_standalone_target(
        self,
        target: LogicalTarget,
        fresh_tracks: dict[int, object],
        timestamp: float,
    ) -> None:
        track_id = target.balloon_track_id
        if track_id is not None and track_id in fresh_tracks:
            target.last_seen_at = timestamp
            target.last_balloon_seen_at = timestamp
            target.dropout_until = timestamp + self.dropout_grace_s
            target.state = LogicalTargetState.STANDALONE
            target.reason_code = "STANDALONE_BALLOON"
            target.updated_at = timestamp
            return
        if target.dropout_until is not None and timestamp <= target.dropout_until:
            target.reason_code = "BALLOON_DROPOUT_HOLD"
        else:
            target.state = LogicalTargetState.REENGAGE
            target.reason_code = "STANDALONE_BALLOON_LOST"
        target.updated_at = timestamp

    def _create_standalone_target(self, track_id: int, detection_id: int | None, timestamp: float) -> LogicalTarget:
        sequence = self._sequence_by_kind.get(("balon", "standalone"), 0) + 1
        self._sequence_by_kind[("balon", "standalone")] = sequence
        target = LogicalTarget(
            target_id=f"balon_{sequence}",
            display_name=f"balon_{sequence}",
            balloon_target_id=f"balon_{sequence}_hedefi",
            balloon_display_name=f"balon_{sequence}_hedefi",
            target_class="balloon",
            target_team="unknown",
            balloon_track_id=track_id,
            balloon_detection_id=detection_id,
            state=LogicalTargetState.STANDALONE,
            association_duration_s=0.0,
            last_seen_at=timestamp,
            last_balloon_seen_at=timestamp,
            dropout_until=timestamp + self.dropout_grace_s,
            reason_code="STANDALONE_BALLOON",
            updated_at=timestamp,
        )
        self._targets[target.target_id] = target
        self._target_meta[target.target_id] = {"body_key": None, "detection_id": detection_id}
        return target

    def _apply_confirmations(self, confirmations: EngagementStatus, timestamp: float) -> None:
        for record in confirmations.records:
            if record.state == EngagementState.CONFIRMED_HIT:
                self._consumed_balloon_track_ids.add(record.balloon_track_id)
                target = self.target_for_balloon_track(record.balloon_track_id)
                if target is not None:
                    self._mark_destroyed(target, timestamp)
                if record.body_track_id is not None:
                    self._destroyed_body_track_ids.add(record.body_track_id)
                    self._destroyed_body_keys.add(f"track:{record.body_track_id}")
            elif record.state == EngagementState.PENDING_CONFIRMATION:
                # The visible state is applied after the frame association is
                # refreshed below; this branch only records the transition.
                self._engagement_state_seen.setdefault(record.balloon_track_id, record.state)

    def _mark_destroyed(
        self,
        target: LogicalTarget,
        timestamp: float,
        *,
        reason_code: str = "TARGET_BALLOON_CONFIRMED_DESTROYED",
    ) -> None:
        target.state = LogicalTargetState.DESTROYED
        target.consumed = True
        target.destroyed_at = timestamp
        target.reason_code = reason_code
        target.updated_at = timestamp
        if not target.display_name.endswith("— İmha Edildi"):
            target.display_name = f"{target.display_name} — İmha Edildi"
        if not target.balloon_display_name.endswith("— İmha Edildi"):
            target.balloon_display_name = f"{target.balloon_display_name} — İmha Edildi"
        meta = self._target_meta.get(target.target_id, {})
        body_key = meta.get("body_key")
        if body_key:
            self._destroyed_body_keys.add(body_key)
            self._body_target_by_key[body_key] = target.target_id
        if target.body_track_id is not None:
            self._destroyed_body_track_ids.add(target.body_track_id)
            self._destroyed_body_keys.add(f"track:{target.body_track_id}")

    def _candidate_matches_target(
        self,
        target: LogicalTarget,
        candidate: _Candidate,
        body: BodyDetection | None,
    ) -> bool:
        """Return whether an association still belongs to ``target``.

        Body tracker ids may be recreated after a camera dropout. Resolve the
        replacement through the registry's class/team/nearby-body identity,
        but never accept a different aircraft merely because its bbox is now
        the nearest one to the same balloon track.
        """
        meta = self._target_meta.get(target.target_id, {})
        if candidate.body_key == meta.get("body_key"):
            return True
        if target.body_track_id is not None and candidate.body_track_id == target.body_track_id:
            return True
        if body is not None:
            resolved = self.target_for_body(body)
            if resolved is not None and resolved.target_id == target.target_id:
                return True
        return False

    def _update_pending_states(self, confirmations: EngagementStatus | None, timestamp: float) -> None:
        if confirmations is None:
            return
        records = {record.balloon_track_id: record for record in confirmations.records}
        for target in self._targets.values():
            record = records.get(target.balloon_track_id)
            if record is None or target.state == LogicalTargetState.DESTROYED:
                continue
            if record.state == EngagementState.CONFIRMED_HIT:
                self._mark_destroyed(target, timestamp)
            elif record.state == EngagementState.PENDING_CONFIRMATION:
                target.state = LogicalTargetState.PENDING_CONFIRMATION
                target.reason_code = "HIT_CONFIRMATION_PENDING"
                target.updated_at = timestamp
            elif record.state == EngagementState.REENGAGE and target.state == LogicalTargetState.PENDING_CONFIRMATION:
                target.state = LogicalTargetState.REENGAGE
                target.reason_code = "MISS_REENGAGE_SAME_ID"
                target.updated_at = timestamp
            elif record.state == EngagementState.REENGAGE and self._engagement_state_seen.get(record.balloon_track_id) != record.state:
                # Expose the miss transition once.  On the next frame the
                # same locked balloon may return to READY, but its identity
                # and target id remain unchanged.
                target.state = LogicalTargetState.REENGAGE
                target.reason_code = "MISS_REENGAGE_SAME_ID"
                target.updated_at = timestamp
            self._engagement_state_seen[record.balloon_track_id] = record.state

    def _prune_candidates(self, observed_pairs: set[tuple[str, int]], timestamp: float) -> None:
        for key, candidate in list(self._candidates.items()):
            if key in observed_pairs:
                continue
            if timestamp - candidate.last_seen_at > self.dropout_grace_s:
                # Retain the candidate while its target exists only as a
                # locked historical identity; remove the pending timer so a
                # genuinely new body/balloon pair must prove one full second.
                self._candidates.pop(key, None)

    def _rebuild_status(self, timestamp: float) -> TargetRegistryStatus:
        targets = sorted(self._targets.values(), key=lambda item: item.target_id)
        ready = [
            item
            for item in targets
            if item.engagement_allowed
            and item.state in {LogicalTargetState.READY, LogicalTargetState.STANDALONE, LogicalTargetState.PENDING_CONFIRMATION}
        ]
        selected = max(ready, key=lambda item: (item.last_seen_at or 0.0, item.target_id), default=None)
        self._status = TargetRegistryStatus(
            targets=[item.model_copy(deep=True) for item in targets],
            selected_target_id=selected.target_id if selected is not None else None,
            selected_balloon_track_id=selected.balloon_track_id if selected is not None else None,
            destroyed_target_ids=sorted(item.target_id for item in targets if item.state == LogicalTargetState.DESTROYED),
            consumed_balloon_track_ids=sorted(self._consumed_balloon_track_ids),
            association_min_duration_s=self.min_association_duration_s,
            dropout_grace_s=self.dropout_grace_s,
            updated_at=timestamp,
        )
        return self._status

    # ------------------------------------------------------------------
    # Identity/geometry helpers
    # ------------------------------------------------------------------

    def _association_body_is_destroyed(self, association, bodies_by_id: dict[int, BodyDetection]) -> bool:
        body = bodies_by_id.get(association.body_detection_id) if association.body_detection_id is not None else None
        if body is not None:
            return self.body_is_destroyed(body)
        return association.body_track_id in self._destroyed_body_track_ids

    def _body_for_target(self, target: LogicalTarget, bodies: Iterable[BodyDetection]) -> BodyDetection | None:
        meta = self._target_meta.get(target.target_id, {})
        body_key = meta.get("body_key")
        for body in bodies:
            if self._body_key(body) == body_key:
                return body
        if target.body_track_id is not None:
            for body in bodies:
                if body.track_id == target.body_track_id:
                    return body
        return None

    def _canonical_body_key(self, body: BodyDetection) -> str:
        key = self._body_key(body)
        if key in self._body_target_by_key:
            return key
        target = self.target_for_body(body)
        if target is not None:
            return self._target_meta.get(target.target_id, {}).get("body_key", key)
        return key

    @staticmethod
    def _body_key(body: BodyDetection) -> str:
        if body.track_id is not None:
            return f"track:{body.track_id}"
        return f"detection:{TargetEngagementRegistry._class_token(body.class_name)}:{body.id}"

    @staticmethod
    def _body_center(body: BodyDetection) -> tuple[float, float]:
        return (body.bbox.x + body.bbox.w / 2.0, body.bbox.y + body.bbox.h / 2.0)

    @staticmethod
    def _class_token(class_name: str) -> str:
        token = re.sub(r"[^a-z0-9]+", "_", (class_name or "target").strip().lower()).strip("_")
        aliases = {
            "f_16": "f16",
            "f16_dusman": "f16",
            "uav": "mini_micro_uav",
            "mini_micro_iha": "mini_micro_uav",
            "enemy_f16": "f16",
            "f16_enemy": "f16",
            "enemy_helicopter": "helicopter",
            "helicopter_enemy": "helicopter",
            "enemy_ballistic_missile": "ballistic_missile",
            "ballistic_missile_enemy": "ballistic_missile",
            "enemy_mini_micro_uav": "mini_micro_uav",
            "mini_micro_uav_enemy": "mini_micro_uav",
            "balloon": "balloon",
            "balon": "balloon",
        }
        return aliases.get(token, token or "target")

    @classmethod
    def is_aircraft_body(cls, body: BodyDetection | None) -> bool:
        return body is not None and cls._class_token(body.class_name) in AIRCRAFT_CLASSES

    @staticmethod
    def _team_token(team: str) -> str:
        normalized = TargetEngagementRegistry._normalize_team(team)
        return {"enemy": "dusman", "friend": "dost", "unknown": "bilinmeyen"}.get(normalized, "bilinmeyen")

    @staticmethod
    def _normalize_team(team: str | None) -> str:
        return {
            "enemy": "enemy",
            "dusman": "enemy",
            "friend": "friend",
            "dost": "friend",
            "unknown": "unknown",
            "bilinmeyen": "unknown",
        }.get((team or "unknown").strip().lower(), "unknown")

    @classmethod
    def _balloon_target_id(cls, class_token: str, team: str, sequence: int) -> str:
        """Build the stable aircraft-owned balloon identity.

        Enemy/unknown balloons are operational target balloons and keep the
        competition ``*_hedefi`` suffix.  A friendly aircraft's balloon is
        evidence-only, so its identity explicitly uses ``*_dost_*_balon`` and
        cannot be mistaken for something the turret should follow or fire at.
        """
        if cls._normalize_team(team) == "friend":
            return f"{class_token}_dost_{sequence}_balon"
        return f"{class_token}_{sequence}_hedefi"

    @staticmethod
    def _team_label(team_token: str) -> str:
        return {"dusman": "Düşman", "dost": "Dost", "bilinmeyen": "Bilinmeyen"}.get(team_token, team_token.title())

    @staticmethod
    def _class_label(class_token: str) -> str:
        return {
            "f16": "F-16",
            "helicopter": "Helikopter",
            "ballistic_missile": "Balistik Füze",
            "mini_micro_uav": "Mini/Mikro İHA",
            "balloon": "Balon",
        }.get(class_token, class_token.replace("_", " ").title())

    @staticmethod
    def _timestamp(event: VisionEvent | None, now: float | None) -> float:
        if now is not None:
            return float(now)
        if event is not None:
            return float(event.timestamp_ms) / 1000.0
        return time.time()
