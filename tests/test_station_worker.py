import queue
import time
from types import SimpleNamespace

import pytest

from parcel_dimensioner.barcode_watch import BarcodeWatch
from parcel_dimensioner.settings import CameraSettings
from parcel_dimensioner.station_worker import StationWorker


@pytest.mark.parametrize("pause_during_decode", [False, True])
def test_live_id_is_held_focus_restored_and_next_box_is_deliberate(
    tmp_path, monkeypatch, pause_during_decode
):
    import threading

    from parcel_dimensioner import station_worker

    state = SimpleNamespace(focus=False, starts=0, measured=[])
    code = {
        "status": "found",
        "selected_index": 0,
        "candidates": [{"value": "A", "formats": ["Code 128"], "positions": []}],
    }
    image = pytest.importorskip("PIL.Image")

    class Camera:
        def __init__(self, *args):
            pass

        def start(self):
            pass

        def close(self):
            state.focus = False

        def start_label_focus(self):
            state.focus = True
            state.starts += 1

        def stop_label_focus(self):
            state.focus = False

        def label_frame(self, **kwargs):
            return SimpleNamespace(preview=image.new("RGB", (1280, 720), "white"))

        def preview(self):
            return image.new("RGB", (800, 450), "white")

    class Scale:
        def __init__(self, *args):
            pass

        def start(self):
            pass

        def close(self):
            pass

        def zero(self, *args):
            assert not state.focus

    class Session:
        def __init__(self, *args, **kwargs):
            self.directory = tmp_path
            self.reference = None
            self.pending_label = None
            self.last_scan = None
            self.count = 0
            self.review_warning = ""

        def capture_empty(self):
            assert not state.focus
            self.reference = True

        def clear_label(self):
            self.pending_label = None

        def accept_live_label(self, frame, barcode):
            self.count += 1
            result = {"scan_id": f"label-{self.count}", "package_id": "A", "barcode": barcode}
            self.pending_label = (tmp_path, result)
            return result

        def scan(self, **kwargs):
            assert not state.focus
            state.measured.append(self.pending_label[1]["scan_id"])
            self.pending_label = None
            return ({}, tmp_path / "marked.jpg")

    monkeypatch.setattr(station_worker, "StationCamera", Camera)
    monkeypatch.setattr(station_worker, "ScanSession", Session)
    monkeypatch.setattr(station_worker, "SerialScale", Scale)
    entered, release = threading.Event(), threading.Event()
    if not pause_during_decode:
        release.set()

    def reader(frame):
        entered.set()
        assert release.wait(2)
        return code

    monkeypatch.setattr(station_worker, "BarcodeWatch", lambda: BarcodeWatch(reader=reader))
    worker = StationWorker(CameraSettings(), tmp_path)

    def event(name, predicate=lambda payload: True):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            kind, payload = worker.events.get(timeout=1)
            assert kind != "fatal", payload
            if kind == name and predicate(payload):
                return payload
        raise AssertionError(f"No {name} event")

    worker.start()
    try:
        event("ready")
        worker.commands.put("empty")
        if pause_during_decode:
            assert entered.wait(2)
            worker.paused.set()
            event("listening", lambda payload: payload is False)
            release.set()
            with pytest.raises(queue.Empty):
                worker.events.get(timeout=0.2)
            assert not state.focus
            worker.paused.clear()
        label = event("label")
        assert label["scan_id"] == "label-1" and not state.focus
        worker.commands.put("scan")
        event("result")
        assert state.measured == ["label-1"]
        starts = state.starts
        with pytest.raises(queue.Empty):
            worker.events.get(timeout=0.3)
        assert state.starts == starts  # Result display never rearms on its own.
        worker.commands.put("next")
        label = event("label")
        assert label["scan_id"] == "label-2"
        worker.paused.set()
        assert not state.focus
    finally:
        release.set()
        worker.stopping.set()
        worker.join(timeout=4)
        assert not worker.is_alive()
