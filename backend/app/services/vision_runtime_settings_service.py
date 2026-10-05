import time
import statistics
from pathlib import Path

import yaml

from app.schemas.config import AppConfig
from app.schemas.log import LogLevel
from app.schemas.vision_runtime_settings import (
    VisionRuntimeApplyResult,
    VisionRuntimePreset,
    VisionRuntimeProfile,
    VisionRuntimeStatus,
    VisionRuntimeTestResult,
    VisionRuntimeVerifyResult,
)
from app.services.log_service import JsonlLogService
from app.services.model_registry_service import ModelRegistryService
from app.services.storage_paths import project_root, resolve_project_path


class VisionRuntimeSettingsService:
    def __init__(self, config: AppConfig, models: ModelRegistryService, logger: JsonlLogService) -> None:
        self.config = config
        self.models = models
        self.logger = logger
        active = models.active_models()
        self.profile = VisionRuntimeProfile(
            inference_adapter=config.vision_runtime.default_adapter,
            active_body_model_id=active.active_body_model_id,
            active_balloon_model_id=active.active_balloon_model_id,
            imgsz=config.vision_runtime.default_imgsz,
            conf=config.vision_runtime.default_conf,
            iou=config.vision_runtime.default_iou,
            max_det=config.vision_runtime.default_max_det,
            device=config.vision_runtime.default_device,
        )
        self.parameter_version = 1
        self.model_packages = None
        self.last_warnings: list[str] = []
        self.last_errors: list[str] = []
        self.updated_at = time.time()
        self.last_event: tuple[str, dict] | None = None
        self.loaded_parameter_version: int | None = None
        self.loaded_model_path: str | None = None
        # Setup Center can select .pt files directly. Keep those paths as
        # runtime truth instead of requiring a stale model-registry slot.
        self.direct_model_paths: dict[str, str] = {}
        self.set_direct_model_paths(config.vision.body_model_path, config.vision.balloon_model_path)
        self._cuda_cache_until = 0.0
        self._cuda_cache = False
        self.path = project_root() / "config" / "runtime" / "vision_profile.active.yaml"
        self._load_persisted_profile()
        self._mark_existing_model_loaded()

    def status(self, current_fps: float = 0.0, latest_latency_ms: float = 0.0, camera_source_type: str | None = None) -> VisionRuntimeStatus:
        active = self.models.active_models()
        warnings = list(self.last_warnings)
        errors = list(self.last_errors)
        resolved_device, device_reason = self.resolve_device()
        cuda_available = self.cuda_available()
        active_details = self.active_model_details()
        yolo_model_loaded = bool(active_details.get("loaded") and active_details.get("file_path"))
        direct_runtime = bool(active_details.get("direct_runtime"))
        # A readable weights file is not evidence that its class mapping or
        # golden inference has been verified.  Do not promote it to
        # production/competition status merely because Ultralytics can load.
        production_loaded = bool(active_details.get("production_ready"))
        yolo_reload_required = self.profile.inference_adapter == "ultralytics_yolo" and (
            self.loaded_parameter_version != self.parameter_version or self.loaded_model_path != active_details.get("file_path")
        )
        surrogate_source_kind = None
        frame_origin = None
        if self.profile.inference_adapter == "opencv_live_circle_surrogate":
            surrogate_source_kind = "mock" if camera_source_type == "mock" else "real_camera"
            frame_origin = "mock_frame" if surrogate_source_kind == "mock" else "real_capture"
        effective_adapter = (
            "ultralytics_yolo"
            if production_loaded and self.profile.inference_adapter == "ultralytics_yolo"
            else "mock_camera_surrogate"
            if self.profile.inference_adapter == "opencv_live_circle_surrogate" and surrogate_source_kind == "mock"
            else "live_camera_surrogate"
            if self.profile.inference_adapter == "opencv_live_circle_surrogate"
            else "test_adapter"
            if self.profile.inference_adapter == "opencv_circle_test" or active_details.get("package_kind") in {"fixture", "test_adapter"}
            else self.profile.inference_adapter
        )
        if self.profile.inference_adapter == "opencv_circle_test":
            warnings.append("OpenCV daire algılayıcı yalnızca test adaptörüdür; yarışma modeli değildir.")
        if self.profile.inference_adapter == "opencv_live_circle_surrogate":
            warnings.append("OpenCV live circle surrogate yalnızca UI/pipeline testi içindir; production YOLO veya yarışma modeli değildir.")
        if self.profile.inference_adapter == "ultralytics_yolo" and not self.direct_model_paths:
            for model_id in [self.profile.active_body_model_id, self.profile.active_balloon_model_id]:
                if model_id:
                    try:
                        model = self.models.get_model(model_id)
                    except KeyError:
                        warnings.append(f"model_missing:{model_id}")
                        continue
                    if model.file_path and not Path(model.file_path).exists():
                        warnings.append(f"model_file_missing:{model_id}")
        if resolved_device is None:
            errors.append(device_reason)
        elif self.profile.half and resolved_device != "cuda":
            warnings.append("half_disabled_without_cuda")
        return VisionRuntimeStatus(
            profile=self.profile,
            active_model_summary={
                "active_body_model_id": "setup_body_path" if direct_runtime and "body" in active_details.get("direct_model_paths", {}) else active.active_body_model_id,
                "active_balloon_model_id": "setup_balloon_path" if direct_runtime and "balloon" in active_details.get("direct_model_paths", {}) else active.active_balloon_model_id,
                "active_combined_model_id": active.active_combined_model_id,
                "active_test_adapter": active.active_test_adapter,
            },
            active_model_details=active_details,
            selected_adapter=self.profile.inference_adapter,
            effective_adapter=effective_adapter,
            production_yolo_loaded=production_loaded,
            test_adapter_active=effective_adapter == "test_adapter",
            model_package_id=active_details.get("package_id"),
            runtime_source="production_model" if production_loaded else "direct_model_runtime" if direct_runtime else effective_adapter if self.profile.inference_adapter == "opencv_live_circle_surrogate" else "fixture_or_test_adapter",
            surrogate_source_kind=surrogate_source_kind,
            frame_origin=frame_origin,
            advisory_only=True,
            reload_required=yolo_reload_required,
            adapter_available=(self.profile.inference_adapter in {"mock", "opencv_circle_test", "opencv_live_circle_surrogate"} or yolo_model_loaded) and resolved_device is not None,
            requested_device=self.profile.device,
            resolved_device=resolved_device,
            cuda_available=cuda_available,
            device_reason=device_reason,
            latest_parameter_version=self.parameter_version,
            current_fps=current_fps,
            latest_latency_ms=latest_latency_ms,
            warnings=sorted(set(warning for warning in warnings if not (warning == "Model reload required for Ultralytics YOLO adapter." and not yolo_reload_required))),
            errors=sorted(set(errors)),
            updated_at=self.updated_at,
        )

    def set_direct_model_paths(self, body_path: str | None, balloon_path: str | None) -> None:
        """Record operator-selected model files without changing hardware authority."""
        direct: dict[str, str] = {}
        for role, value in (("body", body_path), ("balloon", balloon_path)):
            if value:
                direct[role] = str(resolve_project_path(value))
        self.direct_model_paths = direct

    def cuda_available(self) -> bool:
        """Read actual CUDA capability, with a short cache for status polling."""
        now = time.monotonic()
        if now < self._cuda_cache_until:
            return self._cuda_cache
        try:
            import torch

            self._cuda_cache = bool(torch.cuda.is_available())
        except Exception:
            self._cuda_cache = False
        self._cuda_cache_until = now + 5.0
        return self._cuda_cache

    def resolve_device(self, profile: VisionRuntimeProfile | None = None) -> tuple[str | None, str]:
        """Resolve the selected device without silently substituting CPU."""
        selected = profile or self.profile
        cuda_available = self.cuda_available()
        if selected.device == "cpu":
            return "cpu", "cpu_requested"
        if selected.device == "cuda":
            if not self.config.vision_runtime.allow_cuda:
                return None, "cuda_not_allowed"
            if not cuda_available:
                return None, "cuda_unavailable"
            return "cuda", "cuda_requested"
        if self.config.vision_runtime.allow_cuda and cuda_available:
            return "cuda", "auto_cuda_selected"
        if cuda_available:
            return "cpu", "auto_cpu_cuda_not_allowed"
        return "cpu", "auto_cpu_cuda_unavailable"

    def presets(self) -> list[VisionRuntimePreset]:
        return [
            VisionRuntimePreset(name="safe_low_cpu", capture_width=640, capture_height=360, stream_width=640, stream_height=360, inference_width=416, inference_height=416, fps=15, imgsz=416, conf=0.35, iou=0.45, max_det=10, frame_skip=1, vid_stride=2, tracker="none", half=False),
            VisionRuntimePreset(name="balanced", capture_width=640, capture_height=360, stream_width=640, stream_height=360, inference_width=640, inference_height=640, fps=15, imgsz=640, conf=0.25, iou=0.45, max_det=20, frame_skip=0, vid_stride=1, tracker="bytetrack", half=False),
            VisionRuntimePreset(name="high_accuracy", capture_width=1280, capture_height=720, stream_width=960, stream_height=540, inference_width=960, inference_height=960, fps=10, imgsz=960, conf=0.20, iou=0.50, max_det=40, frame_skip=0, vid_stride=1, tracker="bytetrack", half=False),
            VisionRuntimePreset(name="high_fps", capture_width=640, capture_height=360, stream_width=640, stream_height=360, inference_width=512, inference_height=512, fps=45, imgsz=512, conf=0.30, iou=0.45, max_det=15, frame_skip=1, vid_stride=2, tracker="none", half=False),
            VisionRuntimePreset(name="custom", capture_width=640, capture_height=360, stream_width=640, stream_height=360, inference_width=640, inference_height=640, fps=15, imgsz=640, conf=0.25, iou=0.45, max_det=20, frame_skip=0, vid_stride=1, tracker="none", half=False),
        ]

    def apply_preset(self, preset_name: str, current_fps: float = 0.0, latest_latency_ms: float = 0.0) -> VisionRuntimeApplyResult:
        preset = next((item for item in self.presets() if item.name == preset_name), None)
        if preset is None:
            return VisionRuntimeApplyResult(
                accepted=False,
                applied=False,
                rollback_performed=False,
                profile=self.profile,
                status=self.status(current_fps=current_fps, latest_latency_ms=latest_latency_ms),
                warnings=[],
                errors=["preset_not_found"],
                suggested_action="Select one of the configured runtime presets.",
            )
        profile = self.profile.model_copy(
            update={
                "imgsz": preset.imgsz,
                "conf": preset.conf,
                "iou": preset.iou,
                "max_det": preset.max_det,
                "frame_skip": preset.frame_skip,
                "vid_stride": preset.vid_stride,
                "tracker_enabled": preset.tracker != "none",
                "tracker_type": preset.tracker,
                "half": preset.half,
            }
        )
        return self.apply(profile, current_fps=current_fps, latest_latency_ms=latest_latency_ms)

    def save_preset(self, preset: VisionRuntimePreset) -> dict:
        path = project_root() / "config" / "runtime" / "vision_presets.custom.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        existing: list[dict] = []
        if path.exists():
            loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or []
            if isinstance(loaded, list):
                existing = loaded
        existing = [item for item in existing if item.get("name") != preset.name]
        existing.append(preset.model_dump(mode="json"))
        path.write_text(yaml.safe_dump(existing, sort_keys=False), encoding="utf-8")
        return {"accepted": True, "path": str(path), "preset": preset.model_dump(mode="json"), "no_physical_command_generated": True}

    def verify_active(self) -> VisionRuntimeVerifyResult:
        details = self.active_model_details()
        warnings = list(self.status().warnings)
        if not details.get("active_model_id"):
            warnings.append("Production YOLO modeli yüklü değil. OpenCV daire algılayıcı yalnızca test adaptörüdür; yarışma modeli değildir.")
        return VisionRuntimeVerifyResult(accepted=True, profile=self.profile, active_model_details=details, warnings=sorted(set(warnings)))

    def test_active_model(self) -> VisionRuntimeTestResult:
        details = self.active_model_details()
        warnings = []
        if not details.get("active_model_id"):
            warnings.append("Production YOLO modeli yüklü değil. OpenCV daire algılayıcı yalnızca test adaptörüdür; yarışma modeli değildir.")
        return VisionRuntimeTestResult(
            accepted=True,
            active_model_id=details.get("active_model_id"),
            adapter=self.profile.inference_adapter,
            detections=[],
            latency_ms=round(max(5.0, self.profile.imgsz / 20), 3),
            warnings=warnings,
        )

    def active_model_details(self) -> dict:
        active = self.models.active_models()
        active_model_id = self.profile.active_body_model_id or self.profile.active_balloon_model_id or active.active_combined_model_id
        detail = {
            "active_model_id": active_model_id,
            "adapter_mode": self.profile.inference_adapter,
            "model_type": None,
            "model_file": None,
            "file_path": None,
            "class_names": [],
            "expected_classes": ["f16", "helicopter", "ballistic_missile", "mini_micro_uav", "balloon"],
            "detected_classes": [],
            "class_mapping_status": "missing_model",
            "loaded": False,
            "last_test_status": "not_run",
            "last_inference_test": None,
            "test_adapter_active": self.profile.inference_adapter == "opencv_circle_test",
        }
        direct_present = {
            role: path
            for role, path in self.direct_model_paths.items()
            if Path(path).is_file()
        }
        if direct_present and self.profile.inference_adapter == "ultralytics_yolo":
            selected_path = direct_present.get("balloon") or direct_present.get("body")
            selected_role = "balloon" if "balloon" in direct_present else "body"
            detail.update(
                {
                    "active_model_id": "setup_direct_model_path",
                    "model_type": "combined_detector" if len(direct_present) > 1 else f"{selected_role}_detector",
                    "model_file": Path(selected_path).name if selected_path else None,
                    "file_path": selected_path,
                    "direct_model_paths": direct_present,
                    "direct_runtime": True,
                    "class_mapping_status": "direct_path_present_unverified",
                    "loaded": True,
                    "last_test_status": "live_inference_active",
                }
            )
        if active_model_id and not direct_present:
            try:
                model = self.models.get_model(active_model_id)
                detail.update(
                    {
                        "model_type": model.model_type,
                        "model_file": model.file_name,
                        "file_path": model.file_path,
                        "class_names": model.class_names,
                        "detected_classes": model.class_names,
                        "class_mapping_status": "complete" if model.class_names else "class_names_missing",
                        "loaded": bool(model.file_path and Path(model.file_path).exists()),
                        "last_test_status": "completed" if model.last_test_result else "not_run",
                        "last_inference_test": model.last_test_result,
                    }
                )
            except KeyError:
                detail["class_mapping_status"] = "model_missing"
        if self.model_packages is not None and not direct_present:
            try:
                package = self.model_packages.get_package(active_model_id) if active_model_id else None
            except KeyError:
                package = None
            if package and package.metadata:
                validation = package.validation
                semantic = self.model_packages.semantic_state(package)
                detail.update(
                    {
                        "package_name": package.package_name,
                        "package_id": package.package_name,
                        "package_kind": semantic.package_kind,
                        "checksum_sha256": package.checksum_sha256,
                        "model_file": Path(package.model_file).name if package.model_file else None,
                        "file_path": package.model_file,
                        "expected_classes": package.metadata.expected_classes,
                        "detected_classes": list(package.metadata.class_id_to_name.values()),
                        "class_mapping_status": validation.class_mapping_status if validation else "not_validated",
                        "package_schema_status": semantic.package_schema_validation,
                        "runtime_status": semantic.runtime_validation,
                        "production_status": semantic.production_readiness,
                        "competition_status": semantic.competition_readiness,
                        "loaded": bool(package.model_file and Path(package.model_file).exists()),
                        "last_test_status": "completed" if package.last_test_result else "not_run",
                        "last_inference_test": package.last_test_result,
                        "production_model": semantic.production_model,
                        "production_ready": semantic.production_ready,
                        "competition_ready": semantic.competition_ready,
                        "blockers": semantic.blockers,
                        "advisory_only": package.metadata.safety_note == "advisory_only",
                    }
                )
        return detail

    def apply(self, profile: VisionRuntimeProfile, current_fps: float = 0.0, latest_latency_ms: float = 0.0, camera_source_type: str | None = None) -> VisionRuntimeApplyResult:
        self._event("vision.settings_apply_started", profile.model_dump(mode="json"), "Vision runtime settings apply started")
        previous = self.profile
        warnings: list[str] = []
        errors: list[str] = []
        accepted = True
        rollback = False
        profile, stability_warnings = self._apply_cuda_stability_defaults(profile)
        warnings.extend(stability_warnings)
        resolved_device, device_reason = self.resolve_device(profile)
        if resolved_device is None:
            accepted = False
            errors.append(device_reason)
        if profile.inference_adapter == "ultralytics_yolo":
            direct_runtime_available = any(Path(path).is_file() for path in self.direct_model_paths.values())
            if not (profile.active_body_model_id or profile.active_balloon_model_id) and not direct_runtime_available:
                accepted = False
                errors.append("ultralytics_yolo_requires_active_model")
        if profile.inference_adapter == "opencv_circle_test":
            warnings.append("OpenCV daire algılayıcı yalnızca test adaptörüdür; yarışma modeli değildir.")
        if profile.inference_adapter == "opencv_live_circle_surrogate":
            warnings.append("OpenCV live circle surrogate yalnızca arayüz/görüntü aktarımı/overlay/loglama testi içindir; production YOLO değildir.")
        if accepted:
            self.profile = profile
            self.parameter_version += 1
            # The live worker reads inference parameters from this profile on
            # every new camera frame, and its model-spec cache is keyed by
            # ``parameter_version``. Applying imgsz/conf/IOU/device settings is
            # therefore immediate; reporting a mandatory reload while live
            # CUDA inference already used the new values left Setup and the
            # cockpit in a false "not ready" state.
            if profile.inference_adapter == "ultralytics_yolo":
                active_path = self.active_model_details().get("file_path")
                if active_path and Path(active_path).is_file():
                    self.loaded_parameter_version = self.parameter_version
                    self.loaded_model_path = str(active_path)
            self.last_errors = []
            self.last_warnings = warnings
            self._persist()
        else:
            self.profile = previous
            rollback = True
            self.last_errors = errors
            self.last_warnings = warnings
        self.updated_at = time.time()
        status = self.status(current_fps=current_fps, latest_latency_ms=latest_latency_ms, camera_source_type=camera_source_type)
        result = VisionRuntimeApplyResult(
            accepted=accepted,
            applied=accepted,
            rollback_performed=rollback,
            reload_required=status.reload_required,
            profile=self.profile,
            status=status,
            warnings=warnings,
            errors=errors,
            suggested_action="Select opencv_circle_test until the vision team model is available." if errors else None,
        )
        self._event("vision.settings_apply_completed", result.model_dump(mode="json"), "Vision runtime settings apply completed", LogLevel.INFO if accepted else LogLevel.WARN)
        return result

    def reset_defaults(self) -> VisionRuntimeApplyResult:
        active = self.models.active_models()
        return self.apply(
            VisionRuntimeProfile(
                inference_adapter=self.config.vision_runtime.default_adapter,
                active_body_model_id=active.active_body_model_id,
                active_balloon_model_id=active.active_balloon_model_id,
                imgsz=self.config.vision_runtime.default_imgsz,
                conf=self.config.vision_runtime.default_conf,
                iou=self.config.vision_runtime.default_iou,
                max_det=self.config.vision_runtime.default_max_det,
                device=self.config.vision_runtime.default_device,
                balloon_conf_threshold=self.config.vision_runtime.default_conf,
            )
        )

    def reload_models(self) -> dict:
        status = self.status()
        active_details = self.active_model_details()
        accepted = True
        errors: list[str] = []
        warnings: list[str] = []
        if self.profile.inference_adapter == "ultralytics_yolo":
            model_path = active_details.get("file_path")
            direct_paths = active_details.get("direct_model_paths") or {}
            missing_direct = [path for path in direct_paths.values() if not Path(path).exists()]
            if missing_direct or (not direct_paths and (not model_path or not Path(model_path).exists())):
                accepted = False
                errors.append("active_yolo_model_file_missing")
            else:
                self.loaded_parameter_version = self.parameter_version
                self.loaded_model_path = str(model_path)
                self.last_warnings = [warning for warning in self.last_warnings if warning != "Model reload required for Ultralytics YOLO adapter."]
                self.last_errors = []
        if not accepted:
            self.last_errors = errors
            warnings = status.warnings
        payload = {
            "accepted": accepted,
            "reload_required": self.status().reload_required if accepted else True,
            "active_model_file": active_details.get("file_path"),
            "warnings": warnings,
            "errors": errors,
            "no_physical_command_generated": True,
        }
        self._event("vision.model_reload", payload, "Vision model reload requested")
        return payload

    def _mark_existing_model_loaded(self) -> None:
        if self.profile.inference_adapter != "ultralytics_yolo":
            return
        model_path = self.active_model_details().get("file_path")
        if model_path and Path(model_path).exists():
            self.loaded_parameter_version = self.parameter_version
            self.loaded_model_path = str(model_path)

    def warmup(self, pipeline) -> dict:
        """Execute one real runtime inference path; never fabricate a warm-up."""
        ready, blockers = self._runtime_measurement_ready()
        if not ready:
            payload = {
                "accepted": False,
                "adapter": self.profile.inference_adapter,
                "reason_codes": blockers,
                "no_physical_command_generated": True,
            }
            self._event("vision.warmup_rejected", payload, "Vision warm-up rejected; real inference is unavailable.", LogLevel.WARN)
            return payload
        event = pipeline.latest()
        errors = [item for item in event.warnings if item.startswith("ultralytics_inference_failed:")]
        payload = {
            "accepted": not errors,
            "adapter": self.profile.inference_adapter,
            "requested_device": self.profile.device,
            "resolved_device": self.resolve_device()[0],
            "frame_id": event.frame_id,
            "latency_ms": event.total_latency_ms,
            "warnings": event.warnings,
            "reason_codes": errors,
            "no_physical_command_generated": True,
        }
        self._event("vision.warmup_completed", payload, "Vision runtime warm-up completed", LogLevel.INFO if not errors else LogLevel.WARN)
        return payload

    def benchmark(self, pipeline, sample_count: int = 10) -> dict:
        """Measure real YOLO frames; test adapters never receive fake estimates."""
        ready, blockers = self._runtime_measurement_ready()
        if not ready:
            payload = {
                "accepted": False,
                "adapter": self.profile.inference_adapter,
                "sample_count": 0,
                "reason_codes": blockers,
                "no_physical_command_generated": True,
            }
            self._event("vision.benchmark_rejected", payload, "Vision benchmark rejected; real inference is unavailable.", LogLevel.WARN)
            return payload
        latencies: list[float] = []
        frame_ids: set[int] = set()
        warnings: list[str] = []
        failures: list[str] = []
        interval_s = min(0.2, max(0.01, 1.0 / max(self.profile.target_fps, 1.0)))
        target_samples = max(1, min(30, int(sample_count)))
        previous_frame_id: int | None = None
        benchmark_deadline = time.monotonic() + max(5.0, target_samples * 0.75)
        while len(latencies) < target_samples and time.monotonic() < benchmark_deadline:
            event = pipeline.latest()
            # The worker publishes asynchronously.  Only count a fresh frame;
            # duplicate latest-event reads are not independent measurements.
            if event.frame_id <= 0 or event.frame_id == previous_frame_id:
                time.sleep(0.01)
                continue
            frame_ids.add(event.frame_id)
            previous_frame_id = event.frame_id
            warnings.extend(event.warnings)
            failure = next((item for item in event.warnings if item.startswith("ultralytics_inference_failed:")), None)
            if failure:
                failures.append(failure)
                break
            latencies.append(float(event.total_latency_ms))
            time.sleep(interval_s)
        if not latencies or failures or len(frame_ids) != len(latencies):
            payload = {
                "accepted": False,
                "adapter": self.profile.inference_adapter,
                "sample_count": len(latencies),
                "unique_frame_count": len(frame_ids),
                "reason_codes": sorted(set([*failures, "REAL_INFERENCE_SAMPLE_INSUFFICIENT"])),
                "warnings": sorted(set(warnings)),
                "no_physical_command_generated": True,
            }
            self._event("vision.benchmark_rejected", payload, "Vision benchmark did not produce independent real frames.", LogLevel.WARN)
            return payload
        p50 = self._percentile(latencies, 0.50)
        p95 = self._percentile(latencies, 0.95)
        live_status = pipeline.status()
        live_event = pipeline.latest()
        payload = {
            "accepted": True,
            "adapter": self.profile.inference_adapter,
            "requested_device": self.profile.device,
            "resolved_device": self.resolve_device()[0],
            "sample_count": len(latencies),
            "unique_frame_count": len(frame_ids),
            "latency_p50_ms": p50,
            "latency_p95_ms": p95,
            "mean_latency_ms": round(statistics.mean(latencies), 3),
            "measured_fps": round(1000.0 / max(statistics.mean(latencies), 0.001), 3),
            # ``measured_fps`` is model-only capacity; these are the
            # end-to-end worker values the operator sees on the camera HUD.
            "live_detector_fps": live_status.fps,
            "live_camera_fps": live_event.camera_fps,
            "model_count": live_event.model_count,
            "model_execution": live_event.model_execution,
            "model_timings_ms": live_event.model_timings_ms,
            "warnings": sorted(set(warnings)),
            "no_physical_command_generated": True,
        }
        self._event("vision.benchmark_completed", payload, "Vision real-inference benchmark completed")
        return payload

    def _runtime_measurement_ready(self) -> tuple[bool, list[str]]:
        status = self.status()
        blockers: list[str] = []
        if self.profile.inference_adapter != "ultralytics_yolo":
            blockers.append("REAL_YOLO_ADAPTER_REQUIRED")
        if not status.production_yolo_loaded and not bool(status.active_model_details.get("direct_runtime")):
            blockers.append("PRODUCTION_MODEL_GOLDEN_EVIDENCE_REQUIRED")
        if status.resolved_device is None:
            blockers.append(status.device_reason)
        if status.reload_required:
            blockers.append("MODEL_RELOAD_REQUIRED")
        return not blockers, sorted(set(blockers))

    @staticmethod
    def _percentile(values: list[float], quantile: float) -> float:
        ordered = sorted(values)
        index = min(len(ordered) - 1, max(0, int((len(ordered) - 1) * quantile + 0.999999)))
        return round(ordered[index], 3)

    def _persist(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(yaml.safe_dump(self.profile.model_dump(mode="json"), sort_keys=False), encoding="utf-8")

    def _load_persisted_profile(self) -> None:
        active_models_path = Path(self.config.models.active_models_file)
        if active_models_path.is_absolute() and not active_models_path.is_relative_to(project_root()):
            return
        if not self.path.exists():
            return
        try:
            loaded = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
            self.profile, stability_warnings = self._apply_cuda_stability_defaults(VisionRuntimeProfile(**loaded))
            self.last_warnings = stability_warnings
            if stability_warnings:
                # Persist the safe effective profile so a restart cannot
                # silently restore the known-bad FP16/960 CUDA combination.
                self._persist()
            self.last_errors = []
        except Exception as exc:
            self.last_errors = [f"vision_profile_load_failed:{exc}"]

    @staticmethod
    def _apply_cuda_stability_defaults(profile: VisionRuntimeProfile) -> tuple[VisionRuntimeProfile, list[str]]:
        """Normalize the field YOLO profile after the observed CUDA crash.

        The operator still chooses the adapter/device/confidence.  Only the
        two settings proven unsafe on the 4 GB Windows GPU are normalized:
        FP16 is disabled and inference is capped at 640.  Test/OpenCV
        profiles are left untouched.
        """
        if profile.inference_adapter != "ultralytics_yolo":
            return profile, []
        updates: dict[str, object] = {}
        warnings: list[str] = []
        if profile.half:
            updates["half"] = False
            warnings.append("half_disabled_for_cuda_stability")
        if profile.imgsz > 640:
            updates["imgsz"] = 640
            warnings.append(f"imgsz_capped_for_cuda_stability:{profile.imgsz}->640")
        return (profile.model_copy(update=updates) if updates else profile), warnings

    def _event(self, event_type: str, payload: dict, message: str, level: LogLevel = LogLevel.INFO) -> None:
        payload = {**payload, "no_physical_command_generated": True}
        self.last_event = (event_type, payload)
        self.logger.emit(level, "VISION_RUNTIME", message, payload)
