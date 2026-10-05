import base64
import json
import time
import uuid
from pathlib import Path
from typing import Any

from app.schemas.config import AppConfig
from app.schemas.log import LogLevel
from app.schemas.session import (
    RecordEventRequest,
    SessionQualityRequest,
    SessionRecord,
    SessionStats,
    SnapshotResponse,
    StartSessionRequest,
)
from app.services.log_service import JsonlLogService
from app.services.storage_paths import resolve_project_path

TINY_JPEG = base64.b64decode(
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAP//////////////////////////////////////////////////////////////////////////////////////"
    "2wBDAf//////////////////////////////////////////////////////////////////////////////////////wAARCAABAAEDASIAAhEBAxEB/8QAFQABAQAAAAAAAAAAAAAAAAAAAAX/"
    "xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oADAMBAAIQAxAAAAH/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oACAEBAAEFAqf/xAAUEQEAAAAAAAAAAAAAAAAAAAAA/9oACAEDAQE/ASP/"
    "xAAUEQEAAAAAAAAAAAAAAAAAAAAA/9oACAECAQE/ASP/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oACAEBAAY/Al//xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oACAEBAAE/IV//2gAMAwEAAgADAAAAEP/EFBQRAQAAAAAAAAAAAAAAAAAAABD/2gAIAQMBAT8QH//EFBQRAQAAAAAAAAAAAAAAAAAAABD/2gAIAQIBAT8QH//EFBABAQAAAAAAAAAAAAAAAAAAABD/2gAIAQEAAT8QH//Z"
)


