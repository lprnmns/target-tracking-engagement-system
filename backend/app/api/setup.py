from __future__ import annotations

import glob
import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.services.storage_paths import project_root
from app.api.deps import get_runtime
from app.services.runtime_state import RuntimeState
from app.schemas.vision import VisionConfigUpdate


router = APIRouter(prefix="/api/setup", tags=["setup-wizard"])

CONFIG_PATH = project_root() / "config" / "runtime" / "setup_wizard_profile.json"


@router.post("/reset-session")
async def reset_setup_session(runtime: RuntimeState = Depends(get_runtime)) -> dict[str, bool]:
    """Enter Setup safely without destroying the active device profile.

    A browser refresh is a UI/session event, not an operator request to forget
    the selected camera, models or Pico transport.  Physical commands still
    require a fresh visible preflight/arm action after entering Setup.
    """
    # Entering Setup must never leave a previous autonomous tracking loop in
    # control of the turret.  This is a software stop only; it does not alter
    # the saved camera/model/Pico profile.
    await runtime.tracking_loop.stop()
    runtime.auto_tracker.stop_tracking()
    runtime.command_gateway.stop_motion()
    runtime.command_gateway.invalidate_preflight(runtime, "SETUP_SESSION_RESET")
    return {
        "reset": True,
        "camera_running": runtime.camera_runtime.status().running,
        "vision_running": runtime.vision_pipeline.status().running,
        "pico_connected": runtime.serial.status().real_serial_enabled,
    }


class CameraSettings(BaseModel):
    brightness: int = Field(default=0, ge=-100, le=100)
    contrast: int = Field(default=0, ge=-100, le=100)
    saturation: int = Field(default=0, ge=-100, le=100)
    exposure: int = Field(default=0, ge=-100, le=100)
    auto_exposure: bool = True


class ModelConfig(BaseModel):
    air_target_model_path: str = ""
    balloon_model_path: str = "models/balloon/best.pt"
    person_safety_model_path: str = ""
    air_target_confidence: float = Field(default=0.25, ge=0.0, le=1.0)
    balloon_confidence: float = Field(default=0.05, ge=0.0, le=1.0)
    person_safety_confidence: float = Field(default=0.35, ge=0.0, le=1.0)


class SetupProfile(BaseModel):
    profile_name: str = "Geliştirme / Laptop Profil"
    operation_mode: str = "Sadece Kamera + Dijital İkiz"
    selected_camera_id: str = ""
    selected_camera_path: str = ""
    camera_resolution: str = "1280x720"
    camera_fps: int = 30
    camera_settings: CameraSettings = Field(default_factory=CameraSettings)
    selected_pico_port: str = ""
    baudrate: int = 460800
    models: ModelConfig = Field(default_factory=ModelConfig)
    motion: dict[str, Any] = Field(default_factory=lambda: {
        "yaw_max_speed": 30,
        "pitch_max_speed": 20,
        "acceleration_limit": 80,
        "deadzone": 1.5,
        "smoothing": 0.35,
        "yaw_pid": {"kp": 0.8, "ki": 0.0, "kd": 0.08},
        "pitch_pid": {"kp": 0.8, "ki": 0.0, "kd": 0.08},
    })
    safety: dict[str, bool] = Field(default_factory=lambda: {
        "physical_command_enabled": False,
        "serial_tx_enabled": False,
        "no_physical_command_generated": True,
    })


class ModelValidateRequest(BaseModel):
    path: str
    run_test_inference: bool = False


class PicoPortRequest(BaseModel):
    port: str = ""
    baudrate: int = 460800


class MotorTestRequest(BaseModel):
    axis: str
    direction: str
    step_deg: float = Field(default=1.0, ge=0.0, le=5.0)
    speed: float = Field(default=10.0, ge=0.0, le=50.0)
    physical_unlock: bool = False


class ActuatorSafeTestRequest(BaseModel):
    checklist: dict[str, bool] = Field(default_factory=dict)
    explicit_unlock: bool = False


def _safe_flags() -> dict[str, Any]:
    return {
        "visualization_only": True,
        "physical_command_enabled": False,
        "serial_tx_enabled": False,
        "no_physical_command_generated": True,
    }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path, fallback: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return fallback


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _run_text(command: list[str], timeout: float = 1.5) -> str:
    try:
        completed = subprocess.run(command, check=False, capture_output=True, text=True, timeout=timeout)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""
    return f"{completed.stdout}\n{completed.stderr}".strip()


def _video_device_name(path: str) -> str:
    sysfs_name = Path("/sys/class/video4linux") / Path(path).name / "name"
    try:
        name = sysfs_name.read_text(encoding="utf-8").strip()
        if name:
            return name
    except OSError:
        pass
    return Path(path).name


def _stable_video_path(path: str) -> str:
    real_path = os.path.realpath(path)
    for symlink in sorted(glob.glob("/dev/v4l/by-id/*") + glob.glob("/dev/v4l/by-path/*")):
        if os.path.realpath(symlink) == real_path:
            return symlink
    return ""


