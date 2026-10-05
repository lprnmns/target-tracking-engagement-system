import json
import threading
import time
from pathlib import Path
from typing import Any

from app.schemas.log import LogEvent, LogLevel


class JsonlLogService:
    def __init__(self, log_dir: Path, filename: str = "backend.jsonl", max_bytes: int = 64 * 1024 * 1024, backup_count: int = 2) -> None:
        self.log_dir = log_dir
        self.filename = filename
        self.max_bytes = max(1024 * 1024, int(max_bytes))
        self.backup_count = max(1, int(backup_count))
        self._lock = threading.RLock()
        self._emit_count = 0
        self.log_dir.mkdir(parents=True, exist_ok=True)
        with self._lock:
            if self.path.exists() and self.path.stat().st_size >= self.max_bytes:
                self._rotate_locked()
            self._handle = self.path.open("a", encoding="utf-8", buffering=1)

    @property
    def path(self) -> Path:
        return self.log_dir / self.filename

    def emit(
        self,
        level: LogLevel,
        subsystem: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> LogEvent:
        event = LogEvent(
            ts=time.time(),
            level=level,
            subsystem=subsystem,
            message=message,
            details=details or {},
        )
        line = json.dumps(event.model_dump(mode="json"), ensure_ascii=False) + "\n"
        with self._lock:
            self._emit_count += 1
            if self._emit_count % 256 == 0 and self.path.exists() and self.path.stat().st_size >= self.max_bytes:
                self._handle.flush()
                self._handle.close()
                self._rotate_locked()
                self._handle = self.path.open("a", encoding="utf-8", buffering=1)
            self._handle.write(line)
        return event

    def _rotate_locked(self) -> None:
        oldest = self.path.with_name(f"{self.filename}.{self.backup_count}")
        if oldest.exists():
            oldest.unlink()
        for index in range(self.backup_count - 1, 0, -1):
            source = self.path.with_name(f"{self.filename}.{index}")
            if source.exists():
                source.replace(self.path.with_name(f"{self.filename}.{index + 1}"))
        if self.path.exists():
            self.path.replace(self.path.with_name(f"{self.filename}.1"))


def default_log_dir() -> Path:
    return Path(__file__).resolve().parents[3] / "logs"
