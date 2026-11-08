from pathlib import Path

import pytest

from parcel_dimensioner.settings import AppSettings, SettingsError, load_settings


def test_touchscreen_cli_can_override_saved_capture_resolution():
    from parcel_dimensioner.cli import _camera_settings_with_overrides, build_parser
    from parcel_dimensioner.settings import CameraSettings

    args = build_parser().parse_args(["touchscreen", "--width", "4608", "--height", "2592"])
    settings = _camera_settings_with_overrides(CameraSettings(width=1920, height=1080), args)
    assert (settings.width, settings.height) == (4608, 2592)


def test_missing_implicit_config_uses_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    assert load_settings() == AppSettings()


def test_loads_camera_settings(tmp_path: Path) -> None:
    config = tmp_path / "test.toml"
    config.write_text(
        """
captures_dir = "captures"
[camera]
index = 1
width = 1280
height = 720
warmup_seconds = 1.5
preview_backend = "drm"
""".strip(),
        encoding="utf-8",
    )

    settings = load_settings(config)

    assert settings.captures_dir == Path("captures")
    assert settings.camera.index == 1
    assert settings.camera.width == 1280
    assert settings.camera.height == 720
    assert settings.camera.warmup_seconds == 1.5
    assert settings.camera.preview_backend == "drm"


def test_rejects_unknown_settings(tmp_path: Path) -> None:
    config = tmp_path / "test.toml"
    config.write_text("surprise = true", encoding="utf-8")

    with pytest.raises(SettingsError, match="unknown root setting"):
        load_settings(config)


def test_loads_persistent_scale_calibration(tmp_path):
    config = tmp_path / "scale.toml"
    config.write_text('[scale]\ncounts_per_pound = -10640.0\nport = "/dev/ttyACM0"\n')
    settings = load_settings(config)
    assert settings.scale.counts_per_pound == -10640
    assert settings.scale.port == "/dev/ttyACM0"
