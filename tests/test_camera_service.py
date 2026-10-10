from datetime import UTC, datetime
from pathlib import Path

from parcel_dimensioner.camera.contracts import CameraInfo, CaptureResult
from parcel_dimensioner.camera.service import CameraService
from parcel_dimensioner.settings import CameraSettings


class StubCamera:
    def __init__(self) -> None:
        self.capture_args: tuple[Path, int, int, float] | None = None

    def list_cameras(self) -> list[CameraInfo]:
        return [CameraInfo(0, "stub", "stub-0")]

    def capture_still(
        self,
        output: Path,
        *,
        width: int,
        height: int,
        warmup_seconds: float,
    ) -> CaptureResult:
        self.capture_args = (output, width, height, warmup_seconds)
        return CaptureResult(
            output,
            datetime.now(UTC),
            width,
            height,
            self.list_cameras()[0],
        )

    def preview(
        self,
        *,
        width: int,
        height: int,
        backend: str,
        duration_seconds: float,
    ) -> None:
        raise NotImplementedError


def test_capture_uses_explicit_destination(tmp_path: Path) -> None:
    backend = StubCamera()
    settings = CameraSettings(width=640, height=480, warmup_seconds=0.25)
    service = CameraService(backend, settings, tmp_path)
    output = tmp_path / "manual.jpg"

    result = service.capture(output)

    assert result.path == output
    assert backend.capture_args == (output, 640, 480, 0.25)


def test_capture_generates_timestamped_jpeg_path(tmp_path: Path) -> None:
    backend = StubCamera()
    service = CameraService(backend, CameraSettings(), tmp_path)

    result = service.capture()

    assert result.path.parent == tmp_path
    assert result.path.name.startswith("parcel-")
    assert result.path.suffix == ".jpg"
