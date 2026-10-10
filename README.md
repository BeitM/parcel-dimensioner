# Parcel Dimensioner

A Raspberry Pi station for capturing a parcel, estimating its dimensions, reading
its barcode, and recording its weight.

The repository is currently in **camera bring-up**. The first milestone is a
repeatable preview and still-image capture from the camera attached to the Pi.

## System shape

The application is intentionally split by capability:

- **Camera** — owns Picamera2 and produces images plus capture metadata.
- **Measurement** — will turn calibrated image/depth data into length, width,
  and height.
- **Barcode** — will decode the parcel identifier from an image.
- **Scale** — will read and stabilize the parcel weight.
- **Workflow** — will coordinate a complete scan and persist the result.
- **UI** — will present the live view and guide the operator on the attached
  display.

Only the camera capability exists in code today. Planned capabilities are
documented rather than represented by empty placeholder packages.

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

## Repository layout

```text
config/                         Versioned hardware/application defaults
docs/                           Architecture and Pi bring-up notes
src/parcel_dimensioner/
  camera/                       Camera contract and Picamera2 adapter
  cli.py                        Operator/developer command line
  settings.py                   TOML configuration loading and validation
tests/                          Hardware-independent tests
data/captures/                  Local captures (ignored by Git)
```

## Configuration

Defaults live in [`config/parcel-dimensioner.toml`](config/parcel-dimensioner.toml).
Use another file with `--config PATH` or the `PARCEL_DIMENSIONER_CONFIG`
environment variable. Command-line camera settings override the file for that
invocation.

## References

- [Raspberry Pi camera software documentation](https://www.raspberrypi.com/documentation/computers/camera_software.html)
- [Official Picamera2 repository and installation guidance](https://github.com/raspberrypi/picamera2)
