"""One camera owner and a reusable empty reference for the touchscreen workflow."""

from __future__ import annotations

import json
import math
import time
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from parcel_dimensioner.barcodes import (
    PackageIdError,
    decode_image,
    initial_identity,
    with_identity,
)
from parcel_dimensioner.camera.contracts import CameraError
from parcel_dimensioner.camera.picamera2_backend import (
    _load_picamera2,
    _plain_metadata,
    _set_manual_focus,
)
from parcel_dimensioner.measurement import (
    BOARDS,
    MeasurementError,
    _detect_wall,
    _floor_line,
    _vision,
)
from parcel_dimensioner.settings import CameraSettings


class StationCamera:
    """Used only on the worker thread; never opens a second preview camera."""

    def __init__(self, settings: CameraSettings):
        self.settings = settings
        self.camera = None
        self.position = settings.lens_position

    def start(self):
        self.camera = _load_picamera2().Picamera2(self.settings.index)
        self.camera.configure(
            self.camera.create_still_configuration(
                main={"size": (self.settings.width, self.settings.height), "format": "BGR888"},
                buffer_count=4,
                queue=False,
            )
        )
        if self.position is not None:
            _set_manual_focus(self.camera, self.position)
        self.camera.start()
        time.sleep(self.settings.warmup_seconds)
        if self.position is None:
            if not self.camera.autofocus_cycle():
                raise CameraError("Focus failed. Improve lighting and reopen the app.")
            self.position = self.camera.capture_metadata().get("LensPosition")
            if self.position is None:
                raise CameraError("Camera did not report focus. Set --lens-position explicitly.")
            _set_manual_focus(self.camera, float(self.position))
        time.sleep(0.5)

    def preview(self):
        image = self.camera.capture_image("main")
        image.thumbnail((800, 450))
        return image

    def capture(self, path):
        metadata = _plain_metadata(self.camera.capture_file(str(path)) or {})
        reported = metadata.get("LensPosition")
        if not isinstance(reported, (int, float)) or not math.isclose(
            reported, self.position, abs_tol=0.05
        ):
            raise CameraError("Focus changed. Reopen the app and capture the empty station again.")
        return metadata

    def close(self):
        if self.camera is not None:
            self.camera.close()
            self.camera = None


def validate_reference(path):
    cv, np = _vision()
    image = cv.imread(str(path))
    if image is None:
        raise MeasurementError("Cannot read the empty image.")
    for spec in BOARDS:
        wall = _detect_wall(image, spec, 38.1, 28.0, cv, np)
        _floor_line(image, wall, 38.1, cv, np)


class ScanSession:
    """Persist all attempts; never reuse a reference across app restarts."""

    def __init__(
        self,
        root: Path,
        camera,
        settings: CameraSettings,
        validator=validate_reference,
        measure=None,
        scale=None,
        cancel=None,
        decoder=decode_image,
    ):
        from parcel_dimensioner.measurement import save_measurement

        self.directory = root / (
            datetime.now(UTC).strftime("station-%Y%m%dT%H%M%S-") + uuid4().hex[:8]
        )
        self.directory.mkdir(parents=True, exist_ok=False)
        self.camera = camera
        self.settings = settings
        self.validator = validator
        self.measure = measure or save_measurement
        self.reference = None
        self.reference_metadata = None
        self.counter = 0
        self.scale = scale
        self.cancel = cancel
        self.decoder = decoder
        self.last_scan = None

    def _capture(self, path):
        metadata = self.camera.capture(path)
        record = {
            "captured_at": datetime.now(UTC).isoformat(),
            "camera_settings": asdict(self.settings),
            "metadata": metadata,
        }
        path.with_suffix(".json").write_text(json.dumps(record, indent=2), encoding="utf-8")
        return metadata

    def capture_empty(self):
        self.last_scan = None
        self.reference = self.reference_metadata = None
        path = self.directory / f"empty-{uuid4().hex[:8]}.jpg"
        metadata = self._capture(path)
        self.validator(path)
        self.reference, self.reference_metadata = path, metadata
        return path

    def scan(self, *, dimensions_only_reason=None):
        self.last_scan = None
        if self.reference is None:
            raise MeasurementError("Capture the empty station first.")
        self.counter += 1
        folder = self.directory / f"scan-{self.counter:04}"
        folder.mkdir(exist_ok=False)
        weight = {
            "status": "unavailable",
            "pounds": None,
            "unit": "lb",
            "stable": False,
            "reason": dimensions_only_reason or "Scale not configured",
        }
        try:
            ticket = None
            if self.scale is not None and dimensions_only_reason is None:
                ticket = self.scale.wait_stable(self.cancel)
            path = folder / "box.jpg"
            metadata = self._capture(path)
            if ticket is not None:
                weight = self.scale.verify_capture(ticket, self.cancel)
            if metadata.get("ScalerCrop") != self.reference_metadata.get("ScalerCrop"):
                self.reference = None
                raise MeasurementError("Camera crop changed. Capture a new empty station.")
            result = self.measure(self.reference, path, folder / "review")
            barcode = self.decoder(path, outline=result.get("box_contour_pixels", []))
            result = {
                **result,
                "scan_id": uuid4().hex,
                "weight": weight,
                "scan_mode": "dimensions_and_weight" if ticket else "dimensions_only",
                "barcode": barcode,
                **initial_identity(barcode),
            }
            self._save_result(folder, result)
            self.last_scan = folder, result
            return result, folder / "review" / "measurement.jpg"
        except Exception as exc:
            (folder / "error.txt").write_text(str(exc), encoding="utf-8")
            (folder / "attempt.json").write_text(
                json.dumps(
                    {
                        "status": "failed",
                        "error": str(exc),
                        "weight": weight,
                        "dimensions_only_reason": dimensions_only_reason,
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            raise

    @staticmethod
    def _save_result(folder, result):
        temporary = folder / "result.json.tmp"
        temporary.write_text(json.dumps(result, indent=2), encoding="utf-8")
        temporary.replace(folder / "result.json")

    def set_package_id(self, scan_id, value, candidate_index=None):
        if self.last_scan is None or self.last_scan[1]["scan_id"] != scan_id:
            raise PackageIdError("This scan is no longer current. Measure the box again.")
        folder, original = self.last_scan
        result = with_identity(original, value, candidate_index)
        self._save_result(folder, result)
        self.last_scan = folder, result
        return result
