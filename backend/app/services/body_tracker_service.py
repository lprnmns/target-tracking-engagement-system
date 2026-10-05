"""Small, deterministic body tracker used only for perception evidence.

It owns no actuator and emits no command.  Its purpose is to stop a frame
local YOLO detection id being mistaken for temporal IFF continuity.
"""

from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass

from app.schemas.vision import BBox, BodyDetection
from app.services.live_tracker_bridge import create_live_tracker, lab_detection, normalize_tracker_profile


@dataclass
class _BodyTrack:
    track_id: int
    class_name: str
    center_x: float
    center_y: float
    width: float
    height: float
    stable_frames: int = 1
    misses: int = 0
    velocity_x: float = 0.0
    velocity_y: float = 0.0
    last_seen_mono: float = 0.0
    last_center_x: float | None = None
    last_center_y: float | None = None


class BodyTrackerService:
    def __init__(
        self,
        max_match_distance_px: float = 140.0,
        max_misses: int = 10,
        max_camera_slew_reacquire_px: float = 960.0,
        max_camera_slew_reacquire_misses: int = 6,
        min_reacquire_size_similarity: float = 0.55,
    ) -> None:
        self.max_match_distance_px = max_match_distance_px
        self.max_misses = max_misses
        self.max_camera_slew_reacquire_px = max_camera_slew_reacquire_px
        self.max_camera_slew_reacquire_misses = max_camera_slew_reacquire_misses
        self.min_reacquire_size_similarity = min_reacquire_size_similarity
        self._next_track_id = 1
        self._tracks: dict[int, _BodyTrack] = {}
        self.profile = "current_custom"
        self._external_adapter = None
        self.last_error: str | None = None
        self._lock = threading.RLock()

    def reset(self) -> None:
        with self._lock:
            self._reset_unlocked()

    def _reset_unlocked(self) -> None:
        self._next_track_id = 1
        self._tracks.clear()
        if self._external_adapter is not None:
            self._external_adapter.reset()

    def configure(self, profile: str | None, *, enabled: bool = True) -> str:
        with self._lock:
            selected = normalize_tracker_profile(profile, enabled)
            adapter = None if selected == "current_custom" else create_live_tracker("aircraft", selected)
            self.profile = selected
            self._external_adapter = adapter
            self.last_error = None
            self._reset_unlocked()
            return selected

    def update(
        self,
        bodies: list[BodyDetection],
        *,
        frame_id: int | None = None,
        capture_timestamp_ns: int | None = None,
        image=None,
    ) -> list[BodyDetection]:
        with self._lock:
            return self._update_unlocked(
                bodies,
                frame_id=frame_id,
                capture_timestamp_ns=capture_timestamp_ns,
                image=image,
            )

    def coasted_regions(self, ttl_s: float = 0.600) -> list[dict]:
        """Return all active and coasted body regions within TTL."""
        with self._lock:
            now_mono = time.monotonic()
            regions: list[dict] = []
            for track_id, track in self._tracks.items():
                age_s = now_mono - track.last_seen_mono if track.last_seen_mono > 0 else 0.0
                if age_s > ttl_s or track.misses > self.max_misses:
                    continue
                pred_cx = track.center_x + track.velocity_x * age_s
                pred_cy = track.center_y + track.velocity_y * age_s
                pred_x = int(pred_cx - track.width / 2.0)
                pred_y = int(pred_cy - track.height / 2.0)
                regions.append({
                    "track_id": track_id,
                    "class_name": track.class_name,
                    "bbox": BBox(x=max(0, pred_x), y=max(0, pred_y), w=int(track.width), h=int(track.height)),
                    "center_x": pred_cx,
                    "center_y": pred_cy,
                    "width": track.width,
                    "height": track.height,
                    "velocity_x": track.velocity_x,
                    "velocity_y": track.velocity_y,
                    "age_s": age_s,
                    "misses": track.misses,
                    "is_ghost": track.misses > 0,
                    "stable_frames": track.stable_frames,
                })
            return regions

    def _record_observation(
        self,
        track: _BodyTrack,
        center_x: float,
        center_y: float,
        width: float,
        height: float,
        now_mono: float,
    ) -> None:
        if track.last_seen_mono > 0.0 and track.last_center_x is not None and track.last_center_y is not None:
            dt = now_mono - track.last_seen_mono
            if 0.005 <= dt <= 0.5:
                inst_vx = (center_x - track.last_center_x) / dt
                inst_vy = (center_y - track.last_center_y) / dt
                track.velocity_x = track.velocity_x * 0.6 + inst_vx * 0.4
                track.velocity_y = track.velocity_y * 0.6 + inst_vy * 0.4
        track.last_center_x = center_x
        track.last_center_y = center_y
        track.last_seen_mono = now_mono
        track.center_x = center_x
        track.center_y = center_y
        track.width = float(width)
        track.height = float(height)
        track.stable_frames += 1
        track.misses = 0

    def _update_unlocked(
        self,
        bodies: list[BodyDetection],
        *,
        frame_id: int | None = None,
        capture_timestamp_ns: int | None = None,
        image=None,
    ) -> list[BodyDetection]:
        if self._external_adapter is not None and frame_id is not None:
            return self._update_external(
                bodies,
                frame_id=int(frame_id),
                capture_timestamp_ns=int(capture_timestamp_ns or time.time_ns()),
                image=image,
            )
        now_mono = time.monotonic()
        candidates: list[tuple[float, int, int]] = []
        matched_tracks: set[int] = set()
        matched_detections: set[int] = set()
        updates: dict[int, BodyDetection] = {}

        for track_id, track in self._tracks.items():
            for detection_index, body in enumerate(bodies):
                # A class switch is not temporal evidence.  It must begin a
                # new track rather than inheriting a previous IFF decision.
                if body.class_name != track.class_name:
                    continue
                center_x = body.bbox.x + body.bbox.w / 2
                center_y = body.bbox.y + body.bbox.h / 2
                distance = math.hypot(center_x - track.center_x, center_y - track.center_y)
                if distance <= self.max_match_distance_px:
                    candidates.append((distance, track_id, detection_index))

        for _, track_id, detection_index in sorted(candidates):
            if track_id in matched_tracks or detection_index in matched_detections:
                continue
            track = self._tracks[track_id]
            body = bodies[detection_index]
            center_x = body.bbox.x + body.bbox.w / 2
            center_y = body.bbox.y + body.bbox.h / 2
            self._record_observation(track, center_x, center_y, body.bbox.w, body.bbox.h, now_mono)
            matched_tracks.add(track_id)
            matched_detections.add(detection_index)
            updates[detection_index] = body.model_copy(update={"track_id": track_id, "stable_frames": track.stable_frames})

        # A camera/turret slew can move a stationary object hundreds of pixels
        # between detector frames.  The normal 140 px nearest-neighbour gate
        # correctly rejects that jump, but minting a new id leaves an explicit
        # cockpit selection pinned to a stale identity forever.  Recover only
        # an unambiguous one-track/one-detection pair from the immediately
        # preceding frame, and require its bbox geometry to remain consistent.
        # Multiple candidates deliberately remain unmatched instead of making
        # a potentially dangerous target handoff.
        unmatched_track_ids = [track_id for track_id in self._tracks if track_id not in matched_tracks]
        unmatched_detection_ids = [index for index in range(len(bodies)) if index not in matched_detections]
        classes = {
            self._tracks[track_id].class_name for track_id in unmatched_track_ids
        } | {
            bodies[index].class_name for index in unmatched_detection_ids
        }
        for class_name in classes:
            class_tracks = [
                track_id
                for track_id in unmatched_track_ids
                if self._tracks[track_id].class_name == class_name
                and self._tracks[track_id].misses <= self.max_camera_slew_reacquire_misses
            ]
            class_detections = [
                index
                for index in unmatched_detection_ids
                if bodies[index].class_name == class_name
            ]
            if len(class_tracks) != 1 or len(class_detections) != 1:
                continue
            track_id = class_tracks[0]
            detection_index = class_detections[0]
            track = self._tracks[track_id]
            body = bodies[detection_index]
            center_x = body.bbox.x + body.bbox.w / 2
            center_y = body.bbox.y + body.bbox.h / 2
            distance = math.hypot(center_x - track.center_x, center_y - track.center_y)
            if distance > self.max_camera_slew_reacquire_px:
                continue
            old_area = max(1.0, track.width * track.height)
            new_area = max(1.0, float(body.bbox.w * body.bbox.h))
            area_similarity = min(old_area, new_area) / max(old_area, new_area)
            old_aspect = track.width / max(1.0, track.height)
            new_aspect = float(body.bbox.w) / max(1.0, float(body.bbox.h))
            aspect_similarity = min(old_aspect, new_aspect) / max(old_aspect, new_aspect)
            if min(area_similarity, aspect_similarity) < self.min_reacquire_size_similarity:
                continue
            self._record_observation(track, center_x, center_y, body.bbox.w, body.bbox.h, now_mono)
            matched_tracks.add(track_id)
            matched_detections.add(detection_index)
            updates[detection_index] = body.model_copy(
                update={"track_id": track_id, "stable_frames": track.stable_frames}
            )

        for track_id, track in list(self._tracks.items()):
            if track_id in matched_tracks:
                continue
            track.misses += 1
            if track.misses > self.max_misses:
                del self._tracks[track_id]

        for detection_index, body in enumerate(bodies):
            if detection_index in matched_detections:
                continue
            center_x = body.bbox.x + body.bbox.w / 2
            center_y = body.bbox.y + body.bbox.h / 2
            track_id = self._next_track_id
            self._next_track_id += 1
            self._tracks[track_id] = _BodyTrack(
                track_id=track_id,
                class_name=body.class_name,
                center_x=center_x,
                center_y=center_y,
                width=float(body.bbox.w),
                height=float(body.bbox.h),
                last_seen_mono=now_mono,
                last_center_x=center_x,
                last_center_y=center_y,
            )
            updates[detection_index] = body.model_copy(update={"track_id": track_id, "stable_frames": 1})
        return [updates[index] for index in range(len(bodies))]

    def _update_external(
        self,
        bodies: list[BodyDetection],
        *,
        frame_id: int,
        capture_timestamp_ns: int,
        image,
    ) -> list[BodyDetection]:
        detections = [
            lab_detection(
                stream="aircraft",
                detection_id=body.id,
                x=body.bbox.x,
                y=body.bbox.y,
                w=body.bbox.w,
                h=body.bbox.h,
                confidence=body.confidence,
                class_id=body.class_id,
                class_name=body.class_name,
                model_id=body.source,
            )
            for body in bodies
        ]
        try:
            tracks = self._external_adapter.update(
                frame_id=frame_id,
                capture_timestamp_ns=capture_timestamp_ns,
                detections=detections,
                image=image,
            )
            self.last_error = None
        except Exception as exc:
            self.last_error = f"{self.profile}:{exc}"
            return bodies
        updates: dict[int, BodyDetection] = {}
        for track in tracks:
            index = track.detection_index
            if index is None or not 0 <= int(index) < len(bodies):
                continue
            updates[int(index)] = bodies[int(index)].model_copy(
                update={"track_id": int(track.track_id), "stable_frames": int(track.hits)}
            )
        return [updates.get(index, body) for index, body in enumerate(bodies)]
