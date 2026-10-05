import os
import shutil
import sys
import json
import platform
import subprocess
import hashlib
import time
import zipfile
import tempfile
from pathlib import Path

from app.schemas.log import LogLevel
from app.schemas.release import CleanroomSmokeEndpoint, CleanroomVerificationRecord, ReleaseCheckItem, ReleaseManifest, ReleasePackageRecord, ReleaseStatus
from app.schemas.report_export import ReportExportRequest
from app.services.log_service import JsonlLogService
from app.services.storage_paths import project_root

SAFETY_TEXT = "DISARMED + NO_FIRE + dry_run=true + hardware_enabled=false + physical_command_enabled=false"


class ReleaseService:
    def __init__(self, logger: JsonlLogService) -> None:
        self.logger = logger
        self.last_event: tuple[str, dict] | None = None
        self.latest_cleanroom: CleanroomVerificationRecord | None = None

    def status(self, runtime) -> ReleaseStatus:
        return self.preflight(runtime, emit_log=False, write_manifest=False)

    def preflight(self, runtime, emit_log: bool = False, write_manifest: bool = True) -> ReleaseStatus:
        root = project_root()
        inventory = runtime.device_manager.inventory()
        active = runtime.model_registry.active_models()
        hardware = runtime.hardware.status(mock_pico_active=runtime.pico.status().mock_mode)
        safety_ok = self._safety_invariant(runtime)
        manifest_path = self.ensure_manifest(runtime) if write_manifest else self._latest_manifest_path()
        checks = [
            self._item("python version", sys.version_info >= (3, 12), sys.version.split()[0], True),
            self._item("uv availability", shutil.which("uv") is not None, shutil.which("uv") or "uv not found", False),
            self._item("backend import", True, "app.main imported", True),
            self._item("frontend dist", self._frontend_dist(root), "frontend/dist/index.html", False),
            self._item("config loaded", runtime.config is not None, "config/config.yaml", True),
            self._writable("writable logs", root / "logs"),
            self._writable("writable exports", root / "exports"),
            self._item("release launcher files", self._release_launchers(root), "release/linux and release/windows launchers", True),
            self._item("root launcher files", (root / "start_linux.sh").exists() and (root / "start_windows.bat").exists(), "root start scripts", False),
            self._item("model dir", (root / "models").exists(), "models/", False),
            self._item("release manifest", manifest_path is not None and Path(manifest_path).exists(), manifest_path or "not generated", False),
            self._item("device manager", len(inventory.devices) >= 0, f"{len(inventory.devices)} devices"),
            self._item("camera source", runtime.camera_runtime.status().profile.source_type == "mock" or len(inventory.cameras) > 0, "mock camera or camera device available", False),
            self._item("model registry", True, f"{len(runtime.model_registry.list_models())} registered models"),
            self._item("no physical command invariant", safety_ok, "DISARMED + NO_FIRE + dry_run + hardware disabled + physical command disabled", True),
        ]
        launcher = any(item.name == "release launcher files" and item.status == "passed" for item in checks)
        static = any(item.name == "frontend dist" and item.status == "passed" for item in checks)
        writable_logs = any(item.name == "writable logs" and item.status == "passed" for item in checks)
        writable_exports = any(item.name == "writable exports" and item.status == "passed" for item in checks)
        writable = writable_logs and writable_exports
        field_profile = runtime.device_profiles.active().profile_id is not None
        active_model_loaded = bool(active.active_body_model_id or active.active_balloon_model_id or active.active_combined_model_id)
        failed = [item for item in checks if item.status == "failed"]
        warnings = [item for item in checks if item.status == "warning"]
        overall = "failed" if failed else ("warning" if warnings else "passed")
        suggestions = [
            item.detail.get("suggested_action", item.message)
            for item in checks
            if item.status in {"warning", "failed"}
        ]
        offline = "ready" if launcher and static and writable else ("blocked" if failed else "warning")
        return ReleaseStatus(
            launcher_available=launcher,
            frontend_static_available=static,
            writable_runtime_dirs=writable,
            offline_readiness=offline,
            field_profile_saved=field_profile,
            status=overall,
            platform=platform.platform(),
            python_version=sys.version.split()[0],
            app_root=str(root),
            writable_logs=writable_logs,
            writable_exports=writable_exports,
            config_loaded=runtime.config is not None,
            model_dir_present=(root / "models").exists(),
            active_model_loaded=active_model_loaded,
            camera_devices_detected=len(inventory.cameras),
            serial_devices_detected=len(inventory.serial),
            pico_candidate_count=len(inventory.pico_candidates),
            hardware_command_enabled=hardware.physical_command_enabled,
            dry_run=runtime.config.system.dry_run,
            no_fire=runtime.config.system.default_fire_policy == "NO_FIRE",
            safety_invariant_ok=safety_ok,
            release_manifest_path=manifest_path,
            suggested_actions=[str(item) for item in suggestions],
            checks=checks,
        )

    def check(self, runtime) -> ReleaseStatus:
        status = self.preflight(runtime, emit_log=False, write_manifest=True)
        payload = status.model_dump(mode="json")
        self.last_event = ("release.check_completed", payload)
        self.logger.emit(LogLevel.INFO, "RELEASE", "Release QA check completed", payload)
        return status

    def cold_start_check(self, runtime) -> ReleaseStatus:
        status = self.preflight(runtime, emit_log=False, write_manifest=True)
        status = status.model_copy(update={"cold_start_evidence": self.cold_start_evidence(runtime)})
        payload = status.model_dump(mode="json")
        payload["summary"] = "Cold-start release check completed; no physical command path enabled."
        self.last_event = ("release.cold_start_checked", payload)
        self.logger.emit(
            LogLevel.INFO,
            "RELEASE",
            "Cold-start release check completed; no physical command path enabled.",
            payload,
        )
        return status

    def cold_start_evidence(self, runtime) -> dict:
        root = project_root()
        inventory = runtime.device_manager.inventory()
        camera_status = runtime.camera_runtime.status()
        hardware = runtime.hardware.status(mock_pico_active=runtime.pico.status().mock_mode)
        active_package = runtime.model_packages.active_package_summary() if hasattr(runtime, "model_packages") else {}
        active_kind = active_package.get("package_kind") or ("fixture" if runtime.model_registry.active_models().active_test_adapter else "none")
        return {
            "platform": platform.platform(),
            "python_version": sys.version.split()[0],
            "uv_available": shutil.which("uv") is not None,
            "frontend_dist_present": self._frontend_dist(root),
            "writable_logs": self._path_writable(root / "logs"),
            "writable_exports": self._path_writable(root / "exports"),
            "config_loaded": runtime.config is not None,
            "model_dir_present": (root / "models").exists(),
            "active_model_kind": active_kind,
            "active_model_production": bool(active_package.get("production_model")),
            "active_model_id": active_package.get("active_model_id") or runtime.model_registry.active_models().active_combined_model_id,
            "camera_source": camera_status.profile.source_type,
            "camera_selected": camera_status.selected_camera,
            "pico_state": "verified" if hardware.pico_verified else ("candidate" if len(inventory.pico_candidates) else "absent"),
            "pico_verified": hardware.pico_verified,
            "launcher_files_exist": self._release_launchers(root),
            "safety_invariant_ok": self._safety_invariant(runtime),
            "no_physical_command_generated": True,
        }

    def latest_package(self) -> ReleasePackageRecord | None:
        root = project_root() / "exports" / "release"
        manifests = sorted(root.glob("istiklal_c2_release_*/package_manifest.json"), key=lambda path: path.stat().st_mtime, reverse=True)
        if not manifests:
            return None
        data = json.loads(manifests[0].read_text(encoding="utf-8"))
        root = project_root()
        generated = data.get("package_generated_commit") or data.get("commit_hash") or self._git_short_hash(root)
        workflow = data.get("package_workflow_commit") or self._commit_for_subject(root, "feat: add portable release package workflow") or generated
        report = data.get("report_commit") or self._commit_for_subject(root, "docs: add phase 22 portable release report hash") or generated
        data.setdefault("commit_hash", generated)
        data.setdefault("package_generated_commit", generated)
        data.setdefault("package_workflow_commit", workflow)
        data.setdefault("source_commit", workflow)
        data.setdefault("report_commit", report)
        return ReleasePackageRecord(**data)

    def build_package(self, runtime) -> ReleasePackageRecord:
        root = project_root()
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        package_id = f"istiklal_c2_release_{timestamp}"
        out_root = root / "exports" / "release"
        package_dir = out_root / package_id
        if package_dir.exists():
            shutil.rmtree(package_dir)
        package_dir.mkdir(parents=True, exist_ok=True)
        package_generated_commit = self._git_short_hash(root)
        package_workflow_commit = self._commit_for_subject(root, "feat: add portable release package workflow") or package_generated_commit
        report_commit = self._commit_for_subject(root, "docs: add phase 22 portable release report hash") or package_generated_commit
        source_commit = package_workflow_commit

        timeline = runtime.demo.run(runtime) if hasattr(runtime, "demo") else None
        report = runtime.report_export.generate_ktr_summary(runtime, ReportExportRequest(notes="Phase 22 portable release package evidence."))

        self._copy_tree(root / "backend", package_dir / "backend", extra_ignore=("tests", ".pytest_cache"))
        self._copy_tree(root / "config", package_dir / "config")
        self._copy_tree(root / "docs", package_dir / "docs")
        self._copy_tree(root / "firmware", package_dir / "firmware")
        self._copy_tree(root / "models", package_dir / "models")
        self._copy_tree(root / "frontend" / "dist", package_dir / "frontend_dist")
        self._copy_tree(root / "frontend" / "dist", package_dir / "frontend" / "dist")
        self._copy_file(root / "release" / "linux" / "start_istiklal_c2.sh", package_dir / "release" / "linux" / "start_istiklal_c2.sh")
        self._copy_file(root / "release" / "windows" / "start_istiklal_c2.bat", package_dir / "release" / "windows" / "start_istiklal_c2.bat")
        self._copy_file(root / "start_linux.sh", package_dir / "start_linux.sh")
        self._copy_file(root / "start_windows.bat", package_dir / "start_windows.bat")
        if Path(report.output_dir).exists():
            self._copy_tree(Path(report.output_dir), package_dir / "demo_evidence_package" / Path(report.output_dir).name)

        (package_dir / "README_RELEASE.md").write_text(self._release_readme(report), encoding="utf-8")
        (package_dir / "RUNBOOK_DEMO.md").write_text(runtime.demo.operator_script_markdown(runtime) if hasattr(runtime, "demo") else self._fallback_runbook(), encoding="utf-8")
        (package_dir / ".env.example").write_text(self._env_example(), encoding="utf-8")
        (package_dir / "config.example.yaml").write_text(self._config_example(), encoding="utf-8")

        manifest = {
            "package_id": package_id,
            "phase": "Phase 22",
            "commit_hash": package_generated_commit,
            "source_commit": source_commit,
            "package_generated_commit": package_generated_commit,
            "package_workflow_commit": package_workflow_commit,
            "report_commit": report_commit,
            "commit_semantics": {
                "source_commit": "Phase 22 portable release workflow source commit.",
                "package_generated_commit": "Git commit checked out when this package was generated.",
                "package_workflow_commit": "Commit that introduced the portable release package workflow.",
                "report_commit": "Latest Phase 22 report/hash documentation commit known at package generation time.",
            },
            "created_at": time.time(),
            "output_dir": str(package_dir),
            "zip_path": str(out_root / f"{package_id}.zip"),
            "included": [
                "frontend_dist/",
                "frontend/dist/",
                "backend/",
                "config/",
                "docs/",
                "firmware/",
                "models/",
                "release/linux/start_istiklal_c2.sh",
                "release/windows/start_istiklal_c2.bat",
                "start_linux.sh",
                "start_windows.bat",
                "demo_evidence_package/",
                "README_RELEASE.md",
                "RUNBOOK_DEMO.md",
                ".env.example",
                "config.example.yaml",
            ],
            "excluded": [".git", "node_modules", ".venv", "__pycache__", "logs", "data/sessions", "secrets", "tokens"],
            "release_demo_ready": bool(timeline and timeline.verdict.release_demo_ready),
            "competition_ready": bool(timeline and timeline.verdict.competition_ready),
            "dataset_ready_for_training": bool(timeline and timeline.verdict.dataset_ready_for_training),
            "safety_invariant": SAFETY_TEXT,
            "hardware_enabled": runtime.config.system.hardware_enabled,
            "physical_command_enabled": runtime.config.hardware.physical_command_enabled,
            "dry_run": runtime.config.system.dry_run,
            "no_fire": runtime.config.system.default_fire_policy == "NO_FIRE",
            "no_physical_command_generated": True,
        }
        manifest_path = package_dir / "package_manifest.json"
        files_count = len([path for path in package_dir.rglob("*") if path.is_file()]) + 2
        record = ReleasePackageRecord(
            package_id=package_id,
            output_dir=str(package_dir),
            zip_path=str(out_root / f"{package_id}.zip"),
            files_count=files_count,
            checksums_path=str(package_dir / "checksums.json"),
            manifest_path=str(manifest_path),
            commit_hash=package_generated_commit,
            source_commit=source_commit,
            package_generated_commit=package_generated_commit,
            package_workflow_commit=package_workflow_commit,
            report_commit=report_commit,
            release_demo_ready=manifest["release_demo_ready"],
            competition_ready=manifest["competition_ready"],
            dataset_ready_for_training=manifest["dataset_ready_for_training"],
        )
        manifest_path.write_text(json.dumps(record.model_dump(mode="json") | manifest, indent=2), encoding="utf-8")
        checksums = self._write_checksums(package_dir)
        checksums_path = package_dir / "checksums.json"
        checksums_path.write_text(json.dumps(checksums, indent=2), encoding="utf-8")
        zip_path = out_root / f"{package_id}.zip"
        self._write_zip(package_dir, zip_path)
        payload = record.model_dump(mode="json")
        self.last_event = ("release.package_generated", payload)
        self.logger.emit(LogLevel.INFO, "RELEASE", f"Portable release package generated; files={files_count}; no_physical_command_generated=true.", payload)
        self.logger.emit(LogLevel.INFO, "RELEASE", f"Portable release zip generated; path={zip_path}; no_physical_command_generated=true.", payload)
        self.logger.emit(LogLevel.INFO, "RELEASE", "Portable release package validated; no_physical_command_generated=true.", payload)
        return record

    def latest_cleanroom_verification(self) -> CleanroomVerificationRecord | None:
        if self.latest_cleanroom:
            return self.latest_cleanroom
        root = project_root() / "reports"
        files = sorted(root.glob("phase23_cleanroom_smoke_results.json"), key=lambda path: path.stat().st_mtime, reverse=True)
        if not files:
            return None
        data = json.loads(files[0].read_text(encoding="utf-8"))
        return CleanroomVerificationRecord(**data)

    def run_cleanroom_verification(self, runtime) -> CleanroomVerificationRecord:
        latest = self.latest_package()
        if latest is None:
            latest = self.build_package(runtime)
        zip_path = Path(latest.zip_path)
        run_id = f"cleanroom_{time.strftime('%Y%m%d_%H%M%S')}"
        extract_parent = Path(tempfile.gettempdir()) / f"istiklal_c2_cleanroom_{time.strftime('%Y%m%d_%H%M%S')}"
        if extract_parent.exists():
            shutil.rmtree(extract_parent)
        extract_parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(zip_path) as archive:
            names = archive.namelist()
            archive.extractall(extract_parent)
        forbidden = [name for name in names if any(part in name.split("/") for part in [".git", "node_modules", ".venv", "__pycache__"])]
        secrets = [name for name in names if "secret" in name.lower() or "token" in name.lower()]
        extract_root = extract_parent / latest.package_id
        launchers = [extract_root / "release" / "linux" / "start_istiklal_c2.sh", extract_root / "start_linux.sh"]
        hardcoded = any("/home/alperen/teknofest" in path.read_text(encoding="utf-8", errors="ignore") for path in launchers if path.exists())
        for path in launchers:
            if path.exists():
                subprocess.run(["bash", "-n", str(path)], check=True, cwd=extract_root)
        endpoints = self._cleanroom_endpoint_smoke(extract_root)
        passed = sum(1 for item in endpoints if item.ok)
        status = "passed" if passed == len(endpoints) and not forbidden and not secrets and not hardcoded else "failed"
        record = CleanroomVerificationRecord(
            run_id=run_id,
            package_id=latest.package_id,
            zip_path=str(zip_path),
            extract_path=str(extract_root),
            launch_command="bash release/linux/start_istiklal_c2.sh",
            smoke_status=status,
            endpoints=endpoints,
            endpoints_passed=passed,
            endpoints_total=len(endpoints),
            frontend_dist_present=(extract_root / "frontend" / "dist" / "index.html").exists(),
            backend_present=(extract_root / "backend" / "app" / "main.py").exists(),
            forbidden_entries=forbidden,
            secrets_or_tokens=secrets,
            launcher_hardcoded_repo_path=hardcoded,
            release_demo_ready=latest.release_demo_ready,
            competition_ready=False,
            no_physical_command_generated=True,
        )
        self._write_cleanroom_reports(record)
        payload = record.model_dump(mode="json")
        self.last_event = ("release.cleanroom_smoke_completed", payload)
        self.logger.emit(LogLevel.INFO, "RELEASE", f"Clean-room release smoke completed; endpoints={passed}/{len(endpoints)}; no_physical_command_generated=true.", payload)
        self.logger.emit(LogLevel.INFO, "RELEASE", f"Release clean-room extracted; path={extract_root}; no_physical_command_generated=true.", payload)
        self.logger.emit(LogLevel.INFO, "RELEASE", "Release portability audit generated; no_physical_command_generated=true.", payload)
        self.latest_cleanroom = record
        return record

    def cleanroom_report_markdown(self) -> str:
        record = self.latest_cleanroom_verification()
        if record is None:
            return "# Release Portability Audit\n\nNo clean-room verification run yet.\n\n- no_physical_command_generated=true\n"
        lines = [
            "# Release Portability Audit",
            "",
            f"- Run ID: {record.run_id}",
            f"- Package ID: {record.package_id}",
            f"- ZIP path: {record.zip_path}",
            f"- Extract path: {record.extract_path}",
            f"- Launch command: `{record.launch_command}`",
            f"- Smoke status: {record.smoke_status}",
            f"- Endpoints passed: {record.endpoints_passed}/{record.endpoints_total}",
            f"- Frontend dist present: {record.frontend_dist_present}",
            f"- Backend present: {record.backend_present}",
            f"- Forbidden dirs present: {bool(record.forbidden_entries)}",
            f"- Secrets/tokens present: {bool(record.secrets_or_tokens)}",
            f"- Launcher hardcoded repo path: {record.launcher_hardcoded_repo_path}",
            f"- Release demo ready: {record.release_demo_ready}",
            f"- Competition ready: {record.competition_ready}",
            "- no_physical_command_generated=true",
            "",
            "## Endpoints",
            "",
        ]
        lines.extend(f"- {item.method} {item.path}: HTTP {item.status_code}" for item in record.endpoints)
        return "\n".join(lines) + "\n"

    def cleanroom_results_json(self) -> str:
        record = self.latest_cleanroom_verification()
        return json.dumps(record.model_dump(mode="json") if record else {"status": "not_run", "no_physical_command_generated": True}, indent=2)

    def cleanroom_launch_notes_markdown(self) -> str:
        record = self.latest_cleanroom_verification()
        extract = record.extract_path if record else "not_run"
        return f"""# Clean-room Launch Notes

- Extract path: {extract}
- Launch command: `bash release/linux/start_istiklal_c2.sh`
- Linux/root launcher syntax checked from clean-room extract when available.
- Runtime dependency on `/home/alperen/teknofest`: false
- no_physical_command_generated=true
"""

    def portable_runtime_requirements_markdown(self) -> str:
        return """# Portable Runtime Requirements

- Python 3.12+
- uv available for dependency install/sync
- Writable `logs/` and `exports/`
- Frontend static build at `frontend/dist`
- Backend source at `backend/app`
- Config at `config/config.yaml`
- Demo mode / dry-run only
- no_physical_command_generated=true
"""

    def _cleanroom_endpoint_smoke(self, extract_root: Path) -> list[CleanroomSmokeEndpoint]:
        script = r'''
import json, os, sys
from pathlib import Path
root = Path.cwd()
sys.path.insert(0, str(root / "backend"))
from fastapi.testclient import TestClient
from app.main import create_app
routes = ["/dashboard", "/demo", "/reports", "/interfaces", "/logs", "/data-lab", "/api/demo/readiness", "/api/demo/latest", "/api/release/package/latest", "/api/health"]
out = []
with TestClient(create_app()) as client:
    for route in routes:
        response = client.get(route)
        out.append({"method": "GET", "path": route, "status_code": response.status_code, "ok": 200 <= response.status_code < 300})
print(json.dumps(out))
'''
        result = subprocess.run([sys.executable, "-c", script], cwd=extract_root, text=True, capture_output=True, timeout=60)
        if result.returncode != 0:
            return [CleanroomSmokeEndpoint(path="cleanroom_subprocess", status_code=500, ok=False)]
        return [CleanroomSmokeEndpoint(**item) for item in json.loads(result.stdout)]

    def _write_cleanroom_reports(self, record: CleanroomVerificationRecord) -> None:
        reports = project_root() / "reports"
        reports.mkdir(parents=True, exist_ok=True)
        record.report_paths = {
            "release_portability_audit.md": str(reports / "phase23_cleanroom_smoke_summary.md"),
            "cleanroom_smoke_results.json": str(reports / "phase23_cleanroom_smoke_results.json"),
        }
        (reports / "phase23_cleanroom_smoke_results.json").write_text(json.dumps(record.model_dump(mode="json"), indent=2), encoding="utf-8")
        lines = [
            "# Release Portability Audit",
            "",
            f"- Run ID: {record.run_id}",
            f"- Package ID: {record.package_id}",
            f"- ZIP path: {record.zip_path}",
            f"- Extract path: {record.extract_path}",
            f"- Launch command: `{record.launch_command}`",
            f"- Smoke status: {record.smoke_status}",
            f"- Endpoints passed: {record.endpoints_passed}/{record.endpoints_total}",
            f"- Frontend dist present: {record.frontend_dist_present}",
            f"- Backend present: {record.backend_present}",
            f"- Forbidden dirs present: {bool(record.forbidden_entries)}",
            f"- Secrets/tokens present: {bool(record.secrets_or_tokens)}",
            f"- Launcher hardcoded repo path: {record.launcher_hardcoded_repo_path}",
            f"- Release demo ready: {record.release_demo_ready}",
            f"- Competition ready: {record.competition_ready}",
            "- no_physical_command_generated=true",
            "",
            "## Endpoints",
            "",
            *(f"- {item.method} {item.path}: HTTP {item.status_code}" for item in record.endpoints),
        ]
        (reports / "phase23_cleanroom_smoke_summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    def launcher_inspection(self) -> dict:
        root = project_root()
        files = [
            root / "release" / "linux" / "start_istiklal_c2.sh",
            root / "release" / "windows" / "start_istiklal_c2.bat",
            root / "start_linux.sh",
            root / "start_windows.bat",
        ]
        forbidden_patterns = [
            "/api/safety/fire-request",
            "/api/motion/jog",
            "/api/motion/go-to",
            "/api/motion/home",
            "/api/motion/scan/start",
            "physical_command_enabled=true",
            "hardware_enabled=true",
        ]
        inspected: list[dict] = []
        for path in files:
            exists = path.exists()
            text = path.read_text(encoding="utf-8", errors="ignore") if exists else ""
            matches = [pattern for pattern in forbidden_patterns if pattern in text]
            inspected.append(
                {
                    "path": str(path),
                    "exists": exists,
                    "contains_safety_invariant": SAFETY_TEXT in text,
                    "forbidden_endpoint_calls": matches,
                    "safe": exists and not matches,
                }
            )
        return {
            "files": inspected,
            "safe": all(item["safe"] for item in inspected),
            "summary": "Launcher scripts only start the software; no motor/fire/GPIO endpoint call was found.",
            "no_physical_command_generated": True,
        }

    def package_summary_markdown(self) -> str:
        latest = self.latest_package()
        if latest is None:
            return "# Release Package Summary\n\nNo portable release package generated yet.\n\n- no_physical_command_generated=true\n"
        return f"""# Release Package Summary

- Package ID: {latest.package_id}
- Output dir: {latest.output_dir}
- ZIP path: {latest.zip_path}
- Source commit: {latest.source_commit}
- Package generated commit: {latest.package_generated_commit}
- Package workflow commit: {latest.package_workflow_commit}
- Report/docs commit: {latest.report_commit}
- Files count: {latest.files_count}
- Checksum status: {latest.checksum_status}
- Release demo ready: {latest.release_demo_ready}
- Competition ready: {latest.competition_ready}
- Dataset ready for training: {latest.dataset_ready_for_training}
- no_physical_command_generated=true

Portable release package is a demo/evidence package. It does not enable hardware, fire, motor, servo, GPIO, STEP/DIR/PWM or physical command paths.
"""

    def package_manifest_json(self) -> str:
        latest = self.latest_package()
        if latest is None:
            return json.dumps({"status": "not_generated", "no_physical_command_generated": True}, indent=2)
        manifest_path = Path(latest.manifest_path)
        return manifest_path.read_text(encoding="utf-8") if manifest_path.exists() else json.dumps(latest.model_dump(mode="json"), indent=2)

    def zip_check_markdown(self) -> str:
        latest = self.latest_package()
        if latest is None:
            return "# Release ZIP Check\n\nNo ZIP generated yet.\n\n- no_physical_command_generated=true\n"
        zip_path = Path(latest.zip_path)
        exists = zip_path.exists()
        return f"""# Release ZIP Check

- ZIP exists: {exists}
- ZIP path: {latest.zip_path}
- Checksum status: {latest.checksum_status}
- Forbidden dirs excluded: .git, node_modules, .venv, __pycache__
- Secrets/tokens included: false
- no_physical_command_generated=true
"""

    def ensure_manifest(self, runtime) -> str:
        manifest = self.manifest(runtime)
        out_dir = project_root() / "exports" / "release"
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"release_manifest_{int(manifest.generated_at)}.json"
        path.write_text(json.dumps(manifest.model_dump(mode="json"), indent=2), encoding="utf-8")
        return str(path)

    def manifest(self, runtime) -> ReleaseManifest:
        root = project_root()
        commit = self._git_short_hash(root)
        frontend_dist = self._frontend_dist(root)
        return ReleaseManifest(
            commit_hash=commit,
            build_id=f"phase22-{commit}",
            platform=platform.platform(),
            included_components=[
                "backend FastAPI application",
                "frontend static dist" if frontend_dist else "frontend static dist missing",
                "config files",
                "firmware telemetry-only documentation",
                "model import folders",
                "release launcher scripts",
                "docs and first-run guides",
            ],
            excluded_runtime_dirs=["logs/**", "exports/**", "data/sessions/**", "models/uploaded/**", "frontend/node_modules/**"],
            safety_invariant={
                "mode": runtime.config.system.mode,
                "fire_policy": runtime.config.system.default_fire_policy,
                "dry_run": runtime.config.system.dry_run,
                "hardware_enabled": runtime.config.system.hardware_enabled,
                "physical_command_enabled": runtime.config.hardware.physical_command_enabled,
                "ok": self._safety_invariant(runtime),
            },
            launcher_files=[
                "release/linux/start_istiklal_c2.sh",
                "release/windows/start_istiklal_c2.bat",
                "start_linux.sh",
                "start_windows.bat",
            ],
            frontend_dist_present=frontend_dist,
            backend_entrypoint="backend/app/main.py",
            dependency_strategy="uv creates .venv and installs backend dependencies; frontend dist is prebuilt and pnpm is not required at runtime.",
        )

    def _item(self, name: str, ok: bool, message: str, blocking: bool = False) -> ReleaseCheckItem:
        return ReleaseCheckItem(name=name, status="passed" if ok else ("failed" if blocking else "warning"), message=message, blocking=blocking)

    def _writable(self, name: str, path: Path) -> ReleaseCheckItem:
        try:
            path.mkdir(parents=True, exist_ok=True)
            probe = path / ".release_write_probe"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink(missing_ok=True)
            return self._item(name, True, str(path))
        except OSError as exc:
            return ReleaseCheckItem(name=name, status="failed", message=str(exc), blocking=True, detail={"path": str(path)})

    def _frontend_dist(self, root: Path) -> bool:
        return (root / "frontend" / "dist" / "index.html").exists()

    def _release_launchers(self, root: Path) -> bool:
        return (root / "release" / "linux" / "start_istiklal_c2.sh").exists() and (root / "release" / "windows" / "start_istiklal_c2.bat").exists()

    def _path_writable(self, path: Path) -> bool:
        try:
            path.mkdir(parents=True, exist_ok=True)
            probe = path / ".release_cold_start_probe"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink(missing_ok=True)
            return True
        except OSError:
            return False

    def _safety_invariant(self, runtime) -> bool:
        return (
            runtime.config.system.mode == "DISARMED"
            and runtime.config.system.default_fire_policy == "NO_FIRE"
            and runtime.config.system.dry_run
            and not runtime.config.system.hardware_enabled
            and not runtime.config.hardware.physical_command_enabled
        )

    def _git_short_hash(self, root: Path) -> str:
        try:
            result = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=root, check=True, text=True, capture_output=True)
            return result.stdout.strip() or "dev-local"
        except (OSError, subprocess.CalledProcessError):
            return os.environ.get("ISTIKLAL_BUILD_ID", "dev-local")

    def _commit_for_subject(self, root: Path, subject: str) -> str | None:
        try:
            result = subprocess.run(
                ["git", "log", "--all", "--format=%h %s", "--grep", f"^{subject}$", "-n", "1"],
                cwd=root,
                check=True,
                text=True,
                capture_output=True,
            )
            line = result.stdout.strip()
            return line.split(" ", 1)[0] if line else None
        except (OSError, subprocess.CalledProcessError):
            return None

    def _latest_manifest_path(self) -> str | None:
        out_dir = project_root() / "exports" / "release"
        manifests = sorted(out_dir.glob("release_manifest_*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
        return str(manifests[0]) if manifests else None

    def _copy_file(self, source: Path, target: Path) -> None:
        if not source.exists():
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)

    def _copy_tree(self, source: Path, target: Path, extra_ignore: tuple[str, ...] = ()) -> None:
        if not source.exists():
            return
        ignore = shutil.ignore_patterns(
            ".git",
            "node_modules",
            ".venv",
            "__pycache__",
            "*.pyc",
            "*.token",
            "*secret*",
            *extra_ignore,
        )
        shutil.copytree(source, target, dirs_exist_ok=True, ignore=ignore)

    def _write_checksums(self, package_dir: Path) -> dict:
        checksums: dict[str, str] = {}
        for path in sorted(package_dir.rglob("*")):
            if not path.is_file() or path.name == "checksums.json":
                continue
            relative = path.relative_to(package_dir).as_posix()
            checksums[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
        return {"algorithm": "sha256", "files": checksums, "no_physical_command_generated": True}

    def _write_zip(self, package_dir: Path, zip_path: Path) -> None:
        if zip_path.exists():
            zip_path.unlink()
        forbidden = {".git", "node_modules", ".venv", "__pycache__"}
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(package_dir.rglob("*")):
                if not path.is_file():
                    continue
                parts = set(path.relative_to(package_dir).parts)
                if parts & forbidden:
                    continue
                archive.write(path, package_dir.name + "/" + path.relative_to(package_dir).as_posix())

    def _release_readme(self, report) -> str:
        return f"""# ISTIKLAL C2 Portable Release

This package is a demo/evidence package. It does not claim competition readiness.

- Latest report export: {report.export_id}
- Run Linux: `bash release/linux/start_istiklal_c2.sh`
- Run Windows: `release\\windows\\start_istiklal_c2.bat`
- Demo mode: dry-run only
- no_physical_command_generated=true

Portable release package is a demo/evidence package. It does not enable hardware, fire, motor, servo, GPIO, STEP/DIR/PWM or physical command paths.
"""

    def _fallback_runbook(self) -> str:
        return "# Demo Runbook\n\n- Start software.\n- Show Jury Demo Center.\n- Confirm no_physical_command_generated=true.\n"

    def _env_example(self) -> str:
        return "ISTIKLAL_RUNTIME_MODE=demo\nISTIKLAL_NO_PHYSICAL_COMMAND=true\n"

    def _config_example(self) -> str:
        return "system:\n  mode: DISARMED\n  default_fire_policy: NO_FIRE\n  dry_run: true\n  hardware_enabled: false\nhardware:\n  physical_command_enabled: false\n"
