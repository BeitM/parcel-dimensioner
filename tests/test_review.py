from copy import deepcopy

import pytest


def test_box_lines_are_thick_and_do_not_modify_source():
    cv = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    from parcel_dimensioner.review import draw_box_review

    image = np.zeros((1080, 1920, 3), np.uint8)
    original = image.copy()
    result = {
        "box_contour_pixels": [[100, 100], [500, 100], [500, 700], [100, 700]],
        "box_edges_pixels": [
            {"start": [600, 200], "end": [1200, 200], "kind": "measured"},
            {"start": [600, 400], "end": [1200, 400], "kind": "inferred"},
        ],
    }
    marked = draw_box_review(image, result, cv, np)
    assert np.array_equal(image, original)
    assert marked.shape == image.shape
    assert np.all(marked[98:103, 300] == [70, 230, 70])
    assert np.all(marked[198:203, 900] == [0, 255, 255])
    assert marked[400, 607].tolist() == [255, 215, 65]
    assert marked[400, 627].tolist() == [0, 0, 0]  # Gap between inferred dashes.


def test_partial_green_outline_does_not_close_across_missing_front():
    cv = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    from parcel_dimensioner.review import draw_box_review

    image = np.zeros((400, 600, 3), np.uint8)
    result = {
        "box_outline_complete": False,
        "box_contour_pixels": [[100, 399], [100, 100], [500, 100], [500, 399]],
        "box_contour_segments_pixels": [[[100, 300], [100, 100], [500, 100], [500, 300]]],
    }
    marked = draw_box_review(image, result, cv, np)
    assert marked[100, 300].tolist() == [70, 230, 70]
    assert marked[300, 300].tolist() == [0, 0, 0]  # No line joining the two open ends.
    assert marked[399, 300].tolist() == [0, 0, 0]  # No artificial frame-edge outline.


def test_box_detail_zooms_to_native_outline_and_retains_context():
    image = pytest.importorskip("PIL.Image")
    from parcel_dimensioner.review import box_detail

    source = image.new("RGB", (4608, 2592), "white")
    source.putpixel((2400, 1800), (255, 0, 0))
    result = {"box_contour_pixels": [[2000, 1500], [2800, 1500], [2800, 2591], [2000, 2591]]}
    detail = box_detail(source, result)
    assert 800 < detail.width < 2000
    assert 1091 < detail.height < 1800
    assert detail.getbbox() == (0, 0, detail.width, detail.height)
    assert source.size == (4608, 2592)
    assert detail.getextrema()[1] == (0, 255)  # The box's reference pixel remains in the crop.
    # A crop with a missing front corner stays within the actual source image.
    assert detail.getpixel((0, detail.height - 1)) == (255, 255, 255)


def test_barcode_review_highlights_candidates_and_refreshes_id(tmp_path):
    image = pytest.importorskip("PIL.Image")
    np = pytest.importorskip("numpy")
    from parcel_dimensioner.review import save_scan_review

    folder = tmp_path / "review"
    folder.mkdir()
    base = folder / "measurement.jpg"
    image.new("RGB", (1920, 1080), "#dddddd").save(base)
    original_bytes = base.read_bytes()
    result = {
        "package_id": "AUTO-123",
        "package_id_source": "barcode",
        "barcode": {
            "status": "found",
            "selected_index": 0,
            "candidates": [
                {
                    "value": "AUTO-123",
                    "formats": ["Code 128"],
                    "positions": [
                        {
                            "format": "Code 128",
                            "points": [[700, 500], [1100, 500], [1100, 650], [700, 650]],
                        }
                    ],
                }
            ],
        },
    }
    before = deepcopy(result)
    target = save_scan_review(tmp_path, result)
    with image.open(target) as im:
        first = np.array(im)
    assert first.shape[0] > 1080 and first.shape[1] == 1920
    color = first[500, 900].astype(int)
    assert np.max(abs(color - [255, 84, 222])) < 35
    result["package_id"] = "MANUAL-999"
    result["package_id_source"] = "manual"
    result["barcode"]["selected_index"] = None
    save_scan_review(tmp_path, result)
    with image.open(target) as im:
        second = np.array(im)
    assert not np.array_equal(first[1080:], second[1080:])
    assert base.read_bytes() == original_bytes
    assert not (folder / "scan.tmp.jpg").exists()
    assert before["barcode"]["candidates"] == result["barcode"]["candidates"]


def test_long_manual_id_and_no_barcodes_render_without_overwriting_base(tmp_path):
    image = pytest.importorskip("PIL.Image")
    from parcel_dimensioner.review import save_scan_review

    (tmp_path / "review").mkdir()
    image.new("RGB", (480, 270), "gray").save(tmp_path / "review/measurement.jpg")
    target = save_scan_review(
        tmp_path,
        {
            "package_id": "X" * 256,
            "package_id_source": "manual",
            "barcode": {"status": "not_found", "candidates": [], "selected_index": None},
        },
    )
    with image.open(target) as im:
        assert im.width == 480 and im.height > 270


def test_separate_label_coordinates_never_drawn_on_station_image(tmp_path):
    image = pytest.importorskip("PIL.Image")
    np = pytest.importorskip("numpy")
    from parcel_dimensioner.review import save_scan_review

    (tmp_path / "review").mkdir()
    image.new("RGB", (1920, 1080), "#dddddd").save(tmp_path / "review/measurement.jpg")
    image.new("RGB", (1920, 1080), "#dddddd").save(tmp_path / "label.jpg")
    result = {
        "package_id": "CLOSE-42",
        "barcode_capture": {"mode": "separate_label"},
        "barcode": {
            "status": "found",
            "selected_index": 0,
            "candidates": [
                {
                    "value": "CLOSE-42",
                    "formats": ["Code 128"],
                    "positions": [{"points": [[700, 500], [1100, 500], [1100, 650], [700, 650]]}],
                }
            ],
        },
    }
    target = save_scan_review(tmp_path, result)
    with image.open(target) as im:
        pixels = np.array(im).astype(int)
    assert abs(pixels[500, 900] - [221, 221, 221]).max() < 5
    pink = (pixels[1100:, :, 0] > 220) & (pixels[1100:, :, 1] < 130) & (pixels[1100:, :, 2] > 170)
    assert pink.sum() > 500
