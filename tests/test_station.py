import json
from pathlib import Path

import pytest

from parcel_dimensioner.measurement import MeasurementError
from parcel_dimensioner.settings import CameraSettings
from parcel_dimensioner.station import ScanSession, StationCamera


class FakeCamera:
    def __init__(self):
        self.crop = [0, 0, 4608, 2592]

    def capture(self, path):
        path.write_bytes(b"fake")
        return {"LensPosition": 1.0, "ScalerCrop": self.crop}


def session(tmp_path, validator=lambda _: None, measure=None):
    def fake_measure(empty, box, output):
        assert empty.exists() and box.exists()
        output.mkdir()
        return {"dimensions_inches": {"length": 3, "width": 2, "height": 1}}

    return ScanSession(tmp_path, FakeCamera(), CameraSettings(), validator, measure or fake_measure)


def test_reference_reused_and_scans_saved_separately(tmp_path):
    s = session(tmp_path)
    with pytest.raises(MeasurementError, match="empty station first"):
        s.scan()
    empty = s.capture_empty()
    s.scan()
    s.scan()
    assert s.reference == empty
    assert (s.directory / "scan-0001/box.jpg").exists()
    assert (s.directory / "scan-0002/box.jpg").exists()
    metadata = json.loads((s.directory / "scan-0002/box.json").read_text())
    assert metadata["metadata"]["LensPosition"] == 1.0
    assert session(tmp_path).reference is None


def test_failed_reference_replacement_invalidates_old_reference(tmp_path):
    s = session(tmp_path)
    old = s.capture_empty()

    def fail(_):
        raise MeasurementError("hidden wall")

    s.validator = fail
    with pytest.raises(MeasurementError):
        s.capture_empty()
    assert s.reference is None
    assert old.exists()
    with pytest.raises(MeasurementError, match="empty station first"):
        s.scan()


def test_failed_scan_is_retained_and_next_scan_can_retry(tmp_path):
    def fail(*_):
        raise MeasurementError("no box")

    s = session(tmp_path, measure=fail)
    reference = s.capture_empty()
    with pytest.raises(MeasurementError):
        s.scan()
    assert s.reference == reference
    assert (s.directory / "scan-0001/error.txt").read_text() == "no box"


def test_crop_change_requires_new_reference(tmp_path):
    s = session(tmp_path)
    s.capture_empty()
    s.camera.crop = [10, 10, 2000, 1000]
    with pytest.raises(MeasurementError, match="crop changed"):
        s.scan()
    assert s.reference is None


def test_camera_focus_mismatch_rejected_and_close_releases_owner(tmp_path):
    from parcel_dimensioner.camera.contracts import CameraError

    class Device:
        closed = False

        def capture_file(self, _):
            return {"LensPosition": 2.0}

        def close(self):
            self.closed = True

    device = Device()
    c = StationCamera(CameraSettings(lens_position=1.0))
    c.camera = device
    with pytest.raises(CameraError, match="Focus changed"):
        c.capture(tmp_path / "test.jpg")
    c.close()
    assert device.closed and c.camera is None


def test_desktop_entry_keeps_venv_path_and_focus():
    from parcel_dimensioner.desktop import desktop_entry

    text = desktop_entry(
        Path("/home/pi/my scanner"),
        "/home/pi/my scanner/.venv/bin/python",
        Path("/home/pi/my scanner/camera.toml"),
        1.018378,
    )
    assert '"/home/pi/my scanner/.venv/bin/python"' in text
    assert '"--config"' in text and '"--lens-position" "1.018378"' in text
    assert "Terminal=false" in text
