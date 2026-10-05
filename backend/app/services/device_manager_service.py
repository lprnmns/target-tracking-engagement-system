import glob
import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

from app.schemas.config import AppConfig
from app.schemas.device_manager import CameraCapability, CameraProbeResult, DeviceInventory, DeviceKind, ManagedDevice
from app.schemas.log import LogLevel
from app.services.log_service import JsonlLogService


class DeviceManagerService:
    def __init__(self, config: AppConfig, logger: JsonlLogService) -> None:
        self.config = config
        self.logger = logger
        self.last_inventory = DeviceInventory(devices=[], cameras=[], serial=[], pico_candidates=[])
        self.last_event: tuple[str, dict] | None = None
        # A Windows UVC driver must have one owner.  The runtime marks its
        # active worker here so a harmless UI refresh cannot open/probe the
        # same DirectShow device and make its ordinal appear to change.
        self._scan_lock = threading.RLock()
        self.active_camera_path: str | None = None
        self.active_camera_stable_path: str | None = None
        self._camera_affinity: dict[str, str] = {}
        self._camera_affinity_path = Path("config/runtime/windows_camera_affinity.json")
        self._load_camera_affinity()

    def set_active_camera(self, device_path: str | None, stable_path: str | None = None) -> None:
        self.active_camera_path = device_path
        self.active_camera_stable_path = stable_path

    def clear_active_camera(self, device_path: str | None = None) -> None:
        if device_path is None or self.active_camera_path == device_path:
            self.active_camera_path = None
            self.active_camera_stable_path = None

    def remember_camera_mapping(self, stable_path: str | None, device_path: str | None) -> None:
        if not stable_path or not device_path:
            return
        self._camera_affinity[self._normalise_camera_identity(stable_path)] = device_path
        try:
            self._camera_affinity_path.parent.mkdir(parents=True, exist_ok=True)
            self._camera_affinity_path.write_text(json.dumps(self._camera_affinity, indent=2), encoding="utf-8")
        except OSError:
            # Affinity is an optimisation; failure must never block camera use.
            pass

    @staticmethod
    def _normalise_camera_identity(value: str | None) -> str:
        return str(value or "").strip().replace("/", "\\").casefold()

    def _load_camera_affinity(self) -> None:
        try:
            payload = json.loads(self._camera_affinity_path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                self._camera_affinity = {
                    self._normalise_camera_identity(str(key)): str(value)
                    for key, value in payload.items()
                    if isinstance(value, str)
                }
        except (OSError, json.JSONDecodeError):
            self._camera_affinity = {}

    def scan(self) -> DeviceInventory:
        # Do not probe a live camera a second time.  The capture worker already
        # owns the handle and its PnP watchdog is the authoritative hot-plug
        # signal.  Returning the existing inventory keeps the selected physical
        # identity stable while the operator opens/refreshes the UI.
        if os.name == "nt" and self.active_camera_path and self.last_inventory.cameras:
            return self.last_inventory
        with self._scan_lock:
            if os.name == "nt" and self.active_camera_path and self.last_inventory.cameras:
                return self.last_inventory
            return self._scan_unlocked()

    def _scan_unlocked(self) -> DeviceInventory:
        self._event("devices.scan_started", {"no_physical_command_generated": True}, "Device scan started")
        devices: list[ManagedDevice] = []
        devices.extend(self._serial_devices())
        devices.extend(self._camera_devices())
        unique: dict[str, ManagedDevice] = {device.device_id: device for device in devices}
        devices = sorted(unique.values(), key=lambda item: (item.kind.value, item.device_path))
        # Physical turret USB camera is the first choice everywhere in the UI;
        # operators can still explicitly select the laptop camera. The sort is
        # based on verified identity/recommendation, never on DirectShow index.
        cameras = sorted(
            (item for item in devices if item.kind == DeviceKind.CAMERA),
            key=lambda item: (-int(item.is_external_camera), -item.recommendation_score, item.name.casefold(), item.device_path),
        )
        serial = [item for item in devices if item.kind in {DeviceKind.SERIAL, DeviceKind.PICO_CANDIDATE}]
        pico_candidates = [item for item in serial if item.kind == DeviceKind.PICO_CANDIDATE]
        inventory = DeviceInventory(
            devices=devices,
            cameras=cameras,
            serial=serial,
            pico_candidates=pico_candidates,
            warnings=[] if devices else ["No serial or camera devices discovered."],
        )
        self.last_inventory = inventory
        self._event(
            "devices.scan_completed",
            {
                "device_count": len(devices),
                "camera_count": len(cameras),
                "serial_count": len(serial),
                "pico_candidate_count": len(pico_candidates),
                "no_physical_command_generated": True,
            },
            "Device scan completed",
        )
        for candidate in pico_candidates:
            self._event("devices.pico_candidate_found", candidate.model_dump(mode="json"), "Pico candidate found")
        return inventory

    def inventory(self) -> DeviceInventory:
        return self.last_inventory if self.last_inventory.devices else self.scan()

    def serial_devices(self) -> list[ManagedDevice]:
        return self.inventory().serial

    def cameras(self) -> list[ManagedDevice]:
        return self.inventory().cameras

    def pico_candidates(self) -> list[ManagedDevice]:
        return self.inventory().pico_candidates

    def camera_capabilities(self, device_id: str) -> CameraCapability:
        device = self._find_device(device_id)
        if device is None or device.kind != DeviceKind.CAMERA:
            return CameraCapability(
                device_id=device_id,
                device_path=device_id,
                warnings=["Camera device not found."],
                suggested_action="Refresh devices and select a listed camera.",
            )
        return self._probe_camera(device)

    def resolve_camera_identity(
        self,
        *,
        device_id: str | None = None,
        device_path: str | None = None,
        stable_path: str | None = None,
    ) -> ManagedDevice | None:
        """Resolve a selected camera without trusting a volatile index.

        Windows DirectShow ordinals can change when a UVC camera is unplugged,
        a virtual camera appears, or the USB topology is re-enumerated.  The
        PnP instance path is the primary identity.  Some Windows drivers
        regenerate the instance suffix after a replug while retaining the
        same physical USB VID/PID.  In that case a *unique* VID/PID match is
        safe; ambiguous identical cameras remain unresolved instead of being
        guessed.  Only fall back to the ordinal/id when no durable path was
        saved.
        """

        def normalise(value: str | None) -> str:
            return str(value or "").strip().replace("/", "\\").casefold()

        def usb_signature(value: str | None) -> str:
            match = re.search(r"vid_([0-9a-f]{4})&pid_([0-9a-f]{4})", normalise(value))
            return f"{match.group(1)}:{match.group(2)}" if match else ""

        def find(inventory: DeviceInventory) -> ManagedDevice | None:
            cameras = inventory.cameras
            if stable_path:
                wanted = normalise(stable_path)
                exact = next((item for item in cameras if normalise(item.stable_path) == wanted), None)
                if exact is not None:
                    return exact
                # The instance suffix is not stable across some USB replug
                # paths. Accept this only when the hardware signature maps to
                # exactly one currently connected physical camera.
                signature = usb_signature(stable_path)
                if signature:
                    matches = [item for item in cameras if usb_signature(item.stable_path) == signature]
                    if len(matches) == 1:
                        return matches[0]
                return None
            if device_id:
                found = next((item for item in cameras if item.device_id == device_id), None)
                if found is not None:
                    return found
            if device_path:
                wanted = normalise(device_path)
                return next((item for item in cameras if normalise(item.device_path) == wanted), None)
            return None

        inventory = self.inventory()
        resolved = find(inventory)
        if resolved is not None or not stable_path:
            return resolved

        # A cached inventory may still carry the old DirectShow ordinal after
        # hot-unplug/replug. Refresh once before reporting the camera missing.
        return find(self.scan())

    def probe_camera(self, device_id: str) -> CameraProbeResult:
        device = self._find_device(device_id)
        if device is None or device.kind != DeviceKind.CAMERA:
            return CameraProbeResult(
                accepted=False,
                warnings=["Camera device not found."],
                suggested_action="Refresh devices and select a listed camera.",
            )
        capability = self._probe_camera(device)
        result = CameraProbeResult(
            accepted=capability.open_ok,
            device=device,
            capabilities=capability,
            warnings=capability.warnings,
            suggested_action=capability.suggested_action,
        )
        self._event("devices.camera_probe", result.model_dump(mode="json"), "Camera probe completed", LogLevel.INFO if result.accepted else LogLevel.WARN)
        return result

    def _serial_devices(self) -> list[ManagedDevice]:
        devices: list[ManagedDevice] = []
        try:
            from serial.tools import list_ports  # type: ignore[import-not-found]

            for port in list_ports.comports():
                text = " ".join(str(item or "") for item in (port.device, port.description, port.hwid, port.manufacturer))
                score = self._pico_score(text)
                vid = f"{port.vid:04x}" if getattr(port, "vid", None) is not None else self._extract_usb_id(port.hwid or "", "VID")
                pid = f"{port.pid:04x}" if getattr(port, "pid", None) is not None else self._extract_usb_id(port.hwid or "", "PID")
                devices.append(
                    ManagedDevice(
                        device_id=self._id("serial", port.device),
                        device_path=port.device,
                        stable_path=self._stable_serial_path(port.device),
                        kind=DeviceKind.PICO_CANDIDATE if score >= 50 else DeviceKind.SERIAL,
                        name=port.device,
                        description=port.description or port.device,
                        manufacturer=port.manufacturer,
                        vid=vid,
                        pid=pid,
                        serial_number=getattr(port, "serial_number", None),
                        bus_path=getattr(port, "location", None),
                        permissions_ok=True if os.name == "nt" else os.access(port.device, os.R_OK | os.W_OK),
                        candidate_score=score,
                        recommendation_score=score,
                        warnings=[] if score >= 50 else ["Serial device is not verified as Pico."],
                        suggested_action="Use read-only telemetry verification before trusting this Pico candidate." if score >= 50 else "Leave unselected unless physically confirmed.",
                    )
                )
        except Exception as exc:
            self._event("devices.scan_warning", {"warning": f"pyserial_unavailable:{exc}"}, "pyserial port scan unavailable", LogLevel.WARN)

        seen = {device.device_path for device in devices}
        for path in sorted(set(glob.glob("/dev/ttyACM*") + glob.glob("/dev/ttyUSB*"))):
            if path in seen:
                continue
            score = self._pico_score(path)
            devices.append(
                ManagedDevice(
                    device_id=self._id("serial", path),
                    device_path=path,
                    stable_path=self._stable_serial_path(path),
                    kind=DeviceKind.PICO_CANDIDATE if score >= 30 else DeviceKind.SERIAL,
                    name=Path(path).name,
                    description="glob detected serial device",
                    permissions_ok=os.access(path, os.R_OK | os.W_OK),
                    candidate_score=score,
                    recommendation_score=score,
                    warnings=[] if score >= 30 else ["Serial device is not identified as Pico."],
                    suggested_action="Verify with telemetry-only firmware.",
                )
            )
        return devices

    def _camera_devices(self) -> list[ManagedDevice]:
        if os.name == "nt":
            return self._windows_camera_devices()
        by_id = self._video_by_id()
        devices: list[ManagedDevice] = []
        for path in sorted(glob.glob("/dev/video*")):
            permissions_ok = os.access(path, os.R_OK | os.W_OK)
            stable = by_id.get(os.path.realpath(path))
            name = Path(stable).name if stable else Path(path).name
            devices.append(
                ManagedDevice(
                    device_id=self._id("camera", path),
                    device_path=path,
                    stable_path=stable,
                    kind=DeviceKind.CAMERA,
                    name=name,
                    description=self._v4l2_name(path) or "Video capture device",
                    driver="v4l2",
                    permissions_ok=permissions_ok,
                    candidate_score=0,
                    recommendation_score=self._camera_recommendation(path, stable, permissions_ok),
                    warnings=[] if permissions_ok else ["Permission denied for camera device."],
                    suggested_action=None if permissions_ok else "Add user to video group or close permission blocker.",
                )
            )
        return devices

    def _windows_camera_devices(self) -> list[ManagedDevice]:
        command = [
            "powershell.exe",
            "-NoProfile",
            "-Command",
            "Get-PnpDevice -PresentOnly | Where-Object { $_.Class -in @('Camera','Image') } | "
            "Select-Object FriendlyName,InstanceId,Manufacturer,Status,Class | ConvertTo-Json -Compress",
        ]
        try:
            completed = subprocess.run(command, capture_output=True, text=True, timeout=5, check=False)
            raw = completed.stdout.strip()
            payload = json.loads(raw) if raw else []
        except (FileNotFoundError, subprocess.SubprocessError, json.JSONDecodeError):
            payload = []
        if isinstance(payload, dict):
            payload = [payload]

        pnp_devices = [item for item in payload if isinstance(item, dict)] if isinstance(payload, list) else []
        pnp_devices = [item for item in pnp_devices if str(item.get("Status") or "OK").casefold() == "ok"]
        # PnP exposes virtual DirectShow sources as Camera/Image devices too.
        # They are useful to conferencing software, but are not physical
        # turret/laptop choices and can make the DirectShow ordinal shift.
        pnp_devices = [
            item for item in pnp_devices
            if not self._windows_item_is_virtual(str(item.get("FriendlyName") or ""))
        ]
        if not pnp_devices:
            return []

        # pygrabber asks DirectShow for the actual device names without
        # opening a capture handle.  This is the only reliable way to keep a
        # PnP identity paired with its volatile OpenCV ordinal when virtual
        # cameras occupy gaps (e.g. 1=laptop, 2=virtual, 3=turret).
        directshow_names = self._windows_directshow_names()
        assignments: list[tuple[dict, int, bool, str]] = []
        used_pnp: set[int] = set()
        if directshow_names:
            for index, directshow_name in enumerate(directshow_names):
                match_index = self._match_windows_pnp_device(directshow_name, pnp_devices, used_pnp)
                if match_index is None:
                    continue  # virtual camera: never expose it as a physical option
                used_pnp.add(match_index)
                assignments.append((pnp_devices[match_index], index, True, "directshow_name"))

        if not assignments:
            # Portable fallback for installations that do not include pygrabber.
            # Probe only enough indexes to find real frames; never fabricate a
            # camera path for a missing index. The result is explicitly marked
            # unverified so LIVE mode can require a fresh worker frame.
            capture_indices = self._windows_capture_indices(len(pnp_devices))
            for ordinal, item in enumerate(pnp_devices):
                if ordinal >= len(capture_indices):
                    break
                assignments.append((item, capture_indices[ordinal], False, "isolated_index_probe"))

        # Prefer the last operator-confirmed index for a durable PnP identity,
        # but only when that index is among the currently enumerated physical
        # assignments. This handles a 2→3 re-enumeration without guessing.
        by_stable = {
            self._normalise_camera_identity(str(item.get("InstanceId") or "")): (item, index, verified, source)
            for item, index, verified, source in assignments
        }
        assignments_by_index = {index: (item, index, verified, source) for item, index, verified, source in assignments}
        ordered: list[tuple[dict, int, bool, str]] = []
        used_indices: set[int] = set()
        for item, index, verified, source in assignments:
            stable = self._normalise_camera_identity(str(item.get("InstanceId") or ""))
            preferred_path = self._camera_affinity.get(stable)
            preferred_index = self._camera_index_number(preferred_path)
            if preferred_index is not None and preferred_index in assignments_by_index and preferred_index not in used_indices:
                selected = assignments_by_index[preferred_index]
                # Only swap when the stable identity also matches; otherwise a
                # stale affinity must not relabel another physical camera.
                if self._normalise_camera_identity(str(selected[0].get("InstanceId") or "")) == stable:
                    item, index, verified, source = selected
            if index not in used_indices:
                ordered.append((item, index, verified, source))
                used_indices.add(index)

        devices: list[ManagedDevice] = []
        for item, index, verified, source in ordered:
            friendly = str(item.get("FriendlyName") or f"Windows Camera {index}")
            instance_id = str(item.get("InstanceId") or "")
            usb_match = re.search(r"VID_([0-9A-Fa-f]{4})&PID_([0-9A-Fa-f]{4})", instance_id)
            is_laptop = self._windows_item_is_laptop(friendly, instance_id)
            external = not is_laptop
            device_path = f"camera-index:{index}"
            warnings = [] if verified else ["CAMERA_IDENTITY_UNVERIFIED: DirectShow name mapper unavailable; confirm the preview before live use."]
            devices.append(
                ManagedDevice(
                    device_id=f"camera_index_{index}",
                    device_path=device_path,
                    stable_path=instance_id or None,
                    kind=DeviceKind.CAMERA,
                    name=friendly,
                    description=f"{friendly} · {('DirectShow adı doğrulandı' if verified else 'frame doğrulaması gerekli')}",
                    manufacturer=str(item.get("Manufacturer") or "") or None,
                    vid=usb_match.group(1).lower() if usb_match else None,
                    pid=usb_match.group(2).lower() if usb_match else None,
                    bus_path=instance_id or None,
                    driver="dshow",
                    permissions_ok=True,
                    candidate_score=0,
                    recommendation_score=95 if external and verified else (85 if external else 65),
                    warnings=warnings,
                    suggested_action="Taret USB kamerası olarak önerildi." if external else "Laptop kamerası; yalnız test için kullanın.",
                    identity_verified=verified,
                    identity_source=source,
                    is_external_camera=external,
                    is_laptop_camera=is_laptop,
                )
            )
        return devices

    @staticmethod
    def _camera_index_number(path: str | None) -> int | None:
        if not isinstance(path, str) or not path.startswith("camera-index:"):
            return None
        try:
            return int(path.split(":", 1)[1])
        except ValueError:
            return None

    def _windows_directshow_names(self) -> list[str]:
        """Return DirectShow's ordered names without opening camera handles."""
        com_initialized = False
        try:
            # Scheduled-task/uvicorn threads do not always enter a COM
            # apartment before pygrabber asks DirectShow for monikers. Make
            # the enumeration deterministic instead of silently falling back
            # to ordinal pairing.
            try:
                import comtypes  # type: ignore[import-not-found]

                comtypes.CoInitialize()
                com_initialized = True
            except Exception:
                pass
            from pygrabber.dshow_graph import FilterGraph  # type: ignore[import-not-found]

            names = [str(name).strip() for name in FilterGraph().get_input_devices() if str(name).strip()]
            if names:
                self._event(
                    "devices.windows_directshow_mapper_available",
                    {"names": names, "count": len(names), "no_physical_command_generated": True},
                    "DirectShow camera names enumerated",
                )
                return names
            raise RuntimeError("pygrabber returned no DirectShow devices")
        except Exception as exc:
            # COM can be unavailable in a scheduled-task apartment even though
            # the same Python environment works from an interactive terminal.
            # Retry the name-only query in a disposable child; it never opens a
            # camera handle and avoids making the operator chase an index.
            code = (
                "import json; from pygrabber.dshow_graph import FilterGraph; "
                "print(json.dumps([str(x).strip() for x in FilterGraph().get_input_devices() if str(x).strip()]))"
            )
            try:
                completed = subprocess.run(
                    [sys.executable, "-c", code],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    check=False,
                )
                for line in reversed(completed.stdout.splitlines()):
                    try:
                        parsed = json.loads(line.strip())
                    except json.JSONDecodeError:
                        continue
                    if isinstance(parsed, list) and parsed:
                        names = [str(name).strip() for name in parsed if str(name).strip()]
                        self._event(
                            "devices.windows_directshow_mapper_available",
                            {"names": names, "count": len(names), "source": "child", "no_physical_command_generated": True},
                            "DirectShow camera names enumerated in child",
                        )
                        return names
            except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
                pass
            self._event(
                "devices.windows_directshow_mapper_unavailable",
                {"warning": f"{type(exc).__name__}:{exc}", "no_physical_command_generated": True},
                "DirectShow name mapper unavailable; using isolated fallback",
                LogLevel.WARN,
            )
            return []
        finally:
            if com_initialized:
                try:
                    import comtypes  # type: ignore[import-not-found]

                    comtypes.CoUninitialize()
                except Exception:
                    pass

    @classmethod
    def _match_windows_pnp_device(cls, directshow_name: str, pnp_devices: list[dict], used: set[int]) -> int | None:
        wanted = cls._normalise_camera_name(directshow_name)
        exact = [
            index for index, item in enumerate(pnp_devices)
            if index not in used and wanted == cls._normalise_camera_name(str(item.get("FriendlyName") or ""))
        ]
        if exact:
            return exact[0]
        # Driver names can add a suffix such as "(USB Video Device)". A
        # conservative token overlap is safe because a duplicate name still
        # falls back to ordinal/affinity rather than being force-matched.
        wanted_tokens = set(wanted.split())
        candidates: list[tuple[int, int]] = []
        for index, item in enumerate(pnp_devices):
            if index in used:
                continue
            tokens = set(cls._normalise_camera_name(str(item.get("FriendlyName") or "")).split())
            overlap = len(wanted_tokens & tokens)
            if overlap >= 2:
                candidates.append((overlap, index))
        if not candidates:
            return None
        candidates.sort(reverse=True)
        if len(candidates) > 1 and candidates[0][0] == candidates[1][0]:
            return None
        return candidates[0][1]

    @staticmethod
    def _normalise_camera_name(value: str) -> str:
        return re.sub(r"[^a-z0-9]+", " ", str(value or "").casefold()).strip()

    @staticmethod
    def _windows_item_is_laptop(friendly: str, instance_id: str) -> bool:
        text = f"{friendly} {instance_id}"
        # VID_2B7E/PID_B685 is the known laptop webcam on the field laptop.
        return bool(re.search(r"integrated|internal|built.?in|front|user.?facing|laptop", friendly, re.IGNORECASE)) or bool(
            re.search(r"VID_2B7E&PID_B685", instance_id, re.IGNORECASE)
        )

    @staticmethod
    def _windows_item_is_virtual(friendly: str) -> bool:
        return bool(re.search(
            r"virtual|droidcam|obs\s+virtual|nvidia\s+broadcast|intel\s+virtual|manycam|vcam",
            friendly,
            re.IGNORECASE,
        ))

    @staticmethod
    def _windows_capture_indices(required_count: int, max_index: int = 5) -> list[int]:
        if required_count <= 0:
            return []
        # Never load DirectShow camera drivers in the long-lived backend while
        # enumerating indexes. Some virtual camera drivers (observed with
        # NVIDIA Broadcast VCAMDS) can raise native heap corruption that Python
        # cannot catch. Each candidate is therefore opened in a disposable
        # child; a crash/timeout rejects only that index and keeps the API alive.
        discovered: list[int] = []
        for index in range(max_index + 1):
            if DeviceManagerService.windows_camera_path_responds(f"camera-index:{index}"):
                discovered.append(index)
                if len(discovered) >= required_count:
                    break
        return discovered

    @staticmethod
    def windows_camera_path_responds(device_path: str, timeout_s: float = 6.0) -> bool:
        if os.name != "nt" or not device_path.startswith("camera-index:"):
            return False
        try:
            index = int(device_path.split(":", 1)[1])
            completed = subprocess.run(
                [sys.executable, "-m", "app.services.windows_camera_probe_worker", "--index", str(index)],
                capture_output=True,
                text=True,
                timeout=timeout_s,
                check=False,
            )
        except (OSError, ValueError, subprocess.TimeoutExpired):
            return False
        return completed.returncode == 0 and completed.stdout.strip().endswith("1")

    def _probe_camera(self, device: ManagedDevice) -> CameraCapability:
        warnings: list[str] = []
        formats, resolutions, fps_values = self._v4l2_formats(device.device_path)
        open_ok = False
        frame_grab_ok = False
        actual_width = None
        actual_height = None
        actual_fps = None
        latency_ms = None
        started = time.time()
        # Never open a Windows DirectShow device in the long-lived backend
        # process. A number of UVC drivers crash or refuse the second handle
        # while the profile-owned capture worker is alive. The isolated probe
        # has the same ownership boundary as the live worker and gives the UI
        # a trustworthy open/frame result.
        if os.name == "nt" and device.device_path.startswith("camera-index:"):
            started = time.time()
            capabilities = self._windows_camera_capabilities(device.device_path)
            accepted = bool(capabilities.get("accepted"))
            open_ok = accepted
            frame_grab_ok = accepted
            actual_width = capabilities.get("actual_width") if accepted else None
            actual_height = capabilities.get("actual_height") if accepted else None
            actual_fps = capabilities.get("actual_fps") if accepted else None
            latency_ms = round((time.time() - started) * 1000, 3)
            if not accepted:
                warnings.append("Camera could not be opened or frame grab failed in isolated probe.")
            return CameraCapability(
                device_id=device.device_id,
                device_path=device.device_path,
                stable_path=device.stable_path,
                supported_resolutions=list(capabilities.get("supported_resolutions") or resolutions or ["640x480", "1280x720"]),
                supported_fps=list(capabilities.get("supported_fps") or fps_values or [15, 30]),
                supported_pixel_formats=list(capabilities.get("pixel_formats") or formats or ["auto", "MJPG", "YUYV"]),
                open_ok=open_ok,
                frame_grab_ok=frame_grab_ok,
                actual_width=actual_width,
                actual_height=actual_height,
                actual_fps=actual_fps,
                latency_ms=latency_ms,
                warnings=warnings,
                suggested_action=None if accepted else "Kamerayı yeniden takın ve yalnızca tek bir uygulamanın kullandığını doğrulayın.",
            )

        try:
            import cv2  # type: ignore[import-not-found]

            source: str | int = device.device_path
            backend = cv2.CAP_ANY
            if os.name == "nt" and device.device_path.startswith("camera-index:"):
                source = int(device.device_path.split(":", 1)[1])
                backend = cv2.CAP_DSHOW
            capture = cv2.VideoCapture(source, backend)
            open_ok = bool(capture.isOpened())
            if open_ok:
                capture.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
                capture.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
                capture.set(cv2.CAP_PROP_FPS, 30)
                capture.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
                capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                ok = False
                _frame = None
                for _ in range(3):
                    ok, _frame = capture.read()
                    if ok and _frame is not None:
                        break
                frame_grab_ok = bool(ok)
                actual_width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0) or None
                actual_height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0) or None
                actual_fps = float(capture.get(cv2.CAP_PROP_FPS) or 0) or None
            capture.release()
        except Exception as exc:
            warnings.append(f"opencv_probe_unavailable:{exc}")
        latency_ms = round((time.time() - started) * 1000, 3)
        if not open_ok:
            warnings.append("Camera could not be opened or OpenCV is unavailable.")
        if open_ok and not frame_grab_ok:
            warnings.append("Camera opened but frame grab failed; device may be busy.")
        return CameraCapability(
            device_id=device.device_id,
            device_path=device.device_path,
            stable_path=device.stable_path,
            supported_resolutions=resolutions or ["640x480", "1280x720"],
            supported_fps=fps_values or [15, 30],
            supported_pixel_formats=formats or ["auto", "MJPG", "YUYV"],
            open_ok=open_ok,
            frame_grab_ok=frame_grab_ok,
            actual_width=actual_width,
            actual_height=actual_height,
            actual_fps=actual_fps,
            latency_ms=latency_ms,
            warnings=warnings,
            suggested_action=None if open_ok else "Select mock camera or verify permissions/device availability.",
        )

    @staticmethod
    def _windows_camera_capabilities(device_path: str) -> dict:
        """Probe DirectShow modes in an isolated child process.

        The long-lived API never opens the vendor UVC handle while probing.
        Older workers only print ``1``/``0``; that remains the fallback so a
        packaged Windows runtime can still enumerate a camera safely.
        """
        if os.name != "nt" or not device_path.startswith("camera-index:"):
            return {"accepted": False}
        try:
            index = int(device_path.split(":", 1)[1])
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "app.services.windows_camera_probe_worker",
                    "--index",
                    str(index),
                    "--capabilities",
                ],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
        except (OSError, ValueError, subprocess.TimeoutExpired):
            return {"accepted": False}
        for line in reversed(completed.stdout.splitlines()):
            try:
                payload = json.loads(line.strip())
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict) and "supported_resolutions" in payload:
                return payload
        return {"accepted": completed.returncode == 0 and completed.stdout.strip().endswith("1")}

    def _find_device(self, device_id: str) -> ManagedDevice | None:
        return next((device for device in self.inventory().devices if device.device_id == device_id), None)

    def _camera_recommendation(self, path: str, stable: str | None, permissions_ok: bool) -> int:
        score = 35 if path.endswith("video0") else 50
        if stable:
            score += 20
        if permissions_ok:
            score += 20
        else:
            score -= 30
        return max(0, min(score, 100))

    @staticmethod
    def _id(prefix: str, path: str) -> str:
        safe = path.strip("/").replace("/", "_").replace(".", "_").replace("-", "_")
        return f"{prefix}_{safe}"

    @staticmethod
    def _pico_score(text: str) -> int:
        lowered = text.lower()
        score = 0
        if "pico" in lowered:
            score += 55
        if any(token in lowered for token in ("rp2040", "rp2350", "raspberry pi")):
            score += 35
        if "2e8a" in lowered:
            # Raspberry Pi's official USB vendor ID is sufficient to present
            # the port as a Pico candidate; handshake still verifies identity.
            score += 55
        if "ttyacm" in lowered:
            score += 20
        if "ttyusb" in lowered:
            score += 10
        return min(score, 100)

    @staticmethod
    def _extract_usb_id(hwid: str, key: str) -> str | None:
        match = re.search(rf"{key}:PID=([0-9A-Fa-f]{{4}}):([0-9A-Fa-f]{{4}})", hwid)
        if match:
            return match.group(1 if key == "VID" else 2).lower()
        return None

    @staticmethod
    def _stable_serial_path(device_path: str) -> str | None:
        for path in glob.glob("/dev/serial/by-id/*"):
            if os.path.realpath(path) == os.path.realpath(device_path):
                return path
        return None

    @staticmethod
    def _video_by_id() -> dict[str, str]:
        mapping: dict[str, str] = {}
        for path in glob.glob("/dev/v4l/by-id/*"):
            mapping[os.path.realpath(path)] = path
        return mapping

    @staticmethod
    def _v4l2_name(device_path: str) -> str | None:
        try:
            result = subprocess.run(["v4l2-ctl", "-d", device_path, "--info"], capture_output=True, text=True, timeout=1.5, check=False)
        except (FileNotFoundError, subprocess.SubprocessError):
            return None
        for line in result.stdout.splitlines():
            if "Card type" in line:
                return line.split(":", 1)[-1].strip()
        return None

    @staticmethod
    def _v4l2_formats(device_path: str) -> tuple[list[str], list[str], list[int]]:
        try:
            result = subprocess.run(["v4l2-ctl", "-d", device_path, "--list-formats-ext"], capture_output=True, text=True, timeout=2, check=False)
        except (FileNotFoundError, subprocess.SubprocessError):
            return [], [], []
        formats = sorted(set(re.findall(r"'([A-Z0-9]{4})'", result.stdout)))
        resolutions = sorted(set(re.findall(r"Size: Discrete ([0-9]+x[0-9]+)", result.stdout)))
        fps_values = sorted({int(round(float(value))) for value in re.findall(r"Interval: Discrete [^(]+\\(([0-9.]+) fps\\)", result.stdout)})
        return formats, resolutions, fps_values

    def _event(self, event_type: str, payload: dict, message: str, level: LogLevel = LogLevel.INFO) -> None:
        payload = {**payload, "no_physical_command_generated": True}
        self.last_event = (event_type, payload)
        self.logger.emit(level, "DEVICES", message, payload)
