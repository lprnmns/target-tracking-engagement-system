import base64
import io
import math
import re
import time
from pathlib import Path
import unicodedata

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response, StreamingResponse

from app.api.deps import get_runtime
from app.schemas.vision import (
    CameraSelectRequest,
    CameraSource,
    CameraStatus,
    BrowserFrameInferenceRequest,
    VisionConfigUpdate,
    VisionEvent,
    VisionStatus,
)
from app.schemas.legacy_perception import (
    CameraHostDiagnostic,
    LegacyPerceptionPreset,
    LegacyPerceptionPresetList,
    RealCameraEvidence,
    RealCameraAcceptance,
    RealCameraEvidenceStatus,
    RealCameraSelection,
    RealCameraSelectRequest,
)
from app.schemas.camera_runtime import CameraRuntimeApplyResult, CameraRuntimeControlsUpdate, CameraRuntimeProfile, CameraRuntimeStatus
from app.schemas.vision_runtime_settings import (
    VisionRuntimeApplyResult,
    VisionRuntimePreset,
    VisionRuntimePresetApplyRequest,
    VisionRuntimePresetSaveRequest,
    VisionRuntimeProfile,
    VisionRuntimeStatus,
    VisionRuntimeTestResult,
    VisionRuntimeVerifyResult,
)
from app.services.runtime_state import RuntimeState
from app.services.camera_status_bridge import camera_status_from_runtime
from app.services.cockpit_verdict_service import build_cockpit_verdicts
from app.services.live_tracker_bridge import normalize_tracker_profile, tracker_profile_catalog
from app.services.storage_paths import project_root

try:  # pragma: no cover - host dependent
    import numpy as np
except Exception:  # pragma: no cover
    np = None

try:  # pragma: no cover - host dependent
    from PIL import Image
except Exception:  # pragma: no cover
    Image = None

try:  # pragma: no cover - host dependent
    import cv2
except Exception:  # pragma: no cover
    cv2 = None

vision_router = APIRouter(prefix="/api/vision", tags=["vision"])
camera_router = APIRouter(prefix="/api/camera", tags=["camera"])


def _camera_transport_headers(runtime: RuntimeState) -> dict[str, str]:
    """Expose capture timing without changing the JPEG body contract.

    The cockpit's old polling transport made it impossible to distinguish a
    fresh camera frame from a delayed HTTP response. These headers are useful
    to diagnostics and proxies; the MJPEG endpoint repeats the same values on
    each multipart part.
    """
    camera = runtime.camera_runtime
    captured_at = getattr(camera, "last_frame_at", None)
    sequence = int(getattr(camera, "frame_sequence", 0) or 0)
    generated_at_ms = int(time.time() * 1000)
    captured_at_ms = int(float(captured_at) * 1000) if captured_at is not None else 0
    age_ms = max(0, generated_at_ms - captured_at_ms) if captured_at_ms else -1
    return {
        "X-Camera-Frame-Sequence": str(sequence),
        "X-Camera-Captured-At-Ms": str(captured_at_ms),
        "X-Camera-Frame-Age-Ms": str(age_ms),
        "X-Camera-Transport-Generated-At-Ms": str(generated_at_ms),
    }


def _machine_class_token(value: str | None) -> str:
    """Keep camera overlay labels identical to the logical target registry."""
    raw = unicodedata.normalize("NFKD", value or "target").encode("ascii", "ignore").decode("ascii")
    token = re.sub(r"[^a-zA-Z0-9]+", "_", raw.strip().lower()).strip("_") or "target"
    return {"f_16": "f16", "uav": "mini_micro_uav", "mini_micro_iha": "mini_micro_uav"}.get(token, token)


def _machine_team_token(value: str | None) -> str:
    return {
        "enemy": "dusman",
        "dusman": "dusman",
        "friend": "dost",
        "dost": "dost",
    }.get((value or "unknown").lower(), "bilinmeyen")


def _display_class_slug(value: str | None) -> str:
    """Return the Turkish, ASCII-safe slug used only in visible overlays."""
    token = _machine_class_token(value)
    return {
        "f16": "savas_ucagi",
        "helicopter": "helikopter",
        "ballistic_missile": "balistik_fuze",
        "mini_micro_uav": "mini_mikro_iha",
        "balloon": "balon",
    }.get(token, token)


def _sequence_from_target_id(value: str | None, fallback: int = 1) -> int:
    match = re.search(r"_(\d+)(?:_hedefi)?(?:\s*—\s*İmha Edildi)?$", str(value or ""), re.IGNORECASE)
    if not match:
        return fallback
    try:
        sequence = int(match.group(1))
    except ValueError:
        return fallback
    return sequence if sequence > 0 else fallback


def _display_target_name(
    *,
    class_name: str | None = None,
    team: str | None = None,
    sequence: int = 1,
    balloon: bool = False,
    standalone_balloon: bool = False,
    destroyed: bool = False,
) -> str:
    """Build the Turkish visual name without changing machine identities."""
    sequence = max(1, int(sequence))
    if standalone_balloon:
        name = f"balon_{sequence}_hedefi"
    elif balloon:
        class_slug = _display_class_slug(class_name)
        # Friendly aircraft balloons are evidence-only and must not be
        # labelled as an operational ``hedefi``.  Keep the aircraft class,
        # IFF and sequence in the name so the association is obvious.
        if (team or "unknown").lower() in {"friend", "dost"}:
            name = f"{class_slug}_dost_{sequence}_balon"
        else:
            name = f"{class_slug}_{sequence}_hedefi"
    else:
        name = f"{_display_class_slug(class_name)}_{_machine_team_token(team)}_{sequence}"
    return f"{name} — İmha Edildi" if destroyed else name


