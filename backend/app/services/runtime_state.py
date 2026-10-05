import time
from pathlib import Path

from app.schemas.config import AppConfig
from app.schemas.log import LogLevel
from app.schemas.system import SystemState
from app.services.auto_tracker_service import AutoTrackerService
from app.services.calibration_service import CalibrationService
from app.services.camera_service import CameraService
from app.services.camera_host_diagnostic_service import CameraHostDiagnosticService
from app.services.camera_runtime_service import CameraRuntimeService
from app.services.color_classifier_service import ColorClassifierService
from app.services.command_gateway import CommandGateway
from app.services.body_balloon_association_service import BodyBalloonAssociationService
from app.services.annotation_service import AnnotationService
from app.services.dataset_service import DatasetService
from app.services.data_lab_service import DataLabService
from app.services.decision_engine import DecisionEngine
from app.services.demo_timeline_service import DemoTimelineService
from app.services.digital_twin_service import DigitalTwinService
from app.services.ballistics_calibration_service import BallisticsCalibrationService
from app.services.engagement_evidence_service import EngagementEvidenceService
from app.services.inference_adapter_service import InferenceAdapterService
from app.services.hardware_service import HardwareDiscoveryService
from app.services.device_manager_service import DeviceManagerService
from app.services.device_profile_service import DeviceProfileService
from app.services.first_run_service import FirstRunService
from app.services.interface_inventory_service import InterfaceInventoryService
from app.services.legacy_perception_service import LegacyPerceptionService
from app.services.log_service import JsonlLogService
from app.services.model_registry_service import ModelRegistryService
from app.services.model_package_service import ModelPackageService
from app.services.model_upload_service import ModelUploadService
from app.services.mission_service import MissionService
from app.services.motion_service import MotionService
from app.services.opencv_live_circle_surrogate import OpenCVLiveCircleSurrogate
from app.services.operation_service import OperationService
from app.services.patrol_radar_service import PatrolRadarService
from app.services.person_safety_gate_service import PersonSafetyGateService
from app.services.pico_service import PicoService
from app.services.performance_service import PerformanceService
from app.services.replay_service import ReplayService
from app.services.report_export_service import ReportExportService
from app.services.release_service import ReleaseService
from app.services.session_service import SessionService
from app.services.serial_service import SerialService
from app.services.self_test_service import SelfTestService
from app.services.ktr_export_service import KtrExportService
from app.services.safety_service import SafetyService
from app.services.safety_zone_service import SafetyZoneProfileService
from app.services.stage3_range_calibration_service import Stage3RangeCalibrationService
from app.services.stage2_engagement_service import Stage2EngagementService
from app.services.stage3_engagement_service import Stage3EngagementService
from app.services.storage_paths import project_root
from app.services.turret_service import TurretService
from app.services.tracking_loop import TrackingLoop
from app.services.tracking_tuning_service import TrackingTuningService
from app.services.target_priority_service import TargetPriorityService
from app.services.hit_confirmation_service import HitConfirmationService
from app.services.target_engagement_registry import TargetEngagementRegistry
from app.services.vision_pipeline import VisionPipeline
from app.services.vision_service import VisionService
from app.services.vision_runtime_settings_service import VisionRuntimeSettingsService


