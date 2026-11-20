# Station API and laptop test

The touchscreen app serves the API while it is running. A capture request
reserves the next measurement for a caller-supplied ID. The operator handles
the box on the station and taps **Measure box**. Successful scans go into
measurement history automatically. Standalone scans use the same workflow.

## Enable on the Pi

From the project directory on the Pi, create a private token file once. The
command protects an existing token file from being overwritten.

```bash
cd ~/parcel-dimensioner
install -d -m 700 ~/.config/parcel-dimensioner
(umask 077; set -C; .venv/bin/python -c 'import secrets; print(secrets.token_urlsafe(32))' > ~/.config/parcel-dimensioner/api-token)
```

Add this section to the TOML the station already uses. If the station uses the
repository defaults, copy `config/parcel-dimensioner.toml` to `config/local.toml`
and edit the copy. Set a unique `device_id` before the first API startup and
keep it stable for the lifetime of that station’s history database.

```toml
[api]
enabled = true
device_id = "box-scanner"
host = "127.0.0.1"
port = 8765
token_file = "~/.config/parcel-dimensioner/api-token"
```

Close any running Box Scanner instance. Start the API-enabled instance from a
terminal on the Pi desktop. Use the station’s existing lens setting if it
differs from the example.

```bash
cd ~/parcel-dimensioner
.venv/bin/parcel-dimensioner --config config/local.toml touchscreen --lens-position 1.018378
```

To update the managed Box Scanner desktop shortcut, use `desktop-install` in
place of `touchscreen` in that command. If the station already has a separate
autostart entry, update that entry’s command to use the same config. After
startup, clear the platform and tap **Capture empty station** to capture the
camera reference and zero the scale. Keep the deck and walls in place.

## Test from Windows

The laptop can act as the receiving app with PowerShell. With both machines
on the same Wi-Fi, open a tunnel in one PowerShell window. Substitute the Pi’s
Wi-Fi address and leave this window open through the test.

```powershell
ssh -N -L 8765:127.0.0.1:8765 box-scanner@PI_WIFI_ADDRESS
```

Open a second PowerShell window. Retrieve the token into a variable over SSH
and check that `ready` is `True`.

```powershell
$pi = 'PI_WIFI_ADDRESS'
$token = (ssh "box-scanner@$pi" 'cat ~/.config/parcel-dimensioner/api-token').Trim()
$auth = @{ Authorization = "Bearer $token" }
$api = 'http://127.0.0.1:8765/api/v1'
Invoke-RestMethod -Uri "$api/health" -Headers $auth
```

Reserve a scan. The request should return `pending`.

```powershell
$id = [guid]::NewGuid().ToString()
$body = @{ requestId = $id; expiresInSeconds = 120 } | ConvertTo-Json -Compress
Invoke-RestMethod -Method Post -Uri "$api/capture-requests" -Headers $auth -ContentType 'application/json' -Body $body
```

On the Pi, verify that **Capture requested** appears. If a previous result is
open, tap **Next box**. Hold the label in front of the camera until it reads,
choose a code if prompted, or tap **Continue without barcode**. Place the same
box against both walls, remove your hands, then tap **Measure box** before the
request expires. After the station saves the result, run the following.

```powershell
$request = Invoke-RestMethod -Uri "$api/capture-requests/$id" -Headers $auth
$request
if ($request.state -eq 'completed') {
    Invoke-RestMethod -Uri "$api/measurements/$($request.measurementId)" -Headers $auth |
        ConvertTo-Json -Depth 5
}
```

A completed request returns its `measurementId`. The corresponding measurement
has this shape.

```json
{
  "schemaVersion": 1,
  "measurementId": "692860bfa95241dd9f454cc00208f99a",
  "deviceId": "box-scanner",
  "requestId": "req-7b82a941",
  "capturedAt": "2026-11-18T14:30:00.000Z",
  "status": "complete",
  "imageUrl": "/api/v1/measurements/692860bfa95241dd9f454cc00208f99a/image",
  "barcodeStatus": "read",
  "barcode": {"value": "00123456789012", "symbology": "ITF"},
  "dimensions": {"length": 18.25, "width": 12.5, "height": 10.0, "unit": "in"},
  "weight": {"value": 14.62, "unit": "lb", "basis": "gross"}
}
```

If the measurement includes `imageUrl`, download its box photo with the same
bearer token. The URL is relative to the API server's origin. In the laptop
test, `$api` already includes `/api/v1`, so this command builds the equivalent
URL from the measurement ID and saves the original JPEG.

