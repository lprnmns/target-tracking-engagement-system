"""Non-blocking lock/shot evidence timeline.

This service observes tracking and Gateway acknowledgement events.  It never
evaluates a fire gate, emits serial data, or changes control state.
"""

from __future__ import annotations

import json
import os
import queue
import re
import threading
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Any

from app.schemas.engagement_evidence import EngagementEvidenceManifest, EngagementEvidenceStatus, EngagementEvidenceSummary
from app.schemas.digital_twin import DigitalTwinReplayEvent, DigitalTwinReplaySummary, DigitalTwinState
from app.schemas.log import LogLevel
from app.schemas.tracking import AssociationStatus, MultiTargetTrackingStatus, TrackingState, TrackingUpdate
from app.schemas.vision import VisionEvent
from app.services.storage_paths import project_root
from app.services import yarisma_bayraklari as yb


class EngagementEvidenceService:
    """Creates an auditable engagement timeline when a tracked target locks.

    Timeline writes are dispatched to a bounded daemon worker.  Evidence is
    deliberately best-effort: an unavailable disk or full queue is visible in
    the record but cannot delay, reject, or replay a physical command.
    """

    PRE_ROLL_MAX_EVENTS = 60
    RECENT_MAX = 40
    WRITER_QUEUE_MAX = 512
    CAMERA_SAMPLE_INTERVAL_S = 0.10
    DIGITAL_TWIN_SAMPLE_INTERVAL_S = 0.10
    POST_ROLL_S = 3.0
    # records() is served from an in-memory index.  The disk is re-scanned in a
    # low-priority daemon thread, never on the request path.
    INDEX_REFRESH_MIN_INTERVAL_S = 10.0
    INDEX_PARSE_YIELD_S = 0.005
    INDEX_STARTUP_DELAY_S = 2.0
    _ENGAGEMENT_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{1,128}$")

    def __init__(self, logger, root: Path | None = None) -> None:
        self.logger = logger
        self.root = root or (project_root() / "reports" / "engagement_evidence")
        self._pre_roll: deque[dict[str, Any]] = deque(maxlen=self.PRE_ROLL_MAX_EVENTS)
        self._recent: deque[EngagementEvidenceSummary] = deque(maxlen=self.RECENT_MAX)
        self._active: EngagementEvidenceSummary | None = None
        self._active_key: str | None = None
        self._active_manifest: EngagementEvidenceManifest | None = None
        self._queue: queue.Queue[tuple[str, Path, Any]] = queue.Queue(maxsize=self.WRITER_QUEUE_MAX)
        self._dropped = 0
        self._camera_frame_index = 0
        self._last_camera_sample_at = 0.0
        self._last_digital_twin_sample_at = 0.0
        self._last_observed_frame_id: int | None = None
        self._last_observed_at = 0.0
        self._post_roll_until: float | None = None
        self._review_finalize_queued = False
        # Several state transitions append a timeline entry while updating the
        # manifest.  They intentionally share the same short critical section;
        # RLock prevents the observer path from waiting on itself.
        self._lock = threading.RLock()
        # manifest path -> (mtime_ns, size, summary).  Summaries are tiny; the
        # manifests themselves (pre-roll included) can be >1 MB each.
        self._index: dict[str, tuple[int, int, EngagementEvidenceSummary]] = {}
        self._index_lock = threading.Lock()
        self._index_scan_running = False
        self._index_last_scan_at: float | None = None
        self.last_event: tuple[str, dict] | None = None
        self._worker = threading.Thread(target=self._writer_loop, name="engagement-evidence-writer", daemon=True)
        self._worker.start()
        self._schedule_index_scan(delay_s=self.INDEX_STARTUP_DELAY_S)

    def observe_frame(
        self,
        event: VisionEvent | None,
        update: TrackingUpdate,
        tracks: MultiTargetTrackingStatus,
        associations: AssociationStatus,
        *,
        mission_stage: str,
        command_profile: str,
    ) -> EngagementEvidenceStatus:
        # Tracking runs faster than perception (roughly 80 Hz vs 20–40 Hz).
        # Serialising the same VisionEvent on every control tick inflated the
        # evidence queue and competed with the detector for CPU/disk.  Keep a
        # 10 Hz timeline while still processing the first LOCKED transition
        # immediately so the engagement record is never delayed.
        now_mono = time.monotonic()
        event_frame_id = event.frame_id if event is not None else update.frame_id
        lock_transition = update.state == TrackingState.LOCKED and (self._active is None or self._active_key is None)
        if (
            event_frame_id == self._last_observed_frame_id
            and now_mono - self._last_observed_at < self.CAMERA_SAMPLE_INTERVAL_S
            and not lock_transition
        ):
            return self.status()
        self._last_observed_frame_id = event_frame_id
        self._last_observed_at = now_mono
        frame = self._frame_snapshot(event, update, tracks, associations)
        self._pre_roll.append(frame)
        if update.state != TrackingState.LOCKED or event is None:
            self._active_key = None
            return self.status()

        target = self._target_snapshot(event, update, tracks, associations)
        key = self._target_key(target)
        # Evidence identity must be target-stable. Falling back to frame_id
        # made AIRCRAFT-only tracking open a new engagement on every detector
        # frame, flooding the JPEG/JSON writer and starving camera + YOLO.
        # An unresolved identity is not auditable evidence, so wait for the
        # next frame instead of manufacturing a per-frame target.
        if key is None:
            return self.status()
        _y1b_surdur = (
            yb.Y1B_KANIT_SURDUR
            and self._active is not None
            and self._active_key == key
            and self._active.state in {"LOCKED_RECORDING", "SHOT_PENDING_CONFIRMATION"}
        )
        if not _y1b_surdur and (self._active is None or self._active_key != key or self._active.state != "LOCKED_RECORDING"):
            self._begin_lock(target, key, frame, mission_stage, command_profile)
        self._append_timeline("vision_timeline.jsonl", {"kind": "lock_frame", **frame, "target": target})
        return self.status()

    def capture_active_camera_frame(self, camera_runtime: Any) -> None:
        """Queue an actual camera JPEG while a lock evidence record is active."""
        with self._lock:
            active = self._active
        if active is None or not self._capture_is_active(active):
            return
        now = time.monotonic()
        if now - self._last_camera_sample_at < self.CAMERA_SAMPLE_INTERVAL_S:
            return
        if self._queue.qsize() > 5:
            return
        self._last_camera_sample_at = now
        try:
            frame, captured_at = camera_runtime.evidence_frame_copy()
        except Exception:
            return
        if frame is None:
            return
        self._camera_frame_index += 1
        filename = f"{self._camera_frame_index:06d}_{int((captured_at or time.time()) * 1000)}.jpg"
        self._enqueue("write_jpeg", self._event_dir(active) / "camera_frames" / filename, frame)
        with self._lock:
            manifest = self._active_manifest
            if manifest is not None and manifest.camera_capture.get("status") != "JPEG_SEQUENCE":
                active = active.model_copy(update={"camera_capture_status": "JPEG_SEQUENCE", "updated_at": time.time()})
                self._active = active
                self._active_manifest = manifest.model_copy(
                    update={
                        "summary": active,
                        "camera_capture": {
                            "status": "JPEG_SEQUENCE",
                            "reason_code": "CAMERA_FRAME_SEQUENCE_CAPTURE_ACTIVE",
                            "first_frame": f"camera_frames/{filename}",
                            "sample_interval_ms": int(self.CAMERA_SAMPLE_INTERVAL_S * 1000),
                        }
                    }
                )
                self._enqueue("write_json", self._manifest_path(active), self._active_manifest.model_dump(mode="json"))

    def record_shot_ack(self, runtime: Any, candidate: dict[str, Any], result: Any) -> EngagementEvidenceSummary | None:
        """Attach a confirmed Gateway acknowledgement to the active lock record."""
        if not getattr(result, "accepted", False):
            return None
        with self._lock:
            active = self._active
            manifest = self._active_manifest
            if active is None or manifest is None:
                return None
            candidate_track = candidate.get("balloon_track_id")
            if candidate_track is not None and active.balloon_track_id != candidate_track:
                return None
            now = time.time()
            shot_id = f"shot-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
            active = active.model_copy(
                update={
                    "shot_id": shot_id,
                    "state": "SHOT_PENDING_CONFIRMATION",
                    "updated_at": now,
                    "reason_codes": [*active.reason_codes, "PICO_FIRE_ACK"],
                }
            )
            shot_snapshot = {
                "shot_id": shot_id,
                "wall_clock": now,
                "monotonic_ns": time.monotonic_ns(),
                "candidate": candidate,
                "gateway_result": result.model_dump(mode="json"),
                "motion": self._motion_snapshot(runtime),
            }
            self._active = active
            self._active_manifest = manifest.model_copy(update={"summary": active, "shot_snapshot": shot_snapshot})
            self._enqueue("write_json", self._manifest_path(active), self._active_manifest.model_dump(mode="json"))
            self._append_timeline("command_timeline.jsonl", {"kind": "pico_fire_ack", **shot_snapshot})
            self._emit("engagement_evidence.shot_ack", active)
        return active

    def capture_active_digital_twin_state(self, state_factory) -> None:
        """Sample a read-only twin state on the engagement's monotonic clock."""
        with self._lock:
            active = self._active
        if active is None or not self._capture_is_active(active):
            return
        now = time.monotonic()
        if now - self._last_digital_twin_sample_at < self.DIGITAL_TWIN_SAMPLE_INTERVAL_S:
            return
        self._last_digital_twin_sample_at = now
        try:
            state = state_factory()
            payload = state.model_dump(mode="json")
        except Exception as exc:
            self.logger.emit(LogLevel.WARN, "ENGAGEMENT_EVIDENCE", "Digital twin evidence snapshot unavailable", {"reason_code": "DIGITAL_TWIN_SNAPSHOT_FAILED", "error": str(exc)})
            return
        self._append_timeline(
            "digital_twin_timeline.jsonl",
            {"kind": "digital_twin_state", "monotonic_ns": time.monotonic_ns(), "state": payload},
        )
        with self._lock:
            manifest = self._active_manifest
            if manifest is not None and manifest.digital_twin_capture.get("status") != "TIMELINE_READY":
                self._active_manifest = manifest.model_copy(
                    update={
                        "digital_twin_capture": {
                            "status": "TIMELINE_READY",
                            "reason_code": "DIGITAL_TWIN_STATE_TIMELINE_CAPTURE_ACTIVE",
                            "sample_interval_ms": int(self.DIGITAL_TWIN_SAMPLE_INTERVAL_S * 1000),
                        }
                    }
                )
                self._enqueue("write_json", self._manifest_path(active), self._active_manifest.model_dump(mode="json"))

    def record_confirmation_status(self, confirmations: Any) -> EngagementEvidenceSummary | None:
        """Persist a terminal visual result without influencing engagement control."""
        with self._lock:
            active = self._active
            manifest = self._active_manifest
            if active is None or manifest is None or active.state != "SHOT_PENDING_CONFIRMATION" or active.balloon_track_id is None:
                return None
            record = next((item for item in confirmations.records if item.balloon_track_id == active.balloon_track_id), None)
            if record is None or record.state.value == "PENDING_CONFIRMATION":
                return None
            now = time.time()
            active = active.model_copy(
                update={
                    "state": "COMPLETED",
                    "outcome": record.outcome.value,
                    "updated_at": now,
                    "reason_codes": [*active.reason_codes, record.reason],
                }
            )
            outcome = {
                "outcome": record.outcome.value,
                "reason_code": record.reason,
                "confirmed_at": now,
                "record": record.model_dump(mode="json"),
            }
            self._active = active
            camera_capture = {
                **manifest.camera_capture,
                "review_video": "camera_review.mp4",
                "review_video_status": "QUEUED",
            }
            self._active_manifest = manifest.model_copy(update={"summary": active, "outcome": outcome, "camera_capture": camera_capture})
            self._post_roll_until = time.monotonic() + self.POST_ROLL_S
            self._review_finalize_queued = False
            self._enqueue("write_json", self._manifest_path(active), self._active_manifest.model_dump(mode="json"))
            self._append_timeline("command_timeline.jsonl", {"kind": "visual_outcome", **outcome})
            self._emit("engagement_evidence.outcome_committed", active)
            return active

    def finalize_due_recording(self) -> None:
        """Render the review video only after the fixed post-roll elapsed."""
        with self._lock:
            active = self._active
            if active is None or active.state != "COMPLETED" or self._post_roll_until is None:
                return
            if time.monotonic() < self._post_roll_until or self._review_finalize_queued:
                return
            self._review_finalize_queued = True
            self._post_roll_until = None
        self._append_timeline("command_timeline.jsonl", {"kind": "post_roll_complete", "monotonic_ns": time.monotonic_ns()})
        self._enqueue("assemble_camera_review", self._event_dir(active) / "camera_review.mp4", self._event_dir(active))

    def records(self, limit: int = 50) -> list[EngagementEvidenceSummary]:
        """Return persisted + live evidence summaries without touching the disk.

        The previous implementation parsed every ``manifest.json`` (each one
        embeds the pre-roll and can exceed 1 MB) on every call.  The result is
        now assembled from an in-memory index that the writer thread keeps
        current and a background thread reconciles with the disk, so a REST
        poll costs microseconds and cannot starve tracking/WebSocket threads.
        """
        self._schedule_index_scan()
        merged: dict[str, EngagementEvidenceSummary] = {}
        with self._index_lock:
            for _mtime_ns, _size, summary in self._index.values():
                merged[summary.engagement_id] = summary
        with self._lock:
            live = [*self._recent]
            if self._active is not None:
                live.append(self._active)
        for summary in live:
            known = merged.get(summary.engagement_id)
            if known is None or summary.updated_at >= known.updated_at:
                merged[summary.engagement_id] = summary
        ordered = sorted(merged.values(), key=lambda item: (item.updated_at, item.engagement_id), reverse=True)
        return ordered[:max(1, limit)]

    def manifest(self, engagement_id: str) -> EngagementEvidenceManifest:
        summary = self._summary_for(engagement_id)
        return EngagementEvidenceManifest.model_validate_json(self._manifest_path(summary).read_text(encoding="utf-8"))

    def media_path(self, engagement_id: str, filename: str) -> Path:
        if Path(filename).name != filename or filename not in {"camera_review.mp4", "camera_review_status.json"}:
            raise KeyError(filename)
        # Only the summary is needed to locate the event directory; do not
        # parse the multi-MB manifest for a media download.
        path = self._event_dir(self._summary_for(engagement_id)) / filename
        if not path.is_file():
            raise KeyError(filename)
        return path

    def digital_twin_replay(self, engagement_id: str) -> DigitalTwinReplaySummary:
        summary = self._summary_for(engagement_id)
        path = self._event_dir(summary) / "digital_twin_timeline.jsonl"
        events: list[DigitalTwinReplayEvent] = []
        first_ns: int | None = None
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                try:
                    entry = json.loads(line)
                    state = DigitalTwinState.model_validate(entry.get("state"))
                    monotonic_ns = int(entry["monotonic_ns"])
                except Exception:
                    continue
                if first_ns is None:
                    first_ns = monotonic_ns
                events.append(
                    DigitalTwinReplayEvent(
                        t_ms=max(0, int((monotonic_ns - first_ns) / 1_000_000)),
                        target=state.target,
                        target_projection_estimates=state.target_projection_estimates,
                        device_pose=state.device_pose,
                        tracker=state.tracker,
                        note=f"engagement={engagement_id}; outcome={summary.outcome}",
                    )
                )
        duration_ms = events[-1].t_ms if events else 0
        return DigitalTwinReplaySummary(
            run_id=f"engagement-{engagement_id}",
            source="engagement_evidence_read_only",
            duration_ms=duration_ms,
            event_count=len(events),
            events=events,
        )

    def _summary_for(self, engagement_id: str) -> EngagementEvidenceSummary:
        """Locate one engagement summary without scanning/parsing every manifest."""
        if not self._ENGAGEMENT_ID_RE.match(engagement_id):
            raise KeyError(engagement_id)
        with self._lock:
            for summary in (self._active, *self._recent):
                if summary is not None and summary.engagement_id == engagement_id:
                    return summary
        with self._index_lock:
            for _mtime_ns, _size, summary in self._index.values():
                if summary.engagement_id == engagement_id:
                    return summary
        # Not indexed yet (e.g. just after a restart): resolve only this one id.
        if self.root.exists():
            for manifest_path in self.root.glob(f"*/{engagement_id}/manifest.json"):
                summary = self._read_summary(manifest_path)
                if summary is not None and summary.engagement_id == engagement_id:
                    self._index_put_from_disk(manifest_path, summary)
                    return summary
        raise KeyError(engagement_id)

    def _schedule_index_scan(self, delay_s: float = 0.0) -> None:
        with self._index_lock:
            if self._index_scan_running:
                return
            if self._index_last_scan_at is not None and time.monotonic() - self._index_last_scan_at < self.INDEX_REFRESH_MIN_INTERVAL_S:
                return
            self._index_scan_running = True
        threading.Thread(target=self._index_scan, args=(delay_s,), name="engagement-evidence-index", daemon=True).start()

    def _index_scan(self, delay_s: float) -> None:
        """Reconcile the index with the disk; parses only new/changed manifests."""
        try:
            if delay_s > 0:
                time.sleep(delay_s)
            if self.root.exists():
                for manifest_path in self.root.glob("*/*/manifest.json"):
                    try:
                        stat = manifest_path.stat()
                    except OSError:
                        continue
                    key = str(manifest_path)
                    with self._index_lock:
                        cached = self._index.get(key)
                    if cached is not None and cached[0] == stat.st_mtime_ns and cached[1] == stat.st_size:
                        continue
                    summary = self._read_summary(manifest_path)
                    if summary is not None:
                        with self._index_lock:
                            self._index[key] = (stat.st_mtime_ns, stat.st_size, summary)
                    # Yield the GIL between large parses so tracking/WS threads keep running.
                    time.sleep(self.INDEX_PARSE_YIELD_S)
            with self._index_lock:
                for key in [item for item in self._index if not os.path.exists(item)]:
                    self._index.pop(key, None)
        except Exception as exc:  # index is a read-side cache; never fatal
            self.logger.emit(LogLevel.WARN, "ENGAGEMENT_EVIDENCE", "Evidence index scan failed", {"reason_code": "EVIDENCE_INDEX_SCAN_FAILED", "error": str(exc)})
        finally:
            with self._index_lock:
                self._index_scan_running = False
                self._index_last_scan_at = time.monotonic()

    @staticmethod
    def _read_summary(manifest_path: Path) -> EngagementEvidenceSummary | None:
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
            return EngagementEvidenceSummary.model_validate(data["summary"])
        except Exception:
            return None

    def _index_put_from_disk(self, manifest_path: Path, summary: EngagementEvidenceSummary) -> None:
        try:
            stat = manifest_path.stat()
        except OSError:
            return
        with self._index_lock:
            self._index[str(manifest_path)] = (stat.st_mtime_ns, stat.st_size, summary)

    def status(self) -> EngagementEvidenceStatus:
        with self._lock:
            return EngagementEvidenceStatus(
                active=self._active,
                recent=list(reversed(self._recent)),
                pre_roll_frame_count=len(self._pre_roll),
                writer_queue_depth=self._queue.qsize(),
                dropped_timeline_entries=self._dropped,
            )

    def flush(self, timeout_s: float = 2.0) -> None:
        """Test/controlled-shutdown helper; never called from the control loop."""
        deadline = time.monotonic() + timeout_s
        while self._queue.unfinished_tasks and time.monotonic() < deadline:
            time.sleep(0.005)

    def _begin_lock(self, target: dict[str, Any], target_key: str, frame: dict[str, Any], mission_stage: str, command_profile: str) -> None:
        now = time.time()
        engagement_id = f"lock-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
        relative = Path(time.strftime("%Y-%m-%d")) / engagement_id
        summary = EngagementEvidenceSummary(
            engagement_id=engagement_id,
            state="LOCKED_RECORDING",
            created_at=now,
            updated_at=now,
            mission_stage=mission_stage,
            command_profile=command_profile,
            body_track_id=target.get("body_track_id"),
            body_detection_id=target.get("body_detection_id"),
            balloon_track_id=target.get("balloon_track_id"),
            balloon_detection_id=target.get("balloon_detection_id"),
            target_class=target.get("body_class"),
            target_team=target.get("body_team"),
            association_state=str(target.get("association_state", "unresolved")),
            frame_id=frame.get("frame_id"),
            reason_codes=["TARGET_LOCKED_RECORDING_STARTED", "CAMERA_TIMELINE_CAPTURE_ACTIVE"],
            evidence_path=str(relative),
        )
        manifest = EngagementEvidenceManifest(
            summary=summary,
            monotonic_ns=time.monotonic_ns(),
            lock_snapshot={"target": target, "frame": frame, "pre_roll": list(self._pre_roll)},
            camera_capture={
                "status": "TIMELINE_ONLY",
                "reason_code": "CAMERA_VIDEO_RECORDER_PENDING",
                "pre_roll_event_count": len(self._pre_roll),
            },
            digital_twin_capture={
                "status": "TIMELINE_ONLY",
                "reason_code": "DIGITAL_TWIN_REPLAY_TIMELINE_PENDING",
            },
        )
        with self._lock:
            if self._active is not None:
                self._recent.append(self._active)
            self._active = summary
            self._active_key = target_key
            self._active_manifest = manifest
            self._camera_frame_index = 0
            self._last_camera_sample_at = 0.0
            self._last_digital_twin_sample_at = 0.0
            self._post_roll_until = None
            self._review_finalize_queued = False
        self._enqueue("write_json", self._manifest_path(summary), manifest.model_dump(mode="json"))
        for item in self._pre_roll:
            self._append_timeline("vision_timeline.jsonl", {"kind": "pre_roll_frame", **item})
        self._emit("engagement_evidence.lock_started", summary)

    def _append_timeline(self, filename: str, payload: dict[str, Any]) -> None:
        with self._lock:
            active = self._active
        if active is None:
            return
        self._enqueue("append_jsonl", self._event_dir(active) / filename, payload)

    def _capture_is_active(self, active: EngagementEvidenceSummary) -> bool:
        return active.state in {"LOCKED_RECORDING", "SHOT_PENDING_CONFIRMATION"} or (
            active.state == "COMPLETED" and self._post_roll_until is not None and time.monotonic() < self._post_roll_until
        )

    def _enqueue(self, kind: str, path: Path, payload: Any) -> None:
        try:
            self._queue.put_nowait((kind, path, payload))
        except queue.Full:
            self._dropped += 1
            self.logger.emit(LogLevel.WARN, "ENGAGEMENT_EVIDENCE", "Evidence writer queue full", {"reason_code": "EVIDENCE_DROPPED_FRAMES"})

    def _writer_loop(self) -> None:
        while True:
            kind, path, payload = self._queue.get()
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                if kind == "write_json":
                    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                    if path.name == "manifest.json":
                        self._index_note_manifest_written(path, payload)
                elif kind == "append_jsonl":
                    with path.open("a", encoding="utf-8") as handle:
                        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
                elif kind == "write_jpeg":
                    try:
                        import cv2
                        ok, encoded = cv2.imencode(".jpg", payload, [int(cv2.IMWRITE_JPEG_QUALITY), 65])
                        if not ok:
                            raise RuntimeError("jpeg_encode_failed")
                        path.write_bytes(encoded.tobytes())
                    except ImportError as exc:
                        raise RuntimeError("opencv_jpeg_encoder_unavailable") from exc
                elif kind == "assemble_camera_review":
                    self._assemble_camera_review(path, payload)
            except Exception as exc:  # evidence must never affect control flow
                self.logger.emit(LogLevel.WARN, "ENGAGEMENT_EVIDENCE", "Evidence writer failed", {"reason_code": "EVIDENCE_WRITE_FAILED", "error": str(exc)})
            finally:
                self._queue.task_done()

    def _index_note_manifest_written(self, path: Path, payload: Any) -> None:
        """Keep the read index current from the payload we just wrote (no re-read)."""
        try:
            summary = EngagementEvidenceSummary.model_validate(payload["summary"])
            self._index_put_from_disk(path, summary)
        except Exception:
            pass

    def _assemble_camera_review(self, output_path: Path, event_dir: Path) -> None:
        """Create a review-only MP4 after recording has stopped.

        It runs in the evidence writer worker, never in tracking/Gateway.
        The source JPEG sequence stays canonical if a host codec is absent.
        """
        status_path = event_dir / "camera_review_status.json"
        if os.environ.get("ISTIKLAL_DISABLE_VIDEO_REVIEW", "1") == "1":
            status_path.write_text(json.dumps({"status": "BYPASSED", "reason_code": "COMPETITION_PERFORMANCE_OPTIMIZED"}, indent=2) + "\n", encoding="utf-8")
            return
        frames = sorted((event_dir / "camera_frames").glob("*.jpg"))
        if not frames:
            status_path.write_text(json.dumps({"status": "UNAVAILABLE", "reason_code": "CAMERA_FRAMES_UNAVAILABLE"}, indent=2) + "\n", encoding="utf-8")
            return
        try:
            import cv2
        except ImportError:
            status_path.write_text(json.dumps({"status": "UNAVAILABLE", "reason_code": "OPENCV_VIDEO_ENCODER_UNAVAILABLE"}, indent=2) + "\n", encoding="utf-8")
            return
        first = cv2.imread(str(frames[0]))
        if first is None:
            status_path.write_text(json.dumps({"status": "UNAVAILABLE", "reason_code": "CAMERA_JPEG_DECODE_FAILED"}, indent=2) + "\n", encoding="utf-8")
            return
        height, width = first.shape[:2]
        writer = cv2.VideoWriter(str(output_path), cv2.VideoWriter_fourcc(*"mp4v"), 10.0, (width, height))
        if not writer.isOpened():
            status_path.write_text(json.dumps({"status": "UNAVAILABLE", "reason_code": "MP4_VIDEO_WRITER_UNAVAILABLE"}, indent=2) + "\n", encoding="utf-8")
            return
        encoded_count = 0
        try:
            for frame_path in frames:
                frame = cv2.imread(str(frame_path))
                if frame is None:
                    continue
                if frame.shape[:2] != (height, width):
                    frame = cv2.resize(frame, (width, height))
                writer.write(frame)
                encoded_count += 1
        finally:
            writer.release()
        status_path.write_text(json.dumps({"status": "READY", "frame_count": encoded_count, "path": output_path.name}, indent=2) + "\n", encoding="utf-8")

    def _event_dir(self, summary: EngagementEvidenceSummary) -> Path:
        return self.root / summary.evidence_path

    def _manifest_path(self, summary: EngagementEvidenceSummary) -> Path:
        return self._event_dir(summary) / "manifest.json"

    def _emit(self, event_type: str, summary: EngagementEvidenceSummary) -> None:
        payload = summary.model_dump(mode="json")
        self.last_event = (event_type, payload)
        self.logger.emit(LogLevel.INFO, "ENGAGEMENT_EVIDENCE", event_type, payload)

    @staticmethod
    def _frame_snapshot(event: VisionEvent | None, update: TrackingUpdate, tracks: MultiTargetTrackingStatus, associations: AssociationStatus) -> dict[str, Any]:
        return {
            "wall_clock": time.time(),
            "monotonic_ns": time.monotonic_ns(),
            "frame_id": event.frame_id if event is not None else update.frame_id,
            "vision_timestamp_ms": event.timestamp_ms if event is not None else None,
            "tracking": update.model_dump(mode="json"),
            "tracks": tracks.model_dump(mode="json"),
            "associations": associations.model_dump(mode="json"),
            "vision": event.model_dump(mode="json") if event is not None else None,
        }

    @staticmethod
    def _target_snapshot(event: VisionEvent, update: TrackingUpdate, tracks: MultiTargetTrackingStatus, associations: AssociationStatus) -> dict[str, Any]:
        if update.selected_target_kind == "body" and event.body_detections:
            body = next(
                (
                    item
                    for item in event.body_detections
                    if update.selected_track_id is not None and item.track_id == update.selected_track_id
                ),
                None,
            )
            if body is None:
                body = next(
                    (
                        item
                        for item in event.body_detections
                        if update.selected_detection_id is not None and item.id == update.selected_detection_id
                    ),
                    None,
                )
            if body is None and update.target_center_x is not None and update.target_center_y is not None:
                body = min(
                    event.body_detections,
                    key=lambda item: (
                        item.bbox.x + item.bbox.w / 2 - update.target_center_x
                    ) ** 2
                    + (
                        item.bbox.y + item.bbox.h / 2 - update.target_center_y
                    ) ** 2,
                )
            if body is None:
                return {}
            return {
                "body_detection_id": body.id,
                "body_track_id": body.track_id,
                "body_bbox": body.bbox.model_dump(mode="json"),
                "body_class": body.class_name,
                "body_team": body.target_team,
                "association_state": "aircraft_direct",
            }
        if update.target_center_x is None or update.target_center_y is None or not event.balloon_detections:
            return {}
        balloon = min(
            event.balloon_detections,
            key=lambda item: (item.center_x - update.target_center_x) ** 2 + (item.center_y - update.target_center_y) ** 2,
        )
        track = next((item for item in tracks.tracks if item.detection_id == balloon.id and item.fresh), None)
        association = next((item for item in associations.associations if track is not None and item.balloon_track_id == track.track_id), None)
        body = next((item for item in event.body_detections if association is not None and item.id == association.body_detection_id), None)
        return {
            "balloon_detection_id": balloon.id,
            "balloon_track_id": track.track_id if track is not None else None,
            "balloon_bbox": balloon.bbox.model_dump(mode="json"),
            "body_detection_id": association.body_detection_id if association is not None else None,
            "body_track_id": association.body_track_id if association is not None else None,
            "association_state": association.state if association is not None else "unresolved",
            "body_class": body.class_name if body is not None else None,
            "body_team": body.target_team if body is not None else None,
            "body_bbox": body.bbox.model_dump(mode="json") if body is not None else None,
        }

    @staticmethod
    def _target_key(target: dict[str, Any]) -> str | None:
        """Return a stable, kind-qualified evidence identity."""
        if target.get("balloon_track_id") is not None:
            return f"balloon-track:{target['balloon_track_id']}"
        if target.get("association_state") == "aircraft_direct" and target.get("body_track_id") is None:
            return None
        if target.get("body_track_id") is not None:
            return f"body-track:{target['body_track_id']}"
        if target.get("balloon_detection_id") is not None:
            return f"balloon-detection:{target['balloon_detection_id']}"
        if target.get("body_detection_id") is not None:
            return f"body-detection:{target['body_detection_id']}"
        return None

    @staticmethod
    def _motion_snapshot(runtime: Any) -> dict[str, Any]:
        try:
            return runtime.motion.status().model_dump(mode="json")
        except Exception:
            return {"reason_code": "MOTION_SNAPSHOT_UNAVAILABLE"}
