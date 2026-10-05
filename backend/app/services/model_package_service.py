import hashlib
import json
import shutil
import time
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory

from app.schemas.config import AppConfig
from app.schemas.log import LogLevel
from app.schemas.model_package import (
    REQUIRED_COMPETITION_CLASSES,
    ClassMappingReviewItem,
    ActiveModelSemanticState,
    ModelPackageActivateRequest,
    ModelPackageBenchmarkResult,
    ModelPackageImportRequest,
    ModelPackageMetadata,
    ModelPackageRecord,
    ModelPackageTestRequest,
    ModelPackageTestResult,
    ModelPackageThresholds,
    ModelPackageValidationResult,
    RecommendedSettingsApplyResult,
)
from app.schemas.model_registry import ModelActivationRequest, ModelMetadata
from app.schemas.vision_runtime_settings import VisionRuntimeProfile
from app.services.log_service import JsonlLogService
from app.services.model_registry_service import ModelRegistryService
from app.services.storage_paths import project_root, resolve_project_path


ALLOWED_PACKAGE_EXTENSIONS = {".pt", ".onnx", ".engine", ".yaml", ".yml", ".json", ".md", ".jpg", ".jpeg", ".png", ".txt"}


class ModelPackageService:
    def __init__(self, config: AppConfig, registry: ModelRegistryService, logger: JsonlLogService) -> None:
        self.config = config
        self.registry = registry
        self.logger = logger
        self.incoming_dir = resolve_project_path(config.models.root_dir) / "incoming"
        self.package_dir = resolve_project_path(config.models.root_dir) / "packages"
        self.state_dir = self.package_dir / "_state"
        self.registry_path = self.state_dir / "model_packages.json"
        self.last_event: tuple[str, dict] | None = None
        self.incoming_dir.mkdir(parents=True, exist_ok=True)
        self.package_dir.mkdir(parents=True, exist_ok=True)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        if not self.registry_path.exists():
            self._write_records([])

    def list_packages(self) -> list[ModelPackageRecord]:
        return sorted(self._read_records(), key=lambda item: item.imported_at, reverse=True)

    def get_package(self, model_id: str) -> ModelPackageRecord:
        package = self._find(model_id)
        if package is None:
            raise KeyError(model_id)
        return package

    def import_package(self, request: ModelPackageImportRequest) -> ModelPackageRecord:
        source = self._resolve_source(request)
        self._event("model.package_import_started", {"source": str(source)}, "Model package import started")
        if source.suffix.lower() == ".zip":
            with TemporaryDirectory() as temp_dir:
                extracted = Path(temp_dir) / "package"
                self._safe_extract(source, extracted)
                package = self._import_directory(extracted)
        else:
            package = self._import_directory(source)
        existing = self._find(package.model_id)
        if existing and existing.version == package.version:
            package.warnings.append("same_model_id_version_already_imported")
        records = [item for item in self._read_records() if not (item.model_id == package.model_id and item.version == package.version)]
        records.append(package)
        self._write_records(records)
        self._event("model.package_import_completed", package.model_dump(mode="json"), "Model package import completed")
        return package

    def validate_package(self, model_id: str) -> ModelPackageValidationResult:
        package = self.get_package(model_id)
        result = self._validate_record(package)
        status = "validated" if result.valid else "missing_file" if not result.checks.get("model_file_present", False) else "invalid"
        package = package.model_copy(update={"validation": result, "status": status, "warnings": sorted(set(package.warnings + result.warnings))})
        self._replace(package)
        event_type = "model.package_validation_passed" if result.valid else "model.package_validation_failed"
        summary = "Model package schema validation passed." if result.valid else "Model package schema validation failed."
        semantic = self.semantic_state(package)
        self._event(
            event_type,
            {
                **result.model_dump(mode="json"),
                "package_kind": semantic.package_kind,
                "production_ready": semantic.production_ready,
                "competition_ready": semantic.competition_ready,
            },
            summary,
        )
        return result

    def activate_package(self, model_id: str, request: ModelPackageActivateRequest) -> dict:
        package = self.get_package(model_id)
        validation = package.validation or self.validate_package(model_id)
        if not validation.valid or not validation.can_activate:
            raise ValueError("model package validation must pass before activation")
        metadata = package.metadata
        if metadata is None:
            raise ValueError("model package metadata is missing")
        model = ModelMetadata(
            model_id=metadata.model_id,
            name=metadata.model_name,
            version=metadata.version,
            model_type="combined_detector" if request.slot == "combined" else f"{request.slot}_detector",  # type: ignore[arg-type]
            framework="ultralytics" if metadata.model_format == "pt" else "onnx" if metadata.model_format == "onnx" else "external_adapter",
            file_path=package.model_file,
            file_name=Path(package.model_file).name if package.model_file else None,
            file_size_bytes=Path(package.model_file).stat().st_size if package.model_file and Path(package.model_file).exists() else 0,
            class_names=list(metadata.class_id_to_name.values()),
            input_size=metadata.input_size,
            confidence_threshold=metadata.recommended_conf,
            iou_threshold=metadata.recommended_iou,
            status="validated",
            provided_by="vision_team" if metadata.provided_by == "vision_team" else "imported",
            notes=f"Imported from model package {package.package_name}. Advisory only.",
            warnings=sorted(set(package.warnings + validation.warnings + (["fixture/test package; not production"] if not metadata.production_ready else []))),
        )
        self.registry.upsert_model(model)
        active = self.registry.activate(model.model_id, ModelActivationRequest(slot=request.slot))
        package = package.model_copy(update={"active": True, "status": "active", "activated_at": time.time()})
        self._replace_active(package)
        semantic = self.semantic_state(package)
        if semantic.package_kind in {"fixture", "test_adapter"}:
            summary = "Test adapter activated; production readiness remains blocked."
        elif semantic.package_kind == "production":
            summary = "Production model activated."
        else:
            summary = "Model activated; production status requires validation."
        self._event(
            "model.activated",
            {
                "package": package.model_dump(mode="json"),
                "active": active.model_dump(mode="json"),
                "semantic_state": semantic.model_dump(mode="json"),
                "package_kind": semantic.package_kind,
                "production_ready": semantic.production_ready,
                "competition_ready": semantic.competition_ready,
            },
            summary,
        )
        return {"active": active, "package": package, "no_physical_command_generated": True}

    def deactivate_package(self, model_id: str) -> dict:
        package = self.get_package(model_id)
        active = self.registry.deactivate(model_id)
        package = package.model_copy(update={"active": False, "status": "inactive"})
        self._replace(package)
        self._event("model.deactivated", {"model_id": model_id, "active": active.model_dump(mode="json"), "production_ready": False, "competition_ready": False}, "Active model deactivated; vision falls back to no production model.")
        return {"active": active, "package": package, "no_physical_command_generated": True}

    def test_package(self, model_id: str, request: ModelPackageTestRequest) -> ModelPackageTestResult:
        package = self.get_package(model_id)
        self._event("model.test_started", {"model_id": model_id, "source": request.source}, "Model package test started")
        validation = package.validation or self.validate_package(model_id)
        warnings = list(validation.warnings)
        errors = [] if validation.valid else list(validation.errors)
        detections = []
        accepted = validation.valid
        evidence_kind = "not_executed"
        class_mapping_verified = False
        if validation.valid and package.metadata and package.metadata.production_ready:
            accepted, detections, golden_warnings, golden_errors = self._run_production_golden_inference(package)
            warnings.extend(golden_warnings)
            errors.extend(golden_errors)
            evidence_kind = "golden_inference" if accepted else "not_executed"
            class_mapping_verified = accepted
        elif validation.valid:
            detections = [
                {
                    "detection_id": "pkg-test-1",
                    "class_id": 4,
                    "class_name": "balloon",
                    "confidence": round(max(package.thresholds.default_conf if package.thresholds else 0.35, 0.35), 2),
                    "bbox_xyxy_pixel": [260, 120, 380, 240],
                    "bbox_xywh_pixel": [260, 120, 120, 120],
                    "bbox_yolo_normalized": [0.5, 0.5, 0.1875, 0.3333],
                    "source": "model_package_dry_run",
                    "is_balloon": True,
                }
            ]
            evidence_kind = "fixture_synthetic"
        result = ModelPackageTestResult(
            model_id=model_id,
            accepted=accepted,
            source=request.source,
            detections=detections,
            latency_ms=round(max(8.0, (package.metadata.recommended_imgsz if package.metadata else 640) / 18), 3),
            warnings=warnings,
            errors=errors,
            evidence_kind=evidence_kind,
            class_mapping_verified=class_mapping_verified,
        )
        package = package.model_copy(update={"last_test_result": result.model_dump(mode="json")})
        self._replace(package)
        try:
            self.registry.record_test_result(model_id, result.model_dump(mode="json"))
        except KeyError:
            pass
        semantic = self.semantic_state(package)
        self._event(
            "model.test_completed",
            {
                **result.model_dump(mode="json"),
                "package_kind": semantic.package_kind,
                "production_ready": semantic.production_ready,
                "competition_ready": semantic.competition_ready,
            },
            "Model dry-run test completed; no physical command generated.",
        )
        self._event("model.safety_no_physical_command_verified", {"model_id": model_id, "no_physical_command_generated": True}, "Model test verified no physical command")
        return result

    def benchmark_package(self, model_id: str) -> ModelPackageBenchmarkResult:
        package = self.get_package(model_id)
        imgsz = package.metadata.recommended_imgsz if package.metadata else 640
        latency = round(max(10.0, imgsz / 14), 3)
        result = ModelPackageBenchmarkResult(
            model_id=model_id,
            accepted=True,
            estimated_fps=round(1000.0 / latency, 2),
            estimated_latency_ms=latency,
            device=package.metadata.recommended_device if package.metadata else "cpu",
            warnings=["dry-run benchmark; no production inference engine executed"] if not (package.metadata and package.metadata.production_ready) else ["advisory dry-run benchmark"],
        )
        package = package.model_copy(update={"last_benchmark_result": result.model_dump(mode="json")})
        self._replace(package)
        self._event("model.benchmark_completed", result.model_dump(mode="json"), "Model package benchmark completed")
        return result

    def apply_recommended_settings(self, model_id: str, vision_runtime) -> RecommendedSettingsApplyResult:
        package = self.get_package(model_id)
        if not package.metadata or not package.thresholds:
            return RecommendedSettingsApplyResult(
                accepted=False,
                applied=False,
                model_id=model_id,
                recommended_settings={},
                errors=["metadata_or_thresholds_missing"],
            )
        profile = vision_runtime.profile.model_copy(
            update={
                "inference_adapter": "ultralytics_yolo" if package.metadata.production_ready and package.metadata.model_format in {"pt", "onnx"} else "opencv_circle_test",
                "active_body_model_id": model_id,
                "active_balloon_model_id": model_id,
                # Preserve the package's declared device intent. Runtime
                # validation rejects unavailable CUDA instead of silently
                # changing the requested execution device to CPU.
                "device": package.metadata.recommended_device,
                "imgsz": package.metadata.recommended_imgsz,
                "conf": package.thresholds.default_conf,
                "iou": package.thresholds.default_iou,
                "max_det": package.thresholds.max_det,
            }
        )
        result = vision_runtime.apply(profile)
        payload = RecommendedSettingsApplyResult(
            accepted=result.accepted,
            applied=result.applied,
            model_id=model_id,
            recommended_settings={
                "imgsz": package.metadata.recommended_imgsz,
                "conf": package.thresholds.default_conf,
                "iou": package.thresholds.default_iou,
                "max_det": package.thresholds.max_det,
                "preset": package.thresholds.recommended_runtime_preset,
            },
            runtime_result=result.model_dump(mode="json"),
            warnings=result.warnings,
            errors=result.errors,
        )
        semantic = self.semantic_state(package)
        self._event(
            "model.runtime_recommended_applied",
            {
                **payload.model_dump(mode="json"),
                "package_kind": semantic.package_kind,
                "production_ready": semantic.production_ready,
                "competition_ready": semantic.competition_ready,
            },
            "Recommended vision runtime settings applied; safety state unchanged.",
        )
        return payload

    def semantic_state(self, active: ModelPackageRecord | None = None, runtime_valid: bool = True) -> ActiveModelSemanticState:
        active = active or next((item for item in self.list_packages() if item.active), None)
        if not active:
            return ActiveModelSemanticState(blockers=["production_model_not_loaded", "class_mapping_not_valid", "model_test_not_run"])
        validation = active.validation or self._validate_record(active)
        metadata = active.metadata
        package_kind = "production" if metadata and metadata.production_ready else "fixture" if metadata and metadata.provided_by == "test_fixture" else "test_adapter"
        production_model = package_kind == "production"
        package_valid = validation.valid
        class_mapping_valid = validation.class_mapping_status == "complete"
        test_passed = bool(
            active.last_test_result
            and active.last_test_result.get("accepted") is True
            and active.last_test_result.get("evidence_kind") == "golden_inference"
            and active.last_test_result.get("class_mapping_verified") is True
        )
        production_readiness = "test_adapter_only"
        blockers: list[str] = []
        warnings = list(active.warnings)
        if not production_model:
            blockers.append("production_model_not_loaded")
            warnings.append("fixture_test_adapter_not_competition_model")
        elif not class_mapping_valid:
            production_readiness = "missing_class_mapping"
            blockers.append("class_mapping_not_valid")
        elif not test_passed:
            production_readiness = "production_model_loaded"
            blockers.append("production_model_test_not_passed")
        else:
            production_readiness = "production_ready"
        production_ready = production_model and package_valid and class_mapping_valid and test_passed
        competition_ready = production_ready
        return ActiveModelSemanticState(
            active_model_id=active.model_id,
            package_id=active.package_name,
            package_kind=package_kind,  # type: ignore[arg-type]
            adapter_mode="ultralytics_yolo" if production_model and metadata and metadata.model_format in {"pt", "onnx"} else "fixture_test_adapter",
            model_format=metadata.model_format if metadata else None,
            active_model_state="production_model_active" if production_model else "fixture_model_active",
            package_schema_validation="passed" if package_valid else "failed",
            runtime_validation="passed" if runtime_valid else "failed",
            production_readiness=production_readiness,  # type: ignore[arg-type]
            competition_readiness="rehearsal_ready" if competition_ready else "limited_demo_only" if not production_model else "blocked",
            package_schema_valid=package_valid,
            runtime_valid=runtime_valid,
            class_mapping_valid=class_mapping_valid,
            production_model=production_model,
            production_ready=production_ready,
            competition_ready=competition_ready,
            warnings=sorted(set(warnings)),
            blockers=sorted(set(blockers)),
        )

    def active_package_summary(self) -> dict:
        active = next((item for item in self.list_packages() if item.active), None)
        if not active:
            return self.semantic_state(None).model_dump(mode="json")
        validation = active.validation or self._validate_record(active)
        semantic = self.semantic_state(active)
        return {
            **semantic.model_dump(mode="json"),
            "active_model_id": active.model_id,
            "model_name": active.metadata.model_name if active.metadata else active.package_name,
            "version": active.version,
            "model_validation_status": validation.status,
            "package_schema_status": semantic.package_schema_validation,
            "runtime_status": semantic.runtime_validation,
            "class_mapping_status": validation.class_mapping_status,
            "production_status": semantic.production_readiness,
            "competition_status": semantic.competition_readiness,
            "production_model": semantic.production_model,
            "production_ready": semantic.production_ready,
            "competition_ready": semantic.competition_ready,
            "advisory_only": True,
            "no_physical_command_generated": True,
            "last_test_status": "completed" if active.last_test_result else "not_run",
        }

    def inventory_json(self) -> dict:
        return {
            "packages": [item.model_dump(mode="json") for item in self.list_packages()],
            "active": self.active_package_summary(),
            "no_physical_command_generated": True,
        }

    def _resolve_source(self, request: ModelPackageImportRequest) -> Path:
        if request.source_path:
            source = Path(request.source_path)
            if not source.is_absolute():
                source = project_root() / source
        else:
            source = self.incoming_dir / (request.package_name or "")
        if not source.exists():
            raise FileNotFoundError(f"model package source not found: {source}")
        return source.resolve()

    def _safe_extract(self, zip_path: Path, destination: Path) -> None:
        destination.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(zip_path) as archive:
            for member in archive.infolist():
                target = (destination / member.filename).resolve()
                if not str(target).startswith(str(destination.resolve())):
                    raise ValueError("zip path traversal rejected")
                if Path(member.filename).suffix.lower() and Path(member.filename).suffix.lower() not in ALLOWED_PACKAGE_EXTENSIONS:
                    raise ValueError(f"unsupported package file extension: {member.filename}")
            archive.extractall(destination)

    def _import_directory(self, source: Path) -> ModelPackageRecord:
        if not source.is_dir():
            raise ValueError("model package source must be a directory or zip file")
        metadata = self._load_metadata(source / "metadata.json")
        thresholds = self._load_thresholds(source / "thresholds.json")
        model_file = self._find_model_file(source)
        package_name = f"{metadata.model_id}-{metadata.version}".replace("/", "-")
        target = self.package_dir / package_name
        if target.exists():
            shutil.rmtree(target)
        self._copy_package(source, target)
        target_model_file = str(target / model_file.name) if model_file else None
        package = ModelPackageRecord(
            model_id=metadata.model_id,
            version=metadata.version,
            package_name=package_name,
            package_path=str(target),
            model_file=target_model_file,
            checksum_sha256=self._checksum_dir(target),
            metadata=metadata,
            thresholds=thresholds,
        )
        validation = self._validate_record(package)
        package = package.model_copy(update={"validation": validation, "status": "validated" if validation.valid else "invalid", "warnings": validation.warnings})
        return package

    def _copy_package(self, source: Path, target: Path) -> None:
        def ignore(_dir: str, names: list[str]) -> set[str]:
            ignored = set()
            for name in names:
                if Path(name).suffix.lower() and Path(name).suffix.lower() not in ALLOWED_PACKAGE_EXTENSIONS:
                    ignored.add(name)
            return ignored
        shutil.copytree(source, target, ignore=ignore)

    def _load_metadata(self, path: Path) -> ModelPackageMetadata:
        if not path.exists():
            raise ValueError("metadata.json is required")
        return ModelPackageMetadata.model_validate_json(path.read_text(encoding="utf-8"))

    def _load_thresholds(self, path: Path) -> ModelPackageThresholds:
        if not path.exists():
            raise ValueError("thresholds.json is required")
        return ModelPackageThresholds.model_validate_json(path.read_text(encoding="utf-8"))

    def _find_model_file(self, path: Path) -> Path | None:
        for suffix in (".pt", ".onnx", ".engine"):
            matches = sorted(path.glob(f"*{suffix}"))
            if matches:
                return matches[0]
        return None

    def _validate_record(self, package: ModelPackageRecord) -> ModelPackageValidationResult:
        metadata = package.metadata
        thresholds = package.thresholds
        model_file_present = bool(package.model_file and Path(package.model_file).exists())
        metadata_present = metadata is not None
        thresholds_present = thresholds is not None
        class_mapping_present = metadata_present and bool(metadata.class_id_to_name)
        expected_present = metadata_present and bool(metadata.expected_classes)
        safety_note_ok = metadata_present and metadata.safety_note == "advisory_only"
        required_missing = []
        if metadata_present:
            classes = set(metadata.class_id_to_name.values()) | set(metadata.expected_classes)
            required_missing = [item for item in REQUIRED_COMPETITION_CLASSES if item not in classes]
        class_mapping_status = "complete" if not required_missing and class_mapping_present else "missing_required_classes" if metadata_present else "metadata_missing"
        checks = {
            "metadata_present": metadata_present,
            "thresholds_present": thresholds_present,
            "model_file_present": model_file_present,
            "class_mapping_present": bool(class_mapping_present),
            "expected_classes_present": bool(expected_present),
            "safety_note_advisory_only": bool(safety_note_ok),
            "required_competition_classes_present": not required_missing,
        }
        errors = []
        warnings = []
        if not metadata_present:
            errors.append("metadata_missing")
        if not thresholds_present:
            errors.append("thresholds_missing")
        if not model_file_present:
            errors.append("model_file_missing")
        if required_missing:
            warnings.append("required_competition_classes_missing:" + ",".join(required_missing))
        if metadata_present and not metadata.production_ready:
            warnings.append("fixture_or_non_production_package")
        if not safety_note_ok:
            errors.append("safety_note_must_be_advisory_only")
        valid = not errors and metadata_present and thresholds_present and class_mapping_present
        mapping = self._mapping_items(metadata, required_missing) if metadata else []
        return ModelPackageValidationResult(
            model_id=package.model_id,
            version=package.version,
            status="passed" if valid else "failed",
            valid=valid,
            can_activate=valid,
            production_ready=bool(metadata and metadata.production_ready and not required_missing),
            class_mapping_status=class_mapping_status,  # type: ignore[arg-type]
            checks=checks,
            class_mapping=mapping,
            warnings=sorted(set(warnings)),
            errors=errors,
        )

    def _mapping_items(self, metadata: ModelPackageMetadata, required_missing: list[str]) -> list[ClassMappingReviewItem]:
        class_names = {int(key): value for key, value in metadata.class_id_to_name.items()}
        items = [
            ClassMappingReviewItem(
                class_id=class_id,
                class_name=name,
                    mapped_role="balloon_target" if name == "balloon" else "body_target" if name in REQUIRED_COMPETITION_CLASSES else "unknown",
                required=name in REQUIRED_COMPETITION_CLASSES,
                status="mapped",
            )
            for class_id, name in sorted(class_names.items())
        ]
        for missing in required_missing:
            items.append(
                ClassMappingReviewItem(
                    class_id=-1,
                    class_name=missing,
                    mapped_role="balloon_target" if missing == "balloon" else "body_target",
                    required=True,
                    status="missing",
                )
            )
        return items

    def _run_production_golden_inference(self, package: ModelPackageRecord) -> tuple[bool, list[dict], list[str], list[str]]:
        """Execute real model output against package-owned golden frames.

        Metadata alone is never enough: every required competition class must
        appear in at least one golden case and its returned tensor class id
        must map to the exact package class name.  This remains a perception
        check only and has no CommandGateway/serial dependency.
        """
        if package.metadata is None or not package.model_file:
            return False, [], [], ["production_model_or_metadata_missing"]
        manifest_path = Path(package.package_path) / "golden_cases.json"
        if not manifest_path.is_file():
            return False, [], [], ["production_golden_manifest_missing"]
        try:
            raw = json.loads(manifest_path.read_text(encoding="utf-8"))
            cases = raw.get("cases", []) if isinstance(raw, dict) else []
        except (OSError, ValueError):
            return False, [], [], ["production_golden_manifest_invalid"]
        if not isinstance(cases, list) or not cases:
            return False, [], [], ["production_golden_cases_missing"]
        package_root = Path(package.package_path).resolve()
        expected_by_id = {int(class_id): self._normalise_class_name(name) for class_id, name in package.metadata.class_id_to_name.items()}
        required = {self._normalise_class_name(item) for item in REQUIRED_COMPETITION_CLASSES}
        observed: set[str] = set()
        detections: list[dict] = []
        warnings: list[str] = []
        errors: list[str] = []
        try:
            from ultralytics import YOLO

            model = YOLO(package.model_file)
        except Exception as exc:  # pragma: no cover - depends on delivered weights/runtime
            return False, [], [], [f"production_model_load_failed:{exc}"]
        for index, case in enumerate(cases):
            if not isinstance(case, dict):
                errors.append(f"production_golden_case_invalid:{index}")
                continue
            image_name = case.get("image")
            required_classes = case.get("required_classes")
            if not isinstance(image_name, str) or not isinstance(required_classes, list) or not required_classes:
                errors.append(f"production_golden_case_schema_invalid:{index}")
                continue
            image_path = (package_root / image_name).resolve()
            if not str(image_path).startswith(str(package_root)) or not image_path.is_file():
                errors.append(f"production_golden_image_missing:{index}")
                continue
            requested = {self._normalise_class_name(item) for item in required_classes if isinstance(item, str)}
            if not requested or not requested <= required:
                errors.append(f"production_golden_required_class_invalid:{index}")
                continue
            try:
                results = model(
                    str(image_path),
                    imgsz=package.metadata.recommended_imgsz,
                    conf=package.metadata.recommended_conf,
                    iou=package.metadata.recommended_iou,
                    device="cpu",
                    verbose=False,
                )
            except Exception as exc:  # pragma: no cover - delivered model/runtime dependent
                errors.append(f"production_golden_inference_failed:{index}:{exc}")
                continue
            case_observed: set[str] = set()
            for result in results:
                names = getattr(result, "names", {}) or {}
                boxes = getattr(result, "boxes", None)
                if boxes is None:
                    continue
                for box in boxes:
                    class_id = int(round(self._tensor_scalar(box.cls[0]))) if getattr(box, "cls", None) is not None else -1
                    expected_name = expected_by_id.get(class_id)
                    output_name = self._normalise_class_name(self._result_class_name(names, class_id))
                    if expected_name is None or (output_name and output_name != expected_name):
                        errors.append(f"production_golden_class_mapping_mismatch:{index}:{class_id}")
                        continue
                    if expected_name:
                        case_observed.add(expected_name)
                        observed.add(expected_name)
                        detections.append({"case": index, "class_id": class_id, "class_name": expected_name, "confidence": round(self._tensor_scalar(box.conf[0]), 4)})
            missing_case = requested - case_observed
            if missing_case:
                errors.append("production_golden_expected_class_missing:" + ",".join(sorted(missing_case)))
        missing_global = required - observed
        if missing_global:
            errors.append("production_golden_competition_coverage_missing:" + ",".join(sorted(missing_global)))
        if not errors:
            warnings.append("production_golden_tensor_class_mapping_verified")
        return not errors, detections, warnings, errors

    @staticmethod
    def _normalise_class_name(value: str) -> str:
        normalised = str(value).strip().lower().replace("-", "_").replace(" ", "_")
        return {
            "f_16": "f16",
            "f16": "f16",
            "drone": "mini_micro_uav",
            "uav": "mini_micro_uav",
            "balistic": "ballistic_missile",
            "balistic_missile": "ballistic_missile",
        }.get(normalised, normalised)

    @staticmethod
    def _tensor_scalar(value) -> float:
        if hasattr(value, "detach"):
            value = value.detach().cpu().item()
        elif hasattr(value, "item"):
            value = value.item()
        return float(value)

    @staticmethod
    def _result_class_name(names, class_id: int) -> str:
        if isinstance(names, dict):
            return str(names.get(class_id, ""))
        if isinstance(names, (list, tuple)) and 0 <= class_id < len(names):
            return str(names[class_id])
        return ""

    def _checksum_dir(self, directory: Path) -> str:
        digest = hashlib.sha256()
        for path in sorted(item for item in directory.rglob("*") if item.is_file()):
            digest.update(str(path.relative_to(directory)).encode("utf-8"))
            digest.update(path.read_bytes())
        return digest.hexdigest()

    def _read_records(self) -> list[ModelPackageRecord]:
        raw = json.loads(self.registry_path.read_text(encoding="utf-8"))
        return [ModelPackageRecord.model_validate(item) for item in raw]

    def _write_records(self, records: list[ModelPackageRecord]) -> None:
        self.registry_path.write_text(json.dumps([item.model_dump(mode="json") for item in records], indent=2), encoding="utf-8")

    def _find(self, model_id: str) -> ModelPackageRecord | None:
        return next((item for item in self._read_records() if item.model_id == model_id), None)

    def _replace(self, package: ModelPackageRecord) -> None:
        self._write_records([package if item.model_id == package.model_id else item for item in self._read_records()])

    def _replace_active(self, package: ModelPackageRecord) -> None:
        self._write_records([package if item.model_id == package.model_id else item.model_copy(update={"active": False, "status": "inactive" if item.active else item.status}) for item in self._read_records()])

    def _event(self, event_type: str, payload: dict, message: str, level: LogLevel = LogLevel.INFO) -> None:
        payload = {**payload, "summary": message, "no_physical_command_generated": True}
        self.last_event = (event_type, payload)
        self.logger.emit(level, "MODEL", message, payload)
