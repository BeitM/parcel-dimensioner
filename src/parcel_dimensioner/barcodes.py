"""Decode the captured package image; preserve candidates separately from the chosen ID."""

from __future__ import annotations

import base64
from datetime import UTC, datetime
from pathlib import Path

MAX_ID_LENGTH = 256


class PackageIdError(ValueError):
    pass


def validate_package_id(value):
    if not isinstance(value, str):
        raise PackageIdError("Package ID must be text.")
    if any(not c.isprintable() for c in value):
        raise PackageIdError("Enter a single-line package ID without control characters.")
    value = value.strip()
    if len(value) > MAX_ID_LENGTH:
        raise PackageIdError(f"Package ID must be at most {MAX_ID_LENGTH} characters.")
    return value or None


def _inside(point, outline):
    """Ray crossing on the vision worker's selected contour, including its boundary."""
    x, y = point
    inside = False
    for (ax, ay), (bx, by) in zip(outline, [*outline[1:], outline[0]], strict=True):
        cross = (x - ax) * (by - ay) - (y - ay) * (bx - ax)
        if (
            abs(cross) < 1e-6
            and min(ax, bx) <= x <= max(ax, bx)
            and min(ay, by) <= y <= max(ay, by)
        ):
            return True
        if (ay > y) != (by > y) and x < (bx - ax) * (y - ay) / (by - ay) + ax:
            inside = not inside
    return inside


def unavailable(reason):
    return {
        "status": "unavailable",
        "candidates": [],
        "selected_index": None,
        "reason": reason,
        "decoder": "zxing-cpp",
    }


def decode_image(path: Path, *, outline=None, enhance=False):
    """Three bounded image-processing passes; never change focus or capture resolution.

    None scans the whole image for standalone diagnostics. The app supplies the
    detected box contour and never substitutes the full image if it is missing.
    """
    try:
        import zxingcpp
        from PIL import Image, ImageOps
    except ImportError:
        return unavailable("Install this project's [touchscreen] or [barcode] extra.")
    if outline is not None and len(outline) < 3:
        return unavailable("No usable box outline; enter the package ID manually.")
    try:
        with Image.open(path) as source:
            image = source.convert("RGB")
        width, height = image.size
        identity = ((1, 0, 0), (0, 1, 0), (0, 0, 1))
        if enhance:
            from parcel_dimensioner.label_reader import label_views

            views = label_views(image, outline)
        else:
            gray = ImageOps.autocontrast(ImageOps.grayscale(image))
            views = ((current, identity) for current in (image, gray, ImageOps.invert(gray)))
        candidates = _read_views(views, outline, zxingcpp, downscale=not enhance)
    except Exception as exc:
        return unavailable(f"Barcode decoding failed: {exc}")
    return barcode_result(
        candidates,
        image_size=[width, height],
        region=(
            "label_target" if enhance else "detected_box" if outline is not None else "full_image"
        ),
    )


def _read_views(views, outline, decoder, *, downscale):
    candidates = {}
    for current, matrix in views:
        for code in decoder.read_barcodes(
            current, try_rotate=True, try_downscale=downscale, try_invert=True, return_errors=False
        ):
            if not code.valid or not code.text:
                continue
            p = code.position
            points = [[v.x, v.y] for v in (p.top_left, p.top_right, p.bottom_right, p.bottom_left)]

            def mapped(x, y, matrix=matrix):
                scale = matrix[2][0] * x + matrix[2][1] * y + matrix[2][2]
                if abs(scale) < 1e-9:
                    raise ValueError("Invalid barcode coordinate transform")
                return [round((row[0] * x + row[1] * y + row[2]) / scale, 3) for row in matrix[:2]]

            points = [mapped(x, y) for x, y in points]
            center = [sum(v[i] for v in points) / 4 for i in (0, 1)]
            if outline is not None and not _inside(center, outline):
                continue
            candidate = candidates.setdefault(
                code.text,
                {
                    "value": code.text,
                    "formats": [],
                    "positions": [],
                    "raw_bytes_base64": [],
                },
            )
            format_name = str(code.format)
            if format_name not in candidate["formats"]:
                candidate["formats"].append(format_name)
            location = {"format": format_name, "points": points}
            if location not in candidate["positions"]:
                candidate["positions"].append(location)
            raw = base64.b64encode(code.bytes).decode("ascii")
            if raw not in candidate["raw_bytes_base64"]:
                candidate["raw_bytes_base64"].append(raw)
    return list(candidates.values())


def barcode_result(found, *, image_size, region):
    selected = None
    status = "not_found" if not found else "multiple"
    reason = None
    if len(found) == 1:
        status = "found"
        try:
            if validate_package_id(found[0]["value"]):
                selected = 0
        except PackageIdError:
            status = "needs_review"
            reason = "Decoded data needs a manually entered package ID."
    return {
        "status": status,
        "candidates": found,
        "selected_index": selected,
        "reason": reason,
        "decoder": "zxing-cpp",
        "decoded_at": datetime.now(UTC).isoformat(),
        "image_size": image_size,
        "region": region,
    }


def initial_identity(barcode):
    selected = barcode["selected_index"]
    if selected is None:
        return {"package_id": None, "package_id_source": None, "package_id_history": []}
    value = validate_package_id(barcode["candidates"][selected]["value"])
    return {
        "package_id": value,
        "package_id_source": "barcode",
        "package_id_history": [
            {"value": value, "source": "barcode", "changed_at": datetime.now(UTC).isoformat()}
        ],
    }


def with_identity(result, value, candidate_index=None):
    value = validate_package_id(value)
    barcode = dict(result["barcode"])
    source = "manual" if value is not None else None
    if candidate_index is not None:
        candidates = barcode["candidates"]
        if type(candidate_index) is not int or not 0 <= candidate_index < len(candidates):
            raise PackageIdError("That barcode is no longer available. Reopen the ID editor.")
        if value != validate_package_id(candidates[candidate_index]["value"]):
            raise PackageIdError("Selected barcode and package ID do not match.")
        if value is None:
            raise PackageIdError("That barcode has no usable package ID. Enter one manually.")
        source = "selected_barcode"
    barcode["selected_index"] = candidate_index
    history = [
        *result.get("package_id_history", []),
        {
            "value": value,
            "source": source,
            "changed_at": datetime.now(UTC).isoformat(),
        },
    ]
    return {
        **result,
        "package_id": value,
        "package_id_source": source,
        "barcode": barcode,
        "package_id_history": history,
    }
