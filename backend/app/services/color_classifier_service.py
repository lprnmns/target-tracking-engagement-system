import hashlib
import json
import time
from collections import deque
from pathlib import Path

from app.schemas.color import (
    ColorClassifierConfig,
    ColorCalibrationReference,
    ColorCalibrationReferenceRequest,
    ColorCalibrationStatus,
    ColorClassifySampleRequest,
    ColorDecisionResult,
    MaskPreviewResult,
    TeamValue,
)
from app.schemas.config import AppConfig
from app.schemas.log import LogLevel
from app.schemas.vision import BalloonDetection, BodyDetection
from app.services.log_service import JsonlLogService

try:  # pragma: no cover - availability depends on deployment image
    import cv2
    import numpy as np
except Exception:  # pragma: no cover
    cv2 = None
    np = None


class ColorClassifierService:
    def __init__(self, config: AppConfig, logger: JsonlLogService, calibration_path: Path | None = None) -> None:
        self.config = config
        self.logger = logger
        self.classifier_config = ColorClassifierConfig(
            color_space=config.color.color_space,
            enemy_hsv_ranges=config.color.enemy_hsv_ranges,
            friend_hsv_ranges=config.color.friend_hsv_ranges,
            saturation_min=config.color.saturation_min,
            value_min=config.color.value_min,
            lab_enabled=config.color.lab_enabled,
            min_body_pixels=config.color.min_body_pixels,
            decision_threshold=config.color.decision_threshold,
            temporal_window=config.color.temporal_window,
            required_consistent_frames=config.color.required_consistent_frames,
            balloon_mask_enabled=config.color.balloon_mask_enabled,
            balloon_hsv_ranges=config.color.balloon_hsv_ranges,
            morphology_kernel=config.color.morphology_kernel,
            updated_at=time.time(),
        )
        self.latest_result: ColorDecisionResult | None = None
        self._latest_by_track: dict[int, ColorDecisionResult] = {}
        self._history_by_track: dict[int, deque[ColorDecisionResult]] = {}
        self._last_trace_at_by_track: dict[int, float] = {}
        self._last_trace_decision_by_track: dict[int, str] = {}
        self.calibration_path = calibration_path
        self._calibration_references: list[ColorCalibrationReference] = []
        self.last_event: tuple[str, dict] | None = None
        self._load_calibration()

    def get_config(self) -> ColorClassifierConfig:
        return self.classifier_config

    def update_config(self, update: ColorClassifierConfig) -> ColorClassifierConfig:
        self.classifier_config = update.model_copy(update={"updated_at": time.time()})
        self._latest_by_track.clear()
        self._history_by_track.clear()
        self._last_trace_at_by_track.clear()
        self._last_trace_decision_by_track.clear()
        self._calibration_references.clear()
        self._persist_calibration()
        payload = self.classifier_config.model_dump(mode="json")
        self.last_event = ("color.config_updated", payload)
        self.logger.emit(LogLevel.INFO, "COLOR", "Color config updated", payload)
        return self.classifier_config

    def classify_sample(self, request: ColorClassifySampleRequest) -> ColorDecisionResult:
        pixel_count = request.body_pixel_count or max(self.classifier_config.min_body_pixels + 120, 320)
        balloon_mask_applied = self.classifier_config.balloon_mask_enabled and request.balloon_bbox_present
        warnings: list[str] = []
        if self.classifier_config.balloon_mask_enabled and not balloon_mask_applied:
            warnings.append("balloon_mask_not_applied")
        if pixel_count < self.classifier_config.min_body_pixels:
            warnings.append("insufficient_body_pixels")

        enemy_ratio, friend_ratio, unknown_ratio = self._ratios_for(request.mock_team)
        decision = self._decision(enemy_ratio, friend_ratio, unknown_ratio, pixel_count)
        confidence = max(enemy_ratio, friend_ratio, unknown_ratio)
        if request.mock_team == TeamValue.UNKNOWN:
            confidence = unknown_ratio
        result = ColorDecisionResult(
            frame_id=request.frame_id,
            detection_id=request.detection_id,
            body_crop_bbox=request.body_crop_bbox,
            balloon_mask_applied=balloon_mask_applied,
            body_pixel_count=pixel_count,
            enemy_pixel_ratio=enemy_ratio,
            friend_pixel_ratio=friend_ratio,
            unknown_pixel_ratio=unknown_ratio,
            decision=decision,
            confidence=round(confidence, 3),
            blocking_warnings=warnings,
            debug_masks_available=True,
            evidence_source="mock_sample",
            body_track_id=None,
            temporal_frames=0,
            consistent_frames=0,
            profile_hash=self.profile_hash(),
            usable_for_live_fire=False,
            updated_at=time.time(),
        )
        self.latest_result = result
        payload = result.model_dump(mode="json")
        self.last_event = ("color.classification", payload)
        self.logger.emit(LogLevel.INFO, "COLOR", "Color classification sample evaluated", payload)
        if warnings:
            self.last_event = ("color.warning", payload)
            self.logger.emit(LogLevel.WARN, "COLOR", "Color classification warning", {"warnings": warnings})
        return result

    def latest(self) -> ColorDecisionResult | None:
        return self.latest_result

    def latest_for_body(self, body: BodyDetection) -> ColorDecisionResult | None:
        if body.track_id is not None:
            return self._latest_by_track.get(body.track_id)
        if self.latest_result and self.latest_result.detection_id == body.id:
            return self.latest_result
        return None

    def profile_hash(self) -> str:
        payload = self.classifier_config.model_dump(mode="json", exclude={"updated_at"})
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def calibration_status(self) -> ColorCalibrationStatus:
        profile_hash = self.profile_hash()
        references = [item for item in self._calibration_references if item.profile_hash == profile_hash]
        enemy = [item for item in references if item.expected_team == TeamValue.ENEMY]
        friend = [item for item in references if item.expected_team == TeamValue.FRIEND]
        valid = len(enemy) >= 3 and len(friend) >= 3
        reasons: list[str] = []
        if len(enemy) < 3:
            reasons.append("A3_IFF_ENEMY_REFERENCE_INSUFFICIENT")
        if len(friend) < 3:
            reasons.append("A3_IFF_FRIEND_REFERENCE_INSUFFICIENT")
        if not valid and not reasons:
            reasons.append("A3_IFF_CALIBRATION_REQUIRED")
        return ColorCalibrationStatus(
            valid=valid,
            profile_hash=profile_hash,
            enemy_reference_count=len(enemy),
            friend_reference_count=len(friend),
            references=references,
            reason_codes=reasons,
        )

    def record_calibration_reference(self, request: ColorCalibrationReferenceRequest) -> ColorCalibrationStatus:
        result = self.latest_result
        if result is None or result.evidence_source != "real_body_roi":
            raise ValueError("A3_IFF_REAL_ROI_EVIDENCE_REQUIRED")
        if result.decision != request.expected_team:
            raise ValueError("A3_IFF_REFERENCE_TEAM_MISMATCH")
        if result.body_pixel_count < self.classifier_config.min_body_pixels:
            raise ValueError("A3_IFF_REFERENCE_PIXELS_INSUFFICIENT")
        if request.expected_team == TeamValue.ENEMY and not result.usable_for_live_fire:
            raise ValueError("A3_IFF_REFERENCE_TEMPORAL_EVIDENCE_REQUIRED")
        profile_hash = self.profile_hash()
        if any(
            item.expected_team == request.expected_team
            and (item.capture_id == request.capture_id or item.frame_id == result.frame_id)
            and item.profile_hash == profile_hash
            for item in self._calibration_references
        ):
            raise ValueError("A3_IFF_REFERENCE_DUPLICATE")
        self._calibration_references.append(
            ColorCalibrationReference(
                expected_team=request.expected_team,
                capture_id=request.capture_id,
                frame_id=result.frame_id,
                detection_id=result.detection_id,
                body_track_id=result.body_track_id,
                body_pixel_count=result.body_pixel_count,
                decision=result.decision,
                confidence=result.confidence,
                profile_hash=profile_hash,
                frame_hash=result.frame_hash,
            )
        )
        self._persist_calibration()
        status = self.calibration_status()
        self.last_event = ("color.calibration_reference_recorded", status.model_dump(mode="json"))
        self.logger.emit(LogLevel.INFO, "COLOR", "IFF calibration reference recorded", status.model_dump(mode="json"))
        return status

    def reset_calibration(self) -> ColorCalibrationStatus:
        self._calibration_references.clear()
        self._persist_calibration()
        status = self.calibration_status()
        self.last_event = ("color.calibration_reset", status.model_dump(mode="json"))
        return status

    def classify_frame_bodies(
        self,
        frame,
        frame_id: int,
        bodies: list[BodyDetection],
        balloons: list[BalloonDetection],
    ) -> list[BodyDetection]:
        """Classify body-only ROIs and accumulate results per stable body track.

        The balloon bounding boxes are *removed* from the ROI before HSV
        statistics are calculated.  Balloon hue therefore cannot affect IFF.
        A missing OpenCV/Numpy runtime yields UNKNOWN evidence, never a guess.
        """
        if frame is None or cv2 is None or np is None:
            return [self._unavailable_body(body, frame_id, "iff_frame_processing_unavailable") for body in bodies]
        classified: list[BodyDetection] = []
        for body in bodies:
            result = self._classify_body_roi(frame, frame_id, body, balloons)
            if body.track_id is not None:
                history = self._history_by_track.setdefault(body.track_id, deque(maxlen=self.classifier_config.temporal_window))
                history.append(result)
                result = self._with_temporal_evidence(result, history)
                self._latest_by_track[body.track_id] = result
            self.latest_result = result
            payload = result.model_dump(mode="json")
            self.last_event = ("color.real_roi_classification", payload)
            trace_key = int(body.track_id if body.track_id is not None else -body.id)
            trace_now = time.monotonic()
            decision_value = str(result.decision)
            if (
                self._last_trace_decision_by_track.get(trace_key) != decision_value
                or trace_now - self._last_trace_at_by_track.get(trace_key, 0.0) >= 1.0
            ):
                self.logger.emit(LogLevel.INFO, "COLOR", "Body ROI IFF evaluated", payload)
                self._last_trace_at_by_track[trace_key] = trace_now
                self._last_trace_decision_by_track[trace_key] = decision_value
            # Field rule: FRIEND is the only exceptional class. Everything
            # that does not positively match the friend colour profile is an
            # enemy target; temporal evidence remains available in the debug
            # payload but no longer creates an operator-visible UNKNOWN team.
            team = TeamValue.FRIEND.value if result.decision == TeamValue.FRIEND else TeamValue.ENEMY.value
            classified.append(body.model_copy(update={"target_team": team, "color_decision": payload}))
        return classified

    def real_iff_ready_for(self, body: BodyDetection | None, frame_id: int | None = None) -> tuple[bool, str]:
        if body is None:
            return False, "No body is available for real ROI IFF."
        result = self.latest_for_body(body)
        if result is None:
            return False, "No current body-ROI IFF result exists."
        if result.evidence_source != "real_body_roi":
            return False, "IFF evidence source is not a real body ROI."
        calibration = self.calibration_status()
        if not calibration.valid:
            return False, "Real IFF field calibration is incomplete: " + ", ".join(calibration.reason_codes) + "."
        if frame_id is not None and result.frame_id != frame_id:
            return False, "Real body-ROI IFF evidence does not belong to the current frame."
        if not result.usable_for_live_fire:
            warnings = ", ".join(result.blocking_warnings) or "temporal IFF requirement not met"
            return False, f"Real body-ROI IFF is not ready: {warnings}."
        return True, "Real body-ROI temporal IFF evidence is current."

    def reset(self) -> dict[str, bool]:
        self.latest_result = None
        self._latest_by_track.clear()
        self._history_by_track.clear()
        self._last_trace_at_by_track.clear()
        self._last_trace_decision_by_track.clear()
        self._calibration_references.clear()
        self._persist_calibration()
        self.last_event = ("color.config_updated", {"reset": True})
        self.logger.emit(LogLevel.INFO, "COLOR", "Color classifier reset", {"reset": True})
        return {"reset": True}

    def preview_mask(self, request: ColorClassifySampleRequest) -> MaskPreviewResult:
        applied = self.classifier_config.balloon_mask_enabled and request.balloon_bbox_present
        warnings = [] if applied else ["balloon_mask_not_applied"]
        result = MaskPreviewResult(
            frame_id=request.frame_id,
            detection_id=request.detection_id,
            balloon_mask_enabled=self.classifier_config.balloon_mask_enabled,
            balloon_mask_applied=applied,
            debug_masks_available=True,
            warnings=warnings,
            updated_at=time.time(),
        )
        payload = result.model_dump(mode="json")
        self.last_event = ("color.mask_preview", payload)
        self.logger.emit(LogLevel.INFO if applied else LogLevel.WARN, "COLOR", "Color mask preview evaluated", payload)
        return result

    def _ratios_for(self, team: TeamValue) -> tuple[float, float, float]:
        if team == TeamValue.ENEMY:
            return 0.72, 0.06, 0.22
        if team == TeamValue.FRIEND:
            return 0.08, 0.74, 0.18
        return 0.2, 0.2, 0.6

    def _decision(
        self,
        enemy_ratio: float,
        friend_ratio: float,
        unknown_ratio: float,
        pixel_count: int,
        enemy_pixels: int = 0,
        friend_pixels: int = 0,
    ) -> TeamValue:
        if pixel_count < self.classifier_config.min_body_pixels:
            return TeamValue.UNKNOWN

        # Doğrudan Mavi vs Kırmızı piksel karşılaştırması (sıfır gecikme, 0 ms):
        # Poligonda hedef ya Dost (mavi) ya Düşmandır (kırmızı).
        # Uçağın gövde/ahşap/gölge alanı ne olursa olsun mavi kırmızıdan fazlaysa DOST,
        # kırmızı maviden fazlaysa DÜŞMAN olarak sınıflandırılır.
        total_color_pixels = enemy_pixels + friend_pixels
        if total_color_pixels >= 10:
            if friend_pixels > enemy_pixels:
                return TeamValue.FRIEND
            if enemy_pixels > friend_pixels:
                return TeamValue.ENEMY

        if friend_ratio >= self.classifier_config.decision_threshold:
            return TeamValue.FRIEND
        if enemy_ratio >= self.classifier_config.decision_threshold:
            return TeamValue.ENEMY
        if unknown_ratio >= self.classifier_config.decision_threshold:
            return TeamValue.UNKNOWN
        return TeamValue.UNKNOWN

    def _unavailable_body(self, body: BodyDetection, frame_id: int, warning: str) -> BodyDetection:
        result = ColorDecisionResult(
            frame_id=frame_id,
            detection_id=body.id,
            body_crop_bbox=body.bbox,
            balloon_mask_applied=False,
            body_pixel_count=0,
            enemy_pixel_ratio=0.0,
            friend_pixel_ratio=0.0,
            unknown_pixel_ratio=1.0,
            decision=TeamValue.UNKNOWN,
            confidence=1.0,
            blocking_warnings=[warning],
            debug_masks_available=False,
            evidence_source="real_body_roi",
            body_track_id=body.track_id,
            profile_hash=self.profile_hash(),
            usable_for_live_fire=False,
            updated_at=time.time(),
        )
        if body.track_id is not None:
            self._latest_by_track[body.track_id] = result
        self.latest_result = result
        return body.model_copy(update={"target_team": TeamValue.ENEMY.value, "color_decision": result.model_dump(mode="json")})

    def _classify_body_roi(self, frame, frame_id: int, body: BodyDetection, balloons: list[BalloonDetection]) -> ColorDecisionResult:
        frame_h, frame_w = int(frame.shape[0]), int(frame.shape[1])
        x1 = max(0, min(frame_w - 1, body.bbox.x))
        y1 = max(0, min(frame_h - 1, body.bbox.y))
        x2 = max(x1 + 1, min(frame_w, body.bbox.x + body.bbox.w))
        y2 = max(y1 + 1, min(frame_h, body.bbox.y + body.bbox.h))
        crop = frame[y1:y2, x1:x2]
        warnings: list[str] = []
        if crop.size == 0:
            return self._empty_result(frame_id, body, warnings + ["empty_body_roi"])
        hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
        eligible = (hsv[:, :, 1] >= self.classifier_config.saturation_min) & (hsv[:, :, 2] >= self.classifier_config.value_min)
        balloon_mask_applied = False
        if self.classifier_config.balloon_mask_enabled:
            for balloon in balloons:
                bx1 = max(x1, balloon.bbox.x)
                by1 = max(y1, balloon.bbox.y)
                bx2 = min(x2, balloon.bbox.x + balloon.bbox.w)
                by2 = min(y2, balloon.bbox.y + balloon.bbox.h)
                if bx1 < bx2 and by1 < by2:
                    eligible[by1 - y1:by2 - y1, bx1 - x1:bx2 - x1] = False
                    balloon_mask_applied = True
        # No balloon in this frame does not invalidate body-only IFF.  A
        # balloon, when present and overlapping, is always excluded; its
        # absence is handled by the separate association/balloon gate.
        if self.classifier_config.balloon_mask_enabled and balloons and not balloon_mask_applied:
            warnings.append("balloon_mask_not_applied")
        enemy_mask = self._hsv_ranges_mask(hsv, self.classifier_config.enemy_hsv_ranges) & eligible
        friend_mask = self._hsv_ranges_mask(hsv, self.classifier_config.friend_hsv_ranges) & eligible
        overlap = enemy_mask & friend_mask
        if bool(overlap.any()):
            enemy_mask &= ~overlap
            friend_mask &= ~overlap
            warnings.append("iff_color_profile_overlap")
        pixel_count = int(eligible.sum())
        if pixel_count < self.classifier_config.min_body_pixels:
            warnings.append("insufficient_body_pixels")
        enemy_pixels = int(enemy_mask.sum())
        friend_pixels = int(friend_mask.sum())
        enemy_ratio = float(enemy_pixels) / max(pixel_count, 1)
        friend_ratio = float(friend_pixels) / max(pixel_count, 1)
        unknown_ratio = max(0.0, 1.0 - enemy_ratio - friend_ratio)
        decision = self._decision(
            enemy_ratio,
            friend_ratio,
            unknown_ratio,
            pixel_count,
            enemy_pixels=enemy_pixels,
            friend_pixels=friend_pixels,
        )
        if enemy_ratio >= self.classifier_config.decision_threshold and friend_ratio >= self.classifier_config.decision_threshold:
            decision = TeamValue.UNKNOWN
            warnings.append("iff_color_ambiguous")
        frame_hash = hashlib.sha256(crop.tobytes()).hexdigest()
        return ColorDecisionResult(
            frame_id=frame_id,
            detection_id=body.id,
            body_crop_bbox=body.bbox,
            balloon_mask_applied=balloon_mask_applied,
            body_pixel_count=pixel_count,
            enemy_pixel_ratio=round(enemy_ratio, 5),
            friend_pixel_ratio=round(friend_ratio, 5),
            unknown_pixel_ratio=round(unknown_ratio, 5),
            decision=decision,
            confidence=round(max(enemy_ratio, friend_ratio, unknown_ratio), 5),
            blocking_warnings=warnings,
            debug_masks_available=True,
            evidence_source="real_body_roi",
            body_track_id=body.track_id,
            profile_hash=self.profile_hash(),
            frame_hash=frame_hash,
            usable_for_live_fire=False,
            updated_at=time.time(),
        )

    def _with_temporal_evidence(self, result: ColorDecisionResult, history: deque[ColorDecisionResult]) -> ColorDecisionResult:
        recent = list(history)
        same = 0
        for item in reversed(recent):
            if item.decision != result.decision or item.blocking_warnings:
                break
            same += 1
        usable = (
            result.decision == TeamValue.ENEMY
            and same >= self.classifier_config.required_consistent_frames
            and result.body_pixel_count >= self.classifier_config.min_body_pixels
            and not result.blocking_warnings
        )
        warnings = list(result.blocking_warnings)
        if result.decision == TeamValue.UNKNOWN:
            warnings.append("iff_unknown")
        if result.decision == TeamValue.ENEMY and same < self.classifier_config.required_consistent_frames:
            warnings.append("iff_temporal_consensus_pending")
        return result.model_copy(
            update={
                "blocking_warnings": sorted(set(warnings)),
                "temporal_frames": len(recent),
                "consistent_frames": same,
                "usable_for_live_fire": usable,
            }
        )

    def _empty_result(self, frame_id: int, body: BodyDetection, warnings: list[str]) -> ColorDecisionResult:
        return ColorDecisionResult(
            frame_id=frame_id,
            detection_id=body.id,
            body_crop_bbox=body.bbox,
            balloon_mask_applied=False,
            body_pixel_count=0,
            enemy_pixel_ratio=0.0,
            friend_pixel_ratio=0.0,
            unknown_pixel_ratio=1.0,
            decision=TeamValue.UNKNOWN,
            confidence=1.0,
            blocking_warnings=warnings,
            debug_masks_available=False,
            evidence_source="real_body_roi",
            body_track_id=body.track_id,
            profile_hash=self.profile_hash(),
            usable_for_live_fire=False,
            updated_at=time.time(),
        )

    @staticmethod
    def _hsv_ranges_mask(hsv, ranges) -> "np.ndarray":
        mask = np.zeros(hsv.shape[:2], dtype=bool)
        for item in ranges:
            lower = np.array([item.h_min, item.s_min, item.v_min], dtype=np.uint8)
            upper = np.array([item.h_max, 255, 255], dtype=np.uint8)
            mask |= cv2.inRange(hsv, lower, upper).astype(bool)
        return mask

    def _load_calibration(self) -> None:
        if self.calibration_path is None:
            return
        try:
            content = self.calibration_path.read_text(encoding="utf-8")
            raw = json.loads(content)
            if isinstance(raw, list):
                self._calibration_references = [ColorCalibrationReference.model_validate(item) for item in raw]
        except (OSError, ValueError):
            self._calibration_references = []

    def _persist_calibration(self) -> None:
        if self.calibration_path is None:
            return
        self.calibration_path.parent.mkdir(parents=True, exist_ok=True)
        payload = [item.model_dump(mode="json") for item in self._calibration_references]
        self.calibration_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
