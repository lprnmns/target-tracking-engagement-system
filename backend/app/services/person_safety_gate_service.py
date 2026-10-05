from __future__ import annotations

import time

from app.schemas.config import AppConfig
from app.schemas.log import LogLevel
from app.schemas.person_safety import PersonSafetyGateStatus
from app.schemas.vision import BodyDetection, VisionEvent
from app.services.log_service import JsonlLogService


PERSON_CLASSES = {"person", "human", "insan"}


class PersonSafetyGateService:
    """Additional software-only person safety gate.

    This is a read-only gate over existing detections. It does not replace
    emergency stop, operator supervision, hardware interlocks, or existing fire
    policy checks.
    """

    def __init__(self, config: AppConfig, logger: JsonlLogService) -> None:
        self.config = config
        self.logger = logger
        self._last_seen_ms: int | None = None
        self._last_confidence: float | None = None
        self._last_class: str | None = None
        self._last_detection_id: int | None = None
        self._was_active = False
        self._last_log_at = 0.0
        self.last_event: tuple[str, dict] | None = None

    def status(self, now_ms: int | None = None) -> PersonSafetyGateStatus:
        return self.evaluate(None, now_ms=now_ms, update_from_frame=False)

    def evaluate(
        self,
        event: VisionEvent | None,
        now_ms: int | None = None,
        update_from_frame: bool = True,
    ) -> PersonSafetyGateStatus:
        now = now_ms if now_ms is not None else int(time.time() * 1000)
        config = self.config.person_safety
        if not config.enabled:
            status = PersonSafetyGateStatus(
                enabled=False,
                confidence_threshold=config.confidence_threshold,
                hold_ms=config.hold_ms,
                clear_after_ms=config.clear_after_ms,
            )
            self._remember_event(status)
            return status

        detection = self._select_person_detection(event) if update_from_frame else None
        if detection is not None:
            self._last_seen_ms = now
            self._last_confidence = detection.confidence
            self._last_class = detection.class_name
            self._last_detection_id = detection.id

        active_until = None if self._last_seen_ms is None else self._last_seen_ms + config.clear_after_ms
        hold_until = None if self._last_seen_ms is None else self._last_seen_ms + config.hold_ms
        active = active_until is not None and now <= active_until
        in_hold = hold_until is not None and now <= hold_until
        recommended = "SAFE_HOLD" if active and in_hold else "FIRE_BLOCKED" if active else "CLEAR"
        status = PersonSafetyGateStatus(
            enabled=True,
            person_detected=active,
            fire_gate_blocked_reason="PERSON_DETECTED" if active else None,
            recommended_state=recommended,
            confidence_threshold=config.confidence_threshold,
            hold_ms=config.hold_ms,
            clear_after_ms=config.clear_after_ms,
            last_detection_confidence=self._last_confidence if active else None,
            last_detection_class=self._last_class if active else None,
            last_detection_id=self._last_detection_id if active else None,
            last_detection_timestamp_ms=self._last_seen_ms if active else None,
            active_until_ms=active_until if active else None,
        )
        self._remember_event(status)
        return status

    def _select_person_detection(self, event: VisionEvent | None) -> BodyDetection | None:
        if event is None:
            return None
        threshold = self.config.person_safety.confidence_threshold
        candidates = [
            detection for detection in event.body_detections
            if detection.class_name.lower() in PERSON_CLASSES and detection.confidence >= threshold
        ]
        return max(candidates, key=lambda item: item.confidence) if candidates else None

    def _remember_event(self, status: PersonSafetyGateStatus) -> None:
        payload = status.model_dump(mode="json")
        payload["canonical_safety_wording"] = (
            "no_physical_command_generated=true; person safety is an additional software gate; "
            "fire remains blocked with PERSON_DETECTED while active."
        )
        should_emit = status.person_detected != self._was_active or (status.person_detected and time.monotonic() - self._last_log_at >= 1.0)
        if (status.person_detected or self._was_active) and should_emit:
            self._last_log_at = time.monotonic()
            event_type = "person_safety.person_detected" if status.person_detected else "person_safety.cleared"
            self.last_event = (event_type, payload)
            self.logger.emit(
                LogLevel.WARN if status.person_detected else LogLevel.INFO,
                "SAFETY",
                "FIRE_BLOCKED: PERSON_DETECTED; no physical command generated"
                if status.person_detected
                else "Person safety gate cleared; no physical command generated",
                payload,
            )
        self._was_active = status.person_detected
