"""Deterministic multi-target tracking for Aşama 2 telemetry.

This service is intentionally perception/control only.  It produces stable
nearest-neighbour identities, but does not emit motor or fire
commands.  CommandGateway remains the only physical-output boundary.
"""

from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass

from app.schemas.tracking import MultiTargetTrack, MultiTargetTrackingStatus
from app.schemas.vision import BalloonDetection, VisionEvent
from app.services.live_tracker_bridge import create_live_tracker, lab_detection, normalize_tracker_profile


@dataclass
class _Track:
    track_id: int
    center_x: float
    center_y: float
    velocity_x: float
    velocity_y: float
    detection_id: int | None
    confidence: float
    bbox_x: float
    bbox_y: float
    bbox_w: float
    bbox_h: float
    age_frames: int = 1
    hits: int = 1
    misses: int = 0
    updated_at: float = 0.0
    last_seen_at: float = 0.0


class MultiTargetTrackerService:
    """Nearest-neighbour association against each track's last observation."""

    def __init__(
        self,
        max_match_distance_px: float = 120.0,
        max_misses: int = 30,
        max_lost_seconds: float = 1.25,
    ) -> None:
        self.max_match_distance_px = max_match_distance_px
        self.max_misses = max_misses
        self.max_lost_seconds = max_lost_seconds
        self._next_track_id = 1
        self._tracks: dict[int, _Track] = {}
        self._last_timestamp_s: float | None = None
        self._last_frame_id: int | None = None
        self._id_switch_count = 0
        self._rejected_stale_frames = 0
        self._last_detection_tracks: dict[int, int] = {}
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
        self._last_timestamp_s = None
        self._last_frame_id = None
        self._id_switch_count = 0
        self._rejected_stale_frames = 0
        self._last_detection_tracks.clear()
        if self._external_adapter is not None:
            self._external_adapter.reset()

    def configure(self, profile: str | None, *, enabled: bool = True) -> str:
        with self._lock:
            selected = normalize_tracker_profile(profile, enabled)
            adapter = None if selected == "current_custom" else create_live_tracker("balloon", selected)
            self.profile = selected
            self._external_adapter = adapter
            self.last_error = None
            self._reset_unlocked()
            return selected

    def update(self, event: VisionEvent | None, *, image=None) -> MultiTargetTrackingStatus:
        with self._lock:
            return self._update_unlocked(event, image=image)

    def _update_unlocked(self, event: VisionEvent | None, *, image=None) -> MultiTargetTrackingStatus:
        if self._external_adapter is not None:
            return self._update_external(event, image=image)
        # TrackingLoop runs much faster than YOLO. A detector frame may
        # advance miss counters exactly once; otherwise one missed frame
        # expires an identity in a few control ticks.
        if event is not None and self._last_frame_id is not None:
            timestamp_s = float(event.timestamp_ms) / 1000.0
            if event.frame_id == self._last_frame_id and timestamp_s == self._last_timestamp_s:
                return self.status()
            if event.frame_id < self._last_frame_id - 10 or (self._last_timestamp_s is not None and abs(timestamp_s - self._last_timestamp_s) > 2.0):
                self._last_frame_id = None
                self._last_timestamp_s = None
            elif event.frame_id < self._last_frame_id or (
                self._last_timestamp_s is not None and timestamp_s < self._last_timestamp_s - 0.5
            ):
                self._rejected_stale_frames += 1
                return self.status()
        now = time.time() if event is None else float(event.timestamp_ms) / 1000.0
        if self._last_timestamp_s is None:
            dt = 1 / 30.0
        else:
            dt = max(1 / 120.0, min(0.5, now - self._last_timestamp_s))
        if event is not None:
            self._last_frame_id = event.frame_id
            self._last_timestamp_s = now

        # Stationary-field baseline: associate against the last real
        # observation. No Kalman prediction or synthetic motion is injected.
        predictions = {
            track_id: (track.center_x, track.center_y)
            for track_id, track in self._tracks.items()
        }

        detections = list(event.balloon_detections) if event is not None else []
        matches = self._match(predictions, detections)
        matched_tracks = {track_id for track_id, _ in matches}
        matched_detections = {detection_index for _, detection_index in matches}

        for track_id, detection_index in matches:
            track = self._tracks[track_id]
            detection = detections[detection_index]
            previous_x, previous_y = track.center_x, track.center_y
            track.center_x = float(detection.center_x)
            track.center_y = float(detection.center_y)
            track.velocity_x = (track.center_x - previous_x) / dt
            track.velocity_y = (track.center_y - previous_y) / dt
            track.detection_id = detection.id
            track.confidence = detection.confidence
            track.bbox_x = float(detection.bbox.x)
            track.bbox_y = float(detection.bbox.y)
            track.bbox_w = float(detection.bbox.w)
            track.bbox_h = float(detection.bbox.h)
            track.age_frames += 1
            track.hits += 1
            track.misses = 0
            track.updated_at = now
            track.last_seen_at = now

        for track_id, track in list(self._tracks.items()):
            if track_id in matched_tracks:
                continue
            track.age_frames += 1
            # A None event means that the control loop has no current detector
            # frame; it must not consume a 30-frame miss budget at 80 Hz.
            # Wall-clock expiry below still retires genuinely stale tracks.
            if event is not None:
                track.misses += 1
            track.detection_id = None
            track.updated_at = now
            if track.misses > self.max_misses or now - track.last_seen_at > self.max_lost_seconds:
                del self._tracks[track_id]

        for detection_index, detection in enumerate(detections):
            if detection_index not in matched_detections:
                self._create_track(detection, now)

        if event is not None:
            current_detection_tracks = {
                int(track.detection_id): track.track_id
                for track in self._tracks.values()
                if track.detection_id is not None and track.misses == 0
            }
            for detection_id, track_id in current_detection_tracks.items():
                previous_track_id = self._last_detection_tracks.get(detection_id)
                if previous_track_id is not None and previous_track_id != track_id:
                    self._id_switch_count += 1
            self._last_detection_tracks = current_detection_tracks

        return self.status()

    def _update_external(self, event: VisionEvent | None, *, image=None) -> MultiTargetTrackingStatus:
        if event is None:
            return self.status()
        timestamp_s = float(event.timestamp_ms) / 1000.0
        if self._last_frame_id is not None:
            if event.frame_id == self._last_frame_id and timestamp_s == self._last_timestamp_s:
                return self.status()
            if event.frame_id <= self._last_frame_id or (
                self._last_timestamp_s is not None and timestamp_s <= self._last_timestamp_s
            ):
                self._rejected_stale_frames += 1
                return self.status()
        dt = 1 / 30.0 if self._last_timestamp_s is None else max(1 / 120.0, min(0.5, timestamp_s - self._last_timestamp_s))
        detections = [
            lab_detection(
                stream="balloon",
                detection_id=item.id,
                x=item.bbox.x,
                y=item.bbox.y,
                w=item.bbox.w,
                h=item.bbox.h,
                confidence=item.confidence,
                class_id=0,
                class_name="balloon",
                model_id=item.source,
            )
            for item in event.balloon_detections
        ]
        try:
            outputs = self._external_adapter.update(
                frame_id=int(event.frame_id),
                capture_timestamp_ns=int(event.timestamp_ms) * 1_000_000,
                detections=detections,
                image=image,
            )
            self.last_error = None
        except Exception as exc:
            self.last_error = f"{self.profile}:{exc}"
            return self.status()

        previous = self._tracks
        current: dict[int, _Track] = {}
        for output in outputs:
            track_id = int(output.track_id)
            center_x, center_y = output.bbox.center
            prior = previous.get(track_id)
            detection = None
            if output.detection_index is not None and 0 <= int(output.detection_index) < len(event.balloon_detections):
                detection = event.balloon_detections[int(output.detection_index)]
            last_seen_at = timestamp_s if detection is not None else (prior.last_seen_at if prior is not None else timestamp_s)
            velocity_x = 0.0 if prior is None else (float(center_x) - prior.center_x) / dt
            velocity_y = 0.0 if prior is None else (float(center_y) - prior.center_y) / dt
            current[track_id] = _Track(
                track_id=track_id,
                center_x=float(center_x),
                center_y=float(center_y),
                velocity_x=velocity_x,
                velocity_y=velocity_y,
                detection_id=detection.id if detection is not None else None,
                confidence=float(output.confidence),
                bbox_x=float(output.bbox.x1),
                bbox_y=float(output.bbox.y1),
                bbox_w=float(output.bbox.width),
                bbox_h=float(output.bbox.height),
                age_frames=int(output.age_frames),
                hits=int(output.hits),
                misses=int(output.misses),
                updated_at=timestamp_s,
                last_seen_at=last_seen_at,
            )
        self._tracks = current
        current_detection_tracks = {
            int(track.detection_id): track.track_id
            for track in current.values()
            if track.detection_id is not None and track.misses == 0
        }
        for detection_id, track_id in current_detection_tracks.items():
            prior_track_id = self._last_detection_tracks.get(detection_id)
            if prior_track_id is not None and prior_track_id != track_id:
                self._id_switch_count += 1
        self._last_detection_tracks = current_detection_tracks
        self._last_frame_id = int(event.frame_id)
        self._last_timestamp_s = timestamp_s
        return self.status()

    def status(self) -> MultiTargetTrackingStatus:
        now = time.time()
        tracks = [
            MultiTargetTrack(
                track_id=track.track_id,
                detection_id=track.detection_id,
                center_x=round(track.center_x, 3),
                center_y=round(track.center_y, 3),
                velocity_x=round(track.velocity_x, 3),
                velocity_y=round(track.velocity_y, 3),
                age_frames=track.age_frames,
                hits=track.hits,
                misses=track.misses,
                confidence=track.confidence,
                bbox_x=track.bbox_x,
                bbox_y=track.bbox_y,
                bbox_w=track.bbox_w,
                bbox_h=track.bbox_h,
                predicted=track.misses > 0,
                fresh=track.misses == 0,
                last_seen_at=track.last_seen_at,
                last_seen_age_ms=max(0.0, (now - track.last_seen_at) * 1000.0),
                updated_at=track.updated_at,
            )
            for track in sorted(self._tracks.values(), key=lambda item: item.track_id)
        ]
        return MultiTargetTrackingStatus(
            tracker_kind=self.profile,
            active_track_count=len(tracks),
            id_switch_count=self._id_switch_count,
            rejected_stale_frames=self._rejected_stale_frames,
            last_frame_id=self._last_frame_id,
            last_timestamp_ms=(round(self._last_timestamp_s * 1000) if self._last_timestamp_s is not None else None),
            tracks=tracks,
        )

    def _create_track(self, detection: BalloonDetection, timestamp_s: float) -> None:
        track_id = self._next_track_id
        self._next_track_id += 1
        self._tracks[track_id] = _Track(
            track_id=track_id,
            center_x=float(detection.center_x),
            center_y=float(detection.center_y),
            velocity_x=0.0,
            velocity_y=0.0,
            detection_id=detection.id,
            confidence=detection.confidence,
            bbox_x=float(detection.bbox.x),
            bbox_y=float(detection.bbox.y),
            bbox_w=float(detection.bbox.w),
            bbox_h=float(detection.bbox.h),
            updated_at=timestamp_s,
            last_seen_at=timestamp_s,
        )

    def _match(self, predictions: dict[int, tuple[float, float]], detections: list[BalloonDetection]) -> list[tuple[int, int]]:
        matches: list[tuple[int, int]] = []
        used_tracks: set[int] = set()
        used_detections: set[int] = set()

        candidates: list[tuple[float, int, int]] = []
        for track_id, (x, y) in predictions.items():
            if track_id in used_tracks:
                continue
            for detection_index, detection in enumerate(detections):
                if detection_index in used_detections:
                    continue
                distance = math.hypot(detection.center_x - x, detection.center_y - y)
                if distance <= self.max_match_distance_px:
                    candidates.append((distance, track_id, detection_index))
        for _, track_id, detection_index in sorted(candidates):
            if track_id not in used_tracks and detection_index not in used_detections:
                matches.append((track_id, detection_index))
                used_tracks.add(track_id)
                used_detections.add(detection_index)
        return matches
