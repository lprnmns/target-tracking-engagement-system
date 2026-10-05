from app.mocks.mock_vision import MockVisionGenerator
from app.schemas.config import AppConfig
from app.schemas.vision import VisionConfigUpdate, VisionEvent, VisionStatus
from app.services.storage_paths import resolve_project_path


class VisionService:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.vision_mode = config.vision.vision_mode
        self.body_model_path = config.vision.body_model_path
        self.balloon_model_path = config.vision.balloon_model_path
        self.body_conf_threshold = config.vision.body_conf_threshold
        self.balloon_conf_threshold = config.vision.balloon_conf_threshold
        self.generator = MockVisionGenerator()
        self.running = False
        self.latest_event: VisionEvent | None = None
        self.model_warnings = self._model_warnings()

    def configure(self, update: VisionConfigUpdate) -> VisionStatus:
        self.vision_mode = update.vision_mode
        self.body_model_path = update.body_model_path
        self.balloon_model_path = update.balloon_model_path
        self.body_conf_threshold = update.body_conf_threshold
        self.balloon_conf_threshold = update.balloon_conf_threshold
        self.model_warnings = self._model_warnings()
        return self.status()

    def next_event(self, source: str, width: int, height: int) -> VisionEvent:
        event = self.generator.next_event(source=source, width=width, height=height)
        event.warnings.extend(self.model_warnings)
        self.latest_event = event
        return event

    def status(self) -> VisionStatus:
        latest = self.latest_event
        active_latest = latest if self.running else None
        return VisionStatus(
            running=self.running,
            vision_mode=self.vision_mode,
            model_loading_required=self.config.vision.model_loading_required,
            body_model_path=self.body_model_path,
            balloon_model_path=self.balloon_model_path,
            body_model_loaded=self.body_model_path is not None and resolve_project_path(self.body_model_path).exists(),
            balloon_model_loaded=self.balloon_model_path is not None and resolve_project_path(self.balloon_model_path).exists(),
            fps=active_latest.fps if active_latest else 0.0,
            camera_fps=active_latest.camera_fps if active_latest else None,
            detector_fps=active_latest.detector_fps if active_latest else None,
            latest_frame_id=active_latest.frame_id if active_latest else 0,
            latest_latency_ms=active_latest.total_latency_ms if active_latest else 0.0,
            latest_total_ms=active_latest.total_ms if active_latest else None,
            camera_source_kind=active_latest.camera_source_kind if active_latest else None,
            frame_origin=active_latest.frame_origin if active_latest else None,
            detector_kind=active_latest.detector_kind if active_latest else None,
            body_count=len(active_latest.body_detections) if active_latest else 0,
            balloon_count=len(active_latest.balloon_detections) if active_latest else 0,
            model_count=active_latest.model_count if active_latest else 0,
            model_execution=active_latest.model_execution if active_latest else "none",
            model_timings_ms=dict(active_latest.model_timings_ms) if active_latest else {},
            warnings=(active_latest.warnings if active_latest else self.model_warnings),
        )

    def _model_warnings(self) -> list[str]:
        warnings: list[str] = []
        if self.body_model_path and not resolve_project_path(self.body_model_path).exists():
            warnings.append("body_model_path_not_found")
        if self.balloon_model_path and not resolve_project_path(self.balloon_model_path).exists():
            warnings.append("balloon_model_path_not_found")
        if self.vision_mode == "yolo" and not self.config.vision.model_loading_required:
            warnings.append("yolo_optional_interface_only")
        return warnings
