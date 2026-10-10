"""Application settings loaded from a small, versioned TOML file."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_CONFIG_PATH = Path("config/parcel-dimensioner.toml")
CONFIG_ENV_VAR = "PARCEL_DIMENSIONER_CONFIG"
PREVIEW_BACKENDS = {"auto", "qtgl", "drm", "null"}


class SettingsError(ValueError):
    """Raised when application configuration is invalid."""


@dataclass(frozen=True, slots=True)
class CameraSettings:
    index: int = 0
    width: int = 1920
    height: int = 1080
    warmup_seconds: float = 2.0
    preview_backend: str = "auto"

    def __post_init__(self) -> None:
        if self.index < 0:
            raise SettingsError("camera.index must be zero or greater")
        if self.width <= 0 or self.height <= 0:
            raise SettingsError("camera width and height must be greater than zero")
        if self.warmup_seconds < 0:
            raise SettingsError("camera.warmup_seconds must be zero or greater")
        if self.preview_backend not in PREVIEW_BACKENDS:
            choices = ", ".join(sorted(PREVIEW_BACKENDS))
            raise SettingsError(f"camera.preview_backend must be one of: {choices}")


@dataclass(frozen=True, slots=True)
class AppSettings:
    captures_dir: Path = Path("data/captures")
    camera: CameraSettings = CameraSettings()


def resolve_config_path(path: Path | None = None) -> Path:
    """Resolve an explicit path, environment override, or repository default."""
    if path is not None:
        return path
    if env_path := os.environ.get(CONFIG_ENV_VAR):
        return Path(env_path)
    return DEFAULT_CONFIG_PATH


def load_settings(path: Path | None = None) -> AppSettings:
    """Load settings; a missing implicit default means use built-in defaults."""
    config_path = resolve_config_path(path)
    if not config_path.exists():
        if path is not None or os.environ.get(CONFIG_ENV_VAR):
            raise SettingsError(f"configuration file does not exist: {config_path}")
        return AppSettings()

    try:
        with config_path.open("rb") as config_file:
            raw = tomllib.load(config_file)
    except tomllib.TOMLDecodeError as exc:
        raise SettingsError(f"invalid TOML in {config_path}: {exc}") from exc

    _reject_unknown(raw, {"captures_dir", "camera"}, "root")
    camera_raw = raw.get("camera", {})
    if not isinstance(camera_raw, dict):
        raise SettingsError("camera must be a TOML table")
    _reject_unknown(
        camera_raw,
        {"index", "width", "height", "warmup_seconds", "preview_backend"},
        "camera",
    )

    try:
        camera = CameraSettings(**camera_raw)
        captures_dir = Path(raw.get("captures_dir", "data/captures"))
        return AppSettings(captures_dir=captures_dir, camera=camera)
    except (TypeError, ValueError) as exc:
        if isinstance(exc, SettingsError):
            raise
        raise SettingsError(f"invalid configuration value: {exc}") from exc


def _reject_unknown(values: dict[str, Any], allowed: set[str], section: str) -> None:
    unknown = sorted(set(values) - allowed)
    if unknown:
        raise SettingsError(f"unknown {section} setting(s): {', '.join(unknown)}")
