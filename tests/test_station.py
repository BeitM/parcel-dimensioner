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

    def capture_labels(self, directory, target, **kwargs):
        from PIL import Image

        frames = []
        for index in range(1, 4):
            path = directory / f"label-{index:02}.jpg"
            Image.new("RGB", (1920, 1080), (index * 40, index * 40, index * 40)).save(path)
            frames.append(
                {"image": path.name, "metadata": {"LensPosition": 4.0, "ScalerCrop": self.crop}}
            )
        return {"frames": frames, "target": list(target)}


def session(tmp_path, validator=lambda _: None, measure=None):
    def fake_measure(empty, box, output):
        assert empty.exists() and box.exists()
        output.mkdir()
        return {"dimensions_inches": {"length": 3, "width": 2, "height": 1}}

    return ScanSession(
        tmp_path,
        FakeCamera(),
        CameraSettings(),
        validator,
        measure or fake_measure,
        reviewer=lambda folder, result: folder / "review" / "measurement.jpg",
    )


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


def test_combined_result_saved_with_locked_weight(tmp_path):
    from parcel_dimensioner.scale import ScaleError

    class Scale:
        fail = False

        def wait_stable(self, cancel):
            if self.fail:
                raise ScaleError("USB disconnected")
            return object()

        def verify_capture(self, ticket, cancel):
            return {"status": "measured", "pounds": 5.01, "calibration_id": "test"}

    s = session(tmp_path)
    s.scale = Scale()
    s.capture_empty()
    result, _ = s.scan()
    assert result["weight"]["pounds"] == 5.01
    assert json.loads((s.directory / "scan-0001/result.json").read_text()) == result
    s.scale.fail = True
    with pytest.raises(ScaleError):
        s.scan()
    assert not (s.directory / "scan-0002/result.json").exists()
    assert (s.directory / "scan-0002/attempt.json").exists()
    result, _ = s.scan(dimensions_only_reason="USB disconnected; operator chose dimensions only")
    assert result["scan_mode"] == "dimensions_only"
    assert result["weight"]["pounds"] is None
    assert "operator chose" in result["weight"]["reason"]


def test_weight_change_after_photo_prevents_accepted_result(tmp_path):
    from parcel_dimensioner.scale import ScaleError

    class Scale:
        def wait_stable(self, cancel):
            return object()

        def verify_capture(self, ticket, cancel):
            raise ScaleError("Weight changed during capture")

    s = session(tmp_path)
    s.scale = Scale()
    s.capture_empty()
    with pytest.raises(ScaleError, match="changed during capture"):
        s.scan()
    assert (s.directory / "scan-0001/box.jpg").exists()
    assert not (s.directory / "scan-0001/result.json").exists()


def test_package_id_saved_on_exact_scan_and_never_reused(tmp_path, monkeypatch):
    from parcel_dimensioner.barcodes import PackageIdError

    barcode = {
        "status": "found",
        "selected_index": 0,
        "candidates": [{"value": "AUTO-123", "formats": ["Code 128"]}],
    }
    s = session(tmp_path)
    s.decoder = lambda *a, **kw: barcode
    s.capture_empty()
    result, _ = s.scan()
    scan_id = result["scan_id"]
    assert result["package_id"] == "AUTO-123"
    updated = s.set_package_id(scan_id, "MANUAL-42")
    record = s.directory / "scan-0001/result.json"
    assert json.loads(record.read_text())["package_id"] == "MANUAL-42"
    assert updated["dimensions_inches"] == result["dimensions_inches"]
    assert updated["weight"] == result["weight"]
    assert len(updated["package_id_history"]) == 2
    assert not record.with_suffix(".json.tmp").exists()

    def fail_write(*_):
        raise OSError("disk full")

    with monkeypatch.context() as patch:
        patch.setattr(s, "_save_result", fail_write)
        with pytest.raises(OSError, match="disk full"):
            s.set_package_id(scan_id, "NOT-SAVED")
    assert s.last_scan[1]["package_id"] == "MANUAL-42"
    assert json.loads(record.read_text())["package_id"] == "MANUAL-42"
    s.decoder = lambda *a, **kw: {"status": "not_found", "selected_index": None, "candidates": []}
    second, _ = s.scan()
    assert second["scan_id"] != scan_id
    assert second["package_id"] is None
    with pytest.raises(PackageIdError, match="no longer current"):
        s.set_package_id(scan_id, "STALE-ID")