def _overlay_logical_names(runtime: RuntimeState, event: VisionEvent) -> tuple[dict[int, str], dict[int, str]]:
    """Resolve frame-local ids to mission-scoped names for setup/camera overlays.

    Tracker and detector ids are different namespaces.  Resolve the current
    frame through the persistent body track where possible, then fall back to
    a standalone label only when no logical aircraft association is present.
    """
    body_names: dict[int, str] = {}
    balloon_names: dict[int, str] = {}
    tracker_by_id = {}
    tracker_service = getattr(getattr(runtime, "auto_tracker", None), "multi_target_tracker", None)
    if tracker_service is not None:
        try:
            tracker_by_id = {item.track_id: item for item in tracker_service.status().tracks}
        except Exception:  # pragma: no cover - optional runtime adapter
            tracker_by_id = {}
    for target in runtime.target_registry.status().targets:
        sequence = _sequence_from_target_id(target.target_id)
        destroyed = str(target.state) == "DESTROYED"
        current_body = None
        if target.body_track_id is not None:
            current_body = next((body for body in event.body_detections if body.track_id == target.body_track_id), None)
        if current_body is None and target.body_track_id is None and target.body_detection_id is not None:
            current_body = next((body for body in event.body_detections if body.id == target.body_detection_id), None)
        display_class = current_body.class_name if current_body is not None else target.target_class
        display_team = (
            current_body.target_team
            if current_body is not None and (current_body.target_team or "unknown").lower() != "unknown"
            else target.target_team
        )
        body_name = _display_target_name(
            class_name=display_class,
            team=display_team,
            sequence=sequence,
            destroyed=destroyed,
        )
        balloon_name = _display_target_name(
            class_name=display_class,
            team=display_team,
            sequence=sequence,
            balloon=True,
            destroyed=destroyed,
        )
        # Registry detection ids are frame-local.  Prefer the persistent body
        # track to resolve the id in this exact event, and never paint a stale
        # id onto a different class after the detector reuses an integer.
        if current_body is not None:
            body_names[int(current_body.id)] = body_name
        tracked = tracker_by_id.get(target.balloon_track_id) if target.balloon_track_id is not None else None
        current_balloon_id = None
        if tracked is not None and tracked.detection_id is not None and any(item.id == tracked.detection_id for item in event.balloon_detections):
            current_balloon_id = tracked.detection_id
        elif tracked is None and target.balloon_detection_id is not None and any(item.id == target.balloon_detection_id for item in event.balloon_detections):
            current_balloon_id = target.balloon_detection_id
        if current_balloon_id is not None:
            balloon_names[int(current_balloon_id)] = balloon_name
    for body in event.body_detections:
        body_names.setdefault(
            int(body.id),
            _display_target_name(
                class_name=body.class_name,
                team=body.target_team,
                sequence=body.id,
            ),
        )
    for balloon in event.balloon_detections:
        balloon_names.setdefault(
            int(balloon.id),
            _display_target_name(sequence=balloon.id, balloon=True, standalone_balloon=True),
        )
    return body_names, balloon_names


@vision_router.post("/models/upload")
async def upload_vision_model(request: Request) -> dict[str, str]:
    """Receive a locally chosen .pt file without exposing browser fake paths."""
    raw_name = request.headers.get("x-file-name", "")
    name = Path(raw_name).name
    if not name or Path(name).suffix.lower() not in {".pt", ".onnx", ".engine"}:
        raise HTTPException(status_code=400, detail="MODEL_FILE_EXTENSION_INVALID")
    if not re.fullmatch(r"[A-Za-z0-9._ -]+", name):
        raise HTTPException(status_code=400, detail="MODEL_FILE_NAME_INVALID")
    content = await request.body()
    max_bytes = 512 * 1024 * 1024
    if not content or len(content) > max_bytes:
        raise HTTPException(status_code=413, detail="MODEL_FILE_SIZE_INVALID")
    destination_dir = project_root() / "models" / "uploaded"
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = destination_dir / name
    destination.write_bytes(content)
    return {"path": str(destination), "file_name": name}


@vision_router.get("/status", response_model=VisionStatus)
def get_vision_status(runtime: RuntimeState = Depends(get_runtime)) -> VisionStatus:
    return runtime.vision_pipeline.status()


@vision_router.get("/config", response_model=VisionConfigUpdate)
def get_vision_config(runtime: RuntimeState = Depends(get_runtime)) -> VisionConfigUpdate:
    return VisionConfigUpdate(
        vision_mode=runtime.vision.vision_mode,
        body_model_path=runtime.vision.body_model_path,
        balloon_model_path=runtime.vision.balloon_model_path,
        body_conf_threshold=runtime.vision.body_conf_threshold,
        balloon_conf_threshold=runtime.vision.balloon_conf_threshold,
    )


@vision_router.put("/config", response_model=VisionStatus)
def update_vision_config(
    update: VisionConfigUpdate,
    runtime: RuntimeState = Depends(get_runtime),
) -> VisionStatus:
    status = runtime.vision_pipeline.configure(update)
    # The YOLO worker reads per-model thresholds from VisionRuntimeProfile on
    # every frame.  Keeping only VisionService in sync made the engineering
    # slider report "applied" while inference continued with the old values.
    runtime_status = runtime.vision_pipeline.status()
    runtime.vision_runtime.apply(
        runtime.vision_runtime.profile.model_copy(
            update={
                "body_conf_threshold": update.body_conf_threshold,
                "balloon_conf_threshold": update.balloon_conf_threshold,
                "conf": min(update.body_conf_threshold, update.balloon_conf_threshold),
            }
        ),
        current_fps=runtime_status.fps,
        latest_latency_ms=runtime_status.latest_latency_ms,
        camera_source_type=runtime.camera_runtime.profile.source_type,
    )
    # Keep direct Setup-selected .pt files visible to apply/reload/benchmark
    # even when no registry package has been imported for them yet.
    runtime.vision_runtime.set_direct_model_paths(update.body_model_path, update.balloon_model_path)
    return status


@vision_router.post("/start", response_model=VisionStatus)
def start_vision(runtime: RuntimeState = Depends(get_runtime)) -> VisionStatus:
    return runtime.vision_pipeline.start()


@vision_router.post("/stop", response_model=VisionStatus)
def stop_vision(runtime: RuntimeState = Depends(get_runtime)) -> VisionStatus:
    return runtime.vision_pipeline.stop()


@vision_router.post("/snapshot")
def snapshot(runtime: RuntimeState = Depends(get_runtime)) -> Response:
    if runtime.vision_runtime.profile.inference_adapter == "opencv_live_circle_surrogate":
        runtime.vision_surrogate.snapshot(runtime.camera_runtime, runtime.vision_runtime.profile)
    return Response(content=runtime.camera.snapshot(), media_type="image/jpeg")


@vision_router.get("/latest", response_model=VisionEvent)
def get_latest_vision(runtime: RuntimeState = Depends(get_runtime)) -> VisionEvent:
    return runtime.vision_pipeline.latest()


