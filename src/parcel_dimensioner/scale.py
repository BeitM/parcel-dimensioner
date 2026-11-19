"""USB scale acquisition independent of camera/vision and Tk's event loop.

Only a live, stable window may be zeroed or attached to a capture. Every USB
reconnect, Pico restart, sensor error, or sample gap invalidates the session zero.
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime

from parcel_dimensioner.settings import ScaleSettings

PROTOCOL = "parcel-scale-v1"


class ScaleError(RuntimeError):
    """Weight is unavailable; a dimensions-only attempt requires explicit consent."""


@dataclass(frozen=True)
class WeightTicket:
    record: dict
    generation: int
    last_time: float


def select_port(ports, requested="auto"):
    if requested != "auto":
        return requested
    candidates = [p.device for p in ports if p.vid == 0x2E8A and p.pid != 0x000F]
    if not candidates:
        raise ScaleError("Scale disconnected. Connect the Pico USB data cable.")
    if len(candidates) != 1:
        raise ScaleError("Multiple Pico ports found. Set scale.port in the configuration.")
    return candidates[0]


class SerialScale(threading.Thread):
    def __init__(self, settings: ScaleSettings | None = None):
        super().__init__(daemon=True, name="scale-usb")
        self.settings = settings or ScaleSettings()
        self.stopping = threading.Event()
        self.condition = threading.Condition()
        self.samples = deque(maxlen=1024)
        self.connected = False
        self.error = "Connecting scale…"
        self.zero_raw = None
        self.zeroed_at = None
        self.generation = 0
        self.device = self.port = self.sequence = None

    def _invalidate(self, message):
        self.samples.clear()
        self.zero_raw = self.zeroed_at = None
        self.generation += 1
        self.error = message

    def disconnected(self, message):
        with self.condition:
            self.connected = False
            self.sequence = None
            self._invalidate(message)
            self.condition.notify_all()

    def consume(self, line, *, now=None):
        """Validate one firmware frame. Also used by hardware-independent tests."""
        try:
            frame = json.loads(line)
        except (ValueError, UnicodeError) as exc:
            raise ScaleError("Invalid scale data. Install the Pico scale firmware.") from exc
        if not isinstance(frame, dict) or frame.get("protocol") != PROTOCOL:
            raise ScaleError("Unexpected scale protocol. Install the Pico scale firmware.")
        device = frame.get("device")
        if not isinstance(device, str) or not device:
            raise ScaleError("Scale frame is missing its device ID.")
        with self.condition:
            self.connected = True
            if "error" in frame:
                self._invalidate(str(frame["error"]))
                self.condition.notify_all()
                return
            raw, seq = frame.get("raw"), frame.get("seq")
            if type(raw) is not int or not -8388608 < raw < 8388607:
                raise ScaleError("Invalid or saturated scale reading.")
            if type(seq) is not int or seq < 0:
                raise ScaleError("Invalid scale sequence number.")
            now = time.monotonic() if now is None else now
            restarted = self.device != device or (
                self.sequence is not None and seq <= self.sequence
            )
            gap = self.samples and now - self.samples[-1][0] > self.settings.freshness_seconds
            if restarted or gap:
                self._invalidate("Scale needs zeroing. Empty the platform and tap Zero scale.")
            elif self.sequence is not None and seq != self.sequence + 1:
                self.samples.clear()  # Missing samples cannot prove stability.
            self.device, self.sequence = device, seq
            self.error = ""
            self.samples.append((now, raw, datetime.now(UTC).isoformat()))
            self.condition.notify_all()

    def _window(self, now):
        if not self.connected or not self.samples:
            raise ScaleError(self.error or "Waiting for scale readings.")
        latest = self.samples[-1][0]
        if now - latest > self.settings.freshness_seconds:
            raise ScaleError("Scale readings stopped. Check USB and Pico firmware.")
        # Include the sample immediately preceding the requested interval.
        window = list(self.samples)
        while len(window) > 1 and window[1][0] <= latest - self.settings.stable_seconds:
            window.pop(0)
        if len(window) < 6 or latest - window[0][0] < self.settings.stable_seconds:
            raise ScaleError("Settling — keep hands off the platform.")
        spread = (max(s[1] for s in window) - min(s[1] for s in window)) / abs(
            self.settings.counts_per_pound
        )
        if spread > self.settings.stable_range_lb:
            raise ScaleError("Weight is moving — keep hands off the platform.")
        return window, spread

    def ready_for_capture(self):
        """A fresh, zeroed scale can accept a request even while weight is settling."""
        with self.condition:
            return bool(
                self.connected
                and self.zero_raw is not None
                and self.samples
                and time.monotonic() - self.samples[-1][0] <= self.settings.freshness_seconds
            )

    def snapshot(self):
        with self.condition:
            now = time.monotonic()
            pounds = None
            fresh = self.samples and now - self.samples[-1][0] <= self.settings.freshness_seconds
            if fresh and self.zero_raw is not None:
                values = [s[1] for s in list(self.samples)[-5:]]
                pounds = (
                    sum(values) / len(values) - self.zero_raw
                ) / self.settings.counts_per_pound
            try:
                self._window(now)
                if self.zero_raw is None:
                    raise ScaleError("Needs zero — empty the platform, then tap Zero scale.")
                if pounds is not None and pounds < -self.settings.stable_range_lb:
                    raise ScaleError("Below zero — empty the platform and zero the scale.")
                return {"pounds": pounds, "stable": True, "message": "Stable"}
            except ScaleError as exc:
                return {"pounds": pounds, "stable": False, "message": str(exc)}

    def _wait(self, operation, cancel):
        deadline = time.monotonic() + self.settings.timeout_seconds
        with self.condition:
            while True:
                if self.stopping.is_set() or (cancel is not None and cancel.is_set()):
                    raise ScaleError("Scale operation cancelled.")
                if not self.connected:
                    raise ScaleError(self.error or "Scale disconnected.")
                try:
                    return operation()
                except ScaleError as exc:
                    if time.monotonic() >= deadline:
                        raise ScaleError(f"Could not obtain stable weight: {exc}") from exc
                    self.condition.wait(0.1)

    def zero(self, cancel=None):
        def set_zero():
            window, _ = self._window(time.monotonic())
            self.zero_raw = sum(s[1] for s in window) / len(window)
            self.zeroed_at = datetime.now(UTC).isoformat()
            self.generation += 1
            return self.zero_raw

        return self._wait(set_zero, cancel)

    def wait_stable(self, cancel=None):
        with self.condition:
            if not self.connected:
                raise ScaleError(self.error or "Scale disconnected.")
            if self.zero_raw is None:
                raise ScaleError("Scale needs zeroing. Empty the platform and tap Zero scale.")

        def take():
            if self.zero_raw is None:
                raise ScaleError("Scale needs zeroing after reconnecting or restarting.")
            window, spread = self._window(time.monotonic())
            raw = sum(s[1] for s in window) / len(window)
            pounds = (raw - self.zero_raw) / self.settings.counts_per_pound
            if pounds < -self.settings.stable_range_lb:
                raise ScaleError("Below zero. Remove all boxes and zero the scale.")
            return WeightTicket(
                {
                    "status": "measured",
                    "pounds": pounds,
                    "unit": "lb",
                    "stable": True,
                    "raw_counts": raw,
                    "zero_raw_counts": self.zero_raw,
                    "zeroed_at": self.zeroed_at,
                    "sampled_at": window[-1][2],
                    "sample_count": len(window),
                    "stable_window_seconds": window[-1][0] - window[0][0],
                    "range_lb": spread,
                    "stable_range_limit_lb": self.settings.stable_range_lb,
                    "freshness_limit_seconds": self.settings.freshness_seconds,
                    "counts_per_pound": self.settings.counts_per_pound,
                    "calibration_id": self.settings.calibration_id,
                    "device_id": self.device,
                    "port": self.port,
                    "firmware_protocol": PROTOCOL,
                },
                self.generation,
                window[-1][0],
            )

        return self._wait(take, cancel)

    def verify_capture(self, ticket, cancel=None):
        """Require a fresh sample after capture and no movement across the exposure."""
        end = time.monotonic()
        deadline = end + self.settings.freshness_seconds + 0.5
        with self.condition:
            while not self.samples or self.samples[-1][0] < end:
                self._check_ticket(ticket, cancel)
                if time.monotonic() >= deadline:
                    raise ScaleError("No fresh scale reading after capture. Retry the scan.")
                self.condition.wait(0.05)
            self._check_ticket(ticket, cancel)
            if self.samples[0][0] > ticket.last_time:
                raise ScaleError("Missing scale readings during capture. Retry the scan.")
            values = [s[1] for s in self.samples if s[0] >= ticket.last_time]
            values.append(ticket.record["raw_counts"])
            spread = (max(values) - min(values)) / abs(self.settings.counts_per_pound)
            if spread > self.settings.stable_range_lb:
                raise ScaleError("Weight changed during capture. Keep the box still and retry.")
            return {
                **ticket.record,
                "capture_verified_at": datetime.now(UTC).isoformat(),
                "capture_range_lb": spread,
            }

    def _check_ticket(self, ticket, cancel):
        if self.stopping.is_set() or (cancel is not None and cancel.is_set()):
            raise ScaleError("Scale operation cancelled.")
        if not self.connected or self.generation != ticket.generation:
            raise ScaleError("Scale reset or disconnected during capture. Zero it and retry.")
        if (
            not self.samples
            or time.monotonic() - self.samples[-1][0] > self.settings.freshness_seconds
        ):
            raise ScaleError("Scale readings stopped during capture.")

    def run(self):
        try:
            import serial
            from serial.tools.list_ports import comports
        except ImportError:
            self.disconnected("Scale support missing. Install this project's [touchscreen] extra.")
            return
        while not self.stopping.is_set():
            try:
                port = select_port(comports(), self.settings.port)
                options = {"exclusive": True} if os.name == "posix" else {}
                with serial.Serial(
                    port, 115200, timeout=0.2, write_timeout=0.2, **options
                ) as stream:
                    stream.reset_input_buffer()
                    self.port = port
                    buffer = b""
                    last = time.monotonic()
                    while not self.stopping.is_set():
                        chunk = stream.read_until(b"\n", size=512)
                        buffer += chunk
                        if len(buffer) > 1024:
                            raise ScaleError("Invalid scale stream. Install the Pico firmware.")
                        if buffer.endswith(b"\n"):
                            self.consume(buffer)
                            buffer = b""
                            last = time.monotonic()
                        if time.monotonic() - last > 3:
                            raise ScaleError(
                                "No scale data. Install/start the Pico scale firmware."
                            )
            except (OSError, ValueError, ScaleError, serial.SerialException) as exc:
                self.disconnected(str(exc))
                self.stopping.wait(1)
        self.disconnected("Scale closed.")

    def close(self):
        self.stopping.set()
        with self.condition:
            self.condition.notify_all()
        if self.is_alive():
            self.join(timeout=2)
