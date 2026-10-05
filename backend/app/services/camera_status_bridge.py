from typing import Any

from app.schemas.camera_runtime import CameraRuntimeProfile
from app.schemas.vision import CameraStatus


def camera_status_from_runtime(runtime: Any, runtime_status: Any | None = None) -> CameraStatus:
    """Build the legacy camera status from one already-published runtime status.

    The WebSocket publisher emits both legacy and runtime camera contracts.
    Accepting the runtime status here prevents it from resolving the selected
    Windows identity and rebuilding the same Pydantic object a second time on
    every client tick.
    """
    runtime_status = runtime_status or runtime.camera_runtime.status()
    profile = runtime_status.profile
    if profile.source_type == "mock":
        return CameraStatus(
            camera_mode="mock",
            source="mock",
            connected=True,
            running=True,
            stream_enabled=runtime.config.camera.stream_enabled,
            width=profile.stream_width,
            height=profile.stream_height,
            fps=profile.fps,
            last_error=runtime_status.last_error,
            selected_device=runtime_status.selected_device,
            selected_backend=runtime_status.selected_backend,
            source_mode=runtime_status.source_mode,
            input_format=runtime_status.input_format,
            resolution=runtime_status.resolution,
            last_frame_age_ms=runtime_status.last_frame_age_ms,
            last_capture_error=runtime_status.last_capture_error,
            is_real_camera_evidence=False,
            is_external_usb_camera=False,
            is_laptop_camera=False,
            hardware_presence_note=runtime_status.hardware_presence_note,
        )

    connected = _selected_camera_connected(runtime, profile)
    source = profile.device_path or profile.stable_path or profile.device_id
    return CameraStatus(
        camera_mode=profile.source_type,
        source=source,
        connected=connected,
        running=connected and runtime_status.running,
        stream_enabled=runtime.config.camera.stream_enabled,
        width=profile.stream_width,
        height=profile.stream_height,
        fps=profile.fps,
        last_error=runtime_status.last_capture_error or (None if connected else "selected_camera_not_in_inventory"),
        selected_device=runtime_status.selected_device,
        selected_backend=runtime_status.selected_backend,
        source_mode=runtime_status.source_mode,
        input_format=runtime_status.input_format,
        resolution=runtime_status.resolution,
        last_frame_age_ms=runtime_status.last_frame_age_ms,
        last_capture_error=runtime_status.last_capture_error,
        is_real_camera_evidence=runtime_status.is_real_camera_evidence,
        is_external_usb_camera=runtime_status.is_external_usb_camera,
        is_laptop_camera=runtime_status.is_laptop_camera,
        hardware_presence_note=runtime_status.hardware_presence_note,
    )


def _selected_camera_connected(runtime: Any, profile: CameraRuntimeProfile) -> bool:
    if profile.source_type in {"video_file", "replay"}:
        source = profile.device_path or profile.stable_path
        return bool(source)
    if profile.source_type not in {"usb", "laptop"}:
        return False
    inventory = runtime.device_manager.inventory()
    return any(
        device.connected
        and (
            (profile.device_path is not None and device.device_path == profile.device_path)
            or (profile.stable_path is not None and device.stable_path == profile.stable_path)
            or (profile.device_id is not None and device.device_id == profile.device_id)
        )
        for device in inventory.cameras
    )
