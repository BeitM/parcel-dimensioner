"""Application settings loaded from a small, versioned TOML file."""

from __future__ import annotations

import math
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
    lens_position: float | None = None

    def __post_init__(self) -> None:
        if self.index < 0:
            raise SettingsError("camera.index must be zero or greater")
        if self.width <= 0 or self.height <= 0:
            raise SettingsError("camera width and height must be greater than zero")
        if self.warmup_seconds < 0:
            raise SettingsError("camera.warmup_seconds must be zero or greater")
        if self.lens_position is not None and (
            not isinstance(self.lens_position, (int, float))
            or not math.isfinite(self.lens_position)
            or self.lens_position < 0
        ):
            raise SettingsError("camera.lens_position must be a finite nonnegative number")
        if self.preview_backend not in PREVIEW_BACKENDS:
            choices = ", ".join(sorted(PREVIEW_BACKENDS))
            raise SettingsError(f"camera.preview_backend must be one of: {choices}")


@dataclass(frozen=True, slots=True)
class ScaleSettings:
    port: str = "auto"
    counts_per_pound: float = -10640.0
    calibration_id: str = "prototype-20261107-5lb-10lb"
    stable_seconds: float = 1.0
    stable_range_lb: float = 0.03
    timeout_seconds: float = 10.0
    freshness_seconds: float = 0.5

    def __post_init__(self) -> None:
        if not isinstance(self.port, str) or not self.port.strip():
            raise SettingsError("scale.port must be auto or a serial port path")
        if not isinstance(self.calibration_id, str) or not self.calibration_id.strip():
            raise SettingsError("scale.calibration_id must be a nonempty string")
        for name in (
            "counts_per_pound",
            "stable_seconds",
            "stable_range_lb",
            "timeout_seconds",
            "freshness_seconds",
        ):
            value = getattr(self, name)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
            ):
                raise SettingsError(f"scale.{name} must be a finite number")
            if value == 0 or (name != "counts_per_pound" and value < 0):
                requirement = "nonzero" if name == "counts_per_pound" else "positive"
                raise SettingsError(f"scale.{name} must be {requirement}")
        if self.timeout_seconds <= self.stable_seconds:
            raise SettingsError("scale.timeout_seconds must exceed scale.stable_seconds")


@dataclass(frozen=True, slots=True)
class AppSettings:
    captures_dir: Path = Path("data/captures")
    camera: CameraSettings = CameraSettings()
    scale: ScaleSettings = ScaleSettings()


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

    _reject_unknown(raw, {"captures_dir", "camera", "scale"}, "root")
    camera_raw = raw.get("camera", {})
    if not isinstance(camera_raw, dict):
        raise SettingsError("camera must be a TOML table")
    _reject_unknown(
        camera_raw,
        {"index", "width", "height", "warmup_seconds", "preview_backend", "lens_position"},
        "camera",
    )
    scale_raw = raw.get("scale", {})
    if not isinstance(scale_raw, dict):
        raise SettingsError("scale must be a TOML table")
    _reject_unknown(scale_raw, set(ScaleSettings.__dataclass_fields__), "scale")

    try:
        camera = CameraSettings(**camera_raw)
        captures_dir = Path(raw.get("captures_dir", "data/captures"))
        return AppSettings(
            captures_dir=captures_dir, camera=camera, scale=ScaleSettings(**scale_raw)
        )
    except (TypeError, ValueError) as exc:
        if isinstance(exc, SettingsError):
            raise
        raise SettingsError(f"invalid configuration value: {exc}") from exc


def _reject_unknown(values: dict[str, Any], allowed: set[str], section: str) -> None:
    unknown = sorted(set(values) - allowed)
    if unknown:
        raise SettingsError(f"unknown {section} setting(s): {', '.join(unknown)}")
