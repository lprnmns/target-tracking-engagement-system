from __future__ import annotations

import json
import struct
import time
from pathlib import Path

from app.schemas.log import LogLevel
from app.services.log_service import JsonlLogService
from app.services.vision_pipeline import worker_idle_delay_s


ROOT = Path(__file__).resolve().parents[2]


def _glb_json(path: Path) -> dict:
    raw = path.read_bytes()
    magic, version, _ = struct.unpack_from("<III", raw, 0)
    assert magic == 0x46546C67 and version == 2
    json_length, json_type = struct.unpack_from("<II", raw, 12)
    assert json_type == 0x4E4F534A
    return json.loads(raw[20:20 + json_length].decode("utf-8").rstrip("\x00 "))


def test_oversized_backend_log_rotates_before_first_runtime_write(tmp_path: Path) -> None:
    active = tmp_path / "backend.jsonl"
    active.write_bytes(b"x" * (1024 * 1024 + 1))

    logger = JsonlLogService(tmp_path, max_bytes=1024 * 1024, backup_count=2)
    logger.emit(LogLevel.INFO, "TEST", "fresh runtime")

    assert (tmp_path / "backend.jsonl.1").stat().st_size > 1024 * 1024
    assert '"message": "fresh runtime"' in active.read_text(encoding="utf-8")


def test_detector_fps_discards_profile_pause_instead_of_showing_false_one_fps() -> None:
    source = (ROOT / "backend/app/services/vision_pipeline.py").read_text(encoding="utf-8")
    assert "if 0 < delta <= 0.5:" in source
    assert "self._worker_fps_samples.clear()" in source
    assert "event.detector_fps or event.fps" in source


def test_cold_start_no_frame_tick_cannot_kill_yolo_worker(client) -> None:
    pipeline = client.app.state.runtime.vision_pipeline
    profile = client.app.state.runtime.vision_runtime.profile

    event = pipeline._ultralytics_event_from_frame(
        frame=None,
        width=1920,
        height=1080,
        started=time.perf_counter(),
        profile=profile,
        warnings=[],
        source="phase155_cold_start_noop",
        camera_source_kind="real_camera",
        camera_device_path="camera-index:0",
        frame_origin="real_capture",
        camera_fps=0.0,
        frame_id=15501,
    )

    assert "real_camera_frame_unavailable" in event.warnings
    assert event.body_detections == []
    assert event.balloon_detections == []


def test_camera_sequence_is_the_only_throttle_at_matching_target_fps() -> None:
    assert worker_idle_delay_s(30, 29.4, 0.022) == 0.0
    assert 0.04 < worker_idle_delay_s(15, 30, 0.022) < 0.05


def test_live_cockpit_tracker_is_locked_to_current_custom() -> None:
    source = (ROOT / "backend/app/api/vision.py").read_text(encoding="utf-8")
    profile_source = (ROOT / "backend/app/services/device_profile_service.py").read_text(encoding="utf-8")
    panel = (ROOT / "frontend/src/components/cockpit/QuickPidFloatingPanel.vue").read_text(encoding="utf-8")
    assert 'profile.model_copy(update={"tracker_enabled": True, "tracker_type": "current_custom"})' in source
    assert 'update={"tracker_enabled": True, "tracker_type": "current_custom"}' in profile_source
    assert "live_runtime_profile" in profile_source
    assert '"locked_profile": "current_custom"' in source
    assert '<section v-if="false" class="quick-section tracker-section"' in panel


def test_operator_draco_asset_preserves_raw_node_contract_without_meshopt_transforms() -> None:
    raw_path = ROOT / "frontend/public/assets/digital-twin/ktr1_kinematic_world_phase55.glb"
    draco_path = ROOT / "frontend/public/assets/digital-twin/ktr1_kinematic_world_phase55_draco.glb"
    raw = _glb_json(raw_path)
    draco = _glb_json(draco_path)

    assert draco_path.stat().st_size < raw_path.stat().st_size * 0.25
    assert [item.get("name") for item in draco["nodes"]] == [item.get("name") for item in raw["nodes"]]
    assert all(not any(key in node for key in ("matrix", "translation", "rotation", "scale")) for node in draco["nodes"])
    assert draco.get("extensionsRequired") == ["KHR_draco_mesh_compression"]


