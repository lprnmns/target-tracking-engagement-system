from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

from app.schemas.config import AppConfig
from app.schemas.digital_twin import (
    DigitalTwinAsset,
    DigitalTwinAssetsResponse,
    DigitalTwinBBox,
    DigitalTwinCameraState,
    DigitalTwinDevicePose,
    DigitalTwinEngagementState,
    DigitalTwinLatencyMetrics,
    DigitalTwinReplayEvent,
    DigitalTwinReplayGenerateResult,
    DigitalTwinReplaySummary,
    DigitalTwinRuntimeState,
    DigitalTwinSafetyState,
    DigitalTwinSceneNode,
    DigitalTwinState,
    DigitalTwinTargetProjectionEstimate,
    DigitalTwinTargetState,
    DigitalTwinTrackerState,
    DigitalTwinVector3,
)
from app.schemas.log import LogLevel
from app.schemas.vision import BalloonDetection, VisionEvent
from app.services.digital_twin_projection import project_bbox_to_scene
from app.services.log_service import JsonlLogService
from app.services.storage_paths import project_root


FORBIDDEN_DIGITAL_TWIN_ACTIONS = [
    "motor",
    "fire",
    "servo",
    "gpio",
    "pwm",
    "step_dir",
    "serial_tx",
    "hardware_enable",
]


