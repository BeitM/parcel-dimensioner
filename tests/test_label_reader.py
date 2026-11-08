import pytest

from parcel_dimensioner.label_reader import preview_point, target_bounds


def test_letterbox_touches_and_target_edge_mapping():
    assert preview_point(10, 20, (0, 50, 800, 450)) is None
    assert preview_point(600, 350, (0, 50, 800, 450)) == pytest.approx((0.75, 2 / 3))
    assert target_bounds((1, 0), width=0.12, height=0.16) == pytest.approx((0.88, 0, 1, 0.16))
    with pytest.raises(ValueError):
        target_bounds((float("nan"), 0.5))


@pytest.mark.parametrize("angle", [0, 25])
def test_small_rotated_label_in_large_frame_uses_original_coordinates(tmp_path, angle):
    image = pytest.importorskip("PIL.Image")
    zxing = pytest.importorskip("zxingcpp")
    from parcel_dimensioner.barcodes import decode_image, initial_identity

    code = image.fromarray(
        zxing.create_barcode("FARTHER-42", zxing.BarcodeFormat.Code128).to_image(scale=3)
    ).convert("RGB")
    code = code.rotate(angle, expand=True, fillcolor="white")
    canvas = image.new("RGB", (4608, 2592), "#777777")
    canvas.paste(code, (3000, 1600))
    # A background ID outside the target must never be attached to this label.
    background = image.fromarray(
        zxing.create_barcode("BACKGROUND", zxing.BarcodeFormat.QRCode).to_image(scale=4)
    ).convert("RGB")
    canvas.paste(background, (100, 100))
    path = tmp_path / "label.jpg"
    canvas.save(path, quality=95)
    outline = [[2800, 1400], [4200, 1400], [4200, 2400], [2800, 2400]]
    result = decode_image(path, outline=outline, enhance=True)
    assert initial_identity(result)["package_id"] == "FARTHER-42"
    assert result["image_size"] == [4608, 2592]
    assert len(result["candidates"]) == 1
    for location in result["candidates"][0]["positions"]:
        assert all(2800 < x < 4200 and 1400 < y < 2400 for x, y in location["points"])


def test_straightened_views_map_back_to_original_barcode_quad(monkeypatch):
    image = pytest.importorskip("PIL.Image")
    cv = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    from parcel_dimensioner.label_reader import label_views

    class Detector:
        def detect(self, pixels):
            return True, np.float32([[[70, 240], [100, 80], [350, 110], [320, 270]]])

    monkeypatch.setattr(cv, "barcode_BarcodeDetector", Detector)
    views = list(label_views(image.new("RGB", (600, 400), "white"), None))
    straight, inverse = views[-2]
    corners = cv.perspectiveTransform(
        np.float32(
            [
                [
                    [0, 0],
                    [straight.width - 1, 0],
                    [straight.width - 1, straight.height - 1],
                    [0, straight.height - 1],
                ]
            ]
        ),
        np.array(inverse),
    )
    assert corners[0, 0, 0] < corners[0, 1, 0]
    assert corners[0, 0, 1] < corners[0, 3, 1]
    assert abs(corners[0, 0, 1] - corners[0, 1, 1]) > 10  # Native image tilt is preserved.
