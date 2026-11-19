"""Durable reservations and immutable measurements for the station API."""

from __future__ import annotations

import json
import math
import re
import shutil
import sqlite3
import threading
import time
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

REQUEST_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")
_CURSOR = re.compile(r"([a-f0-9]{32}):(0|[1-9][0-9]{0,18})")
_MIN_FREE_BYTES = 32 * 1024 * 1024


class ApiError(Exception):
    def __init__(self, status, code, message, **details):
        super().__init__(message)
        self.status = status
        self.payload = {"error": {"code": code, "message": message, **details}}


@dataclass(frozen=True)
class CaptureAttempt:
    request_id: str | None


def _utc(timestamp):
    return (
        datetime.fromtimestamp(timestamp, UTC)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def _request_record(row):
    record = {
        "requestId": row["id"],
        "state": row["state"],
        "createdAt": _utc(row["created"]),
        "expiresAt": _utc(row["expires"]),
    }
    if row["measurement_id"]:
        record["measurementId"] = row["measurement_id"]
    if row["reason"]:
        record["reason"] = row["reason"]
    return record


def _make_measurement(result, device_id, request_id, captured_at):
    weight = result["weight"]
    dimensions = {key: result["dimensions_inches"][key] for key in ("length", "width", "height")}
    numbers = (*dimensions.values(), weight.get("pounds"))
    if weight.get("stable") is not True or any(
        type(value) not in (int, float) or not math.isfinite(value) or value <= 0
        for value in numbers
    ):
        raise ApiError(
            409, "invalid_measurement", "Measure with positive dimensions and stable weight."
        )
    record = {
        "schemaVersion": 1,
        "measurementId": result["scan_id"],
        "deviceId": device_id,
        "capturedAt": _utc(captured_at.timestamp()),
        "status": "complete",
        "dimensions": {**dimensions, "unit": "in"},
        "weight": {"value": weight["pounds"], "unit": "lb", "basis": "gross"},
    }
    if request_id is not None:
        record["requestId"] = request_id
    barcode = result["barcode"]
    selected = barcode.get("selected_index")
    if selected is not None and result.get("package_id_source") in {"barcode", "selected_barcode"}:
        candidate = barcode["candidates"][selected]
        record["barcodeStatus"] = "read"
        record["barcode"] = {
            "value": candidate["value"],
            "symbology": next(iter(candidate.get("formats", [])), "unknown"),
        }
    elif barcode["status"] == "skipped" or barcode.get("region") == "manual":
        record["barcodeStatus"] = "skipped"
    elif barcode["status"] == "not_found":
        record["barcodeStatus"] = "not_detected"
    elif barcode["status"] == "multiple" and result.get("package_id_source") != "manual":
        raise ApiError(
            409, "barcode_selection_required", "Choose a barcode or skip it, then retry."
        )
    else:
        record["barcodeStatus"] = "unreadable"
    return record


class MeasurementStore:
    def __init__(self, path: Path, device_id: str, *, hardware_ready, clock=time.time):
        self.path = path
        self.device_id = device_id
        self._hardware_ready = hardware_ready
        self._clock = clock
        self._lock = threading.RLock()
        self._attempt: CaptureAttempt | None = None
        self._reference_ready = False
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._transaction() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS station (
                singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                device_id TEXT NOT NULL, generation TEXT NOT NULL)""")
            db.execute("""CREATE TABLE IF NOT EXISTS measurements (
                position INTEGER PRIMARY KEY AUTOINCREMENT,
                id TEXT UNIQUE NOT NULL, payload TEXT NOT NULL)""")
            db.execute("""CREATE TABLE IF NOT EXISTS requests (
                id TEXT PRIMARY KEY, ttl INTEGER NOT NULL, created REAL NOT NULL,
                expires REAL NOT NULL, state TEXT NOT NULL, measurement_id TEXT, reason TEXT)""")
            db.execute("""CREATE UNIQUE INDEX IF NOT EXISTS one_active_request
                ON requests ((1)) WHERE state IN ('pending', 'capturing')""")
            db.execute("INSERT OR IGNORE INTO station VALUES (1, ?, ?)", (device_id, uuid4().hex))
            station = db.execute("SELECT * FROM station WHERE singleton = 1").fetchone()
            if station["device_id"] != device_id:
                raise ValueError(
                    "api.device_id differs from the saved measurement history's device ID"
                )
            self._generation = station["generation"]
            self._interrupt_requests(db, "station_restarted")

    @contextmanager
    def _transaction(self):
        with self._lock, closing(sqlite3.connect(self.path, timeout=2)) as db, db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA synchronous = FULL")
            db.execute("BEGIN IMMEDIATE")
            yield db

    @staticmethod
    def _interrupt_requests(db, reason):
        db.execute(
            "UPDATE requests SET state='interrupted', reason=? "
            "WHERE state IN ('pending','capturing')",
            (reason,),
        )

    def stop(self):
        with self._transaction() as db:
            self._reference_ready = False
            self._interrupt_requests(db, "station_stopped")

    def set_reference_ready(self, ready):
        with self._lock:
            self._reference_ready = ready

    def _storage_available(self):
        try:
            return shutil.disk_usage(self.path.parent).free >= _MIN_FREE_BYTES
        except OSError:
            return False

    def _expire(self, db, now):
        db.execute(
            "UPDATE requests SET state='expired' WHERE state='pending' AND expires<=?", (now,)
        )

    @staticmethod
    def _active(db):
        return db.execute(
            "SELECT * FROM requests WHERE state IN ('pending','capturing')"
        ).fetchone()

    def create_request(self, request_id, ttl):
        with self._transaction() as db:
            now = self._clock()
            self._expire(db, now)
            current = db.execute("SELECT * FROM requests WHERE id=?", (request_id,)).fetchone()
            if current is not None:
                if current["ttl"] != ttl:
                    raise ApiError(
                        409,
                        "request_id_conflict",
                        "This request ID was used with a different expiry.",
                    )
                return 200, _request_record(current)
            if self._attempt is not None or self._active(db) is not None:
                raise ApiError(
                    409, "device_busy", "The station already has an active request or capture."
                )
            if (
                not self._storage_available()
                or not self._reference_ready
                or not self._hardware_ready()
            ):
                raise ApiError(
                    503,
                    "device_unavailable",
                    "The station needs a ready camera, zeroed scale and free storage.",
                )
            db.execute(
                "INSERT INTO requests (id, ttl, created, expires, state) "
                "VALUES (?, ?, ?, ?, 'pending')",
                (request_id, ttl, now, now + ttl),
            )
            return 201, _request_record(self._active(db))

    def request(self, request_id, *, cancel=False):
        deferred_error = None
        with self._transaction() as db:
            self._expire(db, self._clock())
            row = db.execute("SELECT * FROM requests WHERE id=?", (request_id,)).fetchone()
            if row is None:
                raise ApiError(404, "not_found", "Capture request not found.")
            record = _request_record(row)
            if cancel and row["state"] == "pending":
                db.execute("UPDATE requests SET state='cancelled' WHERE id=?", (request_id,))
                record["state"] = "cancelled"
            elif cancel and row["state"] != "cancelled":
                deferred_error = ApiError(
                    409,
                    "request_state_conflict",
                    "Only a pending request can be cancelled.",
                    state=row["state"],
                )
        if deferred_error is not None:
            raise deferred_error
        return record

    def active_request_id(self):
        with self._transaction() as db:
            self._expire(db, self._clock())
            row = self._active(db)
            return row["id"] if row else None

    def begin(self, displayed_request_id):
        # A new request must be visible to the operator before it can claim a tap.
        with self._lock:
            if self._attempt is not None:
                raise ApiError(409, "device_busy", "A measurement is already running.")
            if not self._storage_available():
                raise ApiError(503, "storage_unavailable", "Free space on the Pi before measuring.")
            with self._transaction() as db:
                self._expire(db, self._clock())
                row = self._active(db)
                request_id = row["id"] if row else None
                if request_id == displayed_request_id and request_id is not None:
                    db.execute("UPDATE requests SET state='capturing' WHERE id=?", (request_id,))
            if request_id != displayed_request_id:
                raise ApiError(
                    409,
                    "request_changed",
                    "Capture request changed. Check the screen and tap Measure again.",
                )
            self._attempt = CaptureAttempt(request_id)
            return self._attempt

    def complete(self, attempt, result, captured_at):
        record = _make_measurement(result, self.device_id, attempt.request_id, captured_at)
        with self._lock:
            self._require_attempt(attempt)
            with self._transaction() as db:
                db.execute(
                    "INSERT INTO measurements (id, payload) VALUES (?, ?)",
                    (record["measurementId"], json.dumps(record, allow_nan=False)),
                )
                if attempt.request_id is not None:
                    updated = db.execute(
                        """UPDATE requests SET state='completed', measurement_id=?
                        WHERE id=? AND state='capturing'""",
                        (record["measurementId"], attempt.request_id),
                    )
                    if updated.rowcount != 1:
                        raise RuntimeError("Capture request is no longer active")
            self._attempt = None

    def release(self, attempt):
        with self._lock:
            if self._attempt is None:
                return  # A successful commit already released the attempt.
            self._require_attempt(attempt)
            with self._transaction() as db:
                if attempt.request_id is not None:
                    db.execute(
                        """UPDATE requests
                        SET state=CASE WHEN expires<=? THEN 'expired' ELSE 'pending' END
                        WHERE id=? AND state='capturing'""",
                        (self._clock(), attempt.request_id),
                    )
            self._attempt = None

    def _require_attempt(self, attempt):
        if self._attempt is not attempt:
            raise RuntimeError("Capture attempt is no longer active")

    def measurement(self, measurement_id):
        with self._transaction() as db:
            row = db.execute(
                "SELECT payload FROM measurements WHERE id=?", (measurement_id,)
            ).fetchone()
            if row is None:
                raise ApiError(404, "not_found", "Measurement not found.")
            return json.loads(row["payload"])

    def history(self, after, limit):
        position = 0
        with self._transaction() as db:
            if after is not None:
                match = _CURSOR.fullmatch(after)
                if not match:
                    raise ApiError(400, "invalid_cursor", "The history cursor is malformed.")
                generation, sequence = match.groups()
                position = int(sequence)
                newest = db.execute(
                    "SELECT COALESCE(MAX(position), 0) FROM measurements"
                ).fetchone()[0]
                if generation != self._generation or position > newest:
                    raise ApiError(410, "cursor_expired", "This history position is unavailable.")
            rows = db.execute(
                "SELECT position, payload FROM measurements "
                "WHERE position>? ORDER BY position LIMIT ?",
                (position, limit + 1),
            ).fetchall()
        page = rows[:limit]
        last = page[-1]["position"] if page else position
        return {
            "items": [json.loads(row["payload"]) for row in page],
            "nextCursor": f"{self._generation}:{last}",
            "hasMore": len(rows) > limit,
        }

    def health(self):
        with self._transaction() as db:
            self._expire(db, self._clock())
            row = self._active(db)
            storage = self._storage_available()
            state = row["state"] if row else "capturing" if self._attempt is not None else "idle"
            result = {
                "deviceId": self.device_id,
                "ready": bool(
                    state == "idle" and storage and self._reference_ready and self._hardware_ready()
                ),
                "captureState": state,
                "storageAvailable": storage,
            }
            if row:
                result["activeRequestId"] = row["id"]
            return result
