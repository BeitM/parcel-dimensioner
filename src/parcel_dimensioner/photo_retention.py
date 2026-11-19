"""Expire touchscreen capture images while keeping the records and diagnostics."""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime
from pathlib import Path

PHOTO_RETENTION_SECONDS = 48 * 60 * 60
PHOTO_CLEANUP_INTERVAL_SECONDS = 10 * 60

_STATION = re.compile(r"station-[0-9]{8}T[0-9]{6}-[0-9a-f]{8}")
_EMPTY_PHOTO = re.compile(r"empty-[0-9a-f]{8}\.jpg")
_SCAN = re.compile(r"scan-[0-9]{4,}")
_LABEL = re.compile(r"label-[0-9a-f]{32}")
_LABEL_PHOTO = re.compile(r"label(?:-[0-9]{2,})?\.jpg")
_REVIEW_IMAGES = {"measurement.jpg", "scan.jpg", "scan.tmp.jpg", "change-mask.png"}
_LOG = logging.getLogger(__name__)


def _entries(directory: Path) -> list[Path]:
    try:
        return list(directory.iterdir())
    except FileNotFoundError:
        pass
    except OSError as exc:
        _LOG.warning("Could not inspect captures in %s: %s", directory, exc)
    return []


def _local_directory(path: Path) -> bool:
    # Don't follow links or junctions inside the configured captures directory.
    try:
        return not path.is_symlink() and path.is_dir() and path.resolve() == path
    except OSError as exc:
        _LOG.warning("Could not check capture directory %s: %s", path, exc)
        return False


def _captured_at(sidecar: Path) -> float | None:
    try:
        if sidecar.is_symlink():
            return None
        metadata = json.loads(sidecar.read_text(encoding="utf-8"))
        captured = datetime.fromisoformat(metadata["captured_at"])
        if captured.utcoffset() is not None:
            return captured.timestamp()
    except (OSError, ValueError, TypeError, KeyError, OverflowError):
        pass  # Interrupted and older captures may lack a usable sidecar.
    return None


def _expire_photo(path: Path, cutoff: float, captured_at: float | None) -> None:
    try:
        if path.is_symlink() or not path.is_file():
            return
        age_origin = path.stat().st_mtime if captured_at is None else captured_at
        if age_origin <= cutoff:
            path.unlink(missing_ok=True)
    except OSError as exc:
        _LOG.warning("Could not remove expired capture %s: %s", path, exc)


def prune_station_photos(root: Path, *, protected: tuple[Path, ...] = ()) -> None:
    """Remove app-created images past retention, including those in previous sessions."""
    try:
        root = root.resolve()
        in_use = {path.resolve() for path in protected}
    except OSError as exc:
        _LOG.warning("Could not resolve captures for cleanup: %s", exc)
        return
    cutoff = time.time() - PHOTO_RETENTION_SECONDS
    for station in _entries(root):
        if not _STATION.fullmatch(station.name) or not _local_directory(station):
            continue
        for entry in _entries(station):
            if entry in in_use:
                continue
            if _EMPTY_PHOTO.fullmatch(entry.name):
                _expire_photo(entry, cutoff, _captured_at(entry.with_suffix(".json")))
            elif _SCAN.fullmatch(entry.name) and _local_directory(entry):
                _prune_scan(entry, cutoff)
            elif _LABEL.fullmatch(entry.name) and _local_directory(entry):
                _prune_labels(entry, cutoff)


def _prune_labels(folder: Path, cutoff: float) -> None:
    for photo in _entries(folder):
        if _LABEL_PHOTO.fullmatch(photo.name):
            _expire_photo(photo, cutoff, _captured_at(photo.with_suffix(".json")))


def _prune_scan(folder: Path, cutoff: float) -> None:
    _prune_labels(folder, cutoff)
    review = folder / "review"
    review_images = (
        [path for path in _entries(review) if path.name in _REVIEW_IMAGES]
        if _local_directory(review)
        else []
    )
    box = folder / "box.jpg"
    if box.is_file() or review_images:
        captured_at = _captured_at(box.with_suffix(".json"))
        for image in (box, *review_images):
            _expire_photo(image, cutoff, captured_at)
