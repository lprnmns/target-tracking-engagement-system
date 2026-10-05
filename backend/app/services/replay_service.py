import time

from app.schemas.log import LogLevel
from app.schemas.replay import ReplayStatus
from app.services.log_service import JsonlLogService
from app.services.session_service import SessionService


class ReplayService:
    def __init__(self, sessions: SessionService, logger: JsonlLogService) -> None:
        self.sessions = sessions
        self.logger = logger
        self.status = ReplayStatus()
        self.last_event: tuple[str, dict] | None = None

    def load_session(self, session_id: str) -> ReplayStatus:
        frames = self.sessions.frames(session_id)
        self.status = ReplayStatus(state="loaded", session_id=session_id, frame_count=len(frames), frame_index=0, current_frame_path=frames[0]["image_path"] if frames else None)
        self._event("replay.loaded", self.status.model_dump(mode="json"), "Replay session loaded")
        return self.status

    def play(self) -> ReplayStatus:
        self.status = self.status.model_copy(update={"state": "playing", "updated_at": time.time()})
        self._event("replay.playing", self.status.model_dump(mode="json"), "Replay playing")
        return self.status

    def pause(self) -> ReplayStatus:
        self.status = self.status.model_copy(update={"state": "paused", "updated_at": time.time()})
        self._event("replay.paused", self.status.model_dump(mode="json"), "Replay paused")
        return self.status

    def stop(self) -> ReplayStatus:
        self.status = self.status.model_copy(update={"state": "stopped", "frame_index": 0, "updated_at": time.time()})
        self._event("replay.stopped", self.status.model_dump(mode="json"), "Replay stopped")
        return self.status

    def seek(self, frame_index: int) -> ReplayStatus:
        frame_index = min(frame_index, max(0, self.status.frame_count - 1))
        self.status = self.status.model_copy(update={"frame_index": frame_index, "updated_at": time.time()})
        self._event("replay.frame", self.status.model_dump(mode="json"), "Replay seek")
        return self.status

    def step(self) -> ReplayStatus:
        return self.seek(self.status.frame_index + 1)

    def speed(self, speed: float) -> ReplayStatus:
        self.status = self.status.model_copy(update={"speed": speed, "updated_at": time.time()})
        self._event("replay.updated", self.status.model_dump(mode="json"), "Replay speed updated")
        return self.status

    def frame_event(self) -> tuple[str, dict] | None:
        if self.status.state != "playing":
            return None
        return ("replay.frame", self.status.model_dump(mode="json"))

    def _event(self, event_type: str, payload: dict, message: str) -> None:
        self.last_event = (event_type, payload)
        self.logger.emit(LogLevel.INFO, "REPLAY", message, payload)
