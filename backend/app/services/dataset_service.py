import json
import shutil
import time
from pathlib import Path

import yaml

from app.schemas.annotation import AnnotationRecord
from app.schemas.config import AppConfig
from app.schemas.dataset import DATASET_CLASS_MAPS, DatasetExportRequest, DatasetExportResult, DatasetHealth, DatasetValidationResult
from app.schemas.log import LogLevel
from app.services.annotation_service import AnnotationService
from app.services.log_service import JsonlLogService
from app.services.model_registry_service import ModelRegistryService
from app.services.session_service import SessionService
from app.services.storage_paths import resolve_project_path


class DatasetService:
    def __init__(
        self,
        config: AppConfig,
        sessions: SessionService,
        annotations: AnnotationService,
        models: ModelRegistryService,
        logger: JsonlLogService,
    ) -> None:
        self.config = config
        self.sessions = sessions
        self.annotations = annotations
        self.models = models
        self.logger = logger
        self.root = resolve_project_path(config.dataset.root_dir)
        self.datasets_dir = self.root / "datasets"
        self.exports_dir = self.root / "exports" / "yolo"
        self.reports_dir = self.root / "exports" / "reports"
        for path in (self.datasets_dir, self.exports_dir, self.reports_dir):
            path.mkdir(parents=True, exist_ok=True)
        self.last_event: tuple[str, dict] | None = None

    def list_datasets(self) -> list[dict]:
        return [{"dataset_id": path.name, "path": str(path)} for path in sorted(self.datasets_dir.glob("*")) if path.is_dir()]

    def list_exports(self) -> list[dict]:
        exports = []
        for path in sorted(self.exports_dir.glob("*"), reverse=True):
            if not path.is_dir():
                continue
            exports.append(
                {
                    "dataset_id": path.name,
                    "path": str(path),
                    "data_yaml_path": str(path / "data.yaml"),
                    "image_count": len(list((path / "images").glob("*/*"))),
                    "label_count": len(list((path / "labels").glob("*/*.txt"))),
                    "no_physical_command_generated": True,
                }
            )
        return exports

    def get_dataset(self, dataset_id: str) -> dict:
        path = self.exports_dir / dataset_id
        if not path.exists():
            path = self.datasets_dir / dataset_id
        if not path.exists():
            raise KeyError(dataset_id)
        metadata_path = path / "metadata.json"
        metadata = json.loads(metadata_path.read_text(encoding="utf-8")) if metadata_path.exists() else {}
        return {"dataset_id": dataset_id, "path": str(path), "metadata": metadata}

    def export_yolo(self, request: DatasetExportRequest) -> DatasetExportResult:
        self._event("dataset.export_started", request.model_dump(mode="json"), "Dataset export started")
        dataset_id = f"{request.dataset_name}-{request.version}"
        output = self.exports_dir / dataset_id
        for subdir in ("images/train", "images/val", "labels/train", "labels/val"):
            (output / subdir).mkdir(parents=True, exist_ok=True)
        annotations = self._selected_annotations(request)
        warnings = []
        if not annotations:
            warnings.append("no_annotations_selected")
        class_map = DATASET_CLASS_MAPS[request.export_mode]
        train_count = int(len(annotations) * request.train_val_split)
        label_count = 0
        image_count = 0
        for index, annotation in enumerate(annotations):
            split = "train" if index < train_count else "val"
            image_path = Path(annotation.image_path)
            image_name = f"{annotation.frame_id}.jpg"
            target_image = output / f"images/{split}" / image_name
            if image_path.exists():
                shutil.copyfile(image_path, target_image)
            else:
                target_image.write_bytes(b"")
                warnings.append(f"missing_image:{annotation.frame_id}")
            label_lines = []
            for obj in annotation.objects:
                if not request.include_unverified_annotations and not obj.verified_by_operator:
                    continue
                if request.min_confidence is not None and obj.confidence is not None and obj.confidence < request.min_confidence:
                    continue
                class_id = self._class_id_for_mode(obj.class_name, obj.is_balloon, request.export_mode)
                if class_id not in class_map:
                    warnings.append(f"unsupported_class:{obj.class_name}")
                    continue
                bbox = obj.bbox if obj.bbox_format == "yolo_normalized" else self._to_yolo_bbox(obj.bbox, obj.bbox_format)
                label_lines.append(f"{class_id} {' '.join(f'{value:.6f}' for value in bbox)}")
            (output / f"labels/{split}" / f"{annotation.frame_id}.txt").write_text("\n".join(label_lines), encoding="utf-8")
            label_count += len(label_lines)
            image_count += 1
        data_yaml = {
            "path": str(output),
            "train": "images/train",
            "val": "images/val",
            "names": class_map,
        }
        data_yaml_path = output / "data.yaml"
        data_yaml_path.write_text(yaml.safe_dump(data_yaml, sort_keys=False), encoding="utf-8")
        metadata = {
            "request": request.model_dump(mode="json"),
            "active_models": self.models.active_models().model_dump(mode="json"),
            "created_at": time.time(),
            "warnings": warnings,
            "no_physical_command_generated": True,
        }
        (output / "metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        result = DatasetExportResult(
            dataset_id=dataset_id,
            output_path=str(output),
            data_yaml_path=str(data_yaml_path),
            image_count=image_count,
            label_count=label_count,
            train_count=train_count,
            val_count=max(0, len(annotations) - train_count),
            warnings=warnings,
            no_physical_command_generated=True,
        )
        self._event("dataset.export_completed", result.model_dump(mode="json"), "Dataset export completed")
        return result

    def validate(self, request: DatasetExportRequest | None = None) -> DatasetValidationResult:
        annotations = self._selected_annotations(request) if request else [ann for session in self.sessions.list_sessions() for ann in self.annotations.list_annotations(session.session_id)]
        errors = []
        warnings = []
        seen_frames = set()
        checked = 0
        for annotation in annotations:
            if annotation.frame_id in seen_frames:
                warnings.append(f"duplicate_frame:{annotation.frame_id}")
            seen_frames.add(annotation.frame_id)
            if not Path(annotation.image_path).exists():
                warnings.append(f"image_missing:{annotation.frame_id}")
            for obj in annotation.objects:
                checked += 1
                if obj.bbox_format == "yolo_normalized" and any(value < 0 or value > 1 for value in obj.bbox):
                    errors.append(f"invalid_bbox:{annotation.frame_id}:{obj.object_id}")
                if obj.class_id < 0:
                    errors.append(f"invalid_class_id:{annotation.frame_id}:{obj.object_id}")
        if not annotations:
            warnings.append("no_annotations_available")
        result = DatasetValidationResult(valid=not errors, errors=errors, warnings=warnings, checked_items=checked)
        self._event("dataset.validation", result.model_dump(mode="json"), "Dataset validation completed")
        return result

    def health(self) -> DatasetHealth:
        sessions = self.sessions.list_sessions()
        annotations = [ann for session in sessions for ann in self.annotations.list_annotations(session.session_id)]
        class_distribution: dict[str, int] = {}
        for annotation in annotations:
            for obj in annotation.objects:
                class_distribution[obj.class_name] = class_distribution.get(obj.class_name, 0) + 1
        distance_distribution: dict[str, int] = {}
        team_distribution: dict[str, int] = {}
        lens_distribution: dict[str, int] = {}
        model_distribution: dict[str, int] = {}
        for session in sessions:
            distance_distribution[session.scenario.distance_m] = distance_distribution.get(session.scenario.distance_m, 0) + 1
            team_distribution[session.scenario.team] = team_distribution.get(session.scenario.team, 0) + 1
            lens_distribution[session.scenario.lens_profile] = lens_distribution.get(session.scenario.lens_profile, 0) + 1
            for model_id in session.scenario.active_model_ids:
                model_distribution[model_id] = model_distribution.get(model_id, 0) + 1
        recommendations = []
        if distance_distribution.get("15", 0) < 3:
            recommendations.append("Need more 15m helicopter enemy samples.")
        if team_distribution.get("friend", 0) < 3:
            recommendations.append("Need friend-color examples.")
        if lens_distribution.get("12mm", 0) < 3:
            recommendations.append("Need 12mm/15m balloon samples.")
        if not model_distribution:
            recommendations.append("Need samples tested with active body model.")
        return DatasetHealth(
            total_sessions=len(sessions),
            total_images=sum(session.stats.snapshot_count for session in sessions),
            total_annotations=len(annotations),
            class_distribution=class_distribution,
            distance_distribution=distance_distribution,
            team_distribution=team_distribution,
            lens_distribution=lens_distribution,
            model_distribution=model_distribution,
            missing_metadata_warnings=["team metadata missing"] if team_distribution.get("unknown", 0) else [],
            recommendations=recommendations,
        )

    def split(self, dataset_id: str, train_val_split: float) -> dict:
        return {"dataset_id": dataset_id, "train_val_split": train_val_split, "status": "recorded_only"}

    def frame_extract(self, session_id: str, every_n_frames: int) -> dict:
        frames = self.sessions.frames(session_id)
        return {"session_id": session_id, "selected_frames": frames[::every_n_frames], "no_physical_command_generated": True}

    def _selected_annotations(self, request: DatasetExportRequest | None) -> list[AnnotationRecord]:
        sessions = self.sessions.list_sessions()
        if request and request.selected_sessions:
            sessions = [session for session in sessions if session.session_id in request.selected_sessions]
        annotations = []
        for session in sessions:
            if request and request.selected_target_types and session.scenario.target_type not in request.selected_target_types:
                continue
            if request and request.selected_distances and session.scenario.distance_m not in request.selected_distances:
                continue
            if request and request.selected_lens_profiles and session.scenario.lens_profile not in request.selected_lens_profiles:
                continue
            annotations.extend(self.annotations.list_annotations(session.session_id))
        return annotations

    def _class_id_for_mode(self, class_name: str, is_balloon: bool, mode: str) -> int:
        if mode == "balloon_singleclass":
            return 0
        if mode == "target_singleclass":
            return 0
        if mode == "combined_body_balloon" and is_balloon:
            return 4
        mapping = {"f16": 0, "helicopter": 1, "ballistic_missile": 2, "mini_micro_uav": 3, "balloon": 4}
        return mapping.get(class_name, 0)

    def _to_yolo_bbox(self, bbox: list[float], bbox_format: str) -> list[float]:
        if bbox_format == "xyxy_pixel":
            x1, y1, x2, y2 = bbox
            width = max(1.0, x2 - x1)
            height = max(1.0, y2 - y1)
            return [min(1.0, (x1 + width / 2) / 640), min(1.0, (y1 + height / 2) / 360), min(1.0, width / 640), min(1.0, height / 360)]
        x, y, width, height = bbox
        return [min(1.0, (x + width / 2) / 640), min(1.0, (y + height / 2) / 360), min(1.0, width / 640), min(1.0, height / 360)]

    def _event(self, event_type: str, payload: dict, message: str) -> None:
        self.last_event = (event_type, payload)
        self.logger.emit(LogLevel.INFO, "DATASET", message, payload)