def test_barcode_failure_keeps_dimensions_and_weight(tmp_path):
    s = session(tmp_path)
    s.decoder = lambda *a, **kw: {
        "status": "unavailable",
        "selected_index": None,
        "candidates": [],
        "reason": "Decoder missing",
    }
    s.capture_empty()
    result, _ = s.scan()
    assert result["dimensions_inches"]["length"] == 3
    assert result["barcode"]["status"] == "unavailable"
    assert result["package_id"] is None


def test_separate_label_used_once_and_retained_for_failed_measurement(tmp_path):
    s = session(tmp_path)
    calls = []

    def decode(path, **kw):
        calls.append(path.name)
        return {
            "status": "found",
            "selected_index": 0,
            "candidates": [{"value": "CLOSE-LABEL", "formats": ["Code 128"]}],
        }

    s.decoder = decode
    s.capture_empty()
    label = s.scan_label()
    assert label["package_id"] == "CLOSE-LABEL"
    original_measure = s.measure
    s.measure = lambda *args: (_ for _ in ()).throw(MeasurementError("retry"))
    with pytest.raises(MeasurementError, match="retry"):
        s.scan()
    assert s.pending_label[1]["scan_id"] == label["scan_id"]
    s.measure = original_measure
    result, _ = s.scan()
    assert result["package_id"] == "CLOSE-LABEL"
    assert calls == ["label-01.jpg", "label-02.jpg", "label-03.jpg"]
    assert s.pending_label is None
    assert result["barcode_capture"] == {
        "mode": "separate_label",
        "image": "label.jpg",
        "label_id": label["scan_id"],
        "source_frame": "label-01.jpg",
    }
    assert (s.directory / "scan-0002/label.jpg").exists()
    assert (
        json.loads((s.directory / "scan-0002/label.json").read_text())["metadata"]["LensPosition"]
        == 4
    )
    s.decoder = lambda *a, **kw: {"status": "not_found", "selected_index": None, "candidates": []}
    next_result, _ = s.scan()
    assert next_result["package_id"] is None
    assert next_result["barcode_capture"]["mode"] == "measurement"


def test_pending_multiple_labels_need_selection_and_stale_edit_rejected(tmp_path):
    from parcel_dimensioner.barcodes import PackageIdError

    s = session(tmp_path)
    s.decoder = lambda *a, **kw: {
        "status": "multiple",
        "selected_index": None,
        "candidates": [{"value": v, "formats": ["Code 128"]} for v in ("A", "B")],
    }
    s.capture_empty()
    label = s.scan_label()
    with pytest.raises(PackageIdError, match="Choose or enter"):
        s.scan()
    s.set_label_id(label["scan_id"], "B", 1)
    result, _ = s.scan()
    assert result["package_id"] == "B"
    assert result["barcode"]["selected_index"] == 1
    with pytest.raises(PackageIdError, match="no longer current"):
        s.set_label_id(label["scan_id"], "A", 0)
    s.scan_label()
    s.clear_label()
    assert s.pending_label is None
    s.scan_label()
    s.capture_empty()
    assert s.pending_label is None


def test_label_capture_refused_when_station_focus_cannot_be_restored(tmp_path, monkeypatch):
    from parcel_dimensioner import station
    from parcel_dimensioner.camera.contracts import CameraError
    from parcel_dimensioner.label_capture import LabelRestoreError

    def failed_restore(*args, **kwargs):
        raise LabelRestoreError("Station camera settings could not be restored.")

    monkeypatch.setattr(station, "capture_labels", failed_restore)
    c = StationCamera(CameraSettings(lens_position=1.0))
    with pytest.raises(CameraError, match="could not be restored"):
        c.capture_labels(tmp_path, (0.5, 0.5))
    with pytest.raises(CameraError, match="could not be restored"):
        c.capture(tmp_path / "box.jpg")


