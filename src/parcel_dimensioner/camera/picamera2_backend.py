"""Picamera2 adapter for Raspberry Pi CSI cameras."""

from __future__ import annotations

import importlib
import os
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any

from parcel_dimensioner.camera.contracts import (
    CameraDependencyError,
    CameraError,
    CameraInfo,
    CameraUnavailableError,
    CaptureResult,
)


class Picamera2Backend:
    """Own the complete lifecycle of one Picamera2 camera."""

    def __init__(self, camera_index: int = 0) -> None:
        self.camera_index = camera_index

    def list_cameras(self) -> list[CameraInfo]:
        module = _load_picamera2()
        try:
            raw_cameras = module.Picamera2.global_camera_info()
        except Exception as exc:
            raise CameraError(f"Picamera2 could not enumerate cameras: {exc}") from exc

        return [_camera_info(item, fallback_index=index) for index, item in enumerate(raw_cameras)]

    def capture_still(
        self,
        output: Path,
        *,
        width: int,
        height: int,
        warmup_seconds: float,
    ) -> CaptureResult:
        camera_info = self._selected_camera()
        module = _load_picamera2()
        camera = None
        configured_width = width
        configured_height = height
        output = output.resolve()
        output.parent.mkdir(parents=True, exist_ok=True)

        try:
            camera = module.Picamera2(self.camera_index)
            configuration = camera.create_still_configuration(main={"size": (width, height)})
            camera.configure(configuration)
            configured_width, configured_height = camera.camera_configuration()["main"]["size"]
            camera.start()
            if warmup_seconds:
                time.sleep(warmup_seconds)
            metadata = camera.capture_file(str(output)) or {}
        except Exception as exc:
            raise CameraError(f"still capture failed: {exc}") from exc
        finally:
            if camera is not None:
                camera.close()

        return CaptureResult(
            path=output,
            captured_at=datetime.now(UTC),
            width=configured_width,
            height=configured_height,
            camera=camera_info,
            metadata=_plain_metadata(metadata),
        )

    def preview(
        self,
        *,
        width: int,
        height: int,
        backend: str,
        duration_seconds: float,
    ) -> None:
        self._selected_camera()
        module = _load_picamera2()
        camera = None

        try:
            camera = module.Picamera2(self.camera_index)
            configuration = camera.create_preview_configuration(main={"size": (width, height)})
            camera.configure(configuration)
            camera.start_preview(_preview_type(module, backend))
            camera.start()
            _wait_for_preview(duration_seconds)
        except KeyboardInterrupt:
            return
        except Exception as exc:
            raise CameraError(f"camera preview failed: {exc}") from exc
        finally:
            if camera is not None:
                camera.close()

    def _selected_camera(self) -> CameraInfo:
        cameras = self.list_cameras()
        try:
            return next(camera for camera in cameras if camera.index == self.camera_index)
        except StopIteration as exc:
            available = ", ".join(str(camera.index) for camera in cameras) or "none"
            raise CameraUnavailableError(
                f"camera {self.camera_index} is unavailable (available indices: {available})"
            ) from exc


def _load_picamera2() -> ModuleType:
    try:
        return importlib.import_module("picamera2")
    except ImportError as exc:
        raise CameraDependencyError(
            "Picamera2 is not importable. On Raspberry Pi OS install "
            "python3-picamera2 with apt and create the venv with --system-site-packages."
        ) from exc


def _camera_info(raw: dict[str, Any], fallback_index: int) -> CameraInfo:
    return CameraInfo(
        index=int(raw.get("Num", fallback_index)),
        model=str(raw.get("Model", "unknown")),
        identifier=str(raw.get("Id", "unknown")),
        location=_optional_int(raw.get("Location")),
        rotation=_optional_int(raw.get("Rotation")),
    )


def _optional_int(value: Any) -> int | None:
    return None if value is None else int(value)


def _preview_type(module: ModuleType, backend: str) -> Any:
    selected = backend
    if selected == "auto":
        selected = (
            "qtgl" if os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY") else "drm"
        )
    names = {"qtgl": "QTGL", "drm": "DRM", "null": "NULL"}
    try:
        return getattr(module.Preview, names[selected])
    except (AttributeError, KeyError) as exc:
        raise CameraError(f"unsupported preview backend: {backend}") from exc


def _wait_for_preview(duration_seconds: float) -> None:
    if duration_seconds > 0:
        time.sleep(duration_seconds)
        return
    while True:
        time.sleep(1)


def _plain_metadata(metadata: Mapping[str, Any]) -> dict[str, Any]:
    """Convert common libcamera scalar values to JSON-friendly Python values."""
    return {str(key): _plain_value(value) for key, value in metadata.items()}


def _plain_value(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool, type(None))):
        return value
    if isinstance(value, Mapping):
        return {str(key): _plain_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain_value(item) for item in value]
    return str(value)
