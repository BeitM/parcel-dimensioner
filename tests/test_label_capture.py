import json
import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar

import pytest

from parcel_dimensioner.camera.contracts import CameraError
from parcel_dimensioner.label_capture import LabelRestoreError, capture_labels


@pytest.fixture
def camera(monkeypatch):
    class Rectangle:
        """Strict non-sequence object, as supplied by libcamera's Python binding."""

        def __init__(self, x, y, width, height):
            assert all(type(v) is int for v in (x, y, width, height))
            self.values = (x, y, width, height)

    controls = SimpleNamespace(
        AfModeEnum=SimpleNamespace(Manual=0), AfMeteringEnum=SimpleNamespace(Auto=0, Windows=1)
    )
    monkeypatch.setitem(
        sys.modules, "libcamera", SimpleNamespace(controls=controls, Rectangle=Rectangle)
    )

    class Camera:
        camera_controls: ClassVar = {
            "AfMode": (0, 2, 0),
            "LensPosition": (0, 32, 1),
            "AfMetering": (0, 1, 0),
            "AfWindows": None,
            "AeEnable": (False, True, True),
            "ExposureTime": (50, 200000, None),
            "AnalogueGain": (1, 16, None),
        }
        camera_properties: ClassVar = {"ScalerCropMaximum": [0, 0, 4608, 2592]}

        def __init__(self):
            self.metadata = {
                "LensPosition": 1.0,
                "ExposureTime": 50000,
                "AnalogueGain": 3.0,
                "ScalerCrop": (140, 250, 4000, 2250),
            }
            self.requests, self.captures = [], []
            self.focus_ok = True
            self.fail_photo = False
            self.fail_restore = False
            self.flushed = False

        def set_controls(self, values):
            if "AfWindows" in values:
                # Picamera2 0.3.38 Controls.get_libcamera_controls() distinguishes
                # rectangle arrays by nested list/tuple, not Rectangle objects.
                windows = values["AfWindows"]
                if type(windows) in (list, tuple) and windows and type(windows[0]) in (list, tuple):
                    self.windows = [Rectangle(*window) for window in windows]
                else:
                    self.windows = Rectangle(*windows)
            self.requests.append(values)
            if self.fail_restore and values.get("AeEnable") is True:
                raise CameraError("restore failed")
            for key in ("LensPosition", "ExposureTime", "AnalogueGain"):
                if key in values:
                    self.metadata[key] = values[key]

        def capture_metadata(self, *, wait):
            assert wait is False
            return self.metadata.copy()

        def autofocus_cycle(self, *, wait):
            assert wait is False
            self.metadata["LensPosition"] = 2.2
            return self.focus_ok

        def capture_file(self, path, *, wait):
            assert wait is False
            if self.fail_photo:
                raise CameraError("photo failed")
            Path(path).write_bytes(b"fake jpeg")
            self.captures.append(self.metadata.copy())
            return self.metadata.copy()

        def wait(self, job, timeout):
            assert timeout <= 0.1
            return job

        def cancel_all_and_flush(self):
            self.flushed = True

    return Camera()


def test_targeted_focus_short_bracket_and_restore(camera, tmp_path):
    report = capture_labels(camera, 1.0, tmp_path, (0.8, 0.7))
    assert report["focus_window"] == [3100, 1645, 480, 360]
    assert camera.windows[0].values == (3100, 1645, 480, 360)
    assert isinstance(camera.metadata["ScalerCrop"], tuple)
    assert report["frames"][0]["metadata"]["ScalerCrop"] == [140, 250, 4000, 2250]
    assert [frame["ExposureTime"] for frame in camera.captures] == [10000, 6000, 16000]
    assert all(1 <= frame["AnalogueGain"] <= 16 for frame in camera.captures)
    assert all(frame["LensPosition"] == 2.2 for frame in camera.captures)
    assert camera.metadata["LensPosition"] == 1.0
    assert any(r.get("AeEnable") is True for r in camera.requests)
    assert camera.requests[-2]["AfMetering"] == 0
    for frame in report["frames"]:
        record = json.loads((tmp_path / frame["image"]).with_suffix(".json").read_text())
        assert record["metadata"]["LensPosition"] == 2.2


@pytest.mark.parametrize("failure", ["focus", "photo"])
def test_failed_capture_still_restores_focus_and_auto_exposure(camera, tmp_path, failure):
    camera.focus_ok = failure != "focus"
    camera.fail_photo = failure == "photo"
    with pytest.raises(CameraError):
        capture_labels(camera, 1.0, tmp_path)
    assert camera.metadata["LensPosition"] == 1.0
    assert any(r.get("AeEnable") is True for r in camera.requests)


def test_restore_failure_is_distinct_and_blocks_further_use(camera, tmp_path):
    camera.fail_restore = True
    with pytest.raises(LabelRestoreError, match="could not be restored"):
        capture_labels(camera, 1.0, tmp_path)


def test_cancelling_autofocus_flushes_job_then_restores(camera, tmp_path):
    cancel = threading.Event()
    original = camera.wait

    def wait(job, timeout):
        if job is True:
            cancel.set()
            raise TimeoutError
        return original(job, timeout)

    camera.wait = wait
    with pytest.raises(CameraError, match="cancelled"):
        capture_labels(camera, 1.0, tmp_path, cancel=cancel)
    assert camera.flushed
    assert camera.metadata["LensPosition"] == 1.0


def test_wrong_captured_exposure_is_rejected_and_metadata_retained(camera, tmp_path):
    original = camera.capture_file

    def capture_file(*args, **kwargs):
        return {**original(*args, **kwargs), "ExposureTime": 50000}

    camera.capture_file = capture_file
    with pytest.raises(CameraError, match="exposure changed"):
        capture_labels(camera, 1.0, tmp_path)
    assert camera.metadata["LensPosition"] == 1.0
    record = json.loads((tmp_path / "label-01.json").read_text())
    assert record["metadata"]["ExposureTime"] == 50000


def test_camera_contract_reproduces_prebuilt_rectangle_failure(camera):
    rectangle = sys.modules["libcamera"].Rectangle(2028, 1477, 553, 415)
    with pytest.raises(TypeError):
        camera.set_controls({"AfWindows": [rectangle]})
