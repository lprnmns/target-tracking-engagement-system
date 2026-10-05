import json
import subprocess
import time
import uuid
from pathlib import Path

from app.schemas.config import AppConfig
from app.schemas.log import LogLevel
from app.schemas.report_export import ReportExportRecord, ReportExportRequest, ReportsStatus
from app.services.log_service import JsonlLogService
from app.services.storage_paths import project_root, resolve_project_path


CORE_INTERFACES: list[dict[str, str | bool]] = [
    {"name": "Frontend ↔ Backend REST", "source": "Vue UI", "target": "FastAPI", "protocol": "HTTP/JSON", "data_type": "commands and state", "direction": "bidirectional", "safety_critical": True, "status": "implemented", "notes": "Safety-changing calls remain backend gated."},
    {"name": "Frontend ↔ Backend WebSocket", "source": "FastAPI", "target": "Vue stores", "protocol": "WebSocket JSON envelope", "data_type": "telemetry and events", "direction": "backend to frontend", "safety_critical": False, "status": "implemented", "notes": "No frontend mock fallback."},
    {"name": "Backend ↔ Pico Serial JSON-line", "source": "Backend serial service", "target": "Pico 2", "protocol": "JSON-line dev protocol", "data_type": "heartbeat, disarm, self-test and telemetry", "direction": "bidirectional", "safety_critical": True, "status": "mock transport", "notes": "Real serial disabled by config."},
    {"name": "Backend ↔ Pico Binary Protocol codec", "source": "Backend protocol codec", "target": "Pico 2", "protocol": "AA TYPE SEQ LEN PAYLOAD CRC16 55", "data_type": "binary packets", "direction": "bidirectional", "safety_critical": True, "status": "codec tested, not deployed", "notes": "Physical device use is future work."},
    {"name": "Camera ↔ Backend MJPEG/OpenCV", "source": "Mock/OpenCV camera", "target": "CameraService", "protocol": "MJPEG/OpenCV", "data_type": "frames", "direction": "camera to backend/frontend", "safety_critical": False, "status": "mock default", "notes": "Webcam absence does not crash backend."},
    {"name": "Vision model ↔ Inference adapter", "source": "Vision team model", "target": "InferenceAdapterService", "protocol": "adapter interface", "data_type": "detections", "direction": "model to backend", "safety_critical": False, "status": "interface/test adapter", "notes": "Production algorithm owned by vision team."},
    {"name": "Motion service ↔ Serial dry-run layer", "source": "MotionService", "target": "SerialService", "protocol": "dry-run command planning", "data_type": "motion command plans", "direction": "backend internal", "safety_critical": True, "status": "dry-run only", "notes": "No physical movement generated."},
    {"name": "Dataset/replay ↔ Vision pipeline", "source": "Session/Replay services", "target": "Vision/Data Lab", "protocol": "file metadata + JSONL", "data_type": "frames, annotations and detections", "direction": "bidirectional metadata", "safety_critical": False, "status": "implemented", "notes": "Replay output advisory only."},
    {"name": "Self-test ↔ all services", "source": "SelfTestService", "target": "Runtime services", "protocol": "internal checks", "data_type": "readiness steps", "direction": "backend internal", "safety_critical": True, "status": "implemented", "notes": "Readiness does not enable fire."},
    {"name": "Reports/KTR export ↔ backend services", "source": "ReportExportService", "target": "Runtime services and filesystem", "protocol": "REST + Markdown/JSON files", "data_type": "KTR, demo, readiness and inventory reports", "direction": "backend to filesystem/frontend", "safety_critical": False, "status": "implemented", "notes": "Report generation does not change safety state or enable commands."},
]


GATE_LABELS = {
    "system_disarmed_gate": "System Mode / Disarmed Gate",
    "system_armed_gate": "Armed for Dry-run Evaluation",
    "dry_run_gate": "Dry-run Gate",
    "hardware_enabled_gate": "Hardware Enabled Gate",
    "estop_gate": "E-stop Released",
    "pico_connected_gate": "Pico Service Available",
    "pico_heartbeat_gate": "Pico Heartbeat",
    "serial_ok_gate": "Serial Layer Safe",
    "motion_soft_limits_gate": "Motion Soft Limits",
    "motion_estop_gate": "Motion E-stop",
    "motion_fault_gate": "Motion Fault Clear",
    "motion_driver_gate": "Motion Driver Disabled",
    "motion_dry_run_gate": "Motion Dry-run",
    "vision_running_gate": "Vision Running",
    "body_detected_gate": "Body Detected",
    "balloon_detected_gate": "Balloon Detected",
    "team_classified_gate": "Team Classified",
    "enemy_target_gate": "Enemy Target Confirmed",
    "friend_rejection_gate": "Friend Target Rejected",
    "range_valid_gate": "Range Valid",
    "stable_track_gate": "Stable Track",
    "forbidden_zone_gate": "Forbidden Zone",
    "operator_confirm_gate": "Operator Confirm",
}


