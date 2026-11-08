"""One camera owner and a reusable empty reference for the touchscreen workflow."""

from __future__ import annotations

import json
import math
import shutil
import time
from contextlib import suppress
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from parcel_dimensioner.barcodes import (
    PackageIdError,
    barcode_result,
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
from parcel_dimensioner.label_capture import LabelRestoreError, capture_labels
from parcel_dimensioner.label_reader import target_bounds
from parcel_dimensioner.measurement import (
    BOARDS,
    MeasurementError,
    _detect_wall,
    _floor_line,
    _measurement_image,
    _vision,
)
from parcel_dimensioner.review import save_scan_review
from parcel_dimensioner.settings import CameraSettings


class StationCamera:
    """Used only on the worker thread; never opens a second preview camera."""

    def __init__(self, settings: CameraSettings):
        self.settings = settings
        self.camera = None
        self.position = settings.lens_position
        self.restore_failed = False

    def start(self):
        self.camera = _load_picamera2().Picamera2(self.settings.index)
        self.camera.configure(
            self.camera.create_still_configuration(
                main={"size": (self.settings.width, self.settings.height), "format": "BGR888"},
                lores={"size": (800, 450), "format": "YUV420"},
                raw=None,
                buffer_count=2,
                queue=False,
            )
        )
        self.camera.options["quality"] = 95
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
        from PIL import Image

        self._check_restored()
        cv, _ = _vision()
        # Convert only the small stream. YUV includes stride padding; crop RGB
        # after conversion, as required by Picamera2's planar array layout.
        frame = self.camera.capture_array("lores")
        width, height = self.camera.camera_configuration()["lores"]["size"]
        rgb = cv.cvtColor(frame, cv.COLOR_YUV2RGB_I420)[:height, :width]
        return Image.fromarray(rgb)

    def capture(self, path):
        self._check_restored()
        metadata = _plain_metadata(self.camera.capture_file(str(path)) or {})
        reported = metadata.get("LensPosition")
        if not isinstance(reported, (int, float)) or not math.isclose(
            reported, self.position, abs_tol=0.05
        ):
            raise CameraError("Focus changed. Reopen the app and capture the empty station again.")
        return metadata

    def capture_labels(self, directory, target, *, cancel=None, progress=None):
        self._check_restored()
        try:
            return capture_labels(
                self.camera, self.position, directory, target, cancel=cancel, progress=progress
            )
        except LabelRestoreError:
            self.restore_failed = True
            raise

    def _check_restored(self):
        if self.restore_failed:
            raise CameraError("Station camera settings could not be restored. Reopen the app.")

    def close(self):
        if self.camera is not None:
            self.camera.close()
            self.camera = None


def validate_reference(path):
    cv, np = _vision()
    image = cv.imread(str(path))
    if image is None:
        raise MeasurementError("Cannot read the empty image.")
    image = _measurement_image(image, cv)
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
        reviewer=save_scan_review,
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
        self.reviewer = reviewer
        self.review_path = None
        self.review_warning = ""
        self.pending_label = None

    def _capture(self, path):
        metadata = self.camera.capture(path)
        self._capture_record(path, metadata)
        return metadata

    def _capture_record(self, path, metadata, captured_at=None):
        record = {
            "captured_at": captured_at or datetime.now(UTC).isoformat(),
            "camera_settings": asdict(self.settings),
            "metadata": metadata,
        }
        path.with_suffix(".json").write_text(json.dumps(record, indent=2), encoding="utf-8")

    def capture_empty(self):
        self.last_scan = None
        self.clear_label()
        self.reference = self.reference_metadata = None
        path = self.directory / f"empty-{uuid4().hex[:8]}.jpg"
        metadata = self._capture(path)
        self.validator(path)
        self.reference, self.reference_metadata = path, metadata
        return path

    def clear_label(self):
        self.pending_label = None

    def scan_label(self, target=(0.5, 0.5), *, progress=None):
        self.last_scan = None
        self.clear_label()
        folder = self.directory / f"label-{uuid4().hex}"
        folder.mkdir(exist_ok=False)
        path = folder / "label.jpg"
        try:
            report = self.camera.capture_labels(
                folder, target, cancel=self.cancel, progress=progress
            )
            from PIL import Image

            with Image.open(folder / report["frames"][0]["image"]) as image:
                width, height = image.size
            left, top, right, bottom = target_bounds(target)
            outline = [
                [width * x, height * y]
                for x, y in ((left, top), (right, top), (right, bottom), (left, bottom))
            ]
            candidates = {}
            primary = report["frames"][0]["image"]
            failures = []
            for index, frame in enumerate(report["frames"], 1):
                if self.cancel is not None and self.cancel.is_set():
                    raise CameraError("Label scan cancelled.")
                name = frame["image"]
                self._capture_record(folder / name, frame["metadata"], frame.get("captured_at"))
                if progress:
                    progress(f"Reading label picture {index}/{len(report['frames'])}…")
                decoded = self.decoder(folder / name, outline=outline, enhance=True)
                frame["barcode"] = decoded
                if decoded["status"] == "unavailable":
                    failures.append(decoded.get("reason"))
                if decoded["candidates"] and not candidates:
                    primary = name
                for candidate in decoded["candidates"]:
                    saved = candidates.setdefault(
                        candidate["value"],
                        {
                            **candidate,
                            "formats": [],
                            "positions": [],
                            "raw_bytes_base64": [],
                        },
                    )
                    for key in ("formats", "raw_bytes_base64"):
                        for value in candidate.get(key, []):
                            if value not in saved[key]:
                                saved[key].append(value)
                    for location in candidate.get("positions", []):
                        saved["positions"].append({**location, "image": name})
            barcode = barcode_result(
                list(candidates.values()), image_size=[width, height], region="label_target"
            )
            if failures and len(failures) == len(report["frames"]):
                barcode.update(status="unavailable", reason=failures[0])
            report["primary_image"] = primary
            shutil.copyfile(folder / primary, path)
            shutil.copyfile((folder / primary).with_suffix(".json"), path.with_suffix(".json"))
            result = {
                "scan_id": folder.name,
                "barcode": barcode,
                "label_capture": report,
                **initial_identity(barcode),
            }
            self._save_result(folder, result)
            self.pending_label = folder, result
            return result
        except Exception as exc:
            (folder / "error.txt").write_text(str(exc), encoding="utf-8")
            raise

    def set_label_id(self, scan_id, value, candidate_index=None):
        if self.pending_label is None or self.pending_label[1]["scan_id"] != scan_id:
            raise PackageIdError("This label is no longer current. Scan the label again.")
        folder, original = self.pending_label
        result = with_identity(original, value, candidate_index)
        self._save_result(folder, result)
        self.pending_label = folder, result
        return result

    def scan(self, *, dimensions_only_reason=None):
        self.last_scan = None
        if self.reference is None:
            raise MeasurementError("Capture the empty station first.")
        pending = self.pending_label
        if pending is not None and not pending[1].get("package_id"):
            raise PackageIdError("Choose or enter the label ID first, or clear the pending label.")
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
            if pending is None:
                barcode = self.decoder(path, outline=result.get("box_contour_pixels", []))
                identity = initial_identity(barcode)
                barcode_capture = {"mode": "measurement", "image": "box.jpg"}
            else:
                label_folder, label_result = pending
                barcode = label_result["barcode"]
                identity = {
                    key: label_result[key]
                    for key in ("package_id", "package_id_source", "package_id_history")
                }
                capture = label_result.get("label_capture", {})
                for frame in capture.get("frames", []):
                    name = frame["image"]
                    shutil.copyfile(label_folder / name, folder / name)
                    shutil.copyfile(
                        (label_folder / name).with_suffix(".json"),
                        (folder / name).with_suffix(".json"),
                    )
                source = capture.get("primary_image", "label.jpg")
                selected = barcode.get("selected_index")
                if selected is not None:
                    locations = barcode["candidates"][selected].get("positions", [])
                    if locations:
                        source = locations[0].get("image", source)
                shutil.copyfile(label_folder / source, folder / "label.jpg")
                shutil.copyfile((label_folder / source).with_suffix(".json"), folder / "label.json")
                barcode_capture = {
                    "mode": "separate_label",
                    "image": "label.jpg",
                    "label_id": label_result["scan_id"],
                    "source_frame": source,
                }
            result = {
                **result,
                "scan_id": uuid4().hex,
                "weight": weight,
                "scan_mode": "dimensions_and_weight" if ticket else "dimensions_only",
                "barcode": barcode,
                "barcode_capture": barcode_capture,
                **({"label_capture": capture} if pending is not None else {}),
                **identity,
            }
            self._save_result(folder, result)
            self.last_scan = folder, result
            self._update_review(folder, result)
            self.clear_label()
            return result, self.review_path
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
        if (
            candidate_index is not None
            and result.get("barcode_capture", {}).get("mode") == "separate_label"
        ):
            locations = result["barcode"]["candidates"][candidate_index].get("positions", [])
            if locations and locations[0].get("image"):
                result["barcode_capture"] = {
                    **result["barcode_capture"],
                    "source_frame": locations[0]["image"],
                }
        self._save_result(folder, result)
        self.last_scan = folder, result
        self._update_review(folder, result)
        return result

    def _update_review(self, folder, result):
        self.review_warning = ""
        try:
            capture = result.get("barcode_capture", {})
            source = capture.get("source_frame")
            if capture.get("mode") == "separate_label" and source and source != "label.jpg":
                shutil.copyfile(folder / source, folder / "label.jpg")
                shutil.copyfile((folder / source).with_suffix(".json"), folder / "label.json")
            self.review_path = self.reviewer(folder, result)
            with suppress(OSError):
                (folder / "review" / "overlay-error.txt").unlink(missing_ok=True)
        except Exception as exc:
            # The JSON is authoritative. A derived-image failure must not undo
            # the saved ID or leave an old ID displayed in the review image.
            self.review_path = folder / "review" / "measurement.jpg"
            self.review_warning = f"Marked barcode/ID overlay unavailable: {exc}"
            for name in ("scan.jpg", "scan.tmp.jpg"):
                with suppress(OSError):
                    (folder / "review" / name).unlink(missing_ok=True)
            with suppress(OSError):
                (folder / "review" / "overlay-error.txt").write_text(
                    self.review_warning, encoding="utf-8"
                )
