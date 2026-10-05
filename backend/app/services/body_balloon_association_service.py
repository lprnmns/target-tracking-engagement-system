"""Fail-closed, telemetry-only generic body–balloon association."""

from __future__ import annotations

import math
import time

from app.schemas.tracking import AssociationStatus, BodyBalloonAssociation, MultiTargetTrackingStatus
from app.schemas.vision import BodyDetection, VisionEvent
from app.services import stage3_roi


class BodyBalloonAssociationService:
    def __init__(self, max_distance_px: float = 180.0, ambiguity_margin_px: float = 24.0, stable_frames_required: int = 3) -> None:
        self.max_distance_px = max_distance_px
        self.ambiguity_margin_px = ambiguity_margin_px
        self.stable_frames_required = stable_frames_required
        self._previous_body_by_track: dict[int, int] = {}
        self._stable_frames_by_track: dict[int, int] = {}
        # Once a balloon has a stable owner, its track is permanently tied to
        # that body track for the current mission.  A later nearest-body
        # calculation may not silently transfer it to a neighbouring aircraft.
        self._locked_body_by_track: dict[int, int] = {}
        self._locked_body_last_seen: dict[int, float] = {}
        self.coast_ttl_s: float = 0.600
        self._status = AssociationStatus()

    def reset(self) -> None:
        self._previous_body_by_track.clear()
        self._stable_frames_by_track.clear()
        self._locked_body_by_track.clear()
        self._locked_body_last_seen.clear()
        self._status = AssociationStatus()

    def update(self, event: VisionEvent | None, tracks: MultiTargetTrackingStatus) -> AssociationStatus:
        now = time.time() if event is None else float(event.timestamp_ms) / 1000.0
        bodies = list(event.body_detections) if event is not None else []
        effective_max_distance = self._effective_max_distance(event)
        effective_ambiguity_margin = self.ambiguity_margin_px * self._frame_scale(event)
        associations: list[BodyBalloonAssociation] = []
        active_track_ids = {track.track_id for track in tracks.tracks}
        for track_id in list(self._previous_body_by_track):
            if track_id not in active_track_ids:
                self._previous_body_by_track.pop(track_id, None)
                self._stable_frames_by_track.pop(track_id, None)

        # Do not expire ``_locked_body_by_track`` when the temporal balloon
        # tracker briefly drops a track.  A lock is mission-scoped ownership,
        # not a cache entry: expiring it here was the exact path that allowed
        # a previously assigned fighter balloon to be re-attached to a nearby
        # helicopter after a detector dropout.  ``reset()`` is the explicit
        # mission/round boundary and is the only place that clears locks.
        reserved_body_keys = set(self._locked_body_by_track.values())

        candidates: list[tuple[float, int, BodyDetection, int, bool]] = []
        ambiguous_tracks: set[int] = set()
        for track in tracks.tracks:
            if not track.fresh:
                associations.append(BodyBalloonAssociation(balloon_track_id=track.track_id, state="orphan", stable_frames=0, updated_at=now))
                continue
            # Y19: kilitli sahip govde izi coast suresinden (0.6 sn) uzun yoksa kilidi birak.
            # Model sinif degistirince govde yeni iz no aliyor; eski kilit balonu sonsuza dek
            # 'orphan' tutup birlikte modda atisi engelliyordu. Yeniden baglanma normal ROI +
            # belirsizlik + 3 kare kuralindan gecer.
            _y19_lk = self._locked_body_by_track.get(track.track_id)
            if _y19_lk is not None and not any(self._body_key(b) == _y19_lk for b in bodies):
                _y19_ls = self._locked_body_last_seen.get(track.track_id, 0.0)
                if _y19_ls <= 0.0 or (now - _y19_ls) > self.coast_ttl_s:
                    self._locked_body_by_track.pop(track.track_id, None)
                    self._clear_track(track.track_id)
                    if _y19_lk not in self._locked_body_by_track.values():
                        reserved_body_keys.discard(_y19_lk)
            locked_body_key = self._locked_body_by_track.get(track.track_id)
            if locked_body_key is not None:
                # A stable ownership lock is stronger than current nearest
                # geometry.  If the owner is temporarily absent, preserve the
                # lock as orphan instead of attaching this balloon to another
                # aircraft; the registry will retain or destroy the logical
                # target according to its dropout policy.
                owner = next((body for body in bodies if self._body_key(body) == locked_body_key), None)
                if owner is None:
                    last_seen = self._locked_body_last_seen.get(track.track_id, 0.0)
                    is_coasting = (now - last_seen <= self.coast_ttl_s) if last_seen > 0.0 else False
                    state = "coasting" if is_coasting else "orphan"
                    associations.append(
                        BodyBalloonAssociation(
                            balloon_track_id=track.track_id,
                            body_track_id=locked_body_key,
                            state=state,
                            stable_frames=self._stable_frames_by_track.get(track.track_id, 0),
                            updated_at=now,
                            coasting=is_coasting,
                            attachment_region_ok=is_coasting,
                        )
                    )
                    continue
                self._locked_body_last_seen[track.track_id] = now
                distance = self._distance(track.center_x, track.center_y, owner)
                if distance > effective_max_distance or not self._inside_attachment_region(track.center_x, track.center_y, owner):
                    associations.append(
                        BodyBalloonAssociation(
                            balloon_track_id=track.track_id,
                            body_track_id=owner.track_id,
                            state="orphan",
                            distance_px=round(distance, 3),
                            stable_frames=self._stable_frames_by_track.get(track.track_id, 0),
                            updated_at=now,
                        )
                    )
                    continue
                stable_frames = self._stable_frames_by_track.get(track.track_id, self.stable_frames_required)
                confidence = max(0.0, min(1.0, 1.0 - distance / effective_max_distance))
                associations.append(
                    BodyBalloonAssociation(
                        balloon_track_id=track.track_id,
                        body_detection_id=owner.id,
                        body_track_id=owner.track_id,
                        state="stable",
                        distance_px=round(distance, 3),
                        confidence=round(confidence, 4),
                        stable_frames=stable_frames,
                        attachment_region_ok=True,
                        association_cost=round(distance / effective_max_distance, 4),
                        updated_at=now,
                    )
                )
                continue
            distances = sorted(
                (
                    (self._distance(track.center_x, track.center_y, body), body, self._body_key(body), self._inside_attachment_region(track.center_x, track.center_y, body))
                    for body in bodies
                    if self._inside_attachment_region(track.center_x, track.center_y, body)
                ),
                key=lambda item: (item[0], item[1].id),
            )
            if not distances or distances[0][0] > effective_max_distance:
                self._clear_track(track.track_id)
                associations.append(BodyBalloonAssociation(balloon_track_id=track.track_id, state="orphan", stable_frames=0, updated_at=now))
                continue
            if len(distances) > 1 and distances[1][0] - distances[0][0] <= effective_ambiguity_margin:
                ambiguous_tracks.add(track.track_id)
                self._clear_track(track.track_id)
                associations.append(BodyBalloonAssociation(balloon_track_id=track.track_id, state="ambiguous", distance_px=round(distances[0][0], 3), updated_at=now))
                continue
            # A body that already owns a stable balloon is reserved for that
            # balloon for the rest of the mission.  Even if a second balloon
            # is geometrically closer in a later frame, it must not create a
            # second body association which could make the UI relabel the
            # first balloon as another aircraft.
            if distances[0][2] in reserved_body_keys:
                previous_owner_track = next(
                    (
                        locked_track_id
                        for locked_track_id, locked_body_key in self._locked_body_by_track.items()
                        if locked_body_key == distances[0][2]
                    ),
                    None,
                )
                if previous_owner_track in active_track_ids:
                    associations.append(
                        BodyBalloonAssociation(
                            balloon_track_id=track.track_id,
                            state="orphan",
                            distance_px=round(distances[0][0], 3),
                            updated_at=now,
                        )
                    )
                    continue
                # The balloon tracker may recreate an id after a detector
                # gap. The old id is no longer active and this candidate is
                # still inside the same body's strict lower ROI, so treat it
                # as identity continuity rather than a second balloon.
                if previous_owner_track is not None:
                    self._locked_body_by_track.pop(previous_owner_track, None)
                reserved_body_keys.discard(distances[0][2])
            candidates.append((distances[0][0], track.track_id, distances[0][1], distances[0][2], distances[0][3]))

        used_bodies: set[int] = set()
        for distance, track_id, body, body_key, attachment_region_ok in sorted(candidates):
            if track_id in ambiguous_tracks:
                continue
            if body_key in used_bodies:
                self._clear_track(track_id)
                associations.append(BodyBalloonAssociation(balloon_track_id=track_id, state="ambiguous", distance_px=round(distance, 3), updated_at=now))
                continue
            used_bodies.add(body_key)
            if self._previous_body_by_track.get(track_id) == body_key:
                stable_frames = self._stable_frames_by_track.get(track_id, 0) + 1
            else:
                stable_frames = 1
            self._previous_body_by_track[track_id] = body_key
            self._stable_frames_by_track[track_id] = stable_frames
            state = "stable" if stable_frames >= self.stable_frames_required else "tentative"
            if state == "stable":
                self._locked_body_by_track.setdefault(track_id, body_key)
                self._locked_body_last_seen[track_id] = now
            confidence = max(0.0, min(1.0, 1.0 - distance / effective_max_distance))
            associations.append(
                BodyBalloonAssociation(
                    balloon_track_id=track_id,
                    body_detection_id=body.id,
                    body_track_id=body.track_id,
                    state=state,
                    distance_px=round(distance, 3),
                    confidence=round(confidence, 4),
                    stable_frames=stable_frames,
                    attachment_region_ok=attachment_region_ok,
                    association_cost=round(distance / effective_max_distance, 4),
                    updated_at=now,
                )
            )

        self._status = AssociationStatus(
            associations=sorted(associations, key=lambda item: item.balloon_track_id),
            stable_count=sum(item.state == "stable" for item in associations),
            ambiguous_count=sum(item.state == "ambiguous" for item in associations),
            orphan_count=sum(item.state == "orphan" for item in associations),
            coasting_count=sum(item.state == "coasting" for item in associations),
            updated_at=now,
        )
        return self._status

    def status(self) -> AssociationStatus:
        return self._status

    def _clear_track(self, track_id: int) -> None:
        self._previous_body_by_track.pop(track_id, None)
        self._stable_frames_by_track.pop(track_id, None)
        self._locked_body_last_seen.pop(track_id, None)

    @staticmethod
    def _frame_scale(event: VisionEvent | None) -> float:
        """Scale pixel gates from the contract's 640x360 reference frame."""
        if event is None:
            return 1.0
        width = max(1.0, float(event.frame_width or 640))
        height = max(1.0, float(event.frame_height or 360))
        # A 1920x1080 stream therefore receives the expected 3x pixel budget
        # while existing low-resolution tests keep their original thresholds.
        return min(4.0, max(1.0, width / 640.0, height / 360.0))

    def _effective_max_distance(self, event: VisionEvent | None) -> float:
        return self.max_distance_px * self._frame_scale(event)

    @staticmethod
    def _distance(x: float, y: float, body: BodyDetection) -> float:
        # The physical attachment starts below the aircraft, so distance is
        # measured from the lower-centre attachment point rather than from
        # the middle of the aircraft silhouette.
        anchor_x = body.bbox.x + body.bbox.w / 2
        anchor_y = body.bbox.y + body.bbox.h
        return math.hypot(x - anchor_x, y - anchor_y)

    @staticmethod
    def _body_key(body: BodyDetection) -> int:
        return body.track_id if body.track_id is not None else body.id

    @staticmethod
    def _inside_attachment_region(x: float, y: float, body: BodyDetection) -> bool:
        """Kural 2 gate, delegated to the shared stage-3 geometry module.

        Competition targets carry exactly one balloon below the aircraft:
        the shared envelope is +-20% of the body width horizontally and
        from the lower edge down to 2.5 body heights, nothing inside the
        fuselage.  This intentionally rejects side/above/standalone
        balloons even if Euclidean distance is small, while accepting the
        tether swing-out that the physical 20-25 cm suspension produces.
        """
        return stage3_roi.attachment_roi_contains(body.bbox, x, y)
