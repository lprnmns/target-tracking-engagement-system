import json
import time
import uuid

from app.schemas.demo import DemoReadiness, DemoTimeline, DemoTimelineEvent, DemoVerdict
from app.schemas.log import LogLevel
from app.schemas.report_export import ReportExportRequest
from app.services.log_service import JsonlLogService


class DemoTimelineService:
    def __init__(self, logger: JsonlLogService) -> None:
        self.logger = logger
        self.latest_timeline = DemoTimeline(run_id="not_run", status="not_run")
        self.latest_jury_rehearsal: dict = {}
        self.last_event: tuple[str, dict] | None = None

    def timeline(self, runtime) -> DemoTimeline:
        if self.latest_timeline.run_id == "not_run":
            self.latest_timeline = self._build_timeline(runtime, run_actions=False)
        return self.latest_timeline

    def run_jury_rehearsal(self, runtime) -> dict:
        timeline = self.run(runtime)
        cleanroom = runtime.release.latest_cleanroom_verification() if hasattr(runtime, "release") else None
        if cleanroom is None and hasattr(runtime, "release"):
            cleanroom = runtime.release.run_cleanroom_verification(runtime)
        report = runtime.report_export.generate_ktr_summary(runtime, ReportExportRequest(notes="Jury rehearsal package evidence."))
        verdict = {
            "release_demo_ready": timeline.verdict.release_demo_ready,
            "release_demo_blockers": timeline.verdict.release_demo_blockers,
            "release_demo_warnings": timeline.verdict.release_demo_warnings,
            "competition_ready": False,
            "competition_blockers": timeline.verdict.competition_blockers,
            "dataset_ready_for_training": timeline.verdict.dataset_ready_for_training,
            "dataset_blockers": timeline.verdict.dataset_blockers,
            "no_physical_command_generated": True,
        }
        payload = {
            "rehearsal_id": f"jury_rehearsal_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}",
            "created_at": time.time(),
            "timeline_id": timeline.run_id,
            "report_export_id": report.export_id,
            "cleanroom_run_id": getattr(cleanroom, "run_id", None),
            "cleanroom_verified": bool(cleanroom and cleanroom.smoke_status == "passed"),
            "latest_release_package": getattr(cleanroom, "package_id", None) or (runtime.release.latest_package().package_id if hasattr(runtime, "release") and runtime.release.latest_package() else None),
            "verdict": verdict,
            "safety_invariant": "DISARMED + NO_FIRE + dry_run=true + hardware_enabled=false + physical_command_enabled=false",
            "no_physical_command_generated": True,
        }
        self.latest_jury_rehearsal = payload
        self._event("demo.jury_rehearsal_completed", {**payload, "summary": "Jury rehearsal completed; no_physical_command_generated=true."}, "Jury rehearsal completed; no_physical_command_generated=true.")
        self._event("demo.jury_rehearsal_package_generated", {**payload, "summary": "Jury rehearsal package generated; no_physical_command_generated=true."}, "Jury rehearsal package generated; no_physical_command_generated=true.")
        return payload

    def latest_jury(self) -> dict:
        return self.latest_jury_rehearsal or {"status": "not_run", "no_physical_command_generated": True}

    def latest(self) -> DemoTimeline:
        return self.latest_timeline

    def readiness(self, runtime) -> DemoReadiness:
        timeline = self.timeline(runtime)
        readiness = DemoReadiness(
            release_demo_ready=timeline.verdict.release_demo_ready,
            release_demo_warnings=timeline.verdict.release_demo_warnings,
            release_demo_blockers=timeline.verdict.release_demo_blockers,
            competition_ready=timeline.verdict.competition_ready,
            competition_blockers=timeline.verdict.competition_blockers,
            dataset_ready_for_training=timeline.verdict.dataset_ready_for_training,
            dataset_blockers=timeline.verdict.dataset_blockers,
            no_physical_command_generated=True,
        )
        summary = (
            f"Demo readiness checked; release_demo_ready={readiness.release_demo_ready}; "
            f"release_blockers={len(readiness.release_demo_blockers)}; "
            f"competition_blockers={len(readiness.competition_blockers)}; "
            f"dataset_blockers={len(readiness.dataset_blockers)}; no_physical_command_generated=true."
        )
        self._event("demo.readiness_checked", {**readiness.model_dump(mode="json"), "summary": summary}, summary)
        return readiness

    def run(self, runtime) -> DemoTimeline:
        timeline = self._build_timeline(runtime, run_actions=True)
        self.latest_timeline = timeline
        report = runtime.report_export.generate_ktr_summary(runtime, ReportExportRequest(notes="End-to-end demo evidence timeline export."))
        event = self._event_item(
            "report_export",
            "Report export",
            "completed",
            "report",
            f"KTR/report summary export generated; export_id={report.export_id}; no physical command generated.",
            report.output_dir,
        )
        self.latest_timeline.events.append(event)
        self.latest_timeline.report_export_id = report.export_id
        self.latest_timeline.verdict = self._verdict(runtime)
        self.latest_timeline.status = "completed" if self.latest_timeline.verdict.release_demo_ready else "warning"
        generated_summary = f"Demo evidence timeline generated; steps={len(self.latest_timeline.events)}; no physical command generated."
        self._event("demo.timeline_generated", {**self.latest_timeline.model_dump(mode="json"), "summary": generated_summary}, generated_summary)
        completed_summary = (
            f"End-to-end demo run completed; release_demo_ready={self.latest_timeline.verdict.release_demo_ready}; "
            "competition_ready=false."
        )
        self._event("demo.run_completed", {**self.latest_timeline.model_dump(mode="json"), "summary": completed_summary}, completed_summary)
        self._event(
            "demo.evidence_index_generated",
            {"files": self.evidence_index_files(), "no_physical_command_generated": True},
            f"Demo evidence index generated; files={len(self.evidence_index_files())}; no_physical_command_generated=true.",
        )
        self._event(
            "demo.operator_script_generated",
            {"script": "demo_operator_script.md", "no_physical_command_generated": True},
            "Demo operator script generated; no_physical_command_generated=true.",
        )
        self._event(
            "demo.jury_package_generated",
            {"report_export_id": report.export_id, "no_physical_command_generated": True},
            f"Jury demo package generated; export_id={report.export_id}; no_physical_command_generated=true.",
        )
        return self.latest_timeline

    def known_limitations(self) -> list[str]:
        return [
            "Production YOLO modeli henüz yüklenmedi.",
            "Gerçek laptop/USB kamera kanıtı henüz alınmadı.",
            "Pico telemetry doğrulaması henüz yapılmadı.",
            "Self-test current state tamamlanmadan competition readiness geçmez.",
            "Mock/surrogate evidence yalnızca release/demo kanıtıdır, yarışma/prod kanıtı değildir.",
        ]

    def evidence_index_files(self) -> list[str]:
        return [
            "demo_timeline.md",
            "demo_timeline.json",
            "demo_readiness_summary.md",
            "demo_runbook.md",
            "jury_demo_summary.md",
            "release_demo_verdict.json",
            "evidence_index.md",
            "known_limitations.md",
            "demo_operator_script.md",
            "data_lab_summary.md",
            "data_lab_sessions.json",
            "replay_summary.md",
            "annotation_review_summary.md",
            "dataset_health_summary.md",
            "safety_summary.md",
            "launcher_inspection.md",
            "interface_inventory.md",
            "ktr_4_3_interfaces.md",
        ]

    def timeline_json(self, runtime) -> str:
        return json.dumps(self.timeline(runtime).model_dump(mode="json"), indent=2)

    def timeline_markdown(self, runtime) -> str:
        timeline = self.timeline(runtime)
        lines = [
            "# Demo Evidence Timeline",
            "",
            f"- Run ID: {timeline.run_id}",
            f"- Status: {timeline.status}",
            f"- Release demo ready: {timeline.verdict.release_demo_ready}",
            f"- Release demo blockers: {len(timeline.verdict.release_demo_blockers)}",
            f"- Release demo warnings: {len(timeline.verdict.release_demo_warnings)}",
            f"- Competition ready: {timeline.verdict.competition_ready}",
            f"- Competition blockers: {len(timeline.verdict.competition_blockers)}",
            f"- Dataset ready for training: {timeline.verdict.dataset_ready_for_training}",
            f"- Dataset blockers: {len(timeline.verdict.dataset_blockers)}",
            "- Advisory only: true",
            "- No physical command generated: true",
            "",
            "## Final Verdict",
            "",
            "### Release Demo Warnings",
            "",
            *(f"- {item}" for item in timeline.verdict.release_demo_warnings),
            *([] if timeline.verdict.release_demo_warnings else ["- none"]),
            "",
            "### Release Demo Blockers",
            "",
            *(f"- {item}" for item in timeline.verdict.release_demo_blockers),
            *([] if timeline.verdict.release_demo_blockers else ["- none"]),
            "",
            "### Competition Blockers",
            "",
            *(f"- {item}" for item in timeline.verdict.competition_blockers),
            *([] if timeline.verdict.competition_blockers else ["- none"]),
            "",
            "### Dataset Blockers",
            "",
            *(f"- {item}" for item in timeline.verdict.dataset_blockers),
            *([] if timeline.verdict.dataset_blockers else ["- none"]),
            "",
            "## Timeline",
            "",
        ]
        for item in timeline.events:
            lines.extend(
                [
                    f"### {item.title}",
                    "",
                    f"- Step: {item.step}",
                    f"- Status: {item.status}",
                    f"- Source: {item.source}",
                    f"- Summary: {item.summary}",
                    f"- Evidence: {item.evidence_ref or 'runtime metadata'}",
                    f"- No physical command generated: {item.no_physical_command_generated}",
                    "",
                ]
            )
        return "\n".join(lines)

    def readiness_markdown(self, runtime) -> str:
        readiness = self.readiness(runtime)
        return f"""# Demo Readiness Summary

- Release demo ready: {readiness.release_demo_ready}
- Release demo blockers: {len(readiness.release_demo_blockers)}
- Release demo warnings: {len(readiness.release_demo_warnings)}
- Competition ready: {readiness.competition_ready}
- Competition blockers: {len(readiness.competition_blockers)}
- Dataset ready for training: {readiness.dataset_ready_for_training}
- Dataset blockers: {len(readiness.dataset_blockers)}
- no_physical_command_generated: true

## Release Demo Warnings

{chr(10).join(f"- {item}" for item in readiness.release_demo_warnings) or "- none"}

## Release Demo Blockers

{chr(10).join(f"- {item}" for item in readiness.release_demo_blockers) or "- none"}

## Competition Blockers

{chr(10).join(f"- {item}" for item in readiness.competition_blockers) or "- none"}

## Dataset Blockers

{chr(10).join(f"- {item}" for item in readiness.dataset_blockers) or "- none"}

Mock/surrogate evidence is release/demo evidence only. Competition readiness requires production YOLO, real camera evidence, verified Pico telemetry and completed self-test.

## Legacy Log Format Note

Older demo.readiness_checked log samples may contain an old combined blockers field. Those entries are legacy readiness events and should be interpreted through newer split release demo, competition and dataset readiness fields when available.
"""

    def jury_demo_summary_markdown(self, runtime) -> str:
        timeline = self.timeline(runtime)
        latest_session = getattr(runtime.data_lab.latest_session(), "session_id", None) if hasattr(runtime, "data_lab") else None
        latest_replay = getattr(runtime.data_lab, "latest_replay", None) if hasattr(runtime, "data_lab") else None
        return f"""# Jury Demo Summary

This package is for release/demo evidence only. It does not enable physical commands.

- Demo status: {timeline.status}
- Release demo ready: {timeline.verdict.release_demo_ready}
- Competition ready: {timeline.verdict.competition_ready}
- Dataset ready for training: {timeline.verdict.dataset_ready_for_training}
- Safety invariant: DISARMED + NO_FIRE + dry_run=true + hardware_enabled=false + physical_command_enabled=false
- Latest demo timeline: {timeline.run_id}
- Latest report export: {timeline.report_export_id or "not generated"}
- Latest Data Lab session: {latest_session or "not available"}
- Latest replay: {getattr(latest_replay, "replay_id", None) or "not available"}
- Annotation review: foundation ready, data-state only
- no_physical_command_generated=true

## Why Competition Is Not Ready

{chr(10).join(f"- {item}" for item in timeline.verdict.competition_blockers) or "- none"}
"""

    def release_demo_verdict_json(self, runtime) -> str:
        timeline = self.timeline(runtime)
        payload = {
            "release_demo_ready": timeline.verdict.release_demo_ready,
            "release_demo_blockers": timeline.verdict.release_demo_blockers,
            "release_demo_warnings": timeline.verdict.release_demo_warnings,
            "competition_ready": timeline.verdict.competition_ready,
            "competition_blockers": timeline.verdict.competition_blockers,
            "dataset_ready_for_training": timeline.verdict.dataset_ready_for_training,
            "dataset_blockers": timeline.verdict.dataset_blockers,
            "no_physical_command_generated": True,
            "safety_invariant": "DISARMED + NO_FIRE + dry_run=true + hardware_enabled=false + physical_command_enabled=false",
        }
        return json.dumps(payload, indent=2)

    def evidence_index_markdown(self, runtime) -> str:
        return "# Evidence Index\n\n" + "\n".join(f"- `{item}`" for item in self.evidence_index_files()) + "\n\n- no_physical_command_generated=true\n"

    def known_limitations_markdown(self, runtime) -> str:
        return "# Known Limitations\n\n" + "\n".join(f"- {item}" for item in self.known_limitations()) + "\n\n- no_physical_command_generated=true\n"

    def operator_script_markdown(self, runtime) -> str:
        return """# Demo Operator Script

1. Dashboard açılır.
   Expected: safety invariant and no physical command badges are visible.
2. Safety invariant gösterilir.
   Expected: DISARMED, NO_FIRE, dry_run=true, hardware_enabled=false, physical_command_enabled=false.
3. Vision mock/surrogate gösterilir.
   Expected: advisory metadata only; production YOLO is not claimed.
4. Data Lab session kaydı gösterilir.
   Expected: session evidence exists and no physical command is generated.
5. Replay gösterilir.
   Expected: recorded metadata replay runs without hardware movement.
6. Annotation review gösterilir.
   Expected: candidates are data-state only.
7. Dataset health gösterilir.
   Expected: dataset_ready_for_training=false until sufficient real data exists.
8. Demo readiness split semantics gösterilir.
   Expected: release demo readiness, competition blockers and dataset blockers are separate.
9. Reports/KTR export gösterilir.
   Expected: evidence index and KTR interface files are listed.
10. Sonuç açıklanır.
    Expected: release demo ready may pass; competition not ready because production YOLO, Pico telemetry, real camera evidence and completed self-test are missing.

no_physical_command_generated=true
"""

    def jury_rehearsal_summary_markdown(self, runtime) -> str:
        jury = self.latest_jury()
        verdict = jury.get("verdict", {})
        return f"""# Jury Rehearsal Summary

- Rehearsal ID: {jury.get("rehearsal_id", "not_run")}
- Timeline ID: {jury.get("timeline_id", "not_run")}
- Report export: {jury.get("report_export_id", "not_generated")}
- Clean-room verified: {jury.get("cleanroom_verified", False)}
- Release demo ready: {verdict.get("release_demo_ready", False)}
- Competition ready: {verdict.get("competition_ready", False)}
- Dataset ready for training: {verdict.get("dataset_ready_for_training", False)}
- no_physical_command_generated=true
"""

    def jury_rehearsal_verdict_json(self, runtime) -> str:
        return json.dumps(self.latest_jury(), indent=2)

    def jury_rehearsal_timeline_markdown(self, runtime) -> str:
        return "# Jury Rehearsal Timeline\n\n" + self.timeline_markdown(runtime)

    def jury_rehearsal_operator_script_markdown(self, runtime) -> str:
        return """# Jury Rehearsal Operator Script

## 60-second summary

ISTIKLAL C2 Console is shown in safe demo/release mode. The system displays safety lock, demo evidence timeline, Data Lab evidence, clean-room release verification and KTR exports. It is not competition-ready until production YOLO, real camera evidence, Pico telemetry and self-test are completed.

## 3-minute technical demo

1. Open Dashboard and show safety invariant.
2. Open Jury Demo Center and run full demo evidence.
3. Show Vision mock/surrogate evidence as non-production.
4. Show Data Lab session, replay, annotation review and dataset health.
5. Show clean-room release verification.
6. Export KTR/report package.
7. Confirm no_physical_command_generated=true.

## Jury Q/A

- Is this competition-ready? No. It is release/demo ready only.
- Is production YOLO loaded? No. Fixture/test adapter evidence is not production.
- Is real camera evidence available? No, mock/surrogate evidence is demo evidence.
- Is Pico verified? No, Pico telemetry verification is pending.
- Does it generate physical commands? No. no_physical_command_generated=true.
- What does this package prove? Portable demo/evidence, UI, reports, Data Lab and safety boundaries.
"""

    def jury_rehearsal_limitations_markdown(self, runtime) -> str:
        return "# Jury Rehearsal Limitations\n\n" + "\n".join(f"- {item}" for item in self.known_limitations()) + "\n\n- no_physical_command_generated=true\n"

    def runbook_markdown(self, runtime) -> str:
        return """# Demo Runbook

- Start backend/frontend.
- Check safety lock: DISARMED, NO_FIRE, dry_run=true, hardware_enabled=false, physical_command_enabled=false.
- Run first-run acceptance or inspect current first-run state.
- Start or inspect vision mock/surrogate metadata.
- Record Data Lab evidence.
- Run Data Lab replay from the latest session.
- Review annotation candidates and dataset health.
- Generate KTR summary/report export.
- Confirm no physical command generated in timeline, logs and reports.
"""

    def _build_timeline(self, runtime, run_actions: bool) -> DemoTimeline:
        run_id = f"demo_timeline_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
        events: list[DemoTimelineEvent] = []
        state = runtime.system_state()
        safe = state.mode == "DISARMED" and state.fire_policy == "NO_FIRE" and state.dry_run and not state.hardware_enabled
        physical_disabled = not runtime.config.hardware.physical_command_enabled
        events.append(
            self._event_item(
                "safety_lock",
                "System safety lock",
                "completed" if safe and physical_disabled else "blocked",
                "safety",
                f"Safety invariant snapshot: mode={state.mode}, fire_policy={state.fire_policy}, dry_run={state.dry_run}, hardware_enabled={state.hardware_enabled}, physical_command_enabled={runtime.config.hardware.physical_command_enabled}.",
                "/api/system/state",
            )
        )
        first_run = runtime.first_run.status(runtime)
        events.append(
            self._event_item(
                "first_run",
                "First Run / profile evaluation",
                "completed" if first_run.current_first_run_status == "passed" else "warning",
                "first_run",
                f"First Run={first_run.current_first_run_status}; profile={first_run.current_profile_id}; profile_eval={first_run.current_profile_evaluation_status}.",
                "/api/first-run/status",
            )
        )
        vision = runtime.vision_pipeline.latest()
        detections = len(vision.body_detections) + len(vision.balloon_detections)
        events.append(
            self._event_item(
                "vision_evidence",
                "Vision evidence",
                "completed" if detections > 0 else "warning",
                "vision",
                f"Vision source={vision.source}; detector={vision.detector_kind or 'not_available'}; detections={detections}; advisory only.",
                "/api/vision/status",
            )
        )
        if run_actions or runtime.data_lab.latest_session() is None:
            record = runtime.data_lab.record_latest_detection(runtime)
            session_id = record.session.session_id
        else:
            latest = runtime.data_lab.latest_session()
            session_id = latest.session_id if latest else None
        events.append(
            self._event_item(
                "data_lab_session",
                "Data Lab session",
                "completed" if session_id else "warning",
                "data_lab",
                f"Data Lab session evidence available; session={session_id or 'none'}; no physical command generated.",
                f"/api/data-lab/sessions/{session_id}" if session_id else "/api/data-lab/sessions/latest",
            )
        )
        replay = runtime.data_lab.run_replay(session_id) if run_actions else runtime.data_lab.replay_status()
        events.append(
            self._event_item(
                "replay",
                "Replay",
                "completed" if replay.replay_status.startswith("completed") else "warning",
                "data_lab",
                f"Replay status={replay.replay_status}; events={replay.events_replayed}; detections={replay.detections_replayed}; not physical.",
                "/api/data-lab/replay/latest",
            )
        )
        candidates = runtime.data_lab.annotation_candidates()
        events.append(
            self._event_item(
                "annotation_review",
                "Annotation review",
                "completed" if candidates else "warning",
                "data_lab",
                f"Annotation candidates={len(candidates)}; accepted={sum(1 for c in candidates if c.review_status == 'accepted')}; data-state only.",
                "/api/data-lab/annotations/candidates",
            )
        )
        health = runtime.data_lab.dataset_health()
        events.append(
            self._event_item(
                "dataset_health",
                "Dataset health",
                "warning",
                "data_lab",
                f"Dataset ready for training={health.dataset_ready_for_training}; reason={health.reason}.",
                "/api/data-lab/dataset-health",
            )
        )
        timeline = DemoTimeline(run_id=run_id, status="completed", events=events, verdict=self._verdict(runtime))
        summary = f"Demo evidence timeline generated; steps={len(events)}; no physical command generated."
        self._event("demo.timeline_generated", {**timeline.model_dump(mode="json"), "summary": summary}, summary)
        return timeline

    def _verdict(self, runtime) -> DemoVerdict:
        first = runtime.first_run.status(runtime)
        health = runtime.data_lab.dataset_health()
        production = runtime.model_packages.active_package_summary().get("production_ready", False)
        hardware = runtime.hardware.status(mock_pico_active=runtime.pico.status().mock_mode)
        release_warnings: list[str] = []
        release_blockers: list[str] = []
        competition_blockers: list[str] = []
        dataset_blockers: list[str] = []
        state = runtime.system_state()
        safety_ok = state.mode == "DISARMED" and state.fire_policy == "NO_FIRE" and state.dry_run and not state.hardware_enabled and not runtime.config.hardware.physical_command_enabled
        if not safety_ok:
            release_blockers.append("Safety invariant is not satisfied.")
        if first.current_first_run_status != "passed":
            release_warnings.append("First-run current status is not passed; demo can run but acceptance should be completed before presentation.")
        if not production:
            competition_blockers.append("Competition rehearsal requires production YOLO model.")
        if not hardware.pico_verified:
            competition_blockers.append("Competition rehearsal requires verified Pico telemetry.")
        if runtime.camera_runtime.profile.source_type == "mock":
            release_warnings.append("Mock/surrogate evidence is acceptable for release demo but not competition rehearsal.")
            competition_blockers.append("Competition rehearsal requires real camera evidence.")
        if runtime.self_test.latest_run is None:
            competition_blockers.append("Competition rehearsal requires completed self-test.")
        if not health.dataset_ready_for_training:
            dataset_blockers.append("Dataset is not ready for training; mock/surrogate or insufficient real data.")
        reasons = [*release_warnings, *release_blockers, *competition_blockers, *dataset_blockers]
        return DemoVerdict(
            release_demo_ready=not release_blockers,
            release_demo_warnings=release_warnings,
            release_demo_blockers=release_blockers,
            competition_ready=not competition_blockers,
            competition_blockers=competition_blockers,
            dataset_ready_for_training=health.dataset_ready_for_training,
            dataset_blockers=dataset_blockers,
            reasons=reasons,
            advisory_only=True,
            no_physical_command_generated=True,
        )

    def _event_item(self, step: str, title: str, status: str, source: str, summary: str, evidence_ref: str | None) -> DemoTimelineEvent:
        return DemoTimelineEvent(
            event_id=f"{step}-{uuid.uuid4().hex[:8]}",
            step=step,
            title=title,
            status=status,  # type: ignore[arg-type]
            source=source,  # type: ignore[arg-type]
            summary=summary,
            evidence_ref=evidence_ref,
            advisory_only=True,
            no_physical_command_generated=True,
        )

    def _event(self, event_type: str, payload: dict, message: str) -> None:
        self.last_event = (event_type, payload)
        self.logger.emit(LogLevel.INFO, "DEMO", message, {"type": event_type, "summary": message, **payload})