@vision_router.post("/browser-frame", response_model=VisionEvent)
def browser_frame_inference(request: BrowserFrameInferenceRequest, runtime: RuntimeState = Depends(get_runtime)) -> VisionEvent:
    if np is None or Image is None:
        raise HTTPException(status_code=503, detail="browser_frame_decode_dependencies_unavailable")
    payload = request.image_base64
    if "," in payload:
        payload = payload.split(",", 1)[1]
    try:
        decoded = base64.b64decode(payload, validate=False)
        image = Image.open(io.BytesIO(decoded)).convert("RGB")
        # CameraRuntime/OpenCV frames are BGR.  Keep browser frames in the
        # same convention so model preprocessing and body-ROI HSV IFF see
        # identical channel ordering.
        frame = np.asarray(image)[:, :, ::-1].copy()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"browser_frame_decode_failed:{exc}") from exc
    return runtime.vision_pipeline.latest_from_external_frame(
        frame,
        source="browser_camera_ultralytics_yolo",
        camera_source_kind="browser_camera",
        frame_origin="browser_frame_upload",
        camera_device_path=request.device_label,
    )


@vision_router.get("/legacy-presets", response_model=LegacyPerceptionPresetList)
def legacy_perception_presets(runtime: RuntimeState = Depends(get_runtime)) -> LegacyPerceptionPresetList:
    return runtime.legacy_perception.list_presets()


@vision_router.get("/legacy-presets/{preset_id}", response_model=LegacyPerceptionPreset)
def legacy_perception_preset(preset_id: str, runtime: RuntimeState = Depends(get_runtime)) -> LegacyPerceptionPreset:
    return runtime.legacy_perception.get_preset(preset_id)


@vision_router.get("/real-camera/status", response_model=RealCameraEvidenceStatus)
def real_camera_status(runtime: RuntimeState = Depends(get_runtime)) -> RealCameraEvidenceStatus:
    return runtime.legacy_perception.status(runtime.camera_runtime)


@vision_router.post("/real-camera/select", response_model=RealCameraSelection)
def real_camera_select(request: RealCameraSelectRequest, runtime: RuntimeState = Depends(get_runtime)) -> RealCameraSelection:
    return runtime.camera_host.select_camera(request.device_path, request.camera_kind)


@vision_router.post("/real-camera/capture-evidence", response_model=RealCameraEvidence)
def real_camera_capture_evidence(runtime: RuntimeState = Depends(get_runtime), preset_id: str | None = None, device_path: str | None = None) -> RealCameraEvidence:
    host = runtime.camera_host.status()
    if not host.host_camera_devices_detected:
        blocked = runtime.camera_host.capture_blocked("Linux host did not expose /dev/video* camera devices")
        return runtime.legacy_perception.capture_host_blocked_evidence(blocked, preset_id=preset_id)
    if "permission" in host.blocker_reason.lower():
        blocked = runtime.camera_host.capture_blocked(host.blocker_reason)
        return runtime.legacy_perception.capture_host_blocked_evidence(blocked, preset_id=preset_id)
    direct_capture = runtime.camera_host.capture_frame_evidence(device_path=device_path)
    if direct_capture.get("frame_captured") or runtime.camera_runtime.profile.source_type == "mock":
        return runtime.legacy_perception.record_camera_host_frame_evidence(direct_capture, preset_id=preset_id)
    evidence = runtime.legacy_perception.capture_evidence(runtime.camera_runtime, preset_id=preset_id)
    runtime.camera_host.mark_capture_attempt(
        frame_captured=evidence.status == "recorded",
        reason="real camera frame evidence captured" if evidence.status == "recorded" else evidence.status,
    )
    return evidence


@vision_router.get("/real-camera/latest", response_model=RealCameraEvidence)
def real_camera_latest(runtime: RuntimeState = Depends(get_runtime)) -> RealCameraEvidence:
    return runtime.legacy_perception.latest()


@vision_router.get("/real-camera/acceptance", response_model=RealCameraAcceptance)
def real_camera_acceptance(runtime: RuntimeState = Depends(get_runtime)) -> RealCameraAcceptance:
    host = runtime.camera_host.latest()
    latest = runtime.legacy_perception.latest()
    status = "passed" if latest.status == "recorded" and latest.frame_origin == "real_capture" else (
        "blocked" if host.camera_acceptance_status == "blocked_by_host_os" else "partial"
    )
    return RealCameraAcceptance(
        status=status,
        camera_tooling_status=host.camera_acceptance_status,
        frame_captured=latest.status == "recorded" and latest.frame_origin == "real_capture",
        device_path=latest.camera_device_path,
        width=latest.frame_width,
        height=latest.frame_height,
        fps_estimate=latest.fps_estimate,
        frame_hash=latest.target_center_metadata.get("frame_hash") if isinstance(latest.target_center_metadata, dict) else None,
        frame_path=latest.target_center_metadata.get("frame_path") if isinstance(latest.target_center_metadata, dict) else None,
        capture_method=latest.target_center_metadata.get("capture_method") if isinstance(latest.target_center_metadata, dict) else None,
        selected_camera_device=latest.target_center_metadata.get("selected_camera_device") if isinstance(latest.target_center_metadata, dict) else latest.camera_device_path,
        selected_camera_name=latest.target_center_metadata.get("selected_camera_name") if isinstance(latest.target_center_metadata, dict) else None,
        camera_kind=(latest.target_center_metadata.get("camera_kind") if isinstance(latest.target_center_metadata, dict) else None) or "unknown_camera",
        internal_camera_passed=latest.status == "recorded" and latest.camera_device_path in {"/dev/video0", "/dev/video1"},
        external_usb_camera_passed=latest.status == "recorded" and latest.camera_device_path in {"/dev/video2", "/dev/video3"},
        blocker_reason=host.blocker_reason,
        camera_host=host,
        latest_evidence=latest,
        advisory_only=True,
        physical_command_enabled=False,
        no_physical_command_generated=True,
    )


@vision_router.get("/camera-host/status", response_model=CameraHostDiagnostic)
def camera_host_status(runtime: RuntimeState = Depends(get_runtime)) -> CameraHostDiagnostic:
    return runtime.camera_host.status()