```powershell
Invoke-WebRequest -Uri "$api/measurements/$($request.measurementId)/image" `
    -Headers $auth -OutFile "box-$($request.measurementId).jpg"
```

Run a second scan without posting a request. It appears in history without a
`requestId`. List history and poll from the returned cursor as follows.

```powershell
$page = Invoke-RestMethod -Uri "$api/measurements?limit=100" -Headers $auth
$page.items | ConvertTo-Json -Depth 5
$after = [uri]::EscapeDataString($page.nextCursor)
Invoke-RestMethod -Uri "$api/measurements?after=$after" -Headers $auth
```

To test cancellation, create a fresh request and run
`Invoke-RestMethod -Method Post -Uri "$api/capture-requests/$id/cancel" -Headers $auth`
while it is pending.

## Connect a receiving backend

The laptop setup uses an encrypted SSH tunnel. For a direct connection on the
warehouse network, configure `api.host` (for example, `0.0.0.0`) and set both
`api.tls_cert_file` and `api.tls_key_file`. Give the backend a station hostname
or address covered by the certificate and a trust chain it can validate. Use
`Authorization: Bearer <token>` on all requests.

| Method and path under `/api/v1` | Behavior |
| --- | --- |
| `GET /health` | `ready` indicates whether a new request can be accepted. Check `captureState`, `activeRequestId`, `storageAvailable`, and `clockSynchronized` when diagnosing the station. |
| `POST /capture-requests` | Accepts one reservation at a time. `expiresInSeconds` defaults to 120 and accepts 10–600. A new reservation returns 201. Replaying the original ID and parameters returns 200 with its current state. |
| `GET /capture-requests/{requestId}` | Returns `pending`, `capturing`, `completed`, `cancelled`, `expired`, or `interrupted`. Use the completed request’s `measurementId` to fetch the result. |
| `POST /capture-requests/{requestId}/cancel` | Cancels a pending request. Repeating a successful cancellation returns the same terminal state. A capture already in progress returns 409. |
| `GET /measurements/{measurementId}` | Returns one immutable measurement. |
| `GET /measurements/{measurementId}/image` | Returns the original box photo as `image/jpeg`. HTTP 404 means the ID is unknown or the measurement has no linked photo. HTTP 410 `image_unavailable` means a previously linked photo is gone. |
| `GET /measurements?after={cursor}&limit=100` | Returns `items`, `nextCursor`, and `hasMore` in insertion order. Limit accepts 1–500. Begin without `after`. |

Save the request ID and draft association before posting. After a network
timeout, GET that request ID or retry with the same ID and parameters. Errors
have an `error` object with `code` and `message`. A different active request or
capture returns 409 `device_busy`. Reusing an ID with different parameters
returns 409 `request_id_conflict`.

Deduplicate records by `deviceId` and `measurementId`. Check `hasMore` to keep
paging, and save `nextCursor` only after persisting the whole page. If a
database reset causes HTTP 410 `cursor_expired`, start again without `after`
and reconcile with the records already received. Present a requested result
on the associated draft for operator review. Standalone results can go to an
unassigned inbox. This version uses polling.

## Recovery and measurement behavior

- The expiration deadline limits when Measure can be tapped. A capture that
  starts in time can finish afterward. A failed capture can be retried with
  another tap before the deadline. Use a new request ID after expiration or
  interruption.
- Dimensions are in inches with the longer horizontal side listed first. Weight
  is gross in pounds with the empty platform tared. The scale must be stable
  across the camera capture. See [scale setup and validation](scale.md) for the
  default stability thresholds and the station’s observed corner spread.
- The scan waits up to the configured scale timeout for stable weight and up
  to eight seconds for the box image capture. The explicit dimensions-only
  option saves a local result and leaves the API request incomplete.
- Only a decoded and selected barcode produces a `barcode` field. Preserve
  its value as a string. A different choice or manual ID saved after
  measurement is a local edit. A fresh measurement gets a new ID.
- The SQLite database is `data/captures/measurement-history.sqlite3` with the
  default captures directory. It retains requests and measurements without
  automatic expiry of completed history. `imageUrl` is included on measurements
  with a linked box photo. The API serves that existing JPEG while it is on the
  Pi, subject to the 48-hour photo cleanup. The measurement stays available
  after the photo is removed. On upgrade, the station links earlier API
  measurements to retained box photos by their saved scan IDs.
- After a restart, the station marks any unfinished requests `interrupted`.
  Completed records and history cursors persist. Capture the empty station
  again before accepting new requests. When disk space drops below 32 MiB,
  free storage before taking more measurements.
