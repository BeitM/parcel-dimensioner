"""Experimental fixed-wall cuboid measurement from an empty/occupied image pair.

No camera intrinsics, box dimensions, or manually selected image points are used.
This is a wall-contact silhouette method, not a general single-image 3-D solver.
"""

from __future__ import annotations

import importlib
import json
import math
from dataclasses import dataclass
from pathlib import Path


class MeasurementError(ValueError):
    """An image pair does not provide a usable measurement."""


@dataclass(frozen=True)
class BoardSpec:
    name: str
    columns: int
    rows: int
    first_id: int


BOARDS = (BoardSpec("long", 15, 11, 0), BoardSpec("short", 11, 15, 200))


def _vision():
    try:
        cv = importlib.import_module("cv2")
        np = importlib.import_module("numpy")
        if not hasattr(cv, "aruco") or not hasattr(cv.aruco, "CharucoDetector"):
            raise ImportError("OpenCV needs the ChArUco detector (4.8 or later)")
        return cv, np
    except ImportError as exc:
        raise MeasurementError(
            'Measurement needs the vision extra: python -m pip install -e ".[vision]"'
        ) from exc


def _project(points, matrix, cv, np):
    return cv.perspectiveTransform(np.asarray(points, np.float32).reshape(-1, 1, 2), matrix)[:, 0]


def _measurement_image(image, cv):
    """Bound vision memory and preserve the existing detection thresholds."""
    height, width = image.shape[:2]
    ratio = min(1.0, 1920 / width, 1080 / height)
    if ratio < 1:
        return cv.resize(
            image, (round(width * ratio), round(height * ratio)), interpolation=cv.INTER_AREA
        )
    return image


def _largest_marker_group(corners, cv, np):
    """Separate the wall from small spare prints with repeated IDs, without screen ROIs."""
    centers = np.array([c.reshape(4, 2).mean(axis=0) for c in corners])
    scales = np.array(
        [np.mean(np.linalg.norm(np.roll(c[0], 1, axis=0) - c[0], axis=1)) for c in corners]
    )
    near = np.linalg.norm(centers[:, None] - centers[None, :], axis=2) < 2 * (
        scales[:, None] + scales[None, :]
    )
    remaining = set(range(len(corners)))
    groups = []
    while remaining:
        todo, group = [remaining.pop()], []
        while todo:
            i = todo.pop()
            group.append(i)
            neighbors = set(np.flatnonzero(near[i])) & remaining
            remaining -= neighbors
            todo.extend(neighbors)
        groups.append(group)
    group = max(groups, key=len)
    if len(group) < 8:
        raise MeasurementError("Too few connected wall markers; show both wall patterns clearly.")
    points = np.concatenate([corners[i][0] for i in group])
    return points, float(scales[group].max())


def _detect_wall(image, spec, square, marker, cv, np):
    dictionary = cv.aruco.getPredefinedDictionary(cv.aruco.DICT_6X6_1000)
    corners, ids, _ = cv.aruco.ArucoDetector(dictionary).detectMarkers(image)
    count = spec.columns * spec.rows // 2
    selected = (
        []
        if ids is None
        else [
            c
            for c, i in zip(corners, ids.flatten(), strict=True)
            if spec.first_id <= i < spec.first_id + count
        ]
    )
    if len(selected) < 8:
        raise MeasurementError(f"Cannot detect the {spec.name} wall's markers.")
    points, padding = _largest_marker_group(selected, cv, np)
    lo = np.maximum(0, np.floor(points.min(axis=0) - padding).astype(int))
    hi = np.minimum(image.shape[1::-1], np.ceil(points.max(axis=0) + padding).astype(int))
    clean = np.full_like(image, 127)
    clean[lo[1] : hi[1], lo[0] : hi[0]] = image[lo[1] : hi[1], lo[0] : hi[0]]
    board = cv.aruco.CharucoBoard(
        (spec.columns, spec.rows),
        square,
        marker,
        dictionary,
        np.arange(spec.first_id, spec.first_id + count, dtype=np.int32),
    )
    board.setLegacyPattern(False)
    cc, ci, _, _ = cv.aruco.CharucoDetector(board).detectBoard(clean)
    if ci is None or len(ci) < 12:
        raise MeasurementError(f"Too few ChArUco corners on the {spec.name} wall.")
    xy = cc.reshape(-1, 2)
    obj = board.getChessboardCorners()[ci.flatten(), :2]
    matrix, inliers = cv.findHomography(obj, xy, cv.RANSAC, 2.0)
    if matrix is None or inliers is None or inliers.sum() < 12:
        raise MeasurementError(f"Cannot fit the {spec.name} wall.")
    good = inliers.flatten().astype(bool)
    errors = np.linalg.norm(_project(obj, matrix, cv, np) - xy, axis=1)
    rms = float(np.sqrt(np.mean(errors[good] ** 2)))
    if rms > 2:
        raise MeasurementError(f"Poor {spec.name} wall fit; check focus, flatness, and distortion.")
    return {
        "spec": spec,
        "matrix": matrix,
        "inverse": np.linalg.inv(matrix),
        "width": spec.columns * square,
        "height": spec.rows * square,
        "corners": {int(i): p for i, p in zip(ci.flatten(), xy, strict=True)},
        "fit_rms_pixels": rms,
    }