def test_world_floor_has_no_filled_initial_scan_sector() -> None:
    source = (ROOT / "frontend/src/components/digital-twin/DigitalTwinPanel.vue").read_text(encoding="utf-8")
    assert "addScanSector3d(center, basis, floorY)" not in source
    assert "operator-draco-node-safe-20260818" in source
    assert "now - lastDynamicRebuildAt >= 250" in source
    # Throttling a dynamic rebuild must retain the previous scene graph.  The
    # old combined condition fell through to ``disposeChildren`` on every
    # frame between 100 ms rebuilds, visibly blinking FOV edges and targets.
    assert "if (showTacticalOverlays.value) {\n      if (now - lastDynamicRebuildAt >= 250)" in source
    assert "}\n    else if (dynamicGroup?.children?.length) disposeChildren(dynamicGroup)" in source


def test_field_cuda_defaults_avoid_thread_contention_and_shape_autotune_stall() -> None:
    profile = (ROOT / "backend/app/schemas/vision_runtime_settings.py").read_text(encoding="utf-8")
    pipeline = (ROOT / "backend/app/services/vision_pipeline.py").read_text(encoding="utf-8")
    assert "parallel_models: bool = False" in profile
    assert "torch.backends.cudnn.benchmark = False" in pipeline


def test_one_click_holds_field_gpu_clock_only_for_runtime_lifetime() -> None:
    launcher = (ROOT / "release/one_click/launcher.py").read_text(encoding="utf-8")
    assert 'WINDOWS_GPU_MIN_CLOCK_MHZ = 1500' in launcher
    assert 'WINDOWS_GPU_MEMORY_CLOCK_MHZ = 7001' in launcher
    assert '"-lgc"' in launcher
    assert '"-lmc"' in launcher
    assert 'gpu_performance = configure_windows_gpu_performance()' in launcher
    assert '"gpu_performance": gpu_performance or "driver_default"' in launcher
    assert '"-rgc"' in launcher
    assert '"-rmc"' in launcher
    assert launcher.count("restore_windows_gpu_performance()") >= 2


def test_cockpit_pose_poll_and_access_log_do_not_starve_live_inference() -> None:
    cockpit = (ROOT / "frontend/src/views/CockpitView.vue").read_text(encoding="utf-8")
    launcher = (ROOT / "release/one_click/launcher.py").read_text(encoding="utf-8")
    assert "const DIGITAL_TWIN_POSE_POLL_MS = 250" in cockpit
    assert '"--no-access-log"' in launcher


def test_acknowledged_fire_refreshes_projectile_and_visual_outcome_evidence() -> None:
    cockpit = (ROOT / "frontend/src/views/CockpitView.vue").read_text(encoding="utf-8")
    store = (ROOT / "frontend/src/stores/digitalTwinStore.ts").read_text(encoding="utf-8")
    panel = (ROOT / "frontend/src/components/digital-twin/DigitalTwinPanel.vue").read_text(encoding="utf-8")

    # Both autonomous and manual ACK paths fetch the newly-created shot now,
    # then fetch its one-second visual hit/miss decision without reloading the
    # large digital-twin asset or delaying pose telemetry.
    assert cockpit.count("digitalTwin.refreshEngagementEvidence()") >= 4
    assert cockpit.count("}, 1150)") >= 2
    assert "async function refreshEngagementEvidence(): Promise<void>" in store
    assert "refreshEngagementEvidence," in store
    assert "assets.value ? Promise.resolve(assets.value)" in store

    # Engagement evidence remains available as metadata, but the live twin is
    # now perception-truth only: no projectile, impact, explosion or synthetic
    # target-destruction animation may compete with YOLO/Chrome rendering.
    assert "addAcknowledgedProjectile3d" not in panel
    assert "addShotOutcomeEffect3d" not in panel
    assert "acknowledged_visual_projectile" not in panel
    assert "visual_hit_confirmation_burst" not in panel
