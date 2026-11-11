"""Image-level checks use rendered geometry, never manually chosen detection points."""

import json
from pathlib import Path

import pytest

from parcel_dimensioner.cli import main
from parcel_dimensioner.measurement import MeasurementError, measure_pair, save_measurement

cv = pytest.importorskip("cv2")
np = pytest.importorskip("numpy")
if not hasattr(cv, "aruco") or not hasattr(cv.aruco, "CharucoDetector"):
    pytest.skip("ChArUco detector requires newer OpenCV", allow_module_level=True)


def render_station(tmp_path, dimensions=(230, 200, 100)):
    """Project two real marker boards and a cuboid through a synthetic pinhole camera."""
    position = np.array([1000.0, 1000.0, 1100.0])
    forward = np.array([150.0, 150.0, 150.0]) - position
    forward /= np.linalg.norm(forward)
    right = np.cross(forward, [0, 0, 1.0])
    right /= np.linalg.norm(right)
    down = np.cross(forward, right)
    rotation = np.array([right, down, forward])

    def project(points):
        points = (np.array(points) - position) @ rotation.T
        return np.float32(points[:, :2] / points[:, 2:] * 1700 + [960, 540])

    empty = np.full((1080, 1920, 3), (125, 170, 205), np.uint8)
    dictionary = cv.aruco.getPredefinedDictionary(cv.aruco.DICT_6X6_1000)
    for columns, rows, first in [(15, 11, 0), (11, 15, 200)]:
        w, h = columns * 38.1, rows * 38.1
        # Different side margins, equal 12 mm floor margin; neither is a detection input.
        if first == 0:

            def world(u, v, height=h):
                return (0, u + 20, height + 12 - v)
        else:

            def world(u, v, width=w, height=h):
                return (width + 17 - u, 0, height + 12 - v)

        polygon = project(
            [world(-20, -15), world(w + 20, -15), world(w + 20, h + 12), world(-20, h + 12)]
        )
        cv.fillConvexPoly(empty, np.int32(polygon), (240, 240, 240))
        board = cv.aruco.CharucoBoard(
            (columns, rows),
            38.1,
            28.0,
            dictionary,
            np.arange(first, first + columns * rows // 2, dtype=np.int32),
        )
        board.setLegacyPattern(False)
        texture = board.generateImage((columns * 100, rows * 100))
        corners = project([world(0, 0), world(w, 0), world(w, h), world(0, h)])
        H = cv.getPerspectiveTransform(
            np.float32(
                [
                    [0, 0],
                    [texture.shape[1] - 1, 0],
                    [texture.shape[1] - 1, texture.shape[0] - 1],
                    [0, texture.shape[0] - 1],
                ]
            ),
            corners,
        )
        warped = cv.warpPerspective(texture, H, (1920, 1080))
        mask = np.zeros(empty.shape[:2], np.uint8)
        cv.fillConvexPoly(mask, np.int32(corners), 255)
        empty[mask > 0] = warped[mask > 0, None]
    box = empty.copy()
    x, y, z = dimensions
    for face, color in [
        ([(x, 0, 0), (x, y, 0), (x, y, z), (x, 0, z)], (65, 110, 155)),
        ([(0, y, 0), (x, y, 0), (x, y, z), (0, y, z)], (75, 120, 165)),
        ([(0, 0, z), (x, 0, z), (x, y, z), (0, y, z)], (90, 135, 180)),
    ]:
        cv.fillConvexPoly(box, np.int32(project(face)), color)
    empty_path, box_path = tmp_path / "empty.jpg", tmp_path / "box.jpg"
    cv.imwrite(str(empty_path), empty)
    cv.imwrite(str(box_path), box)
    return empty_path, box_path


@pytest.mark.parametrize("physical", [(230, 200, 100), (180, 260, 70), (300, 240, 270)])
def test_rendered_box_automatic_measurement(tmp_path, physical):
    empty, box = render_station(tmp_path, physical)
    result, _, _ = measure_pair(empty, box)
    dimensions = result["dimensions_inches"]
    assert sum(edge["kind"] == "measured" for edge in result["box_edges_pixels"]) == 2
    assert sum(edge["kind"] == "inferred" for edge in result["box_edges_pixels"]) >= 7
    # Detection thresholds/blur are approximate; errors must stay below 8 mm on clean geometry.
    expected_dimensions = [max(physical[:2]), min(physical[:2]), physical[2]]
    for name, expected in zip(["length", "width", "height"], expected_dimensions, strict=True):
        assert dimensions[name] * 25.4 == pytest.approx(expected, abs=8)
    output = tmp_path / "review"
    save_measurement(empty, box, output)
    saved = json.loads((output / "result.json").read_text())
    assert saved["method"] == "automatic-fixed-wall-silhouette"
    assert (output / "measurement.jpg").is_file()
    assert (output / "change-mask.png").is_file()


def test_no_box_is_rejected(tmp_path):
    empty, _ = render_station(tmp_path)
    with pytest.raises(MeasurementError, match="No changed box"):
        measure_pair(empty, empty)


@pytest.mark.parametrize("physical", [(400, 330, 65), (450, 360, 65)])
def test_large_box_outline_includes_front_beyond_projected_walls(tmp_path, physical):
    empty, box = render_station(tmp_path, physical)
    result, _, mask = measure_pair(empty, box)
    # The physical package is entirely in view, but its front lies beyond the
    # old wall-derived analysis polygon. Compare with the rendered pixels,
    # rather than reproducing that polygon in the expected result.
    changed = np.max(cv.absdiff(cv.imread(str(empty)), cv.imread(str(box))), axis=2) > 60
    rows, columns = np.nonzero(changed)
    contour = np.asarray(result["box_contour_pixels"])
    np.testing.assert_allclose(contour.min(axis=0), [columns.min(), rows.min()], atol=3)
    np.testing.assert_allclose(contour.max(axis=0), [columns.max(), rows.max()], atol=3)
    assert np.count_nonzero(changed & (mask > 0)) / np.count_nonzero(changed) > 0.99
    for name, mm in zip(("length", "width", "height"), physical, strict=True):
        assert result["dimensions_inches"][name] * 25.4 == pytest.approx(mm, abs=8)


@pytest.mark.parametrize("scale", [1, 2.4])
def test_front_outside_image_still_measures_with_open_outline(tmp_path, scale):
    empty, box = render_station(tmp_path, (450, 360, 65))
    baseline, _, _ = measure_pair(empty, box)
    for path in (empty, box):
        cropped = cv.imread(str(path))[:900]
        if scale != 1:
            cropped = cv.resize(cropped, (round(1920 * scale), round(900 * scale)))
        cv.imwrite(str(path), cropped)
    result = save_measurement(empty, box, tmp_path / "review")
    assert result["box_outline_complete"] is False
    assert result["outline_warnings"]
    for key, value in baseline["dimensions_inches"].items():
        assert result["dimensions_inches"][key] == pytest.approx(value, abs=0.05)
    for segment in result["box_contour_segments_pixels"]:
        points = np.asarray(segment)
        assert points[:, 1].max() < 895 * scale
    saved = json.loads((tmp_path / "review/result.json").read_text())
    assert saved["box_outline_complete"] is False


@pytest.mark.parametrize("left,right", [(650, 1920), (0, 1200)])
def test_cut_off_wall_contact_edge_cannot_shorten_measurement(tmp_path, left, right):
    empty, box = render_station(tmp_path, (450, 360, 65))
    for path in (empty, box):
        cv.imwrite(str(path), cv.imread(str(path))[:, left:right])
    with pytest.raises(MeasurementError, match="required top edge"):
        measure_pair(empty, box)


def test_multiple_frame_gaps_produce_separate_visible_paths():
    from parcel_dimensioner.measurement import _visible_outline

    contour = np.int32(
        [
            [[0, 200]],
            [[100, 100]],
            [[500, 100]],
            [[599, 200]],
            [[599, 300]],
            [[500, 350]],
            [[100, 350]],
            [[0, 300]],
        ]
    )
    _, _, segments, complete = _visible_outline(contour, (400, 600), cv, np)
    assert not complete
    assert segments == [[[100, 100], [500, 100]], [[500, 350], [100, 350]]]


def test_full_resolution_measurement_preserves_native_barcode_coordinates(tmp_path):
    empty, box = render_station(tmp_path)
    baseline, _, _ = measure_pair(empty, box)
    for path in (empty, box):
        enlarged = cv.resize(cv.imread(str(path)), (4608, 2592), interpolation=cv.INTER_LINEAR)
        cv.imwrite(str(path), enlarged)
    result, annotated, mask = measure_pair(empty, box)
    assert result["source_image_size"] == [4608, 2592]
    assert result["processing_image_size"] == [1920, 1080]
    assert annotated.shape[:2] == (2592, 4608)
    assert mask.shape == (1080, 1920)
    for key, value in baseline["dimensions_inches"].items():
        assert result["dimensions_inches"][key] == pytest.approx(value, abs=0.1)
    np.testing.assert_allclose(
        result["points_pixels"]["floor"], np.array(baseline["points_pixels"]["floor"]) * 2.4, atol=3
    )
    assert max(p[0] for p in result["box_contour_pixels"]) > 1920
    assert result["box_outline_complete"] is True
    np.testing.assert_allclose(
        result["box_contour_segments_pixels"][0], result["box_contour_pixels"]
    )
    for edge in result["box_edges_pixels"]:
        assert len(edge["start"]) == len(edge["end"]) == 2


def test_moved_station_is_rejected(tmp_path):
    empty, box = render_station(tmp_path)
    image = cv.imread(str(box))
    shifted = cv.warpAffine(image, np.float32([[1, 0, 12], [0, 1, 0]]), image.shape[1::-1])
    cv.imwrite(str(box), shifted)
    with pytest.raises(MeasurementError, match="moved"):
        measure_pair(empty, box)


def test_small_station_shift_warns_instead_of_blocking(tmp_path):
    empty, box = render_station(tmp_path)
    image = cv.imread(str(box))
    shifted = cv.warpAffine(image, np.float32([[1, 0, 4], [0, 1, 0]]), image.shape[1::-1])
    cv.imwrite(str(box), shifted)
    result, _, _ = measure_pair(empty, box)
    assert result["station_movement_warnings"]
    assert max(result["wall_fitted_shift_p95_pixels"].values()) < 6
    for key, mm in (("length", 230), ("width", 200), ("height", 100)):
        assert result["dimensions_inches"][key] * 25.4 == pytest.approx(mm, abs=12)


def test_independent_wall_shift_cannot_use_global_alignment(tmp_path, monkeypatch):
    from parcel_dimensioner import measurement

    empty, box = render_station(tmp_path)
    detect = measurement._detect_wall
    calls = 0

    def shifted_fit(*args):
        nonlocal calls
        wall = detect(*args)
        calls += 1
        if calls == 3:  # Occupied long wall only: opposite wall stays fixed.
            movement = np.array([[1, 0, 5.5], [0, 1, 0], [0, 0, 1]])
            wall["matrix"] = movement @ wall["matrix"]
            wall["inverse"] = np.linalg.inv(wall["matrix"])
            wall["corners"] = {i: np.add(p, [5.5, 0]) for i, p in wall["corners"].items()}
        return wall

    monkeypatch.setattr(measurement, "_detect_wall", shifted_fit)
    with pytest.raises(MeasurementError, match="Walls moved differently"):
        measure_pair(empty, box)


def test_wrong_size_and_missing_boards_are_rejected(tmp_path):
    empty, box = render_station(tmp_path)
    cv.imwrite(str(box), np.zeros((100, 100, 3), np.uint8))
    with pytest.raises(MeasurementError, match="same resolution"):
        measure_pair(empty, box)
    with pytest.raises(MeasurementError, match="markers"):
        measure_pair(box, box)


@pytest.mark.parametrize("square,marker", [(0, 28), (38.1, 40), (float("nan"), 28)])
def test_invalid_physical_sizes(square, marker):
    with pytest.raises(MeasurementError, match="finite sizes"):
        measure_pair(Path("unused"), Path("unused"), square_mm=square, marker_mm=marker)


def test_existing_results_are_preserved(tmp_path):
    marker = tmp_path / "result.json"
    marker.write_text("keep")
    with pytest.raises(MeasurementError, match="already exists"):
        save_measurement(Path("unused"), Path("unused"), tmp_path)
    assert marker.read_text() == "keep"


def test_cli_bad_image_pair_has_clean_error(tmp_path, capsys):
    status = main(
        [
            "measure",
            "--empty",
            str(tmp_path / "missing"),
            "--box",
            str(tmp_path / "missing"),
            "--output-dir",
            str(tmp_path / "new"),
        ]
    )
    assert status == 2
    assert "Cannot read" in capsys.readouterr().err


def test_cli_missing_vision_extra_has_install_instructions(tmp_path, capsys, monkeypatch):
    from parcel_dimensioner import measurement

    def unavailable(_):
        raise ImportError("not installed")

    monkeypatch.setattr(measurement.importlib, "import_module", unavailable)
    status = main(
        [
            "measure",
            "--empty",
            "empty.jpg",
            "--box",
            "box.jpg",
            "--output-dir",
            str(tmp_path / "new"),
        ]
    )
    assert status == 2
    assert "[vision]" in capsys.readouterr().err
