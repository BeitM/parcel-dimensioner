"""Targeted, exposure-bracketed label capture with station-state restoration."""

from __future__ import annotations

import importlib
import json
import math
import time
from datetime import UTC, datetime

from parcel_dimensioner.camera.contracts import CameraError
from parcel_dimensioner.camera.picamera2_backend import _plain_metadata, _set_manual_focus
from parcel_dimensioner.label_reader import target_bounds


class LabelRestoreError(CameraError):
    pass


def _wait(camera, job, cancel=None, timeout=8, *, timeout_message=None):
    deadline = time.monotonic() + timeout
    while True:
        if (cancel is not None and cancel.is_set()) or time.monotonic() >= deadline:
            camera.cancel_all_and_flush()
            raise CameraError(
                timeout_message
                or "Label capture cancelled or timed out. Try again with the box still."
            )
        try:
            return camera.wait(job, timeout=0.1)
        except TimeoutError:
            pass


def _metadata(camera, cancel=None):
    return _wait(camera, camera.capture_metadata(wait=False), cancel)


def capture_labels(camera, position, directory, target=(0.5, 0.5), *, cancel=None, progress=None):
    """Three real full-resolution photos, without changing crop or station focus."""
    controls = importlib.import_module("libcamera").controls
    required = {
        "AfMode",
        "LensPosition",
        "AfMetering",
        "AfWindows",
        "AeEnable",
        "ExposureTime",
        "AnalogueGain",
    }
    if not required.issubset(camera.camera_controls):
        raise CameraError("This camera does not expose the label focus/exposure controls.")
    bounds = target_bounds(target, width=0.12, height=0.16)
    # Normalize tuple/list metadata consistently with the saved capture below.
    before = _plain_metadata(_metadata(camera, cancel))
    crop = before.get("ScalerCrop") or camera.camera_properties["ScalerCropMaximum"]
    x, y, w, h = crop
    window = (
        round(x + w * bounds[0]),
        round(y + h * bounds[1]),
        max(1, round(w * (bounds[2] - bounds[0]))),
        max(1, round(h * (bounds[3] - bounds[1]))),
    )
    frames = []
    try:
        if progress:
            progress("Focusing on the selected label area… Keep the box still.")
        camera.set_controls(
            {
                "AfMetering": controls.AfMeteringEnum.Windows,
                # Picamera2 constructs the libcamera Rectangles itself. Passing
                # prebuilt objects breaks its rectangle-array conversion.
                "AfWindows": [window],
            }
        )
        if not _wait(camera, camera.autofocus_cycle(wait=False), cancel):
            raise CameraError("Label focus failed. Tap the barcode in live view and try again.")
        focused = _metadata(camera, cancel)
        lens = focused.get("LensPosition")
        if not isinstance(lens, (float, int)) or not math.isfinite(lens):
            raise CameraError("Camera did not report label focus. Try again.")
        _set_manual_focus(camera, lens)
        exposure = focused.get("ExposureTime", before.get("ExposureTime"))
        gain = focused.get("AnalogueGain", before.get("AnalogueGain"))
        if not all(
            isinstance(v, (float, int)) and math.isfinite(v) and v > 0 for v in (exposure, gain)
        ):
            raise CameraError("Camera did not report usable exposure/gain for the label.")
        minimum, maximum, _ = camera.camera_controls["AnalogueGain"]
        min_time, max_time, _ = camera.camera_controls["ExposureTime"]
        # Keep shutters short and bracket brightness to preserve bars in bright labels.
        for index, (requested, brightness) in enumerate(
            ((10000, 0.7), (6000, 0.4), (16000, 1.0)), 1
        ):
            shutter = round(max(min_time, min(requested, max_time if max_time > 0 else requested)))
            requested_gain = max(minimum, min(maximum, exposure * gain * brightness / shutter))
            camera.set_controls(
                {"AeEnable": False, "ExposureTime": shutter, "AnalogueGain": requested_gain}
            )
            for _ in range(24):
                current = _metadata(camera, cancel)
                if (
                    abs(current.get("ExposureTime", -1000000) - shutter) <= max(100, shutter * 0.1)
                    and abs(current.get("AnalogueGain", -1000) - requested_gain)
                    <= max(0.1, requested_gain * 0.15)
                    and abs(current.get("LensPosition", -1000) - lens) <= 0.05
                ):
                    break
            else:
                raise CameraError("Label exposure did not settle. Try again.")
            if progress:
                progress(f"Taking label picture {index}/3… Keep the box still.")
            path = directory / f"label-{index:02}.jpg"
            metadata = _plain_metadata(
                _wait(camera, camera.capture_file(str(path), wait=False), cancel)
            )
            captured_at = datetime.now(UTC).isoformat()
            path.with_suffix(".json").write_text(
                json.dumps({"captured_at": captured_at, "metadata": metadata}, indent=2),
                encoding="utf-8",
            )
            if metadata.get("ScalerCrop") != before.get("ScalerCrop"):
                raise CameraError("Camera crop changed during label capture. Reopen the app.")
            if abs(metadata.get("LensPosition", -1000) - lens) > 0.05:
                raise CameraError("Label focus changed during capture. Retry with the box still.")
            if abs(metadata.get("ExposureTime", -1000000) - shutter) > max(
                100, shutter * 0.1
            ) or abs(metadata.get("AnalogueGain", -1000) - requested_gain) > max(
                0.1, requested_gain * 0.15
            ):
                raise CameraError("Label exposure changed during capture. Retry the label scan.")
            frames.append({"image": path.name, "metadata": metadata, "captured_at": captured_at})
        return {
            "frames": frames,
            "target": list(target),
            "focus_window": list(window),
            "focus_position": lens,
        }
    finally:
        try:
            camera.set_controls({"AeEnable": True, "AfMetering": controls.AfMeteringEnum.Auto})
            _set_manual_focus(camera, position)
            settled = 0
            for _ in range(30):
                metadata = _metadata(camera)
                reported = metadata.get("LensPosition")
                matches = isinstance(reported, (int, float)) and math.isclose(
                    reported, position, abs_tol=0.05
                )
                settled = settled + 1 if matches else 0
                if settled == 3:
                    break
            else:
                raise CameraError("Station focus did not return to its locked position.")
        except Exception as exc:
            raise LabelRestoreError(
                "Station camera settings could not be restored. Reopen the app."
            ) from exc