@vision_router.post("/camera-host/diagnose", response_model=CameraHostDiagnostic)
def camera_host_diagnose(runtime: RuntimeState = Depends(get_runtime)) -> CameraHostDiagnostic:
    return runtime.camera_host.diagnose()


@vision_router.get("/camera-host/latest", response_model=CameraHostDiagnostic)
def camera_host_latest(runtime: RuntimeState = Depends(get_runtime)) -> CameraHostDiagnostic:
    return runtime.camera_host.latest()


@camera_router.get("/status", response_model=CameraStatus)
def get_camera_status(runtime: RuntimeState = Depends(get_runtime)) -> CameraStatus:
    return camera_status_from_runtime(runtime)


@camera_router.get("/sources", response_model=list[CameraSource])
def get_camera_sources(runtime: RuntimeState = Depends(get_runtime)) -> list[CameraSource]:
    return runtime.camera.sources()


@camera_router.post("/select", response_model=CameraStatus)
def select_camera(
    request: CameraSelectRequest,
    runtime: RuntimeState = Depends(get_runtime),
) -> CameraStatus:
    return runtime.camera.select(request)


@camera_router.get("/stream.mjpg")
def camera_stream(
    runtime: RuntimeState = Depends(get_runtime),
    preview: str | None = Query(default=None),
    width: int | None = Query(default=None, ge=160, le=3840),
    height: int | None = Query(default=None, ge=120, le=2160),
    # Native transport keeps quality 95; the cockpit receives a sharp 720p
    # preview while inference continues to consume the untouched 1080p frame.
    quality: int = Query(default=95, ge=40, le=95),
) -> StreamingResponse:
    if preview == "ui":
        width = width or 1280
        height = height or 720
        quality = min(quality, 86)
    if runtime.camera_runtime.profile.source_type != "mock":
        return StreamingResponse(
            runtime.camera_runtime.mjpeg_stream(output_width=width, output_height=height, quality=quality),
            media_type="multipart/x-mixed-replace; boundary=frame",
            headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0", "Pragma": "no-cache", "X-Accel-Buffering": "no"},
        )
    return StreamingResponse(
        runtime.camera.mjpeg_stream(),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0", "Pragma": "no-cache", "X-Accel-Buffering": "no"},
    )


@camera_router.get("/frame.jpg")
def camera_frame(
    runtime: RuntimeState = Depends(get_runtime),
    preview: str | None = Query(default=None),
    width: int | None = Query(default=None, ge=160, le=3840),
    height: int | None = Query(default=None, ge=120, le=2160),
    # Native transport keeps quality 95; UI defaults are bounded at 720p/86.
    quality: int = Query(default=95, ge=40, le=95),
) -> Response:
    """Return one current frame for a browser-safe live preview."""
    headers = {
        "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
        "Pragma": "no-cache",
        "X-Content-Type-Options": "nosniff",
        **_camera_transport_headers(runtime),
    }
    if runtime.camera_runtime.profile.source_type == "mock":
        return Response(content=runtime.camera.snapshot(), media_type="image/jpeg", headers=headers)
    frame, warnings = runtime.camera_runtime.live_preview_frame()
    if cv2 is None:
        return Response(content=b"opencv unavailable", status_code=503, media_type="text/plain", headers=headers)
    if frame is None:
        frame = runtime.camera_runtime._placeholder_frame("; ".join(warnings) or "camera frame unavailable")
    # The UI requests ``preview=ui`` (1280x720, quality 86) so Tailscale/
    # browser transport does not become the apparent camera bottleneck.  A
    # caller may still request native dimensions explicitly; inference always
    # uses the untouched capture frame above.
    if preview == "ui":
        width = width or 1280
        height = height or 720
        quality = min(quality, 86)
    encoded_bytes = runtime.camera_runtime.encode_stream_jpeg(
        frame,
        quality=quality,
        output_width=width,
        output_height=height,
        cache_token=("plain", width, height, quality),
    )
    if encoded_bytes is None:
        return Response(content=b"jpeg encode failed", status_code=503, media_type="text/plain", headers=headers)
    return Response(content=encoded_bytes, media_type="image/jpeg", headers=headers)


_TR_ASCII_TRANS = str.maketrans("çÇğĞıİöÖşŞüÜ", "cCgGiIoOsSuU")


def _ascii_for_cv2(text: str) -> str:
    return text.translate(_TR_ASCII_TRANS)


