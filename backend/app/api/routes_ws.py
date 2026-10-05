import asyncio
import time

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.services.camera_status_bridge import camera_status_from_runtime

router = APIRouter(tags=["websocket"])

# The camera preview is delivered independently over HTTP; the WebSocket
# carries detections/tracking state used to draw its overlay.  Five Hz made a
# moving target visibly lag behind the current camera frame, especially over
# a remote Windows/Tailscale link.  Ten Hz keeps the overlay responsive while
# remaining far below the camera/inference rate and avoids sending image data
# through the socket.
# 30 Hz: taret dönerken kokpit bbox'ları 60 FPS kamera akışına yetişsin.
# Eski 0.1 (10 Hz) değerinde kutular hızlı pandomda görsel olarak geride
# kalıyordu; 0.033 MJPEG kare yaşı (~17 ms) ile aynı mertebededir.
WEBSOCKET_PUBLISH_INTERVAL_S = 0.033


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()
    runtime = websocket.app.state.runtime
    seq = 0
    # Most auxiliary events are edge-triggered state changes, but the service
    # objects intentionally retain their last event for REST/UI inspection.
    # Do not resend the same object every 200 ms to every browser.
    last_event_refs: dict[str, object] = {}
    last_warning_keys: set[str] = set()
    last_serial_log_ids: set[int] = set()
    last_motion_command_ids: set[str] = set()
    try:
        while True:
            serial_timeout_entries = runtime.serial.check_timeouts()
            vision_event = runtime.vision_pipeline.latest()
            vision_status = runtime.vision_pipeline.status()
            camera_runtime_status = runtime.camera_runtime.status()
            camera_status = camera_status_from_runtime(runtime, camera_runtime_status)
            # The target registry is also a perception/UI truth source.  When
            # tracking is idle there is no TrackingLoop tick to advance the
            # body↔balloon association, which used to leave Cockpit labels at
            # the generic frame-id fallback until the operator pressed Track.
            # Advance the read-only identity path here; physical motion and
            # FIRE remain exclusively owned by TrackingLoop/CommandGateway.
            if not runtime.auto_tracker.tracking_active:
                # Y17: gosterim govdeleri kimlik/sahiplik yoluna girmez.
                _y17_olay = runtime.vision_pipeline.y17_kontrol_olayi(vision_event) if hasattr(runtime.vision_pipeline, "y17_kontrol_olayi") else vision_event
                preview_tracks = runtime.auto_tracker.multi_target_tracker.update(_y17_olay)
                preview_associations = runtime.association.update(_y17_olay, preview_tracks)
                preview_confirmations = runtime.hit_confirmation.update(_y17_olay, preview_tracks)
                runtime.target_registry.update(
                    _y17_olay,
                    preview_tracks,
                    preview_associations,
                    preview_confirmations,
                    target_policy=runtime.operation.state().target_policy.value,
                )
            decision = runtime.decision_engine.evaluate_cached(runtime)
            pico_status = runtime.pico.status()
            hardware_status = runtime.hardware.status(mock_pico_active=pico_status.mock_mode)
            vision_runtime_status = runtime.vision_runtime.status(
                current_fps=vision_status.fps,
                latest_latency_ms=vision_status.latest_latency_ms,
                camera_source_type=runtime.camera_runtime.profile.source_type,
            )
            system_state_payload = runtime.system_state().model_dump(mode="json")
            decision_payload = decision.model_dump(mode="json")
            pico_telemetry_payload = runtime.pico.telemetry().model_dump(mode="json")
            pico_connection_payload = runtime.pico.last_connection_event.model_dump(mode="json")
            pico_pin_payload = runtime.pico.last_validation.model_dump(mode="json")
            serial_status = runtime.serial.status()
            serial_status_payload = serial_status.model_dump(mode="json")
            motion_status = runtime.motion.status()
            motion_status_payload = motion_status.model_dump(mode="json")
            hardware_status_payload = hardware_status.model_dump(mode="json")
            hardware_telemetry_payload = hardware_status.telemetry.model_dump(mode="json")
            calibration_status_payload = runtime.calibration.status().model_dump(mode="json")
            vision_status_payload = vision_status.model_dump(mode="json")
            vision_event_payload = vision_event.model_dump(mode="json")
            camera_status_payload = camera_status.model_dump(mode="json")
            camera_runtime_payload = camera_runtime_status.model_dump(mode="json")
            vision_runtime_payload = vision_runtime_status.model_dump(mode="json")
            performance_payload = runtime.performance.status(runtime, vision_event=vision_event).model_dump(mode="json")
            mission_payload = runtime.mission.snapshot().model_dump(mode="json")
            safety_state = runtime.safety.state(decision)
            safety_payload = safety_state.model_dump(mode="json")
            tracking_status_payload = runtime.auto_tracker.status().model_dump(mode="json")
            messages = [
                ("system.state", system_state_payload),
                ("decision.updated", decision_payload),
                ("pico.telemetry", pico_telemetry_payload),
                ("pico.connection", pico_connection_payload),
                ("pico.pin_validation", pico_pin_payload),
                ("serial.status", serial_status_payload),
                ("motion.status", motion_status_payload),
                ("hardware.status", hardware_status_payload),
                ("hardware.telemetry", hardware_telemetry_payload),
                ("calibration.status", calibration_status_payload),
                ("vision.status", vision_status_payload),
                ("vision.frame", vision_event_payload),
                ("vision.detections", vision_event_payload),
                ("camera.status", camera_status_payload),
                ("camera.runtime_status", camera_runtime_payload),
                ("vision.runtime_status", vision_runtime_payload),
                ("performance.status", performance_payload),
                ("mission.status", mission_payload),
                (
                    "vision.frame_stats",
                    {
                        "fps": vision_event.fps,
                        "capture_latency_ms": vision_event.capture_ms,
                        "preprocess_latency_ms": vision_event.preprocess_ms,
                        "inference_latency_ms": vision_event.inference_ms,
                        "tracking_latency_ms": runtime.tracking_loop.last_update.dt * 1000.0 if runtime.tracking_loop.last_update is not None else 0.0,
                        "decision_latency_ms": vision_event.postprocess_ms,
                        "pipeline_overhead_ms": vision_event.pipeline_overhead_ms,
                    },
                ),
                ("decision.gates", safety_payload),
                ("safety.gates", safety_payload),
                ("tracking.status", tracking_status_payload),
            ]
            # TrackingLoop owns PID state; WebSocket only publishes its last update.
            if runtime.auto_tracker.tracking_active and runtime.tracking_loop.last_update is not None:
                messages.append(("tracking.update", runtime.tracking_loop.last_update.model_dump(mode="json")))
            messages.extend(runtime.tracking_loop.drain_events())
            current_warning_keys: set[str] = set()
            for warning in vision_event.warnings:
                warning_text = str(warning)
                # Age/sequence suffixes are telemetry detail, not distinct
                # warning conditions. Normalising the key prevents a noisy
                # warning stream when the frame is healthy but cached.
                warning_key = warning_text.split(":", 1)[0] if warning_text.startswith(("using_cached_real_frame:", "cached_real_frame_stale:")) else warning_text
                current_warning_keys.add(warning_key)
                if warning_key not in last_warning_keys:
                    messages.append(("vision.warning", {"frame_id": vision_event.frame_id, "warning": warning_text}))
            last_warning_keys = current_warning_keys

            def append_edge_event(key: str, event) -> None:
                if event is None or last_event_refs.get(key) is event:
                    return
                last_event_refs[key] = event
                messages.append(event)

            append_edge_event("safety", runtime.last_safety_event)
            append_edge_event("motion", runtime.last_motion_event)
            append_edge_event("calibration", runtime.calibration.last_event)
            append_edge_event("color", runtime.color_classifier.last_event)
            append_edge_event("model_registry", runtime.model_registry.last_event)
            append_edge_event("model_packages", runtime.model_packages.last_event)
            append_edge_event("inference_adapter", runtime.inference_adapter.last_event)
            append_edge_event("sessions", runtime.sessions.last_event)
            append_edge_event("annotations", runtime.annotations.last_event)
            append_edge_event("dataset", runtime.dataset.last_event)
            append_edge_event("data_lab", runtime.data_lab.last_event)
            append_edge_event("demo", runtime.demo.last_event)
            append_edge_event("replay", runtime.replay.last_event)
            append_edge_event("engagement_evidence", runtime.engagement_evidence.last_event)
            append_edge_event("self_test", runtime.self_test.last_event)
            append_edge_event("first_run", runtime.first_run.last_event)
            append_edge_event("interface_inventory", runtime.interface_inventory.last_event)
            append_edge_event("report_export", runtime.report_export.last_event)
            append_edge_event("hardware", runtime.hardware.last_event)
            append_edge_event("device_manager", runtime.device_manager.last_event)
            append_edge_event("device_profiles", runtime.device_profiles.last_event)
            append_edge_event("camera_runtime", runtime.camera_runtime.last_event)
            append_edge_event("vision_runtime", runtime.vision_runtime.last_event)
            append_edge_event("vision_surrogate", runtime.vision_surrogate.last_event)
            append_edge_event("release", runtime.release.last_event)
            append_edge_event("mission", runtime.mission.last_event)
            append_edge_event("person_safety", runtime.person_safety.last_event)
            replay_frame = runtime.replay.frame_event()
            if replay_frame is not None:
                messages.append(replay_frame)
            if decision.decision_state == "FAULT":
                messages.append(("safety.fault", decision.model_dump(mode="json")))
            if motion_status.motion_state == "FAULT":
                messages.append(("motion.fault", motion_status_payload))
            for entry in serial_timeout_entries:
                messages.append(("serial.timeout", entry.model_dump(mode="json")))
            for entry in runtime.serial.recent_logs()[-5:]:
                if entry.id in last_serial_log_ids:
                    continue
                last_serial_log_ids.add(entry.id)
                event_type = "serial.log_status" if entry.kind.value == "status" else f"serial.{entry.kind.value}"
                messages.append((event_type, entry.model_dump(mode="json")))
            if len(last_serial_log_ids) > 512:
                last_serial_log_ids = {entry.id for entry in runtime.serial.recent_logs()[-32:]}
            for command in runtime.motion.command_log[-5:]:
                if command.command_id in last_motion_command_ids:
                    continue
                last_motion_command_ids.add(command.command_id)
                payload = command.model_dump(mode="json")
                messages.append(("motion.command_requested", payload))
                if command.command_type in {"stop", "scan_stop"} and command.accepted:
                    messages.append(("motion.stopped", payload))
                elif command.accepted:
                    messages.append(("motion.command_accepted_dry_run", payload))
                else:
                    messages.append(("motion.command_rejected", payload))
            if len(last_motion_command_ids) > 512:
                last_motion_command_ids = {command.command_id for command in runtime.motion.command_log[-32:]}
            envelope_ts = time.time()
            for event_type, payload in messages:
                seq += 1
                # The payloads have already crossed their Pydantic boundary
                # above. Avoid constructing/dumping another Pydantic model
                # for every envelope; the wire contract is unchanged.
                await websocket.send_json({"type": event_type, "ts": envelope_ts, "seq": seq, "payload": payload})
            await asyncio.sleep(WEBSOCKET_PUBLISH_INTERVAL_S)
    except (WebSocketDisconnect, Exception):
        return
