import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from http.client import HTTPConnection
from types import SimpleNamespace
from uuid import uuid4

import pytest

from parcel_dimensioner.measurement_store import ApiError, MeasurementStore
from parcel_dimensioner.settings import SettingsError, load_settings
from parcel_dimensioner.station_api import StationApiServer

TOKEN = "test-token-only-" + "t" * 32


def captured_box(*, skipped=False):
    return {
        "scan_id": uuid4().hex,
        "dimensions_inches": {"length": 18.25, "width": 12.5, "height": 10.0},
        "weight": {"pounds": 14.62, "stable": True},
        "package_id_source": None if skipped else "selected_barcode",
        "barcode": (
            {"status": "skipped", "selected_index": None, "candidates": []}
            if skipped
            else {
                "status": "multiple",
                "selected_index": 1,
                "candidates": [{"value": "serial"}, {"value": "0012345", "formats": ["Code 128"]}],
            }
        ),
    }


def station(db, **options):
    store = MeasurementStore(db, "cart-1", hardware_ready=lambda: True, **options)
    store.set_reference_ready(True)
    return store


@pytest.fixture
def http_station(tmp_path):
    token_file = tmp_path / "token"
    token_file.write_text(TOKEN, encoding="utf-8")
    store = station(tmp_path / "measurements.sqlite3")
    settings = SimpleNamespace(
        host="127.0.0.1", port=0, token_file=token_file, tls_cert_file="", tls_key_file=""
    )
    server = StationApiServer(settings, store)
    server.start()

    def call(method, path, body=None, *, token=TOKEN):
        headers = {"Authorization": f"Bearer {token}"} if token is not None else {}
        if body is not None:
            headers["Content-Type"] = "application/json"
        client = HTTPConnection("127.0.0.1", server.server.server_port, timeout=3)
        try:
            client.request(
                method,
                "/api/v1" + path,
                body=json.dumps(body) if body is not None else None,
                headers=headers,
            )
            response = client.getresponse()
            return response.status, json.loads(response.read())
        finally:
            client.close()

    try:
        yield store, call
    finally:
        server.close()


def test_api_request_capture_and_poll_history(http_station):
    store, call = http_station
    assert call("GET", "/health", token=None)[0] == 401
    assert call("GET", "/health", token="wrong")[0] == 401
    status, health = call("GET", "/health")
    assert status == 200
    assert health["ready"] and health["deviceId"] == "cart-1"
    assert type(health["clockSynchronized"]) is bool

    baseline = call("GET", "/measurements")[1]["nextCursor"]
    assert call("POST", "/capture-requests", {"requestId": "bad/id"})[0] == 400
    invalid_ttl = {"requestId": "id", "expiresInSeconds": True}
    assert call("POST", "/capture-requests", invalid_ttl)[0] == 400
    request = {"requestId": "req-1"}
    status, pending = call("POST", "/capture-requests", request)
    assert status == 201 and pending["state"] == "pending"
    assert call("POST", "/capture-requests", {**request, "expiresInSeconds": 120}) == (200, pending)
    _, conflict = call("POST", "/capture-requests", {**request, "expiresInSeconds": 121})
    assert conflict["error"]["code"] == "request_id_conflict"
    _, busy = call("POST", "/capture-requests", {"requestId": "other"})
    assert busy["error"]["code"] == "device_busy"
    with pytest.raises(ApiError, match="Capture request changed"):
        store.begin(None)  # Request arrived before the touchscreen displayed it.

    attempt = store.begin("req-1")
    assert call("GET", "/capture-requests/req-1")[1]["state"] == "capturing"
    assert call("POST", "/capture-requests/req-1/cancel")[0] == 409
    result = captured_box()
    store.complete(attempt, result, datetime.now(UTC))
    completed = call("GET", "/capture-requests/req-1")[1]
    assert completed["state"] == "completed"
    assert completed["expiresAt"] == pending["expiresAt"]
    assert call("POST", "/capture-requests", request) == (200, completed)

    standalone = store.begin(None)
    assert call("POST", "/capture-requests", {"requestId": "other"})[0] == 409
    store.complete(standalone, captured_box(skipped=True), datetime.now(UTC))
    first = call("GET", f"/measurements?after={baseline}&limit=1")[1]
    (measurement,) = first["items"]
    assert first["hasMore"]
    assert measurement["requestId"] == "req-1"
    assert measurement["barcode"] == {"value": "0012345", "symbology": "Code 128"}
    assert measurement["weight"] == {"value": 14.62, "unit": "lb", "basis": "gross"}
    assert measurement["measurementId"] == completed["measurementId"]
    assert call("GET", f"/measurements/{completed['measurementId']}") == (200, measurement)
    result["barcode"]["candidates"][1]["value"] = "local-edit"
    assert call("GET", f"/measurements/{completed['measurementId']}") == (200, measurement)
    second = call("GET", f"/measurements?after={first['nextCursor']}")[1]
    (next_box,) = second["items"]
    assert "requestId" not in next_box and "barcode" not in next_box
    assert next_box["barcodeStatus"] == "skipped"
    assert call("GET", f"/measurements?after={second['nextCursor']}") == (
        200,
        {"items": [], "nextCursor": second["nextCursor"], "hasMore": False},
    )
    assert call("GET", "/measurements?after=broken")[0] == 400