def _draw_event_overlays(runtime: RuntimeState, frame, event):
    if cv2 is None or event is None:
        return frame
    if (event.frame_width or 0) > 0 and (event.frame_height or 0) > 0:
        sx, sy = frame.shape[1] / event.frame_width, frame.shape[0] / event.frame_height
        body_names, balloon_names = _overlay_logical_names(runtime, event)
        operation_state = runtime.operation.state() if hasattr(runtime, "operation") else None
        hide_balloon_labels = bool(
            operation_state is not None
            and operation_state.target_policy.value == "BALLOON_AIRCRAFT"
        )
        destroyed_targets = [
            target
            for target in runtime.target_registry.status().targets
            if str(target.state) == "DESTROYED" or target.consumed
        ]
        destroyed_body_tracks = {target.body_track_id for target in destroyed_targets if target.body_track_id is not None}
        destroyed_body_ids = {target.body_detection_id for target in destroyed_targets if target.body_detection_id is not None}
        destroyed_balloon_ids = {target.balloon_detection_id for target in destroyed_targets if target.balloon_detection_id is not None}
        live_tracks = runtime.auto_tracker.multi_target_tracker.status().tracks
        for target in destroyed_targets:
            track = next((item for item in live_tracks if item.track_id == target.balloon_track_id), None)
            if track is not None and track.detection_id is not None:
                destroyed_balloon_ids.add(track.detection_id)
        # Check active tracking telemetry for dynamic lead / aim window
        tracking_update = getattr(runtime.auto_tracker, "_last_update", None)
        tracking_active = bool(getattr(runtime.auto_tracker, "tracking_active", False) and tracking_update is not None)
        fcx = frame.shape[1] // 2
        fcy = frame.shape[0] // 2
        any_locked_on_fire_zone = False

        verdicts = getattr(event, "target_verdicts", None)
        if not verdicts:
            active_pol = operation_state.target_policy.value if operation_state else "BALLOON_AIRCRAFT"
            active_stg = runtime.mission.state.active_stage if hasattr(runtime, "mission") else "stage3"
            rrules = getattr(getattr(runtime.config, "decision", None), "range_rules", None) if hasattr(runtime, "config") else None
            verdicts = build_cockpit_verdicts(
                bodies=event.body_detections,
                balloons=event.balloon_detections,
                target_policy=active_pol,
                active_stage=active_stg,
                range_rules=rrules,
            )

        body_verdict_by_id = {v.detection_id: v for v in verdicts if v.kind == "body" and v.detection_id is not None}
        balloon_verdict_by_id = {v.detection_id: v for v in verdicts if v.kind == "balloon" and v.detection_id is not None}

        verdict_color_bgr = {
            "FIRE_AUTHORIZED": (40, 220, 50),
            "RANGE_WAIT": (30, 180, 245),
            "FRIEND_LOCKED": (235, 130, 50),
            "CLASSIFYING": (220, 80, 170),
        }

        # 1. Hava Araçları / Gövde Çizimi (Dikdörtgen kutu + üst etiket şeridi)
        for body in event.body_detections:
            if body.track_id in destroyed_body_tracks or body.id in destroyed_body_ids:
                continue
            verdict = body_verdict_by_id.get(body.id)
            if verdict is not None:
                color = verdict_color_bgr.get(verdict.verdict_state, (220, 150, 40))
                label_text = verdict.label_tr
            else:
                color = (40, 40, 220) if body.target_team == "enemy" else (50, 200, 50) if body.target_team == "friend" else (220, 150, 40)
                label_text = body_names.get(int(body.id), _display_target_name(class_name=body.class_name, team=body.target_team, sequence=body.id))

            bx, by = int(body.bbox.x * sx), int(body.bbox.y * sy)
            bw, bh = int(body.bbox.w * sx), int(body.bbox.h * sy)

            # Hedef kutusu
            cv2.rectangle(frame, (bx, by), (bx + bw, by + bh), color, 2)

            # Üst bilgi şeridi
            if label_text:
                display_text = _ascii_for_cv2(label_text)
                (tw, th), _ = cv2.getTextSize(display_text, cv2.FONT_HERSHEY_SIMPLEX, 0.42, 1)
                lx1 = max(0, bx)
                ly2 = max(th + 6, by)
                ly1 = max(0, ly2 - th - 6)
                lx2 = min(frame.shape[1] - 1, lx1 + tw + 10)
                cv2.rectangle(frame, (lx1, ly1), (lx2, ly2), (18, 18, 18), -1)
                cv2.rectangle(frame, (lx1, ly1), (lx2, ly2), color, 1)
                cv2.putText(frame, display_text, (lx1 + 5, ly2 - 4), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (255, 255, 255), 1, cv2.LINE_AA)

        # 2. Balon Çizimi (Dairesel hedef + kilitlenme alanı + etiket şeridi)
        for balloon in event.balloon_detections:
            if balloon.id in destroyed_balloon_ids:
                continue
            verdict = balloon_verdict_by_id.get(balloon.id)
            if verdict is not None:
                color = verdict_color_bgr.get(verdict.verdict_state, (40, 165, 255))
                label_text = verdict.label_tr
            else:
                color = (40, 165, 255)
                label_text = None if hide_balloon_labels else balloon_names.get(int(balloon.id), f"balon_{balloon.id}_hedefi")

            x, y = int(balloon.bbox.x * sx), int(balloon.bbox.y * sy)
            w, h = int(balloon.bbox.w * sx), int(balloon.bbox.h * sy)
            cx, cy = x + w // 2, y + h // 2
            outer_radius = max(6, int(max(w, h) / 2))
            inner_radius = max(4, int(min(w, h) * 0.35 / 2.0))

            # Takip edilen aktif hedef kontrolü
            is_tracked_target = False
            if tracking_active and tracking_update.target_center_x is not None and tracking_update.target_center_y is not None:
                det_cx = balloon.bbox.x + balloon.bbox.w / 2.0
                det_cy = balloon.bbox.y + balloon.bbox.h / 2.0
                if math.hypot(det_cx - tracking_update.target_center_x, det_cy - tracking_update.target_center_y) < max(balloon.bbox.w, balloon.bbox.h) * 0.8:
                    is_tracked_target = True

            if is_tracked_target and tracking_update.lock_zone_center_x is not None:
                lead_target_x = int(tracking_update.lock_zone_center_x * sx)
                lead_target_y = int(tracking_update.lock_zone_center_y * sy)

                dist_lead = math.hypot(lead_target_x - cx, lead_target_y - cy)
                max_offset = max(0.0, float(outer_radius - inner_radius))
                if dist_lead > max_offset and dist_lead > 0:
                    scale = max_offset / dist_lead
                    lead_target_x = int(cx + (lead_target_x - cx) * scale)
                    lead_target_y = int(cy + (lead_target_y - cy) * scale)

                dist_to_crosshair = math.hypot(lead_target_x - fcx, lead_target_y - fcy)
                dist_balloon_to_crosshair = math.hypot(cx - fcx, cy - fcy)
                inside_lock = (dist_to_crosshair <= inner_radius) or (dist_balloon_to_crosshair <= inner_radius)
                if inside_lock:
                    any_locked_on_fire_zone = True

                balloon_color = (0, 255, 0) if inside_lock else (0, 220, 255)
                aim_color = (0, 255, 0) if inside_lock else (0, 215, 255)

                cv2.circle(frame, (cx, cy), outer_radius, balloon_color, 2)
                cv2.circle(frame, (lead_target_x, lead_target_y), inner_radius, aim_color, 2 if inside_lock else 1)
                cv2.circle(frame, (lead_target_x, lead_target_y), 2, aim_color, -1)
            else:
                cv2.circle(frame, (cx, cy), outer_radius, color, 2)
                cv2.circle(frame, (cx, cy), inner_radius, color, 1)
                cv2.circle(frame, (cx, cy), 2, color, -1)

            # Balon etiketi
            if label_text:
                display_text = _ascii_for_cv2(label_text)
                (tw, th), _ = cv2.getTextSize(display_text, cv2.FONT_HERSHEY_SIMPLEX, 0.38, 1)
                lx1 = max(0, cx - tw // 2 - 5)
                ly2 = max(th + 6, cy - outer_radius - 4)
                ly1 = max(0, ly2 - th - 6)
                lx2 = min(frame.shape[1] - 1, lx1 + tw + 10)
                cv2.rectangle(frame, (lx1, ly1), (lx2, ly2), (18, 18, 18), -1)
                cv2.rectangle(frame, (lx1, ly1), (lx2, ly2), color, 1)
                cv2.putText(frame, display_text, (lx1 + 5, ly2 - 4), cv2.FONT_HERSHEY_SIMPLEX, 0.38, (255, 255, 255), 1, cv2.LINE_AA)

    return frame


def _aligned_overlay_jpeg(runtime: RuntimeState, width: int | None, height: int | None, quality: int):
    """Encode camera frame, burning detections on the exact matched source frame to eliminate visual motion lag."""
    if cv2 is None:
        return None

    # 1. Öncelik: YOLO'nun fiilen koştuğu kaynak kare (exact source frame).
    # Bu sayede taret süpürme/dönüş yaparken dahi kutu cismin tam üzerinde kalır;
    # kutunun geriden gelmesi / sürüklenmesi (desenkronizasyon) sıfırlanır.
    aligned = runtime.vision_pipeline.latest_aligned_overlay_sample()
    if aligned is not None:
        event, source_frame, source_sequence, source_captured_at_ms = aligned
        frame = runtime.camera_runtime._stream_frame(source_frame).copy()
        _draw_event_overlays(runtime, frame, event)
        encoded_bytes = runtime.camera_runtime.encode_stream_jpeg(
            frame,
            quality=quality,
            output_width=width,
            output_height=height,
            cache_token=("overlay-aligned", event.frame_id, source_sequence, width, height, quality),
        )
        if encoded_bytes is not None:
            return encoded_bytes, event, source_sequence, source_captured_at_ms

    # 2. Yedek: Model henüz ısınırken veya ilk inferans öncesinde taze kamera karesi
    live_frame, warnings = runtime.camera_runtime.live_preview_frame()
    if live_frame is not None:
        frame = runtime.camera_runtime._stream_frame(live_frame).copy()
        source_sequence = int(getattr(runtime.camera_runtime, "frame_sequence", 0) or 0)
        source_captured_at = getattr(runtime.camera_runtime, "last_frame_at", None)
        source_captured_at_ms = int(float(source_captured_at) * 1000) if source_captured_at else int(time.time() * 1000)
        event = runtime.vision.latest_event
        if event is not None and getattr(event, "timestamp_ms", 0) > 0:
            if (time.time() * 1000 - event.timestamp_ms) < 2000:
                _draw_event_overlays(runtime, frame, event)
        encoded_bytes = runtime.camera_runtime.encode_stream_jpeg(
            frame,
            quality=quality,
            output_width=width,
            output_height=height,
            cache_token=("overlay-live", source_sequence, width, height, quality),
        )
        if encoded_bytes is not None:
            if event is None:
                event = runtime.vision_pipeline._warming_event()
            return encoded_bytes, event, source_sequence, source_captured_at_ms

    return None


def _aligned_overlay_headers(event, source_sequence: int, source_captured_at_ms: int | None) -> dict[str, str]:
    generated_at_ms = int(time.time() * 1000)
    source_age_ms = max(0, generated_at_ms - source_captured_at_ms) if source_captured_at_ms is not None else -1
    return {
        "X-Camera-Frame-Sequence": str(source_sequence),
        "X-Vision-Frame-Id": str(event.frame_id),
        "X-Overlay-Sync-Status": "EXACT_SOURCE_FRAME",
        "X-Camera-Captured-At-Ms": str(source_captured_at_ms or 0),
        "X-Camera-Frame-Age-Ms": str(source_age_ms),
        "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    }


@camera_router.get("/frame-overlay.jpg")
def camera_overlay_frame(
    runtime: RuntimeState = Depends(get_runtime),
    preview: str | None = Query(default=None),
    width: int | None = Query(default=None, ge=160, le=3840),
    height: int | None = Query(default=None, ge=120, le=2160),
    quality: int = Query(default=95, ge=40, le=95),
) -> Response:
    """Single-frame fallback that preserves the detector/source-frame join."""
    if preview == "ui":
        width = width or 1280
        height = height or 720
        quality = min(quality, 86)
    rendered = _aligned_overlay_jpeg(runtime, width, height, quality)
    if rendered is None:
        return Response(content=b"aligned overlay warming up", status_code=503, media_type="text/plain")
    encoded_bytes, event, source_sequence, source_captured_at_ms = rendered
    return Response(
        content=encoded_bytes,
        media_type="image/jpeg",
        headers=_aligned_overlay_headers(event, source_sequence, source_captured_at_ms),
    )


@camera_router.get("/stream-overlay.mjpg")
def camera_overlay_stream(
    runtime: RuntimeState = Depends(get_runtime),
    preview: str | None = Query(default=None),
    width: int | None = Query(default=None, ge=160, le=3840),
    height: int | None = Query(default=None, ge=120, le=2160),
    # Fluid preview default is 85; UI defaults are bounded at 720p/85.
    quality: int = Query(default=85, ge=40, le=95),
) -> StreamingResponse:
    """Live MJPEG where every bbox is burned into its exact YOLO source frame."""
    if preview == "ui":
        width = width or 1280
        height = height or 720
        quality = min(quality, 85)
    if runtime.camera_runtime.profile.source_type == "mock":
        return StreamingResponse(runtime.camera.mjpeg_stream(), media_type="multipart/x-mixed-replace; boundary=frame")
    def frames():
        last_sequence = -1
        target_fps = max(runtime.camera_runtime.profile.fps, 1)
        target_interval = 1.0 / target_fps
        while True:
            t0 = time.monotonic()
            rendered = _aligned_overlay_jpeg(runtime, width, height, quality)
            if rendered is None:
                time.sleep(0.015)
                continue
            encoded_bytes, event, source_sequence, source_captured_at_ms = rendered
            if source_sequence == last_sequence:
                time.sleep(0.003)
                continue
            last_sequence = source_sequence
            headers = _aligned_overlay_headers(event, source_sequence, source_captured_at_ms)
            part_header = b"--frame\r\nContent-Type: image/jpeg\r\n" + b"".join(
                f"{name}: {value}\r\n".encode() for name, value in headers.items() if name != "Cache-Control"
            ) + b"\r\n"
            yield part_header + encoded_bytes + b"\r\n"
            elapsed = time.monotonic() - t0
            sleep_time = max(0.001, target_interval - elapsed)
            time.sleep(sleep_time)
    return StreamingResponse(frames(), media_type="multipart/x-mixed-replace; boundary=frame")


@camera_router.get("/runtime/status", response_model=CameraRuntimeStatus)
def camera_runtime_status(runtime: RuntimeState = Depends(get_runtime)) -> CameraRuntimeStatus:
    return runtime.camera_runtime.status()


@camera_router.post("/runtime/start-preview", response_model=CameraRuntimeStatus)
def camera_runtime_start_preview(runtime: RuntimeState = Depends(get_runtime)) -> CameraRuntimeStatus:
    """Start only the selected raw camera capture; never enables inference."""
    return runtime.camera_runtime.start_preview()


@camera_router.get("/runtime/profile", response_model=CameraRuntimeProfile)
def camera_runtime_profile(runtime: RuntimeState = Depends(get_runtime)) -> CameraRuntimeProfile:
    return runtime.camera_runtime.profile


@camera_router.post("/runtime/apply-profile", response_model=CameraRuntimeApplyResult)
def camera_runtime_apply(profile: CameraRuntimeProfile, runtime: RuntimeState = Depends(get_runtime)) -> CameraRuntimeApplyResult:
    if runtime.stage3_competition_profile_locked():
        raise HTTPException(status_code=409, detail="A3_PROFILE_LOCKED")
    return runtime.camera_runtime.apply(profile)


@camera_router.patch("/runtime/controls", response_model=CameraRuntimeStatus)
def camera_runtime_controls(update: CameraRuntimeControlsUpdate, runtime: RuntimeState = Depends(get_runtime)) -> CameraRuntimeStatus:
    if runtime.stage3_competition_profile_locked():
        raise HTTPException(status_code=409, detail="A3_PROFILE_LOCKED")
    return runtime.camera_runtime.apply_controls(update)


@camera_router.post("/runtime/reset-defaults", response_model=CameraRuntimeApplyResult)
def camera_runtime_reset(runtime: RuntimeState = Depends(get_runtime)) -> CameraRuntimeApplyResult:
    if runtime.stage3_competition_profile_locked():
        raise HTTPException(status_code=409, detail="A3_PROFILE_LOCKED")
    return runtime.camera_runtime.reset_defaults()


@camera_router.post("/runtime/probe-current", response_model=CameraRuntimeApplyResult)
def camera_runtime_probe_current(runtime: RuntimeState = Depends(get_runtime)) -> CameraRuntimeApplyResult:
    return runtime.camera_runtime.probe_current()


@camera_router.post("/runtime/snapshot")
def camera_runtime_snapshot(runtime: RuntimeState = Depends(get_runtime)) -> dict:
    if runtime.vision_runtime.profile.inference_adapter == "opencv_live_circle_surrogate":
        return runtime.vision_surrogate.snapshot(runtime.camera_runtime, runtime.vision_runtime.profile)
    return runtime.camera_runtime.snapshot()


@camera_router.post("/runtime/benchmark")
def camera_runtime_benchmark(runtime: RuntimeState = Depends(get_runtime)) -> dict:
    return runtime.camera_runtime.benchmark()


@camera_router.post("/runtime/release")
def camera_runtime_release(runtime: RuntimeState = Depends(get_runtime)) -> dict:
    return runtime.camera_runtime.release()


@vision_router.get("/runtime/settings", response_model=VisionRuntimeProfile)
def vision_runtime_settings(runtime: RuntimeState = Depends(get_runtime)) -> VisionRuntimeProfile:
    return runtime.vision_runtime.profile


@vision_router.post("/runtime/apply-settings", response_model=VisionRuntimeApplyResult)
def vision_runtime_apply(profile: VisionRuntimeProfile, runtime: RuntimeState = Depends(get_runtime)) -> VisionRuntimeApplyResult:
    if runtime.stage3_competition_profile_locked():
        raise HTTPException(status_code=409, detail="A3_PROFILE_LOCKED")
    profile = profile.model_copy(update={"tracker_enabled": True, "tracker_type": "current_custom"})
    requested_tracker = "current_custom"
    active_tracker = runtime.vision_pipeline.tracker_status()["active_profile"]
    if requested_tracker != active_tracker:
        available = {item["profile_id"]: item for item in tracker_profile_catalog()}
        tracker_detail = available.get(requested_tracker)
        if tracker_detail is None or not tracker_detail["available"]:
            raise HTTPException(status_code=409, detail=(tracker_detail or {}).get("reason") or "TRACKER_UNAVAILABLE")
        if runtime.tracking_loop.is_running:
            raise HTTPException(status_code=409, detail="TRACKER_SWITCH_REQUIRES_MANUAL")
    status = runtime.vision_pipeline.status()
    result = runtime.vision_runtime.apply(profile, current_fps=status.fps, latest_latency_ms=status.latest_latency_ms, camera_source_type=runtime.camera_runtime.profile.source_type)
    if result.accepted:
        # Mirror the live runtime truth into the legacy/status surface so all
        # cockpit and Setup readers report the same two thresholds.
        runtime.vision.configure(
            VisionConfigUpdate(
                vision_mode=runtime.vision.vision_mode,
                body_model_path=runtime.vision.body_model_path,
                balloon_model_path=runtime.vision.balloon_model_path,
                body_conf_threshold=profile.body_conf_threshold,
                balloon_conf_threshold=profile.balloon_conf_threshold,
            )
        )
        if requested_tracker != active_tracker:
            runtime.vision_pipeline.configure_tracker(profile.tracker_type, enabled=profile.tracker_enabled)
            # A hot swap resets identity namespaces. Clear every consumer of
            # the previous ids before the next detector frame is published.
            runtime.auto_tracker.clear_target()
            runtime.association.reset()
            runtime.target_priority.reset()
            runtime.target_registry.reset()
            runtime.hit_confirmation.reset()
            runtime.operation.clear_target()
    return result


@vision_router.get("/runtime/trackers")
def vision_runtime_trackers(runtime: RuntimeState = Depends(get_runtime)) -> dict:
    profiles = [item for item in tracker_profile_catalog() if item["profile_id"] == "current_custom"]
    return {
        **runtime.vision_pipeline.tracker_status(),
        "profiles": profiles,
        "locked_profile": "current_custom",
        "switch_requires_manual": True,
        "hot_apply": True,
        "no_physical_command_generated": True,
    }


@vision_router.post("/runtime/reset-defaults", response_model=VisionRuntimeApplyResult)
def vision_runtime_reset(runtime: RuntimeState = Depends(get_runtime)) -> VisionRuntimeApplyResult:
    if runtime.stage3_competition_profile_locked():
        raise HTTPException(status_code=409, detail="A3_PROFILE_LOCKED")
    status = runtime.vision_pipeline.status()
    return runtime.vision_runtime.reset_defaults()


@vision_router.post("/runtime/reload-models")
def vision_runtime_reload(runtime: RuntimeState = Depends(get_runtime)) -> dict:
    if runtime.stage3_competition_profile_locked():
        raise HTTPException(status_code=409, detail="A3_PROFILE_LOCKED")
    return runtime.vision_runtime.reload_models()


@vision_router.post("/runtime/warmup")
def vision_runtime_warmup(runtime: RuntimeState = Depends(get_runtime)) -> dict:
    return runtime.vision_runtime.warmup(runtime.vision_pipeline)


@vision_router.post("/runtime/benchmark")
def vision_runtime_benchmark(runtime: RuntimeState = Depends(get_runtime)) -> dict:
    return runtime.vision_runtime.benchmark(runtime.vision_pipeline)


@vision_router.get("/runtime/status", response_model=VisionRuntimeStatus)
def vision_runtime_status(runtime: RuntimeState = Depends(get_runtime)) -> VisionRuntimeStatus:
    pipeline_status = runtime.vision_pipeline.status()
    result = runtime.vision_runtime.status(
        current_fps=pipeline_status.fps,
        latest_latency_ms=pipeline_status.latest_latency_ms,
        camera_source_type=runtime.camera_runtime.profile.source_type,
    )
    # Surface pipeline-level CUDA truth separately from driver discovery. A
    # Windows driver can report CUDA available while the current context is
    # latched to CPU after a failed kernel; operators need that distinction in
    # the same endpoint they already use for runtime status.
    cuda_diagnostics = runtime.vision_pipeline.cuda_runtime_diagnostics()
    diagnostic_warnings = list(result.warnings)
    if cuda_diagnostics.get("cuda_fallback_active"):
        diagnostic_warnings.append("CUDA_FALLBACK_ACTIVE")
    result = result.model_copy(update={**cuda_diagnostics, "warnings": sorted(set(diagnostic_warnings))})
    # Setup Center supports direct local model paths in addition to registry
    # packages. Reflect the path actually used by VisionPipeline; otherwise the
    # cockpit says "model missing / reload required" while CUDA inference is
    # visibly producing detections from that selected file.
    direct_models = [
        ("body", runtime.vision.body_model_path),
        ("balloon", runtime.vision.balloon_model_path),
    ]
    present = [(role, str(Path(path))) for role, path in direct_models if path and Path(path).is_file()]
    inference_failed = any(str(item).startswith("ultralytics_inference_failed:") for item in pipeline_status.warnings)
    if result.profile.inference_adapter == "ultralytics_yolo" and present:
        roles = {role: path for role, path in present}
        selected_path = roles.get("balloon") or roles.get("body")
        details = {
            **result.active_model_details,
            "active_model_id": "setup_direct_model_path",
            "adapter_mode": "ultralytics_yolo",
            "model_type": "combined_detector" if len(present) > 1 else f"{present[0][0]}_detector",
            "model_file": Path(selected_path).name if selected_path else None,
            "file_path": selected_path,
            "class_mapping_status": "direct_path_present_unverified",
            "loaded": not inference_failed,
            "last_test_status": "live_inference_failed" if inference_failed else ("live_inference_active" if pipeline_status.latest_frame_id > 0 else "awaiting_first_frame"),
        }
        warnings = [
            item for item in result.warnings
            if item != "Model reload required for Ultralytics YOLO adapter." and not item.startswith("model_missing:")
        ]
        errors = [item for item in result.errors if item != "active_yolo_model_file_missing"]
        if inference_failed:
            errors.append(next(item for item in pipeline_status.warnings if str(item).startswith("ultralytics_inference_failed:")))
        result = result.model_copy(update={
            "active_model_summary": {
                **result.active_model_summary,
                "active_body_model_id": "setup_body_path" if "body" in roles else None,
                "active_balloon_model_id": "setup_balloon_path" if "balloon" in roles else None,
            },
            "active_model_details": details,
            "effective_adapter": "ultralytics_yolo",
            "runtime_source": "setup_direct_model_path",
            "test_adapter_active": False,
            "reload_required": False,
            "adapter_available": not inference_failed,
            "warnings": warnings,
            "errors": errors,
        })
    return result


@vision_router.get("/runtime/presets", response_model=list[VisionRuntimePreset])
def vision_runtime_presets(runtime: RuntimeState = Depends(get_runtime)) -> list[VisionRuntimePreset]:
    return runtime.vision_runtime.presets()


@vision_router.post("/runtime/apply-preset", response_model=VisionRuntimeApplyResult)
def vision_runtime_apply_preset(request: VisionRuntimePresetApplyRequest, runtime: RuntimeState = Depends(get_runtime)) -> VisionRuntimeApplyResult:
    if runtime.stage3_competition_profile_locked():
        raise HTTPException(status_code=409, detail="A3_PROFILE_LOCKED")
    status = runtime.vision_pipeline.status()
    return runtime.vision_runtime.apply_preset(request.preset_name, current_fps=status.fps, latest_latency_ms=status.latest_latency_ms)


@vision_router.post("/runtime/save-preset")
def vision_runtime_save_preset(request: VisionRuntimePresetSaveRequest, runtime: RuntimeState = Depends(get_runtime)) -> dict:
    return runtime.vision_runtime.save_preset(request.preset)


@vision_router.post("/runtime/verify-active", response_model=VisionRuntimeVerifyResult)
def vision_runtime_verify_active(runtime: RuntimeState = Depends(get_runtime)) -> VisionRuntimeVerifyResult:
    return runtime.vision_runtime.verify_active()


@vision_router.post("/runtime/test-active-model", response_model=VisionRuntimeTestResult)
def vision_runtime_test_active_model(runtime: RuntimeState = Depends(get_runtime)) -> VisionRuntimeTestResult:
    return runtime.vision_runtime.test_active_model()
