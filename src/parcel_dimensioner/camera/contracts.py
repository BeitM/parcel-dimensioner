"""Hardware-independent camera types."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol


class CameraError(RuntimeError):
    """Base error for actionable camera failures."""


class CameraDependencyError(CameraError):
    """The Raspberry Pi camera software is not installed or importable."""


class CameraUnavailableError(CameraError):
    """No matching camera can be opened."""


@dataclass(frozen=True, slots=True)
class CameraInfo:
    index: int
    model: str
    identifier: str
    location: int | None = None
    rotation: int | None = None


@dataclass(frozen=True, slots=True)
class CaptureResult:
    path: Path
    captured_at: datetime
    width: int
    height: int
    camera: CameraInfo
    metadata: dict[str, Any] = field(default_factory=dict)


class CameraBackend(Protocol):
    """Operations needed by camera bring-up and, later, the scan workflow."""

    def list_cameras(self) -> list[CameraInfo]: ...

    def capture_still(
        self,
        output: Path,
        *,
        width: int,
        height: int,
        warmup_seconds: float,
    ) -> CaptureResult: ...

    def preview(
        self,
        *,
        width: int,
        height: int,
        backend: str,
        duration_seconds: float,
    ) -> None: ...
