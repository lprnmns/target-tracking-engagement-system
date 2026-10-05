import json
import time
import uuid
from pathlib import Path
from typing import Any

from app.schemas.legacy_perception import (
    LegacyPerceptionPreset,
    LegacyPerceptionPresetList,
    RealCameraEvidence,
    RealCameraEvidenceStatus,
)
from app.schemas.log import LogLevel
from app.services.log_service import JsonlLogService
from app.services.storage_paths import project_root

try:  # pragma: no cover - host dependent
    import cv2
except Exception:  # pragma: no cover
    cv2 = None


class LegacyPerceptionService:
    def __init__(self, logger: JsonlLogService) -> None:
        self.logger = logger
        self.reports_root = project_root() / "reports"
        self.output_root = project_root() / "exports" / "legacy_perception"
        self.output_root.mkdir(parents=True, exist_ok=True)
        self.latest_evidence: RealCameraEvidence | None = None
        self.last_event: tuple[str, dict] | None = None

    def list_presets(self) -> LegacyPerceptionPresetList:
        presets = self._load_presets()
        payload = {
            "presets_count": len(presets),
            "no_physical_command_generated": True,
            "summary": f"Legacy perception presets loaded; presets={len(presets)}; no_physical_command_generated=true.",
        }
        self._event("vision.legacy_presets_loaded", payload, payload["summary"])
        return LegacyPerceptionPresetList(
            presets=presets,
            source_reports=[
                "reports/legacy_perception_candidates.json",
                "reports/legacy_tracker_config_inventory.json",
                "reports/legacy_safety_boundary_review.md",
            ],
            forbidden_runtime_tokens_present=False,
            advisory_only=True,
            no_physical_command_generated=True,
        )

    def get_preset(self, preset_id: str) -> LegacyPerceptionPreset:
        for preset in self._load_presets():
            if preset.preset_id == preset_id:
                return preset
        raise KeyError(preset_id)

    def status(self, camera_runtime) -> RealCameraEvidenceStatus:
        camera_status = camera_runtime.status()
        profile = camera_runtime.profile
        real_source = profile.source_type in {"laptop", "usb"} and bool(profile.device_path or profile.stable_path or profile.device_id)
        latest = self.latest_evidence
        status = "available_for_capture" if real_source else "not_available"
        warnings = [] if real_source else ["real camera source is not configured; mock/surrogate fallback is not used for this evidence endpoint"]
        payload = {
            "camera_source": profile.source_type,
            "camera_device_path": profile.device_path or profile.stable_path or profile.device_id,
            "status": status,
            "no_physical_command_generated": True,
            "physical_command_enabled": False,
            "summary": f"Real camera evidence status checked; status={status}; no_physical_command_generated=true.",
        }
        self._event("vision.real_camera_status_checked", payload, payload["summary"])
        return RealCameraEvidenceStatus(
            status=status,
            camera_source=profile.source_type,
            camera_device_path=profile.device_path or profile.stable_path or profile.device_id,
            frame_origin=latest.frame_origin if latest else ("real_capture_ready" if real_source else "real_camera_not_available"),
            detector=latest.detector if latest else "legacy_opencv_perception_evidence",
            preset_id=latest.preset_id if latest else (self._load_presets()[0].preset_id if self._load_presets() else None),
            frame_width=latest.frame_width if latest else camera_status.actual_width,
            frame_height=latest.frame_height if latest else camera_status.actual_height,
            fps_estimate=latest.fps_estimate if latest else camera_status.actual_fps_measured,
            detections_count=latest.detections_count if latest else 0,
            target_center_metadata=latest.target_center_metadata if latest else {},
            latest_evidence_id=latest.evidence_id if latest else None,
            warnings=warnings,
            advisory_only=True,
            no_physical_command_generated=True,
            physical_command_enabled=False,
        )

    def latest(self) -> RealCameraEvidence:
        if self.latest_evidence is not None:
            return self.latest_evidence
        return RealCameraEvidence(
            evidence_id="none",
            status="not_recorded",
            camera_source="not_available",
            frame_origin="real_camera_not_available",
            preset_id=self._load_presets()[0].preset_id if self._load_presets() else None,
            warnings=["No real camera evidence has been recorded yet."],
            advisory_only=True,
            no_physical_command_generated=True,
            physical_command_enabled=False,
        )

    def capture_evidence(self, camera_runtime, preset_id: str | None = None) -> RealCameraEvidence:
        presets = self._load_presets()
        preset = self._select_preset(preset_id, presets)
        profile = camera_runtime.profile
        real_source = profile.source_type in {"laptop", "usb"} and bool(profile.device_path or profile.stable_path or profile.device_id)
        if not real_source:
            evidence = RealCameraEvidence(
                evidence_id=self._evidence_id(),
                status="real_camera_not_available",
                camera_source=profile.source_type,
                camera_device_path=profile.device_path or profile.stable_path or profile.device_id,
                frame_origin="real_camera_not_available",
                preset_id=preset.preset_id if preset else None,
                frame_width=profile.width,
                frame_height=profile.height,
                fps_estimate=float(profile.fps),
                warnings=["Configured camera source is not a real camera; no mock fallback was used."],
                advisory_only=True,
                no_physical_command_generated=True,
                physical_command_enabled=False,
            )
            return self._record(evidence)

        started = time.time()
        frame, warnings = camera_runtime.live_preview_frame()
        if frame is None:
            evidence = RealCameraEvidence(
                evidence_id=self._evidence_id(),
                status="real_camera_frame_unavailable",
                camera_source=profile.source_type,
                camera_device_path=profile.device_path or profile.stable_path or profile.device_id,
                frame_origin="real_camera_unavailable",
                preset_id=preset.preset_id if preset else None,
                frame_width=profile.width,
                frame_height=profile.height,
                fps_estimate=float(profile.fps),
                warnings=warnings,
                advisory_only=True,
                no_physical_command_generated=True,
                physical_command_enabled=False,
            )
            return self._record(evidence)

        detections = self._detect(frame, preset)
        elapsed = max(time.time() - started, 0.001)
        height, width = frame.shape[:2]
        target = detections[0] if detections else {}
        evidence = RealCameraEvidence(
            evidence_id=self._evidence_id(),
            status="recorded",
            camera_source=profile.source_type,
            camera_device_path=profile.device_path or profile.stable_path or profile.device_id,
            frame_origin="real_capture",
            detector="legacy_opencv_perception_evidence",
            preset_id=preset.preset_id if preset else None,
            frame_width=int(width),
            frame_height=int(height),
            fps_estimate=round(1.0 / elapsed, 3),
            detections_count=len(detections),
            target_center_metadata=target,
            warnings=warnings,
            advisory_only=True,
            no_physical_command_generated=True,
            physical_command_enabled=False,
        )
        return self._record(evidence)

    def capture_host_blocked_evidence(self, diagnostic, preset_id: str | None = None) -> RealCameraEvidence:
        presets = self._load_presets()
        preset = self._select_preset(preset_id, presets)
        evidence = RealCameraEvidence(
            evidence_id=self._evidence_id(),
            status=diagnostic.camera_acceptance_status,
            camera_source=(
                "host_camera_not_detected"
                if not diagnostic.host_camera_devices_detected
                else ("permission_blocked" if "permission" in diagnostic.blocker_reason.lower() else "frame_capture_failed")
            ),
            camera_device_path=None,
            frame_origin="real_camera_not_available" if not diagnostic.host_camera_devices_detected else "real_camera_frame_unavailable",
            detector="legacy_opencv_perception_evidence",
            preset_id=preset.preset_id if preset else None,
            frame_width=None,
            frame_height=None,
            fps_estimate=None,
            detections_count=0,
            target_center_metadata={
                "camera_acceptance_status": diagnostic.camera_acceptance_status,
                "blocker_reason": diagnostic.blocker_reason,
                "host_camera_devices_detected": diagnostic.host_camera_devices_detected,
                "dev_video_entries": diagnostic.dev_video_entries,
                "real_camera_capture_attempted": False,
                "advisory_only": True,
                "no_physical_command_generated": True,
            },
            warnings=[diagnostic.blocker_reason, "No mock/surrogate fallback was used for real camera evidence."],
            advisory_only=True,
            no_physical_command_generated=True,
            physical_command_enabled=False,
        )
        return self._record(evidence)

    def record_camera_host_frame_evidence(self, capture_result: dict, preset_id: str | None = None) -> RealCameraEvidence:
        presets = self._load_presets()
        preset = self._select_preset(preset_id, presets)
        status = "recorded" if capture_result.get("frame_captured") else str(capture_result.get("status", "partial"))
        evidence = RealCameraEvidence(
            evidence_id=self._evidence_id(),
            status=status,
            camera_source=str(capture_result.get("camera_kind") or ("real_camera" if capture_result.get("frame_captured") else "frame_capture_failed")),
            camera_device_path=capture_result.get("selected_camera_device") or capture_result.get("device_path"),
            frame_origin="real_capture" if capture_result.get("frame_captured") else "real_camera_frame_unavailable",
            detector="legacy_opencv_perception_evidence",
            preset_id=preset.preset_id if preset else None,
            frame_width=capture_result.get("width"),
            frame_height=capture_result.get("height"),
            fps_estimate=capture_result.get("fps_estimate"),
            detections_count=0,
            target_center_metadata={
                "frame_hash": capture_result.get("frame_hash"),
                "frame_sha256": capture_result.get("frame_sha256") or capture_result.get("frame_hash"),
                "frame_path": capture_result.get("frame_path"),
                "capture_method": capture_result.get("capture_method"),
                "selected_camera_device": capture_result.get("selected_camera_device") or capture_result.get("device_path"),
                "selected_camera_name": capture_result.get("selected_camera_name"),
                "camera_kind": capture_result.get("camera_kind", "unknown_camera"),
                "camera_acceptance_status": capture_result.get("status"),
                "blocker_reason": capture_result.get("blocker_reason"),
                "advisory_only": True,
                "no_physical_command_generated": True,
            },
            warnings=[] if capture_result.get("frame_captured") else [str(capture_result.get("blocker_reason", "frame capture failed"))],
            advisory_only=True,
            no_physical_command_generated=True,
            physical_command_enabled=False,
        )
        return self._record(evidence)

    def presets_json(self) -> str:
        return json.dumps(self.list_presets().model_dump(mode="json"), indent=2)

    def migration_summary_markdown(self) -> str:
        presets = self._load_presets()
        lines = [
            "# Legacy Perception Migration Summary",
            "",
            "Legacy tracker audit outputs were converted into advisory perception presets only.",
            "",
            "- advisory_only=true",
            "- no_physical_command_generated=true",
            "- physical_command_enabled=false",
            "- motor/servo/fire/GPIO/PWM/STEP/DIR path not enabled",
            "",
            "## Presets",
            "",
        ]
        for preset in presets:
            lines.append(f"- `{preset.preset_id}` from `{preset.source_file}` risk={preset.risk_class}")
        return "\n".join(lines) + "\n"

    def evidence_summary_markdown(self) -> str:
        latest = self.latest()
        return f"""# Real Camera Evidence Summary

- Evidence ID: {latest.evidence_id}
- Status: {latest.status}
- Camera source: {latest.camera_source}
- Camera device path: {latest.camera_device_path or 'not_available'}
- Frame origin: {latest.frame_origin}
- Detector: {latest.detector}
- Preset ID: {latest.preset_id or 'not_available'}
- Frame size: {latest.frame_width or 'not_available'}x{latest.frame_height or 'not_available'}
- FPS estimate: {latest.fps_estimate if latest.fps_estimate is not None else 'not_available'}
- Detections count: {latest.detections_count}
- advisory_only=true
- no_physical_command_generated=true
- physical_command_enabled=false

Real camera evidence is image-processing metadata only. It does not enable motor, servo, fire, GPIO, PWM, STEP/DIR or physical serial command paths.
"""

    def latest_json(self) -> str:
        return json.dumps(self.latest().model_dump(mode="json"), indent=2)

    def _record(self, evidence: RealCameraEvidence) -> RealCameraEvidence:
        self.latest_evidence = evidence
        path = self.output_root / "real_camera_evidence_latest.json"
        path.write_text(self.latest_json(), encoding="utf-8")
        payload = {
            **evidence.model_dump(mode="json"),
            "summary": (
                f"Real camera evidence recorded; status={evidence.status}; "
                "no_physical_command_generated=true."
            ),
        }
        self._event("vision.real_camera_evidence_recorded", payload, payload["summary"])
        return evidence

    def _detect(self, frame: Any, preset: LegacyPerceptionPreset | None) -> list[dict[str, Any]]:
        if cv2 is None or preset is None:
            return []
        blur = preset.blur_kernel if isinstance(preset.blur_kernel, list) else [preset.blur_kernel or 9, preset.blur_kernel or 9]
        kx = int(blur[0]) if blur else 9
        ky = int(blur[1]) if blur else 9
        if kx % 2 == 0:
            kx += 1
        if ky % 2 == 0:
            ky += 1
        hsv = cv2.cvtColor(cv2.GaussianBlur(frame, (kx, ky), 0), cv2.COLOR_BGR2HSV)
        ranges: list[tuple[list[int], list[int]]] = []
        if isinstance(preset.hsv_lower, list) and isinstance(preset.hsv_upper, list):
            lowers = preset.hsv_lower if preset.hsv_lower and isinstance(preset.hsv_lower[0], list) else [preset.hsv_lower]
            uppers = preset.hsv_upper if preset.hsv_upper and isinstance(preset.hsv_upper[0], list) else [preset.hsv_upper]
            ranges = [(list(lower), list(upper)) for lower, upper in zip(lowers, uppers) if lower and upper]
        if not ranges:
            return []
        mask = None
        for lower, upper in ranges:
            current = cv2.inRange(hsv, tuple(lower), tuple(upper))
            mask = current if mask is None else cv2.bitwise_or(mask, current)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        detections: list[dict[str, Any]] = []
        for contour in contours:
            area = float(cv2.contourArea(contour))
            if preset.min_area is not None and area < preset.min_area:
                continue
            if preset.max_area is not None and area > preset.max_area:
                continue
            perimeter = float(cv2.arcLength(contour, True))
            circularity = 0.0 if perimeter <= 0 else float(4 * 3.14159265 * area / (perimeter * perimeter))
            if preset.circularity_min is not None and circularity < preset.circularity_min:
                continue
            moments = cv2.moments(contour)
            if moments["m00"] == 0:
                continue
            x, y, w, h = cv2.boundingRect(contour)
            detections.append(
                {
                    "center_x": round(moments["m10"] / moments["m00"], 3),
                    "center_y": round(moments["m01"] / moments["m00"], 3),
                    "bbox_xywh_pixel": [int(x), int(y), int(w), int(h)],
                    "area": round(area, 3),
                    "circularity": round(circularity, 3),
                    "selection_rule": preset.target_selection_rule,
                    "advisory_only": True,
                    "no_physical_command_generated": True,
                }
            )
        return sorted(detections, key=lambda item: item["area"], reverse=True)

    def _select_preset(self, preset_id: str | None, presets: list[LegacyPerceptionPreset]) -> LegacyPerceptionPreset | None:
        if not presets:
            return None
        if preset_id is None:
            return next((preset for preset in presets if preset.hsv_lower), presets[0])
        return next((preset for preset in presets if preset.preset_id == preset_id), presets[0])

    def _load_presets(self) -> list[LegacyPerceptionPreset]:
        candidates = self._read_json(self.reports_root / "legacy_perception_candidates.json").get("candidates", [])
        presets: list[LegacyPerceptionPreset] = []
        for item in candidates:
            name = str(item.get("name", "legacy_perception"))
            source = str(item.get("source", "unknown"))
            values = item.get("values", {}) if isinstance(item.get("values"), dict) else {}
            risk = str(item.get("risk", "low"))
            if name.startswith("camera_profile"):
                width, height = self._parse_resolution(values.get("resolution"))
                presets.append(
                    LegacyPerceptionPreset(
                        preset_id=f"legacy_{name}",
                        source_file=source,
                        camera_index=values.get("camera_index"),
                        width=width,
                        height=height,
                        fps=values.get("fps"),
                        color_space="BGR",
                        notes="Legacy camera profile candidate; use for real camera evidence configuration only.",
                        risk_class=risk,
                    )
                )
            elif name == "threaded_camera_low_latency":
                presets.append(
                    LegacyPerceptionPreset(
                        preset_id="legacy_threaded_camera_low_latency",
                        source_file=source,
                        fps=values.get("fps"),
                        color_space="BGR",
                        notes="Low-latency camera tuning metadata; no capture command or hardware output is migrated.",
                        risk_class=risk,
                    )
                )
            elif name == "opencv_hsv_red_pink_detector":
                presets.append(
                    LegacyPerceptionPreset(
                        preset_id="legacy_hsv_red_pink_balanced",
                        source_file=source,
                        color_space="HSV",
                        hsv_lower=[values.get("lower_red1", []), values.get("lower_red2", [])],
                        hsv_upper=[values.get("upper_red1", []), values.get("upper_red2", [])],
                        blur_kernel=values.get("blur_kernel"),
                        morphology_kernel=values.get("morph_kernel"),
                        min_area=values.get("min_area"),
                        max_area=None,
                        circularity_min=0.3,
                        target_selection_rule="largest_area_then_closest_to_crosshair",
                        smoothing_enabled=False,
                        kalman_enabled=False,
                        notes="OpenCV HSV contour detector candidate from legacy fallback detector.",
                        risk_class=risk,
                    )
                )
            elif name == "color_tuner_presets":
                balanced = values.get("balanced", {})
                presets.append(
                    LegacyPerceptionPreset(
                        preset_id="legacy_color_tuner_balanced",
                        source_file=source,
                        color_space="HSV",
                        blur_kernel=balanced.get("blur_size"),
                        min_area=balanced.get("min_area"),
                        target_selection_rule="operator_tuned_color_preset",
                        notes="Balanced color tuner metadata; HSV ranges remain configured by operator.",
                        risk_class=risk,
                    )
                )
            elif name == "target_selection":
                presets.append(
                    LegacyPerceptionPreset(
                        preset_id="legacy_target_selection_metadata",
                        source_file=source,
                        target_selection_rule=str(values.get("auto", "closest_to_crosshair")),
                        smoothing_enabled=True,
                        kalman_enabled=bool(values.get("kalman_prediction", False)),
                        notes="Target selection and prediction metadata only; tracking-to-motion is not migrated.",
                        risk_class=risk,
                    )
                )
        return presets

    def _read_json(self, path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
        return loaded if isinstance(loaded, dict) else {}

    def _parse_resolution(self, value: Any) -> tuple[int | None, int | None]:
        if not isinstance(value, str) or "x" not in value:
            return None, None
        left, right = value.lower().split("x", 1)
        try:
            return int(left), int(right)
        except ValueError:
            return None, None

    def _evidence_id(self) -> str:
        return f"real_camera_evidence_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"

    def _event(self, event_type: str, payload: dict, message: str) -> None:
        safe_payload = {
            **payload,
            "advisory_only": True,
            "no_physical_command_generated": True,
            "physical_command_enabled": False,
        }
        self.last_event = (event_type, safe_payload)
        self.logger.emit(LogLevel.INFO, "VISION", message, {"type": event_type, **safe_payload})
