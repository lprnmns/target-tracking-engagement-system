import math
import os
import re
import threading
import time
from collections import OrderedDict, deque
from contextlib import nullcontext
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from app.schemas.vision import AimPoint, BalloonDetection, BBox, BodyDetection, VisionConfigUpdate, VisionEvent, VisionStatus
from app.services.body_tracker_service import BodyTrackerService
from app.services.camera_service import CameraService
from app.services.cockpit_verdict_service import build_cockpit_verdicts
from app.services import stage3_roi
from app.services.storage_paths import resolve_project_path
from app.services.vision_service import VisionService

try:  # pragma: no cover - host dependent in release packages
    import cv2
    import numpy as np
except Exception:  # pragma: no cover
    cv2 = None
    np = None


EXTERNAL_FRAME_MAX_AGE_S = 0.5
YOLO_WORKER_STOP_TIMEOUT_S = 60.0
CUDA_RECOVERY_INTERVAL_S = 60.0
CUDA_MIN_FREE_MEMORY_BYTES = 512 * 1024 * 1024
CUDA_SAFE_IMGSZ = 640
ENGINE_HEADER_READ_BYTES = 2 * 1024 * 1024
ATTACHMENT_COAST_TTL_S = 0.600


@dataclass
class GhostRegion:
    track_id: int
    bbox: BBox
    velocity_x: float = 0.0
    velocity_y: float = 0.0
    last_seen_mono: float = 0.0
    last_balloon_centers: list[tuple[float, float]] = field(default_factory=list)


def worker_target_fps(profile: object) -> float:
    """Return the exact operator-selected detector ceiling."""
    val = float(getattr(profile, "target_fps", 60.0) or 60.0)
    return max(1.0, val)


def worker_idle_delay_s(target_fps: float, camera_fps: float | None, elapsed_s: float) -> float:
    """Throttle only when the requested detector ceiling is below the producer."""
    target = max(1.0, float(target_fps))
    producer = max(0.0, float(camera_fps or 0.0))
    if producer > 0 and target >= producer - 0.5:
        return 0.0
    return max(0.0, (1.0 / target) - max(0.0, float(elapsed_s)))


