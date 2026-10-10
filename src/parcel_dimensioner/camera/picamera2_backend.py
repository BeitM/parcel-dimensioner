"""Picamera2 adapter for Raspberry Pi CSI cameras."""

from __future__ import annotations

import importlib
import json
import math
import os
import time
from collections.abc import Mapping
from dataclasses import asdict
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

    def __init__(self, camera_index: int = 0, lens_position: float | None = None) -> None:
        self.camera_index = camera_index
        self.lens_position = lens_position

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
            if self.lens_position is not None:
                _set_manual_focus(camera, self.lens_position)
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
            if self.lens_position is not None:
                _set_manual_focus(camera, self.lens_position)
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

    def capture_calibration(
        self,
        output_dir: Path,
        *,
        width: int,
        height: int,
        warmup_seconds: float,
        preview_backend: str,
        count: int,
    ) -> int:
        """Collect prompted stills in one camera session with a fixed lens position."""
        info = self._selected_camera()
        module = _load_picamera2()
        camera = None
        saved = 0
        output_dir = output_dir.resolve()
        try:
            # Refuse an existing directory so a previous dataset cannot be overwritten.
            output_dir.mkdir(parents=True, exist_ok=False)
            camera = module.Picamera2(self.camera_index)
            camera.configure(
                camera.create_still_configuration(
                    # Native Wayland GL cannot display the still default BGR888.
                    main={"size": (width, height), "format": "XBGR8888"},
                    display="main",
                    buffer_count=4,
                )
            )
            if self.lens_position is not None:
                _set_manual_focus(camera, self.lens_position)
            camera.start_preview(_preview_type(module, preview_backend))
            camera.start()
            time.sleep(warmup_seconds)
            if self.lens_position is None:
                input("Keep a board at its normal station distance. Press Enter to focus once: ")
                _require_focus_controls(camera)
                if not camera.autofocus_cycle():
                    raise CameraError("Autofocus failed; improve lighting or use --lens-position.")
                position = camera.capture_metadata().get("LensPosition")
                if position is None:
                    raise CameraError("Camera did not report a LensPosition after autofocus.")
                position = float(position)
                _set_manual_focus(camera, position)
            else:
                position = self.lens_position
            # Allow the manual control and queued requests to settle before collection.
            time.sleep(max(0.5, warmup_seconds))
            actual_size = camera.camera_configuration()["main"]["size"]
            manifest = {
                "kind": "lens-calibration-images",
                "created_at": datetime.now(UTC).isoformat(),
                "camera": asdict(info),
                "camera_configuration": _plain_value(camera.camera_configuration()),
                "image_size": list(actual_size),
                "lens_position": position,
                "requested_count": count,
                "images": [],
            }
            manifest_path = output_dir / "session.json"
            _write_session(manifest_path, manifest)
            (output_dir / "camera.toml").write_text(
                "[camera]\n"
                f"index = {self.camera_index}\n"
                f"width = {actual_size[0]}\nheight = {actual_size[1]}\n"
                f"warmup_seconds = {warmup_seconds}\n"
                f"preview_backend = {json.dumps(preview_backend)}\n"
                f"lens_position = {position}\n",
                encoding="utf-8",
            )
            print(f"Focus locked at LensPosition={position:.6g}. Keep this for later captures.")
            print(f"Reusable camera settings: {output_dir / 'camera.toml'}")
            print("Move ONE board, keep the camera fixed. Vary position and tilt; hold still.")
            for index in range(1, count + 1):
                answer = input(f"Image {index}/{count}: Enter to capture, q to finish: ")
                if answer.strip().lower() == "q":
                    break
                path = output_dir / f"calibration-{index:03d}.jpg"
                metadata = _plain_metadata(camera.capture_file(str(path)) or {})
                reported = metadata.get("LensPosition")
                if not isinstance(reported, (int, float)) or not math.isclose(
                    reported, position, abs_tol=0.05
                ):
                    path.unlink(missing_ok=True)
                    raise CameraError("Lens position could not be verified; frame discarded.")
                manifest["images"].append(
                    {
                        "file": path.name,
                        "captured_at": datetime.now(UTC).isoformat(),
                        "metadata": metadata,
                    }
                )
                _write_session(manifest_path, manifest)
                saved += 1
                print(f"Saved {path}")
            return saved
        except (KeyboardInterrupt, EOFError):
            print(f"Collection stopped; {saved} images retained in {output_dir}.")
            return saved
        except CameraError:
            raise
        except Exception as exc:
            raise CameraError(f"calibration capture failed: {exc}") from exc
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


def _require_focus_controls(camera: Any) -> None:
    if not {"AfMode", "LensPosition"}.issubset(camera.camera_controls):
        raise CameraError("This command requires a camera with AfMode and LensPosition controls.")


def _set_manual_focus(camera: Any, position: float) -> None:
    _require_focus_controls(camera)
    lower, upper, _ = camera.camera_controls["LensPosition"]
    if not math.isfinite(position) or not lower <= position <= upper:
        raise CameraError(f"LensPosition must be between {lower} and {upper} for this camera.")
    controls = importlib.import_module("libcamera").controls
    camera.set_controls({"AfMode": controls.AfModeEnum.Manual, "LensPosition": position})


def _write_session(path: Path, manifest: dict[str, Any]) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    temporary.replace(path)


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
