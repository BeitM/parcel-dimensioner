# Parcel Dimensioner

A Raspberry Pi station for capturing a parcel, estimating its dimensions, reading
its barcode, and recording its weight.

The repository supports camera preview/capture and **experimental automatic box
measurement** from a matching empty/occupied image pair. Measurement accuracy is
still being evaluated; lens calibration and the full scanning workflow remain future work.

## System shape

The application is intentionally split by capability:

- **Camera** — owns Picamera2 and produces images plus capture metadata.
- **Measurement** — estimates dimensions of a rectangular box touching both
  ChArUco walls, using an empty reference image.
- **Barcode** — decodes package-image barcodes and supports selecting or entering
  an ID on the touchscreen; see [barcode operation](docs/barcodes.md).
- **Scale** — Pico/HX711 USB readings, explicit zeroing, stability checks, and
  pounds saved with the touchscreen scan; see [scale setup](docs/scale.md).
- **Workflow** — pairs dimensions and weight in a saved scan record.
- **UI** — presents the live view and guides the operator on the attached
  display.

Camera capture, experimental dimensions, and a touchscreen combined dimension/weight
workflow exist today, with barcode/package ID capture. Company-system delivery remains planned.

See [docs/architecture.md](docs/architecture.md) for the boundaries and staged
roadmap.

## Camera quick start on the Pi

These instructions target current Raspberry Pi OS (Bookworm or later) and its
supported `libcamera`/Picamera2 stack. Picamera2 should be installed with `apt`,
not from PyPI, so its version stays compatible with the system camera libraries.

```bash
sudo apt update
sudo apt install -y python3-picamera2 python3-venv

python3 -m venv --system-site-packages .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e '.[dev]'
```

The `--system-site-packages` flag is important: it lets the virtual environment
use the Picamera2 package installed by Raspberry Pi OS.

Then exercise the camera in this order:

```bash
# Check the OS tools, Python package, and attached camera.
.venv/bin/parcel-dimensioner camera doctor

# Show camera information reported by Picamera2.
.venv/bin/parcel-dimensioner camera list

# Open a live preview on the attached desktop until Ctrl+C.
.venv/bin/parcel-dimensioner camera preview

# Capture a configured-resolution JPEG after auto-exposure has settled.
.venv/bin/parcel-dimensioner camera snapshot
```

Snapshots are written to `data/captures/` by default. Runtime captures and local
configuration overrides are ignored by Git.

If the Pi does not detect the camera, first run:

```bash
rpicam-hello --list-cameras
```

Power the Pi down before reseating a CSI ribbon cable. See
[docs/pi-camera-setup.md](docs/pi-camera-setup.md) for troubleshooting and all
camera options.

## Development on another computer

Picamera2 is imported lazily, so formatting and unit tests work on Windows,
macOS, or Linux without Pi camera libraries:

```bash
python -m venv .venv
# Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
pytest
ruff check .
```

Install `.[dev,vision]` to include image measurement tests; they are skipped when
the optional OpenCV/NumPy dependencies are absent.

## Automatic dimension experiment

For touch-button capture and on-screen dimensions, see the
[touchscreen app setup and daily workflow](docs/touchscreen.md). The app can be
launched from a Box Scanner shortcut on the Pi desktop.

Install the optional vision dependencies in the same environment as the application:

```bash
.venv/bin/python -m pip install -e '.[vision]'
.venv/bin/parcel-dimensioner measure --empty data/captures/repeat-test-02/calibration-001.jpg --box data/captures/repeat-test-02/calibration-002.jpg --output-dir data/captures/measurement-02-002
```

The command selects all measurement points automatically. It prints estimated
length, width, and height in inches and saves `result.json`, `measurement.jpg`
(marked corners), and `change-mask.png` in a **new** output folder. It does not
overwrite images or old results. It works on a laptop as well as the Pi; it needs
no running camera or desktop preview.

This first version assumes the project's two known wall patterns, 1.5-inch
printed squares, a contrasting platform, and a rectangular box flush against
both walls. It applies no lens correction and can mistake reflections/shadows
for box edges. Inspect the marked image; a successful calculation does not
certify accuracy. See [the measurement guide](docs/automatic-measurement.md)
for the method, assumptions, test results, and troubleshooting.

## Repository layout

```text
config/                         Versioned hardware/application defaults
docs/                           Architecture and Pi bring-up notes
src/parcel_dimensioner/
  camera/                       Camera contract and Picamera2 adapter
  measurement.py                Experimental automatic fixed-wall measurement
  cli.py                        Operator/developer command line
  settings.py                   TOML configuration loading and validation
tests/                          Hardware-independent tests
data/captures/                  Local captures (ignored by Git)
```

## Configuration

For guided lens-calibration image collection with locked focus, run
`parcel-dimensioner camera calibration-capture --preview-backend qtgl` on the Pi
desktop. Move one board while the camera stays fixed and press Enter for each
image. See [the collection guide](docs/pi-camera-setup.md#lens-calibration-image-collection)
for focus reuse, fixed-wall setups, and saved session metadata.

Defaults live in [`config/parcel-dimensioner.toml`](config/parcel-dimensioner.toml).
Use another file with `--config PATH` or the `PARCEL_DIMENSIONER_CONFIG`
environment variable. Command-line camera settings override the file for that
invocation.

## References

- [Raspberry Pi camera software documentation](https://www.raspberrypi.com/documentation/computers/camera_software.html)
- [Official Picamera2 repository and installation guidance](https://github.com/raspberrypi/picamera2)