class ReportExportService:
    def __init__(self, config: AppConfig, logger: JsonlLogService) -> None:
        self.config = config
        self.logger = logger
        self.root = resolve_project_path(config.reports.root_dir)
        self.root.mkdir(parents=True, exist_ok=True)
        self.records: list[ReportExportRecord] = []
        self.last_event: tuple[str, dict] | None = None

    def status(self) -> ReportsStatus:
        self._load_existing()
        return ReportsStatus(exports_count=len(self.records), latest_export=self.records[-1] if self.records else None, root_dir=str(self.root))

    def list_exports(self) -> list[ReportExportRecord]:
        self._load_existing()
        return list(reversed(self.records))

    def get_export(self, export_id: str) -> ReportExportRecord:
        self._load_existing()
        for record in self.records:
            if record.export_id == export_id:
                return record
        raise KeyError(export_id)

    def generate_ktr_summary(self, runtime, request: ReportExportRequest | None = None) -> ReportExportRecord:
        return self._generate(runtime, "ktr_summary", request)

    def generate_demo_pack(self, runtime, request: ReportExportRequest | None = None) -> ReportExportRecord:
        return self._generate(runtime, "demo_pack", request)

    def generate_readiness_pack(self, runtime, request: ReportExportRequest | None = None) -> ReportExportRecord:
        return self._generate(runtime, "readiness_pack", request)

    def _generate(self, runtime, kind: str, request: ReportExportRequest | None) -> ReportExportRecord:
        request = request or ReportExportRequest()
        export_id = f"{kind}-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
        output_dir = self.root / export_id
        output_dir.mkdir(parents=True, exist_ok=True)
        started = ReportExportRecord(export_id=export_id, kind=kind, status="running", output_dir=str(output_dir))
        self._event("report.export_started", started.model_dump(mode="json"), f"Report export started: {kind}")
        try:
            files = self._write_pack(runtime, output_dir, kind, request)
            metadata = {
                "export_id": export_id,
                "kind": kind,
                "created_at": time.time(),
                "git_hash": self._git_hash(),
                "no_physical_command_generated": True,
                "notes": request.notes,
                "files": files,
                **self._first_run_snapshot(runtime),
            }
            (output_dir / "export_metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
            files.append(str(output_dir / "export_metadata.json"))
            record = ReportExportRecord(
                export_id=export_id,
                kind=kind,
                status="completed",
                output_dir=str(output_dir),
                files=files,
                summary=self._summary(runtime),
                no_physical_command_generated=True,
            )
            self.records.append(record)
            self._event("report.export_completed", record.model_dump(mode="json"), f"Report export completed: {kind}")
            return record
        except Exception as exc:
            record = started.model_copy(update={"status": "failed", "error": str(exc)})
            self.records.append(record)
            self._event("report.export_failed", record.model_dump(mode="json"), f"Report export failed: {kind}")
            raise

    def _write_pack(self, runtime, output_dir: Path, kind: str, request: ReportExportRequest) -> list[str]:
        files: list[str] = []
        writers = {
            "ktr_summary.md": self._ktr_summary,
            "interface_inventory.md": self._interface_inventory,
            "ktr_4_3_interfaces.md": self._ktr_4_3_interfaces,
            "safety_summary.md": self._safety_summary,
            "self_test_summary.md": self._self_test_summary,
            "model_registry_summary.md": self._model_registry_summary,
            "model_package_inventory.json": self._model_package_inventory_json,
            "active_model_summary.json": self._active_model_summary_json,
            "vision_runtime_summary.json": self._vision_runtime_summary_json,
            "model_validation_summary.json": self._model_validation_summary_json,
            "safety_summary.json": self._safety_summary_json,
            "cold_start_summary.json": self._cold_start_summary_json,
            "cold_start_summary.md": self._cold_start_summary,
            "launcher_inspection.md": self._launcher_inspection,
            "live_camera_surrogate_summary.md": self._live_camera_surrogate_summary,
            "live_camera_surrogate_summary.json": self._live_camera_surrogate_summary_json,
            "vision_circle_detection_sample.json": self._vision_circle_detection_sample_json,
            "snapshot_manifest.json": self._snapshot_manifest_json,
            "data_lab_summary.md": self._data_lab_summary,
            "data_lab_sessions.json": self._data_lab_sessions_json,
            "detection_events_sample.jsonl": self._data_lab_detection_events_sample,
            "replay_readiness.md": self._data_lab_replay_readiness,
            "replay_summary.md": self._data_lab_replay_summary,
            "replay_latest.json": self._data_lab_replay_latest_json,
            "annotation_candidates.json": self._data_lab_annotation_candidates_json,
            "annotation_review_summary.md": self._data_lab_annotation_review_summary,
            "dataset_health_summary.md": self._data_lab_dataset_health_summary,
            "real_camera_evidence_summary.md": self._real_camera_evidence_summary,
            "real_camera_evidence_latest.json": self._real_camera_evidence_latest_json,
            "camera_host_device_inventory.json": self._camera_host_device_inventory_json,
            "camera_device_inventory.json": self._camera_host_device_inventory_json,
            "camera_tooling_diagnosis.json": self._camera_tooling_diagnosis_json,
            "camera_device_permission_report.json": self._camera_device_permission_report_json,
            "camera_host_blocker_report.md": self._camera_host_blocker_report,
            "real_camera_status.json": self._real_camera_status_json,
            "real_camera_capture_evidence.json": self._real_camera_capture_evidence_json,
            "real_camera_frame_capture_attempt.json": self._real_camera_frame_capture_attempt_json,
            "real_camera_frame_acceptance_result.json": self._real_camera_frame_acceptance_result_json,
            "usb_camera_capture_evidence.json": self._real_camera_capture_evidence_json,
            "usb_camera_acceptance_summary.md": self._usb_camera_acceptance_summary,
            "real_camera_acceptance_summary.md": self._real_camera_acceptance_summary,
            "legacy_perception_presets.json": self._legacy_perception_presets_json,
            "legacy_perception_migration_summary.md": self._legacy_perception_migration_summary,
            "direction_calibration_profile.json": self._direction_calibration_profile_json,
            "direction_simulation_summary.md": self._direction_simulation_summary,
            "motion_semantics_contract.md": self._motion_semantics_contract,
            "direction_safety_boundary.md": self._direction_safety_boundary,
            "pico_readonly_status.json": self._pico_readonly_status_json,
            "pico_readonly_port_inventory.json": self._pico_readonly_port_inventory_json,
            "pico_readonly_evidence_summary.md": self._pico_readonly_evidence_summary,
            "pico_readonly_safety_boundary.md": self._pico_readonly_safety_boundary,
            "pico_permission_diagnosis.json": self._pico_permission_diagnosis_json,
            "pico_rxonly_permission_acceptance.json": self._pico_rxonly_permission_acceptance_json,
            "demo_timeline.json": self._demo_timeline_json,
            "demo_timeline.md": self._demo_timeline_md,
            "demo_readiness_summary.md": self._demo_readiness_summary,
            "demo_runbook.md": self._demo_runbook,
            "jury_demo_summary.md": self._jury_demo_summary,
            "release_demo_verdict.json": self._release_demo_verdict_json,
            "evidence_index.md": self._evidence_index,
            "known_limitations.md": self._known_limitations,
            "demo_operator_script.md": self._demo_operator_script,
            "release_package_summary.md": self._release_package_summary,
            "release_package_manifest.json": self._release_package_manifest_json,
            "release_zip_check.md": self._release_zip_check,
            "release_portability_audit.md": self._release_portability_audit,
            "cleanroom_smoke_results.json": self._cleanroom_smoke_results,
            "cleanroom_launch_notes.md": self._cleanroom_launch_notes,
            "portable_runtime_requirements.md": self._portable_runtime_requirements,
            "jury_rehearsal_summary.md": self._jury_rehearsal_summary,
            "jury_rehearsal_verdict.json": self._jury_rehearsal_verdict,
            "jury_rehearsal_timeline.md": self._jury_rehearsal_timeline,
            "jury_rehearsal_operator_script.md": self._jury_rehearsal_operator_script,
            "jury_rehearsal_limitations.md": self._jury_rehearsal_limitations,
            "jury_rehearsal_cleanroom_status.md": self._jury_rehearsal_cleanroom_status,
            "dataset_summary.md": self._dataset_summary,
            "operation_checklist.md": self._operation_checklist,
            "mission_evidence.md": self._mission_evidence,
            "mission_evidence.json": self._mission_evidence_json,
        }
        for filename, writer in writers.items():
            if kind == "readiness_pack" and filename not in {"safety_summary.md", "self_test_summary.md", "operation_checklist.md", "interface_inventory.md", "ktr_4_3_interfaces.md"}:
                continue
            if kind == "demo_pack" and filename == "ktr_summary.md":
                continue
            path = output_dir / filename
            path.write_text(writer(runtime, request), encoding="utf-8")
            files.append(str(path))
        if self.config.reports.include_screenshots and (project_root() / "reports" / "screenshots").exists():
            manifest = self._screenshots_manifest()
            path = output_dir / "screenshots_manifest.json"
            path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
            files.append(str(path))
        return files

    def _summary(self, runtime) -> dict:
        health = runtime.dataset.health()
        latest_self_test = runtime.self_test.latest_run
        return {
            "no_physical_command_generated": True,
            "dry_run": runtime.config.system.dry_run,
            "hardware_enabled": runtime.config.system.hardware_enabled,
            "models": len(runtime.model_registry.list_models()),
            "active_model": runtime.model_packages.active_package_summary() if hasattr(runtime, "model_packages") else {},
            "model_validation_status": (runtime.model_packages.active_package_summary().get("model_validation_status") if hasattr(runtime, "model_packages") else "unavailable"),
            "class_mapping_status": (runtime.model_packages.active_package_summary().get("class_mapping_status") if hasattr(runtime, "model_packages") else "unavailable"),
            "package_kind": (runtime.model_packages.active_package_summary().get("package_kind") if hasattr(runtime, "model_packages") else "unavailable"),
            "production_model": (runtime.model_packages.active_package_summary().get("production_model") if hasattr(runtime, "model_packages") else False),
            "production_ready": (runtime.model_packages.active_package_summary().get("production_ready") if hasattr(runtime, "model_packages") else False),
            "competition_ready": (runtime.model_packages.active_package_summary().get("competition_ready") if hasattr(runtime, "model_packages") else False),
            "advisory_only": True,
            "sessions": health.total_sessions,
            "data_lab": runtime.data_lab.status(runtime).model_dump(mode="json") if hasattr(runtime, "data_lab") else {},
            "annotations": health.total_annotations,
            "latest_self_test": latest_self_test.model_dump(mode="json") if latest_self_test else None,
            "latest_demo_timeline": runtime.demo.latest().run_id if hasattr(runtime, "demo") else "not_available",
            "latest_demo_report_export": runtime.demo.latest().report_export_id if hasattr(runtime, "demo") else None,
            "release_demo_ready": runtime.demo.latest().verdict.release_demo_ready if hasattr(runtime, "demo") else False,
            "competition_ready": runtime.demo.latest().verdict.competition_ready if hasattr(runtime, "demo") else False,
            "dataset_ready_for_training": runtime.demo.latest().verdict.dataset_ready_for_training if hasattr(runtime, "demo") else False,
            "mission": runtime.mission.snapshot().model_dump(mode="json") if hasattr(runtime, "mission") else {},
            **self._first_run_snapshot(runtime),
        }

    def _mission_evidence(self, runtime, request: ReportExportRequest) -> str:
        if not hasattr(runtime, "mission"):
            return "# Competition Mission Evidence\n\nMission service is not available.\n"
        return runtime.mission.markdown()

    def _mission_evidence_json(self, runtime, request: ReportExportRequest) -> str:
        if not hasattr(runtime, "mission"):
            return "{}"
        return runtime.mission.json()

    def _first_run_snapshot(self, runtime) -> dict:
        status = runtime.first_run.status(runtime)
        last = status.last_successful_first_run or {}
        return {
            "current_first_run_status": status.current_first_run_status,
            "current_profile_id": status.current_profile_id,
            "current_profile_evaluation_status": status.current_profile_evaluation_status,
            "last_successful_first_run_run_id": last.get("run_id"),
            "last_successful_first_run_profile_id": last.get("profile_id"),
            "last_successful_first_run_timestamp": last.get("timestamp"),
            "stale_evidence": status.stale_evidence,
        }

    def _ktr_summary(self, runtime, request: ReportExportRequest) -> str:
        first_run = self._first_run_snapshot(runtime)
        return f"""# KTR Summary - ISTIKLAL Komuta Kontrol Merkezi

## Sistem Genel Mimarisi

ISTIKLAL C2, FastAPI backend, Vue 3 frontend, WebSocket telemetry, mock/default Pico serial layer, camera/vision metadata pipeline, decision/safety gates, dry-run motion service, Data Lab and Self-Test services from a single command-control interface.

## Arayüzler Envanteri

Arayüz detayları `interface_inventory.md` içinde tablo olarak, KTR 4.3 için doğrudan kullanılabilecek anlatım `ktr_4_3_interfaces.md` içinde verilmiştir.

## Kullanıcı Arayüzleri

Dashboard, Safety, Self-Test, Pico, Serial, Vision, Motion, Calibration, Color, Data Lab, Reports and Logs screens are available.

## Yazılımsal Arayüzler

REST endpoints, WebSocket envelopes, JSON-line serial protocol, binary protocol codec, dataset/replay file interfaces and report export endpoints are implemented.

## Elektronik/Donanım Arayüzleri

Laptop to Pico 2 serial, Pico 2 to TMC2209 STEP/DIR/UART, servo PWM, E-stop input and limit switches are represented. Real hardware command path remains disabled.

## Mesaj Protokolleri

Backend REST uses JSON schemas. WebSocket envelopes carry `type`, `ts`, `seq`, `payload`. Serial JSON-line supports safe heartbeat, disarm, self-test and set-mode messages in mock mode.

## WebSocket Eventleri

System, safety, decision, pico, serial, vision, motion, dataset, replay, self-test and report events are published.

## Serial Protocol Özeti

JSON-line dev protocol is active by default. Binary packet codec with CRC16 exists as testable foundation but is not used with physical hardware in this phase.

## Pico 2 Pinout ve Pin Validation Yaklaşımı

Placeholder Pico 2 pin profile is loaded and validated for unique critical functions, direction, PWM capability, UART conflicts and required ESTOP_IN.

## Safety Gates

System mode, dry-run, hardware-enabled, E-stop, Pico/serial status, vision target gates, friend/enemy logic, range and stability gates are represented.

## Decision Engine

Default policy is NO_FIRE. Friend or unknown targets cannot become fire-ready. Balloon, valid range, stable frames and operator confirm gates are enforced for evaluation only.

## Vision/Model Integration Yaklaşımı

Vision team owns production model and inference algorithm. Interface team provides model package import, registry, active model selection, adapter contract, runtime parameter management and test adapter.

Model handoff package contains model file, `metadata.json`, class mapping and `thresholds.json`. Model outputs are advisory metadata only and cannot generate physical fire or motion.

OpenCV live circle surrogate, gerçek laptop/USB kamera veya mock kamera üzerinden yuvarlak hedef benzeri şekilleri arayüz, görüntü aktarımı, overlay, loglama, snapshot ve latency/FPS akışını test etmek için kullanılır. Mock kamera ile çalıştırıldığında kanıt `mock_camera_circle_surrogate` ve `mock_frame` olarak işaretlenir; gerçek `/dev/video*` veya laptop/USB kamera ile çalıştırıldığında `live_camera_circle_surrogate` ve `real_capture` olarak ayrılır. OpenCV yuvarlak algılayıcı yalnızca arayüz/görüntü aktarımı/overlay/loglama testi içindir; production YOLO veya yarışma modeli değildir. Mock kamera ile alınan çıktı gerçek kamera doğrulaması olarak sunulmaz.

## Dataset/Replay/Model Registry Yaklaşımı

Data Lab manages sessions, snapshots, annotations, replay status, YOLO export and dataset health. Vision mock/surrogate detections are recorded as session-level evidence with `advisory_only=true` and `no_physical_command_generated=true`. Team metadata remains outside YOLO class labels.

## Veri Seti, Oturum Kaydı ve Replay Arayüzü

Data Lab, `/api/data-lab` endpointleri üzerinden session listesi, latest session, latest mock/surrogate detection evidence, detection event sample JSONL ve replay-readiness özetini üretir. `session.json`, `detections.jsonl`, `annotations.jsonl`, snapshot metadata and replay state are filesystem/REST interfaces for operator review and KTR evidence. This layer is metadata-only and cannot enable physical motion, fire, GPIO, STEP/DIR/PWM or hardware commands.

## Self-Test/Readiness Yaklaşımı

Self-Test checks service readiness and safety invariants. Readiness does not enable physical fire.

## Reports/KTR Export Yaklaşımı

Reports/KTR export collects runtime state, self-test evidence, interface inventory, safety summary, model registry summary, dataset summary, demo runbook and operation checklist into Markdown/JSON files. Export does not change safety state and does not enable hardware.

## Taşınabilir Çalıştırma ve Kurulum Arayüzü

Windows `.bat` ve Linux `.sh` başlatıcıları, hazır frontend static UI çıktısını FastAPI backend üzerinden servis ederek tek ZIP paketinden yerel çalışma sağlar. İlk kurulum/preflight kontrolleri Python/uv bağımlılığını, frontend static build varlığını, yazılabilir log/export klasörlerini, device discovery sonucunu, kamera kaynak bağlamasını, model runtime bağlamasını ve release manifest kanıtını raporlar.

Device Manager kamera/Pico ayrımını, First Run Wizard release candidate acceptance durumunu, Vision Runtime model/test adaptörü durumunu ve Logs/Reports export kanıtlarını operatöre gösterir. Başlatıcı arayüzleri yalnızca yazılımı çalıştırır; fiziksel komut yetkisi vermez.

Release candidate profili, sistemin donanımsız ve üretim YOLO modeli olmadan güvenli şekilde açılabildiğini doğrular. Competition rehearsal profili ise üretim YOLO modeli, doğrulanmış Pico telemetrisi, gerçek kamera/probe doğrulaması ve çalıştırılmış self-test gerektirir; bu koşullar eksikse prova hazırlığı engellenmiş sayılır.

Cold-start evidence raporu, ilk çalıştırmada Python/uv, frontend static build, yazılabilir log/export klasörleri, config, model klasörü, kamera kaynağı, Pico absent/verified ayrımı ve no physical command invariant durumunu ayrı kontrol eder. Release candidate readiness taşınabilir yazılım, arayüz, rapor ve güvenli demo çalışmasını; competition rehearsal readiness ise gerçek kamera, production YOLO, Pico telemetry verification ve saha profili gereksinimlerini ifade eder.

## Clean-room Release Verification ve Jury Rehearsal Arayüzü

Clean-room verification arayüzü, üretilen portable ZIP paketini repo dışı temiz bir `/tmp/istiklal_c2_cleanroom_*` klasörüne çıkarır, launcher scriptlerini syntax/static inspection ile kontrol eder ve çıkarılan paket içinden FastAPI + static frontend route smoke testlerini çalıştırır. Bu arayüz yalnızca demo/evidence/release doğrulama içindir; fiziksel komut üretmez ve `no_physical_command_generated=true` kanıtını raporlar.

Jury rehearsal arayüzü, safety snapshot, first-run/profile state, demo readiness, timeline, Data Lab evidence, replay, annotation review, dataset health, release package, clean-room status ve KTR/report export durumunu tek jüri prova paketinde birleştirir. Release demo hazır olabilir; competition readiness için production YOLO, gerçek kamera kanıtı, Pico telemetry verification ve tamamlanmış self-test gerekir. Mock/surrogate evidence yalnızca demo/release kanıtıdır.

## First Run Current State ve Historical Evidence

- Current first-run status: {first_run['current_first_run_status']}
- Current profile: {first_run['current_profile_id']}
- Current profile evaluation: {first_run['current_profile_evaluation_status']}
- Stale evidence: {first_run['stale_evidence']}

### Previous Evidence

- Last successful run: {first_run.get('last_successful_first_run_run_id') or 'none'}
- Last successful profile: {first_run.get('last_successful_first_run_profile_id') or 'none'}
- Last successful timestamp: {first_run.get('last_successful_first_run_timestamp') or 'none'}

Current status open/not_evaluated ise geçmiş başarılı kanıt current passed olarak raporlanmaz.

## Güvenlik Varsayımları

- DISARMED startup
- NO_FIRE default
- dry_run=true
- hardware_enabled=false
- no physical command generated

## Dry-Run / No Physical Command Politikası

All motion, serial, model, replay, dataset and report paths remain non-physical.

## Bilinçli Olarak Bu Fazda Yapılmayanlar

Production vision algorithm, YOLO training, physical serial enable, physical motor movement, fire/servo command and hardware authorization were not added.
"""

    def _interface_inventory(self, runtime, request: ReportExportRequest) -> str:
        if hasattr(runtime, "interface_inventory"):
            return runtime.interface_inventory.markdown_inventory()
        header = "| Interface name | Source | Target | Protocol | Data type | Direction | Safety critical | Current implementation status | Notes |\n|---|---|---|---|---|---|---|---|---|"
        rows = [
            f"| {item['name']} | {item['source']} | {item['target']} | {item['protocol']} | {item['data_type']} | {item['direction']} | {item['safety_critical']} | {item['status']} | {item['notes']} |"
            for item in CORE_INTERFACES
        ]
        return "# Interface Inventory\n\n" + header + "\n" + "\n".join(rows) + "\n"

    def _ktr_4_3_interfaces(self, runtime, request: ReportExportRequest) -> str:
        if hasattr(runtime, "interface_inventory"):
            return runtime.interface_inventory.ktr_section().markdown
        return "# KTR 4.3 Arayüzler\n\nInterface inventory service is unavailable.\n"

    def _safety_summary(self, runtime, request: ReportExportRequest) -> str:
        decision = runtime.decision_engine.evaluate(runtime)
        gate_lines = []
        for gate in decision.gates:
            label = GATE_LABELS.get(gate.name, gate.name.replace("_", " ").title())
            gate_lines.append(f"- {label} (`{gate.name}`): {gate.status} - {gate.reason}")
        return f"""# Safety Summary

- Default startup: DISARMED
- Default fire policy: {runtime.config.system.default_fire_policy}
- dry_run=true
- hardware_enabled=false
- No physical command generated evidence: true

## Fire Request Rejection Model

Fire request is reject-by-default. Even if all decision gates pass for dry-run evaluation, hardware remains disabled and no physical command is generated.

## Safety Gates

{chr(10).join(gate_lines)}

## Target Rules

- Friend target rejection: enabled
- Unknown team rejection: enabled
- Balloon required: {runtime.config.decision.require_balloon}
- Range required: {runtime.config.decision.require_valid_range}
- Stable track required: {runtime.config.decision.stable_frames_required}

## E-stop, Pico and Hardware Limitations

Pico telemetry exposes E-stop state. Current mock/default implementation can read and display this state, but physical E-stop, limit switches, Pico local safety firmware and hardware enable flow must be validated before real hardware use.

## Before Real Hardware

Finalize pin profile, validate E-stop/limit switches, enable signed/explicit hardware config, complete Pico local safety, verify serial ACK/NACK under load, and perform supervised dry-run field test.
"""

    def _self_test_summary(self, runtime, request: ReportExportRequest) -> str:
        run = runtime.self_test.latest_run
        if run is None:
            return "# Self-Test Summary\n\nNo self-test run is available. Run `/api/self-test/run` before final demo.\n"
        suggested_actions = [action for action in run.summary.get("suggested_actions", []) if action]
        suggested_text = "\n".join(f"- {action}" for action in suggested_actions) if suggested_actions else "- No suggested actions."
        return f"""# Self-Test Summary

- Run ID: {run.run_id}
- Status: {run.status}
- Readiness level: {run.readiness_level}
- Critical failures: {run.summary.get('critical_failures', 0)}
- Warnings: {run.summary.get('warning', 0)}
- No physical command generated: {run.no_physical_command_generated}
- dry_run: {run.dry_run}
- hardware_enabled: {run.hardware_enabled}
- Generated report path: {run.report_path or 'report not exported'}

## Step Summary

{chr(10).join(f"- [{step.status}] {step.name} (`{step.step_id}`): {step.message}" for step in run.steps)}

## Suggested Actions

{suggested_text}
"""

    def _model_registry_summary(self, runtime, request: ReportExportRequest) -> str:
        active = runtime.model_registry.active_models()
        models = runtime.model_registry.list_models()
        package_summary = runtime.model_packages.active_package_summary() if hasattr(runtime, "model_packages") else {}

        def clean(value: str | None) -> str:
            if value is None:
                return "not selected"
            return value.replace("_", " ")

        def warnings_text(warnings: list[str]) -> str:
            if not warnings:
                return "no warnings"
            return "; ".join(clean(warning) for warning in warnings)

        rows = "\n".join(
            f"- {model.name} `{model.model_id}`: {clean(model.model_type)}, {clean(model.framework)}, {clean(model.status)}, warnings={warnings_text(model.warnings)}"
            for model in models
        )
        return f"""# Model Registry Summary

## Active Models

- Body: {clean(active.active_body_model_id)}
- Balloon: {clean(active.active_balloon_model_id)}
- Combined: {clean(active.active_combined_model_id)}
- Test adapter: {clean(active.active_test_adapter)}
- Active package: {clean(package_summary.get('active_model_id') if package_summary else None)}
- Model validation status: {clean(package_summary.get('model_validation_status') if package_summary else None)}
- Class mapping status: {clean(package_summary.get('class_mapping_status') if package_summary else None)}
- Advisory only: true

## Registry

{rows or "- No models registered."}

## Scope

Vision team provides production models, class list, input size, thresholds and adapter details. Interface team provides registry, metadata, active selection, replay/model testing and UI.
"""

    def _model_package_inventory_json(self, runtime, request: ReportExportRequest) -> str:
        payload = runtime.model_packages.inventory_json() if hasattr(runtime, "model_packages") else {"packages": [], "no_physical_command_generated": True}
        return json.dumps(payload, indent=2)

    def _active_model_summary_json(self, runtime, request: ReportExportRequest) -> str:
        payload = runtime.model_packages.active_package_summary() if hasattr(runtime, "model_packages") else {}
        return json.dumps({**payload, "no_physical_command_generated": True}, indent=2)

    def _vision_runtime_summary_json(self, runtime, request: ReportExportRequest) -> str:
        status = runtime.vision_runtime.status()
        return json.dumps(status.model_dump(mode="json"), indent=2)

    def _model_validation_summary_json(self, runtime, request: ReportExportRequest) -> str:
        if not hasattr(runtime, "model_packages"):
            return json.dumps({"validations": [], "no_physical_command_generated": True}, indent=2)
        validations = [
            {
                "model_id": package.model_id,
                "version": package.version,
                "package_kind": runtime.model_packages.semantic_state(package).package_kind,
                "package_schema_status": runtime.model_packages.semantic_state(package).package_schema_validation,
                "runtime_status": runtime.model_packages.semantic_state(package).runtime_validation,
                "class_mapping_status": package.validation.class_mapping_status if package.validation else "not_validated",
                "production_status": runtime.model_packages.semantic_state(package).production_readiness,
                "competition_status": runtime.model_packages.semantic_state(package).competition_readiness,
                "warnings": runtime.model_packages.semantic_state(package).warnings,
                "blockers": runtime.model_packages.semantic_state(package).blockers,
                "validation": package.validation.model_dump(mode="json") if package.validation else None,
            }
            for package in runtime.model_packages.list_packages()
        ]
        return json.dumps({"validations": validations, "no_physical_command_generated": True}, indent=2)

    def _safety_summary_json(self, runtime, request: ReportExportRequest) -> str:
        return json.dumps(
            {
                "mode": runtime.config.system.mode,
                "fire_policy": runtime.config.system.default_fire_policy,
                "dry_run": runtime.config.system.dry_run,
                "hardware_enabled": runtime.config.system.hardware_enabled,
                "physical_command_enabled": runtime.config.hardware.physical_command_enabled,
                "no_physical_command_generated": True,
            },
            indent=2,
        )

    def _cold_start_summary_json(self, runtime, request: ReportExportRequest) -> str:
        status = runtime.release.cold_start_check(runtime)
        return json.dumps(status.model_dump(mode="json"), indent=2)

    def _cold_start_summary(self, runtime, request: ReportExportRequest) -> str:
        status = runtime.release.cold_start_check(runtime)
        evidence = status.cold_start_evidence
        checks = "\n".join(f"- [{item.status}] {item.name}: {item.message} (blocking={item.blocking})" for item in status.checks)
        return f"""# Cold-Start Release Summary

## Readiness Ayrımı

- Release candidate readiness: taşınabilir yazılım, arayüz, rapor ve güvenli demo çalışması.
- Competition rehearsal readiness: gerçek kamera, production YOLO, Pico telemetry verification ve saha profili gerektirir.

## Evidence

- Status: {status.status}
- Platform: {status.platform}
- Python version: {status.python_version}
- Frontend dist present: {status.frontend_static_available}
- Writable logs: {status.writable_logs}
- Writable exports: {status.writable_exports}
- Config loaded: {status.config_loaded}
- Model dir present: {status.model_dir_present}
- Active model kind: {evidence.get("active_model_kind", "unknown")}
- Camera source: {evidence.get("camera_source", "unknown")}
- Pico state: {evidence.get("pico_state", "unknown")}
- No physical command generated: {status.no_physical_command_generated}
- Safety invariant OK: {status.safety_invariant_ok}

## Checks

{checks}
"""

    def _launcher_inspection(self, runtime, request: ReportExportRequest) -> str:
        inspection = runtime.release.launcher_inspection()
        lines = [
            "# Launcher Inspection",
            "",
            "Başlatıcı arayüzleri yalnızca yazılımı çalıştırır; fiziksel komut yetkisi vermez.",
            "",
            f"- Overall safe: {inspection['safe']}",
            f"- No physical command generated: {inspection['no_physical_command_generated']}",
            "",
            "## Files",
            "",
        ]
        for item in inspection["files"]:
            lines.append(f"- `{item['path']}`: exists={item['exists']}, safety_invariant={item['contains_safety_invariant']}, forbidden_endpoint_calls={item['forbidden_endpoint_calls']}")
        return "\n".join(lines) + "\n"

    def _live_camera_surrogate_summary_json(self, runtime, request: ReportExportRequest) -> str:
        summary = runtime.vision_surrogate.summary()
        if summary.get("source") is None or summary.get("source") == "not_run":
            runtime.vision_surrogate.run(runtime.camera_runtime, runtime.vision_runtime.profile.model_copy(update={"inference_adapter": "opencv_live_circle_surrogate"}))
            summary = runtime.vision_surrogate.summary()
        return json.dumps(summary, indent=2)

    def _live_camera_surrogate_summary(self, runtime, request: ReportExportRequest) -> str:
        summary = runtime.vision_surrogate.summary()
        return f"""# Live Camera OpenCV Circle Surrogate Summary

- Adapter: `opencv_live_circle_surrogate`
- Available: {summary.get('available')}
- Running: {summary.get('running')}
- Source: {summary.get('source', 'not_run')}
- Camera source kind: {summary.get('camera_source_kind', 'not_run')}
- Camera device path: {summary.get('camera_device_path', 'not_available')}
- Frame origin: {summary.get('frame_origin', 'not_run')}
- Detector kind: {summary.get('detector_kind', 'opencv_circle_surrogate')}
- Production YOLO loaded: false
- Circle count: {summary.get('circle_count', 0)}
- Camera FPS: {summary.get('camera_fps', 'not_measured')}
- Detector loop FPS: {summary.get('detector_fps', summary.get('fps', 0))}
- Preprocess ms: {summary.get('preprocess_ms', 0)}
- Inference ms: {summary.get('inference_ms', 0)}
- Postprocess ms: {summary.get('postprocess_ms', 0)}
- Total ms: {summary.get('total_ms', summary.get('latency_ms', 0))}
- Advisory only: true
- Production ready: false
- Competition ready: false
- No physical command generated: true

OpenCV yuvarlak algılayıcı yalnızca arayüz/görüntü aktarımı/overlay/loglama testi içindir; production YOLO veya yarışma modeli değildir.

{ "Real camera capture not proven in this run." if summary.get('camera_source_kind') != 'real_camera' else "Real camera capture evidence is present for this run." }
"""

    def _vision_circle_detection_sample_json(self, runtime, request: ReportExportRequest) -> str:
        if not runtime.vision_surrogate.summary().get("detections"):
            runtime.vision_surrogate.run(runtime.camera_runtime, runtime.vision_runtime.profile.model_copy(update={"inference_adapter": "opencv_live_circle_surrogate"}))
        return json.dumps(runtime.vision_surrogate.summary(), indent=2)

    def _snapshot_manifest_json(self, runtime, request: ReportExportRequest) -> str:
        snapshot_dir = project_root() / "exports" / "vision_surrogate" / "snapshots"
        files = sorted(str(path) for path in snapshot_dir.glob("*.json")) if snapshot_dir.exists() else []
        return json.dumps({"snapshots": files, "count": len(files), "no_physical_command_generated": True}, indent=2)

    def _data_lab_summary(self, runtime, request: ReportExportRequest) -> str:
        if hasattr(runtime, "data_lab"):
            return runtime.data_lab.summary_markdown(runtime)
        return "# Data Lab Summary\n\nData Lab service unavailable.\n"

    def _data_lab_sessions_json(self, runtime, request: ReportExportRequest) -> str:
        if hasattr(runtime, "data_lab"):
            return runtime.data_lab.sessions_json()
        return json.dumps({"sessions": [], "advisory_only": True, "no_physical_command_generated": True}, indent=2)

    def _data_lab_detection_events_sample(self, runtime, request: ReportExportRequest) -> str:
        if hasattr(runtime, "data_lab"):
            return runtime.data_lab.detection_events_jsonl()
        return json.dumps({"advisory_only": True, "no_physical_command_generated": True}) + "\n"

    def _data_lab_replay_readiness(self, runtime, request: ReportExportRequest) -> str:
        if hasattr(runtime, "data_lab"):
            return runtime.data_lab.replay_readiness_markdown()
        return "# Replay Readiness\n\nData Lab service unavailable.\n"

    def _data_lab_replay_summary(self, runtime, request: ReportExportRequest) -> str:
        if hasattr(runtime, "data_lab"):
            return runtime.data_lab.replay_summary_markdown()
        return "# Data Lab Replay Summary\n\nData Lab service unavailable.\n"

    def _data_lab_replay_latest_json(self, runtime, request: ReportExportRequest) -> str:
        if hasattr(runtime, "data_lab"):
            return json.dumps(runtime.data_lab.replay_status().model_dump(mode="json"), indent=2)
        return json.dumps({"advisory_only": True, "no_physical_command_generated": True}, indent=2)

    def _data_lab_annotation_candidates_json(self, runtime, request: ReportExportRequest) -> str:
        if hasattr(runtime, "data_lab"):
            return runtime.data_lab.annotation_candidates_json()
        return json.dumps({"candidates": [], "advisory_only": True, "no_physical_command_generated": True}, indent=2)

    def _data_lab_annotation_review_summary(self, runtime, request: ReportExportRequest) -> str:
        if hasattr(runtime, "data_lab"):
            return runtime.data_lab.annotation_review_summary_markdown()
        return "# Annotation Review Summary\n\nData Lab service unavailable.\n"

    def _data_lab_dataset_health_summary(self, runtime, request: ReportExportRequest) -> str:
        if hasattr(runtime, "data_lab"):
            return runtime.data_lab.dataset_health_summary_markdown()
        return "# Dataset Health Summary\n\nData Lab service unavailable.\n"

    def _real_camera_evidence_summary(self, runtime, request: ReportExportRequest) -> str:
        if hasattr(runtime, "legacy_perception"):
            return runtime.legacy_perception.evidence_summary_markdown()
        return "# Real Camera Evidence Summary\n\nLegacy perception service unavailable.\n\n- no_physical_command_generated=true\n"

    def _real_camera_evidence_latest_json(self, runtime, request: ReportExportRequest) -> str:
        if hasattr(runtime, "legacy_perception"):
            return runtime.legacy_perception.latest_json()
        return json.dumps({"status": "unavailable", "no_physical_command_generated": True, "physical_command_enabled": False}, indent=2)

    def _camera_host_device_inventory_json(self, runtime, request: ReportExportRequest) -> str:
        if hasattr(runtime, "camera_host"):
            return runtime.camera_host.inventory_json()
        return json.dumps({"host_camera_devices_detected": False, "no_physical_command_generated": True, "physical_command_enabled": False}, indent=2)

    def _camera_tooling_diagnosis_json(self, runtime, request: ReportExportRequest) -> str:
        if hasattr(runtime, "camera_host"):
            return runtime.camera_host.diagnostic_commands_json()
        return json.dumps({"commands": [], "no_physical_command_generated": True, "physical_command_enabled": False}, indent=2)

    def _camera_device_permission_report_json(self, runtime, request: ReportExportRequest) -> str:
        if hasattr(runtime, "camera_host"):
            latest = runtime.camera_host.latest()
            return json.dumps(
                {
                    "user_in_video_group": latest.user_in_video_group,
                    "dev_video_entries": latest.dev_video_entries,
                    "blocker_reason": latest.blocker_reason,
                    "suggested_actions": latest.suggested_actions,
                    "advisory_only": True,
                    "physical_command_enabled": False,
                    "no_physical_command_generated": True,
                },
                indent=2,
            )
        return json.dumps({"user_in_video_group": False, "no_physical_command_generated": True, "physical_command_enabled": False}, indent=2)

    def _camera_host_blocker_report(self, runtime, request: ReportExportRequest) -> str:
        if hasattr(runtime, "camera_host"):
            return runtime.camera_host.blocker_report_markdown()
        return "# Camera Host Blocker Report\n\nCamera host diagnostic service unavailable.\n\n- no_physical_command_generated=true\n"

    def _real_camera_status_json(self, runtime, request: ReportExportRequest) -> str:
        if hasattr(runtime, "legacy_perception"):
            return runtime.legacy_perception.status(runtime.camera_runtime).model_dump_json(indent=2)
        return json.dumps({"status": "unavailable", "no_physical_command_generated": True, "physical_command_enabled": False}, indent=2)

    def _real_camera_capture_evidence_json(self, runtime, request: ReportExportRequest) -> str:
        if hasattr(runtime, "legacy_perception"):
            return runtime.legacy_perception.latest_json()
        return json.dumps({"status": "unavailable", "no_physical_command_generated": True, "physical_command_enabled": False}, indent=2)

    def _real_camera_frame_capture_attempt_json(self, runtime, request: ReportExportRequest) -> str:
        if hasattr(runtime, "legacy_perception"):
            return runtime.legacy_perception.latest_json()
        return json.dumps({"status": "unavailable", "frame_captured": False, "no_physical_command_generated": True, "physical_command_enabled": False}, indent=2)

    def _real_camera_frame_acceptance_result_json(self, runtime, request: ReportExportRequest) -> str:
        if hasattr(runtime, "camera_host") and hasattr(runtime, "legacy_perception"):
            latest = runtime.camera_host.latest()
            return json.dumps(
                {
                    "status": "passed" if latest.real_camera_frame_captured else ("blocked" if latest.camera_acceptance_status == "blocked_by_host_os" else "partial"),
                    "camera_tooling_status": latest.camera_acceptance_status,
                    "frame_captured": latest.real_camera_frame_captured,
                    "blocker_reason": latest.blocker_reason,
                    "latest_evidence": json.loads(runtime.legacy_perception.latest_json()),
                    "advisory_only": True,
                    "physical_command_enabled": False,
                    "no_physical_command_generated": True,
                },
                indent=2,
            )
        return json.dumps({"status": "unavailable", "frame_captured": False, "no_physical_command_generated": True, "physical_command_enabled": False}, indent=2)

    def _real_camera_acceptance_summary(self, runtime, request: ReportExportRequest) -> str:
        if hasattr(runtime, "legacy_perception"):
            return runtime.legacy_perception.evidence_summary_markdown()
        return "# Real Camera Acceptance Summary\n\nLegacy perception service unavailable.\n\n- no_physical_command_generated=true\n"

    def _usb_camera_acceptance_summary(self, runtime, request: ReportExportRequest) -> str:
        if hasattr(runtime, "camera_host") and hasattr(runtime, "legacy_perception"):
            latest = runtime.legacy_perception.latest()
            host = runtime.camera_host.latest()
            usb_passed = latest.status == "recorded" and latest.camera_device_path in {"/dev/video2", "/dev/video3"}
            return f"""# USB Camera Acceptance Summary

- Selected camera: {host.selected_camera_device or host.recommended_usb_device_path or 'not_selected'}
- Selected camera name: {host.selected_camera_name or 'HD USB Camera'}
- Camera kind: external_usb_camera
- Backend USB frame capture: {'passed' if usb_passed else 'partial'}
- External USB camera passed: {usb_passed}
- Browser external observation: observed_by_operator
- advisory_only=true
- physical_command_enabled=false
- no_physical_command_generated=true

USB camera evidence is real camera evidence only when the backend captures a frame from the external USB camera path such as `/dev/video2`. Browser-level camera success alone is operator observation, not backend acceptance.
"""
        return "# USB Camera Acceptance Summary\n\nCamera services unavailable.\n\n- no_physical_command_generated=true\n"

    def _legacy_perception_presets_json(self, runtime, request: ReportExportRequest) -> str:
        if hasattr(runtime, "legacy_perception"):
            return runtime.legacy_perception.presets_json()
        return json.dumps({"presets": [], "no_physical_command_generated": True}, indent=2)

    def _legacy_perception_migration_summary(self, runtime, request: ReportExportRequest) -> str:
        if hasattr(runtime, "legacy_perception"):
            return runtime.legacy_perception.migration_summary_markdown()
        return "# Legacy Perception Migration Summary\n\nLegacy perception service unavailable.\n\n- no_physical_command_generated=true\n"

    def _direction_calibration_profile_json(self, runtime, request: ReportExportRequest) -> str:
        if hasattr(runtime, "calibration"):
            return runtime.calibration.direction_profile_json()
        return json.dumps({"physical_command_enabled": False, "no_physical_command_generated": True}, indent=2)

    def _direction_simulation_summary(self, runtime, request: ReportExportRequest) -> str:
        if hasattr(runtime, "calibration"):
            return runtime.calibration.direction_simulation_summary_markdown()
        return "# Direction Simulation Summary\n\nCalibration service unavailable.\n\n- no_physical_command_generated=true\n"

    def _motion_semantics_contract(self, runtime, request: ReportExportRequest) -> str:
        if hasattr(runtime, "calibration"):
            return runtime.calibration.motion_semantics_contract_markdown()
        return "# Motion Semantics Contract\n\nCalibration service unavailable.\n\n- no_physical_command_generated=true\n"

    def _direction_safety_boundary(self, runtime, request: ReportExportRequest) -> str:
        if hasattr(runtime, "calibration"):
            return runtime.calibration.direction_safety_boundary_markdown()
        return "# Direction Safety Boundary\n\nCalibration service unavailable.\n\n- no_physical_command_generated=true\n"

    def _pico_readonly_status_json(self, runtime, request: ReportExportRequest) -> str:
        if hasattr(runtime, "pico"):
            return runtime.pico.readonly_status_json()
        return json.dumps({"physical_command_enabled": False, "no_physical_command_generated": True}, indent=2)

    def _pico_readonly_port_inventory_json(self, runtime, request: ReportExportRequest) -> str:
        if hasattr(runtime, "pico"):
            return runtime.pico.readonly_port_inventory_json()
        return json.dumps({"ports": [], "physical_command_enabled": False, "no_physical_command_generated": True}, indent=2)

    def _pico_readonly_evidence_summary(self, runtime, request: ReportExportRequest) -> str:
        if hasattr(runtime, "pico"):
            return runtime.pico.readonly_evidence_summary_markdown()
        return "# Pico Read-only Evidence Summary\n\nPico service unavailable.\n\n- no_physical_command_generated=true\n"

    def _pico_readonly_safety_boundary(self, runtime, request: ReportExportRequest) -> str:
        if hasattr(runtime, "pico"):
            return runtime.pico.readonly_safety_boundary_markdown()
        return "# Pico Read-only Safety Boundary\n\nPico service unavailable.\n\n- no_physical_command_generated=true\n"

    def _pico_permission_diagnosis_json(self, runtime, request: ReportExportRequest) -> str:
        if hasattr(runtime, "pico"):
            return runtime.pico.readonly_permission_status_json()
        return json.dumps({"status": "unavailable", "no_physical_command_generated": True, "physical_command_enabled": False}, indent=2)

    def _pico_rxonly_permission_acceptance_json(self, runtime, request: ReportExportRequest) -> str:
        if hasattr(runtime, "pico"):
            return runtime.pico.readonly_permission_acceptance_json()
        return json.dumps({"acceptance": "unavailable", "no_physical_command_generated": True, "physical_command_enabled": False}, indent=2)

    def _demo_timeline_json(self, runtime, request: ReportExportRequest) -> str:
        if hasattr(runtime, "demo"):
            return runtime.demo.timeline_json(runtime)
        return json.dumps({"events": [], "no_physical_command_generated": True}, indent=2)

    def _demo_timeline_md(self, runtime, request: ReportExportRequest) -> str:
        if hasattr(runtime, "demo"):
            return runtime.demo.timeline_markdown(runtime)
        return "# Demo Evidence Timeline\n\nDemo timeline service unavailable.\n"

    def _demo_readiness_summary(self, runtime, request: ReportExportRequest) -> str:
        if hasattr(runtime, "demo"):
            return runtime.demo.readiness_markdown(runtime)
        return "# Demo Readiness Summary\n\nDemo timeline service unavailable.\n"

    def _jury_demo_summary(self, runtime, request: ReportExportRequest) -> str:
        if hasattr(runtime, "demo"):
            return runtime.demo.jury_demo_summary_markdown(runtime)
        return "# Jury Demo Summary\n\nDemo timeline service unavailable.\n"

    def _release_demo_verdict_json(self, runtime, request: ReportExportRequest) -> str:
        if hasattr(runtime, "demo"):
            return runtime.demo.release_demo_verdict_json(runtime)
        return json.dumps({"no_physical_command_generated": True}, indent=2)

    def _evidence_index(self, runtime, request: ReportExportRequest) -> str:
        if hasattr(runtime, "demo"):
            return runtime.demo.evidence_index_markdown(runtime)
        return "# Evidence Index\n\n- no_physical_command_generated=true\n"

    def _known_limitations(self, runtime, request: ReportExportRequest) -> str:
        if hasattr(runtime, "demo"):
            return runtime.demo.known_limitations_markdown(runtime)
        return "# Known Limitations\n\n- Demo timeline service unavailable.\n"

    def _demo_operator_script(self, runtime, request: ReportExportRequest) -> str:
        if hasattr(runtime, "demo"):
            return runtime.demo.operator_script_markdown(runtime)
        return "# Demo Operator Script\n\nDemo timeline service unavailable.\n"

    def _release_package_summary(self, runtime, request: ReportExportRequest) -> str:
        if hasattr(runtime, "release"):
            return runtime.release.package_summary_markdown()
        return "# Release Package Summary\n\nRelease service unavailable.\n"

    def _release_package_manifest_json(self, runtime, request: ReportExportRequest) -> str:
        if hasattr(runtime, "release"):
            return runtime.release.package_manifest_json()
        return json.dumps({"status": "release_service_unavailable", "no_physical_command_generated": True}, indent=2)

    def _release_zip_check(self, runtime, request: ReportExportRequest) -> str:
        if hasattr(runtime, "release"):
            return runtime.release.zip_check_markdown()
        return "# Release ZIP Check\n\nRelease service unavailable.\n"

    def _release_portability_audit(self, runtime, request: ReportExportRequest) -> str:
        return runtime.release.cleanroom_report_markdown() if hasattr(runtime, "release") else "# Release Portability Audit\n\nRelease service unavailable.\n"

    def _cleanroom_smoke_results(self, runtime, request: ReportExportRequest) -> str:
        return runtime.release.cleanroom_results_json() if hasattr(runtime, "release") else json.dumps({"status": "unavailable", "no_physical_command_generated": True}, indent=2)

    def _cleanroom_launch_notes(self, runtime, request: ReportExportRequest) -> str:
        return runtime.release.cleanroom_launch_notes_markdown() if hasattr(runtime, "release") else "# Clean-room Launch Notes\n\nRelease service unavailable.\n"

    def _portable_runtime_requirements(self, runtime, request: ReportExportRequest) -> str:
        return runtime.release.portable_runtime_requirements_markdown() if hasattr(runtime, "release") else "# Portable Runtime Requirements\n\nRelease service unavailable.\n"

    def _jury_rehearsal_summary(self, runtime, request: ReportExportRequest) -> str:
        return runtime.demo.jury_rehearsal_summary_markdown(runtime) if hasattr(runtime, "demo") else "# Jury Rehearsal Summary\n\nDemo service unavailable.\n"

    def _jury_rehearsal_verdict(self, runtime, request: ReportExportRequest) -> str:
        return runtime.demo.jury_rehearsal_verdict_json(runtime) if hasattr(runtime, "demo") else json.dumps({"no_physical_command_generated": True}, indent=2)

    def _jury_rehearsal_timeline(self, runtime, request: ReportExportRequest) -> str:
        return runtime.demo.jury_rehearsal_timeline_markdown(runtime) if hasattr(runtime, "demo") else "# Jury Rehearsal Timeline\n\nDemo service unavailable.\n"

    def _jury_rehearsal_operator_script(self, runtime, request: ReportExportRequest) -> str:
        return runtime.demo.jury_rehearsal_operator_script_markdown(runtime) if hasattr(runtime, "demo") else "# Jury Rehearsal Operator Script\n\nDemo service unavailable.\n"

    def _jury_rehearsal_limitations(self, runtime, request: ReportExportRequest) -> str:
        return runtime.demo.jury_rehearsal_limitations_markdown(runtime) if hasattr(runtime, "demo") else "# Jury Rehearsal Limitations\n\nDemo service unavailable.\n"

    def _jury_rehearsal_cleanroom_status(self, runtime, request: ReportExportRequest) -> str:
        if hasattr(runtime, "release"):
            latest = runtime.release.latest_cleanroom_verification()
            if latest:
                return f"# Jury Rehearsal Clean-room Status\n\n- Clean-room verified: {latest.smoke_status == 'passed'}\n- Run ID: {latest.run_id}\n- Extract path: {latest.extract_path}\n- Endpoints passed: {latest.endpoints_passed}/{latest.endpoints_total}\n- no_physical_command_generated=true\n"
        return "# Jury Rehearsal Clean-room Status\n\nNo clean-room verification run yet.\n\n- no_physical_command_generated=true\n"

    def _dataset_summary(self, runtime, request: ReportExportRequest) -> str:
        health = runtime.dataset.health()
        exports = runtime.dataset.list_exports()
        return f"""# Dataset Summary

- Session count: {health.total_sessions}
- Snapshot/frame count: {health.total_images}
- Annotation count: {health.total_annotations}
- Dataset export count: {len(exports)}

## Class Distribution

```json
{json.dumps(health.class_distribution, indent=2)}
```

## Distance Distribution

```json
{json.dumps(health.distance_distribution, indent=2)}
```

## Lens Distribution

```json
{json.dumps(health.lens_distribution, indent=2)}
```

## Recommended Next Data Collection

{chr(10).join(f"- {item}" for item in health.recommendations) or "- No immediate recommendation."}
"""

    def _demo_runbook(self, runtime, request: ReportExportRequest) -> str:
        return """# Demo Runbook

1. Backend/frontend başlat.
   Expected result: Dashboard shows Backend Connected and safety lock badges.
2. Safety lock kontrol et.
   Expected result: DISARMED, NO_FIRE, dry_run=true, hardware_enabled=false and physical_command_enabled=false.
3. First-run acceptance çalıştır veya current state’i incele.
   Expected result: release candidate state and competition blockers are separate.
4. Vision mock/surrogate akışını başlat veya latest metadata’yı göster.
   Expected result: detections are advisory metadata only.
5. Data Lab evidence kaydet.
   Expected result: session metadata and detection JSONL evidence are generated.
6. Data Lab replay çalıştır.
   Expected result: recorded detection metadata is replayed without live camera or physical output.
7. Annotation/dataset health kontrol et.
   Expected result: review candidates and dataset_ready_for_training=false reason are visible.
8. KTR summary/report export üret.
   Expected result: demo_timeline, data_lab and safety evidence files are listed.
9. Logs’ta `demo.` and `data_lab.` eventlerini filtrele.
   Expected result: summaries explicitly state no physical command generated.
10. Final demo verdict’i göster.
    Expected result: release_demo_ready may be true, competition_ready remains false until production YOLO, real camera, Pico telemetry and self-test evidence exist.
11. Confirm no physical command generated.
    Expected result: timeline, logs and reports all show no_physical_command_generated=true.

Reports do not enable physical commands.
"""

    def _operation_checklist(self, runtime, request: ReportExportRequest) -> str:
        return """# Operation Checklist

## Pre-demo Checklist

- [ ] Backend running
- [ ] Frontend running
- [ ] Self-test completed
- [ ] NO_FIRE visible
- [ ] DRY RUN visible
- [ ] REAL HARDWARE DISABLED visible
- [ ] Hardware disabled confirmed
- [ ] NO_FIRE confirmed
- [ ] Camera stream checked
- [ ] Active model checked
- [ ] Dataset capture path checked
- [ ] Logs export checked

## Pre-field-test Checklist

- [ ] Approved pin profile
- [ ] E-stop physical verification
- [ ] Limit switch verification
- [ ] Pico local safety firmware review
- [ ] Serial ACK/NACK soak test

## Camera/Lens Checklist

- [ ] Lens profile selected
- [ ] FOV estimate reviewed
- [ ] Calibration warnings reviewed

## Model Loading Checklist

- [ ] Vision team model file received
- [ ] Class list received
- [ ] Input size received
- [ ] Confidence/IoU recommendation received

## Dataset Capture Checklist

- [ ] Target type tagged
- [ ] Team metadata tagged
- [ ] Distance tagged
- [ ] Lens/light/angle tagged

## Safety Checklist

- [ ] Fire request rejected by default
- [ ] Friend target rejected
- [ ] Unknown team rejected
- [ ] Balloon required
- [ ] Stable track required

## Pico/Pin Checklist

- [ ] ESTOP_IN present
- [ ] STEP/DIR output pins valid
- [ ] Servo PWM pin marked but physical trigger disabled

## Motion Dry-Run Checklist

- [ ] Jog dry-run only
- [ ] Out-of-limit rejected
- [ ] Stop accepted

## Known Limitations

- [ ] No production vision adapter yet
- [ ] No physical hardware enable in this phase
- [ ] No YOLO training in interface project
"""

    def _screenshots_manifest(self) -> list[dict]:
        screenshots_root = project_root() / "reports" / "screenshots"
        manifest = []
        for path in sorted(screenshots_root.rglob("*.png")):
            manifest.append(
                {
                    "file_path": str(path),
                    "page": path.stem,
                    "description": path.stem.replace("_", " "),
                    "created_at": path.stat().st_mtime,
                    "phase": path.parent.name,
                    "notes": "Generated screenshot evidence.",
                }
            )
        return manifest

    def _load_existing(self) -> None:
        known = {record.export_id for record in self.records}
        for metadata_path in sorted(self.root.glob("*/export_metadata.json")):
            try:
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                export_id = str(metadata["export_id"])
                if export_id in known:
                    continue
                self.records.append(
                    ReportExportRecord(
                        export_id=export_id,
                        kind=metadata["kind"],
                        status="completed",
                        created_at=float(metadata.get("created_at", metadata_path.stat().st_mtime)),
                        output_dir=str(metadata_path.parent),
                        files=metadata.get("files", []),
                        summary={"git_hash": metadata.get("git_hash"), "no_physical_command_generated": True},
                    )
                )
            except Exception:
                continue
        self.records.sort(key=lambda record: record.created_at)

    def _event(self, event_type: str, payload: dict, message: str) -> None:
        self.last_event = (event_type, payload)
        self.logger.emit(LogLevel.INFO, "REPORTS", message, payload)

    def _git_hash(self) -> str:
        try:
            result = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=project_root(), check=True, capture_output=True, text=True)
            return result.stdout.strip()
        except Exception:
            return "dev-local"
