import time
import os
import mmap
import struct
import subprocess
import sys
import tempfile
import threading
import glob
import re
from collections import OrderedDict, deque
from pathlib import Path

import yaml

from app.schemas.camera_runtime import CameraRuntimeApplyResult, CameraRuntimeControlsUpdate, CameraRuntimeProfile, CameraRuntimeStatus
from app.schemas.config import AppConfig
from app.schemas.log import LogLevel
from app.services.device_manager_service import DeviceManagerService
from app.services.log_service import JsonlLogService
from app.services.storage_paths import project_root

try:  # pragma: no cover - host dependent
    import cv2
    import numpy as np
except Exception:  # pragma: no cover
    cv2 = None
    np = None


class CameraRuntimeService:
    def __init__(self, config: AppConfig, devices: DeviceManagerService, logger: JsonlLogService) -> None:
        self.config = config
        self.devices = devices
        self.logger = logger
        self.profile = CameraRuntimeProfile(
            source_type=config.camera_runtime.default_source_type,
            width=config.camera_runtime.default_width,
            height=config.camera_runtime.default_height,
            fps=config.camera_runtime.default_fps,
            pixel_format=config.camera_runtime.default_fourcc,
            lens_profile=config.camera_runtime.default_lens_profile,
            stream_width=config.camera_runtime.default_width,
            stream_height=config.camera_runtime.default_height,
            inference_width=config.camera_runtime.inference_width,
            inference_height=config.camera_runtime.inference_height,
            device_path=config.camera_runtime.default_device_path,
        )
        self.last_error: str | None = None
        self.last_warnings: list[str] = []
        self.last_capture_backend: str = "fallback"
        self.last_capture_error: str | None = None
        self.last_probe_result: dict | None = None
        self.last_frame = None
        self.last_frame_at: float | None = None
        self.last_frame_mean_luma: float | None = None
        self.frame_sequence = 0
        self.frame_timestamps = deque(maxlen=60)
        self.last_fps_measured = 0.0
        self.last_warmup_ms = 0.0
        self.last_frame_warnings: list[str] = []
        self.frame_lock = threading.Lock()
        self.capture = None
        self.capture_key: tuple | None = None
        self.capture_lock = threading.Lock()
        self.capture_worker_thread: threading.Thread | None = None
        self.capture_worker_process: subprocess.Popen | None = None
        # Windows DirectShow is isolated in a child process for crash
        # containment.  The child normally publishes JPEG bytes through a
        # named memory mapping; the old atomic-file bridge remains a fallback
        # for hosts where named mappings are unavailable.
        self.capture_shared_memory: mmap.mmap | None = None
        self.capture_shared_tag: str | None = None
        self.capture_shared_format: str | None = None
        self.capture_shared_last_sequence = 0
        # Raw BGR for 1920x1080 is about 6.2 MiB. Keeping a 16 MiB mapping
        # avoids JPEG encode/decode latency while leaving headroom for a
        # negotiated frame size change.
        self.capture_shared_size = 16 * 1024 * 1024
        self.capture_worker_stop = threading.Event()
        self.capture_worker_key: tuple | None = None
        self.capture_paused = False
        self._jpeg_cache_lock = threading.Lock()
        # Keep separate cached encodes for concurrent plain/overlay/UI
        # consumers.  A one-entry cache made each consumer evict the previous
        # one and repeat expensive JPEG work for the same capture.
        self._jpeg_cache_entries: OrderedDict[tuple, bytes] = OrderedDict()
        self._jpeg_cache_max_entries = 8
        # Retained as compatibility mirrors for older integrations/tests.
        self._jpeg_cache_key: tuple | None = None
        self._jpeg_cache_bytes: bytes | None = None
        self.updated_at = time.time()
        self.last_event: tuple[str, dict] | None = None
        self.path = project_root() / "config" / "runtime" / "camera_profile.active.yaml"
        self._load_persisted_profile()

    def status(self) -> CameraRuntimeStatus:
        decision = self._camera_source_decision()
        actual_width = self.profile.width
        actual_height = self.profile.height
        measured_fps = self.last_fps_measured if self.last_fps_measured > 0 else float(self.profile.fps)
        if self.last_frame is not None:
            actual_height = int(self.last_frame.shape[0])
            actual_width = int(self.last_frame.shape[1])
        last_frame_age_ms = None
        if self.last_frame_at is not None:
            last_frame_age_ms = max(0, int((time.time() - self.last_frame_at) * 1000))
        selected_device = decision["selected_device"]
        selected_backend = "mock" if self.profile.source_type == "mock" else self.last_capture_backend
        # A frame from a previous Setup/profile session is not proof that the
        # current camera stream is alive. Treat only a recent frame as live so
        # the UI cannot report "camera active" while showing a stale/black
        # capture after a device ownership conflict.
        frame_fresh_limit_ms = max(1500, int(5000 / max(self.profile.fps, 1)))
        frame_is_fresh = last_frame_age_ms is not None and last_frame_age_ms <= frame_fresh_limit_ms
        device_available = decision["capture_device"] is not None
        is_real_camera_evidence = (
            self.profile.source_type in {"usb", "laptop"}
            and device_available
            and self.last_frame is not None
            and frame_is_fresh
            and selected_backend in {"opencv", "ffmpeg"}
        )
        source_mode = str(decision["source_mode"])
        if is_real_camera_evidence:
            source_mode = "REAL_USB_CAMERA_LIVE" if decision["is_external_usb_camera"] else "REAL_LAPTOP_CAMERA_LIVE"
        elif self.last_frame is not None and decision["is_laptop_camera"]:
            source_mode = "REAL_LAPTOP_CAMERA_LATEST_FRAME"
        elif self.last_frame is not None and decision["is_external_usb_camera"]:
            source_mode = "REAL_USB_CAMERA_LATEST_FRAME"
        hardware_presence_note = str(decision["hardware_presence_note"])
        if is_real_camera_evidence:
            camera_name = str(decision.get("camera_name") or selected_device or "camera")
            hardware_presence_note = f"{camera_name} live; fresh frame verified"
        return CameraRuntimeStatus(
            profile=self.profile,
            running=self.profile.source_type == "mock" or is_real_camera_evidence,
            selected_camera=selected_device,
            requested_width=self.profile.width,
            requested_height=self.profile.height,
            requested_fps=self.profile.fps,
            requested_pixel_format=self.profile.pixel_format,
            actual_width=actual_width,
            actual_height=actual_height,
            actual_fps=float(measured_fps),
            actual_fps_measured=float(measured_fps),
            actual_pixel_format=self.profile.pixel_format,
            backend_api=selected_backend,
            warmup_ms=0.0 if self.profile.source_type == "mock" else self.last_warmup_ms,
            dropped_frames=0,
            last_probe_result=self.last_probe_result,
            recommendation_score=self._recommendation_score(),
            last_apply_ok=self.last_error is None,
            last_error=self.last_error,
            warnings=self.last_warnings,
            selected_device=selected_device,
            selected_backend=selected_backend,
            source_mode=source_mode,
            input_format="mjpeg" if self.profile.pixel_format in {"auto", "MJPG"} else self.profile.pixel_format.lower(),
            resolution=f"{actual_width}x{actual_height}",
            last_frame_age_ms=last_frame_age_ms,
            last_capture_error=self.last_capture_error,
            frame_mean_luma=self.last_frame_mean_luma,
            frame_brightness_state=self._frame_brightness_state(),
            frame_sequence=self.frame_sequence,
            is_real_camera_evidence=is_real_camera_evidence,
            is_external_usb_camera=bool(decision["is_external_usb_camera"]),
            is_laptop_camera=bool(decision["is_laptop_camera"]),
            hardware_presence_note=hardware_presence_note,
            updated_at=self.updated_at,
        )

    def apply(self, profile: CameraRuntimeProfile) -> CameraRuntimeApplyResult:
        self._event("camera.profile_apply_started", profile.model_dump(mode="json"), "Camera runtime profile apply started")
        # Applying the same healthy profile is a very common part of the
        # landing -> cockpit hand-off.  Releasing a live DirectShow/UVC handle
        # and opening it again here adds up to the worker warm-up timeout
        # (currently as high as eight seconds on Windows) and creates a race
        # with the Gateway camera-fresh gate.  Reuse the existing owner when
        # the identity and capture settings are unchanged and a real frame is
        # already fresh.  A stale, stopped, or changed profile still follows
        # the full release/probe/start path below.
        current = self.status()
        if self._can_reuse_healthy_profile(profile, current):
            self.capture_paused = False
            self.last_error = None
            self.last_capture_error = None
            self.last_warnings = []
            self.updated_at = time.time()
            result = CameraRuntimeApplyResult(
                accepted=True,
                applied=False,
                rollback_performed=False,
                profile=self.profile,
                actual_width=current.actual_width,
                actual_height=current.actual_height,
                actual_fps=current.actual_fps,
                actual_fps_measured=current.actual_fps_measured,
                actual_pixel_format=current.actual_pixel_format,
                backend_api=current.backend_api,
                warmup_ms=0.0,
                dropped_frames=current.dropped_frames,
                last_probe_result=current.last_probe_result,
                warnings=["CAMERA_PROFILE_REUSED_HEALTHY"],
            )
            self._event("camera.profile_reused", result.model_dump(mode="json"), "Healthy camera profile reused")
            self._event("camera.profile_apply_completed", result.model_dump(mode="json"), "Camera runtime profile apply completed")
            return result
        previous = self.profile
        warnings: list[str] = []
        suggested_action = None
        accepted = True
        rollback = False

        # Release the previous profile before probing the new one. On Windows
        # DirectShow commonly rejects a second handle, which made a perfectly
        # good camera appear unavailable whenever the preview was already on.
        self._release_capture()
        self._clear_frame_cache()
        if profile.source_type != "mock":
            device_id = profile.device_id
            if not device_id and profile.device_path:
                device_id = f"camera_{profile.device_path.strip('/').replace('/', '_').replace('.', '_')}"
            if os.name == "nt":
                # Re-enumerate once while no worker owns a DirectShow handle.
                # This is the only point at which a hot-plugged camera may
                # receive a new ordinal; the selected PnP identity remains the
                # source of truth.
                self.devices.scan()
                resolved = self.devices.resolve_camera_identity(
                    device_id=device_id,
                    device_path=profile.device_path,
                    stable_path=profile.stable_path,
                )
                if profile.stable_path:
                    if resolved is None:
                        accepted = False
                        rollback = True
                        warnings.append("Selected camera identity is not present.")
                        suggested_action = "Refresh cameras and select the listed physical camera."
                    else:
                        profile = profile.model_copy(update={
                            "device_id": resolved.device_id,
                            "device_path": resolved.device_path,
                            "stable_path": resolved.stable_path,
                        })
                elif resolved is not None:
                    # Fill in the durable PnP identity on first selection so
                    # the next reconnect can follow the physical camera even
                    # if its DirectShow index changes.
                    profile = profile.model_copy(update={
                        "device_id": resolved.device_id,
                        "device_path": resolved.device_path,
                        "stable_path": resolved.stable_path,
                    })
                device = resolved or next((item for item in self.devices.inventory().cameras if item.device_id == (device_id or "")), None)
                if device is None or not device.connected:
                    accepted = False
                    rollback = True
                    warnings.append("Camera device not found.")
                    suggested_action = "Refresh devices and select a listed camera."
                else:
                    profile = profile.model_copy(
                        update={
                            "device_id": device.device_id,
                            "device_path": device.device_path,
                            "stable_path": device.stable_path,
                        }
                    )
            else:
                probe = self.devices.probe_camera(device_id or "")
                if not probe.accepted:
                    accepted = False
                    rollback = True
                    warnings.extend(probe.warnings or ["Camera probe failed."])
                    suggested_action = probe.suggested_action or "Use mock camera or verify device permissions."
                elif probe.device is not None:
                    profile = profile.model_copy(update={"device_path": probe.device.device_path, "stable_path": probe.device.stable_path})
        if accepted:
            self.capture_paused = False
            self.profile = profile
            self.last_error = None
            self.last_warnings = warnings
            self._persist()
        else:
            self.profile = previous
            self.last_error = "camera_profile_apply_failed"
            self.last_warnings = warnings
            self._event("camera.profile_rollback", {"warnings": warnings, "profile": previous.model_dump(mode="json")}, "Camera runtime profile rolled back", LogLevel.WARN)
            self.capture_paused = False
            self.start_preview()
        self.updated_at = time.time()
        result = CameraRuntimeApplyResult(
            accepted=accepted,
            applied=accepted,
            rollback_performed=rollback,
            profile=self.profile,
            actual_width=self.profile.width,
            actual_height=self.profile.height,
            actual_fps=float(self.profile.fps),
            actual_fps_measured=float(self.profile.fps),
            actual_pixel_format=self.profile.pixel_format,
            backend_api="mock" if self.profile.source_type == "mock" else "opencv",
            warmup_ms=0.0 if self.profile.source_type == "mock" else 120.0,
            dropped_frames=0,
            last_probe_result=self.last_probe_result,
            warnings=warnings,
            suggested_action=suggested_action,
        )
        self._event("camera.profile_apply_completed", result.model_dump(mode="json"), "Camera runtime profile apply completed", LogLevel.INFO if accepted else LogLevel.WARN)
        return result

    def _can_reuse_healthy_profile(self, requested: CameraRuntimeProfile, current: CameraRuntimeStatus) -> bool:
        """Return true only when reapplying cannot change the live capture.

        The comparison intentionally includes all capture controls and stream
        dimensions.  A camera identity mismatch, a resolution/FPS change, or
        a stale frame must therefore take the normal restart path.
        """
        if requested.source_type == "mock":
            return False
        if requested.source_type not in {"usb", "laptop"} or self.profile.source_type != requested.source_type:
            return False
        if not current.running or not current.is_real_camera_evidence:
            return False
        if current.last_frame_age_ms is None or current.last_frame_age_ms > 1200:
            return False
        # A stream can keep producing occasional frames while the DirectShow
        # handle is degraded after USB re-enumeration. Freshness alone would
        # call that 1 FPS stream healthy and make Apply a no-op. Reopen when
        # measured throughput falls well below the requested capture rate.
        measured_fps = float(current.actual_fps_measured or current.actual_fps or 0.0)
        minimum_healthy_fps = max(3.0, float(requested.fps) * 0.45)
        if measured_fps < minimum_healthy_fps:
            return False

        def identity(profile: CameraRuntimeProfile) -> str:
            # Stable PnP identity survives DirectShow ordinal changes.  Fall
            # back to the portable device/path identifiers for Linux/V4L2 and
            # older saved profiles without a stable path.
            return (
                profile.stable_path
                or profile.device_id
                or profile.device_path
                or ""
            ).replace("\\", "/").strip().lower()

        if identity(requested) != identity(self.profile):
            return False
        comparable = (
            "width", "height", "fps", "pixel_format", "exposure_auto",
            "exposure_value", "gain", "focus_auto", "focus_value",
            "white_balance_auto", "white_balance_value", "brightness",
            "contrast", "saturation", "sharpness", "flip_horizontal",
            "flip_vertical", "rotate_deg", "lens_profile", "stream_width",
            "stream_height", "inference_width", "inference_height", "roi",
        )
        return all(getattr(requested, field) == getattr(self.profile, field) for field in comparable)

    def apply_controls(self, controls: CameraRuntimeControlsUpdate) -> CameraRuntimeStatus:
        update = controls.model_dump(exclude_none=True)
        self.profile = self.profile.model_copy(update=update)
        if self.profile.source_type != "mock":
            # Controls are constructor-time settings for the isolated Windows
            # worker. Restart it when exposure/brightness changes; otherwise
            # Setup would report "applied" while the old child kept streaming
            # with the previous camera controls.
            self._release_capture()
            self._clear_frame_cache()
            self.capture_paused = False
            decision = self._camera_source_decision()
            path = decision["capture_device"]
            if isinstance(path, str) and self._is_persistent_capture_path(path):
                self._ensure_capture_worker(path)
            elif isinstance(path, str):
                self._apply_v4l2_controls(path)
        self.updated_at = time.time()
        self._persist()
        self._event("camera.controls_updated", {"controls": update, "profile": self.profile.model_dump(mode="json")}, "Camera runtime controls updated")
        return self.status()

    def reset_defaults(self) -> CameraRuntimeApplyResult:
        return self.apply(CameraRuntimeProfile())

    def probe_current(self) -> CameraRuntimeApplyResult:
        started = time.time()
        warnings = ["Current profile probe is metadata-only for mock/replay sources."] if self.profile.source_type in {"mock", "replay"} else []
        self.last_probe_result = {
            "requested_width": self.profile.width,
            "requested_height": self.profile.height,
            "requested_fps": self.profile.fps,
            "requested_pixel_format": self.profile.pixel_format,
            "actual_width": self.profile.width,
            "actual_height": self.profile.height,
            "actual_fps_measured": float(self.profile.fps),
            "actual_pixel_format": self.profile.pixel_format,
            "backend_api": "mock" if self.profile.source_type == "mock" else "opencv",
            "warmup_ms": round((time.time() - started) * 1000, 3),
            "dropped_frames": 0,
            "warnings": warnings,
            "no_physical_command_generated": True,
        }
        return CameraRuntimeApplyResult(
            accepted=True,
            applied=False,
            profile=self.profile,
            actual_width=self.profile.width,
            actual_height=self.profile.height,
            actual_fps=float(self.profile.fps),
            actual_fps_measured=float(self.profile.fps),
            actual_pixel_format=self.profile.pixel_format,
            backend_api="mock" if self.profile.source_type == "mock" else "opencv",
            warmup_ms=self.last_probe_result["warmup_ms"],
            dropped_frames=0,
            last_probe_result=self.last_probe_result,
            warnings=warnings,
        )

    def benchmark(self) -> dict:
        result = {
            "source_type": self.profile.source_type,
            "target_fps": self.profile.fps,
            "estimated_latency_ms": round(1000 / max(self.profile.fps, 1), 3),
            "no_physical_command_generated": True,
        }
        self._event("camera.benchmark_completed", result, "Camera runtime benchmark completed")
        return result

    def snapshot(self) -> dict:
        return {
            "accepted": True,
            "source_type": self.profile.source_type,
            "width": self.profile.width,
            "height": self.profile.height,
            "no_physical_command_generated": True,
        }

    def release(self) -> dict:
        self.capture_paused = True
        self._release_capture()
        self._clear_frame_cache()
        self.last_capture_backend = "released"
        self.last_capture_error = None
        self.last_warnings = ["camera runtime capture released by operator request"]
        self.updated_at = time.time()
        self._event("camera.runtime_released", {"profile": self.profile.model_dump(mode="json")}, "Camera runtime capture released")
        return {
            "ok": True,
            "released": True,
            "message": "Kamera runtime yakalaması bırakıldı.",
            "no_physical_command_generated": True,
        }

    def start_preview(self) -> CameraRuntimeStatus:
        """Start the profile-owned capture without blocking the API on USB warmup."""
        warmup_started = time.perf_counter()
        self.capture_paused = False
        # A previous worker can have exited because of a transient USB/DirectShow
        # stall.  Do not let that historical error short-circuit the new warmup
        # loop: start-preview must always make a fresh attempt and report the
        # result of that attempt, not the previous session.
        self.last_error = None
        self.last_capture_error = None
        self.last_warnings = []
        self.last_capture_backend = "fallback"
        if self.profile.source_type == "mock":
            self.read_frame()
            self.last_warmup_ms = round((time.perf_counter() - warmup_started) * 1000, 3)
            return self.status()
        decision = self._camera_source_decision()
        path = decision["capture_device"]
        if path is None:
            self.last_error = "camera_device_unavailable"
            self.last_capture_backend = "fallback"
            self.last_capture_error = str(decision["hardware_presence_note"])
            self.last_warnings = [self.last_capture_error]
            self.last_warmup_ms = round((time.perf_counter() - warmup_started) * 1000, 3)
            self.updated_at = time.time()
            return self.status()
        if self._is_persistent_capture_path(path):
            self._ensure_capture_worker(path)
            # Return a truthful status to Setup instead of reporting
            # ``running=false`` during the first few milliseconds while the
            # isolated Windows worker warms the UVC device. This prevents the
            # UI from leaving a black/stale placeholder even though the next
            # frame is already on its way.
            self._wait_for_preview_frame(8.0)
            # A just-released DirectShow handle can fail its first open while
            # the driver is still unwinding. Retry the exact requested mode
            # once before considering a lower-resolution fallback. This keeps
            # release -> start-preview deterministic for the operator.
            if self.last_error in {"camera_capture_process_stopped", "camera_capture_stalled"}:
                self._stop_capture_worker()
                self._clear_frame_cache()
                self.last_error = None
                self.last_capture_error = None
                self.last_warnings = []
                self._ensure_capture_worker(path)
                self._wait_for_preview_frame(8.0)
            # A DirectShow device may advertise a high mode during a probe but
            # fail to sustain that mode in the long-lived worker (common with
            # UVC MJPEG modes above 1280 px). Try the next validated fallback
            # only after the requested native mode actually fails. This keeps
            # the best available quality as the first choice without leaving
            # the operator with a black preview or asking them to edit code.
            if self.last_error in {"camera_capture_process_stopped", "camera_capture_stalled"} and os.name == "nt":
                self._try_windows_capture_fallback(path)
        else:
            self.read_frame()
        self.last_warmup_ms = round((time.perf_counter() - warmup_started) * 1000, 3)
        self.updated_at = time.time()
        return self.status()

    def _wait_for_preview_frame(self, timeout_s: float) -> None:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            with self.frame_lock:
                fresh = self.last_frame_at is not None and (time.time() - self.last_frame_at) <= 0.75
            if fresh or self.last_error:
                return
            time.sleep(0.02)

    def _try_windows_capture_fallback(self, path: str) -> None:
        current = (int(self.profile.width), int(self.profile.height))
        candidates = [
            (1280, 720),
            (1280, 1024),
            (1920, 1080),
            (960, 540),
            (640, 480),
            (640, 360),
        ]
        candidates = [mode for mode in candidates if mode != current and mode[0] * mode[1] < current[0] * current[1]]
        # Keep the 16:9 1280x720 UVC mode ahead of driver-reported portrait/
        # interpolated modes such as 1280x1024; it is the highest mode already
        # proven stable on this turret camera and preserves the detector FOV.
        is_turret_cam = "VID_32E4" in str(self.profile.stable_path or "").upper() or "32E4" in str(self.profile.device_path or "").upper()
        for width, height in candidates:
            if is_turret_cam and (width < 1280 or height < 720):
                continue
            previous = current
            self._stop_capture_worker()
            self._clear_frame_cache()
            self.profile = self.profile.model_copy(update={
                "width": width,
                "height": height,
                "stream_width": width,
                "stream_height": height,
                "inference_width": width,
                "inference_height": height,
            })
            self.last_error = None
            self.last_capture_error = None
            self.last_warnings = [f"camera_mode_fallback:{previous[0]}x{previous[1]}->{width}x{height}"]
            self._ensure_capture_worker(path)
            deadline = time.monotonic() + 4.0
            while time.monotonic() < deadline:
                with self.frame_lock:
                    fresh = self.last_frame_at is not None and (time.time() - self.last_frame_at) <= 0.75
                if fresh:
                    self._persist()
                    return
                if self.last_error:
                    break
                time.sleep(0.02)
            # A mode can fail without producing a process-exit error (some
            # DirectShow drivers simply block the first read). In that case
            # continue with the next lower advertised mode instead of
            # returning a false "fallback" status with no frame.
            if self.last_error not in {None, "camera_capture_process_stopped", "camera_capture_stalled"}:
                break

    def read_frame(self):
        if self.capture_paused:
            self.last_capture_backend = "released"
            self.last_capture_error = "camera_runtime_capture_released"
            return None, ["camera_runtime_capture_released"]
        if self.profile.source_type == "mock":
            if cv2 is None or np is None:
                return None, ["mock_frame_numpy_unavailable"]
            frame = np.zeros((self.profile.height, self.profile.width, 3), dtype=np.uint8)
            cx = int(self.profile.width * 0.58)
            cy = int(self.profile.height * 0.42)
            radius = max(12, int(min(self.profile.width, self.profile.height) * 0.08))
            cv2.circle(frame, (cx, cy), radius, (0, 0, 255), -1)
            cv2.putText(frame, "MOCK SURROGATE FRAME", (16, 32), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
            self.last_capture_backend = "fallback"
            self.last_capture_error = None
            return frame, ["mock camera frame used for surrogate"]
        if cv2 is None:
            return None, ["opencv_not_available"]
        decision = self._camera_source_decision()
        path = decision["capture_device"]
        if path is None:
            self.last_capture_backend = "fallback"
            self.last_capture_error = str(decision["hardware_presence_note"])
            return None, ["camera_device_not_selected", str(decision["hardware_presence_note"])]
        # The live MJPEG endpoint calls this at the requested stream rate.
        # Do not reuse a one-second cached frame here: doing so created a
        # 1 FPS-looking preview while repeatedly encoding that same image.
        # Cached frames remain available only as a failure fallback below.
        frame, opencv_warnings = self._read_frame_opencv_persistent(path)
        if frame is not None:
            self.last_capture_backend = "opencv"
            self.last_capture_error = None
            return frame, opencv_warnings
        frame, ffmpeg_warnings = self._read_frame_ffmpeg(path)
        if frame is not None:
            self.last_error = None
            self.last_capture_backend = "ffmpeg"
            self.last_capture_error = None
            self._set_last_frame(frame, ffmpeg_warnings)
            return frame, ffmpeg_warnings
        if isinstance(path, str) and path.startswith("/dev/"):
            if self.last_frame is not None:
                cached_frame, cached_warnings = self._cached_frame(max_age_s=None)
                if cached_frame is not None:
                    return cached_frame, [*(opencv_warnings or []), *(ffmpeg_warnings or []), *cached_warnings]
            self.last_error = "camera_frame_read_failed"
            self.last_capture_backend = "fallback"
            self.last_capture_error = "; ".join([*(opencv_warnings or []), *(ffmpeg_warnings or []), f"camera_frame_read_failed:{path}"])
            return None, [*(opencv_warnings or []), *(ffmpeg_warnings or []), f"camera_frame_read_failed:{path}"]
        capture = cv2.VideoCapture(path if isinstance(path, str) and path.startswith("/dev/") else path)
        if not capture.isOpened():
            self.last_error = "camera_open_failed"
            self.last_capture_backend = "fallback"
            self.last_capture_error = f"camera_open_failed:{path}"
            return None, [f"camera_open_failed:{path}"]
        try:
            capture.set(cv2.CAP_PROP_FRAME_WIDTH, self.profile.width)
            capture.set(cv2.CAP_PROP_FRAME_HEIGHT, self.profile.height)
            capture.set(cv2.CAP_PROP_FPS, self.profile.fps)
            ok, frame = capture.read()
            if not ok or frame is None:
                self.last_error = "camera_frame_read_failed"
                self.last_capture_backend = "fallback"
                self.last_capture_error = "camera_frame_read_failed"
                return None, ["camera_frame_read_failed"]
            self.last_error = None
            self.last_capture_backend = "opencv"
            self.last_capture_error = None
            self._set_last_frame(frame, [])
            return frame, []
        finally:
            capture.release()

    def evidence_frame_copy(self):
        """Return the latest camera frame for a non-control evidence writer.

        The copy is intentionally read-only and does not open a device, alter
        capture settings, or wait for a new frame.
        """
        with self.frame_lock:
            if self.last_frame is None:
                return None, None
            return self.last_frame.copy(), self.last_frame_at

    def live_preview_frame(self):
        """Return the latest frame from one profile-owned capture worker.

        Browser previews, inference polling and evidence readers must not each
        open/read the same UVC device independently. The worker is the sole
        continuous camera reader; consumers receive copies of its latest
        frame without contending for the V4L2 handle.
        """
        if self.profile.source_type == "mock":
            return self.read_frame()
        decision = self._camera_source_decision()
        path = decision["capture_device"]
        if self._is_persistent_capture_path(path):
            self._ensure_capture_worker(path)
            deadline = time.monotonic() + 0.6
            while time.monotonic() < deadline:
                # The capture worker publishes immutable frame objects.  Do
                # not copy the full native-resolution BGR buffer for every
                # YOLO/UI reader; evidence_frame_copy() remains the explicit
                # ownership boundary for writers.
                frame, warnings = self._cached_frame(max_age_s=0.5, copy_frame=False)
                if frame is not None:
                    return frame, warnings
                time.sleep(0.01)
            if os.name == "nt" and isinstance(path, str) and path.startswith("camera-index:"):
                # Never fall through to a backend-owned DirectShow open. The
                # isolated child is the only process allowed to touch the UVC
                # driver; callers can retry after warmup without risking a
                # second owner or a native hot-unplug crash.
                return None, ["windows_camera_worker_warming"]
        return self.read_frame()

    def live_preview_sample(self):
        """Return one immutable frame together with its publication metadata.

        ``live_preview_frame()`` historically returned the pixel buffer while
        callers read ``frame_sequence`` and ``last_frame_at`` separately.  A
        30 FPS capture worker can publish between those reads, incorrectly
        labelling an older YOLO input as a newer camera frame.  Resolve the
        identity under ``frame_lock`` and retry if publication raced us.

        The returned frame remains the zero-copy immutable object owned by the
        capture worker; this method does not add a native-resolution copy.
        """
        last_frame = None
        last_warnings: list[str] = []
        for _attempt in range(3):
            frame, warnings = self.live_preview_frame()
            last_frame, last_warnings = frame, warnings
            if frame is None:
                return None, warnings, None, None
            with self.frame_lock:
                if frame is self.last_frame:
                    return frame, warnings, int(self.frame_sequence), self.last_frame_at
            # A new immutable frame was published between the pixel and
            # metadata reads. Yield once, then acquire a coherent sample.
            time.sleep(0)
        return last_frame, [*last_warnings, "camera_sample_metadata_race"], None, None

    def _read_frame_opencv_persistent(self, path: str, *, publish: bool = True):
        if cv2 is None or not self._is_persistent_capture_path(path):
            return None, []
        key = (path, self.profile.width, self.profile.height, self.profile.fps, self.profile.pixel_format)
        with self.capture_lock:
            if self.capture is None or self.capture_key != key:
                self._release_capture_locked()
                self._configure_v4l2_device(path)
                source, backend = self._opencv_capture_source(path)
                capture = cv2.VideoCapture(source, backend)
                # Match the isolated Windows worker.  DirectShow chooses a
                # slow high-resolution mode if FOURCC is set before the
                # requested dimensions/FPS; negotiate the mode first and
                # select the compressed pixel format afterward.
                capture.set(cv2.CAP_PROP_FRAME_WIDTH, self.profile.width)
                capture.set(cv2.CAP_PROP_FRAME_HEIGHT, self.profile.height)
                capture.set(cv2.CAP_PROP_FPS, self.profile.fps)
                if self.profile.pixel_format == "MJPG":
                    capture.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
                elif self.profile.pixel_format == "YUYV":
                    capture.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"YUYV"))
                capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                if not capture.isOpened():
                    capture.release()
                    self.last_error = "camera_open_failed"
                    self.last_capture_backend = "fallback"
                    self.last_capture_error = f"opencv_persistent_open_failed:{path}"
                    return None, [f"opencv_persistent_open_failed:{path}"]
                self.capture = capture
                self.capture_key = key
            ok, frame = self.capture.read()
            if not ok or frame is None:
                self._release_capture_locked()
                self.last_error = "camera_frame_read_failed"
                self.last_capture_backend = "fallback"
                self.last_capture_error = "opencv_persistent_frame_read_failed"
                return None, ["opencv_persistent_frame_read_failed"]
            self.last_error = None
            self.last_capture_backend = "opencv"
            self.last_capture_error = None
            # The normal one-shot/read_frame path needs to publish here.  The
            # long-lived capture worker publishes exactly once after this
            # method returns so that a single camera frame cannot increment
            # the sequence/timestamp counters twice (and cannot incur a
            # second 1080p ownership operation).
            if publish:
                self._set_last_frame(frame, ["opencv persistent capture used for real camera"])
            return frame, ["opencv persistent capture used for real camera"]

    def _release_capture(self) -> None:
        self._stop_capture_worker()
        with self.capture_lock:
            self._release_capture_locked()

    def _release_capture_locked(self) -> None:
        if self.capture is not None:
            try:
                self.capture.release()
            except Exception:
                pass
        self.capture = None
        self.capture_key = None

    def _ensure_capture_worker(self, path: str) -> None:
        key = (path, self.profile.width, self.profile.height, self.profile.fps, self.profile.pixel_format)
        if self.capture_worker_key == key and self._capture_worker_running():
            return
        self._stop_capture_worker()
        self.devices.set_active_camera(path, self.profile.stable_path)
        self.devices.remember_camera_mapping(self.profile.stable_path, path)
        self.capture_worker_stop.clear()
        self.capture_worker_key = key
        # DirectShow always lives outside the backend on Windows.  The raw
        # named-memory bridge sustains 1080p30 on the field host while keeping
        # a vendor UVC crash or hot-unplug from taking down FastAPI, Pico
        # heartbeat and the operator UI together.
        isolated_windows_camera = (
            os.name == "nt"
            and path.startswith("camera-index:")
        )
        if isolated_windows_camera:
            self._create_shared_camera_transport()
        else:
            self._close_shared_camera_transport()
        self.capture_worker_thread = threading.Thread(
            target=self._windows_isolated_capture_worker if isolated_windows_camera else self._capture_worker,
            args=(path, key),
            name="camera-runtime-isolated-bridge" if isolated_windows_camera else "camera-runtime-capture",
            daemon=True,
        )
        self.capture_worker_thread.start()

    def _capture_worker_running(self) -> bool:
        return self.capture_worker_thread is not None and self.capture_worker_thread.is_alive()

    def _stop_capture_worker(self) -> None:
        worker = self.capture_worker_thread
        if worker is None:
            self._close_shared_camera_transport()
            return
        self.capture_worker_stop.set()
        process = self.capture_worker_process
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                process.kill()
        if worker.is_alive() and worker is not threading.current_thread():
            worker.join(timeout=1.0)
        self._cleanup_orphaned_windows_workers()
        self.capture_worker_process = None
        self.capture_worker_thread = None
        self.capture_worker_key = None
        self.capture_worker_stop.clear()
        self.devices.clear_active_camera()
        self._close_shared_camera_transport()

    def _create_shared_camera_transport(self) -> None:
        """Create a per-runtime Windows named mapping for latest JPEG bytes."""
        self._close_shared_camera_transport()
        if os.name != "nt":
            return
        # Keep the tag short and unique.  Windows removes the mapping once all
        # handles close, so no global cleanup registry is necessary.
        tag = f"ISTIKLAL_CAMERA_{os.getpid()}_{int(time.time() * 1000)}"
        try:
            mapping = mmap.mmap(-1, self.capture_shared_size, tagname=tag)
            mapping[:16] = b"\x00" * 16
        except (OSError, ValueError):
            self.capture_shared_memory = None
            self.capture_shared_tag = None
            return
        self.capture_shared_memory = mapping
        self.capture_shared_tag = tag
        self.capture_shared_format = "raw"
        self.capture_shared_last_sequence = 0

    def _close_shared_camera_transport(self) -> None:
        mapping = self.capture_shared_memory
        self.capture_shared_memory = None
        self.capture_shared_tag = None
        self.capture_shared_format = None
        self.capture_shared_last_sequence = 0
        if mapping is not None:
            try:
                mapping.close()
            except (BufferError, OSError, ValueError):
                pass

    def _read_shared_camera_frame(self):
        mapping = self.capture_shared_memory
        if mapping is None or np is None:
            return None
        try:
            sequence_before = struct.unpack_from("<Q", mapping, 0)[0]
            if sequence_before == 0 or sequence_before & 1:
                return None
            # The bridge is polled faster than many cameras publish.  Without
            # this guard the same physical frame was copied and published
            # repeatedly, inflating camera FPS and making YOLO re-infer stale
            # pixels.  A camera sequence is consumed exactly once.
            if sequence_before == self.capture_shared_last_sequence:
                return None
            payload_length = struct.unpack_from("<I", mapping, 8)[0]
            if payload_length <= 0 or payload_length > self.capture_shared_size - 16:
                return None
            if self.capture_shared_format == "raw":
                width, height = struct.unpack_from("<HH", mapping, 12)
                expected = int(width) * int(height) * 3
                if width <= 0 or height <= 0 or payload_length != expected:
                    return None
                # One native memcpy is much faster than materialising a Python
                # ``bytes`` slice and then wrapping it.  The copy also gives
                # consumers an immutable frame that the child process cannot
                # overwrite during YOLO or JPEG encoding.
                frame = np.frombuffer(
                    mapping,
                    dtype=np.uint8,
                    count=payload_length,
                    offset=16,
                ).copy().reshape((int(height), int(width), 3))
                sequence_after = struct.unpack_from("<Q", mapping, 0)[0]
                if sequence_before != sequence_after or sequence_after & 1:
                    return None
                self.capture_shared_last_sequence = sequence_after
                return frame
            if cv2 is None:
                return None
            encoded = bytes(mapping[16 : 16 + payload_length])
            sequence_after = struct.unpack_from("<Q", mapping, 0)[0]
            if sequence_before != sequence_after or sequence_after & 1:
                return None
            frame = cv2.imdecode(np.frombuffer(encoded, dtype=np.uint8), cv2.IMREAD_COLOR)
            if frame is not None:
                self.capture_shared_last_sequence = sequence_after
            return frame
        except (BufferError, OSError, ValueError, struct.error):
            return None

    def _publish_windows_camera_frame(self, frame) -> None:
        """Publish one child frame and surface negotiated dimensions."""
        if frame is None:
            return
        self.last_error = None
        self.last_capture_backend = "opencv"
        self.last_capture_error = None
        requested_size = (int(self.profile.width), int(self.profile.height))
        actual_size = (int(frame.shape[1]), int(frame.shape[0]))
        if actual_size != requested_size:
            self.profile = self.profile.model_copy(update={
                "width": actual_size[0],
                "height": actual_size[1],
                "stream_width": actual_size[0],
                "stream_height": actual_size[1],
                "inference_width": actual_size[0],
                "inference_height": actual_size[1],
            })
            self.last_warnings = [
                f"camera_driver_negotiated:{requested_size[0]}x{requested_size[1]}->{actual_size[0]}x{actual_size[1]}"
            ]
            self._persist()
        else:
            self.last_warnings = []
        self._set_last_frame(frame, ["isolated Windows camera capture"])

    def _capture_worker(self, path: str, key: tuple) -> None:
        target_interval = 1.0 / max(float(self.profile.fps or 30), 1.0)
        while not self.capture_worker_stop.is_set() and self.capture_worker_key == key:
            started = time.perf_counter()
            # Publication is owned by this loop.  _read_frame_opencv_persistent
            # is also used by read_frame(), where it publishes directly; the
            # worker must opt out or one physical frame is counted twice.
            frame, warnings = self._read_frame_opencv_persistent(path, publish=False)
            if frame is not None:
                self._set_last_frame(frame, warnings)
            wait_s = max(0.001, target_interval - (time.perf_counter() - started))
            self.capture_worker_stop.wait(wait_s)

    def _cleanup_orphaned_windows_workers(self) -> None:
        if os.name != "nt":
            return
        try:
            import psutil
            current_pid = os.getpid()
            for p in psutil.process_iter(["pid", "name", "cmdline"]):
                try:
                    if p.info["pid"] != current_pid and p.info["name"] and "python" in p.info["name"].lower():
                        cmd = " ".join(p.info.get("cmdline") or [])
                        if "windows_camera_capture_worker.py" in cmd:
                            p.kill()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
        except Exception:
            pass

    def _windows_isolated_capture_worker(self, path: str, key: tuple) -> None:
        """Keep unstable Windows UVC/OpenCV code outside the backend process.

        Some DirectShow drivers terminate the process with 0xC0000005 when a
        live USB camera is removed. The child owns VideoCapture; the backend
        only decodes its atomic latest-frame JPEG, so Gateway/UI stay alive.
        """
        try:
            camera_index = int(path.split(":", 1)[1])
        except (IndexError, ValueError):
            self.last_error = "camera_index_invalid"
            self.last_capture_error = f"camera_index_invalid:{path}"
            return
        runtime_dir = project_root() / "config" / "runtime" / "windows_camera_worker"
        runtime_dir.mkdir(parents=True, exist_ok=True)
        frame_path = runtime_dir / "latest.jpg"
        frame_path.unlink(missing_ok=True)
        worker_script = Path(__file__).with_name("windows_camera_capture_worker.py")
        if not self._windows_profile_camera_present():
            self.last_error = "camera_device_removed"
            self.last_capture_backend = "fallback"
            self.last_capture_error = "windows_camera_pnp_identity_missing"
            self.last_warnings = [self.last_capture_error]
            self._clear_frame_cache()
            self.devices.clear_active_camera(path)
            return
        self._cleanup_orphaned_windows_workers()
        command = [
            sys.executable,
            str(worker_script),
            "--index", str(camera_index),
            "--width", str(int(self.profile.width)),
            "--height", str(int(self.profile.height)),
            "--fps", str(int(self.profile.fps)),
            "--pixel-format", str(self.profile.pixel_format),
            "--output", str(frame_path),
        ]
        if self.capture_shared_tag:
            command.extend([
                "--shared-memory-tag", self.capture_shared_tag,
                "--shared-memory-size", str(self.capture_shared_size),
                "--shared-memory-format", str(self.capture_shared_format or "raw"),
            ])
        if self.profile.exposure_auto is False:
            command.extend(["--exposure-auto", "0"])
        if self.profile.exposure_value is not None:
            command.extend(["--exposure", str(float(self.profile.exposure_value))])
        if self.profile.brightness is not None:
            command.extend(["--brightness", str(float(self.profile.brightness))])
        if self.profile.contrast is not None:
            command.extend(["--contrast", str(float(self.profile.contrast))])
        if self.profile.gain is not None:
            command.extend(["--gain", str(float(self.profile.gain))])
        process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.capture_worker_process = process
        last_mtime_ns = -1
        worker_started_at = time.monotonic()
        last_frame_received_at: float | None = None
        last_presence_check_at = 0.0
        presence_process: subprocess.Popen | None = None
        identity = self.profile.stable_path or ""
        identity_match = re.search(r"VID_[0-9A-Fa-f]{4}&PID_[0-9A-Fa-f]{4}", identity)
        presence_script = None
        if identity_match is not None:
            token = identity_match.group(0).upper()
            presence_script = (
                f"if (@(Get-PnpDevice -PresentOnly | Where-Object InstanceId -Like '*{token}*').Count -gt 0) "
                "{ '1' } else { '0' }"
            )
        while not self.capture_worker_stop.is_set() and self.capture_worker_key == key:
            return_code = process.poll()
            if return_code is not None:
                if self.capture_worker_stop.is_set() or self.capture_worker_key != key:
                    break
                self.last_error = "camera_capture_process_stopped"
                self.last_capture_backend = "fallback"
                self.last_capture_error = f"windows_camera_capture_process_exit:{return_code}"
                self.last_warnings = [self.last_capture_error]
                self._clear_frame_cache()
                time.sleep(0.5)
                try:
                    self._cleanup_orphaned_windows_workers()
                    process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    self.capture_worker_process = process
                    worker_started_at = time.monotonic()
                    last_frame_received_at = None
                    continue
                except Exception as exc:
                    self.last_capture_error = f"windows_camera_capture_restart_failed:{exc}"
                    break
            if self.capture_shared_memory is not None:
                frame = self._read_shared_camera_frame()
                if frame is not None:
                    self._publish_windows_camera_frame(frame)
                    last_frame_received_at = time.monotonic()
            else:
                try:
                    stat = frame_path.stat()
                    if stat.st_mtime_ns != last_mtime_ns and cv2 is not None:
                        # Compatibility fallback for Windows hosts that reject
                        # named mappings. The normal path above avoids the
                        # file-lock/atomic-replace contention of this bridge.
                        encoded = frame_path.read_bytes()
                        frame = None
                        if encoded and np is not None:
                            frame = cv2.imdecode(np.frombuffer(encoded, dtype=np.uint8), cv2.IMREAD_COLOR)
                        if frame is not None:
                            last_mtime_ns = stat.st_mtime_ns
                            self._publish_windows_camera_frame(frame)
                            last_frame_received_at = time.monotonic()
                except OSError:
                    pass
            now = time.monotonic()
            if presence_process is not None and presence_process.poll() is not None:
                stdout, _stderr = presence_process.communicate()
                camera_present = presence_process.returncode == 0 and stdout.strip().endswith("1")
                presence_process = None
                if not camera_present and (last_frame_received_at is None or now - last_frame_received_at > 2.0):
                    self.last_error = "camera_device_removed"
                    self.last_capture_backend = "fallback"
                    self.last_capture_error = "windows_camera_pnp_identity_missing"
                    self.last_warnings = [self.last_capture_error]
                    self._clear_frame_cache()
                    process.terminate()
                    try:
                        process.wait(timeout=0.75)
                    except subprocess.TimeoutExpired:
                        process.kill()
                    break
            if (
                presence_script is not None
                and presence_process is None
                and (last_frame_received_at is None or now - last_frame_received_at > 2.0)
                and now - last_presence_check_at >= 3.0
            ):
                last_presence_check_at = now
                # PnP enumeration can take 0.5-0.7 s on Windows. Run it
                # asynchronously so stable-identity checks never pause frame
                # ingestion long enough to create a false CAMERA_STALE gate.
                presence_process = subprocess.Popen(
                    ["powershell", "-NoProfile", "-Command", presence_script],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    text=True,
                )
            initial_timeout_s = 6.0
            live_stall_timeout_s = 1.5
            if (
                (last_frame_received_at is None and now - worker_started_at > initial_timeout_s)
                or (last_frame_received_at is not None and now - last_frame_received_at > live_stall_timeout_s)
            ):
                self.last_error = "camera_capture_stalled"
                self.last_capture_backend = "fallback"
                self.last_capture_error = "windows_camera_capture_stalled"
                self.last_warnings = [self.last_capture_error]
                self._clear_frame_cache()
                process.terminate()
                try:
                    process.wait(timeout=0.75)
                except subprocess.TimeoutExpired:
                    process.kill()
                break
            self.capture_worker_stop.wait(0.003 if self.capture_shared_memory is not None else 0.01)
        if presence_process is not None and presence_process.poll() is None:
            presence_process.terminate()
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                process.kill()
        if self.capture_worker_process is process:
            self.capture_worker_process = None
        self.devices.clear_active_camera(path)

    def _windows_profile_camera_present(self) -> bool:
        if os.name != "nt":
            return True
        identity = self.profile.stable_path or ""
        match = re.search(r"VID_[0-9A-Fa-f]{4}&PID_[0-9A-Fa-f]{4}", identity)
        if match is None:
            return True
        token = match.group(0).upper()
        script = (
            f"if (@(Get-PnpDevice -PresentOnly | Where-Object InstanceId -Like '*{token}*').Count -gt 0) "
            "{ '1' } else { '0' }"
        )
        try:
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command", script],
                capture_output=True,
                text=True,
                timeout=1.5,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        return result.returncode == 0 and result.stdout.strip().endswith("1")

    def _set_last_frame(self, frame, warnings: list[str]) -> None:
        now = time.time()
        with self.frame_lock:
            # cv2.read() and the shared-memory decoder hand us a newly-owned
            # array for each capture iteration.  Consumers treat this
            # published reference as immutable and copy only when they need
            # to draw/write evidence.  Avoiding a second 1920x1080 BGR copy
            # removes a major memory-bandwidth bottleneck from the capture
            # worker.  Marking it read-only catches accidental hot-path
            # mutation while retaining the zero-copy contract.
            try:
                frame.setflags(write=False)
            except (AttributeError, ValueError):
                pass
            self.last_frame = frame
            self.last_frame_at = now
            self.last_frame_warnings = warnings
            self.frame_sequence += 1
            self.frame_timestamps.append(now)
            if len(self.frame_timestamps) >= 2:
                elapsed = self.frame_timestamps[-1] - self.frame_timestamps[0]
                if elapsed > 0:
                    self.last_fps_measured = round((len(self.frame_timestamps) - 1) / elapsed, 2)
            try:
                # Brightness is a health indicator, not evidence.  A sparse
                # sample is sufficient and avoids scanning all 6.2 MiB of a
                # 1080p frame on every camera tick.
                sample = frame[::16, ::16] if getattr(frame, "ndim", 0) >= 2 else frame
                self.last_frame_mean_luma = round(float(np.mean(sample)), 2) if np is not None else None
            except Exception:
                self.last_frame_mean_luma = None
        # Do not take/clear the JPEG-cache lock at capture FPS. Every cache
        # key already contains ``frame_sequence`` and the LRU is bounded, so
        # old encodes can never be served as a new frame. Clearing this map
        # 30 times/s contended with each MJPEG/browser encoder; with several
        # old cockpit tabs on Windows it could starve frame.jpg for seconds
        # and also delay the Pico heartbeat thread.

    def _frame_brightness_state(self) -> str:
        if self.last_frame_mean_luma is None:
            return "NO_FRAME"
        if self.last_frame_mean_luma < 18:
            return "DARK"
        if self.last_frame_mean_luma > 245:
            return "OVEREXPOSED"
        return "OK"

    def _clear_frame_cache(self) -> None:
        with self.frame_lock:
            self.last_frame = None
            self.last_frame_at = None
            self.last_frame_mean_luma = None
            self.last_frame_warnings = []
            self.frame_timestamps.clear()
            self.last_fps_measured = 0.0
        with self._jpeg_cache_lock:
            entries = getattr(self, "_jpeg_cache_entries", None)
            if entries is not None:
                entries.clear()
            self._jpeg_cache_key = None
            self._jpeg_cache_bytes = None

    def encode_stream_jpeg(
        self,
        frame,
        *,
        quality: int = 95,
        cache_token: object | None = None,
        output_width: int | None = None,
        output_height: int | None = None,
    ) -> bytes | None:
        """Encode the current plain preview once and reuse it across readers.

        The browser preview can have several consumers (cockpit, setup and
        diagnostics).  Re-encoding the same 1080p frame in each HTTP request
        was unnecessary CPU work; the published frame sequence gives us a
        precise cache key without retaining stale image evidence.
        """
        if cv2 is None or frame is None:
            return None
        source_height, source_width = int(frame.shape[0]), int(frame.shape[1])
        # Resolve the final dimensions before resizing. A second client can
        # then hit the LRU without paying another native-frame resize just to
        # discover that the JPEG is already available.
        stream_width = int(self.profile.stream_width or source_width)
        stream_height = int(self.profile.stream_height or source_height)
        if stream_width > 0 and stream_height > 0:
            stream_scale = min(
                stream_width / max(source_width, 1),
                stream_height / max(source_height, 1),
                1.0,
            )
            stream_width = max(1, int(round(source_width * stream_scale)))
            stream_height = max(1, int(round(source_height * stream_scale)))
        else:
            stream_width, stream_height = source_width, source_height
        requested_width = int(output_width or 0)
        requested_height = int(output_height or 0)
        if requested_width > 0 or requested_height > 0:
            width_scale = requested_width / max(stream_width, 1) if requested_width > 0 else float("inf")
            height_scale = requested_height / max(stream_height, 1) if requested_height > 0 else float("inf")
            # Keep aspect ratio and never upscale the native capture.
            scale = min(width_scale, height_scale, 1.0)
            stream_width = max(1, int(round(stream_width * scale)))
            stream_height = max(1, int(round(stream_height * scale)))
        key = (
            cache_token if cache_token is not None else "plain",
            int(self.frame_sequence),
            source_width,
            source_height,
            stream_width,
            stream_height,
            int(quality),
        )
        with self._jpeg_cache_lock:
            entries = getattr(self, "_jpeg_cache_entries", None)
            if entries is None:
                entries = OrderedDict()
                self._jpeg_cache_entries = entries
            cached = entries.get(key)
            if cached is not None:
                entries.move_to_end(key)
                self._jpeg_cache_key = key
                self._jpeg_cache_bytes = cached
                return cached
        stream_frame = frame
        if (stream_width, stream_height) != (source_width, source_height):
            stream_frame = cv2.resize(frame, (stream_width, stream_height), interpolation=cv2.INTER_AREA)
        with self._jpeg_cache_lock:
            # Another consumer may have encoded while the resize happened
            # outside the lock. Prefer its bytes instead of encoding twice.
            entries = getattr(self, "_jpeg_cache_entries", None)
            if entries is None:
                entries = OrderedDict()
                self._jpeg_cache_entries = entries
            cached = entries.get(key)
            if cached is not None:
                entries.move_to_end(key)
                self._jpeg_cache_key = key
                self._jpeg_cache_bytes = cached
                return cached
            # Native/evidence callers retain the high-quality 95 path; the
            # UI preview explicitly clamps quality to 78 before reaching here.
            if int(quality) == 95:
                ok, encoded = cv2.imencode(".jpg", stream_frame, [int(cv2.IMWRITE_JPEG_QUALITY), 95])
            else:
                ok, encoded = cv2.imencode(".jpg", stream_frame, [int(cv2.IMWRITE_JPEG_QUALITY), int(quality)])
            if not ok:
                return None
            payload = encoded.tobytes()
            entries[key] = payload
            entries.move_to_end(key)
            while len(entries) > int(getattr(self, "_jpeg_cache_max_entries", 8) or 8):
                entries.popitem(last=False)
            self._jpeg_cache_key = key
            self._jpeg_cache_bytes = payload
            return payload

    def _cached_frame(self, max_age_s: float | None, *, copy_frame: bool = True) -> tuple[object | None, list[str]]:
        """Return the latest immutable frame reference.

        ``_set_last_frame`` publishes a new BGR array rather than mutating the
        previously published one.  That makes the old array safe for readers
        until their OpenCV/YOLO call completes, so the hot path can avoid a
        full 6 MiB (1080p) copy for every preview/inference consumer.  Evidence
        capture still asks for an explicit copy and the default remains copied
        for legacy callers.
        """
        with self.frame_lock:
            if self.last_frame is None:
                return None, []
            age_s = time.time() - (self.last_frame_at or time.time())
            if max_age_s is not None and age_s > max_age_s:
                return None, [f"cached_real_frame_stale:{int(age_s * 1000)}ms"]
            frame = self.last_frame.copy() if copy_frame else self.last_frame
            return frame, [*self.last_frame_warnings, f"using_cached_real_frame:{int(age_s * 1000)}ms"]

    def _configure_v4l2_device(self, path: str) -> None:
        if not isinstance(path, str) or not path.startswith("/dev/"):
            return
        pixel_format = "MJPG" if self.profile.pixel_format in {"auto", "MJPG"} else self.profile.pixel_format
        command = [
            "v4l2-ctl",
            f"--device={path}",
            f"--set-fmt-video=width={int(self.profile.width)},height={int(self.profile.height)},pixelformat={pixel_format}",
            f"--set-parm={int(self.profile.fps)}",
        ]
        try:
            subprocess.run(command, check=False, capture_output=True, text=True, timeout=1.5)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return
        self._apply_v4l2_controls(path)

    def _apply_v4l2_controls(self, path: str) -> None:
        controls: dict[str, int] = {}
        if self.profile.brightness is not None:
            controls["brightness"] = int(self.profile.brightness)
        if self.profile.contrast is not None:
            controls["contrast"] = int(self.profile.contrast)
        if self.profile.saturation is not None:
            controls["saturation"] = int(self.profile.saturation)
        if self.profile.sharpness is not None:
            controls["sharpness"] = int(self.profile.sharpness)
        if self.profile.gain is not None:
            controls["gain"] = int(self.profile.gain)
        if self.profile.white_balance_auto is not None:
            controls["white_balance_automatic"] = 1 if self.profile.white_balance_auto else 0
        if self.profile.white_balance_value is not None:
            controls["white_balance_temperature"] = int(self.profile.white_balance_value)
        if self.profile.exposure_auto is not None:
            controls["auto_exposure"] = 3 if self.profile.exposure_auto else 1
        if self.profile.exposure_value is not None:
            controls["exposure_time_absolute"] = int(self.profile.exposure_value)
        if not controls:
            return
        command = ["v4l2-ctl", f"--device={path}"]
        command.extend(f"--set-ctrl={name}={value}" for name, value in controls.items())
        try:
            subprocess.run(command, check=False, capture_output=True, text=True, timeout=1.5)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return

    def _read_frame_ffmpeg(self, path: str):
        if cv2 is None or not isinstance(path, str) or not path.startswith("/dev/"):
            return None, []
        with tempfile.NamedTemporaryFile(prefix="istiklal_camera_frame_", suffix=".jpg", delete=False) as output:
            output_path = Path(output.name)
        input_format = {
            "MJPG": "mjpeg",
            "YUYV": "yuyv422",
            "auto": "mjpeg",
        }.get(self.profile.pixel_format, "mjpeg")
        command = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "v4l2",
            "-input_format",
            input_format,
            "-video_size",
            f"{self.profile.width}x{self.profile.height}",
            "-i",
            path,
            "-frames:v",
            "1",
            "-update",
            "1",
            str(output_path),
        ]
        try:
            completed = subprocess.run(command, check=False, capture_output=True, text=True, timeout=3)
            if completed.returncode != 0:
                self.last_capture_backend = "fallback"
                self.last_capture_error = f"ffmpeg_frame_capture_failed:{completed.stderr.strip() or completed.returncode}"
                return None, [self.last_capture_error]
            frame = cv2.imread(str(output_path))
            if frame is None:
                self.last_capture_backend = "fallback"
                self.last_capture_error = "ffmpeg_frame_decode_failed"
                return None, ["ffmpeg_frame_decode_failed"]
            self.last_capture_backend = "ffmpeg"
            self.last_capture_error = None
            return frame, ["ffmpeg frame capture used for real camera"]
        except FileNotFoundError:
            self.last_capture_backend = "fallback"
            self.last_capture_error = "ffmpeg_not_available"
            return None, ["ffmpeg_not_available"]
        except subprocess.TimeoutExpired:
            self.last_capture_backend = "fallback"
            self.last_capture_error = "ffmpeg_frame_capture_timeout"
            return None, ["ffmpeg_frame_capture_timeout"]
        finally:
            try:
                output_path.unlink(missing_ok=True)
            except OSError:
                pass

    def mjpeg_stream(
        self,
        *,
        output_width: int | None = None,
        output_height: int | None = None,
        quality: int = 95,
    ):
        while True:
            frame, warnings = self.live_preview_frame()
            captured_at = getattr(self, "last_frame_at", None)
            sequence = int(getattr(self, "frame_sequence", 0) or 0)
            captured_at_ms = int(float(captured_at) * 1000) if captured_at is not None else 0
            age_ms = max(0, int(time.time() * 1000) - captured_at_ms) if captured_at_ms else -1
            part_header = (
                b"--frame\r\nContent-Type: image/jpeg\r\n"
                + f"X-Camera-Frame-Sequence: {sequence}\r\n".encode()
                + f"X-Camera-Captured-At-Ms: {captured_at_ms}\r\n".encode()
                + f"X-Camera-Frame-Age-Ms: {age_ms}\r\n\r\n".encode()
            )
            if frame is None or cv2 is None:
                placeholder = self._placeholder_frame("; ".join(warnings) or "camera frame unavailable")
                ok, encoded = cv2.imencode(".jpg", placeholder) if cv2 is not None else (False, None)
                if ok:
                    yield part_header + encoded.tobytes() + b"\r\n"
                else:
                    yield b"--frame\r\nContent-Type: text/plain\r\n\r\ncamera frame unavailable\r\n"
            else:
                encoded_bytes = self.encode_stream_jpeg(
                    frame,
                    quality=quality,
                    output_width=output_width,
                    output_height=output_height,
                    # frame.jpg and every plain MJPEG client share the same
                    # encode for a capture sequence and transport geometry.
                    # Overlay streams keep their separate token because they
                    # draw on a copied frame.
                    cache_token=("plain", output_width, output_height, quality),
                )
                if encoded_bytes is not None:
                    yield part_header + encoded_bytes + b"\r\n"
                else:
                    self.last_warnings = warnings + ["jpeg_encode_failed"]
                    yield b"--frame\r\nContent-Type: text/plain\r\n\r\njpeg encode failed\r\n"
            # The profile-owned worker captures continuously. This consumer
            # sleep paces encoding/network delivery without slowing capture or
            # monopolising the V4L2 lock when multiple browser panels exist.
            time.sleep(1 / max(self.profile.fps, 1))

    def _stream_frame(self, frame):
        if cv2 is None:
            return frame
        width = int(self.profile.stream_width or self.profile.width or frame.shape[1])
        height = int(self.profile.stream_height or self.profile.height or frame.shape[0])
        if width > 0 and height > 0 and (frame.shape[1] != width or frame.shape[0] != height):
            return cv2.resize(frame, (width, height), interpolation=cv2.INTER_AREA)
        return frame

    def _placeholder_frame(self, message: str):
        height = max(int(self.profile.stream_height or self.profile.height or 360), 240)
        width = max(int(self.profile.stream_width or self.profile.width or 640), 320)
        frame = np.zeros((height, width, 3), dtype=np.uint8)
        frame[:] = (18, 24, 32)
        source_mode = str(self._camera_source_decision()["source_mode"])
        title = "CAMERA FRAME UNAVAILABLE" if source_mode == "CAMERA_UNAVAILABLE" else source_mode.replace("_", " ")
        cv2.putText(frame, title[:44], (24, 44), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (80, 220, 255), 2)
        cv2.putText(frame, message[:86], (24, 86), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (230, 230, 230), 1)
        cv2.putText(frame, "no_physical_command_generated=true", (24, height - 32), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (120, 255, 160), 1)
        return frame

    def _camera_source_decision(self) -> dict[str, object]:
        if self.profile.source_type == "mock":
            return {
                "source_mode": "MOCK_OR_FIXTURE",
                "selected_device": "mock",
                "capture_device": None,
                "is_external_usb_camera": False,
                "is_laptop_camera": False,
                "hardware_presence_note": "MOCK/SURROGATE — NOT REAL CAMERA EVIDENCE",
            }
        configured = self.profile.device_path or self.profile.stable_path or self.profile.device_id
        resolved = self.devices.resolve_camera_identity(
            device_id=self.profile.device_id,
            device_path=self.profile.device_path,
            stable_path=self.profile.stable_path,
        )
        if resolved is not None:
            is_laptop = self._camera_device_is_laptop(resolved)
            return {
                "source_mode": "REAL_LAPTOP_CAMERA_LATEST_FRAME" if is_laptop else "REAL_USB_CAMERA_LATEST_FRAME",
                "selected_device": resolved.device_path,
                "capture_device": resolved.device_path,
                "is_external_usb_camera": not is_laptop,
                "is_laptop_camera": is_laptop,
                "hardware_presence_note": f"{resolved.name} present; live frame pending",
            }
        if isinstance(configured, str) and configured.startswith("camera-index:"):
            present = any(item.device_path == configured for item in self.devices.inventory().cameras)
            if present:
                is_laptop = self.profile.source_type == "laptop"
                return {
                    "source_mode": "REAL_LAPTOP_CAMERA_LATEST_FRAME" if is_laptop else "REAL_USB_CAMERA_LATEST_FRAME",
                    "selected_device": configured,
                    "capture_device": configured,
                    "is_external_usb_camera": not is_laptop,
                    "is_laptop_camera": is_laptop,
                    "hardware_presence_note": "Windows camera present; live frame pending",
                }
        if isinstance(configured, str) and configured.startswith("/dev/") and Path(configured).exists():
            is_laptop = self._is_laptop_camera(configured)
            return {
                "source_mode": "REAL_LAPTOP_CAMERA_LATEST_FRAME" if is_laptop else "REAL_USB_CAMERA_LATEST_FRAME",
                "selected_device": configured,
                "capture_device": configured,
                "is_external_usb_camera": not is_laptop,
                "is_laptop_camera": is_laptop,
                "hardware_presence_note": "camera present; live frame pending",
            }
        available = sorted(glob.glob("/dev/video*"))
        if available:
            laptop = next((path for path in available if self._is_laptop_camera(path)), available[0])
            missing_note = f"configured camera {configured or 'not_selected'} not present; using laptop camera for development"
            return {
                "source_mode": "REAL_LAPTOP_CAMERA_LATEST_FRAME",
                "selected_device": laptop,
                "capture_device": laptop,
                "is_external_usb_camera": False,
                "is_laptop_camera": True,
                "hardware_presence_note": missing_note,
            }
        return {
            "source_mode": "CAMERA_UNAVAILABLE",
            "selected_device": configured,
            "capture_device": None,
            "is_external_usb_camera": False,
            "is_laptop_camera": False,
            "hardware_presence_note": "CAMERA_UNAVAILABLE; external USB and laptop camera not present",
        }

    @staticmethod
    def _is_laptop_camera(path: str) -> bool:
        return path.endswith("/video0") or path.endswith("/video1")

    @staticmethod
    def _camera_device_is_laptop(device) -> bool:
        text = " ".join(str(value or "") for value in (device.name, device.description, device.device_path))
        return bool(re.search(r"integrated|internal|built.?in|front|user.?facing|uvc.?webcam|usb2\.0.*webcam|laptop", text, re.IGNORECASE))

    @staticmethod
    def _is_persistent_capture_path(path: object) -> bool:
        return isinstance(path, str) and (path.startswith("/dev/") or path.startswith("camera-index:"))

    @staticmethod
    def _opencv_capture_source(path: str) -> tuple[str | int, int]:
        if path.startswith("camera-index:"):
            return int(path.split(":", 1)[1]), cv2.CAP_DSHOW
        return path, cv2.CAP_V4L2

    def _persist(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(yaml.safe_dump(self.profile.model_dump(mode="json"), sort_keys=False), encoding="utf-8")

    def _load_persisted_profile(self) -> None:
        active_models_path = Path(self.config.models.active_models_file)
        if active_models_path.is_absolute() and not active_models_path.is_relative_to(project_root()):
            return
        if not self.path.exists():
            return
        try:
            loaded = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
            self.profile = CameraRuntimeProfile(**loaded)
            self.last_error = None
        except Exception as exc:
            self.last_error = f"camera_profile_load_failed:{exc}"
            self.last_warnings = [self.last_error]

    def _recommendation_score(self) -> int:
        if self.profile.source_type == "mock":
            return 50
        score = 40 if self.profile.source_type == "usb" else 25
        if self.profile.stable_path:
            score += 20
        if self.last_error is None:
            score += 20
        if self.last_probe_result:
            score += 10
        return min(score, 100)

    def _event(self, event_type: str, payload: dict, message: str, level: LogLevel = LogLevel.INFO) -> None:
        payload = {**payload, "no_physical_command_generated": True}
        self.last_event = (event_type, payload)
        self.logger.emit(level, "CAMERA_RUNTIME", message, payload)
