"""Bounded background decoding; camera requests never leave their owner thread."""

from __future__ import annotations

import math
import queue
import threading
from contextlib import suppress
from dataclasses import dataclass

from parcel_dimensioner.barcodes import _inside, barcode_result, decode_frame, unavailable

GUIDE = (0.24, 0.22, 0.76, 0.78)


@dataclass
class LabelFrame:
    image: object  # Owned full-resolution PIL copy from the SAME request as preview.
    preview: object
    metadata: dict
    captured_at: str


def guide_polygon(size):
    w, h = size
    left, top, right, bottom = GUIDE
    return [[w * x, h * y] for x, y in ((left, top), (right, top), (right, bottom), (left, bottom))]


def eligible(barcode, source_size, *, scale=(1, 1)):
    """Require a readable code in the guide, large enough to be a presented label."""
    width, _ = source_size
    outline = guide_polygon(source_size)
    candidates = []
    for candidate in barcode.get("candidates", []):
        locations = []
        for location in candidate.get("positions", []):
            points = [[x * scale[0], y * scale[1]] for x, y in location["points"]]
            center = [sum(p[i] for p in points) / 4 for i in (0, 1)]
            span = max(
                math.dist(a, b) for a, b in zip(points, [*points[1:], points[0]], strict=True)
            )
            if _inside(center, outline) and span >= width * 0.10:
                locations.append({**location, "points": points})
        if locations:
            candidates.append({**candidate, "positions": locations})
    if barcode.get("status") == "unavailable":
        return barcode
    return barcode_result(candidates, image_size=list(source_size), region="live_guide")


def read_presented_label(frame, decoder=decode_frame):
    small = decoder(frame.preview, outline=guide_polygon(frame.preview.size), quick=True)
    full_size = frame.image.size
    result = eligible(
        small,
        full_size,
        scale=tuple(a / b for a, b in zip(full_size, frame.preview.size, strict=True)),
    )
    if result.get("candidates") or result["status"] == "unavailable":
        return result
    # The decoder may recover detail absent from the small stream. Both images
    # and their metadata were copied from one completed camera request.
    return eligible(
        decoder(frame.image, outline=guide_polygon(full_size), enhance=True, quick=True), full_size
    )


class BarcodeWatch(threading.Thread):
    def __init__(self, reader=read_presented_label):
        super().__init__(daemon=True)
        self.reader = reader
        self.frames, self.results = queue.Queue(maxsize=1), queue.Queue(maxsize=1)
        self.stopping = threading.Event()
        self.epoch = 0
        self.latched = None

    def reset(self):
        self.epoch += 1
        self.latched = None
        for channel in (self.frames, self.results):
            with suppress(queue.Empty):
                channel.get_nowait()
        return self.epoch

    def offer(self, frame):
        if self.latched == self.epoch or self.stopping.is_set():
            return
        with suppress(queue.Empty):
            self.frames.get_nowait()
        self.frames.put_nowait((self.epoch, frame))

    def run(self):
        while not self.stopping.is_set():
            try:
                epoch, frame = self.frames.get(timeout=0.1)
            except queue.Empty:
                continue
            if epoch != self.epoch or self.latched == epoch:
                continue
            try:
                result = self.reader(frame)
            except Exception as exc:
                result = unavailable(f"Live barcode reading failed: {exc}")
            if epoch != self.epoch or self.stopping.is_set():
                continue
            if result.get("candidates") or result["status"] == "unavailable":
                self.latched = epoch
                with suppress(queue.Empty):
                    self.results.get_nowait()
                self.results.put_nowait((epoch, frame, result))

    def close(self):
        self.stopping.set()
        self.reset()
        if self.ident is not None:
            self.join(timeout=4)
