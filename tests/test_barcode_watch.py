import queue
import threading
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from parcel_dimensioner.barcode_watch import (
    BarcodeWatch,
    LabelFrame,
    eligible,
    read_presented_label,
)


def barcode(value="A", points=None):
    points = points or [[180, 100], [380, 100], [380, 180], [180, 180]]
    return {
        "status": "found",
        "candidates": [
            {
                "value": value,
                "formats": ["Code 128"],
                "positions": [{"points": points}],
                "raw_bytes_base64": [],
            }
        ],
        "selected_index": 0,
    }


def test_live_reader_maps_preview_coordinates_and_excludes_distant_background():
    full, small = SimpleNamespace(size=(4000, 2250)), SimpleNamespace(size=(1000, 562))
    frame = LabelFrame(full, small, {}, "time")
    calls = []

    def decoder(image, **kwargs):
        calls.append(image)
        return barcode(points=[[400, 220], [600, 220], [600, 320], [400, 320]])

    result = read_presented_label(frame, decoder)
    assert result["candidates"][0]["positions"][0]["points"][0] == pytest.approx(
        [1600, 220 * 2250 / 562]
    )
    assert calls == [small]
    assert not eligible(
        barcode(points=[[2000, 1200], [2080, 1200], [2080, 1240], [2000, 1240]]), (4000, 2250)
    )["candidates"]
    assert not eligible(barcode(points=[[0, 0], [500, 0], [500, 300], [0, 300]]), (4000, 2250))[
        "candidates"
    ]


def test_full_resolution_fallback_recovers_a_presented_code(tmp_path):
    image = pytest.importorskip("PIL.Image")
    zxing = pytest.importorskip("zxingcpp")
    full = image.new("RGB", (4608, 2592), "white")
    code = image.fromarray(
        zxing.create_barcode("HANDS-FREE-42", zxing.BarcodeFormat.Code128).to_image(scale=6)
    ).convert("RGB")
    full.paste(code, (1900, 1000))
    preview = image.new("RGB", (1280, 720), "white")  # Intentionally unreadable small stream.
    result = read_presented_label(LabelFrame(full, preview, {}, datetime.now(UTC).isoformat()))
    assert result["candidates"][0]["value"] == "HANDS-FREE-42"
    assert result["image_size"] == [4608, 2592]


def test_reset_discards_late_results_and_latches_only_once():
    entered, release = threading.Event(), threading.Event()

    def reader(frame):
        if frame == "old":
            entered.set()
            assert release.wait(2)
        return barcode(str(frame))

    watch = BarcodeWatch(reader)
    watch.start()
    try:
        watch.offer("old")
        assert entered.wait(2)
        epoch = watch.reset()
        watch.offer("new")
        release.set()
        actual, frame, result = watch.results.get(timeout=2)
        assert actual == epoch and frame == "new" and result["candidates"][0]["value"] == "new"
        watch.offer("replacement")
        with pytest.raises(queue.Empty):
            watch.results.get(timeout=0.2)
    finally:
        release.set()
        watch.close()


def test_clearing_allows_same_barcode_for_a_deliberate_next_box():
    watch = BarcodeWatch(lambda frame: barcode())
    watch.start()
    try:
        watch.offer("first")
        first = watch.results.get(timeout=2)
        watch.reset()
        watch.offer("second")
        second = watch.results.get(timeout=2)
        assert first[0] != second[0]
        assert first[2]["candidates"][0]["value"] == second[2]["candidates"][0]["value"]
    finally:
        watch.close()