class RuntimeState:
    def __init__(self, config: AppConfig, logger: JsonlLogService, report_dir: Path | None = None) -> None:
        self.started_at = time.monotonic()
        self.config = config
        self.logger = logger
        self.force_armed = False
        self.last_safety_event: tuple[str, dict] | None = None
        self.last_motion_event: tuple[str, dict] | None = None
        self.hardware = HardwareDiscoveryService(config=config, logger=logger)
        self.device_manager = DeviceManagerService(config=config, logger=logger)
        self.device_profiles = DeviceProfileService(logger=logger)
        self.pico = PicoService(config=config, logger=logger)
        shot_budget_path = (report_dir.parent / "shot_budget.active.json") if report_dir is not None else None
        self.serial = SerialService(
            config=config,
            logger=logger,
            magazine_state_path=shot_budget_path or (project_root() / "config" / "runtime" / "shot_budget.active.json"),
        )
        self.performance = PerformanceService()
        mission_state_path = (report_dir.parent / "mission_state.active.json") if report_dir is not None else None
        self.mission = MissionService(path=mission_state_path)
        self.camera = CameraService(config=config)
        self.camera_host = CameraHostDiagnosticService(logger=logger)
        self.camera_runtime = CameraRuntimeService(config=config, devices=self.device_manager, logger=logger)
        self.vision = VisionService(config=config)
        self.vision_pipeline = VisionPipeline(camera=self.camera, vision=self.vision)
        self.vision_surrogate = OpenCVLiveCircleSurrogate(logger=logger)
        self.legacy_perception = LegacyPerceptionService(logger=logger)
        self.calibration = CalibrationService(config=config, logger=logger)
        color_calibration_path = (report_dir.parent / "iff_color_calibration.active.json") if report_dir is not None else None
        self.color_classifier = ColorClassifierService(
            config=config,
            logger=logger,
            calibration_path=color_calibration_path or (project_root() / "config" / "runtime" / "iff_color_calibration.active.json"),
        )
        self.motion = MotionService(config=config, logger=logger)
        self.operation = OperationService()
        safety_zone_path = (report_dir.parent / "safety_zones.active.json") if report_dir is not None else None
        self.safety_zones = SafetyZoneProfileService(
            config=config,
            logger=logger,
            path=safety_zone_path or (project_root() / "config" / "runtime" / "safety_zones.active.json"),
        )
        self.turret = TurretService(motion=self.motion)
        self.decision_engine = DecisionEngine(config=config, logger=logger)
        motion_envelope_path = (report_dir.parent / "motion_envelope.active.json") if report_dir is not None else None
        self.command_gateway = CommandGateway(serial=self.serial, logger=logger, motion_envelope_path=motion_envelope_path)
        self.safety = SafetyService(config=config, logger=logger)
        self.person_safety = PersonSafetyGateService(config=config, logger=logger)
        ballistics_path = (report_dir.parent / "ballistics_calibration.json") if report_dir is not None else (project_root() / "config" / "runtime" / "ballistics_calibration.json")
        self.ballistics = BallisticsCalibrationService(persistence_path=ballistics_path)
        self.auto_tracker = AutoTrackerService(config=config, logger=logger, ballistics=self.ballistics)
        tuning_path = (report_dir.parent / "tracking_tuning_trials.json") if report_dir is not None else (project_root() / "reports" / "tracking_tuning_trials.json")
        self.tracking_tuning = TrackingTuningService(path=tuning_path)
        self.association = BodyBalloonAssociationService()
        self.target_priority = TargetPriorityService()
        self.hit_confirmation = HitConfirmationService()
        # Logical target identity is mission-scoped.  It is deliberately
        # separate from frame-local detector ids and never emits hardware.
        self.target_registry = TargetEngagementRegistry(
            min_association_duration_s=1.0,
            dropout_grace_s=0.75,
        )
        evidence_root = (report_dir.parent / "engagement_evidence") if report_dir is not None else (project_root() / "reports" / "engagement_evidence")
        self.engagement_evidence = EngagementEvidenceService(logger=logger, root=evidence_root)
        self.stage2_engagement = Stage2EngagementService()
        self.stage3_engagement = Stage3EngagementService()
        self.patrol_radar = PatrolRadarService()
        self.tracking_loop = TrackingLoop(
            auto_tracker=self.auto_tracker,
            vision_pipeline=self.vision_pipeline,
            serial=self.serial,
            gateway=self.command_gateway,
            logger=logger,
            frame_width=config.camera.width,
            frame_height=config.camera.height,
            interval_ms=1000.0 / config.tracking.command_rate_hz,
            tuning=self.tracking_tuning,
        )
        self.model_registry = ModelRegistryService(config=config, logger=logger)
        self.model_packages = ModelPackageService(config=config, registry=self.model_registry, logger=logger)
        self.vision_runtime = VisionRuntimeSettingsService(config=config, models=self.model_registry, logger=logger)
        self.vision_runtime.model_packages = self.model_packages
        self.vision_pipeline.balloon_tracker = self.auto_tracker.multi_target_tracker
        try:
            self.vision_pipeline.configure_tracker(
                self.vision_runtime.profile.tracker_type,
                enabled=self.vision_runtime.profile.tracker_enabled,
            )
        except Exception as exc:
            # A missing optional tracker dependency must not prevent the
            # cockpit from starting. Fall back to the known production custom
            # tracker and surface the reason in runtime warnings.
            self.vision_pipeline.configure_tracker("current_custom", enabled=True)
            self.vision_runtime.last_warnings.append(f"tracker_startup_fallback:{exc}")
        stage3_range_path = (report_dir.parent / "stage3_range_calibration.active.json") if report_dir is not None else None
        self.stage3_range = Stage3RangeCalibrationService(
            logger=logger,
            path=stage3_range_path or (project_root() / "config" / "runtime" / "stage3_range_calibration.active.json"),
        )
        self.vision_pipeline.camera_runtime = self.camera_runtime
        self.vision_pipeline.vision_runtime = self.vision_runtime
        self.vision_pipeline.operation = self.operation
        self.vision_pipeline.surrogate = self.vision_surrogate
        self.vision_pipeline.color_classifier = self.color_classifier
        self.vision_pipeline.stage3_range = self.stage3_range
        self.model_upload = ModelUploadService(registry=self.model_registry)
        self.inference_adapter = InferenceAdapterService(registry=self.model_registry, logger=logger)
        self.sessions = SessionService(config=config, logger=logger)
        self.annotations = AnnotationService(sessions=self.sessions, logger=logger)
        self.dataset = DatasetService(
            config=config,
            sessions=self.sessions,
            annotations=self.annotations,
            models=self.model_registry,
            logger=logger,
        )
        self.data_lab = DataLabService(sessions=self.sessions, logger=logger)
        self.demo = DemoTimelineService(logger=logger)
        self.replay = ReplayService(sessions=self.sessions, logger=logger)
        self.self_test = SelfTestService(logger=logger, report_dir=report_dir)
        self.interface_inventory = InterfaceInventoryService(logger=logger)
        self.first_run = FirstRunService(logger=logger)
        self.release = ReleaseService(logger=logger)
        self.report_export = ReportExportService(config=config, logger=logger)
        self.ktr_export = KtrExportService(reports=self.report_export)
        self.digital_twin = DigitalTwinService(config=config, logger=logger, reports_dir=report_dir)
        self.command_gateway.bind_runtime(self)
        self.logger.emit(
            LogLevel.INFO,
            "SYSTEM",
            "Runtime initialized with safe defaults",
            self.system_state().model_dump(mode="json"),
        )

    def uptime_s(self) -> float:
        return round(time.monotonic() - self.started_at, 3)

    def system_state(self) -> SystemState:
        mode = "STANDBY" if self.force_armed else self.config.system.mode
        blocking_reasons: list[str] = []
        if mode == "DISARMED":
            blocking_reasons.append("system_disarmed")
        return SystemState(
            mode=mode,
            armed=self.force_armed,
            fire_policy=self.config.system.default_fire_policy,
            dry_run=self.config.system.dry_run,
            hardware_enabled=self.config.system.hardware_enabled,
            ready=False,
            uptime_s=self.uptime_s(),
            blocking_reasons=blocking_reasons,
        )

    def stage3_competition_profile_locked(self) -> bool:
        """Perception/calibration profile is immutable during an A3 run."""
        return bool(
            self.command_gateway.profile.value == "COMPETITION"
            and self.mission.state.active_stage == "stage3"
        )


def build_runtime(config: AppConfig, log_dir: Path, report_dir: Path | None = None) -> RuntimeState:
    return RuntimeState(config=config, logger=JsonlLogService(log_dir), report_dir=report_dir)