def test_retry_expiry_restart_and_cursor_recovery(tmp_path):
    now = [1_800_000_000]
    db = tmp_path / "history.sqlite3"
    store = station(db, clock=lambda: now[0])
    created = store.create_request("request", 10)[1]
    bad = captured_box()
    bad["weight"]["pounds"] = 0
    attempt = store.begin("request")
    with pytest.raises(ApiError, match="positive dimensions and stable weight"):
        store.complete(attempt, bad, datetime.now(UTC))
    store.release(attempt)
    assert store.request("request") == created
    attempt = store.begin("request")
    now[0] += 11
    store.complete(attempt, captured_box(skipped=True), datetime.now(UTC))
    assert store.request("request")["state"] == "completed"
    cursor = store.history(None, 100)["nextCursor"]

    store.create_request("late", 10)
    attempt = store.begin("late")
    now[0] += 11
    store.release(attempt)
    assert store.request("late")["state"] == "expired"
    store.create_request("pending-expiry", 10)
    now[0] += 11
    with pytest.raises(ApiError, match="Capture request changed"):
        store.begin("pending-expiry")
    assert store.request("pending-expiry")["state"] == "expired"

    store.create_request("reboot", 10)
    store.begin("reboot")
    restored = station(db, clock=lambda: now[0])
    assert restored.request("reboot")["state"] == "interrupted"
    assert restored.create_request("reboot", 10)[0] == 200
    assert len(restored.history(None, 100)["items"]) == 1
    restored.complete(restored.begin(None), captured_box(), datetime.now(UTC))
    assert len(restored.history(cursor, 100)["items"]) == 1
    with pytest.raises(ApiError) as err:
        station(tmp_path / "new.sqlite3").history(cursor, 100)
    assert err.value.status == 410


def test_cancel_and_measure_compete_for_same_request(tmp_path):
    store = station(tmp_path / "history.sqlite3")
    store.create_request("race", 120)
    barrier = threading.Barrier(2)

    def race(action):
        barrier.wait(timeout=2)
        try:
            action()
            return True
        except ApiError as exc:
            assert exc.status == 409
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        cancelling = pool.submit(race, lambda: store.request("race", cancel=True))
        measuring = pool.submit(race, lambda: store.begin("race"))
        cancelled, measuring = cancelling.result(), measuring.result()
    assert cancelled != measuring
    assert store.request("race")["state"] == ("cancelled" if cancelled else "capturing")
    if cancelled:
        assert store.request("race", cancel=True)["state"] == "cancelled"


def test_api_configuration_requires_tls_for_network_access(tmp_path):
    config = tmp_path / "local.toml"
    config.write_text('[api]\nenabled = true\ntoken_file = "api-token"\n', encoding="utf-8")
    assert load_settings(config).api.host == "127.0.0.1"
    with config.open("a", encoding="utf-8") as settings:
        settings.write('host = "0.0.0.0"\n')
    with pytest.raises(SettingsError, match="require HTTPS"):
        load_settings(config)
    with config.open("a", encoding="utf-8") as settings:
        settings.write('tls_cert_file = "certificate.pem"\ntls_key_file = "key.pem"\n')
    assert load_settings(config).api.host == "0.0.0.0"


def test_low_storage_rejects_new_requests_and_measure(tmp_path, monkeypatch):
    store = station(tmp_path / "history.sqlite3")
    store.create_request("reserved", 120)
    monkeypatch.setattr(store, "_storage_available", lambda: False)
    assert not store.health()["storageAvailable"]
    with pytest.raises(ApiError, match="Free space"):
        store.begin("reserved")
    assert store.request("reserved")["state"] == "pending"
    store.request("reserved", cancel=True)
    with pytest.raises(ApiError) as err:
        store.create_request("new", 120)
    assert err.value.status == 503
    assert store.history(None, 100)["items"] == []
