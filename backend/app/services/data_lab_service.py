import json
import time
import uuid
from pathlib import Path
from typing import Any

from app.schemas.data_lab import (
    DataLabAnnotationCandidate,
    DataLabAnnotationReviewRequest,
    DataLabDatasetHealth,
    DataLabDetectionRecord,
    DataLabExportResponse,
    DataLabRecordResponse,
    DataLabReplayResult,
    DataLabSessionSummary,
    DataLabStatus,
)
from app.schemas.log import LogLevel
from app.schemas.session import RecordEventRequest, SessionRecord, SessionScenario, StartSessionRequest
from app.schemas.vision import VisionEvent
from app.services.log_service import JsonlLogService
from app.services.session_service import SessionService
from app.services.storage_paths import project_root


class DataLabService:
    def __init__(self, sessions: SessionService, logger: JsonlLogService) -> None:
        self.sessions = sessions
        self.logger = logger
        self.root = project_root() / "exports" / "data_lab"
        self.root.mkdir(parents=True, exist_ok=True)
        self.last_event: tuple[str, dict] | None = None
        self.latest_replay = DataLabReplayResult(replay_id="none", replay_status="not_run")
        self._review_path = self.root / "annotation_reviews.json"

    def status(self, runtime) -> DataLabStatus:
        sessions = self.list_sessions()
        latest = sessions[0] if sessions else None
        warnings: list[str] = []
        if latest is None:
            warnings.append("No Data Lab sessions recorded yet.")
        return DataLabStatus(
            sessions_count=len(sessions),
            latest_session_id=latest.session_id if latest else None,
            latest_detection=latest.latest_detection if latest else None,
            export_root=str(self.root),
            replay_status="replay_foundation_ready" if latest else "replay_execution_not_implemented",
            replay_ready=bool(latest and latest.stats.get("frame_count", 0) > 0),
            warnings=warnings,
            advisory_only=True,
            no_physical_command_generated=True,
        )

    def list_sessions(self) -> list[DataLabSessionSummary]:
        return [self._summary_for_session(session) for session in self.sessions.list_sessions()]

    def latest_session(self) -> DataLabSessionSummary | None:
        sessions = self.list_sessions()
        return sessions[0] if sessions else None

    def record_latest_detection(self, runtime) -> DataLabRecordResponse:
        event = runtime.vision_pipeline.latest()
        session = self._active_or_new_session(runtime, event)
        detection_record = self._detection_record(event)
        self.sessions.record_event(
            session.session_id,
            RecordEventRequest(
                event_type="detection",
                payload={
                    **detection_record.model_dump(mode="json"),
                    "session_id": session.session_id,
                    "recorded_by": "data_lab_foundation",
                },
            ),
        )
        refreshed = self.sessions.get_session(session.session_id)
        payload = {
            "session_id": refreshed.session_id,
            "frame_id": detection_record.frame_id,
            "source": detection_record.source,
            "camera_source_kind": detection_record.camera_source_kind,
            "frame_origin": detection_record.frame_origin,
            "detector_kind": detection_record.detector_kind,
            "body_count": detection_record.body_count,
            "balloon_count": detection_record.balloon_count,
            "advisory_only": True,
            "no_physical_command_generated": True,
            "summary": f"Data Lab session recorded; source={detection_record.source}; no physical command generated.",
        }
        summary = f"Data Lab session recorded; source={detection_record.source}; no physical command generated."
        self._event(
            "data_lab.session_recorded",
            payload,
            summary,
        )
        return DataLabRecordResponse(accepted=True, session=refreshed, detection_record=detection_record)

    def export_evidence(self, runtime, output_dir: Path | None = None) -> DataLabExportResponse:
        if not self.sessions.list_sessions():
            self.record_latest_detection(runtime)
        created_at = time.time()
        export_id = f"data_lab_export_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
        export_dir = output_dir or self.root / export_id
        export_dir.mkdir(parents=True, exist_ok=True)
        files = self.write_evidence_files(runtime, export_dir)
        sample_count = len(self.detection_events_sample())
        response = DataLabExportResponse(
            export_id=export_id,
            created_at=created_at,
            output_dir=str(export_dir),
            files=[str(path) for path in files],
            sessions_count=len(self.sessions.list_sessions()),
            detection_events_count=sample_count,
            advisory_only=True,
            no_physical_command_generated=True,
        )
        summary = (
            f"Data Lab evidence export completed; sessions={response.sessions_count}; "
            f"detection_events={response.detection_events_count}; no physical command generated."
        )
        self._event(
            "data_lab.export_completed",
            {**response.model_dump(mode="json"), "summary": summary},
            summary,
        )
        return response

    def write_evidence_files(self, runtime, output_dir: Path) -> list[Path]:
        output_dir.mkdir(parents=True, exist_ok=True)
        sessions_json = output_dir / "data_lab_sessions.json"
        summary_md = output_dir / "data_lab_summary.md"
        sample_jsonl = output_dir / "detection_events_sample.jsonl"
        replay_md = output_dir / "replay_readiness.md"
        replay_summary_md = output_dir / "replay_summary.md"
        replay_latest_json = output_dir / "replay_latest.json"
        annotation_candidates_json = output_dir / "annotation_candidates.json"
        annotation_review_summary_md = output_dir / "annotation_review_summary.md"
        dataset_health_summary_md = output_dir / "dataset_health_summary.md"
        real_camera_summary_md = output_dir / "real_camera_evidence_summary.md"
        real_camera_latest_json = output_dir / "real_camera_evidence_latest.json"
        legacy_presets_json = output_dir / "legacy_perception_presets.json"
        legacy_migration_md = output_dir / "legacy_perception_migration_summary.md"
        direction_profile_json = output_dir / "direction_calibration_profile.json"
        direction_summary_md = output_dir / "direction_simulation_summary.md"
        direction_observation_json = output_dir / "direction_observation_log.json"
        motion_contract_md = output_dir / "motion_semantics_contract.md"
        pico_status_json = output_dir / "pico_readonly_status.json"
        pico_telemetry_json = output_dir / "pico_readonly_latest_telemetry.json"
        pico_summary_md = output_dir / "pico_readonly_evidence_summary.md"
        pico_ports_json = output_dir / "pico_readonly_port_inventory.json"
        pico_safety_md = output_dir / "pico_readonly_safety_boundary.md"
        pico_permission_json = output_dir / "pico_permission_diagnosis.json"
        pico_permission_acceptance_json = output_dir / "pico_rxonly_permission_acceptance.json"
        camera_host_inventory_json = output_dir / "camera_host_device_inventory.json"
        camera_device_inventory_json = output_dir / "camera_device_inventory.json"
        camera_tooling_json = output_dir / "camera_tooling_diagnosis.json"
        camera_permission_json = output_dir / "camera_device_permission_report.json"
        camera_host_blocker_md = output_dir / "camera_host_blocker_report.md"
        real_camera_status_json = output_dir / "real_camera_status.json"
        real_camera_capture_json = output_dir / "real_camera_capture_evidence.json"
        real_camera_frame_attempt_json = output_dir / "real_camera_frame_capture_attempt.json"
        real_camera_frame_acceptance_json = output_dir / "real_camera_frame_acceptance_result.json"
        usb_camera_capture_json = output_dir / "usb_camera_capture_evidence.json"
        usb_camera_acceptance_md = output_dir / "usb_camera_acceptance_summary.md"
        real_camera_acceptance_md = output_dir / "real_camera_acceptance_summary.md"
        sessions_json.write_text(self.sessions_json(), encoding="utf-8")
        summary_md.write_text(self.summary_markdown(runtime), encoding="utf-8")
        sample_jsonl.write_text(self.detection_events_jsonl(), encoding="utf-8")
        replay_md.write_text(self.replay_readiness_markdown(), encoding="utf-8")
        replay_summary_md.write_text(self.replay_summary_markdown(), encoding="utf-8")
        replay_latest_json.write_text(json.dumps(self.latest_replay.model_dump(mode="json"), indent=2), encoding="utf-8")
        annotation_candidates_json.write_text(self.annotation_candidates_json(), encoding="utf-8")
        annotation_review_summary_md.write_text(self.annotation_review_summary_markdown(), encoding="utf-8")
        dataset_health_summary_md.write_text(self.dataset_health_summary_markdown(), encoding="utf-8")
        if hasattr(runtime, "legacy_perception"):
            real_camera_summary_md.write_text(runtime.legacy_perception.evidence_summary_markdown(), encoding="utf-8")
            real_camera_latest_json.write_text(runtime.legacy_perception.latest_json(), encoding="utf-8")
            legacy_presets_json.write_text(runtime.legacy_perception.presets_json(), encoding="utf-8")
            legacy_migration_md.write_text(runtime.legacy_perception.migration_summary_markdown(), encoding="utf-8")
            summary = "Legacy perception evidence exported; files=4; no_physical_command_generated=true."
            self._event(
                "data_lab.legacy_perception_exported",
                {
                    "files": [
                        str(real_camera_summary_md),
                        str(real_camera_latest_json),
                        str(legacy_presets_json),
                        str(legacy_migration_md),
                    ],
                    "no_physical_command_generated": True,
                    "summary": summary,
                },
                summary,
            )
        if hasattr(runtime, "calibration"):
            direction_profile_json.write_text(runtime.calibration.direction_profile_json(), encoding="utf-8")
            direction_summary_md.write_text(runtime.calibration.direction_simulation_summary_markdown(), encoding="utf-8")
            direction_observation_json.write_text(runtime.calibration.direction_observation_log_json(), encoding="utf-8")
            motion_contract_md.write_text(runtime.calibration.motion_semantics_contract_markdown(), encoding="utf-8")
        if hasattr(runtime, "pico"):
            pico_status_json.write_text(runtime.pico.readonly_status_json(), encoding="utf-8")
            pico_telemetry_json.write_text(runtime.pico.readonly_latest_telemetry_json(), encoding="utf-8")
            pico_summary_md.write_text(runtime.pico.readonly_evidence_summary_markdown(), encoding="utf-8")
            pico_ports_json.write_text(runtime.pico.readonly_port_inventory_json(), encoding="utf-8")
            pico_safety_md.write_text(runtime.pico.readonly_safety_boundary_markdown(), encoding="utf-8")
            pico_permission_json.write_text(runtime.pico.readonly_permission_status_json(), encoding="utf-8")
            pico_permission_acceptance_json.write_text(runtime.pico.readonly_permission_acceptance_json(), encoding="utf-8")
        if hasattr(runtime, "camera_host"):
            camera_host_inventory_json.write_text(runtime.camera_host.inventory_json(), encoding="utf-8")
            camera_device_inventory_json.write_text(runtime.camera_host.inventory_json(), encoding="utf-8")
            camera_tooling_json.write_text(runtime.camera_host.diagnostic_commands_json(), encoding="utf-8")
            latest_camera = runtime.camera_host.latest()
            camera_permission_json.write_text(
                json.dumps(
                    {
                        "user_in_video_group": latest_camera.user_in_video_group,
                        "dev_video_entries": latest_camera.dev_video_entries,
                        "blocker_reason": latest_camera.blocker_reason,
                        "suggested_actions": latest_camera.suggested_actions,
                        "advisory_only": True,
                        "physical_command_enabled": False,
                        "no_physical_command_generated": True,
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            camera_host_blocker_md.write_text(runtime.camera_host.blocker_report_markdown(), encoding="utf-8")
            real_camera_status_json.write_text(runtime.legacy_perception.status(runtime.camera_runtime).model_dump_json(indent=2), encoding="utf-8")
            real_camera_capture_json.write_text(runtime.legacy_perception.latest_json(), encoding="utf-8")
            real_camera_frame_attempt_json.write_text(runtime.legacy_perception.latest_json(), encoding="utf-8")
            usb_camera_capture_json.write_text(runtime.legacy_perception.latest_json(), encoding="utf-8")
            usb_camera_acceptance_md.write_text(
                "# USB Camera Acceptance Summary\n\n"
                f"- Selected camera: {runtime.camera_host.latest().selected_camera_device or runtime.camera_host.latest().recommended_usb_device_path or 'not_selected'}\n"
                f"- Camera kind: external_usb_camera\n"
                f"- Frame captured: {runtime.legacy_perception.latest().status == 'recorded' and runtime.legacy_perception.latest().camera_device_path in {'/dev/video2', '/dev/video3'}}\n"
                "- Browser external observation: observed_by_operator\n"
                "- advisory_only=true\n"
                "- physical_command_enabled=false\n"
                "- no_physical_command_generated=true\n\n"
                "USB camera evidence is accepted only when backend capture records a real frame from the external USB camera path.\n",
                encoding="utf-8",
            )
            real_camera_frame_acceptance_json.write_text(
                json.dumps(
                    {
                        "camera_tooling_status": runtime.camera_host.latest().camera_acceptance_status,
                        "frame_captured": runtime.camera_host.latest().real_camera_frame_captured,
                        "blocker_reason": runtime.camera_host.latest().blocker_reason,
                        "latest_evidence": json.loads(runtime.legacy_perception.latest_json()),
                        "advisory_only": True,
                        "physical_command_enabled": False,
                        "no_physical_command_generated": True,
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            real_camera_acceptance_md.write_text(runtime.legacy_perception.evidence_summary_markdown(), encoding="utf-8")
        return [
            summary_md,
            sessions_json,
            sample_jsonl,
            replay_md,
            replay_summary_md,
            replay_latest_json,
            annotation_candidates_json,
            annotation_review_summary_md,
            dataset_health_summary_md,
            real_camera_summary_md,
            real_camera_latest_json,
            legacy_presets_json,
            legacy_migration_md,
            direction_profile_json,
            direction_summary_md,
            direction_observation_json,
            motion_contract_md,
            pico_status_json,
            pico_telemetry_json,
            pico_summary_md,
            pico_ports_json,
            pico_safety_md,
            pico_permission_json,
            pico_permission_acceptance_json,
            camera_host_inventory_json,
            camera_device_inventory_json,
            camera_tooling_json,
            camera_permission_json,
            camera_host_blocker_md,
            real_camera_status_json,
            real_camera_capture_json,
            real_camera_frame_attempt_json,
            real_camera_frame_acceptance_json,
            usb_camera_capture_json,
            usb_camera_acceptance_md,
            real_camera_acceptance_md,
        ]

    def sessions_json(self) -> str:
        payload = {
            "sessions": [session.model_dump(mode="json") for session in self.list_sessions()],
            "advisory_only": True,
            "no_physical_command_generated": True,
        }
        return json.dumps(payload, indent=2)

    def detection_events_sample(self, limit: int = 20) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for session in self.sessions.list_sessions():
            for item in self.sessions.jsonl_records(session.session_id, "detections.jsonl"):
                records.append({"session_id": session.session_id, **item})
        return records[-limit:]

    def detection_events_jsonl(self, limit: int = 20) -> str:
        records = self.detection_events_sample(limit)
        if not records:
            placeholder = {
                "event_type": "detection",
                "payload": {
                    "source": "not_recorded",
                    "advisory_only": True,
                    "no_physical_command_generated": True,
                },
                "no_physical_command_generated": True,
            }
            records = [placeholder]
        return "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in records)

    def replay_status(self) -> DataLabReplayResult:
        return self.latest_replay

    def run_replay(self, session_id: str | None = None) -> DataLabReplayResult:
        session = self._session_for_replay(session_id)
        if session is None:
            result = DataLabReplayResult(
                replay_id=f"data_lab_replay_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}",
                replay_status="no_session_available",
                warnings=["No Data Lab session evidence is available for replay."],
            )
            self.latest_replay = result
            return result
        detections = self.sessions.jsonl_records(session.session_id, "detections.jsonl")
        latest_payload = detections[-1].get("payload", detections[-1]) if detections else {}
        events_replayed = len(detections)
        detections_replayed = 0
        for item in detections:
            payload = item.get("payload", item)
            detections_replayed += len(payload.get("detections", [])) if isinstance(payload, dict) else 0
        result = DataLabReplayResult(
            replay_id=f"data_lab_replay_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}",
            source_session_id=session.session_id,
            frame_origin=str(latest_payload.get("frame_origin") or "not_available"),
            detector=str(latest_payload.get("detector_kind") or latest_payload.get("detector") or "not_available"),
            replay_status="completed" if events_replayed else "completed_no_detection_events",
            frames_replayed=max(session.stats.frame_count, events_replayed),
            events_replayed=events_replayed,
            detections_replayed=detections_replayed,
            advisory_only=True,
            no_physical_command_generated=True,
            replay_execution_not_physical=True,
        )
        self.latest_replay = result
        summary = (
            f"Data Lab replay completed; session={session.session_id}; "
            f"detections={detections_replayed}; no physical command generated."
        )
        self._event("data_lab.replay_completed", {**result.model_dump(mode="json"), "summary": summary}, summary)
        return result

    def annotation_candidates(self) -> list[DataLabAnnotationCandidate]:
        reviews = self._read_reviews()
        candidates: list[DataLabAnnotationCandidate] = []
        for session in self.sessions.list_sessions():
            for record in self.sessions.jsonl_records(session.session_id, "detections.jsonl"):
                payload = record.get("payload", record)
                if not isinstance(payload, dict):
                    continue
                for index, detection in enumerate(payload.get("detections", []) or []):
                    if not isinstance(detection, dict):
                        continue
                    class_name = str(detection.get("class_name") or detection.get("kind") or "unknown")
                    target_group = "balloon_target" if detection.get("is_balloon") or detection.get("kind") == "balloon_or_circle" else "body_target"
                    candidate_id = f"{session.session_id}:{payload.get('frame_id', 'frame')}:{index}"
                    review = reviews.get(candidate_id, {})
                    candidates.append(
                        DataLabAnnotationCandidate(
                            candidate_id=candidate_id,
                            session_id=session.session_id,
                            frame_id=payload.get("frame_id", "unknown"),
                            class_name=class_name,
                            target_group=target_group,
                            bbox=detection.get("bbox_xywh_pixel") or detection.get("bbox") or detection.get("bbox_xyxy_pixel"),
                            circle=detection.get("circle") or self._circle_from_detection(detection),
                            confidence=detection.get("confidence") or detection.get("score"),
                            source=str(payload.get("source") or "unknown"),
                            detector=str(payload.get("detector_kind") or "not_available"),
                            review_status=str(review.get("status") or "pending"),
                            reviewer_note=review.get("reviewer_note"),
                            advisory_only=True,
                            no_physical_command_generated=True,
                        )
                    )
        return candidates

    def review_annotation(self, request: DataLabAnnotationReviewRequest) -> DataLabAnnotationCandidate:
        candidates = {candidate.candidate_id: candidate for candidate in self.annotation_candidates()}
        if request.candidate_id not in candidates:
            raise KeyError(request.candidate_id)
        reviews = self._read_reviews()
        reviews[request.candidate_id] = {
            "status": request.status,
            "reviewer_note": request.reviewer_note,
            "updated_at": time.time(),
            "no_physical_command_generated": True,
        }
        self._write_reviews(reviews)
        reviewed = candidates[request.candidate_id].model_copy(update={"review_status": request.status, "reviewer_note": request.reviewer_note})
        summary = f"Annotation candidate reviewed; status={request.status}; no physical command generated."
        self._event("data_lab.annotation_reviewed", {**reviewed.model_dump(mode="json"), "summary": summary}, summary)
        return reviewed

    def dataset_health(self) -> DataLabDatasetHealth:
        sessions = self.list_sessions()
        detections = self.detection_events_sample(limit=10_000)
        candidates = self.annotation_candidates()
        class_distribution: dict[str, int] = {}
        source_distribution: dict[str, int] = {}
        for candidate in candidates:
            class_distribution[candidate.class_name] = class_distribution.get(candidate.class_name, 0) + 1
            source_key = self._source_distribution_key(candidate.source)
            source_distribution[source_key] = source_distribution.get(source_key, 0) + 1
        accepted = sum(1 for candidate in candidates if candidate.review_status == "accepted")
        rejected = sum(1 for candidate in candidates if candidate.review_status == "rejected")
        uncertain = sum(1 for candidate in candidates if candidate.review_status == "uncertain")
        real_count = source_distribution.get("real_capture", 0)
        result = DataLabDatasetHealth(
            sessions_count=len(sessions),
            detection_events_count=len(detections),
            annotation_candidates=len(candidates),
            accepted_annotations=accepted,
            rejected_annotations=rejected,
            uncertain_annotations=uncertain,
            class_distribution=class_distribution,
            source_distribution=source_distribution,
            dataset_ready_for_training=False,
            reason="only mock/surrogate evidence or insufficient real data" if real_count == 0 else "insufficient reviewed real data for training",
            advisory_only=True,
            no_physical_command_generated=True,
        )
        summary = "Dataset health checked; dataset_ready_for_training=false."
        self._event("data_lab.dataset_health_checked", {**result.model_dump(mode="json"), "summary": summary}, summary)
        return result

    def summary_markdown(self, runtime) -> str:
        status = self.status(runtime)
        latest = status.latest_detection
        latest_text = "No latest detection recorded."
        if latest:
            latest_text = (
                f"- Source: {latest.source}\n"
                f"- Camera source kind: {latest.camera_source_kind or 'not_available'}\n"
                f"- Frame origin: {latest.frame_origin or 'not_available'}\n"
                f"- Detector kind: {latest.detector_kind or 'not_available'}\n"
                f"- Body count: {latest.body_count}\n"
                f"- Balloon/circle count: {latest.balloon_count}\n"
                f"- Advisory only: {latest.advisory_only}\n"
                f"- No physical command generated: {latest.no_physical_command_generated}"
            )
        return f"""# Data Lab Summary

Data Lab records session-level evidence from mock/surrogate vision metadata, snapshots, detections and replay-ready session files. Outputs are advisory evidence only.

## Status

- Sessions count: {status.sessions_count}
- Latest session: {status.latest_session_id or 'none'}
- Replay status: {status.replay_status}
- Replay ready: {status.replay_ready}
- Advisory only: true
- No physical command generated: true

## Latest Detection Evidence

{latest_text}

## Safety Boundary

Data Lab session, export, replay-readiness and detection sample files do not enable hardware, do not arm the system and do not generate motor, servo, fire, GPIO, STEP/DIR/PWM or physical serial commands.
"""

    def replay_readiness_markdown(self) -> str:
        latest = self.latest_session()
        if latest is None:
            return """# Replay Readiness

- Status: replay_execution_not_implemented
- Reason: No session evidence has been recorded yet.
- Advisory only: true
- No physical command generated: true
"""
        ready = latest.stats.get("frame_count", 0) > 0 or latest.stats.get("detection_count", 0) > 0
        return f"""# Replay Readiness

- Status: {'replay_foundation_ready' if ready else 'replay_execution_not_implemented'}
- Latest session: {latest.session_id}
- Frames: {latest.stats.get('frame_count', 0)}
- Detections: {latest.stats.get('detection_count', 0)}
- Snapshots: {latest.stats.get('snapshot_count', 0)}
- Advisory only: true
- No physical command generated: true

Replay foundation readiness means previous session metadata and detection JSONL evidence can be loaded for UI review. Replay execution remains a separate implementation layer and does not authorize physical motion or fire.
"""

    def replay_summary_markdown(self) -> str:
        replay = self.latest_replay
        return f"""# Data Lab Replay Summary

- Replay ID: {replay.replay_id}
- Source session: {replay.source_session_id or 'none'}
- Status: {replay.replay_status}
- Frame origin: {replay.frame_origin}
- Detector: {replay.detector}
- Events replayed: {replay.events_replayed}
- Detections replayed: {replay.detections_replayed}
- Replay execution not physical: {replay.replay_execution_not_physical}
- Advisory only: true
- No physical command generated: true

Data Lab replay replays recorded detection metadata only. It does not require a live camera and cannot move hardware or request fire.
"""

    def annotation_candidates_json(self) -> str:
        return json.dumps(
            {
                "candidates": [candidate.model_dump(mode="json") for candidate in self.annotation_candidates()],
                "advisory_only": True,
                "no_physical_command_generated": True,
            },
            indent=2,
        )

    def annotation_review_summary_markdown(self) -> str:
        health = self.dataset_health()
        return f"""# Annotation Review Summary

- Candidates: {health.annotation_candidates}
- Accepted: {health.accepted_annotations}
- Rejected: {health.rejected_annotations}
- Uncertain: {health.uncertain_annotations}
- Advisory only: true
- No physical command generated: true

Annotation review in this phase is a dataset preparation foundation. Accept/reject/uncertain actions update data state only and do not generate physical commands.
"""

    def dataset_health_summary_markdown(self) -> str:
        health = self.dataset_health()
        return f"""# Dataset Health Summary

- Sessions count: {health.sessions_count}
- Detection events count: {health.detection_events_count}
- Annotation candidates: {health.annotation_candidates}
- Accepted annotations: {health.accepted_annotations}
- Rejected annotations: {health.rejected_annotations}
- Dataset ready for training: {health.dataset_ready_for_training}
- Reason: {health.reason}
- Advisory only: true
- No physical command generated: true

## Class Distribution

```json
{json.dumps(health.class_distribution, indent=2)}
```

## Source Distribution

```json
{json.dumps(health.source_distribution, indent=2)}
```
"""

    def _session_for_replay(self, session_id: str | None) -> SessionRecord | None:
        if session_id:
            try:
                return self.sessions.get_session(session_id)
            except KeyError:
                return None
        sessions = self.sessions.list_sessions()
        return sessions[0] if sessions else None

    def _active_or_new_session(self, runtime, event: VisionEvent) -> SessionRecord:
        if self.sessions.active_session_id:
            return self.sessions.get_session(self.sessions.active_session_id)
        scenario = SessionScenario(
            target_type="unknown",
            team="unknown",
            distance_m="custom",
            lane="unknown",
            angle="unknown",
            lighting="unknown",
            lens_profile=runtime.camera_runtime.profile.lens_profile,
            camera_resolution=f"{runtime.camera_runtime.status().actual_width}x{runtime.camera_runtime.status().actual_height}",
            yolo_imgsz=runtime.vision_runtime.profile.imgsz,
            active_model_ids=[],
            notes=f"Auto-created Data Lab evidence session from {event.source}.",
        )
        return self.sessions.start(
            StartSessionRequest(
                name="data_lab_evidence",
                operator="system",
                mode="mock" if event.camera_source_kind == "mock" else "capture",
                scenario=scenario,
            )
        )

    def _summary_for_session(self, session: SessionRecord) -> DataLabSessionSummary:
        detections = self.sessions.jsonl_records(session.session_id, "detections.jsonl")
        latest = None
        if detections:
            payload = detections[-1].get("payload", detections[-1])
            try:
                latest = DataLabDetectionRecord.model_validate(payload)
            except Exception:
                latest = DataLabDetectionRecord(
                    frame_id=payload.get("frame_id", "unknown") if isinstance(payload, dict) else "unknown",
                    source=payload.get("source", "legacy_detection") if isinstance(payload, dict) else "legacy_detection",
                    detections=[],
                    advisory_only=True,
                    no_physical_command_generated=True,
                )
        return DataLabSessionSummary(
            session_id=session.session_id,
            name=session.name,
            created_at=session.created_at,
            ended_at=session.ended_at,
            mode=session.mode,
            scenario=session.scenario.model_dump(mode="json"),
            stats=session.stats.model_dump(mode="json"),
            safety={
                **session.safety.model_dump(mode="json"),
                "advisory_only": True,
                "no_physical_command_generated": True,
            },
            quality=session.quality,
            latest_detection=latest,
            advisory_only=True,
            no_physical_command_generated=True,
        )

    def _detection_record(self, event: VisionEvent) -> DataLabDetectionRecord:
        detections: list[dict[str, Any]] = []
        for body in event.body_detections:
            detections.append({"kind": "body", **body.model_dump(mode="json")})
        for balloon in event.balloon_detections:
            detections.append({"kind": "balloon_or_circle", **balloon.model_dump(mode="json")})
        return DataLabDetectionRecord(
            frame_id=event.frame_id,
            source=event.source,
            camera_source_kind=event.camera_source_kind,
            frame_origin=event.frame_origin,
            detector_kind=event.detector_kind,
            body_count=len(event.body_detections),
            balloon_count=len(event.balloon_detections),
            detections=detections,
            latency_ms=event.total_ms or event.total_latency_ms,
            camera_fps=event.camera_fps,
            detector_fps=event.detector_fps or event.fps,
            advisory_only=True,
            no_physical_command_generated=True,
        )

    def _read_reviews(self) -> dict[str, dict[str, Any]]:
        if not self._review_path.exists():
            return {}
        try:
            data = json.loads(self._review_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
        return data if isinstance(data, dict) else {}

    def _write_reviews(self, reviews: dict[str, dict[str, Any]]) -> None:
        self._review_path.write_text(json.dumps(reviews, indent=2), encoding="utf-8")

    def _circle_from_detection(self, detection: dict[str, Any]) -> dict[str, Any] | None:
        center = detection.get("center")
        radius = detection.get("radius")
        if center is not None or radius is not None:
            return {"center": center, "radius": radius}
        return None

    def _source_distribution_key(self, source: str) -> str:
        lowered = source.lower()
        if "real" in lowered or "live_camera" in lowered:
            return "real_capture"
        if "surrogate" in lowered:
            return "surrogate"
        if "mock" in lowered:
            return "mock"
        return "unknown"

    def _event(self, event_type: str, payload: dict, message: str) -> None:
        self.last_event = (event_type, payload)
        self.logger.emit(LogLevel.INFO, "DATA_LAB", message, {"type": event_type, "summary": message, **payload})
