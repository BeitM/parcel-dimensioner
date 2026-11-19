import json
import os
from datetime import UTC, datetime, timedelta

from parcel_dimensioner.photo_retention import prune_station_photos


def test_photo_cleanup_preserves_scan_records_and_unrelated_images(tmp_path):
    station = tmp_path / "station-20261101T120000-a1b2c3d4"
    scan = station / "scan-0001"
    review = scan / "review"
    label = station / ("label-" + "a" * 32)
    for directory in (review, label, tmp_path / "calibration-20261101"):
        directory.mkdir(parents=True, exist_ok=True)

    expired = datetime.now(UTC) - timedelta(hours=49)
    for capture in (scan / "box.json", scan / "label.json", label / "label.json"):
        capture.write_text(json.dumps({"captured_at": expired.isoformat()}), encoding="utf-8")

    old_photos = [
        scan / "box.jpg",
        scan / "label.jpg",
        review / "measurement.jpg",
        review / "scan.jpg",
        review / "scan.tmp.jpg",
        review / "change-mask.png",
        label / "label.jpg",
        label / "label-02.jpg",
        station / "empty-aabbccdd.jpg",
    ]
    for path in old_photos:
        path.write_bytes(b"image")
    # Older captures and interrupted attempts can lack capture metadata.
    for path in old_photos[-2:]:
        os.utime(path, (expired.timestamp(), expired.timestamp()))

    kept = [
        scan / "result.json",
        scan / "attempt.json",
        scan / "error.txt",
        review / "result.json",
        station / "empty-deadbeef.jpg",
        scan / "custom.jpg",
        tmp_path / "calibration-20261101" / "calibration-001.jpg",
    ]
    for path in kept:
        path.write_bytes(b"keep")
        age = expired + timedelta(hours=2) if path.name == "empty-deadbeef.jpg" else expired
        os.utime(path, (age.timestamp(), age.timestamp()))

    prune_station_photos(tmp_path)

    assert all(not path.exists() for path in old_photos)
    assert all(path.read_bytes() == b"keep" for path in kept)
    assert (scan / "box.json").is_file()
    assert (scan / "label.json").is_file()
    assert (label / "label.json").is_file()
