import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from parcel_dimensioner.camera import picamera2_backend as adapter
from parcel_dimensioner.camera.contracts import CameraError
from parcel_dimensioner.cli import build_parser
from parcel_dimensioner.settings import CameraSettings, SettingsError, load_settings


class FakeCamera:
    def __init__(self):
        self.camera_controls = {"AfMode": (0, 2, 0), "LensPosition": (0.0, 10.0, 1.0)}
        self.controls = []
        self.closed = False
        self.af_calls = 0
        self.position = 1.25
        self.af_success = True

    def create_still_configuration(self, **kwargs):
        return kwargs

    def configure(self, configuration):
        self.configuration = configuration

    def camera_configuration(self):
        return self.configuration

    def set_controls(self, controls):
        self.controls.append(controls)
        self.position = controls["LensPosition"]

    def start_preview(self, backend):
        self.preview_backend = backend

    def start(self):
        pass

    def autofocus_cycle(self):
        self.af_calls += 1
        return self.af_success

    def capture_metadata(self):
        return {"LensPosition": self.position}

    def capture_file(self, filename):
        Path(filename).write_bytes(b"fake jpeg")
        return self.capture_metadata()

    def close(self):
        self.closed = True


@pytest.fixture
def fake_camera(monkeypatch):
    camera = FakeCamera()
    factory = SimpleNamespace(
        global_camera_info=lambda: [{"Num": 0, "Model": "imx708", "Id": "fake"}]
    )

    class Factory:
        global_camera_info = factory.global_camera_info

        def __new__(cls, index):
            return camera

    module = SimpleNamespace(Picamera2=Factory, Preview=SimpleNamespace(NULL="null"))
    monkeypatch.setattr(adapter, "_load_picamera2", lambda: module)
    monkeypatch.setattr(adapter.time, "sleep", lambda _: None)
    monkeypatch.setitem(
        sys.modules,
        "libcamera",
        SimpleNamespace(controls=SimpleNamespace(AfModeEnum=SimpleNamespace(Manual="manual"))),
    )
    return camera


def collect(path, position=None):
    return adapter.Picamera2Backend(lens_position=position).capture_calibration(
        path,
        width=640,
        height=480,
        warmup_seconds=0,
        preview_backend="null",
        count=3,
    )


def test_autofocus_once_then_manual_across_session(tmp_path, monkeypatch, fake_camera):
    answers = iter(["", "", "", "q"])
    monkeypatch.setattr("builtins.input", lambda _: next(answers))
    folder = tmp_path / "session"
    assert collect(folder) == 2
    manifest = json.loads((folder / "session.json").read_text())
    assert fake_camera.af_calls == 1
    assert fake_camera.controls == [{"AfMode": "manual", "LensPosition": 1.25}]
    assert manifest["image_size"] == [640, 480]
    assert manifest["lens_position"] == 1.25
    assert load_settings(folder / "camera.toml").camera == CameraSettings(
        width=640, height=480, warmup_seconds=0, preview_backend="null", lens_position=1.25
    )
    assert [i["file"] for i in manifest["images"]] == ["calibration-001.jpg", "calibration-002.jpg"]
    assert fake_camera.closed


def test_explicit_focus_skips_autofocus(tmp_path, monkeypatch, fake_camera):
    monkeypatch.setattr("builtins.input", lambda _: "q")
    assert collect(tmp_path / "session", 2.0) == 0
    assert fake_camera.af_calls == 0
    assert fake_camera.controls[0]["LensPosition"] == 2.0
    assert fake_camera.closed


def test_autofocus_failure_closes_camera(tmp_path, monkeypatch, fake_camera):
    fake_camera.af_success = False
    monkeypatch.setattr("builtins.input", lambda _: "")
    with pytest.raises(CameraError, match="Autofocus failed"):
        collect(tmp_path / "session")
    assert fake_camera.closed
    assert not (tmp_path / "session" / "calibration-001.jpg").exists()


def test_existing_dataset_is_not_overwritten(tmp_path, fake_camera):
    sentinel = tmp_path / "calibration-001.jpg"
    sentinel.write_bytes(b"original")
    with pytest.raises(CameraError, match="calibration capture failed"):
        collect(tmp_path, 1.0)
    assert sentinel.read_bytes() == b"original"


def test_focus_applies_to_regular_snapshot(tmp_path, fake_camera):
    result = adapter.Picamera2Backend(lens_position=2.0).capture_still(
        tmp_path / "box.jpg",
        width=640,
        height=480,
        warmup_seconds=0,
    )
    assert result.metadata["LensPosition"] == 2.0
    assert fake_camera.controls[0]["AfMode"] == "manual"
    assert fake_camera.closed


@pytest.mark.parametrize("value", [-1.0, float("nan"), float("inf")])
def test_invalid_focus_rejected(value):
    with pytest.raises(SettingsError):
        CameraSettings(lens_position=value)
    with pytest.raises(SystemExit):
        build_parser().parse_args(["camera", "snapshot", f"--lens-position={value}"])


def test_unsupported_focus_is_actionable(fake_camera):
    fake_camera.camera_controls = {}
    with pytest.raises(CameraError, match="requires a camera"):
        adapter._set_manual_focus(fake_camera, 1.0)


def test_interrupt_retains_completed_images(tmp_path, monkeypatch, fake_camera):
    responses = iter(["", KeyboardInterrupt()])

    def respond(_):
        answer = next(responses)
        if isinstance(answer, BaseException):
            raise answer
        return answer

    monkeypatch.setattr("builtins.input", respond)
    folder = tmp_path / "session"
    assert collect(folder, 1.25) == 1
    assert len(json.loads((folder / "session.json").read_text())["images"]) == 1
    assert (folder / "calibration-001.jpg").exists()
    assert fake_camera.closed


def test_unverified_focus_discards_frame(tmp_path, monkeypatch, fake_camera):
    monkeypatch.setattr("builtins.input", lambda _: "")
    monkeypatch.setattr(fake_camera, "capture_metadata", lambda: {})
    folder = tmp_path / "session"
    with pytest.raises(CameraError, match="frame discarded"):
        collect(folder, 1.25)
    assert not (folder / "calibration-001.jpg").exists()
    assert json.loads((folder / "session.json").read_text())["images"] == []
    assert fake_camera.closed
