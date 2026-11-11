"""Bounded label views, always mapped back to the original photograph."""

from __future__ import annotations


def target_bounds(point, *, width=0.44, height=0.56):
    """Normalized focus/decode windows: keep edge targets within the sensor view."""
    x, y = point
    if not (0 <= x <= 1 and 0 <= y <= 1):
        raise ValueError("Tap inside the live camera image to select the label.")
    left = max(0, min(1 - width, x - width / 2))
    top = max(0, min(1 - height, y - height / 2))
    return left, top, left + width, top + height


def preview_point(x, y, bounds):
    """Map a touch to the displayed image, rejecting letterbox padding."""
    left, top, width, height = bounds
    if width <= 0 or height <= 0 or not (left <= x <= left + width and top <= y <= top + height):
        return None
    return (x - left) / width, (y - top) / height


def live_views(image, outline, *, enhance):
    """Small targeted passes first; preserve native detail for difficult labels.

    Decode-frame stops this lazy sequence on success. Coordinates are always
    mapped back to the same full source exposure, even after cropping/resizing.
    """
    from PIL import Image, ImageFilter, ImageOps

    width, height = image.size
    xs, ys = zip(*outline, strict=True) if outline else ((0, width), (0, height))
    pad_x, pad_y = (max(xs) - min(xs)) * 0.08, (max(ys) - min(ys)) * 0.08
    left, top = max(0, int(min(xs) - pad_x)), max(0, int(min(ys) - pad_y))
    right, bottom = min(width, int(max(xs) + pad_x)), min(height, int(max(ys) + pad_y))
    gray = ImageOps.grayscale(image.crop((left, top, right, bottom)))
    transform = ((1, 0, left), (0, 1, top), (0, 0, 1))
    if enhance and max(gray.size) > 1800:
        small = gray.resize((gray.width // 2, gray.height // 2), Image.Resampling.BILINEAR)
        yield (
            small,
            ((gray.width / small.width, 0, left), (0, gray.height / small.height, top), (0, 0, 1)),
        )
    yield gray, transform
    if enhance:
        yield gray.filter(ImageFilter.UnsharpMask(1, 150, 2)), transform
        # Retain local-contrast and perspective recovery without rescanning
        # the entire photograph or building images after an earlier success.
        yield from _straightened_views(gray, transform)


def _straightened_views(gray, transform):
    from PIL import Image, ImageFilter

    try:
        import cv2 as cv
        import numpy as np
    except ImportError:
        return
    array = np.array(gray)
    yield Image.fromarray(cv.createCLAHE(2.0, (8, 8)).apply(array)), transform
    if not hasattr(cv, "barcode_BarcodeDetector"):
        return
    ratio = min(1, 1600 / max(gray.size))
    search = cv.resize(array, (round(gray.width * ratio), round(gray.height * ratio)))
    try:
        detected, points = cv.barcode_BarcodeDetector().detect(search)
    except cv.error:
        return
    if not detected or points is None:
        return
    for quad in points[:3]:
        q = quad[[1, 2, 3, 0]].astype(np.float32) / ratio
        q = (q - q.mean(axis=0)) * 1.18 + q.mean(axis=0)
        w = max(np.linalg.norm(q[1] - q[0]), np.linalg.norm(q[2] - q[3]))
        h = max(np.linalg.norm(q[3] - q[0]), np.linalg.norm(q[2] - q[1]))
        if min(w, h) < 8:
            continue
        scale = min(2, 2000 / w, 900 / h)
        w, h = max(16, round(w * scale)), max(16, round(h * scale))
        destination = np.float32([[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]])
        matrix = cv.getPerspectiveTransform(q, destination)
        straight = cv.warpPerspective(array, matrix, (w, h), borderValue=255)
        inverse = np.array(transform) @ np.linalg.inv(matrix)
        current = Image.fromarray(straight)
        yield current, inverse.tolist()
        yield current.filter(ImageFilter.UnsharpMask(1, 200, 2)), inverse.tolist()


def label_views(image, outline):
    """Native crop, contrast, resampling and up to three detected straightened codes.

    No generated/reconstructed bar patterns are used. ZXing still has to validate
    every result. Each inverse transform maps its four corners to source pixels.
    """
    from PIL import Image, ImageFilter, ImageOps

    width, height = image.size
    if outline:
        xs, ys = zip(*outline, strict=True)
        pad_x, pad_y = (max(xs) - min(xs)) * 0.25, (max(ys) - min(ys)) * 0.2
        left, top = max(0, int(min(xs) - pad_x)), max(0, int(min(ys) - pad_y))
        right, bottom = min(width, int(max(xs) + pad_x)), min(height, int(max(ys) + pad_y))
    else:
        left, top, right, bottom = 0, 0, width, height
    crop = image.crop((left, top, right, bottom))
    transform = ((1, 0, left), (0, 1, top), (0, 0, 1))
    gray = ImageOps.autocontrast(ImageOps.grayscale(crop))
    for current in (gray, ImageOps.invert(gray), gray.filter(ImageFilter.UnsharpMask(1.2, 150, 3))):
        yield current, transform
    if max(gray.size) <= 1800:
        yield (
            gray.resize((gray.width * 2, gray.height * 2), Image.Resampling.LANCZOS),
            ((0.5, 0, left), (0, 0.5, top), (0, 0, 1)),
        )
    yield from _straightened_views(gray, transform)