class DigitalTwinService:
    """Read-only telemetry adapter for the Phase 31/32 digital twin.

    The service deliberately reads already-materialized runtime state. It does
    not start cameras, run inference, open serial ports, or send commands.
    """

    def __init__(self, config: AppConfig, logger: JsonlLogService, reports_dir: Path | None = None) -> None:
        self.config = config
        self.logger = logger
        self.reports_dir = reports_dir or project_root() / "reports"
        self._run_id = "phase32_fixture_balloon_tracking_001"
        self._last_state_log_at = 0.0
        self._last_replay_log_at = 0.0
        self._last_panel_render_log_at = 0.0
        self._observer_cache_lock = threading.Lock()
        self._observer_cached_state: DigitalTwinState | None = None
        self._observer_cached_at = 0.0

    def state_cached(self, runtime: Any, max_age_s: float = 0.075) -> DigitalTwinState:
        """Share one read-only snapshot across concurrent cockpit observers.

        The physical pose path does not use this cache. It only collapses a
        burst of identical HTTP observers (for example several stale browser
        tabs) so they cannot multiply service/log work and starve camera or
        serial maintenance. A visible 10 Hz cockpit remains within one frame
        of its original update cadence.
        """
        now = time.monotonic()
        cached = self._observer_cached_state
        if cached is not None and now - self._observer_cached_at <= max_age_s:
            return cached
        with self._observer_cache_lock:
            now = time.monotonic()
            cached = self._observer_cached_state
            if cached is not None and now - self._observer_cached_at <= max_age_s:
                return cached
            state = self.state(runtime)
            self._observer_cached_state = state
            self._observer_cached_at = now
            return state

    def state(self, runtime: Any) -> DigitalTwinState:
        now_ms = int(time.time() * 1000)
        serial_status = self._serial_status(runtime)
        hardware_status = self._hardware_status(runtime)
        motion = self._motion_pose(runtime, hardware_status)
        telemetry_protocol = self._telemetry_protocol(runtime, serial_status, motion)
        motion = self._apply_protocol_pose(motion, telemetry_protocol)
        camera = self._camera_state(runtime)
        vision_event = self._latest_vision_event(runtime)
        decision = getattr(runtime.decision_engine, "latest_decision", None)
        allow_fixture = not camera.real_camera_stream
        target_projection_estimates = self._target_projection_estimates(
            vision_event, camera.width, camera.height, decision, allow_fixture=allow_fixture
        )
        target = self._target_state(
            vision_event, camera.width, camera.height, decision, target_projection_estimates,
            allow_fixture=allow_fixture,
        )
        tracker = self._tracker_state(runtime, target)
        system_state = runtime.system_state()
        person_status = runtime.person_safety.status()
        runtime_state = self._runtime_state(runtime, serial_status, camera, vision_event, decision)
        safety = DigitalTwinSafetyState(
            e_stop="released" if not getattr(motion, "estop_state", False) else "active",
            fire_policy=str(system_state.fire_policy),
            hardware_enabled=bool(self.config.system.hardware_enabled),
            physical_command_enabled=bool(self.config.hardware.physical_command_enabled),
            forbidden_actions=FORBIDDEN_DIGITAL_TWIN_ACTIONS,
        )
        engagement = DigitalTwinEngagementState(
            fire_allowed=bool(decision and str(decision.decision_state) == "FIRE_READY" and not person_status.person_detected),
            fire_gate_state="FIRE_BLOCKED" if person_status.person_detected else str(getattr(decision, "decision_state", "FIRE_BLOCKED")),
            fire_blocked_reason="PERSON_DETECTED" if person_status.person_detected else self._fire_blocked_reason(decision),
            last_event="runtime_read_only_mirror",
            target_loss_after_engagement=not target.detected,
            magazine_remaining=getattr(serial_status, "magazine_remaining", None),
            person_safety_blocked=person_status.person_detected,
            person_detection_confidence=person_status.last_detection_confidence,
        )
        live_inputs = camera.real_camera_stream or bool(getattr(telemetry_protocol, "pico_connected", False)) or motion.pose_source != "fixture"
        mode = "live_read_only" if live_inputs else "fixture"
        source = "runtime_read_only_adapter" if mode == "live_read_only" else "fixture_deterministic_mock"
        state = DigitalTwinState(
            timestamp_ms=now_ms,
            mode=mode,
            feature_enabled=bool(self.config.digital_twin.enabled),
            source=source,
            camera_fov_horizontal_deg=float(self.config.digital_twin.camera_fov_horizontal_deg),
            camera_fov_vertical_deg=float(self.config.digital_twin.camera_fov_vertical_deg),
            camera_to_launcher_offset_z_mm=float(self.config.digital_twin.camera_to_launcher_offset_z_mm),
            camera_to_launcher_offset_y_mm=float(self.config.digital_twin.camera_to_launcher_offset_y_mm),
            projection_is_calibrated=False,
            depth_source=target_projection_estimates[0].depth_source if target_projection_estimates else "bbox_area_relative_estimate",
            device_pose=motion,
            camera=camera,
            target=target,
            target_projection_estimates=target_projection_estimates,
            tracker=tracker,
            engagement=engagement,
            runtime=runtime_state,
            telemetry_protocol=telemetry_protocol,
            safety=safety,
            scene_nodes=self.scene_nodes(),
            evidence={
                "contract": "reports/digital_twin_live_state_contract.json",
                "asset_inventory": "reports/target_model_asset_inventory.md",
                "phase": "35",
                "projection_contract": "reports/digital_twin_projection_contract.json",
                "no_physical_command_generated": True,
            },
            no_physical_command_generated=True,
        )
        self._log_state_stream_mapped(state)
        self._log_telemetry_pose_mapped(state)
        return state

    def assets(self) -> DigitalTwinAssetsResponse:
        model_files = self._model_asset_files()
        manifest = self._asset_manifest()
        preferred_manifest = str(manifest.get("preferred_browser_asset") or "")
        preferred = preferred_manifest if preferred_manifest.lower().endswith((".glb", ".gltf")) else next((self._public_asset_path(item) for item in model_files if item.lower().endswith((".glb", ".gltf"))), None)
        stl_asset = manifest.get("selected_asset_path") if manifest.get("selected_asset_type") in {"REAL_STL", "REAL_STL_GEOMETRY_GLB"} else None
        selected_asset_type = str(manifest.get("selected_asset_type") or ("REAL_GLB" if preferred else "PROCEDURAL_FALLBACK"))
        selected_asset_path = str(preferred or stl_asset or manifest.get("selected_asset_path") or "/assets/digital-twin/procedural_turret_fallback")
        fallback_reason = str(
            manifest.get("fallback_reason")
            or ("Phase 55 kinematic STEP GLB active; visualization-only yaw/pitch metadata loaded" if selected_asset_type == "REAL_STEP_KINEMATIC_GLB" else "Phase 54 STEP HiFi GLB active; no fallback used" if selected_asset_type == "REAL_STEP_HIFI_GLB" else "Phase 54 hybrid fidelity GLB active; no physical command path" if selected_asset_type == "HYBRID_FIDELITY_GLB" else "colored STEP GLB active; no fallback used" if selected_asset_type == "REAL_STEP_GLB" else "browser_friendly_asset_available" if preferred else "model asset unavailable; red/white procedural turret fallback rendered")
        )
        device_status = "available" if selected_asset_type in {"REAL_STEP_KINEMATIC_GLB", "REAL_STEP_GLB", "REAL_STEP_HIFI_GLB", "HYBRID_FIDELITY_GLB", "REAL_STL_GEOMETRY_GLB", "REAL_GLB", "REAL_STL"} else "placeholder"
        return DigitalTwinAssetsResponse(
            device_model=DigitalTwinAsset(
                class_id="istiklal_c2_rig",
                label="ISTIKLAL C2 pan/tilt launcher visual rig",
                model_path=selected_asset_path,
                source_file=str(manifest.get("source_stl_path") or manifest.get("source_cad_path") or "") or None,
                source_sha256=str(manifest.get("source_stl_sha256") or manifest.get("source_cad_sha256") or "") or None,
                source_size_bytes=int(manifest.get("derived_web_asset_size_bytes") or manifest.get("glb_size_bytes") or 0) or None,
                status=device_status,
                notes=fallback_reason,
            ),
            target_assets=[
                DigitalTwinAsset(
                    class_id="ballistic_missile",
                    label="Balistik Füze",
                    model_path="/assets/targets/ballistic_missile.glb",
                    source_file="object_18.model",
                    source_sha256="5a87deb8025dc0124e24c73937edb1b261087bf78846574288d07ceef4730a08",
                    source_size_bytes=1593671,
                    confidence_min=0.0,
                    status=self._asset_status("frontend/public/assets/targets/ballistic_missile.glb"),
                    notes="Supplied 3MF target, 500 mm reference span; browser LOD visual.",
                ),
                DigitalTwinAsset(
                    class_id="helicopter",
                    label="Helikopter",
                    model_path="/assets/targets/helicopter.glb",
                    source_file="object_19.model",
                    source_sha256="fc1567c61bfdd49f5900f07abf327f9f45276a63dcffacd375627e9d98ea802e",
                    source_size_bytes=50215966,
                    confidence_min=0.0,
                    status=self._asset_status("frontend/public/assets/targets/helicopter.glb"),
                    notes="Supplied 3MF target, 583 mm reference span; browser LOD visual.",
                ),
                DigitalTwinAsset(
                    class_id="f16",
                    label="Savaş Uçağı",
                    model_path="/assets/targets/f16.glb",
                    source_file="object_20.model",
                    source_sha256="20d7e7ac343e448a95db32cb886f460584539cb789860aa60986c0dc599dc9bf",
                    source_size_bytes=57120249,
                    confidence_min=0.0,
                    status=self._asset_status("frontend/public/assets/targets/f16.glb"),
                    notes="Supplied 3MF target, 500 mm reference span; browser LOD visual.",
                ),
                DigitalTwinAsset(
                    class_id="mini_micro_uav",
                    label="Mini/Mikro İHA",
                    model_path="/assets/targets/mini_micro_uav.glb",
                    source_file="object_21.model",
                    source_sha256="2ddfac214687971b236b516aa99850970d3dce2613c0ea1c734be807e8e45f0b",
                    source_size_bytes=58943183,
                    confidence_min=0.0,
                    status=self._asset_status("frontend/public/assets/targets/mini_micro_uav.glb"),
                    notes="Supplied 3MF target, 375 mm reference span; browser LOD visual.",
                ),
                DigitalTwinAsset(
                    class_id="balloon_fallback",
                    label="Balloon fallback visual",
                    model_path="/models/targets/balloon_fallback.glb",
                    status=self._asset_status("frontend/public/models/targets/balloon_fallback.glb"),
                    notes="Procedural sphere is used when no GLB is available.",
                ),
                DigitalTwinAsset(
                    class_id="unknown_target",
                    label="Unknown target fallback",
                    model_path="/models/targets/unknown_target.glb",
                    status=self._asset_status("frontend/public/models/targets/unknown_target.glb"),
                    notes="Used when class mapping is absent or below confidence threshold.",
                ),
            ],
            available_model_files=model_files,
            preferred_browser_asset=preferred,
            selected_asset_type=selected_asset_type,
            selected_asset_path=selected_asset_path,
            source_cad_path=str(manifest.get("source_cad_path") or "") or None,
            conversion_status=str(manifest.get("conversion_status") or "not_evaluated"),
            scale_units=str(manifest.get("scale_units") or "scene_units"),
            coordinate_notes=str(manifest.get("coordinate_notes") or ""),
            asset_transform=dict(manifest.get("asset_transform") or {}),
            camera_mount_reference_available=bool(manifest.get("camera_mount_reference_available", False)),
            launcher_axis_reference_available=bool(manifest.get("launcher_axis_reference_available", False)),
            asset_fallback_reason=fallback_reason,
        )

    def scene_nodes(self) -> list[DigitalTwinSceneNode]:
        return [
            DigitalTwinSceneNode(id="base_static", label="Base", kind="static"),
            DigitalTwinSceneNode(id="yaw_root", label="Yaw axis", kind="axis", parent="base_static", transform_source="device_pose.pan_deg"),
            DigitalTwinSceneNode(id="pitch_root", label="Pitch axis", kind="axis", parent="yaw_root", transform_source="device_pose.tilt_deg"),
            DigitalTwinSceneNode(id="camera_mount", label="USB camera", kind="sensor", parent="pitch_root"),
            DigitalTwinSceneNode(id="launcher_visual", label="Launcher visual", kind="actuator_visual", parent="pitch_root"),
            DigitalTwinSceneNode(id="trigger_visual", label="Servo/laser indicator", kind="actuator_visual", parent="pitch_root", transform_source="device_pose.servo_angle_deg"),
            DigitalTwinSceneNode(id="target_visual", label="Tracked target", kind="target", transform_source="target.estimated_scene_position_m"),
            DigitalTwinSceneNode(id="safety_zone", label="Read-only safety boundary", kind="evidence_overlay"),
        ]

    def latest_replay(self) -> DigitalTwinReplaySummary:
        events = self._fixture_events()
        replay = DigitalTwinReplaySummary(
            run_id=self._run_id,
            source="fixture_deterministic_mock",
            duration_ms=events[-1].t_ms if events else 0,
            event_count=len(events),
            events=events,
        )
        self._log_replay_loaded(replay)
        return replay

    def panel_rendered(self) -> dict[str, bool | str]:
        now = time.monotonic()
        payload: dict[str, bool | str] = {
            "accepted": True,
            "event": "digital_twin.panel_rendered",
            "canonical_safety_wording": "no_physical_command_generated=true; panel rendered read-only; no command path exists.",
            "no_physical_command_generated": True,
        }
        if now - self._last_panel_render_log_at >= 2.0:
            self._last_panel_render_log_at = now
            self.logger.emit(LogLevel.INFO, "DIGITAL_TWIN", "digital_twin.panel_rendered; no physical command generated", payload)
        return payload

    def generate_replay_report(self) -> DigitalTwinReplayGenerateResult:
        replay = self.latest_replay()
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        path = self.reports_dir / "digital_twin_phase32_replay_fixture.json"
        payload = replay.model_dump(mode="json")
        payload["no_physical_command_generated"] = True
        payload["digital_twin_read_only"] = True
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        self.logger.emit(
            LogLevel.INFO,
            "DIGITAL_TWIN",
            "Phase 32 replay fixture generated without physical command",
            {"path": str(path), "event_count": replay.event_count, "no_physical_command_generated": True},
        )
        try:
            report_path = str(path.relative_to(project_root()))
        except ValueError:
            report_path = str(path)
        return DigitalTwinReplayGenerateResult(
            run_id=replay.run_id,
            report_path=report_path,
            event_count=replay.event_count,
        )

    def _motion_pose(self, runtime: Any, hardware_status: Any | None = None) -> DigitalTwinDevicePose:
        telemetry = getattr(hardware_status, "telemetry", None)
        if getattr(hardware_status, "telemetry_received", False) and telemetry is not None:
            pan_steps = int(getattr(telemetry, "pan_position_steps", 0))
            tilt_steps = int(getattr(telemetry, "tilt_position_steps", 0))
            return DigitalTwinDevicePose(
                pan_deg=round(pan_steps / max(float(self.config.motion.pan_steps_per_degree), 1.0), 3),
                tilt_deg=round(tilt_steps / max(float(self.config.motion.tilt_steps_per_degree), 1.0), 3),
                servo_angle_deg=0.0,
                pose_quality="runtime",
                pose_source="telemetry",
                pan_steps=pan_steps,
                tilt_steps=tilt_steps,
                source="hardware_read_only_telemetry",
            )
        try:
            runtime.command_gateway.refresh_motion_estimate(runtime)
            status = runtime.motion.status()
        except Exception:
            return DigitalTwinDevicePose(pose_quality="unavailable", pose_source="fixture", source="motion_status_unavailable")
        gateway_estimate = str(status.last_command or "").startswith("gateway_")
        return DigitalTwinDevicePose(
            pan_deg=float(status.pan_position_deg),
            tilt_deg=float(status.tilt_position_deg),
            servo_angle_deg=0.0,
            pose_quality="estimated",
            pose_source="gateway_open_loop_estimate" if gateway_estimate else "tracker_estimate",
            pan_steps=int(status.pan_position_steps),
            tilt_steps=int(status.tilt_position_steps),
            source="motion_status_gateway_open_loop_estimate" if gateway_estimate else "motion_status_tracker_estimate",
        )

    def _apply_protocol_pose(self, pose: DigitalTwinDevicePose, telemetry: Any) -> DigitalTwinDevicePose:
        if getattr(telemetry, "pose_source", "") != "telemetry":
            return pose
        pan_steps = getattr(telemetry, "x_steps", None)
        tilt_steps = getattr(telemetry, "y_steps", None)
        pan_deg = getattr(telemetry, "pan_deg", None)
        tilt_deg = getattr(telemetry, "tilt_deg", None)
        if pan_deg is None and pan_steps is not None:
            pan_deg = float(pan_steps) / max(float(self.config.motion.pan_steps_per_degree), 1.0)
        if tilt_deg is None and tilt_steps is not None:
            tilt_deg = float(tilt_steps) / max(float(self.config.motion.tilt_steps_per_degree), 1.0)
        if pan_deg is None and tilt_deg is None:
            return pose
        return DigitalTwinDevicePose(
            pan_deg=round(float(pan_deg if pan_deg is not None else pose.pan_deg), 3),
            tilt_deg=round(float(tilt_deg if tilt_deg is not None else pose.tilt_deg), 3),
            servo_angle_deg=pose.servo_angle_deg,
            pose_quality="runtime",
            pose_source="telemetry",
            pan_steps=int(pan_steps if pan_steps is not None else pose.pan_steps),
            tilt_steps=int(tilt_steps if tilt_steps is not None else pose.tilt_steps),
            source="istiklal_serial_protocol_v1_read_only",
        )

    def _camera_state(self, runtime: Any) -> DigitalTwinCameraState:
        try:
            status = runtime.camera_runtime.status()
            profile = status.profile
            is_real = bool(status.is_real_camera_evidence)
            return DigitalTwinCameraState(
                selected_camera=status.selected_camera,
                selected_device=status.selected_device,
                device_path=status.selected_device or profile.device_path or profile.stable_path or profile.device_id,
                source_type=profile.source_type,
                running=bool(status.running),
                real_camera_stream=is_real,
                is_real_camera_evidence=is_real,
                width=int(status.actual_width or status.requested_width or profile.width),
                height=int(status.actual_height or status.requested_height or profile.height),
                fps=float(status.actual_fps_measured or status.actual_fps or status.requested_fps),
                frame_age_ms=status.last_frame_age_ms,
                source_mode=status.source_mode,
                selected_backend=status.selected_backend,
                input_format=status.input_format,
                last_capture_error=status.last_capture_error,
                is_external_usb_camera=status.is_external_usb_camera,
                is_laptop_camera=status.is_laptop_camera,
                hardware_presence_note=status.hardware_presence_note,
            )
        except Exception:
            return DigitalTwinCameraState()

    def _model_asset_files(self) -> list[str]:
        root = project_root()
        candidates: list[str] = []
        seen: set[str] = set()
        excluded_parts = {".git", "node_modules", "dist", ".venv", "__pycache__"}
        for base in [
            root / "frontend" / "public",
            root / "frontend" / "src" / "assets",
            root / "assets",
            root / "models",
            root / "fixtures",
            root / "ktr_alignment_outputs",
            root,
        ]:
            if not base.exists():
                continue
            if base == root:
                for path in base.iterdir():
                    if path.is_file() and path.suffix.lower() in {".glb", ".gltf", ".obj", ".stl", ".step", ".stp", ".fcstd"}:
                        rel = str(path.relative_to(root))
                        if rel not in seen:
                            seen.add(rel)
                            candidates.append(rel)
                continue
            for path in base.rglob("*"):
                if any(part in excluded_parts for part in path.relative_to(root).parts):
                    continue
                if path.suffix.lower() in {".glb", ".gltf", ".obj", ".stl", ".step", ".stp", ".fcstd"}:
                    try:
                        rel = str(path.relative_to(root))
                    except ValueError:
                        rel = str(path)
                    if rel in seen:
                        continue
                    seen.add(rel)
                    candidates.append(rel)
        return sorted(candidates)

    def _asset_manifest(self) -> dict[str, Any]:
        manifest_path = project_root() / "frontend" / "public" / "assets" / "digital-twin" / "asset_manifest.json"
        try:
            return json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            return {
                "selected_asset_type": "PROCEDURAL_FALLBACK",
                "selected_asset_path": "/assets/digital-twin/procedural_turret_fallback",
                "fallback_reason": "asset manifest unavailable; red/white procedural turret fallback rendered",
                "no_physical_command_generated": True,
            }

    def _public_asset_path(self, path: str) -> str:
        if path.startswith("/"):
            return path
        prefix = "frontend/public"
        if path.startswith(prefix):
            return "/" + path[len(prefix):].lstrip("/")
        return path

    def _serial_status(self, runtime: Any) -> Any | None:
        try:
            return runtime.serial.status()
        except Exception:
            return None

    def _hardware_status(self, runtime: Any) -> Any | None:
        try:
            return runtime.hardware.status(mock_pico_active=runtime.pico.status().mock_mode)
        except Exception:
            return None

    def _tracker_state(self, runtime: Any, target: DigitalTwinTargetState) -> DigitalTwinTrackerState:
        try:
            status = runtime.auto_tracker.status()
            update = status.last_update
            return DigitalTwinTrackerState(
                tracking_enabled=bool(status.active),
                state=str(status.state),
                error_x_px=float(update.error_x_px if update else 0.0),
                error_y_px=float(update.error_y_px if update else 0.0),
                latency_ms=float(update.dt * 1000.0) if update else None,
                command_rate_hz=float(status.command_rate_hz),
                max_speed=int(status.max_speed),
            )
        except Exception:
            return DigitalTwinTrackerState(
                tracking_enabled=False,
                state="FIXTURE",
                error_x_px=-24.0 if target.detected else 0.0,
                error_y_px=12.0 if target.detected else 0.0,
                latency_ms=16.7,
                command_rate_hz=float(self.config.digital_twin.state_refresh_hz),
                max_speed=int(self.config.tracking.max_speed),
                source="fixture",
            )

    def _latest_vision_event(self, runtime: Any) -> VisionEvent | None:
        vision = getattr(getattr(runtime, "vision_pipeline", None), "vision", None)
        event = getattr(vision, "latest_event", None)
        if event is None:
            return None
        # A previously detected target must disappear from the scene when
        # inference polling stops or the frame becomes stale.
        if time.time() * 1000 - event.timestamp_ms > 1000:
            return None
        return event

    def _target_state(
        self,
        event: VisionEvent | None,
        width: int,
        height: int,
        decision: Any | None = None,
        projections: list[DigitalTwinTargetProjectionEstimate] | None = None,
        allow_fixture: bool = True,
    ) -> DigitalTwinTargetState:
        if event and event.balloon_detections:
            detection = max(event.balloon_detections, key=lambda det: det.confidence)
            projection = self._projection_for_target(projections or [], detection.id)
            return self._target_from_detection(detection, width, height, event.source, decision, projection)
        if not allow_fixture:
            return DigitalTwinTargetState(
                detected=False,
                selected_target_id=getattr(decision, "selected_balloon_detection_id", None),
                track_id=None,
                class_id="no_live_target",
                class_label="No live target",
                confidence=0.0,
                source="vision_no_target",
            )
        projection = projections[0] if projections else self._fixture_projection(width, height, decision)
        bbox = projection.bbox
        return DigitalTwinTargetState(
            detected=True,
            selected_target_id=getattr(decision, "selected_balloon_detection_id", None),
            track_id=projection.target_id or 1,
            class_id="balloon_fallback",
            class_label="Balloon fallback fixture",
            confidence=0.72,
            bbox=bbox,
            center_px=DigitalTwinVector3(x=float(bbox.x + bbox.w / 2.0), y=float(bbox.y + bbox.h / 2.0), z=0.0),
            normalized_x=projection.normalized_screen_x,
            normalized_y=-projection.normalized_screen_y,
            estimated_scene_position_m=projection.scene_position_m,
            source="fixture",
        )

    def _target_from_detection(
        self,
        detection: BalloonDetection,
        width: int,
        height: int,
        source: str,
        decision: Any | None = None,
        projection: DigitalTwinTargetProjectionEstimate | None = None,
    ) -> DigitalTwinTargetState:
        frame_cx = max(width / 2.0, 1.0)
        frame_cy = max(height / 2.0, 1.0)
        norm_x = (detection.center_x - frame_cx) / frame_cx
        norm_y = (detection.center_y - frame_cy) / frame_cy
        estimated = projection.scene_position_m if projection else DigitalTwinVector3(x=round(norm_x * 1.2, 3), y=round(-norm_y * 0.8, 3), z=-5.0)
        return DigitalTwinTargetState(
            detected=True,
            selected_target_id=getattr(decision, "selected_balloon_detection_id", None),
            track_id=detection.id,
            class_id="balloon_fallback",
            class_label="Balloon detection",
            confidence=detection.confidence,
            bbox=DigitalTwinBBox(**detection.bbox.model_dump()),
            center_px=DigitalTwinVector3(x=float(detection.center_x), y=float(detection.center_y), z=0.0),
            normalized_x=round(norm_x, 4),
            normalized_y=round(norm_y, 4),
            estimated_scene_position_m=estimated,
            source=source,
        )

    def _target_projection_estimates(
        self,
        event: VisionEvent | None,
        width: int,
        height: int,
        decision: Any | None = None,
        allow_fixture: bool = True,
    ) -> list[DigitalTwinTargetProjectionEstimate]:
        selected_id = getattr(decision, "selected_balloon_detection_id", None)
        estimates: list[DigitalTwinTargetProjectionEstimate] = []
        if event:
            for detection in event.balloon_detections:
                estimates.append(
                    self._projection_from_bbox(
                        bbox=detection.bbox.model_dump(),
                        width=width,
                        height=height,
                        class_name="balloon",
                        confidence=detection.confidence,
                        target_id=detection.id,
                        selected=selected_id == detection.id,
                    )
                )
            for body in event.body_detections:
                estimates.append(
                    self._projection_from_bbox(
                        bbox=body.bbox.model_dump(),
                        width=width,
                        height=height,
                        class_name=body.class_name,
                        confidence=body.confidence,
                        target_id=body.id,
                        selected=False,
                    )
                )
        if estimates:
            return estimates
        return [self._fixture_projection(width, height, decision)] if allow_fixture else []

    def _fixture_projection(self, width: int, height: int, decision: Any | None = None) -> DigitalTwinTargetProjectionEstimate:
        return self._projection_from_bbox(
            bbox={
                "x": int(width * 0.66),
                "y": int(height * 0.34),
                "w": max(32, int(width * 0.1)),
                "h": max(32, int(height * 0.18)),
            },
            width=width,
            height=height,
            class_name="balloon_fixture",
            confidence=0.72,
            target_id=1,
            selected=getattr(decision, "selected_balloon_detection_id", None) in {None, 1},
        )

    def _projection_from_bbox(
        self,
        *,
        bbox: dict[str, Any],
        width: int,
        height: int,
        class_name: str,
        confidence: float,
        target_id: int | None,
        selected: bool,
    ) -> DigitalTwinTargetProjectionEstimate:
        return project_bbox_to_scene(
            bbox=bbox,
            frame_width=width,
            frame_height=height,
            class_name=class_name,
            confidence=confidence,
            target_id=target_id,
            selected=selected,
            camera_fov_horizontal_deg=float(self.config.digital_twin.camera_fov_horizontal_deg),
            camera_fov_vertical_deg=float(self.config.digital_twin.camera_fov_vertical_deg),
            camera_to_launcher_offset_z_mm=float(self.config.digital_twin.camera_to_launcher_offset_z_mm),
            camera_to_launcher_offset_y_mm=float(self.config.digital_twin.camera_to_launcher_offset_y_mm),
            known_target_size_mm={
                "balloon": float(self.config.digital_twin.balloon_diameter_mm),
                "balloon_fixture": float(self.config.digital_twin.balloon_diameter_mm),
                "balloon_replay": float(self.config.digital_twin.balloon_diameter_mm),
                "ballistic_missile": 500.0,
                "helicopter": 583.0,
                "f16": 500.0,
                "mini_micro_uav": 375.0,
            }.get(class_name.lower()),
        )

    @staticmethod
    def _projection_for_target(
        projections: list[DigitalTwinTargetProjectionEstimate],
        target_id: int,
    ) -> DigitalTwinTargetProjectionEstimate | None:
        for projection in projections:
            if projection.target_id == target_id:
                return projection
        return projections[0] if projections else None

    def _runtime_state(
        self,
        runtime: Any,
        serial_status: Any | None,
        camera: DigitalTwinCameraState,
        vision_event: VisionEvent | None,
        decision: Any | None,
    ) -> DigitalTwinRuntimeState:
        tracking_update = getattr(runtime.tracking_loop, "last_update", None)
        latency = DigitalTwinLatencyMetrics(
            camera_frame_age_ms=camera.frame_age_ms,
            inference_ms=float(vision_event.inference_ms) if vision_event else None,
            tracking_loop_ms=round(float(tracking_update.dt) * 1000.0, 3) if tracking_update else None,
            serial_ack_rtt_ms=getattr(serial_status, "last_command_rtt_ms", None),
            total_pipeline_ms=(
                float(vision_event.total_latency_ms)
                + float(getattr(serial_status, "last_command_rtt_ms", 0) or 0)
            ) if vision_event else None,
        )
        pico_connection_state = str(getattr(serial_status, "connection_state", "DISCONNECTED"))
        return DigitalTwinRuntimeState(
            queue_length=int(getattr(serial_status, "command_queue_depth", 0) or 0),
            camera_mode=camera.source_type,
            pico_connection_state=pico_connection_state,
            selected_target_id=getattr(decision, "selected_balloon_detection_id", None),
            latency=latency,
        )

    def _telemetry_protocol(self, runtime: Any, serial_status: Any | None, motion: DigitalTwinDevicePose):
        legacy = runtime.pico.protocol_latest_telemetry()
        if getattr(legacy, "pose_source", "") == "telemetry" and (
            getattr(legacy, "pan_deg", None) is not None or getattr(legacy, "x_steps", None) is not None
        ):
            return legacy
        if serial_status is None:
            return legacy
        state = str(getattr(serial_status, "connection_state", "DISCONNECTED"))
        connected = bool(
            getattr(serial_status, "pico_verified", False)
            and getattr(serial_status, "transport_source", "") == "real_serial"
            and state != "FAULT"
        )
        heartbeat_age = getattr(serial_status, "heartbeat_age_ms", None)
        if heartbeat_age is None:
            heartbeat_age = getattr(serial_status, "last_command_age_ms", None)
        heartbeat_timeout = int(getattr(serial_status, "heartbeat_timeout_ms", 2000) or 2000)
        fresh = connected and heartbeat_age is not None and heartbeat_age <= heartbeat_timeout
        last_rx = getattr(serial_status, "last_rx", None) or {}
        return legacy.model_copy(update={
            "protocol_name": "Pico Arduino Raw CommandGateway",
            "pico_connected": connected,
            "telemetry_fresh": fresh,
            # Raw firmware provides health/ACK but no absolute encoder pose.
            "telemetry_missing": True,
            "port": getattr(runtime.config.serial, "port", None),
            "last_heartbeat_age_ms": heartbeat_age,
            "last_packet_type": "RAW_ACK" if last_rx else None,
            "pan_deg": motion.pan_deg,
            "tilt_deg": motion.tilt_deg,
            "x_steps": motion.pan_steps,
            "y_steps": motion.tilt_steps,
            "driver_enabled": bool(getattr(runtime.command_gateway, "driver_enabled", False)),
            "pose_source": motion.pose_source,
            "packet_parse_status": "gateway_raw_ack" if connected else "no_packet",
            "crc_status": "not_applicable_raw_ascii",
            "physical_tx_disabled": not bool(getattr(serial_status, "physical_command_enabled", False)),
            "serial_tx_enabled": bool(getattr(serial_status, "real_serial_enabled", False)),
            "physical_command_enabled": bool(getattr(serial_status, "physical_command_enabled", False)),
            "updated_at": time.time(),
        })

    @staticmethod
    def _fire_blocked_reason(decision: Any | None) -> str:
        if decision is None:
            return "decision_unavailable"
        if getattr(decision, "decision_state", "") == "FIRE_READY":
            return "fire_gate_ready_read_only"
        reasons = getattr(decision, "blocking_reasons", []) or []
        return str(reasons[0]) if reasons else "fire_gate_not_ready"

    def _log_state_stream_mapped(self, state: DigitalTwinState) -> None:
        now = time.monotonic()
        if now - self._last_state_log_at < 1.0:
            return
        self._last_state_log_at = now
        self.logger.emit(
            LogLevel.INFO,
            "DIGITAL_TWIN",
            "digital_twin.state_stream_mapped; no physical command generated",
            {
                "pose_source": state.device_pose.pose_source,
                "mode": state.mode,
                "target_source": state.target.source,
                "projection_count": len(state.target_projection_estimates),
                "projection_is_calibrated": state.projection_is_calibrated,
                "queue_length": state.runtime.queue_length,
                "fire_gate_state": state.engagement.fire_gate_state,
                "no_physical_command_generated": True,
            },
        )

    def _log_replay_loaded(self, replay: DigitalTwinReplaySummary) -> None:
        now = time.monotonic()
        if now - self._last_replay_log_at < 1.0:
            return
        self._last_replay_log_at = now
        self.logger.emit(
            LogLevel.INFO,
            "DIGITAL_TWIN",
            "digital_twin.replay_loaded; replay labelled non-live; no physical command generated",
            {
                "run_id": replay.run_id,
                "event_count": replay.event_count,
                "mode": replay.mode,
                "no_physical_command_generated": True,
            },
        )

    def _log_telemetry_pose_mapped(self, state: DigitalTwinState) -> None:
        if state.device_pose.pose_source != "telemetry":
            return
        self.logger.emit(
            LogLevel.INFO,
            "DIGITAL_TWIN",
            "digital_twin.telemetry_pose_mapped; no physical command generated",
            {
                "protocol_name": state.telemetry_protocol.protocol_name,
                "protocol_version": state.telemetry_protocol.protocol_version,
                "pan_deg": state.device_pose.pan_deg,
                "tilt_deg": state.device_pose.tilt_deg,
                "pose_source": state.device_pose.pose_source,
                "no_physical_command_generated": True,
            },
        )

    def _asset_status(self, relative_path: str) -> str:
        return "available" if (project_root() / relative_path).exists() else "planned"

    def _fixture_events(self) -> list[DigitalTwinReplayEvent]:
        points = [
            (0, -8.0, 2.0, -40.0, 14.0),
            (250, -4.0, 1.0, -22.0, 8.0),
            (500, 0.0, 0.0, -6.0, 2.0),
            (750, 3.5, -1.2, 12.0, -4.0),
            (1000, 5.0, -2.0, 18.0, -6.0),
        ]
        events: list[DigitalTwinReplayEvent] = []
        for t_ms, pan, tilt, err_x, err_y in points:
            bbox = DigitalTwinBBox(x=390 + int(t_ms / 20), y=130, w=56 + int(t_ms / 90), h=70 + int(t_ms / 70))
            projection = self._projection_from_bbox(
                bbox=bbox.model_dump(),
                width=640,
                height=360,
                class_name="balloon_replay",
                confidence=0.72,
                target_id=1,
                selected=True,
            )
            target = DigitalTwinTargetState(
                detected=True,
                track_id=1,
                class_id="balloon_fallback",
                class_label="Balloon fallback fixture",
                confidence=0.72,
                bbox=bbox,
                center_px=DigitalTwinVector3(x=float(bbox.x + bbox.w / 2.0), y=float(bbox.y + bbox.h / 2.0), z=0.0),
                normalized_x=projection.normalized_screen_x,
                normalized_y=-projection.normalized_screen_y,
                estimated_scene_position_m=projection.scene_position_m,
                source="fixture",
            )
            events.append(
                DigitalTwinReplayEvent(
                    t_ms=t_ms,
                    target=target,
                    target_projection_estimates=[projection],
                    device_pose=DigitalTwinDevicePose(
                        pan_deg=pan,
                        tilt_deg=tilt,
                        servo_angle_deg=0.0,
                        pose_quality="fixture",
                        pose_source="replay_fixture",
                        pan_steps=int(pan * self.config.motion.pan_steps_per_degree),
                        tilt_steps=int(tilt * self.config.motion.tilt_steps_per_degree),
                        source="fixture",
                    ),
                    tracker=DigitalTwinTrackerState(
                        tracking_enabled=True,
                        state="TRACKING",
                        error_x_px=err_x,
                        error_y_px=err_y,
                        latency_ms=18.0,
                        command_rate_hz=float(self.config.digital_twin.state_refresh_hz),
                        max_speed=int(self.config.tracking.max_speed),
                        source="fixture",
                    ),
                    note="Phase 32 deterministic replay fixture",
                )
            )
        return events