def _image_line(wall, y, cv, np):
    a, b = _project([(0, y), (wall["width"], y)], wall["matrix"], cv, np)
    line = np.cross([*a, 1], [*b, 1])
    return line / np.linalg.norm(line[:2])


def _intersection(a, b, np):
    point = np.cross(a, b)
    if abs(point[2]) < 1e-6:
        raise MeasurementError("Wall edges do not form a usable corner.")
    return point[:2] / point[2]


def _floor_line(image, wall, square, cv, np):
    """Find the white-paper to colored-platform transition below the printed grid."""
    scale = 2  # rectified pixels per mm, independent of camera resolution
    pad = square
    transform = np.array([[scale, 0, pad * scale], [0, scale, pad * scale], [0, 0, 1]])
    size = (round((wall["width"] + 2 * pad) * scale), round((wall["height"] + 2 * pad + 5) * scale))
    rect = cv.warpPerspective(image, transform @ wall["inverse"], size)
    hsv = cv.cvtColor(rect, cv.COLOR_BGR2HSV).astype(np.float32)
    x0 = round((pad + wall["width"] * 0.15) * scale)
    x1 = round((pad + wall["width"] * 0.85) * scale)
    y0 = round((pad + wall["height"] + 2) * scale)
    y1 = round((pad + wall["height"] + square) * scale)
    s = cv.GaussianBlur(hsv[:, :, 1], (5, 5), 0)
    step = 4  # 2 mm to either side of the transition
    gradient = s[y0 + step : y1 + step, x0:x1] - s[y0 - step : y1 - step, x0:x1]
    profile = np.median(gradient, axis=1)
    peak = int(np.argmax(profile))
    if profile[peak] < 4 or peak in (0, len(profile) - 1):
        raise MeasurementError(
            "Cannot locate the platform below a wall. This prototype needs a visible "
            "white border meeting a contrasting platform."
        )
    y = (y0 + peak) / scale - pad
    return _image_line(wall, y, cv, np), y


