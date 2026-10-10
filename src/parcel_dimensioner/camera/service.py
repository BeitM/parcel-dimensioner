"""Camera use cases that do not depend on Picamera2 details."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from parcel_dimensioner.camera.contracts import CameraBackend, CaptureResult
from parcel_dimensioner.settings import CameraSettings


class CameraService:
    def __init__(
        self,
        backend: CameraBackend,
        settings: CameraSettings,
        captures_dir: Path,
    ) -> None:
        self.backend = backend
        self.settings = settings
        self.captures_dir = captures_dir

    def capture(self, output: Path | None = None) -> CaptureResult:
        destination = output or self._next_capture_path()
        return self.backend.capture_still(
            destination,
            width=self.settings.width,
            height=self.settings.height,
            warmup_seconds=self.settings.warmup_seconds,
        )

    def _next_capture_path(self) -> Path:
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
        return self.captures_dir / f"parcel-{timestamp}.jpg"