def test_labels_across_burst_require_choice_and_keep_corresponding_frame(tmp_path):
    s = session(tmp_path)
    s.capture_empty()

    def decode(path, **kwargs):
        value = "A" if path.name == "label-01.jpg" else "B"
        return {
            "status": "found",
            "selected_index": 0,
            "candidates": [
                {
                    "value": value,
                    "formats": ["Code 128"],
                    "positions": [{"points": [[0, 0], [10, 0], [10, 10], [0, 10]]}],
                }
            ],
        }

    s.decoder = decode
    label = s.scan_label((0.7, 0.6))
    assert label["barcode"]["status"] == "multiple"
    assert label["package_id"] is None
    assert len(label["label_capture"]["frames"]) == 3
    s.set_label_id(label["scan_id"], "B", 1)
    result, _ = s.scan()
    assert result["barcode_capture"]["source_frame"] == "label-02.jpg"
    folder, _ = s.last_scan
    assert (folder / "label.jpg").read_bytes() == (folder / "label-02.jpg").read_bytes()
    assert all((folder / f"label-{i:02}.jpg").exists() for i in range(1, 4))
    updated = s.set_package_id(result["scan_id"], "A", 0)
    assert updated["barcode_capture"]["source_frame"] == "label-01.jpg"
    assert (folder / "label.jpg").read_bytes() == (folder / "label-01.jpg").read_bytes()


def test_marked_result_refresh_failure_does_not_undo_saved_id(tmp_path):
    s = session(tmp_path)
    s.capture_empty()
    result, _ = s.scan()
    stale = s.directory / "scan-0001/review/scan.jpg"
    stale.write_bytes(b"stale ID overlay")

    def unavailable_review(*_):
        raise OSError("Cannot write overlay")

    s.reviewer = unavailable_review
    updated = s.set_package_id(result["scan_id"], "SAVED-42")
    assert updated["package_id"] == "SAVED-42"
    assert s.last_scan[1]["package_id"] == "SAVED-42"
    stored = json.loads((s.directory / "scan-0001/result.json").read_text())
    assert stored["package_id"] == "SAVED-42"
    assert "Cannot write overlay" in s.review_warning
    assert s.review_path.name == "measurement.jpg"
    assert not stale.exists()


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


def test_camera_full_resolution_configuration_uses_small_preview_stream(monkeypatch):
    from types import SimpleNamespace

    import numpy as np

    from parcel_dimensioner import station

    configured = []
    streams = []
    device = SimpleNamespace(
        options={},
        create_still_configuration=lambda **kwargs: kwargs,
        configure=lambda config: configured.append(config),
        start=lambda: None,
        capture_array=lambda stream: streams.append(stream) or np.full((675, 800), 128, np.uint8),
        camera_configuration=lambda: {"lores": {"size": (800, 450)}},
    )
    monkeypatch.setattr(
        station, "_load_picamera2", lambda: SimpleNamespace(Picamera2=lambda _: device)
    )
    monkeypatch.setattr(station, "_set_manual_focus", lambda *args: None)
    monkeypatch.setattr(station.time, "sleep", lambda _: None)
    camera = StationCamera(CameraSettings(lens_position=1.0))
    camera.start()
    assert configured[0]["main"]["size"] == (4608, 2592)
    assert configured[0]["lores"] == {"size": (800, 450), "format": "YUV420"}
    assert configured[0]["raw"] is None and configured[0]["buffer_count"] == 2
    assert device.options["quality"] == 95
    assert camera.preview().size == (800, 450)
    assert streams == ["lores"]


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
    assert text.startswith("[Desktop Entry]\n")


def test_desktop_installer_repairs_old_header_and_can_reinstall(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from parcel_dimensioner import desktop

    monkeypatch.chdir(tmp_path)
    (tmp_path / "pyproject.toml").touch()
    monkeypatch.setattr(desktop.sys, "platform", "linux")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))
    monkeypatch.setattr(
        desktop.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(stdout=str(tmp_path / "Desktop") + "\n"),
    )
    old = tmp_path / "Desktop/parcel-dimensioner.desktop"
    old.parent.mkdir()
    old.write_text(desktop.MARKER + "\n[Desktop Entry]\nName=Box Scanner\n")
    targets = desktop.install_desktop(lens_position=1.018378)
    desktop.install_desktop(lens_position=1.018378)
    assert all(p.read_text().startswith("[Desktop Entry]\n") for p in targets)
