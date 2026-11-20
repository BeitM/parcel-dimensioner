import queue
import time
from types import SimpleNamespace

import pytest

from parcel_dimensioner.barcode_watch import BarcodeWatch
from parcel_dimensioner.measurement_store import ApiError
from parcel_dimensioner.scale import ScaleError
from parcel_dimensioner.settings import CameraSettings
from parcel_dimensioner.station import ScanSession
from parcel_dimensioner.station_worker import StationWorker


@pytest.mark.parametrize("pause_during_decode", [False, True])
def test_live_id_is_held_focus_restored_and_next_box_is_deliberate(
    tmp_path, monkeypatch, pause_during_decode
):
    import threading

    from parcel_dimensioner import station_worker

    state = SimpleNamespace(focus=False, starts=0, measured=[], cleanup_runs=0)
    periodic_cleanup = threading.Event()
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

        def prune_photos(self):
            state.cleanup_runs += 1
            if state.cleanup_runs == 2:
                periodic_cleanup.set()

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
    monkeypatch.setattr(station_worker, "PHOTO_CLEANUP_INTERVAL_SECONDS", 0.05)
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
        assert state.cleanup_runs >= 1
        assert periodic_cleanup.wait(2)
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


def test_requested_scan_uses_station_capture_and_requires_weight(tmp_path, monkeypatch):
    from parcel_dimensioner import station_worker

    image = pytest.importorskip("PIL.Image")
    scale_available = False

    class Camera:
        def __init__(self, settings):
            self.frame = image.new("RGB", (100, 60), "white")

        def start(self):
            pass

        def close(self):
            pass

        def start_label_focus(self):
            pass

        def stop_label_focus(self):
            pass

        def preview(self):
            return self.frame

        def label_frame(self, **kwargs):
            return SimpleNamespace(preview=self.frame)

        def capture(self, path):
            self.frame.save(path)
            return {"ScalerCrop": [0, 0, 100, 60]}

    class Scale:
        def __init__(self, settings):
            self.zeroed = False

        def start(self):
            pass

        def close(self):
            pass

        def zero(self, cancel):
            self.zeroed = True

        def ready_for_capture(self):
            return self.zeroed

        def wait_stable(self, cancel):
            if not scale_available:
                raise ScaleError("Unstable weight.")
            return object()

        def verify_capture(self, ticket, cancel):
            return {"pounds": 8.5, "stable": True}

    def measure(empty, box, review):
        assert empty.is_file() and box.is_file()
        return {"dimensions_inches": {"length": 12.5, "width": 8, "height": 10}}

    def session(*args, **kwargs):
        return ScanSession(
            *args,
            **kwargs,
            validator=lambda _: None,
            measure=measure,
            reviewer=lambda folder, _: folder / "box.jpg",
        )

    monkeypatch.setattr(station_worker, "StationCamera", Camera)
    monkeypatch.setattr(station_worker, "ScanSession", session)
    monkeypatch.setattr(station_worker, "SerialScale", Scale)
    monkeypatch.setattr(
        station_worker,
        "BarcodeWatch",
        lambda: BarcodeWatch(reader=lambda _: {"status": "not_found"}),
    )
    token_file = tmp_path / "token"
    token_file.write_text("t" * 32, encoding="utf-8")
    settings = SimpleNamespace(
        enabled=True,
        device_id="cart",
        host="127.0.0.1",
        port=0,
        token_file=token_file,
        tls_cert_file="",
        tls_key_file="",
    )
    worker = StationWorker(CameraSettings(), tmp_path, api_settings=settings)

    def event(name):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            kind, payload = worker.events.get(timeout=1)
            assert kind != "fatal", payload
            if kind == name:
                return payload
        raise AssertionError(f"No {name} event")

    worker.start()
    try:
        event("ready")
        store = worker.measurements
        with pytest.raises(ApiError) as err:
            store.create_request("incoming", 120)
        assert err.value.status == 503
        worker.commands.put("empty")
        event("empty")
        event("listening")

        store.create_request("incoming", 120)
        assert event("capture_request") == "incoming"
        worker.commands.put("skip")
        event("skip")
        worker.commands.put(worker.begin_measurement("incoming", None))
        assert event("weight_error")[0] == "Unstable weight."
        assert store.request("incoming")["state"] == "pending"

        worker.commands.put(worker.begin_measurement("incoming", "Unstable weight."))
        result, _ = event("result")
        assert result["scan_mode"] == "dimensions_only"
        assert store.history(None, 100)["items"] == []
        assert store.request("incoming")["state"] == "pending"

        scale_available = True
        worker.commands.put(worker.begin_measurement("incoming", None))
        result, _ = event("result")
        completed = store.request("incoming")
        assert completed["state"] == "completed"
        assert completed["measurementId"] == result["scan_id"]
        measurement = store.measurement(completed["measurementId"])
        assert measurement["imageUrl"].endswith(f"/{completed['measurementId']}/image")
        with store.open_image(completed["measurementId"]) as photo:
            assert photo.read(2) == b"\xff\xd8"
        assert measurement["barcodeStatus"] == "skipped"
        assert measurement["weight"]["value"] == 8.5
        assert measurement["dimensions"]["height"] == 10
        assert event("capture_request") is None
    finally:
        worker.stopping.set()
        worker.join(timeout=4)
        assert not worker.is_alive()
