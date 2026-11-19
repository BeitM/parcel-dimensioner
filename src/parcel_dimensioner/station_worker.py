"""One camera owner, independent barcode decoder, and continuous USB scale."""

from __future__ import annotations

import queue
import sqlite3
import threading
import time
import traceback
from contextlib import suppress
from functools import partial

from parcel_dimensioner.barcode_watch import BarcodeWatch
from parcel_dimensioner.measurement_store import MeasurementStore
from parcel_dimensioner.photo_retention import PHOTO_CLEANUP_INTERVAL_SECONDS
from parcel_dimensioner.scale import ScaleError, SerialScale
from parcel_dimensioner.station import ScanSession, StationCamera
from parcel_dimensioner.station_api import StationApiServer


class StationWorker(threading.Thread):
    def __init__(self, settings, captures_dir, scale_settings=None, *, api_settings=None):
        super().__init__(daemon=True)
        self.settings, self.captures_dir = settings, captures_dir
        self.commands, self.events, self.frames = (
            queue.Queue(maxsize=1),
            queue.Queue(),
            queue.Queue(maxsize=1),
        )
        self.stopping, self.paused = threading.Event(), threading.Event()
        self.scale = SerialScale(scale_settings)
        self.review_warning = ""
        self.api_settings = api_settings
        self.measurements = None

    def begin_measurement(self, displayed_request_id, dimensions_only_reason):
        attempt = self.measurements.begin(displayed_request_id)
        return "scan", (attempt, dimensions_only_reason)

    def _scan(self, session, payload, skip_barcode):
        if self.measurements is None:
            return session.scan(dimensions_only_reason=payload, skip_barcode=skip_barcode)
        attempt, reason = payload
        try:
            return session.scan(
                dimensions_only_reason=reason,
                skip_barcode=skip_barcode,
                publish=partial(self.measurements.complete, attempt) if reason is None else None,
            )
        finally:
            self.measurements.release(attempt)

    def run(self):
        camera, watch = StationCamera(self.settings), BarcodeWatch()
        session = None
        api_server = None
        next_request_check = 0.0
        shown_request_id = None
        wanted, active = False, False
        skip_barcode = False
        next_frame = 0

        def suspend():
            nonlocal active
            watch.reset()  # Invalidate in-flight decodes before touching camera state.
            if active:
                camera.stop_label_focus()
                active = False
                self.events.put(("listening", False))

        try:
            watch.start()
            self.scale.start()
            camera.start()
            session = ScanSession(
                self.captures_dir, camera, self.settings, scale=self.scale, cancel=self.stopping
            )
            session.prune_photos()
            next_cleanup = time.monotonic() + PHOTO_CLEANUP_INTERVAL_SECONDS
            if self.api_settings and self.api_settings.enabled:
                self.measurements = MeasurementStore(
                    self.captures_dir / "measurement-history.sqlite3",
                    self.api_settings.device_id,
                    hardware_ready=self.scale.ready_for_capture,
                )
                api_server = StationApiServer(self.api_settings, self.measurements)
                api_server.start()
            self.events.put(("ready", str(session.directory)))
            while not self.stopping.is_set():
                if time.monotonic() >= next_cleanup:
                    session.prune_photos()
                    next_cleanup = time.monotonic() + PHOTO_CLEANUP_INTERVAL_SECONDS
                try:
                    action = self.commands.get_nowait()
                except queue.Empty:
                    action = None
                if action:
                    command, payload = action if isinstance(action, tuple) else (action, None)
                    suspend()
                    try:
                        if command == "empty":
                            if self.measurements is not None:
                                self.measurements.set_reference_ready(False)
                            skip_barcode = False
                            wanted = False
                            session.capture_empty()
                            try:
                                self.scale.zero(self.stopping)
                                note = "Empty station saved and scale zeroed."
                            except ScaleError as exc:
                                note = f"Empty station saved. Scale: {exc}"
                            wanted = True
                            self.events.put(("empty", note))
                        elif command == "zero":
                            skip_barcode = False
                            session.last_scan = None
                            session.clear_label()
                            self.scale.zero(self.stopping)
                            wanted = True
                            self.events.put(("zero", "Scale zeroed."))
                        elif command in ("next", "clear_label"):
                            skip_barcode = False
                            session.clear_label()
                            session.last_scan = None
                            wanted = True
                            self.events.put(("next", None))
                        elif command == "skip":
                            skip_barcode = True
                            session.last_scan = None
                            session.clear_label()
                            wanted = False
                            self.events.put(("skip", None))
                        elif command == "label_id":
                            self.events.put(("label", session.set_label_id(**payload)))
                        elif command == "manual_id":
                            wanted = False
                            self.events.put(("label", session.manual_label(payload)))
                        elif command == "package_id":
                            result = session.set_package_id(**payload)
                            self.review_warning = session.review_warning
                            self.events.put(("package_id", (result, session.review_path)))
                        else:
                            wanted = False
                            result = self._scan(session, payload, skip_barcode)
                            self.review_warning = session.review_warning
                            self.events.put(("result", result))
                    except ScaleError as exc:
                        event = "zero_error" if command == "zero" else "weight_error"
                        self.events.put((event, (str(exc), session.reference is not None)))
                    except Exception as exc:
                        event = (
                            "package_id_error"
                            if command in ("package_id", "label_id", "manual_id")
                            else "error"
                        )
                        self.events.put((event, (str(exc), session.reference is not None)))
                    finally:
                        if self.measurements is not None:
                            self.measurements.set_reference_ready(session.reference is not None)
                            next_request_check = 0.0
                if self.measurements is not None and time.monotonic() >= next_request_check:
                    next_request_check = time.monotonic() + 0.5
                    try:
                        request_id = self.measurements.active_request_id()
                    except sqlite3.Error:
                        pass  # The next measurement and HTTP queries surface storage errors.
                    else:
                        if request_id != shown_request_id:
                            shown_request_id = request_id
                            self.events.put(("capture_request", request_id))
                desired = (
                    wanted
                    and session.reference is not None
                    and session.pending_label is None
                    and not self.paused.is_set()
                )
                if not desired and active:
                    suspend()
                elif desired and not active:
                    watch.reset()
                    camera.start_label_focus()
                    active = True
                    next_frame = 0
                    self.events.put(("listening", True))
                if active:
                    try:
                        epoch, frame, barcode = watch.results.get_nowait()
                    except queue.Empty:
                        pass
                    else:
                        if (
                            epoch == watch.epoch
                            and not self.paused.is_set()
                            and not self.stopping.is_set()
                        ):
                            wanted = False
                            if barcode["status"] == "unavailable":
                                suspend()
                                self.events.put(
                                    (
                                        "watch_error",
                                        barcode.get("reason") or "Barcode reader unavailable.",
                                    )
                                )
                            else:
                                self.events.put(("label_detected", None))
                                result = session.accept_live_label(frame, barcode)
                                suspend()
                                self.events.put(("label", result))
                if self.stopping.is_set():
                    break
                if active:
                    now = time.monotonic()
                    offer = now >= next_frame
                    frame = camera.label_frame(include_full=offer)
                    preview = frame.preview
                    if offer:
                        watch.offer(frame)
                        next_frame = now + 0.2
                else:
                    preview = camera.preview()
                preview = preview.copy()
                preview.thumbnail((800, 450))
                with suppress(queue.Empty):
                    self.frames.get_nowait()
                self.frames.put_nowait(preview)
                self.stopping.wait(0.12)
        except Exception as exc:
            if session is not None:
                with suppress(OSError):
                    (session.directory / "camera-error.txt").write_text(
                        traceback.format_exc(), encoding="utf-8"
                    )
            self.events.put(("fatal", str(exc)))
        finally:
            if api_server is not None:
                api_server.close()
            if self.measurements is not None:
                with suppress(sqlite3.Error):
                    self.measurements.stop()
            watch.close()
            try:
                camera.close()
            finally:
                self.scale.close()
                self.events.put(("closed", None))
