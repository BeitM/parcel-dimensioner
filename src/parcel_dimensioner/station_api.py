"""Authenticated HTTP endpoints for a running scanner."""

from __future__ import annotations

import hmac
import json
import re
import sqlite3
import ssl
import subprocess
import threading
from contextlib import suppress
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from parcel_dimensioner.measurement_store import REQUEST_ID, ApiError

_REQUEST = re.compile(r"/api/v1/capture-requests/([^/]+)(/cancel)?")
_MEASUREMENT = re.compile(r"/api/v1/measurements/([a-fA-F0-9-]{32,36})")
_MAX_BODY_BYTES = 4096


def _invalid(message):
    return ApiError(400, "invalid_input", message)


def _clock_synchronized():
    try:
        result = subprocess.run(
            ["timedatectl", "show", "--property=NTPSynchronized", "--value"],
            capture_output=True,
            text=True,
            timeout=1,
            check=False,
        )
        return result.returncode == 0 and result.stdout.strip() == "yes"
    except (OSError, subprocess.TimeoutExpired):
        return False


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        # Headers and URLs can contain secrets or identifiers. Keep HTTP access logs off.
        pass

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")

    def send_error(self, code, message=None, explain=None):
        self._send(code, {"error": {"code": "http_error", "message": self.responses[code][0]}})

    def _send(self, status, data):
        body = json.dumps(data, allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        if status == 401:
            self.send_header("WWW-Authenticate", "Bearer")
        self.end_headers()
        self.wfile.write(body)

    def _handle(self, method):
        try:
            headers = self.headers.get_all("Authorization", [])
            if len(headers) != 1 or not hmac.compare_digest(
                headers[0].encode("utf-8"), self.server.authorization
            ):
                raise ApiError(401, "unauthorized", "A valid bearer token is required.")
            status, payload = self._dispatch(method)
        except ApiError as exc:
            status, payload = exc.status, exc.payload
        except (sqlite3.Error, OSError):
            exc = ApiError(
                503, "device_unavailable", "The station could not complete this request."
            )
            status, payload = exc.status, exc.payload
        # Client can replay a request if it loses the reply.
        with suppress(OSError):
            self._send(status, payload)

    def _json_body(self):
        sizes = self.headers.get_all("Content-Length", [])
        if (
            self.headers.get("Transfer-Encoding")
            or len(sizes) != 1
            or re.fullmatch(r"[0-9]{1,4}", sizes[0]) is None
            or not 0 < int(sizes[0]) <= _MAX_BODY_BYTES
        ):
            raise _invalid("Supply a JSON request body up to 4096 bytes.")
        if self.headers.get_content_type() != "application/json":
            raise _invalid("Set Content-Type to application/json.")
        try:
            return json.loads(self.rfile.read(int(sizes[0])))
        except (ValueError, RecursionError) as exc:
            raise _invalid("The request body must be valid JSON.") from exc

    def _dispatch(self, method):
        try:
            url = urlsplit(self.path)
            query = parse_qs(
                url.query, keep_blank_values=True, strict_parsing=True, max_num_fields=4
            )
        except ValueError as exc:
            raise _invalid("Invalid request URL.") from exc
        path = url.path
        if query and (path != "/api/v1/measurements" or method != "GET"):
            raise _invalid("This endpoint does not accept query parameters.")
        store = self.server.store
        if method == "POST" and path == "/api/v1/capture-requests":
            body = self._json_body()
            if not isinstance(body, dict) or body.keys() - {"requestId", "expiresInSeconds"}:
                raise _invalid("Supply requestId and optionally expiresInSeconds.")
            request_id, ttl = body.get("requestId"), body.get("expiresInSeconds", 120)
            if not isinstance(request_id, str) or not REQUEST_ID.fullmatch(request_id):
                raise _invalid(
                    "requestId must use 1 to 128 letters, numbers, hyphens or underscores."
                )
            if type(ttl) is not int or not 10 <= ttl <= 600:
                raise _invalid("expiresInSeconds must be an integer from 10 to 600.")
            return store.create_request(request_id, ttl)
        if request := _REQUEST.fullmatch(path):
            request_id, cancel = request.groups()
            if not REQUEST_ID.fullmatch(request_id):
                raise _invalid("Invalid requestId.")
            if (cancel and method == "POST") or (not cancel and method == "GET"):
                return 200, store.request(request_id, cancel=bool(cancel))
        if method == "GET" and path == "/api/v1/measurements":
            if query.keys() - {"after", "limit"} or any(
                len(values) != 1 for values in query.values()
            ):
                raise _invalid("Supply at most one after cursor and one limit.")
            limit = query.get("limit", ["100"])[0]
            if not re.fullmatch(r"[0-9]{1,3}", limit) or not 1 <= int(limit) <= 500:
                raise _invalid("limit must be an integer from 1 to 500.")
            return 200, store.history(query.get("after", [None])[0], int(limit))
        if method == "GET" and (measurement := _MEASUREMENT.fullmatch(path)):
            return 200, store.measurement(measurement[1])
        if method == "GET" and path == "/api/v1/health":
            return 200, {
                **store.health(),
                "apiVersion": 1,
                "softwareVersion": self.server.software_version,
                "clockSynchronized": _clock_synchronized(),
                "pendingDeliveryCount": 0,
            }
        raise ApiError(404, "not_found", "API endpoint not found.")


class _Server(ThreadingHTTPServer):
    def __init__(self, address, store, token, tls):
        self.store = store
        self.authorization = f"Bearer {token}".encode("ascii")
        self.tls = tls
        try:
            self.software_version = version("parcel-dimensioner")
        except PackageNotFoundError:
            self.software_version = "development"
        super().__init__(address, _Handler)

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(5)
        if self.tls is not None:
            try:
                connection = self.tls.wrap_socket(
                    connection, server_side=True, do_handshake_on_connect=False
                )
            except OSError:
                connection.close()
                raise
        return connection, address


class StationApiServer:
    def __init__(self, settings, store):
        token = Path(settings.token_file).expanduser().read_text(encoding="utf-8").strip()
        if not re.fullmatch(r"[A-Za-z0-9._~+/=-]{32,512}", token):
            raise ValueError(
                "The API token file must contain a bearer token of at least 32 characters"
            )
        tls = None
        if settings.tls_cert_file:
            tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            tls.minimum_version = ssl.TLSVersion.TLSv1_2
            tls.load_cert_chain(
                Path(settings.tls_cert_file).expanduser(), Path(settings.tls_key_file).expanduser()
            )
        if settings.host != "127.0.0.1" and tls is None:
            raise ValueError("API connections outside 127.0.0.1 require HTTPS")
        self.server = _Server((settings.host, settings.port), store, token, tls)
        self._thread = threading.Thread(
            target=self.server.serve_forever,
            kwargs={"poll_interval": 0.2},
            daemon=True,
            name="station-api",
        )

    def start(self):
        self._thread.start()

    def close(self):
        if self._thread.is_alive():
            self.server.shutdown()
            self._thread.join()
        self.server.server_close()
