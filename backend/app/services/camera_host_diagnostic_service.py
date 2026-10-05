from __future__ import annotations

import glob
import hashlib
import json
import os
import platform
import shutil
import subprocess
import time
import uuid
from pathlib import Path

from app.schemas.legacy_perception import CameraDeviceGroup, CameraHostCommandResult, CameraHostDiagnostic, RealCameraSelection
from app.schemas.log import LogLevel
from app.services.log_service import JsonlLogService
from app.services.storage_paths import project_root

try:  # pragma: no cover - host dependent
    import cv2
except Exception:  # pragma: no cover
    cv2 = None


class CameraHostDiagnosticService:
    def __init__(self, logger: JsonlLogService) -> None:
        self.logger = logger
        self.output_root = project_root() / "exports" / "camera_host"
        self.output_root.mkdir(parents=True, exist_ok=True)
        self.latest_diagnostic: CameraHostDiagnostic | None = None
        self.selected_camera_device: str | None = None
        self.selected_camera_kind: str = "unknown_camera"

    def status(self) -> CameraHostDiagnostic:
        dev_video = self._video_entries()
        diagnostic = self._build_diagnostic(
            commands=[],
            capture_attempted=False,
            frame_captured=False,
            dev_video=dev_video,
            v4l2_available=shutil.which("v4l2-ctl") is not None,
            ffmpeg_available=shutil.which("ffmpeg") is not None,
        )
        self.latest_diagnostic = diagnostic
        self._write_latest()
        self._event(
            "vision.camera_device_inventory_recorded",
            diagnostic,
            f"Camera host device inventory recorded; devices={len(dev_video)}; no_physical_command_generated=true.",
        )
        return diagnostic

    def diagnose(self) -> CameraHostDiagnostic:
        dev_video = self._video_entries()
        commands = [
            self._run(["bash", "-lc", "which v4l2-ctl || true"], "which v4l2-ctl || true", tool="bash"),
            self._run(["bash", "-lc", "which ffmpeg || true"], "which ffmpeg || true", tool="bash"),
            self._run(["bash", "-lc", "which python3 || true"], "which python3 || true", tool="bash"),
            self._run(["bash", "-lc", "ls -la /dev/video*"], "ls -la /dev/video*", tool="bash"),
            self._run(["lsusb"], "lsusb", tool="lsusb"),
            self._run(["bash", "-lc", "lspci | grep -i camera || true"], 'lspci | grep -i camera || true', tool="bash"),
            self._run(["v4l2-ctl", "--list-devices"], "v4l2-ctl --list-devices", tool="v4l2-ctl"),
            self._run(["v4l2-ctl", "--list-formats-ext", "-d", "/dev/video0"], "v4l2-ctl --list-formats-ext -d /dev/video0", tool="v4l2-ctl"),
            self._run(["v4l2-ctl", "--all", "-d", "/dev/video0"], "v4l2-ctl --all -d /dev/video0", tool="v4l2-ctl"),
            self._run(["bash", "-lc", "ffmpeg -f v4l2 -list_formats all -i /dev/video0 || true"], "ffmpeg -f v4l2 -list_formats all -i /dev/video0 || true", tool="ffmpeg"),
            self._run(["groups"], "groups", tool="groups"),
            self._run(["id"], "id", tool="id"),
            self._run(["bash", "-lc", 'dmesg | grep -iE "camera|uvc|video|v4l2" | tail -80'], 'dmesg | grep -iE "camera|uvc|video|v4l2" | tail -80', tool="bash"),
        ]
        diagnostic = self._build_diagnostic(
            commands=commands,
            capture_attempted=False,
            frame_captured=False,
            dev_video=dev_video,
            v4l2_available=shutil.which("v4l2-ctl") is not None,
            ffmpeg_available=shutil.which("ffmpeg") is not None,
        )
        self.latest_diagnostic = diagnostic
        self._write_latest()
        self._event(
            "vision.camera_host_diagnosed",
            diagnostic,
            f"Camera host diagnosed; status={diagnostic.camera_acceptance_status}; devices={len(dev_video)}; no_physical_command_generated=true.",
        )
        return diagnostic

    def select_camera(self, device_path: str, camera_kind: str = "unknown_camera") -> RealCameraSelection:
        groups = self._camera_groups()
        selected_group = self._group_for_path(device_path, groups)
        self.selected_camera_device = device_path
        self.selected_camera_kind = camera_kind if camera_kind != "unknown_camera" else (selected_group.camera_kind if selected_group else "unknown_camera")
        selection = RealCameraSelection(
            selected_camera_device=device_path,
            selected_camera_name=selected_group.name if selected_group else None,
            camera_kind=self.selected_camera_kind,
            advisory_only=True,
            physical_command_enabled=False,
            no_physical_command_generated=True,
        )
        payload = {
            **selection.model_dump(mode="json"),
            "type": "vision.camera_selected",
            "summary": (
                f"Camera selected; device={device_path}; kind={selection.camera_kind}; "
                "no_physical_command_generated=true."
            ),
        }
        self.logger.emit(LogLevel.INFO, "VISION", payload["summary"], payload)
        return selection

    def latest(self) -> CameraHostDiagnostic:
        if self.latest_diagnostic is None:
            return self.status()
        return self.latest_diagnostic

    def capture_blocked(self, reason: str = "host camera devices not detected") -> CameraHostDiagnostic:
        dev_video = self._video_entries()
        diagnostic = self._build_diagnostic(
            commands=[],
            capture_attempted=False,
            frame_captured=False,
            dev_video=dev_video,
            v4l2_available=shutil.which("v4l2-ctl") is not None,
            ffmpeg_available=shutil.which("ffmpeg") is not None,
            blocker_reason=reason,
        )
        self.latest_diagnostic = diagnostic
        self._write_latest()
        self._event(
            "vision.real_camera_capture_blocked",
            diagnostic,
            f"Real camera capture blocked; reason={diagnostic.blocker_reason}; no_physical_command_generated=true.",
        )
        return diagnostic

    def mark_capture_attempt(self, frame_captured: bool, reason: str) -> CameraHostDiagnostic:
        dev_video = self._video_entries()
        diagnostic = self._build_diagnostic(
            commands=[],
            capture_attempted=True,
            frame_captured=frame_captured,
            dev_video=dev_video,
            v4l2_available=shutil.which("v4l2-ctl") is not None,
            ffmpeg_available=shutil.which("ffmpeg") is not None,
            blocker_reason=reason,
        )
        self.latest_diagnostic = diagnostic
        self._write_latest()
        event_type = "vision.real_camera_capture_attempted" if frame_captured else "vision.real_camera_capture_blocked"
        self._event(
            event_type,
            diagnostic,
            f"Real camera capture attempted; captured={frame_captured}; status={diagnostic.camera_acceptance_status}; no_physical_command_generated=true.",
        )
        return diagnostic

    def inventory_json(self) -> str:
        latest = self.latest()
        payload = {
            "host_camera_devices_detected": latest.host_camera_devices_detected,
            "dev_video_entries": latest.dev_video_entries,
            "camera_groups": [group.model_dump(mode="json") for group in latest.camera_groups],
            "recommended_usb_device_path": latest.recommended_usb_device_path,
            "selected_camera_device": latest.selected_camera_device,
            "selected_camera_name": latest.selected_camera_name,
            "camera_kind": latest.camera_kind,
            "v4l2_available": latest.v4l2_available,
            "ffmpeg_available": latest.ffmpeg_available,
            "user_in_video_group": latest.user_in_video_group,
            "camera_acceptance_status": latest.camera_acceptance_status,
            "blocker_reason": latest.blocker_reason,
            "advisory_only": True,
            "physical_command_enabled": False,
            "no_physical_command_generated": True,
        }
        return json.dumps(payload, indent=2)

    def diagnostic_commands_json(self) -> str:
        latest = self.latest()
        return json.dumps(
            {
                "diagnostic_id": latest.diagnostic_id,
                "commands": [item.model_dump(mode="json") for item in latest.commands],
                "advisory_only": True,
                "physical_command_enabled": False,
                "no_physical_command_generated": True,
            },
            indent=2,
        )

    def blocker_report_markdown(self) -> str:
        latest = self.latest()
        actions = "\n".join(f"- {item}" for item in latest.suggested_actions)
        return f"""# Camera Host Blocker Report

- Diagnostic ID: {latest.diagnostic_id}
- Acceptance status: {latest.camera_acceptance_status}
- Host camera devices detected: {latest.host_camera_devices_detected}
- /dev/video entries: {', '.join(latest.dev_video_entries) if latest.dev_video_entries else 'none'}
- Recommended USB device path: {latest.recommended_usb_device_path or 'not_available'}
- Selected camera: {latest.selected_camera_device or 'not_selected'} ({latest.camera_kind})
- v4l2 available: {latest.v4l2_available}
- ffmpeg available: {latest.ffmpeg_available}
- user in video group: {latest.user_in_video_group}
- Ubuntu camera app not seen note: {latest.camera_app_not_seen_note}
- Real camera capture attempted: {latest.real_camera_capture_attempted}
- Real camera frame captured: {latest.real_camera_frame_captured}
- Blocker reason: {latest.blocker_reason}
- advisory_only=true
- physical_command_enabled=false
- no_physical_command_generated=true

## Suggested Manual Host Checks

{actions}

No motor, servo, fire, GPIO, PWM, STEP/DIR, TMC write, serial TX/write or hardware enable path was added.
"""

    def latest_json(self) -> str:
        return json.dumps(self.latest().model_dump(mode="json"), indent=2)

    def _build_diagnostic(
        self,
        *,
        commands: list[CameraHostCommandResult],
        capture_attempted: bool,
        frame_captured: bool,
        dev_video: list[str],
        v4l2_available: bool,
        ffmpeg_available: bool = False,
        blocker_reason: str | None = None,
    ) -> CameraHostDiagnostic:
        groups = self._camera_groups()
        detected = bool(dev_video)
        permission_blocked = detected and not any(os.access(path, os.R_OK) for path in dev_video)
        if frame_captured:
            status = "passed"
            reason = "real camera frame evidence captured"
        elif permission_blocked:
            status = "partial"
            reason = blocker_reason or "camera device exists but current user cannot read /dev/video*; permission blocker"
        elif detected and capture_attempted:
            status = "partial"
            reason = blocker_reason or "camera device exists but frame capture failed"
        elif detected:
            status = "partial"
            reason = blocker_reason or "camera device exists; frame capture not attempted yet"
        else:
            status = "blocked_by_host_os"
            reason = blocker_reason or "Linux host did not expose /dev/video* camera devices"
        suggested = [
            "Check BIOS/privacy camera switch and physical cable.",
            "Confirm /dev/video* exists outside sandboxed camera applications.",
            "Install v4l-utils if v4l2-ctl is missing.",
            "Check user permissions for video group without changing them automatically.",
            "Review dmesg uvcvideo/v4l2 messages for driver errors.",
            "If using Snap/Flatpak camera apps, check sandbox camera permissions manually.",
        ]
        if "microsoft" in platform.release().lower() or "wsl" in platform.platform().lower():
            suggested.append("WSL camera support may be limited; verify on native Linux host.")
            reason = "WSL or constrained Linux host may not expose camera devices"
            status = "blocked_by_host_os" if not detected else status
        return CameraHostDiagnostic(
            diagnostic_id=f"camera_host_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}",
            platform=platform.platform(),
            host_camera_devices_detected=detected,
            dev_video_entries=dev_video,
            camera_groups=groups,
            recommended_usb_device_path=self._recommended_usb_path(groups),
            selected_camera_device=self.selected_camera_device,
            selected_camera_name=(self._group_for_path(self.selected_camera_device, groups).name if self.selected_camera_device and self._group_for_path(self.selected_camera_device, groups) else None),
            camera_kind=self.selected_camera_kind,
            v4l2_available=v4l2_available,
            ffmpeg_available=ffmpeg_available,
            user_in_video_group="video" in self._groups(),
            camera_app_not_seen_note=True,
            real_camera_capture_attempted=capture_attempted,
            real_camera_frame_captured=frame_captured,
            camera_acceptance_status=status,
            blocker_reason=reason,
            commands=commands,
            suggested_actions=suggested,
            advisory_only=True,
            physical_command_enabled=False,
            no_physical_command_generated=True,
        )

    def _video_entries(self) -> list[str]:
        return sorted(glob.glob("/dev/video*"))

    def _groups(self) -> list[str]:
        result = self._run(["groups"], "groups")
        return result.output.strip().split()

    def capture_frame_evidence(self, device_path: str | None = None) -> dict:
        dev_video = self._video_entries()
        if not dev_video:
            diagnostic = self.capture_blocked("Linux host did not expose /dev/video* camera devices")
            return {
                "status": "blocked",
                "frame_captured": False,
                "blocker_reason": diagnostic.blocker_reason,
                "camera_host": diagnostic.model_dump(mode="json"),
                "physical_command_enabled": False,
                "no_physical_command_generated": True,
            }
        groups = self._camera_groups()
        device = device_path or self.selected_camera_device or self._recommended_usb_path(groups) or dev_video[0]
        selected_group = self._group_for_path(device, groups)
        camera_kind = selected_group.camera_kind if selected_group else self._kind_for_path(device)
        camera_name = selected_group.name if selected_group else None
        started = time.time()
        cap = None
        if cv2 is None:
            return self._capture_with_ffmpeg(device, camera_kind, camera_name, groups, "opencv_not_available")
        try:
            cap = cv2.VideoCapture(device)
            cap.set(3, 1280)
            cap.set(4, 720)
            try:
                cap.set(6, cv2.VideoWriter_fourcc(*"MJPG"))
            except Exception:
                pass
            if not cap.isOpened():
                return self._capture_with_ffmpeg(device, camera_kind, camera_name, groups, f"opencv_could_not_open_{device}")
            ok, frame = cap.read()
            elapsed = max(time.time() - started, 0.001)
            if not ok or frame is None:
                return self._capture_with_ffmpeg(device, camera_kind, camera_name, groups, f"opencv_frame_read_failed_{device}")
            height, width = frame.shape[:2]
            digest = hashlib.sha256(frame.tobytes()).hexdigest()
            frame_path = self.output_root / f"camera_frame_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}.jpg"
            try:
                cv2.imwrite(str(frame_path), frame)
            except Exception:
                frame_path = None
            self._record_group_result(device, True)
            diagnostic = self.mark_capture_attempt(True, "real camera frame evidence captured")
            result = {
                "status": "passed",
                "frame_captured": True,
                "device_path": device,
                "selected_camera_device": device,
                "selected_camera_name": camera_name,
                "camera_kind": camera_kind,
                "capture_method": "opencv",
                "frame_path": str(frame_path) if frame_path else None,
                "width": int(width),
                "height": int(height),
                "fps_estimate": round(1.0 / elapsed, 3),
                "frame_hash": digest,
                "frame_sha256": digest,
                "blocker_reason": "none",
                "camera_host": diagnostic.model_dump(mode="json"),
                "physical_command_enabled": False,
                "no_physical_command_generated": True,
            }
            (self.output_root / "real_camera_frame_capture_attempt.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
            event_type = "vision.usb_camera_capture_completed" if camera_kind == "external_usb_camera" else "vision.real_camera_capture_completed"
            self.logger.emit(LogLevel.INFO, "VISION", f"Camera frame capture completed; device={device}; kind={camera_kind}; no_physical_command_generated=true.", {**result, "type": event_type, "summary": f"Camera frame capture completed; device={device}; kind={camera_kind}; no_physical_command_generated=true."})
            return result
        except Exception as exc:
            return self._capture_with_ffmpeg(device, camera_kind, camera_name, groups, f"opencv_exception:{exc}")
        finally:
            if cap is not None:
                cap.release()

    def _capture_with_ffmpeg(self, device: str, camera_kind: str, camera_name: str | None, groups: list[CameraDeviceGroup], reason: str) -> dict:
        self.logger.emit(LogLevel.INFO, "VISION", f"USB camera capture attempted; device={device}; kind={camera_kind}; no_physical_command_generated=true.", {"type": "vision.usb_camera_capture_attempted", "device_path": device, "camera_kind": camera_kind, "reason": reason, "summary": f"USB camera capture attempted; device={device}; kind={camera_kind}; no_physical_command_generated=true.", "no_physical_command_generated": True, "physical_command_enabled": False})
        if shutil.which("ffmpeg") is None:
            diagnostic = self.mark_capture_attempt(False, "ffmpeg_missing")
            return self._capture_result("partial", False, device, camera_kind, camera_name, "ffmpeg", None, None, None, None, "ffmpeg_missing", diagnostic)
        output_path = self.output_root / f"camera_frame_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}.jpg"
        errors: list[str] = []
        for width, height in ((1280, 720), (640, 480), (640, 360)):
            cmd = [
                "ffmpeg",
                "-y",
                "-f",
                "v4l2",
                "-input_format",
                "mjpeg",
                "-video_size",
                f"{width}x{height}",
                "-i",
                device,
                "-frames:v",
                "1",
                "-update",
                "1",
                str(output_path),
            ]
            try:
                completed = subprocess.run(cmd, capture_output=True, text=True, timeout=8, check=False)
            except subprocess.TimeoutExpired:
                errors.append(f"{width}x{height}:timeout")
                continue
            if completed.returncode == 0 and output_path.exists() and output_path.stat().st_size > 0:
                digest = hashlib.sha256(output_path.read_bytes()).hexdigest()
                self._record_group_result(device, True)
                diagnostic = self.mark_capture_attempt(True, "real camera frame evidence captured")
                result = self._capture_result("passed", True, device, camera_kind, camera_name, "ffmpeg", output_path, width, height, digest, "none", diagnostic)
                (self.output_root / "real_camera_frame_capture_attempt.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
                self.logger.emit(LogLevel.INFO, "VISION", f"USB camera capture completed; device={device}; kind={camera_kind}; no_physical_command_generated=true.", {**result, "type": "vision.usb_camera_capture_completed", "summary": f"USB camera capture completed; device={device}; kind={camera_kind}; no_physical_command_generated=true."})
                return result
            output = (completed.stderr or completed.stdout or "")[-400:]
            errors.append(f"{width}x{height}:{completed.returncode}:{output}")
        blocker = "device_busy_or_capture_failed" if any("busy" in item.lower() for item in errors) else reason
        self._record_group_result(device, False)
        diagnostic = self.mark_capture_attempt(False, blocker)
        result = self._capture_result("partial", False, device, camera_kind, camera_name, "ffmpeg", None, None, None, None, blocker, diagnostic)
        result["ffmpeg_errors"] = errors
        (self.output_root / "real_camera_frame_capture_attempt.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        self.logger.emit(LogLevel.INFO, "VISION", f"USB camera capture failed; device={device}; kind={camera_kind}; blocker={blocker}; no_physical_command_generated=true.", {**result, "type": "vision.usb_camera_capture_failed", "summary": f"USB camera capture failed; device={device}; kind={camera_kind}; blocker={blocker}; no_physical_command_generated=true."})
        return result

    def _capture_result(self, status: str, frame_captured: bool, device: str, camera_kind: str, camera_name: str | None, method: str, frame_path: Path | None, width: int | None, height: int | None, digest: str | None, blocker: str, diagnostic: CameraHostDiagnostic) -> dict:
        return {
            "status": status,
            "frame_captured": frame_captured,
            "device_path": device,
            "selected_camera_device": device,
            "selected_camera_name": camera_name,
            "camera_kind": camera_kind,
            "capture_method": method,
            "frame_path": str(frame_path) if frame_path else None,
            "width": width,
            "height": height,
            "fps_estimate": None,
            "frame_hash": digest,
            "frame_sha256": digest,
            "blocker_reason": blocker,
            "camera_host": diagnostic.model_dump(mode="json"),
            "physical_command_enabled": False,
            "no_physical_command_generated": True,
        }

    def _camera_groups(self) -> list[CameraDeviceGroup]:
        result = self._run(["v4l2-ctl", "--list-devices"], "v4l2-ctl --list-devices", tool="v4l2-ctl")
        if result.status != "passed":
            paths = self._video_entries()
            return [CameraDeviceGroup(camera_kind=self._kind_for_path(path), name=self._name_for_kind(self._kind_for_path(path)), paths=[path], preferred_capture_path=path) for path in paths]
        groups: list[CameraDeviceGroup] = []
        current_name: str | None = None
        current_paths: list[str] = []
        for line in result.output.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if not line.startswith(("\t", " ")) and ":" in stripped:
                if current_name:
                    groups.append(self._group_from_name_paths(current_name, current_paths))
                current_name = stripped.split(":", 1)[0]
                current_paths = []
            elif stripped.startswith("/dev/video"):
                current_paths.append(stripped)
        if current_name:
            groups.append(self._group_from_name_paths(current_name, current_paths))
        self.logger.emit(LogLevel.INFO, "VISION", f"Camera inventory parsed; groups={len(groups)}; no_physical_command_generated=true.", {"type": "vision.camera_inventory_parsed", "camera_groups": [group.model_dump(mode="json") for group in groups], "summary": f"Camera inventory parsed; groups={len(groups)}; no_physical_command_generated=true.", "no_physical_command_generated": True, "physical_command_enabled": False})
        return groups

    def _group_from_name_paths(self, name: str, paths: list[str]) -> CameraDeviceGroup:
        lowered = name.lower()
        if "usb" in lowered and "hp" not in lowered:
            kind = "external_usb_camera"
        elif "hp" in lowered or "internal" in lowered:
            kind = "internal_laptop_camera"
        else:
            kind = self._kind_for_path(paths[0]) if paths else "unknown_camera"
        preferred = next((path for path in paths if path.endswith("0") or path.endswith("2")), paths[0] if paths else None)
        if kind == "external_usb_camera":
            preferred = next((path for path in paths if path.endswith("2")), preferred)
        return CameraDeviceGroup(camera_kind=kind, name=name, paths=paths, preferred_capture_path=preferred)

    def _kind_for_path(self, path: str | None) -> str:
        if path in {"/dev/video0", "/dev/video1"}:
            return "internal_laptop_camera"
        if path in {"/dev/video2", "/dev/video3"}:
            return "external_usb_camera"
        return "unknown_camera"

    def _name_for_kind(self, kind: str) -> str:
        if kind == "internal_laptop_camera":
            return "HP HD Camera"
        if kind == "external_usb_camera":
            return "HD USB Camera"
        return "Unknown Camera"

    def _group_for_path(self, path: str | None, groups: list[CameraDeviceGroup]) -> CameraDeviceGroup | None:
        if not path:
            return None
        return next((group for group in groups if path in group.paths), None)

    def _recommended_usb_path(self, groups: list[CameraDeviceGroup]) -> str | None:
        usb = next((group for group in groups if group.camera_kind == "external_usb_camera"), None)
        return usb.preferred_capture_path if usb else None

    def _record_group_result(self, device: str, captured: bool) -> None:
        groups = self._camera_groups()
        for group in groups:
            if device in group.paths:
                group.frame_captured = captured
                group.evidence_status = "passed" if captured else "partial"

    def _run(self, args: list[str], label: str, tool: str | None = None) -> CameraHostCommandResult:
        is_windows = platform.system().lower() == "windows"
        if is_windows and (tool in {"bash", "v4l2-ctl", "lsusb", "lspci", "dmesg", "groups", "id"} or (args and args[0] in {"bash", "lsusb", "v4l2-ctl", "groups", "id"})):
            return CameraHostCommandResult(command=label, status="tool_missing", output="", error=f"Linux utility not applicable on Windows: {tool or args[0]}")
        if tool and shutil.which(tool) is None:
            return CameraHostCommandResult(command=label, status="tool_missing", output="", error=f"{tool} not found")
        try:
            completed = subprocess.run(args, capture_output=True, text=True, timeout=1.0, check=False)
        except (FileNotFoundError, OSError) as exc:
            return CameraHostCommandResult(command=label, status="tool_missing", output="", error=str(exc))
        except subprocess.TimeoutExpired:
            return CameraHostCommandResult(command=label, status="timeout", output="", error="command timed out")
        output = (completed.stdout or "")[-6000:]
        error = (completed.stderr or "")[-2000:] or None
        status = "passed" if completed.returncode == 0 else "failed"
        return CameraHostCommandResult(command=label, status=status, exit_code=completed.returncode, output=output, error=error)

    def _write_latest(self) -> None:
        if self.latest_diagnostic is None:
            return
        (self.output_root / "camera_host_latest.json").write_text(self.latest_json(), encoding="utf-8")

    def _event(self, event_type: str, diagnostic: CameraHostDiagnostic, summary: str) -> None:
        payload = {
            **diagnostic.model_dump(mode="json"),
            "type": event_type,
            "summary": summary,
            "physical_command_enabled": False,
            "no_physical_command_generated": True,
        }
        self.logger.emit(LogLevel.INFO, "VISION", summary, payload)
