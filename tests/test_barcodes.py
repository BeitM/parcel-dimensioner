import json
import sys

import pytest

from parcel_dimensioner.barcodes import (
    PackageIdError,
    decode_image,
    initial_identity,
    validate_package_id,
    with_identity,
)


def label(text, format_name="Code128"):
    zxing = pytest.importorskip("zxingcpp")
    image = pytest.importorskip("PIL.Image")
    kind = getattr(zxing.BarcodeFormat, format_name)
    if hasattr(zxing, "create_barcode"):
        return image.fromarray(zxing.create_barcode(text, kind).to_image(scale=3)).convert("RGB")
    return image.fromarray(zxing.write_barcode(kind, text, width=450, height=140)).convert("RGB")


def test_real_code128_from_original_1080p_image(tmp_path):
    image = pytest.importorskip("PIL.Image")
    canvas = image.new("RGB", (1920, 1080), "white")
    canvas.paste(label("PKG-123456"), (650, 450))
    path = tmp_path / "box.jpg"
    canvas.save(path, quality=95)
    result = decode_image(path, outline=[[500, 300], [1500, 300], [1500, 950], [500, 950]])
    assert result["status"] == "found"
    assert result["image_size"] == [1920, 1080]
    assert result["region"] == "detected_box"
    assert result["candidates"][0]["value"] == "PKG-123456"
    assert result["candidates"][0]["raw_bytes_base64"]
    assert initial_identity(result)["package_id"] == "PKG-123456"
    json.dumps(result)


def test_rotated_and_inverted_code(tmp_path):
    imageops = pytest.importorskip("PIL.ImageOps")
    path = tmp_path / "rotated.png"
    imageops.invert(label("ROTATED-42")).rotate(90, expand=True).save(path)
    result = decode_image(path)
    assert initial_identity(result)["package_id"] == "ROTATED-42"


def test_live_pass_keeps_multiple_codes_and_stops_recovery_after_success(monkeypatch):
    from parcel_dimensioner import label_reader
    from parcel_dimensioner.barcodes import decode_frame

    image = pytest.importorskip("PIL.Image")
    canvas = image.new("RGB", (1600, 900), "white")
    canvas.paste(label("LIVE-ONE"), (500, 250))
    canvas.paste(label("LIVE-TWO"), (500, 550))

    def views(*args, **kwargs):
        yield canvas, ((1, 0, 0), (0, 1, 0), (0, 0, 1))
        raise AssertionError("Should not process further views after finding these codes")

    monkeypatch.setattr(label_reader, "live_views", views)
    result = decode_frame(canvas, quick=True)
    assert result["status"] == "multiple"
    assert {c["value"] for c in result["candidates"]} == {"LIVE-ONE", "LIVE-TWO"}


def test_tiny_background_code_does_not_stop_live_recovery(monkeypatch):
    from parcel_dimensioner import label_reader
    from parcel_dimensioner.barcodes import decode_frame

    image = pytest.importorskip("PIL.Image")
    small = image.new("RGB", (1600, 900), "white")
    small.paste(label("SMALL", "QRCode"), (700, 300))
    presented = image.new("RGB", (1600, 900), "white")
    presented.paste(label("PRESENTED"), (500, 350))

    def views(*args, **kwargs):
        for current in (small, presented):
            yield current, ((1, 0, 0), (0, 1, 0), (0, 0, 1))

    monkeypatch.setattr(label_reader, "live_views", views)
    result = decode_frame(small, quick=True)
    assert [c["value"] for c in result["candidates"]] == ["PRESENTED"]


def test_barcode_decoder_keeps_full_resolution_image_and_native_positions(tmp_path):
    image = pytest.importorskip("PIL.Image")
    canvas = image.new("RGB", (4608, 2592), "white")
    canvas.paste(label("FULL-RES-42"), (3000, 1800))
    path = tmp_path / "box.jpg"
    canvas.save(path, quality=95)
    result = decode_image(path, outline=[[2800, 1600], [4200, 1600], [4200, 2400], [2800, 2400]])
    assert result["image_size"] == [4608, 2592]
    assert initial_identity(result)["package_id"] == "FULL-RES-42"
    points = result["candidates"][0]["positions"][0]["points"]
    assert min(p[0] for p in points) > 2800


