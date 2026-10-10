"""Read-only checks for the Pi camera environment."""

from __future__ import annotations

import platform
import shutil
import subprocess
from dataclasses import dataclass

from parcel_dimensioner.camera.contracts import CameraDependencyError, CameraError
from parcel_dimensioner.camera.picamera2_backend import Picamera2Backend


@dataclass(frozen=True, slots=True)
class Diagnostic:
    name: str
    ok: bool
    detail: str


def camera_diagnostics(camera_index: int = 0) -> list[Diagnostic]:
    checks = [
        Diagnostic("platform", platform.system() == "Linux", platform.platform()),
        _check_rpicam_command(),
    ]
    try:
        backend = Picamera2Backend(camera_index)
        cameras = backend.list_cameras()
        checks.append(Diagnostic("Picamera2 import", True, "available"))
        detail = ", ".join(f"{camera.index}: {camera.model}" for camera in cameras)
        checks.append(Diagnostic("camera detection", bool(cameras), detail or "none detected"))
    except CameraDependencyError as exc:
        checks.append(Diagnostic("Picamera2 import", False, str(exc)))
        checks.append(Diagnostic("camera detection", False, "not checked"))
    except CameraError as exc:
        checks.append(Diagnostic("Picamera2 import", True, "available"))
        checks.append(Diagnostic("camera detection", False, str(exc)))
    return checks


def _check_rpicam_command() -> Diagnostic:
    executable = shutil.which("rpicam-hello")
    if executable is None:
        return Diagnostic("rpicam-hello", False, "command not found")
    try:
        result = subprocess.run(
            [executable, "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
        detail = (result.stdout or result.stderr).strip().splitlines()[0]
        return Diagnostic("rpicam-hello", result.returncode == 0, detail or executable)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return Diagnostic("rpicam-hello", False, str(exc))