class VisionPipeline:
    def __init__(self, camera: CameraService, vision: VisionService) -> None:
        self.camera = camera
        self.vision = vision
        self.camera_runtime = None
        self.vision_runtime = None
        self.operation = None
        self.surrogate = None
        # Keep a small LRU so repeated setup/model trials cannot retain every
        # historical .pt file on the GPU. The active two models normally fit
        # in the first two slots; older trials are eligible for release.
        self._yolo_models: OrderedDict[str, object] = OrderedDict()
        # A cached YOLO wrapper is not proof that a TensorRT predictor was
        # initialized. Keep the result of the latest real engine call so
        # diagnostics cannot advertise CUDA for a failed import/inference.
        self._engine_runtime_success: dict[str, bool] = {}
        self._yolo_model_cache_max = 4
        self._model_load_lock = threading.Lock()
        self._model_executor: ThreadPoolExecutor | None = None
        self._model_executor_lock = threading.Lock()
        self.body_tracker = BodyTrackerService()
        self.balloon_tracker = None
        self._tracker_profile = "current_custom"
        self.color_classifier = None
        self.stage3_range = None
        self._attachment_memory: dict[int, GhostRegion] = {}
        self.ATTACHMENT_COAST_TTL_S: float = ATTACHMENT_COAST_TTL_S
        self._last_successful_body_mono = 0.0
        self._frame_id = 0
        self._last_yolo_event: VisionEvent | None = None
        # Exact source-frame join for read-only overlays.  The frame is the
        # immutable object consumed by YOLO, not whatever the camera happens
        # to expose when the browser asks for the next MJPEG part.
        self._aligned_overlay_event: VisionEvent | None = None
        self._aligned_overlay_frame = None
        self._aligned_overlay_camera_sequence: int | None = None
        self._aligned_overlay_captured_at_ms: int | None = None
        self._last_yolo_at = 0.0
        self._last_external_frame_at = 0.0
        self._event_lock = threading.Lock()
        self._frame_id_lock = threading.Lock()
        self._worker_thread: threading.Thread | None = None
        self._worker_stop = threading.Event()
        self._worker_fps_samples: deque[float] = deque(maxlen=30)
        self._worker_last_published_at: float | None = None
        self._worker_fps = 0.0
        self._worker_last_camera_sequence: int | None = None
        self._torch_runtime_configured = False
        # CUDA can remain discoverable after its context has failed (notably
        # on the field Windows RTX host). Keep an explicit pipeline health
        # state instead of trusting ``torch.cuda.is_available()`` forever.
        self._cuda_state_lock = threading.RLock()
        self._cuda_inference_lock = threading.Lock()
        self._cuda_fallback_active = False
        self._cuda_fallback_reason: str | None = None
        self._cuda_last_failure_at = 0.0
        self._cuda_last_recovery_attempt = 0.0
        self._cuda_last_recovery_success_at = 0.0
        self._cuda_warmup_done: set[tuple[str, int]] = set()
        self._model_specs_cache_key: tuple | None = None
        self._model_specs_cache_at = 0.0
        self._model_specs_cache: list[dict] = []
        self._last_body_inference_mono = 0.0
        self._cached_body_detections: list = []
        self._cached_body_warnings: list = []
        self._cached_body_latency: float = 0.0
        self.is_warmed_up = False
        self.warmup_info: dict = {}

    def configure_tracker(self, profile: str | None, *, enabled: bool = True) -> dict:
        """Hot-swap both perception tracker streams without restarting YOLO."""
        profile = "current_custom"
        enabled = True
        body_profile = self.body_tracker.configure(profile, enabled=enabled)
        balloon_profile = body_profile
        if self.balloon_tracker is not None:
            balloon_profile = self.balloon_tracker.configure(profile, enabled=enabled)
        self._tracker_profile = body_profile
        return {
            "accepted": True,
            "requested_profile": profile,
            "active_profile": body_profile,
            "body_profile": body_profile,
            "balloon_profile": balloon_profile,
            "state_reset": True,
            "no_physical_command_generated": True,
        }

    def tracker_status(self) -> dict:
        return {
            "active_profile": self._tracker_profile,
            "body_profile": self.body_tracker.profile,
            "balloon_profile": getattr(self.balloon_tracker, "profile", "current_custom"),
            "body_error": self.body_tracker.last_error,
            "balloon_error": getattr(self.balloon_tracker, "last_error", None),
        }

    def cuda_runtime_diagnostics(self) -> dict:
        """Return driver/device truth plus the pipeline fallback latch.

        ``torch.cuda.is_available()`` can remain true after a Windows CUDA
        context has failed, so the operator needs both values: driver
        discoverability and whether this pipeline is currently using CPU.
        This method is read-only and never initializes a model or hardware.
        """
        with self._cuda_state_lock_for_tests():
            fallback_active = bool(getattr(self, "_cuda_fallback_active", False))
            fallback_reason = getattr(self, "_cuda_fallback_reason", None)
            last_failure = float(getattr(self, "_cuda_last_failure_at", 0.0) or 0.0)
            last_recovery = float(getattr(self, "_cuda_last_recovery_attempt", 0.0) or 0.0)
            last_success = float(getattr(self, "_cuda_last_recovery_success_at", 0.0) or 0.0)
        payload = {
            "cuda_fallback_active": fallback_active,
            "cuda_fallback_reason": fallback_reason,
            "cuda_last_failure_at": last_failure or None,
            "cuda_last_recovery_attempt": last_recovery or None,
            "cuda_last_recovery_success_at": last_success or None,
            "cuda_device_name": None,
            "cuda_free_memory_mb": None,
            "cuda_total_memory_mb": None,
            "cuda_model_devices": [],
        }
        try:
            import torch

            if not torch.cuda.is_available():
                return payload
            payload["cuda_device_name"] = str(torch.cuda.get_device_name(0))
            free_bytes, total_bytes = torch.cuda.mem_get_info()
            payload["cuda_free_memory_mb"] = round(float(free_bytes) / (1024 * 1024), 1)
            payload["cuda_total_memory_mb"] = round(float(total_bytes) / (1024 * 1024), 1)
            for path, model in getattr(self, "_yolo_models", {}).items():
                if self._is_tensorrt_path(path):
                    success = getattr(self, "_engine_runtime_success", {}).get(path)
                    predictor_state = self._tensorrt_predictor_state(model)
                    if success is True and predictor_state == "ready":
                        label = "tensorrt_cuda_predictor_ready"
                    elif success is False:
                        label = "tensorrt_inference_failed"
                    else:
                        label = f"tensorrt_{predictor_state}"
                    payload["cuda_model_devices"].append(f"{Path(path).name}:{label}")
                else:
                    payload["cuda_model_devices"].append(
                        f"{Path(path).name}:{self._model_parameter_device(model)}"
                    )
        except Exception as exc:
            if not fallback_reason:
                payload["cuda_fallback_reason"] = f"cuda_diagnostics_failed:{exc}"
        return payload

    def start(self) -> VisionStatus:
        self.camera.start()
        self.vision.running = True
        if self._is_yolo_runtime():
            self._start_yolo_worker()
        self.latest()
        return self.vision.status()

    def preflight_warmup(self, n_camera_prime_frames: int = 10, n_dummy_infer: int = 20) -> dict:
        """Runs synchronous preflight warmup to prime camera buffers and TensorRT execution contexts.

        Executed at application startup before live visual servoing starts.
        """
        results: dict = {
            "camera_primed_frames": 0,
            "models_warmed": [],
            "warmup_duration_ms": 0.0,
            "ready": False,
        }
        start_t = time.perf_counter()

        # 1. Camera priming: grab initial frames from camera runtime to settle DirectShow / UVC
        if self.camera_runtime is not None:
            try:
                self.camera.start()
                primed = 0
                for _ in range(n_camera_prime_frames):
                    sample, _, _, _ = self.camera_runtime.live_preview_sample()
                    if sample is not None:
                        primed += 1
                    time.sleep(0.02)
                results["camera_primed_frames"] = primed
            except Exception as exc:
                results["camera_prime_error"] = str(exc)

        # 2. TensorRT / YOLO model warmup with dummy frames
        try:
            model_specs = self._active_yolo_model_specs()
            if model_specs and np is not None:
                dummy_frame = np.zeros((720, 1280, 3), dtype=np.uint8)
                profile = getattr(self.vision_runtime, "profile", None)
                conf = float(getattr(profile, "conf", 0.25) or 0.25)
                iou = float(getattr(profile, "iou", 0.45) or 0.45)
                max_det = int(getattr(profile, "max_det", 20) or 20)

                for spec in model_specs:
                    m_path = spec.get("path")
                    if not m_path or not os.path.exists(m_path):
                        continue
                    m_kwargs = {
                        "conf": conf,
                        "iou": iou,
                        "max_det": max_det,
                        "verbose": False,
                    }
                    if spec.get("input_size"):
                        m_kwargs["imgsz"] = int(spec["input_size"])
                    is_eng = self._is_tensorrt_path(m_path)
                    if not is_eng:
                        m_kwargs["device"] = "cuda"

                    # Run dummy inferences to pre-compile CUDA tactics and allocate buffers
                    for _ in range(n_dummy_infer):
                        self._run_model_inference(spec, dummy_frame, 1280, 720, m_kwargs)
                    results["models_warmed"].append(os.path.basename(m_path))
                results["ready"] = True
        except Exception as exc:
            results["model_warmup_error"] = str(exc)

        results["warmup_duration_ms"] = round((time.perf_counter() - start_t) * 1000, 2)
        self.is_warmed_up = True
        self.warmup_info = results
        return results

    def stop(self) -> VisionStatus:
        self.vision.running = False
        self._stop_yolo_worker()
        self.vision.latest_event = None
        with self._event_lock:
            self._last_yolo_event = None
            self._clear_aligned_overlay_locked()
        if self.surrogate is not None:
            self.surrogate.stop()
        self.camera.stop()
        return self.vision.status()

    def configure(self, config: VisionConfigUpdate) -> VisionStatus:
        return self.vision.configure(config)

    def latest(self) -> VisionEvent:
        # Status polling must never be an implicit camera/YOLO start command.
        # Only the visible Setup action (/api/vision/start) authorizes a run.
        if not self.vision.running:
            return VisionEvent(
                frame_id=0, timestamp_ms=int(time.time() * 1000), source="vision_standby",
                frame_width=0, frame_height=0, fps=0.0, preprocess_ms=0.0,
                inference_ms=0.0, postprocess_ms=0.0, total_latency_ms=0.0,
                body_detections=[], balloon_detections=[], aim_points=[],
                warnings=["VISION_NOT_STARTED"],
            )
        if self.vision_runtime is not None and self.surrogate is not None and self.camera_runtime is not None:
            if self.vision_runtime.profile.inference_adapter == "opencv_live_circle_surrogate":
                self._stop_yolo_worker()
                self.camera.start()
                self.vision.running = True
                event = self.surrogate.run(self.camera_runtime, self.vision_runtime.profile)
                self.vision.latest_event = event
                return event
            if self.vision_runtime.profile.inference_adapter == "ultralytics_yolo":
                self._start_yolo_worker()
                with self._event_lock:
                    latest_event = self._last_yolo_event
                if latest_event is not None and latest_event.frame_origin in {
                    "browser_upload",
                    "browser_frame_upload",
                } and time.monotonic() - self._last_external_frame_at <= EXTERNAL_FRAME_MAX_AGE_S:
                    self.vision.latest_event = latest_event
                    return latest_event
                if latest_event is not None and latest_event.frame_origin in {
                    "browser_upload",
                    "browser_frame_upload",
                }:
                    with self._event_lock:
                        if self._last_yolo_event is not None and self._last_yolo_event.frame_origin in {
                            "browser_upload",
                            "browser_frame_upload",
                        }:
                            self._last_yolo_event = None
                    latest_event = None
                if latest_event is not None:
                    self.vision.latest_event = latest_event
                    return latest_event
                return self._warming_event()
            self._stop_yolo_worker()
        if not self.camera.mock.running:
            self.camera.start()
        return self.vision.next_event(
            source=self.camera.camera_mode,
            width=self.camera.config.camera.stream_width,
            height=self.camera.config.camera.stream_height,
        )

    def _is_yolo_runtime(self) -> bool:
        return bool(
            self.vision_runtime is not None
            and self.camera_runtime is not None
            and self.vision_runtime.profile.inference_adapter == "ultralytics_yolo"
        )

    def _start_yolo_worker(self) -> None:
        """Start one owner for live YOLO inference.

        REST, WebSocket and tracking callers only read the latest published
        event.  They must never execute a model synchronously on the async
        application loop or start a second camera/model owner.
        """
        if not self._is_yolo_runtime():
            return
        if self._worker_thread is not None and self._worker_thread.is_alive():
            return
        self._worker_stop.clear()
        self._worker_thread = threading.Thread(
            target=self._yolo_worker_loop,
            name="vision-yolo-worker",
            daemon=True,
        )
        self._worker_thread.start()

    def _stop_yolo_worker(self) -> None:
        self._worker_stop.set()
        worker = self._worker_thread
        if worker is not None and worker.is_alive() and worker is not threading.current_thread():
            # Do not orphan an in-flight CUDA warm-up/inference thread. The
            # previous two-second timeout nulled this reference and the next
            # start cleared the shared stop event, allowing two GPU/camera
            # owners to publish into one runtime. First-load warm-up on the
            # Windows RTX host can legitimately take ~40 seconds.
            worker.join(timeout=YOLO_WORKER_STOP_TIMEOUT_S)
        if worker is not None and worker.is_alive():
            # Fail closed: retain ownership and the asserted stop event. A
            # later start sees the live owner and cannot create a duplicate.
            return
        self._worker_thread = None
        with self._event_lock:
            self._worker_fps_samples.clear()
            self._worker_last_published_at = None
            self._worker_fps = 0.0
            self._worker_last_camera_sequence = None
        with self._model_executor_lock:
            executor = self._model_executor
            self._model_executor = None
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)

    def _yolo_worker_loop(self) -> None:
        if os.name == "nt":
            try:
                import ctypes
                ctypes.windll.winmm.timeBeginPeriod(1)
            except Exception:
                pass
        try:
            while not self._worker_stop.is_set():
                if not self.vision.running or not self._is_yolo_runtime():
                    self._worker_stop.wait(0.05)
                    continue
                self._maybe_recover_cuda()
                # A browser development frame is an explicit source of truth while
                # it is fresh.  Do not race it with the backend camera worker.
                if time.monotonic() - self._last_external_frame_at <= EXTERNAL_FRAME_MAX_AGE_S:
                    self._worker_stop.wait(0.02)
                    continue
                # Never run YOLO twice on the same captured image.  The camera is
                # the producer clock; the detector should consume each published
                # frame once and drop backlog rather than re-infer a stale buffer.
                camera_sequence = int(getattr(self.camera_runtime, "frame_sequence", 0) or 0)
                if camera_sequence > 0 and camera_sequence == self._worker_last_camera_sequence:
                    time.sleep(0.001)
                    continue
                started = time.perf_counter()
                event = None
                try:
                    event, source_frame, source_sequence, source_captured_at_ms = self._latest_ultralytics_event()
                    if source_sequence is not None and source_sequence > 0:
                        self._worker_last_camera_sequence = source_sequence
                    elif camera_sequence > 0:
                        self._worker_last_camera_sequence = camera_sequence
                    self._publish_worker_event(
                        event,
                        overlay_frame=source_frame,
                        overlay_camera_sequence=source_sequence,
                        overlay_captured_at_ms=source_captured_at_ms,
                    )
                except Exception as exc:  # pragma: no cover - host/model dependent
                    event = self._worker_error_event(exc)
                    self._publish_worker_event(event)
                # ``target_fps`` is an operator-facing hard ceiling.  Duplicate
                # camera sequences are already rejected above, so no hidden
                # 30 -> 45 remapping is needed (or honest) here.
                target_fps = worker_target_fps(self.vision_runtime.profile)
                elapsed = time.perf_counter() - started
                cam_fps = getattr(event, "camera_fps", None) if event is not None else None
                idle_delay = worker_idle_delay_s(target_fps, cam_fps, elapsed)
                if idle_delay > 0.001:
                    self._worker_stop.wait(idle_delay)
        finally:
            if os.name == "nt":
                try:
                    import ctypes
                    ctypes.windll.winmm.timeEndPeriod(1)
                except Exception:
                    pass

    def _publish_worker_event(
        self,
        event: VisionEvent,
        *,
        overlay_frame=None,
        overlay_camera_sequence: int | None = None,
        overlay_captured_at_ms: int | None = None,
    ) -> None:
        now = time.monotonic()
        with self._event_lock:
            if self._worker_last_published_at is not None:
                delta = now - self._worker_last_published_at
                # Model/profile changes can pause publication without
                # replacing the worker. That one gap is not detector FPS.
                if 0 < delta <= 0.5:
                    self._worker_fps_samples.append(delta)
                elif delta > 0.5:
                    self._worker_fps_samples.clear()
            self._worker_last_published_at = now
            engine_failure = any(
                str(item).startswith("tensorrt_engine_inference_failed:")
                for item in event.warnings
            )
            if engine_failure:
                # A fast failed call is not detector throughput. Keep the
                # camera producer FPS visible, but make detector FPS and the
                # worker aggregate explicitly unavailable.
                self._worker_fps_samples.clear()
                self._worker_fps = 0.0
                event = event.model_copy(
                    update={
                        "fps": 0.0,
                        "detector_fps": 0.0,
                        "model_timings_ms": {},
                        "model_execution": "unavailable",
                        "model_count": 0,
                    }
                )
            elif self._worker_fps_samples:
                self._worker_fps = round(1.0 / (sum(self._worker_fps_samples) / len(self._worker_fps_samples)), 2)
            else:
                self._worker_fps = round(float(event.detector_fps or event.fps or 0.0), 2)
            if not engine_failure:
                measured_fps = self._worker_fps or event.detector_fps or event.fps
                event = event.model_copy(update={"fps": measured_fps, "detector_fps": measured_fps})
            if self._cuda_fallback_is_active():
                fallback_warning = "CUDA_FALLBACK_ACTIVE"
                if fallback_warning not in event.warnings:
                    event = event.model_copy(update={"warnings": [*event.warnings, fallback_warning]})
            self._last_yolo_event = event
            if (
                overlay_frame is not None
                and overlay_camera_sequence is not None
                and event.camera_frame_sequence == overlay_camera_sequence
            ):
                self._aligned_overlay_event = event
                self._aligned_overlay_frame = overlay_frame
                self._aligned_overlay_camera_sequence = overlay_camera_sequence
                self._aligned_overlay_captured_at_ms = overlay_captured_at_ms
            else:
                self._clear_aligned_overlay_locked()
            self._last_yolo_at = time.perf_counter()
            self.vision.latest_event = event

    def latest_aligned_overlay_sample(self):
        """Return the YOLO event and the exact immutable frame it analysed."""
        with self._event_lock:
            event = self._aligned_overlay_event
            frame = self._aligned_overlay_frame
            sequence = self._aligned_overlay_camera_sequence
            captured_at_ms = self._aligned_overlay_captured_at_ms
            if event is None or frame is None or sequence is None:
                return None
            if event.camera_frame_sequence != sequence:
                return None
            return event, frame, sequence, captured_at_ms

    def _clear_aligned_overlay_locked(self) -> None:
        self._aligned_overlay_event = None
        self._aligned_overlay_frame = None
        self._aligned_overlay_camera_sequence = None
        self._aligned_overlay_captured_at_ms = None

    def _warming_event(self) -> VisionEvent:
        status = self.camera_runtime.status() if self.camera_runtime is not None else None
        return VisionEvent(
            frame_id=0,
            timestamp_ms=int(time.time() * 1000),
            source="live_camera_ultralytics_yolo",
            frame_width=int(status.actual_width if status else 0),
            frame_height=int(status.actual_height if status else 0),
            fps=0.0,
            camera_fps=float(status.actual_fps if status else 0.0) or None,
            detector_fps=0.0,
            preprocess_ms=0.0,
            inference_ms=0.0,
            postprocess_ms=0.0,
            total_latency_ms=0.0,
            total_ms=0.0,
            camera_source_kind="real_camera" if status and status.profile.source_type != "mock" else "mock",
            frame_origin="real_capture" if status and status.profile.source_type != "mock" else "mock_frame",
            detector_kind="ultralytics_yolo",
            body_detections=[],
            balloon_detections=[],
            warnings=["VISION_WARMING_UP", "no_physical_command_generated=true"],
        )

    def _worker_error_event(self, exc: Exception) -> VisionEvent:
        event = self._warming_event()
        return event.model_copy(update={"warnings": [*event.warnings, f"ultralytics_worker_failed:{exc}"]})

    def status(self) -> VisionStatus:
        return self.vision.status()

    def _latest_ultralytics_event(self) -> tuple[VisionEvent, object | None, int | None, int | None]:
        started = time.perf_counter()
        frame_id = self._next_frame_id()
        profile = self.vision_runtime.profile
        capture_started = time.perf_counter()
        camera_status = self.camera_runtime.status()
        # Inference shares the profile-owned capture worker with preview and
        # evidence. On Windows, opening DirectShow here a second time bypasses
        # the hot-unplug isolation process and can either starve the preview or
        # crash the backend in the vendor UVC driver.
        frame, frame_warnings, camera_sequence, captured_at = self.camera_runtime.live_preview_sample()
        captured_at_ms = int(float(captured_at) * 1000) if captured_at is not None else None
        capture_ms = round((time.perf_counter() - capture_started) * 1000, 3)
        width = int(camera_status.actual_width or camera_status.requested_width or 640)
        height = int(camera_status.actual_height or camera_status.requested_height or 360)
        if frame is not None:
            height, width = int(frame.shape[0]), int(frame.shape[1])
        warnings = [
            "ULTRALYTICS YOLO ACTIVE",
            "ADVISORY ONLY",
            "no_physical_command_generated=true",
            *frame_warnings,
        ]
        event = self._ultralytics_event_from_frame(
            frame=frame,
            width=width,
            height=height,
            started=started,
            profile=profile,
            warnings=warnings,
            source="live_camera_ultralytics_yolo",
            camera_source_kind="real_camera" if camera_status.profile.source_type != "mock" else "mock",
            camera_device_path=camera_status.profile.device_path or camera_status.profile.stable_path or camera_status.profile.device_id,
            frame_origin="real_capture" if camera_status.profile.source_type != "mock" else "mock_frame",
            camera_fps=float(camera_status.actual_fps_measured or camera_status.actual_fps or camera_status.requested_fps),
            frame_id=frame_id,
            capture_ms=capture_ms,
            camera_frame_sequence=camera_sequence,
            camera_capture_timestamp_ms=captured_at_ms,
            camera_frame_age_ms=(
                round(max(0.0, (time.time() - float(captured_at)) * 1000), 3)
                if captured_at is not None else None
            ),
        )
        return event, frame, camera_sequence, captured_at_ms

    def latest_from_external_frame(
        self,
        frame,
        source: str,
        camera_source_kind: str,
        frame_origin: str,
        camera_device_path: str | None = None,
    ) -> VisionEvent:
        if self.vision_runtime is None:
            raise RuntimeError("vision_runtime_unavailable")
        started = time.perf_counter()
        frame_id = self._next_frame_id()
        self.vision.running = True
        height, width = int(frame.shape[0]), int(frame.shape[1])
        event = self._ultralytics_event_from_frame(
            frame=frame,
            width=width,
            height=height,
            started=started,
            profile=self.vision_runtime.profile,
            warnings=[
                "ULTRALYTICS YOLO ACTIVE",
                "BROWSER CAMERA FRAME UPLOAD",
                "ADVISORY ONLY",
                "no_physical_command_generated=true",
            ],
            source=source,
            camera_source_kind=camera_source_kind,
            camera_device_path=camera_device_path,
            frame_origin=frame_origin,
            camera_fps=None,
            frame_id=frame_id,
            capture_ms=0.0,
            camera_frame_sequence=None,
            camera_capture_timestamp_ms=int(time.time() * 1000),
            camera_frame_age_ms=0.0,
        )
        self._last_external_frame_at = time.monotonic()
        with self._event_lock:
            self._last_yolo_event = event
            self._last_yolo_at = time.perf_counter()
            self.vision.latest_event = event
        return event

    def _next_frame_id(self) -> int:
        with self._frame_id_lock:
            self._frame_id += 1
            return self._frame_id

    def _ultralytics_event_from_frame(
        self,
        frame,
        width: int,
        height: int,
        started: float,
        profile,
        warnings: list[str],
        source: str,
        camera_source_kind: str,
        camera_device_path: str | None,
        frame_origin: str,
        camera_fps: float | None,
        frame_id: int,
        capture_ms: float = 0.0,
        camera_frame_sequence: int | None = None,
        camera_capture_timestamp_ms: int | None = None,
        camera_frame_age_ms: float | None = None,
    ) -> VisionEvent:
        active_policy = self._active_target_policy()
        competition_stage = None
        if getattr(self, "operation", None) is not None and hasattr(self.operation, "state"):
            op_st = self.operation.state()
            c_stg = getattr(op_st, "competition_stage", None)
            competition_stage = c_stg.value if hasattr(c_stg, "value") else str(c_stg or "")
        body_detections = []
        balloon_detections: list[BalloonDetection] = []
        inference_ms = 0.0
        preprocess_ms = 0.0
        postprocess_ms = 0.0
        model_timings_ms: dict[str, float] = {}
        model_count = 0
        model_execution = "none"
        # The capture worker can legitimately publish its first status tick
        # before a camera frame (or before saved model paths are resolved).
        # Keep the downstream tracker input defined on those cold-start
        # branches; otherwise the first no-frame event raises UnboundLocalError
        # after the event itself has already been built and kills the worker.
        frame_for_inference = frame
        model_specs = self._active_yolo_model_specs()
        engine_requested = any(self._is_tensorrt_path(spec["path"]) for spec in model_specs)
        if frame is None:
            warnings.append("real_camera_frame_unavailable")
        elif not model_specs:
            warnings.append("active_yolo_model_missing")
        else:
            try:
                preprocess_started = time.perf_counter()
                frame_for_inference, preprocess_warnings = self._preprocess_frame_for_ultralytics(frame)
                warnings.extend(preprocess_warnings)
                preprocess_ms = round((time.perf_counter() - preprocess_started) * 1000, 3)
                resolved_device, device_reason = self.vision_runtime.resolve_device(profile)
                if resolved_device is None:
                    warnings.append(f"inference_device_unavailable:{device_reason}")
                    raise RuntimeError(device_reason)
                if engine_requested and resolved_device != "cuda":
                    warnings.append("tensorrt_engine_requires_cuda")
                    raise RuntimeError("tensorrt_engine_requires_cuda")
                if engine_requested and self._cuda_fallback_is_active():
                    warnings.append("tensorrt_engine_cuda_unhealthy")
                    raise RuntimeError("tensorrt_engine_cuda_unhealthy")
                if resolved_device == "cuda" and self._cuda_fallback_is_active() and not engine_requested:
                    resolved_device = "cpu"
                    warnings.append("CUDA_FALLBACK_TO_CPU")
                infer_started = time.perf_counter()
                effective_imgsz = int(profile.imgsz)
                if resolved_device == "cuda" and effective_imgsz > CUDA_SAFE_IMGSZ and not engine_requested:
                    warnings.append(f"cuda_imgsz_capped:{effective_imgsz}->{CUDA_SAFE_IMGSZ}")
                    effective_imgsz = CUDA_SAFE_IMGSZ
                infer_kwargs = {
                    "imgsz": effective_imgsz,
                    "conf": profile.conf,
                    "iou": profile.iou,
                    "max_det": profile.max_det,
                    "device": resolved_device,
                    "verbose": False,
                }
                if profile.half and resolved_device == "cuda":
                    # FP16 is intentionally disabled in the field profile.
                    # A failed FP16 kernel can leave Windows' CUDA context
                    # discoverable but unusable, causing an endless empty
                    # detection loop.
                    warnings.append("half_disabled_for_cuda_stability")
                elif profile.half:
                    warnings.append("half_disabled_without_cuda")
                if profile.classes is not None:
                    infer_kwargs["classes"] = profile.classes
                unique_specs: list[dict] = []
                completed_paths: set[str] = set()
                for spec in model_specs:
                    # One combined detector can populate both semantic types;
                    # do not execute the same heavyweight model twice.
                    if spec["path"] in completed_paths:
                        continue
                    completed_paths.add(spec["path"])
                    unique_specs.append(spec)

                model_count = len(unique_specs)
                active_policy = self._active_target_policy()
                parallel_requested = bool(getattr(profile, "parallel_models", True))
                parallel_enabled = (
                    parallel_requested
                    and resolved_device == "cuda"
                    and len(unique_specs) > 1
                    and not engine_requested
                )
                model_execution = "parallel" if parallel_enabled else "sequential"
                now_mono = time.monotonic()
                competition_stage = None
                if self.operation is not None and hasattr(self.operation, "state"):
                    op_st = self.operation.state()
                    c_stg = getattr(op_st, "competition_stage", None)
                    competition_stage = c_stg.value if hasattr(c_stg, "value") else str(c_stg or "")

                skip_body_inference = False
                body_skip_reason = ""
                self._body_cadence_counter = getattr(self, "_body_cadence_counter", 0) + 1
                time_since_body = (now_mono - self._last_body_inference_mono) if self._last_body_inference_mono > 0.0 else 999.0
                y17_gosterim = active_policy == "BALLOON" and str(competition_stage or "").upper() in {"STAGE_3", "STAGE3"}
                if y17_gosterim:
                    # Y17: Asama 3 + hedef BALON -> govde modeli SADECE GOSTERIM icin 10 karede 1 calisir.
                    if self._last_body_inference_mono > 0.0 and self._body_cadence_counter % 10 != 0:
                        skip_body_inference = True
                        body_skip_reason = "y17_gosterim_10kare"
                elif active_policy in {"BALLOON_AIRCRAFT", "STAGE_3"} or competition_stage in {"STAGE_3"}:
                    # Aşama 3 (İHA + Balon): Balon modeli her karede (~14ms) kesintisiz çalışarak
                    # 45-55+ FPS sağlar. Gövde modeli ise 3 karede 1 (veya <65ms ise) çalıştırılır.
                    # Frame latency 65ms'yi aşsa bile deadlock oluşmaz, ara karelerde gövde atlanır.
                    if self._last_body_inference_mono > 0.0 and (time_since_body < 0.065 or (self._body_cadence_counter % 3 != 0 and time_since_body < 0.120)):
                        skip_body_inference = True
                        body_skip_reason = "stage3_body_cadence_15hz"
                elif competition_stage in {"STAGE_2", "STAGE2", "stage2"}:
                    # Aşama 2: Balonlar ana hedef. Gövde modeli ~15 Hz kadansla çalıştırılarak FPS korunur.
                    if self._last_body_inference_mono > 0.0 and (time_since_body < 0.065 or (self._body_cadence_counter % 3 != 0 and time_since_body < 0.120)):
                        skip_body_inference = True
                        body_skip_reason = "stage2_body_cadence_15hz"
                elif active_policy in {"STAGE1_INDEPENDENT", "STAGE_1"} or competition_stage in {"STAGE_1"}:
                    # Aşama 1: Operatör manuel modu, body ~8 Hz (>= 120 ms)
                    if (now_mono - self._last_body_inference_mono) < 0.120 and self._last_body_inference_mono > 0.0:
                        skip_body_inference = True
                        body_skip_reason = "stage1_body_throttled_8hz"
                elif active_policy == "BALLOON":
                    # Salt balon politikası (Aşama 2 dışı): Yalnızca balon modeli çalışır.
                    skip_body_inference = True
                    body_skip_reason = "balloon_policy_no_aircraft"

                jobs: list[tuple[dict, dict]] = []
                for spec in unique_specs:
                    if spec["role"] == "body" and skip_body_inference:
                        continue
                    # Setup's per-model threshold is the source of truth for
                    # live inference. The generic runtime `profile.conf`
                    # must not silently override the visible model slider.
                    model_kwargs = dict(infer_kwargs)
                    if spec["role"] == "balloon":
                        model_kwargs["conf"] = float(getattr(profile, "balloon_conf_threshold", self.vision.balloon_conf_threshold))
                    elif spec["role"] == "body":
                        model_kwargs["conf"] = float(getattr(profile, "body_conf_threshold", self.vision.body_conf_threshold))
                    else:
                        model_kwargs["conf"] = min(
                            float(getattr(profile, "body_conf_threshold", self.vision.body_conf_threshold)),
                            float(getattr(profile, "balloon_conf_threshold", self.vision.balloon_conf_threshold)),
                        )
                    if self._is_tensorrt_path(spec["path"]):
                        # TensorRT engines are fixed-shape CUDA backends. The
                        # light reference lets the engine own device setup;
                        # passing PyTorch-only placement/precision flags or
                        # the global 640 shape defeats that contract.
                        if spec.get("input_size"):
                            model_kwargs["imgsz"] = int(spec["input_size"])
                        model_kwargs.pop("device", None)
                        model_kwargs.pop("half", None)
                    jobs.append((spec, model_kwargs))

                # Kabul sözleşmesi: model_count fiilen bu karede yürütülen
                # modelleri sayar. Politika body'yi atladığında (ör. Aşama 2
                # balon görevi) sayaç 1'e iner — atlanan model yüklü olsa
                # bile bu karede maliyeti yoktur.
                if jobs:
                    model_count = len(jobs)

                if parallel_enabled:
                    executor = self._get_model_executor()
                    futures = [
                        executor.submit(self._run_model_inference, spec, frame_for_inference, width, height, kwargs)
                        for spec, kwargs in jobs
                    ]
                    results_by_spec = [future.result() for future in futures]
                else:
                    results_by_spec = [
                        self._run_model_inference(spec, frame_for_inference, width, height, kwargs)
                        for spec, kwargs in jobs
                    ]

                postprocess_started = time.perf_counter()
                ran_body_inference = False
                for result in results_by_spec:
                    bodies, balloons, model_warnings, model_latency, role = result
                    if role == "body":
                        ran_body_inference = True
                        self._last_body_inference_mono = now_mono
                        if bodies:
                            self._cached_body_detections = list(bodies)
                            self._cached_body_warnings = list(model_warnings)
                            self._cached_body_latency = model_latency
                            self._last_successful_body_mono = now_mono
                        else:
                            # 1-frame detector miss: preserve cached detections within TTL
                            if (now_mono - getattr(self, "_last_successful_body_mono", 0.0)) > self.ATTACHMENT_COAST_TTL_S:
                                self._cached_body_detections = []
                                self._cached_body_warnings = list(model_warnings)
                                self._cached_body_latency = model_latency
                            else:
                                bodies = list(self._cached_body_detections)
                                model_warnings.append("body_cadence:miss_coasting_reused")
                    body_detections.extend(bodies)
                    balloon_detections.extend(balloons)
                    warnings.extend(model_warnings)
                    model_timings_ms[role] = model_latency

                if skip_body_inference and not ran_body_inference:
                    if body_skip_reason in {"balloon_policy_no_aircraft", "stage2_pure_balloon_mode"}:
                        # İHA görevde yok veya Aşama 2: eski body kutuları da gösterilmez,
                        # önbellek temizlenir (kokpitte hayalet İHA kalmaz).
                        self._cached_body_detections = []
                        self._cached_body_warnings = []
                        warnings.append(f"body_inference_skipped:{body_skip_reason}")
                    else:
                        body_detections.extend(self._cached_body_detections)
                        warnings.extend(self._cached_body_warnings)
                        model_timings_ms["body"] = self._cached_body_latency
                        warnings.append(f"body_inference_cadence:{body_skip_reason}")
                inference_ms = round((time.perf_counter() - infer_started) * 1000, 3)
                if resolved_device == "cuda" and self._cuda_fallback_is_active():
                    resolved_device = "cpu"
                    warnings.append("CUDA_FALLBACK_TO_CPU")
                warnings.append(f"inference_device:{resolved_device}")

                # Update body tracker BEFORE attachment ROI filtering so tracks are established
                tracker_timestamp_ms = camera_capture_timestamp_ms or int(time.time() * 1000)
                body_detections = self.body_tracker.update(
                    body_detections,
                    frame_id=frame_id,
                    capture_timestamp_ns=int(tracker_timestamp_ms) * 1_000_000,
                    image=frame_for_inference,
                )
                warnings.append(f"tracker_profile:{self._tracker_profile}")
                if self.body_tracker.last_error:
                    warnings.append(f"body_tracker_failed:{self.body_tracker.last_error}")

                is_stage2 = competition_stage in {"STAGE_2", "STAGE2", "stage2"}
                if is_stage2:
                    # Aşama 2: Hem hava aracı hem de balonlar serbest ve bağımsız!
                    # Balonlar gövdelere bağlanmaz, hiçbir ROI filtrelemesi yapılmaz.
                    warnings.append(f"stage2_independent:bodies={len(body_detections)},balloons={len(balloon_detections)}")
                elif y17_gosterim:
                    # Y17: govdeler yalniz ekranda; IFF / ROI / sahiplik YOK, tum balonlar serbest.
                    body_detections = [b.model_copy(update={"target_team": "unknown"}) for b in body_detections]
                    warnings.append(f"y17_gosterim:bodies={len(body_detections)},balloons={len(balloon_detections)}")
                elif active_policy == "BALLOON":
                    body_detections = []
                elif active_policy == "AIRCRAFT":
                    balloon_detections = []
                elif active_policy == "BALLOON_AIRCRAFT":
                    before_roi_filter = len(balloon_detections)
                    balloon_detections = self._filter_balloons_by_attachment_regions(
                        balloon_detections, body_detections
                    )
                    # The filter purges expired ghosts first, so checking
                    # after the call reports real live/coasting evidence.
                    if not body_detections and not self._attachment_memory:
                        warnings.append("stage3_body_gate:no_body_no_balloon")
                    warnings.append(
                        f"balloon_attachment_roi_kept:{len(balloon_detections)}/{before_roi_filter}"
                    )
                elif active_policy in {"STAGE1_INDEPENDENT", "STAGE_1"}:
                    # Aşama 1: Hem hava aracı hem de balonlar serbest ve bağımsız!
                    # Hiçbir ROI filtrelemesi yapılmaz, her iki modelin tespitleri de olduğu gibi korunur.
                    warnings.append(f"stage1_independent:bodies={len(body_detections)},balloons={len(balloon_detections)}")
                warnings.append(f"inference_target_policy:{active_policy.lower()}")
                if self.color_classifier is not None and body_detections and ran_body_inference and not y17_gosterim:
                    body_detections = self.color_classifier.classify_frame_bodies(
                        frame_for_inference,
                        self._frame_id,
                        body_detections,
                        balloon_detections,
                    )
                if self.stage3_range is not None and body_detections and ran_body_inference and not y17_gosterim:
                    body_spec = next((item for item in model_specs if item["role"] in {"body", "combined"}), None)
                    body_detections = self.stage3_range.attach_estimates(
                        body_detections,
                        body_spec["model_id"] if body_spec else None,
                        body_spec["path"] if body_spec else None,
                    )
                if is_stage2 and body_detections:
                    # Aşama 2 Kuralı: Alanda dost unsur yoktur. Model ne söylerse söylesin
                    # tüm gövdeler kesinlikle düşmandır, kilitlenme ve atış garantidir.
                    body_detections = [
                        b.model_copy(update={"target_team": "enemy"}) for b in body_detections
                    ]
                if ran_body_inference and body_detections:
                    self._cached_body_detections = list(body_detections)
                if not balloon_detections:
                    # The former red-color fallback generated fixed 0.82
                    # pseudo-confidence boxes. It is useful only as an
                    # explicit test adapter, never alongside a selected YOLO
                    # model: it makes the visible confidence slider untrue.
                    warnings.append("ultralytics_yolo_empty")
                postprocess_ms = round((time.perf_counter() - postprocess_started) * 1000, 3)
            except Exception as exc:  # pragma: no cover - depends on local model/runtime
                failure_code = "tensorrt_engine_inference_failed" if engine_requested else "ultralytics_inference_failed"
                warnings.append(f"{failure_code}:{exc}")
        if preprocess_ms <= 0 and frame is not None:
            preprocess_ms = round((time.perf_counter() - started) * 1000, 3) if inference_ms <= 0 else 0.0
        if inference_ms <= 0:
            inference_ms = round((time.perf_counter() - started) * 1000, 3)
        # ``total_ms`` is wall-clock from capture request through parsing and
        # tracking.  Older code substituted fixed 1.0/0.8 ms values, which
        # made the operator HUD and benchmark under-report real latency.
        total_ms = round((time.perf_counter() - started) * 1000, 3)
        accounted_ms = capture_ms + preprocess_ms + inference_ms + postprocess_ms
        pipeline_overhead_ms = round(max(0.0, total_ms - accounted_ms), 3)
        engine_inference_failed = any(
            str(item).startswith("tensorrt_engine_inference_failed:")
            for item in warnings
        )
        if engine_inference_failed:
            # A missing TensorRT import can fail in a few milliseconds. That
            # failed-call interval is not detector throughput and must not be
            # shown as a healthy 100+ FPS loop. Keep the camera producer FPS
            # above, but make detector/model metrics explicitly unavailable.
            detector_fps = 0.0
            inference_ms = 0.0
            model_count = 0
            model_execution = "unavailable"
            model_timings_ms = {}
            warnings.append("tensorrt_inference_unavailable")
        else:
            detector_fps = round(1000.0 / max(total_ms, 1.0), 2)
        target_verdicts = build_cockpit_verdicts(
            bodies=body_detections,
            balloons=balloon_detections,
            associations=getattr(self.body_tracker, "_associations", None) if hasattr(self, "body_tracker") else None,
            target_policy=active_policy,
            active_stage="stage3" if active_policy in {"BALLOON_AIRCRAFT", "STAGE_3"} or str(competition_stage or "").upper() in {"STAGE_3", "STAGE3"} else ("stage2" if str(competition_stage or "").upper() in {"STAGE_2", "STAGE2", "stage2"} else "stage1"),
            range_rules=getattr(getattr(getattr(self, "config", None), "decision", None), "range_rules", None),
        )
        if active_policy == "BALLOON" and str(competition_stage or "").upper() in {"STAGE_3", "STAGE3"} and target_verdicts:
            # Y17: govde etiketi notr; dost/dusman/ates karari gosterilmez.
            try:
                target_verdicts = [
                    vd.model_copy(update={"label_tr": f"HAVA ARACI: {vd.target_class}", "fire_authorized": False})
                    if vd.kind == "body" else vd
                    for vd in target_verdicts
                ]
            except Exception:
                pass
        event = VisionEvent(
            frame_id=frame_id,
            timestamp_ms=int(time.time() * 1000),
            source=source,
            frame_width=width,
            frame_height=height,
            fps=detector_fps,
            camera_fps=camera_fps,
            detector_fps=detector_fps,
            preprocess_ms=preprocess_ms,
            inference_ms=inference_ms,
            postprocess_ms=postprocess_ms,
            total_latency_ms=total_ms,
            total_ms=total_ms,
            camera_source_kind=camera_source_kind,
            camera_device_path=camera_device_path,
            frame_origin=frame_origin,
            detector_kind="ultralytics_yolo",
            body_detections=body_detections,
            balloon_detections=balloon_detections,
            aim_points=[AimPoint(id=det.id, x=det.center_x, y=det.center_y, source=det.source) for det in balloon_detections],
            model_count=model_count,
            model_execution=model_execution,
            model_timings_ms=model_timings_ms,
            capture_ms=capture_ms,
            pipeline_overhead_ms=pipeline_overhead_ms,
            camera_frame_sequence=camera_frame_sequence,
            camera_capture_timestamp_ms=camera_capture_timestamp_ms,
            camera_frame_age_ms=camera_frame_age_ms,
            target_verdicts=target_verdicts,
            warnings=warnings,
        )
        if self.balloon_tracker is not None:
            self.balloon_tracker.update(event, image=frame_for_inference)
            if self.balloon_tracker.last_error:
                event = event.model_copy(
                    update={"warnings": [*event.warnings, f"balloon_tracker_failed:{self.balloon_tracker.last_error}"]}
                )
        return event

    def _get_model_executor(self) -> ThreadPoolExecutor:
        with self._model_executor_lock:
            if self._model_executor is None:
                self._model_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="vision-model")
            return self._model_executor

    def _run_model_inference(
        self,
        spec: dict,
        frame,
        width: int,
        height: int,
        model_kwargs: dict,
    ) -> tuple[list, list[BalloonDetection], list[str], float, str]:
        started = time.perf_counter()
        model_warnings: list[str] = []
        requested_kwargs = dict(model_kwargs)
        is_engine = self._is_tensorrt_path(spec["path"])
        try:
            model = self._load_yolo_model(spec["path"])
            model_warnings.extend(self._ensure_model_warmup(model, spec["path"], requested_kwargs, is_engine=is_engine))
            effective_kwargs = dict(requested_kwargs) if is_engine else self._cpu_kwargs_if_fallback(requested_kwargs)
            if is_engine:
                # The TensorRT engine already owns its CUDA execution
                # context. ``YOLO.to`` and PyTorch ``half`` are invalid here;
                # the light reference invokes the engine directly.
                effective_kwargs.pop("device", None)
                effective_kwargs.pop("half", None)
            if (
                not is_engine
                and
                effective_kwargs.get("device") == "cuda"
                and not self._cuda_fallback_is_active()
                and not self._model_parameter_device(model).startswith("cuda")
            ):
                # Ultralytics accepts ``device='cuda'`` per call, but a cached
                # predictor can still retain CPU parameters after a prior
                # reload. Make the actual model placement explicit and expose
                # any placement failure through the same CPU fallback path.
                try:
                    self._move_model_to(model, "cuda")
                except Exception as move_exc:
                    if not self._is_cuda_failure(move_exc):
                        raise
                    self._mark_cuda_fallback(move_exc)
                    model_warnings.append(f"CUDA_FALLBACK_TO_CPU:{move_exc}")
                    try:
                        self._move_model_to(model, "cpu")
                    except Exception as cpu_move_exc:
                        model_warnings.append(f"cuda_model_cpu_move_failed:{cpu_move_exc}")
                    effective_kwargs = self._cpu_kwargs(effective_kwargs)
            try:
                try:
                    import torch
                except ImportError:
                    torch = None

                # Do not allow two independent model calls to race a CUDA
                # context while it is being tested/recovered. The executor
                # remains available for CPU hosts and for the existing
                # parallel contract, but CUDA calls are serialized at the
                # boundary where the driver can fail.
                cuda_call = is_engine or effective_kwargs.get("device") == "cuda"
                call_lock = self._cuda_inference_lock_for_tests() if cuda_call else nullcontext()
                with call_lock:
                    if cuda_call and not is_engine and self._cuda_fallback_is_active():
                        effective_kwargs = self._cpu_kwargs(effective_kwargs)
                        model_warnings.append("CUDA_FALLBACK_TO_CPU")
                    inference_context = torch.inference_mode() if torch is not None else nullcontext()
                    with inference_context:
                        results = model(frame, **effective_kwargs)
            except Exception as exc:
                if is_engine:
                    # TensorRT engines are GPU-only artifacts. Retrying them
                    # through the PyTorch CPU path produces either a second
                    # failure or an unexplained 1–5 FPS fallback.
                    raise
                if effective_kwargs.get("device") != "cuda" or not self._is_cuda_failure(exc):
                    raise
                # A CUDA exception is not a detection miss. Clear the cache,
                # move this model out of the failed context and retry the same
                # captured frame on CPU so tracking can continue honestly.
                self._mark_cuda_fallback(exc)
                model_warnings.append(f"CUDA_FALLBACK_TO_CPU:{exc}")
                try:
                    self._move_model_to(model, "cpu")
                except Exception as move_exc:
                    model_warnings.append(f"cuda_model_cpu_move_failed:{move_exc}")
                cpu_kwargs = self._cpu_kwargs(effective_kwargs)
                try:
                    inference_context = torch.inference_mode() if torch is not None else nullcontext()
                    with inference_context:
                        results = model(frame, **cpu_kwargs)
                except Exception as cpu_exc:
                    raise RuntimeError(f"cuda_failed_and_cpu_retry_failed:{cpu_exc}") from cpu_exc
            bodies, balloons, parsed_warnings = self._detections_from_results(
                results,
                width,
                height,
                model_id=spec["model_id"],
                role=spec["role"],
                class_names=spec["class_names"],
            )
            model_warnings.extend(parsed_warnings)
            if is_engine:
                runtime_state = getattr(self, "_engine_runtime_success", None)
                if runtime_state is None:
                    runtime_state = {}
                    self._engine_runtime_success = runtime_state
                runtime_state[spec["path"]] = True
            return (
                bodies,
                balloons,
                model_warnings,
                round((time.perf_counter() - started) * 1000, 3),
                str(spec["role"]),
            )
        except Exception as exc:  # pragma: no cover - host/model dependent
            if is_engine:
                runtime_state = getattr(self, "_engine_runtime_success", None)
                if runtime_state is None:
                    runtime_state = {}
                    self._engine_runtime_success = runtime_state
                runtime_state[spec["path"]] = False
            failure_code = "tensorrt_engine_inference_failed" if is_engine else "ultralytics_model_inference_failed"
            return (
                [],
                [],
                [*model_warnings, f"{failure_code}:{spec['model_id']}:{exc}"],
                round((time.perf_counter() - started) * 1000, 3),
                str(spec["role"]),
            )

    @staticmethod
    def _is_cuda_failure(exc: Exception) -> bool:
        text = str(exc).lower()
        return any(
            marker in text
            for marker in (
                "cuda error",
                "cudaerror",
                "cudnn",
                "cuda out of memory",
                "out of memory",
                "device-side assert",
                "no kernel image is available",
            )
        )

    def _cuda_state_lock_for_tests(self):
        lock = getattr(self, "_cuda_state_lock", None)
        if lock is None:
            lock = threading.RLock()
            self._cuda_state_lock = lock
        return lock

    def _cuda_inference_lock_for_tests(self):
        lock = getattr(self, "_cuda_inference_lock", None)
        if lock is None:
            lock = threading.Lock()
            self._cuda_inference_lock = lock
        return lock

    def _cuda_fallback_is_active(self) -> bool:
        with self._cuda_state_lock_for_tests():
            return bool(getattr(self, "_cuda_fallback_active", False))

    def _mark_cuda_fallback(self, exc: Exception) -> None:
        now = time.time()
        with self._cuda_state_lock_for_tests():
            self._cuda_fallback_active = True
            self._cuda_fallback_reason = str(exc)
            self._cuda_last_failure_at = now
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            # The failed CUDA context is already being treated as unhealthy;
            # CPU fallback must not depend on cleanup succeeding.
            pass

    @staticmethod
    def _cpu_kwargs(kwargs: dict) -> dict:
        result = dict(kwargs)
        result["device"] = "cpu"
        result.pop("half", None)
        return result

    def _cpu_kwargs_if_fallback(self, kwargs: dict) -> dict:
        if kwargs.get("device") == "cuda" and self._cuda_fallback_is_active():
            return self._cpu_kwargs(kwargs)
        return dict(kwargs)

    @staticmethod
    def _move_model_to(model, device: str) -> None:
        mover = getattr(model, "to", None)
        if callable(mover):
            mover(device)
            return
        inner = getattr(model, "model", None)
        inner_mover = getattr(inner, "to", None)
        if callable(inner_mover):
            inner_mover(device)
            return
        raise RuntimeError("model_has_no_device_move_api")

    @staticmethod
    def _model_parameter_device(model) -> str:
        inner = getattr(model, "model", model)
        parameters = getattr(inner, "parameters", None)
        if not callable(parameters):
            return "unknown"
        try:
            parameter = next(parameters())
            return str(getattr(parameter, "device", "unknown"))
        except (StopIteration, RuntimeError, TypeError):
            return "unknown"

    @staticmethod
    def _tensorrt_predictor_state(model) -> str:
        """Return TensorRT predictor readiness from the live backend object."""
        predictor = getattr(model, "predictor", None)
        if predictor is None:
            return "not_initialized"
        backend = getattr(predictor, "model", None)
        if backend is None:
            return "predictor_not_ready"
        device = str(getattr(backend, "device", ""))
        engine = getattr(backend, "model", None)
        context = getattr(backend, "context", None)
        if (
            type(backend).__name__ == "AutoBackend"
            and device.casefold().startswith("cuda")
            and type(engine).__name__ == "ICudaEngine"
            and type(context).__name__ == "IExecutionContext"
        ):
            return "ready"
        if not device.casefold().startswith("cuda"):
            return "not_cuda"
        return "predictor_not_ready"

    def _ensure_model_warmup(self, model, model_path: str, kwargs: dict, *, is_engine: bool = False) -> list[str]:
        # TensorRT performs its own context/tactic initialization on the first
        # real call. Running the PyTorch warm-up path would call ``model.to``
        # on an AutoBackend engine and can poison the CUDA context.
        if is_engine or self._is_tensorrt_path(model_path):
            return []
        if kwargs.get("device") != "cuda":
            return []
        profile = getattr(getattr(self, "vision_runtime", None), "profile", None)
        if not bool(getattr(profile, "warmup_on_load", False)):
            return []
        imgsz = min(int(kwargs.get("imgsz", CUDA_SAFE_IMGSZ) or CUDA_SAFE_IMGSZ), CUDA_SAFE_IMGSZ)
        key = (str(model_path), imgsz)
        if key in getattr(self, "_cuda_warmup_done", set()):
            return []
        warnings: list[str] = []
        try:
            import torch
            if not torch.cuda.is_available():
                return []
            free_bytes, _ = torch.cuda.mem_get_info()
            if int(free_bytes) < CUDA_MIN_FREE_MEMORY_BYTES:
                raise RuntimeError(f"cuda_free_memory_below_512mb:{int(free_bytes)}")
            self._move_model_to(model, "cuda")
            if np is None:
                raise RuntimeError("numpy_unavailable_for_cuda_warmup")
            warmup_frame = np.zeros((imgsz, imgsz, 3), dtype=np.uint8)
            warmup_kwargs = {
                "imgsz": imgsz,
                "conf": float(kwargs.get("conf", 0.25)),
                "iou": float(kwargs.get("iou", 0.45)),
                "max_det": int(kwargs.get("max_det", 20)),
                "device": "cuda",
                "verbose": False,
            }
            with self._cuda_inference_lock_for_tests():
                with torch.inference_mode():
                    model(warmup_frame, **warmup_kwargs)
                torch.cuda.synchronize()
            warmup_done = getattr(self, "_cuda_warmup_done", None)
            if warmup_done is None:
                warmup_done = set()
                self._cuda_warmup_done = warmup_done
            warmup_done.add(key)
        except Exception as exc:
            self._mark_cuda_fallback(exc)
            warnings.append(f"CUDA_WARMUP_FAILED:{exc}")
            try:
                self._move_model_to(model, "cpu")
            except Exception as move_exc:
                warnings.append(f"cuda_model_cpu_move_failed:{move_exc}")
        return warnings

    def _maybe_recover_cuda(self) -> None:
        if not self._cuda_fallback_is_active():
            return
        now = time.time()
        with self._cuda_state_lock_for_tests():
            last_attempt = float(getattr(self, "_cuda_last_recovery_attempt", 0.0) or 0.0)
            if now - last_attempt < CUDA_RECOVERY_INTERVAL_S:
                return
            self._cuda_last_recovery_attempt = now
        try:
            import torch
            if not torch.cuda.is_available():
                return
            free_bytes, _ = torch.cuda.mem_get_info()
            if int(free_bytes) < CUDA_MIN_FREE_MEMORY_BYTES:
                return
            with self._cuda_inference_lock_for_tests():
                probe = torch.zeros((1,), device="cuda")
                probe.sum().item()
                torch.cuda.synchronize()
                for path, model in list(getattr(self, "_yolo_models", {}).items()):
                    if self._is_tensorrt_path(path):
                        continue
                    self._move_model_to(model, "cuda")
            with self._cuda_state_lock_for_tests():
                self._cuda_fallback_active = False
                self._cuda_fallback_reason = None
                self._cuda_last_recovery_success_at = time.time()
        except Exception as exc:
            self._mark_cuda_fallback(exc)

    @staticmethod
    def _combined_balloon_roi(bodies, width: int, height: int) -> tuple[int, int, int, int] | None:
        """Union crop for all lower 2x aircraft attachment regions."""
        if not bodies or width <= 1 or height <= 1:
            return None
        regions: list[tuple[int, int, int, int]] = []
        for body in bodies:
            bbox = body.bbox
            # Small crop padding avoids cutting a balloon box at the region
            # edge; the exact centre gate is applied after inference.
            pad_x = bbox.w * 0.10
            pad_y = bbox.h * 0.10
            left = max(0, int(math.floor(bbox.x - pad_x)))
            right = min(width, int(math.ceil(bbox.x + bbox.w + pad_x)))
            top = max(0, int(math.floor(bbox.y + bbox.h - pad_y)))
            bottom = min(height, int(math.ceil(bbox.y + bbox.h * 3 + pad_y)))
            if right > left and bottom > top:
                regions.append((left, top, right, bottom))
        if not regions:
            return None
        return (
            min(item[0] for item in regions),
            min(item[1] for item in regions),
            max(item[2] for item in regions),
            max(item[3] for item in regions),
        )

    @staticmethod
    def _translate_balloon_detections(
        balloons: list[BalloonDetection], left: int, top: int
    ) -> list[BalloonDetection]:
        return [
            balloon.model_copy(update={
                "bbox": balloon.bbox.model_copy(update={
                    "x": balloon.bbox.x + left,
                    "y": balloon.bbox.y + top,
                }),
                "center_x": balloon.center_x + left,
                "center_y": balloon.center_y + top,
            })
            for balloon in balloons
        ]

    def _filter_balloons_by_attachment_regions(
        self,
        balloons: list[BalloonDetection],
        bodies: list[BodyDetection],
    ) -> list[BalloonDetection]:
        now_mono = time.monotonic()
        # Clean up expired ghost regions from memory
        expired_ids = [
            tid
            for tid, g in self._attachment_memory.items()
            if (now_mono - g.last_seen_mono) > self.ATTACHMENT_COAST_TTL_S
        ]
        for tid in expired_ids:
            del self._attachment_memory[tid]

        # Get coasted/active regions from body tracker
        coasted = self.body_tracker.coasted_regions(self.ATTACHMENT_COAST_TTL_S)
        coasted_by_id = {c["track_id"]: c for c in coasted}

        # Update ghost memory with live bodies seen in current frame
        for body in bodies:
            track_id = body.track_id if body.track_id is not None else body.id
            ghost = self._attachment_memory.get(track_id)
            if ghost is None:
                ghost = GhostRegion(
                    track_id=track_id,
                    bbox=body.bbox,
                    last_seen_mono=now_mono,
                )
                self._attachment_memory[track_id] = ghost
            else:
                ghost.bbox = body.bbox
                ghost.last_seen_mono = now_mono

        # Kural 1: under BALLOON_AIRCRAFT a balloon may never exist as a
        # standalone target.  With neither a live body nor a coasting ghost
        # region there is no owner, so every balloon detection is rejected
        # (this was the workshop "HEDEF BALONU #3 | ATEŞ SERBEST" false
        # positive: a red fuselage triggering the balloon model with no
        # aircraft detection present).
        if not bodies and not self._attachment_memory:
            return []

        kept: list[BalloonDetection] = []
        kept_balloon_ids: set[int] = set()
        prev_memory_centers = {
            tid: list(g.last_balloon_centers) for tid, g in self._attachment_memory.items()
        }

        # Step 1: Check against LIVE bodies
        for balloon in balloons:
            for body in bodies:
                # Check if this balloon is an already-tracked or confirmed attachment
                is_confirmed = False
                track_id = body.track_id if body.track_id is not None else body.id
                if track_id in prev_memory_centers and prev_memory_centers[track_id]:
                    for prev_cx, prev_cy in prev_memory_centers[track_id]:
                        if math.hypot(balloon.center_x - prev_cx, balloon.center_y - prev_cy) <= 140.0:
                            is_confirmed = True
                            break
                if not is_confirmed and self.balloon_tracker is not None:
                    active_tracks = getattr(
                        getattr(self.balloon_tracker, "_tracks", None), "values", lambda: []
                    )()
                    for trk in active_tracks:
                        if getattr(trk, "hits", 1) >= 2 and math.hypot(balloon.center_x - trk.center_x, balloon.center_y - trk.center_y) <= 140.0:
                            is_confirmed = True
                            break

                # Kural 2: lower ROI, L4: silhouette overlap reject.
                # Confirmed tracked balloons use relaxed gate (IoU < 0.35, y + 0.5h) to survive maneuvers.
                if not stage3_roi.attachment_roi_contains(
                    body.bbox, balloon.center_x, balloon.center_y, relaxed=is_confirmed
                ):
                    continue
                if stage3_roi.body_balloon_overlap_reject(body.bbox, balloon.bbox, relaxed=is_confirmed):
                    continue
                kept.append(balloon)
                kept_balloon_ids.add(balloon.id)
                if track_id in self._attachment_memory:
                    self._attachment_memory[track_id].last_balloon_centers.append(
                        (float(balloon.center_x), float(balloon.center_y))
                    )
                    if len(self._attachment_memory[track_id].last_balloon_centers) > 5:
                        self._attachment_memory[track_id].last_balloon_centers = (
                            self._attachment_memory[track_id].last_balloon_centers[-5:]
                        )
                break

        # Step 2: Check remaining balloons against GHOST regions (missing bodies still within TTL)
        live_body_track_ids = {b.track_id for b in bodies if b.track_id is not None}
        ghost_candidates = [
            g
            for tid, g in self._attachment_memory.items()
            if tid not in live_body_track_ids
        ]

        for balloon in balloons:
            if balloon.id in kept_balloon_ids:
                continue
            for ghost in ghost_candidates:
                c_info = coasted_by_id.get(ghost.track_id)
                g_bbox = c_info["bbox"] if c_info is not None else ghost.bbox

                # Critical rule: ghost region only accepts an existing/tracked balloon,
                # NEVER a brand new unassociated detection.
                is_existing = False
                for prev_cx, prev_cy in ghost.last_balloon_centers:
                    if math.hypot(balloon.center_x - prev_cx, balloon.center_y - prev_cy) <= 140.0:
                        is_existing = True
                        break
                if not is_existing and self.balloon_tracker is not None:
                    active_tracks = getattr(
                        getattr(self.balloon_tracker, "_tracks", None), "values", lambda: []
                    )()
                    for trk in active_tracks:
                        if math.hypot(balloon.center_x - trk.center_x, balloon.center_y - trk.center_y) <= 140.0:
                            is_existing = True
                            break

                if not is_existing:
                    continue

                # Kural 2 on the coasted/ghost bbox, L4: silhouette reject (relaxed for existing tracks).
                if not stage3_roi.attachment_roi_contains(
                    g_bbox, balloon.center_x, balloon.center_y, relaxed=True
                ):
                    continue
                if stage3_roi.body_balloon_overlap_reject(g_bbox, balloon.bbox, relaxed=True):
                    continue

                kept.append(balloon)
                kept_balloon_ids.add(balloon.id)
                ghost.last_balloon_centers.append(
                    (float(balloon.center_x), float(balloon.center_y))
                )
                if len(ghost.last_balloon_centers) > 5:
                    ghost.last_balloon_centers = ghost.last_balloon_centers[-5:]
                break

        return kept

    @staticmethod
    def _balloons_inside_attachment_regions(
        balloons: list[BalloonDetection], bodies
    ) -> list[BalloonDetection]:
        # Kural 1: no live body means no standalone target balloon under the
        # combined BALLOON_AIRCRAFT policy.  The old "keep everything" bench
        # fallback was the root cause of the workshop false positive.
        if not bodies:
            return []

        kept: list[BalloonDetection] = []
        for balloon in balloons:
            for body in bodies:
                # Kural 2 shared geometry (+20% horizontal, 2.5h below the
                # lower edge, nothing inside the fuselage) and the L4
                # silhouette overlap reject.
                if not stage3_roi.attachment_roi_contains(
                    body.bbox, balloon.center_x, balloon.center_y
                ):
                    continue
                if stage3_roi.body_balloon_overlap_reject(body.bbox, balloon.bbox):
                    continue
                kept.append(balloon)
                break
        return kept

    def _active_yolo_model_specs(self) -> list[dict]:
        """Return separate body/balloon model provenance, never a blind path."""
        profile = self.vision_runtime.profile
        target_policy = self._active_target_policy()
        competition_stage = None
        op = getattr(self, "operation", None)
        if op is not None and hasattr(op, "state"):
            op_st = op.state()
            c_stg = getattr(op_st, "competition_stage", None)
            competition_stage = c_stg.value if hasattr(c_stg, "value") else str(c_stg or "")
        cache_key = (
            str(getattr(self.vision, "body_model_path", None) or ""),
            str(getattr(self.vision, "balloon_model_path", None) or ""),
            str(getattr(profile, "active_body_model_id", None) or ""),
            str(getattr(profile, "active_balloon_model_id", None) or ""),
            int(getattr(self.vision_runtime, "parameter_version", 0) or 0),
            target_policy,
            str(competition_stage or ""),
        )
        now = time.monotonic()
        if (
            getattr(self, "_model_specs_cache_key", None) == cache_key
            and now - float(getattr(self, "_model_specs_cache_at", 0.0) or 0.0) < 60.0
        ):
            return [dict(item) for item in getattr(self, "_model_specs_cache", [])]
        specs: list[dict] = []
        seen_roles: set[str] = set()

        # Setup Center is the operator-visible source of truth.  A model
        # selected there must take precedence over a previously activated
        # registry model; otherwise the UI reports the new path as applied
        # while inference silently keeps running the old model.
        for role, path in (("body", self.vision.body_model_path), ("balloon", self.vision.balloon_model_path)):
            if not path:
                continue
            resolved_path = resolve_project_path(path)
            if not resolved_path.is_file():
                continue
            normalized_path = str(resolved_path)
            existing = next((item for item in specs if item["path"] == normalized_path), None)
            if existing is not None:
                existing["role"] = "combined"
            else:
                specs.append(
                    {
                        "role": role,
                        "model_id": f"setup_{role}_path",
                        "path": normalized_path,
                        "class_names": [],
                        "format": self._model_format(normalized_path),
                        "input_size": self._model_input_size(resolved_path),
                    }
                )
            seen_roles.add(role)

        # Registry activation is the fallback for roles for which Setup did
        # not provide a path.  This keeps engineering/package workflows
        # working without allowing a stale registry selection to override the
        # operator's current choice.
        for role, model_id in (("body", profile.active_body_model_id), ("balloon", profile.active_balloon_model_id)):
            if not model_id or role in seen_roles:
                continue
            try:
                model = self.vision_runtime.models.get_model(model_id)
            except KeyError:
                continue
            if not model.file_path or not Path(model.file_path).exists():
                continue
            existing = next((item for item in specs if item["path"] == str(model.file_path)), None)
            if existing is not None:
                # Package activation often selects a combined detector for
                # both slots.  Interpret it as combined rather than silently
                # throwing away the second semantic role.
                existing["role"] = "combined"
            else:
                specs.append(
                    {
                        "role": role,
                        "model_id": model.model_id,
                        "path": str(model.file_path),
                        "class_names": list(model.class_names),
                        "format": self._model_format(str(model.file_path)),
                        "input_size": self._model_input_size(Path(model.file_path), getattr(model, "input_size", None)),
                    }
                )
            seen_roles.add(role)
        competition_stage = None
        op = getattr(self, "operation", None)
        if op is not None and hasattr(op, "state"):
            op_st = op.state()
            c_stg = getattr(op_st, "competition_stage", None)
            competition_stage = c_stg.value if hasattr(c_stg, "value") else str(c_stg or "")

        if target_policy == "BALLOON" and competition_stage not in {"STAGE_2", "STAGE2", "STAGE_3", "STAGE3"}:
            specs = [item for item in specs if item["role"] in {"balloon", "combined"}]
        elif target_policy == "AIRCRAFT":
            specs = [item for item in specs if item["role"] in {"body", "combined"}]

        self._model_specs_cache_key = cache_key
        self._model_specs_cache_at = now
        self._model_specs_cache = [dict(item) for item in specs]
        return specs

    @staticmethod
    def _model_format(path: str) -> str:
        return Path(path).suffix.lower().lstrip(".")

    @staticmethod
    def _is_tensorrt_path(path: str) -> bool:
        return Path(path).suffix.lower() == ".engine"

    _ENGINE_SHAPE_CACHE: dict[str, int] = {}

    @staticmethod
    def _model_input_size(path: Path, declared: int | None = None) -> int | None:
        """Resolve an engine's fixed input shape without reading the full file into memory."""
        if path.suffix.lower() != ".engine":
            return int(declared) if declared else None
        path_str = str(path)
        if path_str in VisionPipeline._ENGINE_SHAPE_CACHE:
            return VisionPipeline._ENGINE_SHAPE_CACHE[path_str]
        try:
            with open(path, "rb") as f:
                header = f.read(min(ENGINE_HEADER_READ_BYTES, 65536)).decode("utf-8", errors="ignore")
            match = re.search(r'"imgsz"\s*:\s*\[\s*(\d+)', header)
            if match:
                val = int(match.group(1))
                VisionPipeline._ENGINE_SHAPE_CACHE[path_str] = val
                return val
        except (OSError, ValueError):
            pass
        val = int(declared) if declared else None
        if val is not None:
            VisionPipeline._ENGINE_SHAPE_CACHE[path_str] = val
        return val

    def y17_gosterim_modu(self) -> bool:
        """Y17: Asama 3 + hedef BALON -> govdeler yalniz gosterim."""
        try:
            st = self.operation.state()
            stg = getattr(st, "competition_stage", None)
            stg = stg.value if hasattr(stg, "value") else str(stg or "")
            return str(st.target_policy.value) == "BALLOON" and str(stg).upper() in {"STAGE_3", "STAGE3"}
        except Exception:
            return False

    def y17_kontrol_olayi(self, event):
        """Kontrol/IFF/sahiplik yoluna govdesiz kopya verir (yalniz Y17 modunda)."""
        if event is None or not getattr(event, "body_detections", None):
            return event
        if not self.y17_gosterim_modu():
            return event
        return event.model_copy(update={"body_detections": []})

    def _active_target_policy(self) -> str:
        operation = getattr(self, "operation", None)
        if operation is None:
            return "BALLOON"
        state = operation.state()
        return str(state.target_policy.value)

    def _load_yolo_model(self, model_path: str):
        if model_path in self._yolo_models:
            model = self._yolo_models.pop(model_path)
            self._yolo_models[model_path] = model
            return model
        with self._model_load_lock:
            if model_path in self._yolo_models:
                model = self._yolo_models.pop(model_path)
                self._yolo_models[model_path] = model
                return model
            try:
                import torch

                # These are process-wide knobs; set them once before model
                # execution rather than racing two worker threads on every
                # first frame.
                torch.set_num_threads(4)
                torch.set_num_interop_threads(2)
                if not self._torch_runtime_configured:
                    # These flags improve CUDA kernel selection on RTX-class
                    # hardware.  FP16 remains controlled by the runtime
                    # profile; CPU hosts simply ignore the CUDA switches.
                    # The live profile can change image size at runtime. CUDA
                    # autotuning paused the first frame at each new size for
                    # 1.3-5.2 seconds on the Windows RTX 2050. Heuristic
                    # selection is slightly more conservative but removes
                    # that repeated cold-shape stall and keeps startup honest.
                    torch.backends.cudnn.benchmark = False
                    torch.backends.cuda.matmul.allow_tf32 = True
                    torch.backends.cudnn.allow_tf32 = True
                    try:
                        torch.set_float32_matmul_precision("high")
                    except Exception:
                        pass
                    self._torch_runtime_configured = True
            except Exception:
                pass
            from ultralytics import YOLO

            model = YOLO(model_path)
            self._yolo_models[model_path] = model
            self._yolo_models.move_to_end(model_path)
            while len(self._yolo_models) > int(self._yolo_model_cache_max):
                self._yolo_models.popitem(last=False)
            return model

    def _preprocess_frame_for_ultralytics(self, frame):
        # Preserve the camera pixels used by the actual YOLO training path.
        # The old alpha/beta enhancement suppresses the weak `dusman`
        # response of eski_sistem_arayüz/models/yolo/best.pt on screen tests.
        return frame, ["ultralytics_raw_camera_frame"]

    def _legacy_color_balloon_detections(self, frame, width: int, height: int) -> list[BalloonDetection]:
        if cv2 is None or np is None or frame is None:
            return []
        blurred = cv2.GaussianBlur(frame, (9, 9), 0)
        hsv = cv2.cvtColor(blurred, cv2.COLOR_BGR2HSV)
        lower_red1 = np.array([0, 38, 35])
        upper_red1 = np.array([20, 255, 255])
        lower_red2 = np.array([160, 38, 35])
        upper_red2 = np.array([180, 255, 255])
        hsv_mask = cv2.bitwise_or(cv2.inRange(hsv, lower_red1, upper_red1), cv2.inRange(hsv, lower_red2, upper_red2))
        # Laptop cameras often overexpose red balloons into pink/washed red.
        # Use a conservative red-dominance mask so YOLO-empty frames still
        # expose the physical red test targets without requiring hardware I/O.
        bgr = blurred.astype(np.int16)
        blue = bgr[:, :, 0]
        green = bgr[:, :, 1]
        red = bgr[:, :, 2]
        red_dominant = (
            (red > 95)
            & ((red - green) > 18)
            & ((red - blue) > 18)
            & (red > (green * 1.08))
            & (red > (blue * 1.08))
        )
        dominance_mask = (red_dominant.astype(np.uint8)) * 255
        mask = cv2.bitwise_or(hsv_mask, dominance_mask)
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=2)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel, iterations=1)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        detections: list[BalloonDetection] = []
        frame_area = float(width * height)
        min_contour_area = max(140.0, frame_area * 0.00012)
        max_contour_area = frame_area * 0.04
        max_box_side = min(width, height) * 0.45
        min_box_side = min(width, height) * 0.012
        for contour in sorted(contours, key=cv2.contourArea, reverse=True):
            area = cv2.contourArea(contour)
            if area < min_contour_area or area > max_contour_area:
                continue
            perimeter = cv2.arcLength(contour, True)
            if perimeter <= 0:
                continue
            circularity = 4.0 * np.pi * area / (perimeter * perimeter)
            if circularity < 0.5:
                continue
            x, y, w, h = cv2.boundingRect(contour)
            if w <= 0 or h <= 0:
                continue
            if x <= 0 or y <= 0 or x + w >= width - 1 or y + h >= height - 1:
                continue
            if min(w, h) < min_box_side:
                continue
            if max(w, h) > max_box_side:
                continue
            extent = area / float(w * h)
            if extent < 0.35:
                continue
            aspect_ratio = float(w) / float(h)
            if not 0.55 < aspect_ratio < 1.8:
                continue
            x = max(0, min(width - 1, int(x)))
            y = max(0, min(height - 1, int(y)))
            w = max(1, min(width - x, int(w)))
            h = max(1, min(height - y, int(h)))
            detections.append(
                BalloonDetection(
                    id=len(detections) + 1,
                    confidence=0.82,
                    bbox=BBox(x=x, y=y, w=w, h=h, format="pixel"),
                    center_x=int(x + w / 2),
                    center_y=int(y + h / 2),
                    source="legacy_color_fallback",
                )
            )
        return detections

    def _detections_from_results(
        self,
        results,
        width: int,
        height: int,
        *,
        model_id: str,
        role: str,
        class_names: list[str],
    ) -> tuple[list, list[BalloonDetection], list[str]]:
        """Preserve ``box.cls`` semantics and reject unknown class mappings.

        Previously every YOLO box became a BalloonDetection.  That can make a
        class model look operational while deleting the very evidence A3
        needs.  The parser now has an explicit role plus registry provenance.
        """
        from app.schemas.vision import BodyDetection

        bodies: list[BodyDetection] = []
        balloons: list[BalloonDetection] = []
        warnings: list[str] = []
        body_id = 1
        balloon_id = 1
        registry_names = {index: self._normalise_class_name(name) for index, name in enumerate(class_names)}
        target_map = {
            self._normalise_class_name(name): int(class_id)
            for name, class_id in self.vision_runtime.profile.target_class_map.items()
        }
        for result in results:
            boxes = getattr(result, "boxes", None)
            if boxes is None:
                continue
            output_names = getattr(result, "names", {}) or {}
            # Ultralytics exposes one tensor per field for the whole result.
            # Convert those tensors to CPU once instead of synchronising GPU
            # memory separately for every box.
            for xyxy_raw, conf_raw, cls_raw in self._iter_box_rows(boxes):
                xyxy = self._box_values(xyxy_raw)
                conf = self._box_scalar(conf_raw) if conf_raw is not None else 0.0
                class_id = int(round(self._box_scalar(cls_raw))) if cls_raw is not None else -1
                output_name = self._normalise_class_name(self._class_name_from_result(output_names, class_id))
                # A direct Setup-selected .pt has no registry metadata yet;
                # its embedded Ultralytics class name is then authoritative.
                registry_name = registry_names.get(class_id) or output_name
                if not registry_name:
                    warnings.append(f"model_class_id_unmapped:{model_id}:{class_id}")
                    continue
                if output_name and output_name != registry_name:
                    warnings.append(f"model_class_mapping_mismatch:{model_id}:{class_id}")
                    continue
                class_name = registry_name
                if target_map and class_name in target_map and target_map[class_name] != class_id:
                    warnings.append(f"runtime_target_class_map_mismatch:{model_id}:{class_name}:{class_id}")
                    continue
                x1, y1, x2, y2 = [int(round(value)) for value in xyxy]
                x1 = max(0, min(width - 1, x1))
                y1 = max(0, min(height - 1, y1))
                x2 = max(x1 + 1, min(width, x2))
                y2 = max(y1 + 1, min(height, y2))
                bbox = BBox(x=x1, y=y1, w=max(1, x2 - x1), h=max(1, y2 - y1), format="pixel")
                # Legacy field models label balloon teams as dost/dusman.
                # In the balloon slot they are real balloon observations; a
                # later association/IFF stage decides engagement eligibility.
                is_balloon = class_name in {"balloon", "dost", "dusman"}
                is_competition_body = class_name in {"f16", "helicopter", "ballistic_missile", "mini_micro_uav"}
                # Aşama 2 does not need class recognition, but it does need a
                # real generic carrier/body for balloon association.  Keep it
                # explicit rather than pretending an unknown A3 class is F16.
                is_generic_body = role == "body" and not is_balloon and not is_competition_body
                is_body = is_competition_body or is_generic_body
                if role == "body" and not is_body:
                    warnings.append(f"body_model_non_body_class_rejected:{model_id}:{class_name}")
                    continue
                if role == "balloon" and not is_balloon:
                    warnings.append(f"balloon_model_non_balloon_class_rejected:{model_id}:{class_name}")
                    continue
                if is_balloon:
                    # A balloon may be small at competition distance, so do
                    # not impose a large minimum area. Reject only geometry
                    # that cannot plausibly be the approximately round target
                    # and edge-truncated fragments that are unsafe to aim at.
                    aspect_ratio = float(bbox.w) / float(max(bbox.h, 1))
                    edge_truncated = x1 <= 0 or y1 <= 0 or x2 >= width or y2 >= height
                    if edge_truncated:
                        # The light field engine legitimately reports a
                        # balloon touching the image boundary while the
                        # turret is reacquiring it. Keep the measurement for
                        # tracking; downstream motion/safety envelopes still
                        # decide whether a command is admissible.
                        warnings.append(f"balloon_geometry_edge:{model_id}:{class_name}")
                        warnings.append(f"balloon_geometry_rejected_edge:{model_id}:{class_name}")
                    if not 0.55 <= aspect_ratio <= 1.8:
                        warnings.append(f"balloon_geometry_rejected_aspect:{model_id}:{class_name}")
                        continue
                if is_body:
                    bodies.append(
                        BodyDetection(
                            id=body_id,
                            class_name=class_name if is_competition_body else "generic_target",
                            class_id=class_id,
                            confidence=round(conf, 3),
                            bbox=bbox,
                            source=f"ultralytics_yolo:body:{model_id}",
                        )
                    )
                    body_id += 1
                elif is_balloon:
                    balloons.append(
                        BalloonDetection(
                            id=balloon_id,
                            confidence=round(conf, 3),
                            bbox=bbox,
                            center_x=int((x1 + x2) / 2),
                            center_y=int((y1 + y2) / 2),
                            source=f"ultralytics_yolo:balloon:{model_id}",
                        )
                    )
                    balloon_id += 1
                else:
                    warnings.append(f"unsupported_competition_class:{model_id}:{class_name}")
        return bodies, balloons, sorted(set(warnings))

    @staticmethod
    def _box_values(value) -> list[float]:
        if hasattr(value, "detach"):
            value = value.detach().cpu().numpy()
        if hasattr(value, "tolist"):
            value = value.tolist()
        return [float(item) for item in value]

    @classmethod
    def _iter_box_rows(cls, boxes):
        """Yield ``(xyxy, confidence, class_id)`` rows efficiently.

        The fallback keeps compatibility with lightweight fake box objects
        used by contract tests and with older Ultralytics releases that do
        not expose batched tensor attributes.
        """
        xyxy = getattr(boxes, "xyxy", None)
        conf = getattr(boxes, "conf", None)
        classes = getattr(boxes, "cls", None)
        if xyxy is not None:
            try:
                xyxy_rows = cls._to_nested_list(xyxy)
                conf_rows = cls._to_flat_list(conf)
                class_rows = cls._to_flat_list(classes)
                for index, row in enumerate(xyxy_rows):
                    yield (
                        row,
                        conf_rows[index] if index < len(conf_rows) else None,
                        class_rows[index] if index < len(class_rows) else None,
                    )
                return
            except (TypeError, ValueError, IndexError):
                # Fall through to per-box compatibility mode.
                pass
        try:
            iterator = iter(boxes)
        except TypeError:
            iterator = iter(())
        for box in iterator:
            box_xyxy = getattr(box, "xyxy", None)
            box_conf = getattr(box, "conf", None)
            box_cls = getattr(box, "cls", None)
            if box_xyxy is not None:
                try:
                    box_xyxy = box_xyxy[0]
                except (IndexError, TypeError):
                    pass
            if box_conf is not None:
                try:
                    box_conf = box_conf[0]
                except (IndexError, TypeError):
                    pass
            if box_cls is not None:
                try:
                    box_cls = box_cls[0]
                except (IndexError, TypeError):
                    pass
            yield box_xyxy, box_conf, box_cls

    @staticmethod
    def _to_nested_list(value) -> list[list[float]]:
        if hasattr(value, "detach"):
            value = value.detach().cpu().tolist()
        elif hasattr(value, "tolist"):
            value = value.tolist()
        if value is None:
            return []
        if isinstance(value, (tuple, list)):
            if not value:
                return []
            if not isinstance(value[0], (tuple, list)):
                return [list(value)]
            return [list(row) for row in value]
        return []

    @staticmethod
    def _to_flat_list(value) -> list[float]:
        if hasattr(value, "detach"):
            value = value.detach().cpu().tolist()
        elif hasattr(value, "tolist"):
            value = value.tolist()
        if value is None:
            return []
        if isinstance(value, (tuple, list)):
            flattened: list[float] = []
            for item in value:
                if isinstance(item, (tuple, list)):
                    if item:
                        flattened.append(float(item[0]))
                else:
                    flattened.append(float(item))
            return flattened
        return [float(value)]

    @staticmethod
    def _box_scalar(value) -> float:
        if hasattr(value, "detach"):
            value = value.detach().cpu().item()
        elif hasattr(value, "item"):
            value = value.item()
        return float(value)

    @staticmethod
    def _class_name_from_result(names, class_id: int) -> str:
        if isinstance(names, dict):
            return str(names.get(class_id, ""))
        if isinstance(names, (list, tuple)) and 0 <= class_id < len(names):
            return str(names[class_id])
        return ""

    @staticmethod
    def _normalise_class_name(value: str) -> str:
        normalised = str(value).strip().lower().replace("-", "_").replace(" ", "_")
        aliases = {
            "f_16": "f16",
            "f16": "f16",
            "mini_micro_ua_v": "mini_micro_uav",
            "mini_micro_uav": "mini_micro_uav",
            # The delivered air-vehicle model uses the short training labels
            # ``drone`` and the dataset typo ``balistic``.  Normalize them at
            # the perception boundary so tracking, digital-twin semantics and
            # CommandGateway all receive the competition class vocabulary.
            "drone": "mini_micro_uav",
            "uav": "mini_micro_uav",
            "balistic": "ballistic_missile",
            "balistic_missile": "ballistic_missile",
            "ballisticmissile": "ballistic_missile",
        }
        return aliases.get(normalised, normalised)
