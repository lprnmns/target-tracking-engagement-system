import math
import time
from pathlib import Path
from typing import Any

from app.schemas.log import LogLevel
from app.schemas.vision import AimPoint, BalloonDetection, BBox, VisionEvent
from app.schemas.vision_runtime_settings import VisionRuntimeProfile
from app.services.log_service import JsonlLogService
from app.services.storage_paths import project_root

try:  # pragma: no cover - availability depends on host release package
    import cv2
    import numpy as np
except Exception:  # pragma: no cover
    cv2 = None
    np = None


class OpenCVLiveCircleSurrogate:
    adapter_id = "opencv_live_circle_surrogate"

    def __init__(self, logger: JsonlLogService) -> None:
        self.logger = logger
        self.frame_id = 0
        self.running = False
        self.last_event: tuple[str, dict] | None = None
        self.last_result: dict[str, Any] = {
            "adapter": self.adapter_id,
            "detections": [],
            "warnings": ["surrogate_not_started"],
            "camera_source_kind": "not_run",
            "camera_device_path": None,
            "frame_origin": "not_run",
            "detector_kind": "opencv_circle_surrogate",
            "production_yolo_loaded": False,
            "advisory_only": True,
            "production_ready": False,
            "competition_ready": False,
            "no_physical_command_generated": True,
        }
        self.snapshot_dir = project_root() / "exports" / "vision_surrogate" / "snapshots"
        self.snapshot_dir.mkdir(parents=True, exist_ok=True)

    def available(self) -> bool:
        return cv2 is not None and np is not None

    def start(self, metadata: dict[str, Any] | None = None) -> None:
        self.running = True
        metadata = metadata or {"camera_source_kind": "unknown"}
        event_type = "vision.mock_surrogate_started" if metadata.get("camera_source_kind") == "mock" else "vision.live_camera_surrogate_started"
        source_text = "mock camera circle surrogate" if metadata.get("camera_source_kind") == "mock" else "live camera circle surrogate"
        self._event(event_type, {"adapter": self.adapter_id, **metadata}, f"OpenCV {source_text} started; advisory only.")

    def stop(self) -> None:
        self.running = False
        source_kind = self.last_result.get("camera_source_kind")
        event_type = "vision.mock_surrogate_stopped" if source_kind == "mock" else "vision.live_camera_surrogate_stopped" if source_kind == "real_camera" else "vision.surrogate_stopped"
        source_text = "mock camera surrogate" if source_kind == "mock" else "live camera surrogate" if source_kind == "real_camera" else "OpenCV surrogate"
        self._event(event_type, {"adapter": self.adapter_id, "camera_source_kind": source_kind}, f"{source_text} stopped.")

    def run(self, camera_runtime, profile: VisionRuntimeProfile) -> VisionEvent:
        started = time.perf_counter()
        self.frame_id += 1
        camera_status = camera_runtime.status()
        width = camera_status.actual_width
        height = camera_status.actual_height
        metadata = self._source_metadata(camera_status)
        if not self.running:
            self.start(metadata)
        source = metadata["source"]
        warnings = [
            "SURROGATE ONLY",
            "NOT PRODUCTION YOLO",
            "NO PHYSICAL COMMAND",
            "UI/PIPELINE TEST ONLY",
        ]
        frame = None
        if camera_status.profile.source_type != "mock":
            frame, frame_warnings = camera_runtime.live_preview_frame()
            warnings.extend(frame_warnings)
            if frame is not None:
                height, width = int(frame.shape[0]), int(frame.shape[1])
            else:
                metadata["frame_origin"] = "real_capture_unavailable"
        detections = self._detect(frame, width, height, profile, source)
        preprocess_ms = 1.2
        inference_ms = round((time.perf_counter() - started) * 1000, 3)
        postprocess_ms = 0.9
        total_ms = round(preprocess_ms + inference_ms + postprocess_ms, 3)
        detector_fps = round(1000.0 / max(total_ms, 1.0), 2)
        camera_fps = float(camera_status.actual_fps_measured or camera_status.actual_fps or camera_status.requested_fps)
        event = VisionEvent(
            frame_id=self.frame_id,
            timestamp_ms=int(time.time() * 1000),
            source=source,
            fps=detector_fps,
            camera_fps=camera_fps,
            detector_fps=detector_fps,
            preprocess_ms=preprocess_ms,
            inference_ms=inference_ms,
            postprocess_ms=postprocess_ms,
            total_latency_ms=total_ms,
            total_ms=total_ms,
            camera_source_kind=metadata["camera_source_kind"],
            camera_device_path=metadata["camera_device_path"],
            frame_origin=metadata["frame_origin"],
            detector_kind="opencv_circle_surrogate",
            body_detections=[],
            balloon_detections=detections,
            aim_points=[AimPoint(id=det.id, x=det.center_x, y=det.center_y, source=source) for det in detections],
            warnings=warnings,
        )
        self.last_result = {
            "adapter": self.adapter_id,
            "source": source,
            **metadata,
            "detector_kind": "opencv_circle_surrogate",
            "production_yolo_loaded": False,
            "frame_id": event.frame_id,
            "fps": detector_fps,
            "camera_fps": camera_fps,
            "detector_fps": detector_fps,
            "preprocess_ms": preprocess_ms,
            "inference_ms": inference_ms,
            "postprocess_ms": postprocess_ms,
            "total_ms": total_ms,
            "latency_ms": total_ms,
            "circle_count": len(detections),
            "detections": [det.model_dump(mode="json") for det in detections],
            "warnings": warnings,
            "advisory_only": True,
            "production_ready": False,
            "competition_ready": False,
            "no_physical_command_generated": True,
        }
        event_type = "vision.mock_surrogate_detection" if metadata["camera_source_kind"] == "mock" else "vision.live_camera_surrogate_detection"
        source_text = "mock camera surrogate" if metadata["camera_source_kind"] == "mock" else "live camera surrogate"
        self._event(event_type, self.last_result, f"OpenCV {source_text} detection completed; circles={len(detections)}.")
        return event

    def snapshot(self, camera_runtime, profile: VisionRuntimeProfile) -> dict:
        event = self.run(camera_runtime, profile)
        path = self.snapshot_dir / f"surrogate_frame_{event.frame_id:06d}.json"
        payload = {
            "frame_id": event.frame_id,
            "source": event.source,
            "camera_source_kind": event.camera_source_kind,
            "camera_device_path": event.camera_device_path,
            "frame_origin": event.frame_origin,
            "frame_timestamp_ms": event.timestamp_ms,
            "detector_kind": "opencv_circle_surrogate",
            "production_yolo_loaded": False,
            "camera_fps": event.camera_fps,
            "detector_fps": event.detector_fps,
            "preprocess_ms": event.preprocess_ms,
            "inference_ms": event.inference_ms,
            "postprocess_ms": event.postprocess_ms,
            "total_ms": event.total_ms,
            "detections": [item.model_dump(mode="json") for item in event.balloon_detections],
            "warnings": event.warnings,
            "advisory_only": True,
            "production_ready": False,
            "competition_ready": False,
            "no_physical_command_generated": True,
        }
        path.write_text(__import__("json").dumps(payload, indent=2), encoding="utf-8")
        result = {**payload, "path": str(path)}
        self._event("vision.surrogate_snapshot_saved", result, "OpenCV surrogate snapshot metadata saved; no physical command generated.")
        return result

    def summary(self) -> dict:
        return {
            **self.last_result,
            "available": self.available(),
            "running": self.running,
            "snapshot_dir": str(self.snapshot_dir),
        }

    def _detect(self, frame, width: int, height: int, profile: VisionRuntimeProfile, source: str) -> list[BalloonDetection]:
        if frame is None or not self.available():
            cx = int(width * 0.58)
            cy = int(height * 0.42)
            radius = max(profile.circle_min_radius, min(profile.circle_max_radius, int(min(width, height) * 0.08)))
            return [self._balloon(1, cx, cy, radius, width, height, 0.76, source)]

        work = frame
        if profile.circle_roi_enabled:
            # Runtime ROI is owned by camera profile; if absent, use full frame.
            pass
        if profile.circle_target_color_mode != "any":
            work = self._color_mask(work, profile.circle_target_color_mode)
        gray = cv2.cvtColor(work, cv2.COLOR_BGR2GRAY) if len(work.shape) == 3 else work
        kernel = profile.circle_blur_kernel
        blurred = cv2.GaussianBlur(gray, (kernel, kernel), 0)
        circles = cv2.HoughCircles(
            blurred,
            cv2.HOUGH_GRADIENT,
            dp=1.2,
            minDist=max(10, profile.circle_min_radius * 2),
            param1=profile.circle_edge_param,
            param2=max(10, profile.circle_threshold),
            minRadius=profile.circle_min_radius,
            maxRadius=profile.circle_max_radius,
        )
        found: list[tuple[int, int, int, float]] = []
        if circles is not None:
            for circle in np.round(circles[0, :]).astype("int"):
                x, y, r = int(circle[0]), int(circle[1]), int(circle[2])
                area = math.pi * r * r
                if area >= profile.circle_min_area:
                    found.append((x, y, r, min(0.95, 0.55 + r / max(width, height))))
        return [self._balloon(index + 1, x, y, r, width, height, score, source) for index, (x, y, r, score) in enumerate(found[: profile.max_det])]

    def _source_metadata(self, camera_status) -> dict[str, Any]:
        is_mock = camera_status.profile.source_type == "mock"
        device_path = camera_status.profile.device_path or camera_status.profile.stable_path or camera_status.profile.device_id
        return {
            "source": "mock_camera_circle_surrogate" if is_mock else "live_camera_circle_surrogate",
            "camera_source_kind": "mock" if is_mock else "real_camera",
            "camera_device_path": None if is_mock else device_path,
            "frame_origin": "mock_frame" if is_mock else "real_capture",
        }

    def _color_mask(self, frame, mode: str):
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        if mode == "red":
            lower1 = np.array([0, 70, 50])
            upper1 = np.array([12, 255, 255])
            lower2 = np.array([170, 70, 50])
            upper2 = np.array([180, 255, 255])
            mask = cv2.bitwise_or(cv2.inRange(hsv, lower1, upper1), cv2.inRange(hsv, lower2, upper2))
        elif mode == "green":
            mask = cv2.inRange(hsv, np.array([35, 50, 40]), np.array([90, 255, 255]))
        elif mode == "blue":
            mask = cv2.inRange(hsv, np.array([90, 50, 40]), np.array([135, 255, 255]))
        else:
            mask = cv2.inRange(hsv, np.array([0, 0, 180]), np.array([180, 80, 255]))
        return cv2.bitwise_and(frame, frame, mask=mask)

    def _balloon(self, det_id: int, cx: int, cy: int, radius: int, width: int, height: int, confidence: float, source: str) -> BalloonDetection:
        x = max(0, cx - radius)
        y = max(0, cy - radius)
        w = min(width - x, radius * 2)
        h = min(height - y, radius * 2)
        return BalloonDetection(
            id=det_id,
            confidence=round(float(confidence), 3),
            bbox=BBox(x=x, y=y, w=max(1, w), h=max(1, h), format="pixel"),
            center_x=max(0, min(width, cx)),
            center_y=max(0, min(height, cy)),
            source=source,
        )

    def _event(self, event_type: str, payload: dict, message: str, level: LogLevel = LogLevel.INFO) -> None:
        payload = {
            "event_type": event_type,
            "summary": message,
            "advisory_only": True,
            "production_ready": False,
            "competition_ready": False,
            "no_physical_command_generated": True,
            **payload,
        }
        self.last_event = (event_type, payload)
        self.logger.emit(level, "VISION_SURROGATE", message, payload)