def _camera_busy_by(path: str) -> list[str]:
    text = _run_text(["fuser", path], timeout=0.8)
    pids = sorted({pid for pid in re.findall(r"\b\d+\b", text)})
    holders: list[str] = []
    for pid in pids:
        command = _run_text(["ps", "-p", pid, "-o", "comm="], timeout=0.8).strip()
        holders.append(f"{command or 'pid'}({pid})")
    return holders


def _camera_formats(path: str) -> tuple[list[str], list[int], list[str]]:
    text = _run_text(["v4l2-ctl", f"--device={path}", "--list-formats-ext"], timeout=1.5)
    resolutions = sorted(set(re.findall(r"Size:\s+Discrete\s+(\d+x\d+)", text)), key=lambda item: tuple(int(part) for part in item.split("x")))
    fps_values = sorted({int(round(float(value))) for value in re.findall(r"\((\d+(?:\.\d+)?)\s+fps\)", text)})
    pixel_formats = sorted(set(re.findall(r"\[\d+\]:\s+'([^']+)'", text)))
    return resolutions, fps_values, pixel_formats


def _camera_controls(path: str) -> dict[str, str]:
    text = _run_text(["v4l2-ctl", f"--device={path}", "--list-ctrls"], timeout=1.5)
    def support(*names: str) -> str:
        return "device" if any(name in text for name in names) else "unsupported"

    return {
        "brightness": support("brightness"),
        "contrast": support("contrast"),
        "saturation": support("saturation"),
        "exposure": support("exposure_time_absolute", "auto_exposure", "exposure_auto"),
    }


def _camera_inventory() -> list[dict[str, Any]]:
    cameras: list[dict[str, Any]] = []
    usable_index = 0
    for path in sorted(glob.glob("/dev/video*")):
        resolutions, fps_options, pixel_formats = _camera_formats(path)
        if not resolutions:
            continue
        usable_index += 1
        busy_by = _camera_busy_by(path)
        if busy_by:
            last_status = f"busy_by:{', '.join(busy_by)}"
        elif not os.access(path, os.R_OK):
            last_status = "permission_denied"
        else:
            last_status = "available"
        cameras.append({
            "id": path,
            "name": _video_device_name(path) or f"Kamera {usable_index}",
            "device_path": path,
            "stable_path": _stable_video_path(path),
            "resolutions": resolutions,
            "fps_options": fps_options or [30],
            "pixel_formats": pixel_formats,
            "last_status": last_status,
            "busy_by": busy_by,
            "supports_controls": _camera_controls(path),
        })
    return cameras


def _serial_inventory() -> list[dict[str, Any]]:
    devices: dict[str, dict[str, Any]] = {}

    def is_relevant_serial(path: str) -> bool:
        if path.upper().startswith("COM"):
            return True
        return (
            path.startswith("/dev/ttyACM")
            or path.startswith("/dev/ttyUSB")
            or path.startswith("/dev/serial/")
        )

    try:
        from serial.tools import list_ports  # type: ignore[import-not-found]

        for port in list_ports.comports():
            if not is_relevant_serial(port.device):
                continue
            devices[port.device] = {
                "port": port.device,
                "name": port.name,
                "description": port.description,
                "hwid": port.hwid,
                "baudrate": 460800,
                "last_status": "available",
                "platform_hint": "linux_or_windows",
            }
    except Exception:
        pass
    for path in sorted(set(glob.glob("/dev/ttyACM*") + glob.glob("/dev/ttyUSB*") + glob.glob("/dev/serial/by-id/*"))):
        devices.setdefault(path, {
            "port": path,
            "name": Path(path).name,
            "description": "Serial device",
            "hwid": "",
            "baudrate": 460800,
            "last_status": "available" if os.access(path, os.R_OK | os.W_OK) else "permission_denied",
            "platform_hint": "linux",
        })
    return list(devices.values())


@router.get("/cameras")
def list_setup_cameras() -> dict[str, Any]:
    return {
        "ok": True,
        "cameras": _camera_inventory(),
        "message": "Kameralar tarandı.",
        **_safe_flags(),
    }


@router.post("/camera/apply")
def apply_camera_settings(settings: CameraSettings) -> dict[str, Any]:
    return {
        "ok": True,
        "applied": True,
        "settings": settings.model_dump(),
        "message": "Ayar uygulandı. Donanım desteklemiyorsa görüntü önizleme filtresi olarak kullanılır.",
        "unsupported_controls": ["exposure"] if not settings.auto_exposure else [],
        **_safe_flags(),
    }


@router.get("/serial-devices")
def list_serial_devices() -> dict[str, Any]:
    return {
        "ok": True,
        "devices": _serial_inventory(),
        "permission_help": [
            "sudo usermod -aG dialout $USER",
            "sudo chmod 666 /dev/ttyACM1",
        ],
        **_safe_flags(),
    }


@router.post("/pico/connect")
def pico_connect(request: PicoPortRequest) -> dict[str, Any]:
    found = any(device["port"] == request.port for device in _serial_inventory())
    return {
        "ok": found,
        "connected": False,
        "port": request.port,
        "baudrate": request.baudrate,
        "stages": {
            "command_generated": False,
            "serial_written": False,
            "reached_pico": False,
            "ack_received": False,
        },
        "message": "Dry-run: seri TX kapalı olduğu için gerçek bağlantı komutu gönderilmedi." if found else "Port bulunamadı.",
        "deprecated": True,
        "replacement": "/api/safety/pico-connect then /api/safety/preflight",
        **_safe_flags(),
    }


