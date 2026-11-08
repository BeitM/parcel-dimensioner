import json
from types import SimpleNamespace

import pytest

from parcel_dimensioner import scale
from parcel_dimensioner.scale import PROTOCOL, ScaleError, SerialScale, select_port
from parcel_dimensioner.settings import ScaleSettings


@pytest.fixture
def rig(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(scale, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    monitor = SerialScale(ScaleSettings(timeout_seconds=1.1))
    sequence = [0]

    def feed(raw=-400000, count=15, device="test-pico"):
        for _ in range(count):
            clock[0] += 0.1
            monitor.consume(
                json.dumps(
                    {"protocol": PROTOCOL, "device": device, "seq": sequence[0], "raw": raw}
                ),
                now=clock[0],
            )
            sequence[0] += 1

    return monitor, clock, feed


def test_zero_calibration_stability_and_frozen_capture(rig):
    monitor, _, feed = rig
    feed()
    assert not monitor.snapshot()["stable"]  # A restart must not silently tare a parcel.
    monitor.zero()
    feed(-453200)
    assert monitor.snapshot()["stable"]
    ticket = monitor.wait_stable()
    assert ticket.record["pounds"] == pytest.approx(5)
    feed(-453200, count=1)
    saved = monitor.verify_capture(ticket)
    feed(-400000)
    assert saved["pounds"] == pytest.approx(5)
    assert monitor.snapshot()["pounds"] == pytest.approx(0)


def test_movement_during_capture_rejected(rig):
    monitor, _, feed = rig
    feed()
    monitor.zero()
    feed(-453200)
    ticket = monitor.wait_stable()
    feed(-400000, count=1)
    with pytest.raises(ScaleError, match="changed during capture"):
        monitor.verify_capture(ticket)


def test_missing_samples_during_capture_rejected(rig):
    monitor, clock, feed = rig
    feed()
    monitor.zero()
    ticket = monitor.wait_stable()
    clock[0] += 0.1
    monitor.consume(
        json.dumps({"protocol": PROTOCOL, "device": "test-pico", "seq": 999, "raw": -400000}),
        now=clock[0],
    )
    with pytest.raises(ScaleError, match="Missing scale readings"):
        monitor.verify_capture(ticket)


def test_disconnect_restart_gap_and_device_change_require_new_zero(rig):
    monitor, clock, feed = rig
    feed()
    monitor.zero()
    ticket = monitor.wait_stable()
    monitor.disconnected("USB unplugged")
    assert monitor.zero_raw is None
    assert monitor.snapshot()["pounds"] is None
    with pytest.raises(ScaleError, match="disconnected"):
        monitor.verify_capture(ticket)
    feed()
    monitor.zero()
    clock[0] += 1
    feed(count=1)
    assert monitor.zero_raw is None
    feed()
    monitor.zero()
    feed(device="different-pico")
    assert monitor.zero_raw is None
    monitor.zero()
    monitor.consume(
        json.dumps({"protocol": PROTOCOL, "device": "different-pico", "seq": 0, "raw": -400000}),
        now=clock[0],
    )
    assert monitor.zero_raw is None


def test_stale_window_motion_error_and_missing_samples(rig):
    monitor, clock, feed = rig
    feed()
    monitor.zero()
    clock[0] += 0.6
    assert monitor.snapshot()["pounds"] is None
    assert "stopped" in monitor.snapshot()["message"]
    feed()
    monitor.zero()
    feed(-450000, count=1)
    assert not monitor.snapshot()["stable"]
    monitor.consume(
        json.dumps({"protocol": PROTOCOL, "device": "test-pico", "seq": 999, "raw": -450000}),
        now=clock[0],
    )
    assert not monitor.snapshot()["stable"]
    monitor.consume(
        json.dumps({"protocol": PROTOCOL, "device": "test-pico", "error": "ADC saturated"}),
        now=clock[0],
    )
    assert monitor.zero_raw is None
    assert "saturated" in monitor.snapshot()["message"]


@pytest.mark.parametrize("raw", [True, "123", 8388607, -8388608, float("nan")])
def test_invalid_raw_never_becomes_weight(rig, raw):
    monitor, _, _ = rig
    with pytest.raises(ScaleError, match="Invalid or saturated"):
        monitor.consume(json.dumps({"protocol": PROTOCOL, "device": "test", "seq": 0, "raw": raw}))


def test_cancelled_wait_and_wrong_protocol(rig):
    import threading

    monitor, _, feed = rig
    feed()
    stop = threading.Event()
    stop.set()
    with pytest.raises(ScaleError, match="cancelled"):
        monitor.zero(stop)
    with pytest.raises(ScaleError, match="protocol"):
        monitor.consume('{"raw": 123}')


def test_wait_for_stability_has_a_deadline():
    monitor = SerialScale(ScaleSettings(stable_seconds=0.02, timeout_seconds=0.04))
    monitor.consume(json.dumps({"protocol": PROTOCOL, "device": "test", "seq": 0, "raw": 123}))
    with pytest.raises(ScaleError, match="Could not obtain stable weight"):
        monitor.zero()


def test_serial_reader_reassembles_frames_and_closes_port(monkeypatch):
    import sys

    monitor = SerialScale()
    frame = json.dumps({"protocol": PROTOCOL, "device": "test", "seq": 0, "raw": -123})
    chunks = iter([frame[:10].encode(), (frame[10:] + "\n").encode()])
    consumed = []
    consume = monitor.consume

    def track(line):
        consume(line)
        consumed.append(monitor.samples[-1][1])

    monitor.consume = track

    class Stream:
        closed = False

        def __init__(self, *args, **kwargs):
            assert args[0] == "/dev/ttyACM0"

        def __enter__(self):
            return self

        def __exit__(self, *_):
            Stream.closed = True

        def reset_input_buffer(self):
            pass

        def read_until(self, *_, **kwargs):
            try:
                return next(chunks)
            except StopIteration:
                monitor.stopping.set()
                return b""

    monkeypatch.setitem(
        sys.modules, "serial", SimpleNamespace(Serial=Stream, SerialException=OSError)
    )
    monkeypatch.setitem(sys.modules, "serial.tools", SimpleNamespace())
    port = SimpleNamespace(device="/dev/ttyACM0", vid=0x2E8A, pid=5)
    monkeypatch.setitem(
        sys.modules, "serial.tools.list_ports", SimpleNamespace(comports=lambda: [port])
    )
    monitor.run()
    assert consumed == [-123]
    assert Stream.closed and not monitor.connected


def test_select_only_unambiguous_pico_port():
    pico = SimpleNamespace(device="/dev/ttyACM1", vid=0x2E8A, pid=5)
    unrelated = SimpleNamespace(device="/dev/ttyUSB0", vid=0x1234, pid=5)
    assert select_port([unrelated, pico]) == pico.device
    assert select_port([], "/dev/serial/by-id/my-pico") == "/dev/serial/by-id/my-pico"
    with pytest.raises(ScaleError, match="disconnected"):
        select_port([unrelated])
    with pytest.raises(ScaleError, match="Multiple"):
        select_port([pico, pico])


@pytest.mark.parametrize(
    "change",
    [
        {"counts_per_pound": 0},
        {"counts_per_pound": float("nan")},
        {"stable_seconds": -1},
        {"freshness_seconds": True},
        {"port": ""},
        {"timeout_seconds": 0.5},
    ],
)
def test_invalid_scale_configuration(change):
    with pytest.raises(ValueError):
        ScaleSettings(**change)