def test_multiple_codes_deduplicate_values_and_exclude_background(tmp_path):
    image = pytest.importorskip("PIL.Image")
    canvas = image.new("RGB", (1920, 1080), "white")
    canvas.paste(label("PKG-ONE"), (180, 180))
    canvas.paste(label("PKG-ONE", "QRCode"), (700, 200))
    canvas.paste(label("PKG-TWO", "QRCode"), (180, 550))
    canvas.paste(label("BACKGROUND", "QRCode"), (1600, 800))
    path = tmp_path / "multiple.png"
    canvas.save(path)
    result = decode_image(path, outline=[[100, 100], [1200, 100], [1200, 850], [100, 850]])
    assert result["status"] == "multiple"
    assert result["selected_index"] is None
    found = {c["value"]: c for c in result["candidates"]}
    assert set(found) == {"PKG-ONE", "PKG-TWO"}
    assert len(found["PKG-ONE"]["formats"]) == 2
    assert initial_identity(result)["package_id"] is None


def test_missing_unreadable_and_unavailable_decoder(tmp_path, monkeypatch):
    image = pytest.importorskip("PIL.Image")
    pytest.importorskip("zxingcpp")
    path = tmp_path / "blank.png"
    image.new("RGB", (300, 200), "white").save(path)
    assert decode_image(path)["status"] == "not_found"
    assert decode_image(tmp_path / "missing.jpg")["status"] == "unavailable"
    assert decode_image(path, outline=[])["status"] == "unavailable"
    monkeypatch.setitem(sys.modules, "zxingcpp", None)
    result = decode_image(path)
    assert result["status"] == "unavailable"
    assert "Install" in result["reason"]


def test_manual_id_edits_preserve_candidates_and_add_history():
    barcode = {
        "status": "multiple",
        "selected_index": None,
        "candidates": [{"value": "A"}, {"value": "B"}],
    }
    original = {"scan_id": "scan-1", "barcode": barcode, **initial_identity(barcode)}
    selected = with_identity(original, "B", 1)
    assert selected["package_id_source"] == "selected_barcode"
    manual = with_identity(selected, " MANUAL-42 ")
    assert manual["package_id"] == "MANUAL-42"
    assert manual["package_id_source"] == "manual"
    assert manual["barcode"]["selected_index"] is None
    assert len(manual["package_id_history"]) == 2
    assert original["package_id"] is None
    assert len(original["package_id_history"]) == 0
    assert with_identity(manual, "")["package_id"] is None
    with pytest.raises(PackageIdError, match="do not match"):
        with_identity(original, "WRONG", 0)
    with pytest.raises(PackageIdError, match="no longer available"):
        with_identity(original, "B", 99)


@pytest.mark.parametrize("value", [None, 123, "A\nB", "A\x1dB", "X" * 257])
def test_invalid_package_id(value):
    with pytest.raises(PackageIdError):
        validate_package_id(value)


def test_decoded_control_characters_require_manual_id(tmp_path, monkeypatch):
    from types import SimpleNamespace

    image = pytest.importorskip("PIL.Image")
    zxing = pytest.importorskip("zxingcpp")
    point = SimpleNamespace(x=10, y=10)
    position = SimpleNamespace(
        top_left=point, top_right=point, bottom_left=point, bottom_right=point
    )
    code = SimpleNamespace(
        valid=True, text="A\nB", bytes=b"A\nB", position=position, format="QR Code"
    )
    monkeypatch.setattr(zxing, "read_barcodes", lambda *a, **kw: [code])
    path = tmp_path / "placeholder.png"
    image.new("RGB", (100, 100), "white").save(path)
    result = decode_image(path)
    assert result["status"] == "needs_review"
    assert result["candidates"][0]["value"] == "A\nB"
    assert initial_identity(result)["package_id"] is None


def test_cli_decodes_saved_image_without_camera(tmp_path, capsys):
    from parcel_dimensioner.cli import main

    path = tmp_path / "code.png"
    label("OFFLINE-42", "QRCode").save(path)
    assert main(["barcode-read", "--image", str(path)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["package_id"] == "OFFLINE-42"
    assert result["barcode"]["region"] == "full_image"


def test_cli_no_match_and_unavailable_have_distinct_status(tmp_path, capsys):
    from parcel_dimensioner.cli import main

    image = pytest.importorskip("PIL.Image")
    pytest.importorskip("zxingcpp")
    path = tmp_path / "blank.png"
    image.new("RGB", (300, 200), "white").save(path)
    assert main(["barcode-read", "--image", str(path)]) == 1
    assert json.loads(capsys.readouterr().out)["barcode"]["status"] == "not_found"
    assert main(["barcode-read", "--image", str(tmp_path / "missing.png")]) == 2
    assert json.loads(capsys.readouterr().out)["barcode"]["status"] == "unavailable"