def _changed_contour(empty, occupied, walls, square, cv, np):
    footprint = []
    for wall in walls:
        w, h = wall["width"], wall["height"]
        footprint.extend(
            _project(
                [
                    (-square, -square),
                    (w + square, -square),
                    (w + square, h + 2 * square),
                    (-square, h + 2 * square),
                ],
                wall["matrix"],
                cv,
                np,
            )
        )
    roi = np.zeros(empty.shape[:2], np.uint8)
    cv.fillConvexPoly(roi, cv.convexHull(np.int32(footprint)), 255)
    bg, fg = empty.astype(np.float32), occupied.astype(np.float32)
    for channel in range(3):
        a, b = bg[:, :, channel], fg[:, :, channel]
        stable = (roi > 0) & (a > 40) & (a < 220) & (abs(a - b) < 25)
        if stable.sum() < 100:
            raise MeasurementError("The image pair has too little unchanged background.")
        fg[:, :, channel] *= np.median(a[stable] / np.maximum(b[stable], 1))
    delta = np.max(abs(cv.GaussianBlur(bg, (5, 5), 0) - cv.GaussianBlur(fg, (5, 5), 0)), axis=2)
    mask = ((delta > 35) & (roi > 0)).astype(np.uint8) * 255
    mask = cv.morphologyEx(mask, cv.MORPH_OPEN, np.ones((5, 5), np.uint8))
    mask = cv.morphologyEx(mask, cv.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    contours, _ = cv.findContours(mask, cv.RETR_EXTERNAL, cv.CHAIN_APPROX_NONE)
    # The measured object must occlude BOTH wall patterns; floor-only changes do not qualify.
    candidates = []
    for contour in contours:
        if cv.contourArea(contour) < empty.shape[0] * empty.shape[1] * 0.003:
            continue
        valid = True
        for wall in walls:
            xy = _project(contour, wall["inverse"], cv, np)
            on_wall = (
                (xy[:, 0] > 0)
                & (xy[:, 0] < wall["width"])
                & (xy[:, 1] > 0)
                & (xy[:, 1] < wall["height"])
            )
            valid &= on_wall.sum() >= 20
        if valid:
            candidates.append(contour)
    if not candidates:
        raise MeasurementError("No changed box touching both walls was found.")
    candidates.sort(key=cv.contourArea, reverse=True)
    if len(candidates) > 1 and cv.contourArea(candidates[1]) > 0.5 * cv.contourArea(candidates[0]):
        raise MeasurementError("Multiple large changes found; clear the station and recapture.")
    return candidates[0], mask


def _top_line(contour, wall, cv, np):
    xy = _project(contour, wall["inverse"], cv, np)
    on_wall = (
        (xy[:, 0] > 0) & (xy[:, 0] < wall["width"]) & (xy[:, 1] > 0) & (xy[:, 1] < wall["height"])
    )
    points = xy[on_wall]
    histogram, edges = np.histogram(points[:, 1], np.arange(0, wall["height"] + 2, 2))
    peak = int(np.argmax(histogram))
    y = edges[peak] + 1
    support = points[abs(points[:, 1] - y) < 5]
    if len(support) < 20 or np.ptp(support[:, 0]) < wall["width"] / wall["spec"].columns:
        raise MeasurementError("Cannot isolate a straight top edge against each wall.")
    y = float(np.median(support[:, 1]))
    return _image_line(wall, y, cv, np), y


def measure_pair(
    empty_path: Path, box_path: Path, *, square_mm: float = 38.1, marker_mm: float = 28.0
) -> tuple[dict, object, object]:
    """Return an experimental result, annotated image, and diagnostic change mask."""
    if (
        not math.isfinite(square_mm)
        or not math.isfinite(marker_mm)
        or not 0 < marker_mm < square_mm
    ):
        raise MeasurementError("Require finite sizes with 0 < marker-mm < square-mm.")
    cv, np = _vision()
    empty = cv.imread(str(empty_path))
    box = cv.imread(str(box_path))
    if empty is None or box is None:
        raise MeasurementError("Cannot read one of the supplied image files.")
    if empty.shape != box.shape:
        raise MeasurementError("Empty and box images must have the same resolution and crop.")
    source_box = box
    empty, box = _measurement_image(empty, cv), _measurement_image(box, cv)
    walls = [_detect_wall(empty, s, square_mm, marker_mm, cv, np) for s in BOARDS]
    shifts, fitted_shifts = [], []
    movement_warnings = []
    old_fits, new_fits = [], []
    # Scale thresholds with capture size; compare fitted geometry to reduce
    # false alarms from individual ChArUco corner interpolation near the box.
    pixel_scale = box.shape[1] / 1920
    for wall, spec in zip(walls, BOARDS, strict=True):
        current = _detect_wall(box, spec, square_mm, marker_mm, cv, np)
        common = sorted(wall["corners"].keys() & current["corners"].keys())
        if len(common) < 12:
            raise MeasurementError("Too few shared wall corners; reduce occlusion.")
        distances = np.array(
            [np.linalg.norm(wall["corners"][i] - current["corners"][i]) for i in common]
        )
        shift = float(np.percentile(distances, 95))
        old_points = np.array([wall["corners"][i] for i in common])
        board_points = _project(old_points, wall["inverse"], cv, np)
        old_fit = _project(board_points, wall["matrix"], cv, np)
        new_fit = _project(board_points, current["matrix"], cv, np)
        fitted_shift = float(np.percentile(np.linalg.norm(new_fit - old_fit, axis=1), 95))
        if fitted_shift > 6 * pixel_scale:
            raise MeasurementError(
                f"Camera or {spec.name} wall moved ({fitted_shift:.1f} pixels); "
                "capture a new matching empty image."
            )
        if fitted_shift > 3 * pixel_scale:
            movement_warnings.append(
                f"Small {spec.name} wall shift ({fitted_shift:.1f} pixels); "
                "check marked result and recapture empty if readings look off."
            )
        shifts.append(shift)
        fitted_shifts.append(fitted_shift)
        old_fits.append(old_fit)
        new_fits.append(new_fit)
    alignment = None
    if max(fitted_shifts) > 0.5 * pixel_scale:
        alignment, inliers = cv.findHomography(
            np.concatenate(old_fits), np.concatenate(new_fits), cv.RANSAC, 1.5 * pixel_scale
        )
        if alignment is None or inliers is None:
            raise MeasurementError("Cannot align the station; capture a new matching empty image.")
        residuals = [
            float(np.percentile(np.linalg.norm(_project(old, alignment, cv, np) - new, axis=1), 95))
            for old, new in zip(old_fits, new_fits, strict=True)
        ]
        if max(residuals) > 2 * pixel_scale:
            raise MeasurementError("Walls moved differently; capture a new matching empty image.")
        # Small coherent shifts are compensated before subtracting the empty
        # image. Merely accepting shifted checkerboards can corrupt the silhouette.
        empty = cv.warpPerspective(
            empty, alignment, box.shape[1::-1], borderMode=cv.BORDER_REPLICATE
        )
        for wall in walls:
            wall["matrix"] = alignment @ wall["matrix"]
            wall["inverse"] = np.linalg.inv(wall["matrix"])
    floor_lines = [_floor_line(empty, w, square_mm, cv, np) for w in walls]
    floor = _intersection(floor_lines[0][0], floor_lines[1][0], np)
    contour, mask = _changed_contour(empty, box, walls, square_mm, cv, np)
    top_lines = [_top_line(contour, w, cv, np)[0] for w in walls]
    verticals = []
    for wall in walls:
        xy = _project([floor], wall["inverse"], cv, np)[0]
        above = _project([(xy[0], xy[1] - square_mm)], wall["matrix"], cv, np)[0]
        line = np.cross([*floor, 1], [*above, 1])
        line /= np.linalg.norm(line[:2])
        if verticals and line @ verticals[0] < 0:
            line = -line
        verticals.append(line)
    seam = np.mean(verticals, axis=0)
    rear_by_wall = [_intersection(line, seam, np) for line in top_lines]
    rear = np.mean(rear_by_wall, axis=0)
    sides, heights, ends = [], [], []
    for wall, line, (_, floor_y), own_rear in zip(
        walls, top_lines, floor_lines, rear_by_wall, strict=True
    ):
        distances = abs(contour[:, 0, :] @ line[:2] + line[2])
        close = contour[:, 0, :][distances < 4]
        if len(close) < 10:
            raise MeasurementError("Not enough support for the detected box top edge.")
        coordinates = _project(close, wall["inverse"], cv, np)
        rear_xy = _project([rear], wall["inverse"], cv, np)[0]
        # Side away from the joint follows this board's grid X axis.
        direction = 1 if wall["spec"].name == "long" else -1
        spans = direction * (coordinates[:, 0] - rear_xy[0])
        index = int(np.argmax(spans))
        end = close[index].astype(float)
        end -= (end @ line[:2] + line[2]) * line[:2]
        end_xy = _project([end], wall["inverse"], cv, np)[0]
        sides.append(float(direction * (end_xy[0] - rear_xy[0])))
        own_rear_xy = _project([own_rear], wall["inverse"], cv, np)[0]
        heights.append(float(floor_y - own_rear_xy[1]))
        ends.append(end)
    if min(*sides, *heights) <= 10:
        raise MeasurementError("Detected dimensions are implausible; inspect the image pair.")
    if np.linalg.norm(floor - rear) < 5:
        raise MeasurementError("Box height is too small to resolve in this view.")
    warnings = [
        "Experimental: no lens correction; accuracy has not been established.",
        "Assumes a rectangular box flush against both wall planes and a visible platform edge.",
        "Silhouette thresholds can include shadows/reflections or miss low-contrast box edges.",
    ]
    warnings.extend(movement_warnings)
    if abs(heights[0] - heights[1]) > 6.35:
        warnings.append("Wall height estimates disagree by more than 0.25 inch.")
    dimensions = {
        "length": max(sides) / 25.4,
        "width": min(sides) / 25.4,
        "height": float(np.mean(heights)) / 25.4,
    }
    # Visualization only: complete the cuboid from the two wall-axis vanishing
    # directions. These extra edges are inferred, never measurement inputs.
    review_edges = [
        {"start": rear.tolist(), "end": end.tolist(), "kind": "measured"} for end in ends
    ]
    try:
        bottoms = []
        for end, wall, (_, floor_y) in zip(ends, walls, floor_lines, strict=True):
            xy = _project([end], wall["inverse"], cv, np)[0]
            bottoms.append(_project([(xy[0], floor_y)], wall["matrix"], cv, np)[0])
        directions = [wall["matrix"][:, 0] for wall in walls]

        def across(point, direction):
            return np.cross([*point, 1], direction)

        front = _intersection(across(ends[0], directions[1]), across(ends[1], directions[0]), np)
        front_bottom = _intersection(
            across(bottoms[0], directions[1]), across(bottoms[1], directions[0]), np
        )
        proposed = [
            (ends[0], front),
            (ends[1], front),
            (ends[0], bottoms[0]),
            (ends[1], bottoms[1]),
            (front, front_bottom),
            (bottoms[0], front_bottom),
            (bottoms[1], front_bottom),
            (rear, floor),
        ]
        if all(
            np.isfinite(p).all() and 0 <= p[0] < box.shape[1] and 0 <= p[1] < box.shape[0]
            for edge in proposed
            for p in edge
        ):
            review_edges.extend(
                {"start": np.asarray(a).tolist(), "end": np.asarray(b).tolist(), "kind": "inferred"}
                for a, b in proposed
            )
    except (MeasurementError, ValueError, FloatingPointError):
        pass  # Keep actual detection visible even when a wireframe cannot be inferred.
    result = {
        "status": "experimental",
        "method": "automatic-fixed-wall-silhouette",
        "empty_image": str(empty_path),
        "box_image": str(box_path),
        "square_mm": square_mm,
        "marker_mm": marker_mm,
        "source_image_size": list(source_box.shape[1::-1]),
        "processing_image_size": list(box.shape[1::-1]),
        "diagnostic_pixel_space": "processing_image",
        "dimensions_inches": dimensions,
        "height_by_wall_inches": dict(
            zip((s.name for s in BOARDS), (h / 25.4 for h in heights), strict=True)
        ),
        "horizontal_by_wall_inches": dict(
            zip((s.name for s in BOARDS), (v / 25.4 for v in sides), strict=True)
        ),
        "points_pixels": {
            "floor": floor.tolist(),
            "rear_top": rear.tolist(),
            "wall_top_ends": [p.tolist() for p in ends],
            "rear_top_by_wall": [p.tolist() for p in rear_by_wall],
        },
        "box_contour_pixels": cv.approxPolyDP(contour, 1.0, True).reshape(-1, 2).tolist(),
        "box_edges_pixels": review_edges,
        "wall_fit_rms_pixels": {w["spec"].name: w["fit_rms_pixels"] for w in walls},
        "wall_shift_p95_pixels": dict(zip((s.name for s in BOARDS), shifts, strict=True)),
        "wall_fitted_shift_p95_pixels": dict(
            zip((s.name for s in BOARDS), fitted_shifts, strict=True)
        ),
        "station_movement_warnings": movement_warnings,
        "reference_alignment": None if alignment is None else alignment.tolist(),
        "warnings": warnings,
    }
    from parcel_dimensioner.review import draw_box_review

    # Barcode ROI and marked edges must use original capture coordinates.
    factors = np.array(source_box.shape[1::-1]) / np.array(box.shape[1::-1])
    result["points_pixels"] = {
        key: (np.asarray(value) * factors).tolist()
        for key, value in result["points_pixels"].items()
    }
    result["box_contour_pixels"] = (np.asarray(result["box_contour_pixels"]) * factors).tolist()
    for edge in result["box_edges_pixels"]:
        for key in ("start", "end"):
            edge[key] = (np.asarray(edge[key]) * factors).tolist()
    annotated = draw_box_review(source_box, result, cv, np)
    return result, annotated, mask


def save_measurement(
    empty: Path, box: Path, output_dir: Path, *, square_mm: float = 38.1, marker_mm: float = 28.0
) -> dict:
    """Write a reviewable result to a new folder; never overwrite captures or prior results."""
    if output_dir.exists():
        raise MeasurementError(f"Output folder already exists: {output_dir}. Use a new name.")
    result, annotated, mask = measure_pair(empty, box, square_mm=square_mm, marker_mm=marker_mm)
    cv, _ = _vision()
    try:
        output_dir.mkdir(parents=True, exist_ok=False)
        if not cv.imwrite(str(output_dir / "measurement.jpg"), annotated):
            raise OSError("Could not save the annotated image")
        if not cv.imwrite(str(output_dir / "change-mask.png"), mask):
            raise OSError("Could not save the change mask")
        (output_dir / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    except OSError as exc:
        raise MeasurementError(f"Could not save measurement: {exc}") from exc
    return result
