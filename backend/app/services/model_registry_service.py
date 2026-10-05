import json
import time
import uuid
from pathlib import Path

from app.schemas.config import AppConfig
from app.schemas.log import LogLevel
from app.schemas.model_registry import (
    ActiveModels,
    ModelActivationRequest,
    ModelMetadata,
    ModelMetadataUpdate,
    ModelStatus,
    ModelUploadRequest,
    ModelValidationResult,
)
from app.services.log_service import JsonlLogService
from app.services.storage_paths import resolve_project_path


class ModelRegistryService:
    def __init__(self, config: AppConfig, logger: JsonlLogService) -> None:
        self.config = config
        self.logger = logger
        self.root = resolve_project_path(config.models.root_dir)
        self.uploaded_dir = self.root / "uploaded"
        self.active_dir = self.root / "active"
        self.registry_path = self.active_dir / "registry.json"
        self.active_path = resolve_project_path(config.models.active_models_file)
        self.last_event: tuple[str, dict] | None = None
        self.uploaded_dir.mkdir(parents=True, exist_ok=True)
        self.active_dir.mkdir(parents=True, exist_ok=True)
        self._ensure_files()

    def _ensure_files(self) -> None:
        if not self.registry_path.exists():
            stub = ModelMetadata(
                model_id="opencv-circle-test-adapter",
                name="OpenCV Circle Detector Test Adapter",
                version="0.1.0",
                model_type="test_stub",
                framework="opencv_stub",
                file_path=None,
                file_name=None,
                class_names=["balloon"],
                input_size=self.config.camera.stream_width,
                status="validated",
                provided_by="test_stub",
                notes="Test/demo adapter only. Not a production YOLO model.",
                warnings=["OpenCV daire algılayıcı yalnızca test adaptörüdür; production model değildir."],
            )
            self._write_registry([stub])
        if not self.active_path.exists():
            self.active_path.parent.mkdir(parents=True, exist_ok=True)
            self._write_active(ActiveModels())

    def list_models(self) -> list[ModelMetadata]:
        return sorted(self._read_registry(), key=lambda item: item.created_at, reverse=True)

    def get_model(self, model_id: str) -> ModelMetadata:
        model = self._find(model_id)
        if model is None:
            raise KeyError(model_id)
        return model

    def upload(self, request: ModelUploadRequest) -> ModelMetadata:
        extension = Path(request.file_name).suffix.lower()
        if extension not in {item.lower() for item in self.config.models.allowed_extensions}:
            raise ValueError(f"unsupported model extension: {extension}")
        if request.file_size_bytes > self.config.models.max_upload_size_mb * 1024 * 1024:
            raise ValueError("model upload exceeds max_upload_size_mb")

        model_id = f"model-{uuid.uuid4().hex[:10]}"
        model_dir = self.uploaded_dir / model_id
        model_dir.mkdir(parents=True, exist_ok=True)
        file_path = model_dir / request.file_name
        file_path.touch(exist_ok=True)
        warnings = []
        if not request.class_names:
            warnings.append("class_names_missing")
        if request.framework in {"ultralytics", "onnx"}:
            warnings.append("model uploaded but adapter not available in Phase 9 test environment")
        model = ModelMetadata(
            model_id=model_id,
            name=request.name,
            version=request.version,
            model_type=request.model_type,
            framework=request.framework,
            file_path=str(file_path),
            file_name=request.file_name,
            file_size_bytes=request.file_size_bytes,
            class_names=request.class_names,
            input_size=request.input_size,
            confidence_threshold=request.confidence_threshold,
            iou_threshold=request.iou_threshold,
            status="uploaded",
            provided_by=request.provided_by,
            notes=request.notes,
            warnings=warnings,
        )
        registry = [item for item in self._read_registry() if item.model_id != model.model_id]
        registry.append(model)
        self._write_registry(registry)
        self._write_metadata(model)
        self._event("model.uploaded", model.model_dump(mode="json"), "Model uploaded")
        return model

    def validate_model(self, model_id: str) -> ModelValidationResult:
        model = self.get_model(model_id)
        file_exists = model.framework == "opencv_stub" or (model.file_path is not None and Path(model.file_path).exists())
        extension_supported = model.framework == "opencv_stub" or (
            model.file_name is not None
            and Path(model.file_name).suffix.lower() in {item.lower() for item in self.config.models.allowed_extensions}
        )
        metadata_complete = bool(model.name and model.version and model.model_type and model.framework)
        class_names_present = bool(model.class_names)
        warnings = list(model.warnings)
        errors = []
        if not file_exists:
            errors.append("model_file_missing")
        if not extension_supported:
            errors.append("unsupported_extension")
        if not class_names_present:
            warnings.append("class_names_missing")
        if model.framework in {"ultralytics", "onnx"}:
            warnings.append("model file recorded; runtime adapter is controlled by vision team delivery")
        valid = file_exists and extension_supported and metadata_complete
        status: ModelStatus = "validated" if valid else "missing_file" if not file_exists else "invalid"
        model = model.model_copy(update={"status": status, "last_validated_at": time.time(), "warnings": sorted(set(warnings))})
        self._replace_model(model)
        result = ModelValidationResult(
            model_id=model_id,
            valid=valid,
            status=status,
            checks={
                "file_exists": file_exists,
                "extension_supported": extension_supported,
                "metadata_complete": metadata_complete,
                "class_names_present": class_names_present,
                "can_be_loaded": model.framework == "opencv_stub",
            },
            warnings=sorted(set(warnings)),
            errors=errors,
        )
        self._event("model.validated", result.model_dump(mode="json"), "Model validated")
        return result

    def activate(self, model_id: str, request: ModelActivationRequest) -> ActiveModels:
        model = self.get_model(model_id)
        active = self.active_models()
        slot = request.slot or self._slot_for_model(model)
        if slot == "body":
            active.active_body_model_id = model_id
        elif slot == "balloon":
            active.active_balloon_model_id = model_id
        elif slot == "combined":
            active.active_combined_model_id = model_id
        else:
            active.active_test_adapter = model_id
        active.updated_at = time.time()
        self._write_active(active)
        self._replace_model(model.model_copy(update={"status": "active"}))
        self._event(
            "model.registry_activated",
            active.model_dump(mode="json"),
            "Model registry active slots updated; package-level production status is reported separately.",
        )
        return active

    def deactivate(self, model_id: str) -> ActiveModels:
        active = self.active_models()
        if active.active_body_model_id == model_id:
            active.active_body_model_id = None
        if active.active_balloon_model_id == model_id:
            active.active_balloon_model_id = None
        if active.active_combined_model_id == model_id:
            active.active_combined_model_id = None
        if active.active_test_adapter == model_id:
            active.active_test_adapter = None
        active.updated_at = time.time()
        self._write_active(active)
        model = self._find(model_id)
        if model:
            self._replace_model(model.model_copy(update={"status": "inactive"}))
        self._event("model.deactivated", active.model_dump(mode="json"), "Model deactivated")
        return active

    def update_metadata(self, model_id: str, update: ModelMetadataUpdate) -> ModelMetadata:
        model = self.get_model(model_id)
        changes = update.model_dump(exclude_unset=True)
        updated = model.model_copy(update=changes)
        self._replace_model(updated)
        self._write_metadata(updated)
        self._event("model.metadata_updated", updated.model_dump(mode="json"), "Model metadata updated")
        return updated

    def upsert_model(self, model: ModelMetadata) -> ModelMetadata:
        registry = [item for item in self._read_registry() if item.model_id != model.model_id]
        registry.append(model)
        self._write_registry(registry)
        self._write_metadata(model)
        self._event("model.metadata_updated", model.model_dump(mode="json"), "Model metadata upserted")
        return model

    def record_test_result(self, model_id: str, result: dict) -> ModelMetadata:
        model = self.get_model(model_id)
        updated = model.model_copy(update={"last_test_result": result})
        self._replace_model(updated)
        return updated

    def delete(self, model_id: str) -> dict:
        if model_id == "opencv-circle-test-adapter":
            raise ValueError("test adapter cannot be deleted")
        model = self.get_model(model_id)
        self._write_registry([item for item in self._read_registry() if item.model_id != model_id])
        self.deactivate(model_id)
        self._event("model.deleted", {"model_id": model_id}, "Model deleted")
        return {"deleted": True, "model_id": model.model_id}

    def active_models(self) -> ActiveModels:
        try:
            content = self.active_path.read_text(encoding="utf-8").strip()
            if content:
                return ActiveModels.model_validate_json(content)
        except (OSError, ValueError):
            pass
        active = ActiveModels()
        self._write_active(active)
        return active

    def _slot_for_model(self, model: ModelMetadata) -> str:
        if model.model_type == "body_detector":
            return "body"
        if model.model_type == "balloon_detector":
            return "balloon"
        if model.model_type == "combined_detector":
            return "combined"
        return "test_adapter"

    def _find(self, model_id: str) -> ModelMetadata | None:
        return next((item for item in self._read_registry() if item.model_id == model_id), None)

    def _replace_model(self, model: ModelMetadata) -> None:
        self._write_registry([model if item.model_id == model.model_id else item for item in self._read_registry()])
        self._write_metadata(model)

    def _read_registry(self) -> list[ModelMetadata]:
        raw = json.loads(self.registry_path.read_text(encoding="utf-8"))
        return [ModelMetadata.model_validate(item) for item in raw]

    def _write_registry(self, models: list[ModelMetadata]) -> None:
        self.registry_path.write_text(
            json.dumps([item.model_dump(mode="json") for item in models], indent=2),
            encoding="utf-8",
        )

    def _write_active(self, active: ActiveModels) -> None:
        self.active_path.write_text(json.dumps(active.model_dump(mode="json"), indent=2), encoding="utf-8")

    def _write_metadata(self, model: ModelMetadata) -> None:
        if not model.file_path:
            return
        path = Path(model.file_path).parent / "metadata.json"
        path.write_text(json.dumps(model.model_dump(mode="json"), indent=2), encoding="utf-8")

    def _event(self, event_type: str, payload: dict, message: str) -> None:
        payload = {**payload, "summary": message, "no_physical_command_generated": True}
        self.last_event = (event_type, payload)
        self.logger.emit(LogLevel.INFO, "MODEL", message, payload)
