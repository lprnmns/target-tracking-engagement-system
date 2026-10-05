import json
import shutil
import time
import uuid
from pathlib import Path

from app.schemas.first_run import FirstRunActionResult, FirstRunReport, FirstRunStatus, FirstRunStep
from app.schemas.log import LogLevel
from app.services.log_service import JsonlLogService
from app.services.storage_paths import project_root


class FirstRunService:
    def __init__(self, logger: JsonlLogService) -> None:
        self.logger = logger
        self.completed = False
        self.latest_report: FirstRunReport | None = None
        self.current_profile_id = "release_candidate_ready"
        self.last_successful_first_run: dict | None = None
        self.report_dir = project_root() / "exports" / "first_run"
        self.report_dir.mkdir(parents=True, exist_ok=True)
        self.last_event: tuple[str, dict] | None = None

    def status(self, runtime) -> FirstRunStatus:
        current_profile_status = self._current_profile_status()
        current_first_run_status = self._current_first_run_status(current_profile_status)
        return FirstRunStatus(
            completed=self.completed,
            latest_report=self.latest_report,
            mode=runtime.config.runtime_mode.mode,
            checks_count=len(self.latest_report.steps) if self.latest_report else 0,
            current_first_run_status=current_first_run_status,
            current_profile_id=self.current_profile_id,
            current_profile_evaluation_status=current_profile_status,
            last_successful_first_run=self.last_successful_first_run,
            stale_evidence=self.last_successful_first_run is not None and current_first_run_status == "open",
        )

    def check(self, runtime) -> FirstRunReport:
        steps = [
            self._backend_reachable(runtime),
            self._frontend_static(runtime),
            self._config_loaded(runtime),
            self._writable("writable_logs", "Writable logs", project_root() / "logs"),
            self._writable("writable_exports", "Writable exports", project_root() / "exports"),
            self._device_manager(runtime),
            self._camera_selected(runtime),
            self._pico_absent_safe(runtime),
            self._model_registry(runtime),
            self._self_test_runnable(runtime),
            self._interface_inventory(runtime),
            self._launcher_files(),
            self._release_readiness(runtime),
            self._no_physical_invariant(runtime),
        ]
        failed = [step for step in steps if step.status == "failed"]
        warnings = [step for step in steps if step.status == "warning"]
        overall = "failed" if failed else ("warning" if warnings else "passed")
        report = FirstRunReport(
            run_id=f"first-run-{uuid.uuid4().hex[:10]}",
            mode=runtime.config.runtime_mode.mode,
            completed=overall != "failed",
            overall_status=overall,
            steps=steps,
            summary={
                "passed": sum(1 for step in steps if step.status == "passed"),
                "warning": len(warnings),
                "failed": len(failed),
                "blocking": sum(1 for step in steps if step.blocking),
                "no_physical_command_generated": True,
            },
        )
        profile_checklists = self._profile_checklists(runtime)
        report = report.model_copy(
            update={
                "profile_checklists": profile_checklists,
                "profile_statuses": {
                    profile: self._profile_status(profile, checks)
                    for profile, checks in profile_checklists.items()
                },
            }
        )
        report = self._write_report(report)
        self.latest_report = report
        if report.profile_statuses.get(self.current_profile_id) == "passed":
            self.last_successful_first_run = {
                "run_id": report.run_id,
                "profile_id": self.current_profile_id,
                "timestamp": report.created_at,
                "checks_count": len(report.steps),
            }
        self._event("first_run.checked", report.model_dump(mode="json"), "First-run acceptance checks completed")
        return report

    def mark_complete(self, runtime) -> FirstRunActionResult:
        report = self.latest_report or self.check(runtime)
        can_complete = report.overall_status != "failed"
        self.completed = can_complete
        if can_complete:
            self.last_successful_first_run = {
                "run_id": report.run_id,
                "profile_id": self.current_profile_id,
                "timestamp": report.created_at,
                "checks_count": len(report.steps),
            }
        return FirstRunActionResult(
            accepted=can_complete,
            reason="First-run marked complete." if can_complete else "First-run has blocking failures.",
            status=self.status(runtime),
        )

    def reset(self, runtime) -> FirstRunActionResult:
        self.completed = False
        self.latest_report = None
        return FirstRunActionResult(accepted=True, reason="First-run state reset.", status=self.status(runtime))

    def report(self, runtime) -> FirstRunReport:
        return self.latest_report or self.check(runtime)

    def _step(
        self,
        step_id: str,
        title: str,
        status: str,
        explanation: str,
        detail: dict | None = None,
        suggested_fix: str | None = None,
        blocking: bool = False,
    ) -> FirstRunStep:
        return FirstRunStep(
            step_id=step_id,
            title=title,
            status=status,  # type: ignore[arg-type]
            explanation=explanation,
            suggested_fix=suggested_fix,
            blocking=blocking,
            detail=detail or {},
        )

    def _backend_reachable(self, runtime) -> FirstRunStep:
        return self._step("backend_reachable", "Backend reachable", "passed", "FastAPI runtime is active.", runtime.system_state().model_dump(mode="json"))

    def _frontend_static(self, runtime) -> FirstRunStep:
        dist = project_root() / "frontend" / "dist" / "index.html"
        if dist.exists():
            return self._step("frontend_static", "Frontend static available", "passed", "Portable frontend build is present.", {"path": str(dist)})
        return self._step(
            "frontend_static",
            "Frontend static available",
            "warning",
            "frontend/dist is not present; development server may still be used.",
            {"path": str(dist)},
            "Run pnpm build before creating a portable ZIP.",
        )

    def _config_loaded(self, runtime) -> FirstRunStep:
        return self._step("config_loaded", "Config loaded", "passed", "Config loaded and validated.", runtime.config.system.model_dump(mode="json"))

    def _writable(self, step_id: str, title: str, path: Path) -> FirstRunStep:
        try:
            path.mkdir(parents=True, exist_ok=True)
            probe = path / ".first_run_write_probe"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink(missing_ok=True)
            return self._step(step_id, title, "passed", f"{path} is writable.", {"path": str(path)})
        except OSError as exc:
            return self._step(step_id, title, "failed", f"{path} is not writable.", {"error": str(exc)}, "Fix folder permissions.", True)

    def _device_manager(self, runtime) -> FirstRunStep:
        inventory = runtime.device_manager.inventory()
        return self._step("device_manager", "Device manager works", "passed", "Device inventory service responded.", {"devices": len(inventory.devices)})

    def _camera_selected(self, runtime) -> FirstRunStep:
        status = runtime.camera_runtime.status()
        if status.profile.source_type == "mock" or status.selected_camera:
            return self._step("camera_selected", "Camera selected or mock selected", "passed", "Camera runtime has a safe source.", status.model_dump(mode="json"))
        return self._step("camera_selected", "Camera selected or mock selected", "warning", "No camera selected.", status.model_dump(mode="json"), "Select mock or a physical camera.")

    def _pico_absent_safe(self, runtime) -> FirstRunStep:
        hardware = runtime.hardware.status(mock_pico_active=runtime.pico.status().mock_mode)
        if hardware.physical_command_enabled:
            return self._step("pico_absent_safe", "Pico absent represented safely", "failed", "Physical commands are enabled.", hardware.model_dump(mode="json"), "Disable hardware flags.", True)
        return self._step("pico_absent_safe", "Pico absent represented safely", "passed", "Pico discovery state is safe.", hardware.model_dump(mode="json"))

    def _model_registry(self, runtime) -> FirstRunStep:
        models = runtime.model_registry.list_models()
        active = runtime.model_registry.active_models()
        packages = runtime.model_packages.list_packages() if hasattr(runtime, "model_packages") else []
        package_summary = runtime.model_packages.active_package_summary() if hasattr(runtime, "model_packages") else {}
        status = "passed" if active.active_test_adapter or models or packages else "warning"
        return self._step(
            "model_registry",
            "Model registry available",
            status,
            "Model registry and package handoff service are reachable.",
            {"models": len(models), "packages": len(packages), "active": active.model_dump(mode="json"), "active_package": package_summary},
            None if status == "passed" else "Select OpenCV test adapter or load a vision team model.",
        )

    def _profile_checklists(self, runtime) -> dict:
        status = runtime.hardware.status(mock_pico_active=runtime.pico.status().mock_mode)
        active = runtime.model_registry.active_models()
        package_summary = runtime.model_packages.active_package_summary() if hasattr(runtime, "model_packages") else {}
        model_loaded = bool(active.active_body_model_id or active.active_balloon_model_id or active.active_combined_model_id)
        production_model_verified = bool(package_summary.get("production_ready") and package_summary.get("class_mapping_status") == "complete" and package_summary.get("last_test_status") == "completed")
        test_adapter = bool(active.active_test_adapter) or runtime.vision_runtime.profile.inference_adapter == "opencv_circle_test"
        base_safe = self._no_physical_invariant(runtime)
        frontend = self._frontend_static(runtime)
        device = self._device_manager(runtime)
        camera = self._camera_selected(runtime)
        competition_camera = self._competition_camera_verified(runtime)
        self_test_completed = self._self_test_completed(runtime)
        interfaces = self._interface_inventory(runtime)
        release = self._launcher_files()
        release_readiness = self._release_readiness(runtime)
        release_manifest = self._release_manifest(runtime)
        model_step = self._step(
            "production_model_loaded",
            "Production YOLO model loaded",
            "passed" if production_model_verified else "warning",
            "Production model is loaded, mapped and tested." if production_model_verified else "Production YOLO model is not loaded or not fully verified; test adapter may be used for demo only.",
            {"model_loaded": model_loaded, "production_model_verified": production_model_verified, "test_adapter": test_adapter, "active": active.model_dump(mode="json"), "active_package": package_summary},
            "Load the vision team YOLO/ONNX model before competition rehearsal." if not model_loaded else None,
            blocking=not production_model_verified,
        )
        model_registry_step = self._model_registry(runtime)
        model_explicit_step = self._step(
            "model_or_test_adapter_explicit",
            "Model or test adapter state explicit",
            "passed" if test_adapter or model_loaded else "warning",
            "Model state is explicit for release candidate.",
            {"test_adapter": test_adapter, "model_loaded": model_loaded, "active_package": package_summary},
            "Import a vision team model package or keep the OpenCV test adapter visibly selected.",
        )
        release_model_not_required_step = self._step(
            "production_model_not_required_for_release_candidate",
            "Production model not required for release candidate",
            "passed",
            "Release candidate can run without production YOLO; fixture/test adapter must remain explicitly marked as non-competition.",
            {"production_model_verified": production_model_verified, "test_adapter": test_adapter, "active_package": package_summary},
        )
        competition_model_required_step = self._step(
            "production_model_required_for_competition_rehearsal",
            "Production model required for competition rehearsal",
            "passed" if production_model_verified else "warning",
            "Competition rehearsal requires production YOLO model.",
            {"production_model_verified": production_model_verified, "active_package": package_summary},
            "Load, validate and dry-run test the production model package before competition rehearsal." if not production_model_verified else None,
            blocking=not production_model_verified,
        )
        ktr_export_step = self._interface_inventory(runtime)
        pico_step = self._step(
            "pico_telemetry_verified",
            "Pico telemetry verified",
            "passed" if status.pico_verified else "warning",
            "Pico telemetry-only firmware is verified." if status.pico_verified else "Pico is absent or not verified from telemetry.",
            status.model_dump(mode="json"),
            "Connect Pico 2 with telemetry-only firmware for hardware telemetry readiness." if not status.pico_verified else None,
            blocking=not status.pico_verified,
        )
        return {
            "development_ready": [base_safe, device, camera],
            "demo_ready": [base_safe, frontend, camera, self._model_registry(runtime)],
            "field_dry_run_ready": [base_safe, frontend, device, camera, release, interfaces, model_step],
            "hardware_telemetry_ready": [base_safe, device, pico_step],
            "competition_rehearsal_ready": [base_safe, frontend, device, competition_camera, self_test_completed, release, interfaces, pico_step, model_step, competition_model_required_step],
            "release_candidate_ready": [base_safe, frontend, device, camera, release, release_readiness, release_manifest, model_registry_step, model_explicit_step, release_model_not_required_step, ktr_export_step],
        }

    def _profile_status(self, profile: str, steps: list[FirstRunStep]) -> str:
        if any(step.status == "failed" for step in steps):
            return "failed"
        if any(step.blocking and step.status in {"warning", "failed"} for step in steps):
            return "blocked"
        if profile in {"hardware_telemetry_ready", "competition_rehearsal_ready"} and any(step.step_id == "pico_telemetry_verified" and step.status != "passed" for step in steps):
            return "blocked" if profile == "hardware_telemetry_ready" else "warning"
        if profile == "competition_rehearsal_ready" and any(step.step_id == "production_model_loaded" and step.status != "passed" for step in steps):
            return "blocked"
        if profile == "release_candidate_ready" and any(step.step_id in {"pico_telemetry_verified", "production_model_loaded"} and step.status != "passed" for step in steps):
            return "warning"
        if any(step.status == "warning" for step in steps):
            return "warning"
        return "passed"

    def _current_profile_status(self) -> str:
        if self.latest_report is None:
            return "not_evaluated"
        return self.latest_report.profile_statuses.get(self.current_profile_id, "not_evaluated")

    def _current_first_run_status(self, profile_status: str) -> str:
        if self.latest_report is None:
            return "open"
        if profile_status == "passed":
            return "passed"
        if profile_status == "warning":
            return "warning"
        if profile_status in {"failed", "blocked"} or self.latest_report.overall_status == "failed":
            return "failed"
        return "open"

    def _self_test_runnable(self, runtime) -> FirstRunStep:
        status = runtime.self_test.status()
        return self._step("self_test_runnable", "Self-test runnable", "passed", "Self-test service is available.", status.model_dump(mode="json"))

    def _self_test_completed(self, runtime) -> FirstRunStep:
        latest = runtime.self_test.latest_run
        if latest and latest.status in {"passed", "warning"}:
            return self._step("self_test_completed", "Self-test completed", "passed", "Self-test has been run for rehearsal readiness.", latest.model_dump(mode="json"))
        return self._step(
            "self_test_completed",
            "Self-test completed",
            "warning",
            "Self-test has not been run; competition rehearsal readiness is blocked.",
            {"latest_run": latest.model_dump(mode="json") if latest else None},
            "Run Self-Test before competition rehearsal.",
            True,
        )

    def _competition_camera_verified(self, runtime) -> FirstRunStep:
        status = runtime.camera_runtime.status()
        real_camera = status.profile.source_type in {"laptop", "usb"} and status.selected_camera != "mock"
        probe_ok = bool(status.last_probe_result and status.last_probe_result.get("status") in {"passed", "ok"})
        if real_camera and probe_ok:
            return self._step("competition_camera_verified", "Real camera probe verified", "passed", "Real camera source has a valid probe.", status.model_dump(mode="json"))
        return self._step(
            "competition_camera_verified",
            "Real camera probe verified",
            "warning",
            "Mock/no-camera source is acceptable for release candidate but blocks competition rehearsal.",
            status.model_dump(mode="json"),
            "Select and probe the real field camera before competition rehearsal.",
            True,
        )

    def _interface_inventory(self, runtime) -> FirstRunStep:
        inventory = runtime.interface_inventory.inventory()
        if len(inventory.interfaces) < 10:
            return self._step("interface_inventory", "Interface inventory available", "warning", "Interface inventory is sparse.", {"interfaces": len(inventory.interfaces)})
        return self._step("interface_inventory", "Interface inventory available", "passed", "KTR interface inventory is populated.", {"interfaces": len(inventory.interfaces)})

    def _launcher_files(self) -> FirstRunStep:
        root = project_root()
        files = [
            root / "start_linux.sh",
            root / "start_windows.bat",
            root / "release" / "linux" / "start_istiklal_c2.sh",
            root / "release" / "windows" / "start_istiklal_c2.bat",
        ]
        missing = [str(path) for path in files if not path.exists()]
        uv_path = shutil.which("uv")
        if missing:
            return self._step("portable_launcher", "Portable launcher files", "failed", "Launcher files are missing.", {"missing": missing, "uv": uv_path}, "Restore release launcher scripts.", True)
        return self._step("portable_launcher", "Portable launcher files", "passed", "Portable launcher scripts are present.", {"files": [str(path) for path in files], "uv": uv_path})

    def _release_readiness(self, runtime) -> FirstRunStep:
        status = runtime.release.preflight(runtime)
        if status.status == "passed":
            return self._step("release_readiness", "Release candidate readiness", "passed", "Release candidate can run without hardware.", status.model_dump(mode="json"))
        return self._step(
            "release_readiness",
            "Release candidate readiness",
            "warning" if status.status != "failed" else "failed",
            "Release candidate can run without hardware, but some packaging checks need attention.",
            status.model_dump(mode="json"),
            "Inspect /api/release/preflight and run scripts/check_release.py.",
            status.status == "failed",
        )

    def _release_manifest(self, runtime) -> FirstRunStep:
        path = runtime.release.ensure_manifest(runtime)
        return self._step("release_manifest", "Release launcher manifest available", "passed", "Release manifest generated for portable ZIP evidence.", {"path": path})

    def _no_physical_invariant(self, runtime) -> FirstRunStep:
        config = runtime.config
        ok = (
            config.system.mode == "DISARMED"
            and config.system.default_fire_policy == "NO_FIRE"
            and config.system.dry_run
            and not config.system.hardware_enabled
            and not config.hardware.physical_command_enabled
        )
        detail = {"system": config.system.model_dump(mode="json"), "hardware": config.hardware.model_dump(mode="json")}
        if ok:
            return self._step("no_physical_invariant", "No physical command invariant", "passed", "Safety invariant is intact.", detail)
        return self._step("no_physical_invariant", "No physical command invariant", "failed", "Safety invariant is broken.", detail, "Restore DISARMED/NO_FIRE/dry-run/hardware-disabled config.", True)

    def _write_report(self, report: FirstRunReport) -> FirstRunReport:
        path = self.report_dir / f"{report.run_id}.json"
        md_path = self.report_dir / f"{report.run_id}.md"
        path.write_text(json.dumps(report.model_dump(mode="json"), indent=2), encoding="utf-8")
        md_path.write_text(self._markdown(report), encoding="utf-8")
        return report.model_copy(update={"report_path": str(md_path)})

    def _markdown(self, report: FirstRunReport) -> str:
        lines = [
            f"# First Run Report {report.run_id}",
            "",
            f"- Mode: {report.mode}",
            f"- Overall status: {report.overall_status}",
            f"- Completed: {report.completed}",
            f"- No physical command generated: {report.no_physical_command_generated}",
            "",
            "## Steps",
            "",
        ]
        for step in report.steps:
            lines.append(f"- [{step.status}] {step.title}: {step.explanation}")
            if step.suggested_fix:
                lines.append(f"  Suggested fix: {step.suggested_fix}")
        lines.append("")
        lines.append("First-run acceptance does not enable physical fire or motion.")
        return "\n".join(lines)

    def _event(self, event_type: str, payload: dict, message: str) -> None:
        self.last_event = (event_type, payload)
        self.logger.emit(LogLevel.INFO, "FIRST_RUN", message, payload)
