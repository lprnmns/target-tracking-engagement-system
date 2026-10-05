from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from app.schemas.config import AppConfig


class ConfigService:
    def __init__(self, config_path: Path) -> None:
        self.config_path = config_path

    def load(self) -> AppConfig:
        raw = self._read_yaml()
        return AppConfig.model_validate(raw)

    def _read_yaml(self) -> dict[str, Any]:
        if not self.config_path.exists():
            raise FileNotFoundError(f"Config file not found: {self.config_path}")
        with self.config_path.open("r", encoding="utf-8") as handle:
            loaded = yaml.safe_load(handle) or {}
        if not isinstance(loaded, dict):
            raise ValueError("Config root must be a mapping")
        return loaded


def default_config_path() -> Path:
    return Path(__file__).resolve().parents[3] / "config" / "config.yaml"


def format_config_error(error: ValidationError | ValueError | FileNotFoundError) -> str:
    return str(error)

