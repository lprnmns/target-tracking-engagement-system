from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient

from app.main import create_app


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = REPO_ROOT / "config" / "config.yaml"


def _safe_test_config(config: dict[str, Any], tmp_path: Path | None = None) -> dict[str, Any]:
    config["system"]["mode"] = "DISARMED"
    config["system"]["default_fire_policy"] = "NO_FIRE"
    config["system"]["dry_run"] = True
    config["system"]["hardware_enabled"] = False
    config["hardware"]["hardware_discovery_enabled"] = False
    config["hardware"]["physical_command_enabled"] = False
    config["hardware"]["allow_real_serial_readonly"] = False
    config["hardware"]["allow_physical_motion"] = False
    config["hardware"]["allow_physical_fire"] = False
    config["serial"]["protocol_mode"] = "json-line"
    config["serial"]["transport_mode"] = "mock"
    config["serial"]["real_serial_enabled"] = False
    config["serial"]["real_serial_readonly"] = False
    config["serial"]["auto_connect"] = False
    config["pico"]["protocol"] = "json-line"
    config["pico"]["mock"] = True
    config["camera"]["mock"] = True
    config["camera"]["camera_mode"] = "mock"
    config["camera"]["width"] = 640
    config["camera"]["height"] = 360
    config["camera"]["fps"] = 15
    config["camera"]["stream_width"] = 640
    config["camera"]["stream_height"] = 360
    config["camera"]["stream_fps"] = 15
    config["vision"]["mock"] = True
    config["vision"]["vision_mode"] = "mock"
    config["vision"]["model_loading_required"] = False
    config["models"]["default_adapter"] = "mock"
    config["dataset"]["save_mock_frames"] = True
    config["runtime_mode"]["mode"] = "development"
    config["runtime_mode"]["frontend_static_enabled"] = True
    config["runtime_mode"]["launcher_managed"] = False
    config["camera_runtime"]["default_source_type"] = "mock"
    config["camera_runtime"]["default_width"] = 640
    config["camera_runtime"]["default_height"] = 360
    config["camera_runtime"]["default_fps"] = 15
    config["camera_runtime"]["default_fourcc"] = "auto"
    config["camera_runtime"]["inference_width"] = 640
    config["camera_runtime"]["inference_height"] = 360
    config["vision_runtime"]["default_adapter"] = "opencv_circle_test"
    config["motion"]["dry_run"] = True
    config["motion"]["real_motion_enabled"] = False
    config["motion"]["pan_steps_per_degree"] = 10
    config["motion"]["tilt_steps_per_degree"] = 10
    config["pins"]["profile_name"] = "pico2_placeholder_not_final"
    config["pins"]["assignments"] = {
        "pan_step": "GP2",
        "pan_dir": "GP3",
        "tilt_step": "GP4",
        "tilt_dir": "GP5",
        "pan_driver_enable": "GP6",
        "tilt_driver_enable": "GP7",
        "trigger_servo_pwm": "GP15",
        "estop_in": "GP20",
        "pan_limit_left": "GP16",
        "pan_limit_right": "GP17",
        "tilt_limit_up": "GP18",
        "tilt_limit_down": "GP19",
    }
    if tmp_path is not None:
        config["models"]["root_dir"] = str(tmp_path / "models")
        config["models"]["active_models_file"] = str(tmp_path / "models" / "active" / "active_models.json")
        config["dataset"]["root_dir"] = str(tmp_path / "data")
        config["reports"]["root_dir"] = str(tmp_path / "exports" / "reports")
    return config


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    with DEFAULT_CONFIG.open("r", encoding="utf-8") as handle:
        config = yaml.safe_load(handle)
    assert isinstance(config, dict)
    config = _safe_test_config(config, tmp_path)
    config_path = tmp_path / "config.yaml"
    with config_path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(config, handle)
    app = create_app(config_path=config_path, log_dir=tmp_path / "logs", report_dir=tmp_path / "reports" / "self_tests")
    return TestClient(app)


@pytest.fixture
def config_data() -> dict[str, Any]:
    with DEFAULT_CONFIG.open("r", encoding="utf-8") as handle:
        loaded = yaml.safe_load(handle)
    assert isinstance(loaded, dict)
    return _safe_test_config(loaded)


def write_config(tmp_path: Path, data: dict[str, Any]) -> Path:
    path = tmp_path / "config.yaml"
    with path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(data, handle)
    return path
