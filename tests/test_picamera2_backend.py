from types import SimpleNamespace

from parcel_dimensioner.camera.picamera2_backend import (
    _camera_info,
    _plain_metadata,
    _preview_type,
)


def test_camera_info_uses_position_when_picamera_omits_number() -> None:
    info = _camera_info({"Model": "imx708", "Id": "csi0"}, fallback_index=1)

    assert info.index == 1
    assert info.model == "imx708"
    assert info.identifier == "csi0"


def test_plain_metadata_recursively_normalizes_values() -> None:
    metadata = {"Crop": (1, 2, 3, 4), "Nested": {"custom": object()}}

    result = _plain_metadata(metadata)

    assert result["Crop"] == [1, 2, 3, 4]
    assert isinstance(result["Nested"]["custom"], str)


def test_preview_backend_can_be_selected_explicitly() -> None:
    module = SimpleNamespace(Preview=SimpleNamespace(QTGL="qt", DRM="drm", NULL="null"))

    assert _preview_type(module, "drm") == "drm"
