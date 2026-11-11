"""Readable diagnostic overlays. Drawing never changes measurement or barcode inputs."""

from __future__ import annotations

import math
from pathlib import Path


def box_detail(image, result):
    """Display-only crop around the detected box, with nearby wall context."""
    points = result.get("box_contour_pixels", [])
    points = [p for p in points if len(p) == 2 and all(math.isfinite(v) for v in p)]
    if len(points) < 3:
        return image.copy()
    xs, ys = zip(*points, strict=True)
    width, height = image.size
    # Limit magnification on tiny packages; preserve some wall and floor context.
    span_x = min(width, max(width * 0.3, (max(xs) - min(xs)) * 1.3 + 80))
    span_y = min(height, max(height * 0.3, (max(ys) - min(ys)) * 1.3 + 80))
    left = max(0, min(width - span_x, (min(xs) + max(xs) - span_x) / 2))
    top = max(0, min(height - span_y, (min(ys) + max(ys) - span_y) / 2))
    return image.crop((round(left), round(top), round(left + span_x), round(top + span_y)))


def draw_box_review(image, result, cv, np):
    marked = image.copy()
    scale = max(0.5, image.shape[1] / 1920)
    width = max(3, round(6 * scale))

    def line(a, b, color, thickness=width):
        a, b = tuple(np.int32(a)), tuple(np.int32(b))
        cv.line(marked, a, b, (12, 12, 12), thickness + 4, cv.LINE_AA)
        cv.line(marked, a, b, color, thickness, cv.LINE_AA)

    segments = result.get("box_contour_segments_pixels", [result.get("box_contour_pixels", [])])
    closed = result.get("box_outline_complete", True)
    for segment in segments:
        outline = np.asarray(segment, np.int32).reshape(-1, 1, 2)
        if len(outline) >= 2:
            cv.polylines(marked, [outline], closed, (12, 12, 12), width + 4, cv.LINE_AA)
            cv.polylines(marked, [outline], closed, (70, 230, 70), width, cv.LINE_AA)
    for edge in result.get("box_edges_pixels", []):
        a, b = np.asarray(edge["start"], float), np.asarray(edge["end"], float)
        if edge["kind"] == "measured":
            line(a, b, (0, 255, 255))
        else:
            distance = float(np.linalg.norm(b - a))
            dash = max(8, round(18 * scale))
            for start in range(0, int(distance), 2 * dash):
                lo, hi = start / distance, min(start + dash, distance) / distance
                line(a + (b - a) * lo, a + (b - a) * hi, (255, 215, 65), max(3, width - 1))
    points = result.get("points_pixels", {})
    labels = [("Floor", points.get("floor")), ("Rear top", points.get("rear_top"))]
    labels += list(zip(("Long top", "Short top"), points.get("wall_top_ends", []), strict=False))
    for label, point in labels:
        if point is None:
            continue
        p = tuple(np.int32(point))
        cv.circle(marked, p, max(5, round(8 * scale)), (20, 20, 240), -1, cv.LINE_AA)
        origin = (max(0, p[0] + 10), max(20, p[1] - 14))
        cv.putText(
            marked,
            label,
            origin,
            cv.FONT_HERSHEY_SIMPLEX,
            0.7 * scale,
            (0, 0, 0),
            max(3, round(5 * scale)),
            cv.LINE_AA,
        )
        cv.putText(
            marked,
            label,
            origin,
            cv.FONT_HERSHEY_SIMPLEX,
            0.7 * scale,
            (0, 255, 255),
            max(1, round(2 * scale)),
            cv.LINE_AA,
        )
    return marked