@router.post("/pico/heartbeat")
def pico_heartbeat(request: PicoPortRequest) -> dict[str, Any]:
    return {
        "ok": bool(request.port),
        "port": request.port,
        "stages": {
            "command_generated": False,
            "serial_written": False,
            "reached_pico": False,
            "ack_received": False,
        },
        "message": "Heartbeat testi güvenli dry-run modunda simüle edildi; seri hatta yazılmadı.",
        "deprecated": True,
        "replacement": "/api/safety/preflight",
        **_safe_flags(),
    }


@router.post("/pico/ack-test")
def pico_ack_test(request: PicoPortRequest) -> dict[str, Any]:
    return {
        "ok": bool(request.port),
        "port": request.port,
        "stages": {
            "command_generated": False,
            "serial_written": False,
            "reached_pico": False,
            "ack_received": False,
            "telemetry_received": False,
        },
        "message": "ACK testi dry-run/no-TX durumunda fiziksel cihaza gönderilmedi.",
        "deprecated": True,
        "replacement": "/api/safety/preflight",
        **_safe_flags(),
    }


@router.post("/models/validate")
def validate_model(request: ModelValidateRequest) -> dict[str, Any]:
    path = Path(request.path).expanduser()
    exists = path.exists()
    size_mb = round(path.stat().st_size / (1024 * 1024), 2) if exists else 0.0
    classes: list[str] = []
    loadable = False
    error = ""
    if exists:
        try:
            from ultralytics import YOLO  # type: ignore[import-not-found]

            model = YOLO(str(path))
            names = getattr(model, "names", {}) or {}
            if isinstance(names, dict):
                classes = [str(value) for _, value in sorted(names.items())]
            else:
                classes = [str(value) for value in names]
            loadable = True
        except Exception as exc:
            error = str(exc)
    return {
        "ok": exists and loadable,
        "path": str(path),
        "exists": exists,
        "loadable": loadable,
        "class_names": classes,
        "size_mb": size_mb,
        "test_inference": "not_run" if not request.run_test_inference else ("load_smoke_ok" if loadable else "failed"),
        "message": "Model yüklenebilir." if exists and loadable else (error or "Model dosyası bulunamadı."),
        **_safe_flags(),
    }


@router.get("/config/load")
def load_setup_config() -> dict[str, Any]:
    profile = _read_json(CONFIG_PATH, SetupProfile().model_dump())
    profile.setdefault("safety", SetupProfile().safety)
    profile["safety"] = SetupProfile().safety
    return {
        "ok": True,
        "path": str(CONFIG_PATH.relative_to(project_root())),
        "profile": profile,
        **_safe_flags(),
    }


@router.post("/config/save")
def save_setup_config(profile: SetupProfile) -> dict[str, Any]:
    data = profile.model_dump()
    data["safety"] = SetupProfile().safety
    data["saved_at"] = _now()
    _write_json(CONFIG_PATH, data)
    return {
        "ok": True,
        "saved": True,
        "path": str(CONFIG_PATH.relative_to(project_root())),
        "profile": data,
        "message": "Konfigürasyon kaydedildi.",
        **_safe_flags(),
    }


@router.post("/motor/test")
def motor_test(request: MotorTestRequest) -> dict[str, Any]:
    return {
        "ok": True,
        "mode": "dry_run_preview",
        "axis": request.axis,
        "direction": request.direction,
        "step_deg": request.step_deg,
        "speed": request.speed,
        "stages": {
            "command_generated": True,
            "serial_written": False,
            "pico_ack": False,
            "driver_status_received": False,
            "digital_twin_preview_updated": True,
        },
        "message": "Motor testi dry-run önizlemede çalıştı; fiziksel komut üretilmedi veya seri hatta yazılmadı.",
        "deprecated": True,
        "replacement": "/api/motion/jog (CommandGateway preflight required)",
        **_safe_flags(),
    }


@router.post("/actuator/safe-test")
def actuator_safe_test(request: ActuatorSafeTestRequest) -> dict[str, Any]:
    required = ["line_clear", "no_projectile", "dummy_mechanism", "emergency_stop", "operator_confirmed"]
    missing = [item for item in required if not request.checklist.get(item)]
    unlocked = request.explicit_unlock and not missing
    return {
        "ok": unlocked,
        "locked": not unlocked,
        "missing_checklist": missing,
        "stages": {
            "command_generated": False,
            "serial_written": False,
            "pico_ack": False,
            "actuator_command_accepted": False,
        },
        "message": "Güvenli aktüatör testi kilitli. Gerçek ateşleme komutu yok." if not unlocked else "Dummy aktüatör testi dry-run olarak doğrulandı; seri TX yok.",
        "no_real_firing": True,
        "deprecated": True,
        "replacement": "/api/safety/preflight with actuator_arm",
        **_safe_flags(),
    }
