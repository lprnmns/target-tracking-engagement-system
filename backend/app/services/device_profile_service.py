import json
import re
import time
import unicodedata
from pathlib import Path

from app.schemas.device_profile import DeviceProfile, DeviceProfileResult, DeviceProfileSaveRequest, DeviceProfilesList
from app.schemas.log import LogLevel
from app.schemas.vision import VisionConfigUpdate
from app.services.log_service import JsonlLogService
from app.services.storage_paths import project_root, resolve_project_path


class DeviceProfileService:
    def __init__(self, logger: JsonlLogService) -> None:
        self.logger = logger
        self.root = project_root() / "data" / "device_profiles"
        self.root.mkdir(parents=True, exist_ok=True)
        self.active_id = self._configured_default_profile_id()
        self.last_event: tuple[str, dict] | None = None

    def _configured_default_profile_id(self) -> str:
        preference_path = project_root() / "config" / "default_device_profile.json"
        try:
            configured = json.loads(preference_path.read_text(encoding="utf-8")).get("profile_id", "default")
        except (OSError, ValueError, AttributeError):
            configured = "default"
        profile_id = self._safe_profile_id(str(configured))
        return profile_id if (self.root / f"{profile_id}.json").is_file() else "default"

    def list_profiles(self) -> DeviceProfilesList:
        profiles: list[DeviceProfile] = []
        for path in sorted(self.root.glob("*.json")):
            try:
                profiles.append(self._load(path))
            except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
                # A partially written or manually damaged profile must not
                # make the landing page unusable. Keep the file untouched so
                # field data can be recovered, skip only that record, and
                # leave a machine-readable diagnostic in the local log.
                self._event(
                    "device_profile.invalid_skipped",
                    {
                        "reason_code": "DEVICE_PROFILE_INVALID_JSON",
                        "profile_file": path.name,
                        "error_type": type(exc).__name__,
                    },
                    "Invalid device profile skipped while listing profiles",
                    LogLevel.WARN,
                )
        # Phase-14 metadata placeholders did not contain runnable camera or
        # vision settings. Do not present those legacy records as selectable
        # operator setup profiles.
        profiles = [profile for profile in profiles if profile.camera_profile is not None or profile.vision_config is not None]
        return DeviceProfilesList(profiles=profiles, active_profile_id=self.active_id)

    def active(self) -> DeviceProfile:
        return self._load(self._path(self.active_id))

    def save(self, runtime, request: DeviceProfileSaveRequest | None = None) -> DeviceProfileResult:
        request = request or DeviceProfileSaveRequest()
        profile_id = self._safe_profile_id(request.profile_id or request.display_name)
        camera = runtime.camera_runtime.status()
        active = runtime.model_registry.active_models()
        pico = runtime.hardware.status(mock_pico_active=runtime.pico.status().mock_mode)
        serial_status = runtime.serial.status()
        gateway_pico_port = (
            runtime.config.serial.port
            if serial_status.real_serial_enabled
            and serial_status.transport_source == "real_serial"
            and str(serial_status.connection_state) != "FAULT"
            else None
        )
        selected_pico_port = gateway_pico_port or pico.telemetry.port
        inventory = runtime.device_manager.inventory()
        selected_camera = next(
            (
                item
                for item in inventory.cameras
                if item.device_id == camera.profile.device_id
                or (camera.profile.stable_path and item.stable_path == camera.profile.stable_path)
                or (camera.profile.device_path and item.device_path == camera.profile.device_path)
            ),
            None,
        )
        selected_pico = next(
            (
                item
                for item in [*inventory.pico_candidates, *inventory.serial]
                if selected_pico_port and item.device_path == selected_pico_port
            ),
            None,
        )
        existing_path = self._path(profile_id)
        existing = self._load(existing_path) if existing_path.exists() else None
        now = time.time()
        vision_config = VisionConfigUpdate(
            vision_mode=runtime.vision.vision_mode,
            body_model_path=self._portable_path(runtime.vision.body_model_path),
            balloon_model_path=self._portable_path(runtime.vision.balloon_model_path),
            body_conf_threshold=runtime.vision.body_conf_threshold,
            balloon_conf_threshold=runtime.vision.balloon_conf_threshold,
        )
        direct_model_roles = [
            role
            for role, model_path in (
                ("body", vision_config.body_model_path),
                ("balloon", vision_config.balloon_model_path),
            )
            if model_path
        ]
        selected_model_id = (
            "setup-direct-models"
            if direct_model_roles
            else active.active_combined_model_id or active.active_body_model_id or active.active_balloon_model_id or active.active_test_adapter
        )
        production_model_selected = bool(
            direct_model_roles
            or active.active_combined_model_id
            or active.active_body_model_id
            or active.active_balloon_model_id
        )
        profile = DeviceProfile(
            profile_id=profile_id,
            display_name=request.display_name.strip(),
            created_at=existing.created_at if existing else now,
            updated_at=now,
            command_profile=request.command_profile,
            selected_camera_id=camera.profile.device_id,
            selected_camera_stable_path=camera.profile.stable_path,
            selected_camera_name=(selected_camera.description or selected_camera.name) if selected_camera else camera.selected_camera,
            selected_camera_backend=camera.backend_api,
            selected_pico_port=selected_pico_port,
            selected_pico_baudrate=(runtime.config.serial.baudrate if gateway_pico_port else pico.telemetry.baudrate) or 460800,
            selected_pico_usb_vid_pid=(f"{selected_pico.vid}:{selected_pico.pid}" if selected_pico and selected_pico.vid and selected_pico.pid else None),
            selected_pico_serial_number=selected_pico.serial_number if selected_pico else None,
            selected_model_id=selected_model_id,
            selected_runtime_profile=runtime.vision_runtime.profile.inference_adapter,
            camera_profile=camera.profile,
            vision_config=vision_config,
            vision_runtime_profile=runtime.vision_runtime.profile,
            servo_release_deg=request.servo_release_deg,
            servo_fire_deg=request.servo_fire_deg,
            servo_pulse_s=request.servo_pulse_s,
            verification_status="not_verified",
            verification_level="demo_verified" if camera.profile.source_type == "mock" else "hardware_pending",
            camera_binding_status="mock/demo only" if camera.profile.source_type == "mock" else "camera_pending",
            pico_binding_status="pico_pending",
            model_binding_status="production_model_loaded" if production_model_selected else "demo adapter" if active.active_test_adapter else "model_pending",
            competition_status="competition_not_verified",
        )
        self._write(profile)
        self.active_id = profile_id
        result = DeviceProfileResult(accepted=True, profile=profile, reason="Kurulum profili kaydedildi.")
        self._event("device_profile.saved", result.model_dump(mode="json"), "Device profile saved")
        return result

    def apply(self, runtime, profile_id: str = "default", connect_hardware: bool = False) -> DeviceProfileResult:
        path = self._path(profile_id)
        if not path.exists():
            profile = DeviceProfile(profile_id=profile_id, display_name=profile_id)
            return DeviceProfileResult(accepted=False, profile=profile, warnings=["PROFILE_NOT_FOUND"], reason="Kurulum profili bulunamadı.")
        profile = self._load(path)
        warnings: list[str] = []

        if profile.camera_profile is not None:
            camera_profile, camera_warning = self._camera_profile_for_current_host(runtime, profile)
            if camera_warning:
                warnings.append(camera_warning)
            if camera_profile is not None:
                camera_result = runtime.camera_runtime.apply(camera_profile)
                if not camera_result.accepted:
                    warnings.extend(camera_result.warnings or ["PROFILE_CAMERA_APPLY_FAILED"])
                elif connect_hardware and camera_profile.source_type != "mock":
                    # Applying a saved profile releases the previous capture
                    # owner.  A live startup must hand the same profile back
                    # to the camera worker before CommandGateway preflight;
                    # otherwise the UI can show the last setup frame while
                    # Gateway correctly sees ``last_frame_at=None`` and
                    # rejects the physical command as CAMERA_STALE.
                    # ``applied=False`` means CameraRuntimeService reused an
                    # already healthy owner; starting it again would only add
                    # another status round trip. A newly applied profile still
                    # starts the worker exactly once.
                    if camera_result.applied:
                        camera_status = runtime.camera_runtime.start_preview()
                        if not camera_status.running:
                            warnings.extend(camera_status.warnings or ["PROFILE_CAMERA_PREVIEW_NOT_READY"])

        if profile.vision_config is not None:
            vision_config = profile.vision_config.model_copy(
                update={
                    "body_model_path": self._runtime_path(profile.vision_config.body_model_path),
                    "balloon_model_path": self._runtime_path(profile.vision_config.balloon_model_path),
                }
            )
            for role, model_path in (("BODY", vision_config.body_model_path), ("BALLOON", vision_config.balloon_model_path)):
                if model_path and not Path(model_path).is_file():
                    warnings.append(f"PROFILE_{role}_MODEL_NOT_FOUND")
            runtime.vision_pipeline.configure(vision_config)

        if profile.vision_runtime_profile is not None:
            status = runtime.vision_pipeline.status()
            # Production tracking is intentionally locked to the field-
            # validated custom tracker. Older saved profiles may still carry
            # ``tracker_type=none`` (or an experimental lab adapter). Do not
            # let that stale metadata make the engineering readback disagree
            # with the tracker that VisionPipeline is actually running.
            live_runtime_profile = profile.vision_runtime_profile.model_copy(
                update={"tracker_enabled": True, "tracker_type": "current_custom"}
            )
            runtime_result = runtime.vision_runtime.apply(
                live_runtime_profile,
                current_fps=status.fps,
                latest_latency_ms=status.latest_latency_ms,
                camera_source_type=runtime.camera_runtime.profile.source_type,
            )
            if not runtime_result.accepted:
                warnings.extend(runtime_result.errors or ["PROFILE_VISION_RUNTIME_APPLY_FAILED"])

        if connect_hardware and profile.selected_pico_port:
            pico_port = self._pico_port_for_current_host(runtime, profile)
            if pico_port is None:
                warnings.append("PROFILE_PICO_NOT_FOUND")
            else:
                connected, reason_code = runtime.command_gateway.connect_pico(pico_port, profile.selected_pico_baudrate)
                if not connected:
                    warnings.append(reason_code or "PROFILE_PICO_CONNECT_FAILED")

        self.active_id = profile_id
        result = DeviceProfileResult(
            # Missing hardware blocks only the relevant physical command via
            # Gateway preflight; it must not lock the whole application.
            accepted=True,
            profile=profile,
            warnings=sorted(set(warnings)),
            reason="Kurulum profili uygulandı." if not warnings else "Profil uygulandı; eksik cihaz veya model ana ekranda gösteriliyor.",
        )
        self._event("device_profile.applied", result.model_dump(mode="json"), "Device profile applied")
        return result

    def verify(self, runtime, profile_id: str = "default") -> DeviceProfileResult:
        profile = self._load(self._path(profile_id))
        inventory = runtime.device_manager.inventory()
        camera_status = runtime.camera_runtime.status()
        hardware_status = runtime.hardware.status(mock_pico_active=runtime.pico.status().mock_mode)
        active_models = runtime.model_registry.active_models()
        mismatch: list[str] = []
        similar: list[dict] = []
        camera_binding = "mock/demo only" if camera_status.profile.source_type == "mock" else "camera_pending"
        pico_binding = "pico_readonly_verified" if hardware_status.pico_verified else "pico_pending"
        model_binding = "production_model_loaded" if (
            active_models.active_body_model_id or active_models.active_balloon_model_id or active_models.active_combined_model_id
        ) else ("test_adapter_only" if active_models.active_test_adapter or runtime.vision_runtime.profile.inference_adapter == "opencv_circle_test" else "model_pending")

        if profile.selected_camera_id:
            camera_found = next((item for item in inventory.cameras if item.device_id == profile.selected_camera_id), None)
            if camera_found is None:
                mismatch.append("Previously selected camera not found.")
                camera_binding = "camera_pending"
                similar = [item.model_dump(mode="json") for item in inventory.cameras[:3]]
                if similar:
                    mismatch.append("Similar camera candidate found.")
                else:
                    mismatch.append("Fallback to mock/manual required.")
            else:
                camera_binding = "camera_hardware_candidate"
        if profile.selected_pico_port and profile.selected_pico_port not in [item.device_path for item in inventory.serial]:
            mismatch.append("Previously selected Pico port not found.")
            pico_binding = "pico_pending"

        if mismatch:
            status = "mismatch"
            verification_level = "hardware_pending"
        elif hardware_status.pico_verified and model_binding == "production_model_loaded" and camera_binding != "mock/demo only":
            status = "hardware_readonly_verified"
            verification_level = "hardware_readonly_verified"
        elif camera_binding == "mock/demo only":
            status = "demo_verified"
            verification_level = "demo_verified"
        else:
            status = "hardware_pending"
            verification_level = "hardware_pending"

        competition_status = "competition_not_verified"
        if not hardware_status.pico_verified:
            competition_status = "competition_not_verified"
        elif model_binding != "production_model_loaded":
            competition_status = "competition_not_verified"

        profile = profile.model_copy(update={
            "last_verified_at": time.time(),
            "verification_status": status,
            "verification_level": verification_level,
            "camera_binding_status": camera_binding,
            "pico_binding_status": pico_binding,
            "model_binding_status": model_binding,
            "competition_status": competition_status,
            "warnings": mismatch,
        })
        self._write(profile)
        result = DeviceProfileResult(
            accepted=status in {"mock_verified", "demo_verified", "hardware_readonly_verified"},
            profile=profile,
            mismatch_warnings=mismatch,
            similar_candidates=similar,
            reason="Profile verified for current readiness level." if status in {"mock_verified", "demo_verified", "hardware_readonly_verified"} else "Profile needs hardware/model verification.",
        )
        self._event("device_profile.checked", result.model_dump(mode="json"), "Device profile verification semantics checked", LogLevel.INFO if result.accepted else LogLevel.WARN)
        return result

    def reset(self) -> DeviceProfileResult:
        profile = DeviceProfile()
        self._write(profile)
        self.active_id = "default"
        result = DeviceProfileResult(accepted=True, profile=profile, reason="Device profile reset.")
        self._event("device_profile.reset", result.model_dump(mode="json"), "Device profile reset")
        return result

    def _path(self, profile_id: str) -> Path:
        safe = self._safe_profile_id(profile_id)
        return self.root / f"{safe}.json"

    @staticmethod
    def _safe_profile_id(value: str) -> str:
        normalized = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode("ascii").lower()
        safe = re.sub(r"[^a-z0-9]+", "-", normalized).strip("-")
        return (safe or "profil")[:64]

    @staticmethod
    def _portable_path(value: str | None) -> str | None:
        if not value:
            return None
        resolved = resolve_project_path(value)
        try:
            return resolved.relative_to(project_root()).as_posix()
        except ValueError:
            return str(resolved)

    @staticmethod
    def _runtime_path(value: str | None) -> str | None:
        if not value:
            return None
        return str(resolve_project_path(value))

    def _camera_profile_for_current_host(self, runtime, profile: DeviceProfile):
        saved = profile.camera_profile
        if saved is None or saved.source_type == "mock":
            return saved, None
        inventory = runtime.device_manager.inventory()
        candidates = list(inventory.cameras)
        expected_stable_path = profile.selected_camera_stable_path or saved.stable_path
        selected = next(
            (item for item in candidates if expected_stable_path and item.stable_path == expected_stable_path),
            None,
        )
        # The USB instance suffix may change after a replug while VID/PID stays
        # stable. Prefer that unique physical identity over opening a guessed
        # DirectShow ordinal. This removes a redundant cold-start camera probe
        # and prevents a saved index from silently becoming the laptop camera.
        if selected is None and expected_stable_path:
            match = re.search(r"VID_[0-9A-Fa-f]{4}&PID_[0-9A-Fa-f]{4}", expected_stable_path)
            if match is not None:
                token = match.group(0).casefold()
                matches = [item for item in candidates if token in str(item.stable_path or "").casefold()]
                if len(matches) == 1:
                    selected = matches[0]
        if selected is not None:
            return saved.model_copy(
                update={
                    "device_id": selected.device_id,
                    "device_path": selected.device_path,
                    "stable_path": selected.stable_path,
                }
            ), None
        # Only legacy profiles without durable PnP identity may fall back to a
        # crash-isolated index probe. A profile with an absent identity fails
        # visibly instead of binding to whichever camera inherited its index.
        if expected_stable_path:
            return None, "PROFILE_CAMERA_NOT_FOUND"
        if runtime.device_manager.windows_camera_path_responds(saved.device_path):
            return saved, None
        if selected is None:
            selected = next((item for item in candidates if profile.selected_camera_id and item.device_id == profile.selected_camera_id), None)
        if selected is None and profile.selected_camera_name:
            expected = profile.selected_camera_name.casefold()
            selected = next(
                (item for item in candidates if expected in f"{item.name} {item.description}".casefold()),
                None,
            )
        if selected is None:
            return None, "PROFILE_CAMERA_NOT_FOUND"
        return saved.model_copy(
            update={
                "device_id": selected.device_id,
                "device_path": selected.device_path,
                "stable_path": selected.stable_path,
            }
        ), None

    @staticmethod
    def _pico_port_for_current_host(runtime, profile: DeviceProfile) -> str | None:
        inventory = runtime.device_manager.inventory()
        candidates = [*inventory.pico_candidates, *inventory.serial]
        if profile.selected_pico_serial_number:
            selected = next((item for item in candidates if item.serial_number == profile.selected_pico_serial_number), None)
            if selected is not None:
                return selected.device_path
        if profile.selected_pico_usb_vid_pid:
            selected = next(
                (
                    item
                    for item in candidates
                    if item.vid and item.pid and f"{item.vid}:{item.pid}" == profile.selected_pico_usb_vid_pid
                ),
                None,
            )
            if selected is not None:
                return selected.device_path
        selected = next((item for item in candidates if item.device_path == profile.selected_pico_port), None)
        return selected.device_path if selected is not None else None

    def _load(self, path: Path) -> DeviceProfile:
        if not path.exists():
            return DeviceProfile(profile_id=path.stem if path.stem else "default")
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("verification_status") == "verified":
            data["verification_status"] = "demo_verified" if data.get("selected_camera_backend") == "mock" else "hardware_readonly_verified"
        data.setdefault("verification_level", data.get("verification_status", "hardware_pending"))
        data.setdefault("camera_binding_status", "mock/demo only" if data.get("selected_camera_backend") == "mock" else "camera_pending")
        data.setdefault("pico_binding_status", "pico_pending")
        data.setdefault("model_binding_status", "test_adapter_only" if data.get("selected_model_id") else "model_pending")
        data.setdefault("competition_status", "competition_not_verified")
        return DeviceProfile.model_validate(data)

    def _write(self, profile: DeviceProfile) -> None:
        self._path(profile.profile_id).write_text(json.dumps(profile.model_dump(mode="json"), indent=2), encoding="utf-8")

    def _event(self, event_type: str, payload: dict, message: str, level: LogLevel = LogLevel.INFO) -> None:
        self.last_event = (event_type, payload)
        self.logger.emit(level, "DEVICE_PROFILE", message, payload)