def save_scan_review(folder: Path, result: dict):
    """Add barcode polygons and a text panel to the immutable vision review image."""
    from PIL import Image, ImageDraw, ImageFont

    with Image.open(folder / "review" / "measurement.jpg") as source:
        image = source.convert("RGB")
    station_image = image
    separate_label = result.get("barcode_capture", {}).get("mode") == "separate_label"
    if separate_label:
        with Image.open(folder / "label.jpg") as source:
            image = source.convert("RGB")
    width = image.width
    scale = width / 1920
    pen = ImageDraw.Draw(image)
    stroke = max(3, round(8 * scale))

    def font(size):
        size = max(14, round(size * scale))
        for name in ("DejaVuSans.ttf", "arial.ttf", "LiberationSans-Regular.ttf"):
            try:
                return ImageFont.truetype(name, size)
            except OSError:
                pass
        try:
            return ImageFont.load_default(size=size)
        except TypeError:
            return ImageFont.load_default()

    body_font, heading_font = font(30), font(42)
    barcode = result.get("barcode", {})
    candidates = barcode.get("candidates", [])
    selected = barcode.get("selected_index")
    for index, candidate in enumerate(candidates):
        color = "#ff54de" if index == selected else "#ffad42"
        for location in candidate.get("positions", []):
            if separate_label and location.get("image") not in (
                None,
                result.get("barcode_capture", {}).get("source_frame"),
            ):
                continue
            points = [tuple(p) for p in location["points"]]
            if len(points) != 4:
                continue
            closed = [*points, points[0]]
            pen.line(closed, fill="#101010", width=stroke + 6, joint="curve")
            pen.line(closed, fill=color, width=stroke, joint="curve")
            label = f"Barcode {index + 1}" + (" (selected)" if index == selected else "")
            size = pen.textbbox((0, 0), label, font=body_font)
            x = min(max(4, min(p[0] for p in points)), max(4, width - (size[2] - size[0]) - 8))
            y = min(
                max(4, min(p[1] for p in points) - round(42 * scale)),
                max(4, image.height - (size[3] - size[1]) - 8),
            )
            bounds = pen.textbbox((x, y), label, font=body_font)
            pen.rectangle(
                (bounds[0] - 4, bounds[1] - 4, bounds[2] + 4, bounds[3] + 4), fill="#101820"
            )
            pen.text((x, y), label, font=body_font, fill=color)

    if separate_label:
        # Barcode coordinates belong to the close-up, never the station image.
        image.thumbnail((station_image.width, max(100, station_image.height // 2)))
        caption_height = max(32, round(50 * scale))
        combined = Image.new(
            "RGB",
            (station_image.width, station_image.height + caption_height + image.height),
            "#10202c",
        )
        combined.paste(station_image, (0, 0))
        ImageDraw.Draw(combined).text(
            (12, station_image.height + 4), "Separate label capture", font=body_font, fill="white"
        )
        combined.paste(
            image, ((combined.width - image.width) // 2, station_image.height + caption_height)
        )
        image = combined
        pen = ImageDraw.Draw(image)

    def safe_text(text):
        return "".join(c if c.isprintable() else " " for c in str(text))

    value = result.get("package_id")
    source = result.get("package_id_source")
    if value:
        label = "Manual package ID: " if source == "manual" else "Package ID: "
        heading = label + safe_text(value)
    elif candidates:
        heading = "Package ID: not selected - choose a barcode or enter an ID"
    elif barcode.get("status") == "not_found":
        heading = "No barcode found - package ID not entered"
    elif barcode.get("status") == "skipped":
        heading = "Barcode skipped - package ID not entered"
    else:
        heading = "Barcode unavailable - package ID not entered"
    lines = [
        (
            "Green: detected outline | Yellow: measured top | Blue dashed: inferred edges",
            body_font,
            "#c3d6e2",
        ),
        (heading, heading_font, "white"),
    ]
    for warning in result.get("outline_warnings", []):
        lines.append((warning, body_font, "#ffcc71"))
    for index, candidate in enumerate(candidates[:4]):
        text = safe_text(candidate["value"])
        if len(text) > 80:
            text = text[:77] + "..."
        lines.append(
            (
                f"Barcode {index + 1} [{'/'.join(candidate['formats'])}]: {text}",
                body_font,
                "#ff54de" if index == selected else "#ffad42",
            )
        )
    if len(candidates) > 4:
        lines.append(
            (f"{len(candidates) - 4} more candidates available in Choose ID.", body_font, "white")
        )
    # Wrap to measured pixel widths, including long IDs with no spaces.
    margin = max(12, round(24 * scale))
    wrapped = []
    for text, face, color in lines:
        line = ""
        for character in text:
            if line and pen.textlength(line + character, font=face) > width - 2 * margin:
                wrapped.append((line, face, color))
                line = ""
            line += character
        wrapped.append((line, face, color))
    heights = [
        face.getbbox("Ag")[3] - face.getbbox("Ag")[1] + max(10, round(16 * scale))
        for _, face, _ in wrapped
    ]
    canvas = Image.new("RGB", (width, image.height + sum(heights) + 2 * margin), "#10202c")
    canvas.paste(image, (0, 0))
    pen = ImageDraw.Draw(canvas)
    y = image.height + margin
    for (text, face, color), height in zip(wrapped, heights, strict=True):
        pen.text((margin, y), text, font=face, fill=color, anchor="lt")
        y += height
    target = folder / "review" / "scan.jpg"
    temporary = target.with_name("scan.tmp.jpg")
    canvas.save(temporary, format="JPEG", quality=95)
    temporary.replace(target)
    return target