class SessionService:
    def __init__(self, config: AppConfig, logger: JsonlLogService) -> None:
        self.config = config
        self.logger = logger
        self.root = resolve_project_path(config.dataset.root_dir) / "sessions"
        self.root.mkdir(parents=True, exist_ok=True)
        self.active_session_id: str | None = None
        self.last_event: tuple[str, dict] | None = None

    def list_sessions(self) -> list[SessionRecord]:
        sessions = []
        for path in sorted(self.root.glob("*/session.json"), reverse=True):
            sessions.append(SessionRecord.model_validate_json(path.read_text(encoding="utf-8")))
        return sessions

    def start(self, request: StartSessionRequest) -> SessionRecord:
        session_id = f"session-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
        session = SessionRecord(
            session_id=session_id,
            name=request.name,
            operator=request.operator,
            mode=request.mode,
            scenario=request.scenario,
            safety={
                "dry_run": self.config.system.dry_run,
                "hardware_enabled": self.config.system.hardware_enabled,
                "no_physical_command_generated": True,
            },
        )
        session_dir = self._session_dir(session_id)
        for name in ("frames", "snapshots", "video"):
            (session_dir / name).mkdir(parents=True, exist_ok=True)
        self._write_session(session)
        for filename in ("detections.jsonl", "color_decisions.jsonl", "decisions.jsonl", "operator_actions.jsonl", "annotations.jsonl"):
            (session_dir / filename).touch(exist_ok=True)
        self.active_session_id = session_id
        self._event("session.started", session.model_dump(mode="json"), "Session started")
        return session

    def stop(self, session_id: str | None = None) -> SessionRecord:
        session = self.get_session(session_id or self._require_active())
        duration = max(0.0, time.time() - session.created_at)
        session = session.model_copy(update={"ended_at": time.time(), "stats": session.stats.model_copy(update={"duration_sec": round(duration, 3)})})
        self._write_session(session)
        if self.active_session_id == session.session_id:
            self.active_session_id = None
        self._event("session.stopped", session.model_dump(mode="json"), "Session stopped")
        return session

    def get_session(self, session_id: str) -> SessionRecord:
        path = self._session_dir(session_id) / "session.json"
        if not path.exists():
            raise KeyError(session_id)
        return SessionRecord.model_validate_json(path.read_text(encoding="utf-8"))

    def delete(self, session_id: str) -> dict:
        session_dir = self._session_dir(session_id)
        if not session_dir.exists():
            raise KeyError(session_id)
        for path in sorted(session_dir.rglob("*"), reverse=True):
            if path.is_file():
                path.unlink()
            elif path.is_dir():
                path.rmdir()
        session_dir.rmdir()
        if self.active_session_id == session_id:
            self.active_session_id = None
        self._event("session.deleted", {"session_id": session_id}, "Session deleted")
        return {"deleted": True, "session_id": session_id}

    def snapshot(self, session_id: str | None = None) -> SnapshotResponse:
        session = self.get_session(session_id or self._require_active())
        frame_id = f"frame-{session.stats.snapshot_count + 1:06d}"
        session_dir = self._session_dir(session.session_id)
        image_path = session_dir / "snapshots" / f"{frame_id}.{self.config.dataset.snapshot_format}"
        metadata_path = session_dir / "frames" / f"{frame_id}.json"
        image_path.write_bytes(TINY_JPEG)
        metadata = {
            "frame_id": frame_id,
            "session_id": session.session_id,
            "image_path": str(image_path),
            "created_at": time.time(),
            "source": "mock" if self.config.dataset.save_mock_frames else "metadata_only",
            "no_physical_command_generated": True,
        }
        metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        stats = session.stats.model_copy(
            update={
                "snapshot_count": session.stats.snapshot_count + 1,
                "frame_count": session.stats.frame_count + 1,
            }
        )
        self._write_session(session.model_copy(update={"stats": stats}))
        response = SnapshotResponse(
            session_id=session.session_id,
            frame_id=frame_id,
            image_path=str(image_path),
            metadata_path=str(metadata_path),
            no_physical_command_generated=True,
        )
        self._event("session.snapshot_saved", response.model_dump(mode="json"), "Snapshot saved")
        return response

    def record_event(self, session_id: str, request: RecordEventRequest) -> dict:
        session = self.get_session(session_id)
        filename = {
            "detection": "detections.jsonl",
            "color_decision": "color_decisions.jsonl",
            "decision": "decisions.jsonl",
            "operator_action": "operator_actions.jsonl",
        }[request.event_type]
        entry = {"ts": time.time(), "event_type": request.event_type, "payload": request.payload, "no_physical_command_generated": True}
        self._append_jsonl(self._session_dir(session_id) / filename, entry)
        updates: dict[str, Any] = {}
        if request.event_type == "detection":
            updates["detection_count"] = session.stats.detection_count + 1
        stats = session.stats.model_copy(update=updates) if updates else session.stats
        self._write_session(session.model_copy(update={"stats": stats}))
        self._event("session.event_recorded", {"session_id": session_id, **entry}, "Session event recorded")
        return entry

    def frames(self, session_id: str) -> list[dict]:
        frames = []
        for path in sorted((self._session_dir(session_id) / "frames").glob("*.json")):
            frames.append(json.loads(path.read_text(encoding="utf-8")))
        return frames

    def jsonl_records(self, session_id: str, filename: str) -> list[dict]:
        path = self._session_dir(session_id) / filename
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def set_quality(self, session_id: str, request: SessionQualityRequest) -> SessionRecord:
        session = self.get_session(session_id)
        updated = session.model_copy(update={"quality": request.quality})
        self._write_session(updated)
        return updated

    def _session_dir(self, session_id: str) -> Path:
        return self.root / session_id

    def _write_session(self, session: SessionRecord) -> None:
        path = self._session_dir(session.session_id)
        path.mkdir(parents=True, exist_ok=True)
        (path / "session.json").write_text(json.dumps(session.model_dump(mode="json"), indent=2), encoding="utf-8")

    def _append_jsonl(self, path: Path, entry: dict) -> None:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def _require_active(self) -> str:
        if self.active_session_id is None:
            raise RuntimeError("no active session")
        return self.active_session_id

    def _event(self, event_type: str, payload: dict, message: str) -> None:
        self.last_event = (event_type, payload)
        self.logger.emit(LogLevel.INFO, "SESSION", message, payload)
