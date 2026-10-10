# Raspberry Pi camera bring-up

## 1. Verify the operating-system camera stack

On Raspberry Pi OS Bookworm or later, the supported command names start with
`rpicam-`. The legacy `raspistill`, `raspivid`, and original `picamera` stack are
not used by this project.

```bash
rpicam-hello --list-cameras
rpicam-hello --timeout 5000
```

The first command should name at least one sensor. The second should show a
five-second preview. If these fail, solve the cable/OS/overlay issue before
debugging Python.

## 2. Install the project environment

Picamera2 and its matching `libcamera` bindings come from Raspberry Pi OS:

```bash
sudo apt update
sudo apt install -y python3-picamera2 python3-venv
python3 -m venv --system-site-packages .venv
.venv/bin/python -m pip install -e '.[dev]'
```

Installing `picamera2` into an isolated virtual environment from PyPI is not the
recommended setup. Keep the environment's system packages visible.

## 3. Run application diagnostics

```bash
.venv/bin/parcel-dimensioner camera doctor
.venv/bin/parcel-dimensioner camera list
```

The doctor reports four layers independently: platform, `rpicam` command,
Picamera2 Python import, and detected sensors.

## 4. Preview

From a terminal on the attached Raspberry Pi desktop:

```bash
.venv/bin/parcel-dimensioner camera preview
```

`auto` chooses a Qt/OpenGL preview when a desktop display is present and a DRM
preview otherwise. It can be selected explicitly when diagnosing display issues:

```bash
.venv/bin/parcel-dimensioner camera preview --preview-backend qtgl
.venv/bin/parcel-dimensioner camera preview --preview-backend drm
```

Use `--duration 10` to close automatically after ten seconds. The default of
zero runs until Ctrl+C.

## 5. Capture

```bash
.venv/bin/parcel-dimensioner camera snapshot
.venv/bin/parcel-dimensioner camera snapshot --output data/captures/test.jpg
.venv/bin/parcel-dimensioner camera snapshot --width 4056 --height 3040 --warmup 3
```

The last resolution is only an example. Use a mode supported by the actual
sensor, and do not treat the requested size as a calibrated measurement setting.

## Lens calibration image collection

Lens calibration and station calibration are separate. Keep the camera fixed and
move one flat ChArUco board through varied positions and tilts for lens calibration.
If both wall boards are permanently fixed, use a separate portable board with a
known definition and physical square size. A single repeated wall view is not a
replacement for a varied calibration dataset. Return removable walls to measured,
repeatable positions and redo station calibration afterward.

Stop any existing preview with Ctrl+C. From a terminal inside the Pi desktop
(including Pi Connect screen sharing), run:

```bash
cd ~/parcel-dimensioner
.venv/bin/parcel-dimensioner camera calibration-capture --count 25 --preview-backend qtgl
```

The command opens one continuous preview using the still-image configuration.
Leave a board at its normal station distance and press Enter to autofocus once.
It then sets manual focus at that lens position. Inspect sharpness in the preview;
quit and restart if it is unsuitable. Do not refocus as you move the board.

Move the board, hold it still, then press Enter for each photograph. Include front
views, moderate left/right and up/down tilts, and coverage near all image edges.
Keep markers sharp; avoid extreme tilts, glare, hands over the pattern, and moving
the board so close that it becomes blurred at the locked focus. Around 20-30 useful
views is an initial target, not a guarantee that the dataset passes calibration.
This command collects images; it does not yet detect boards or solve calibration.

Use `q` then Enter or Ctrl+C to finish early. Completed images and a `session.json`
manifest are retained under a timestamped `data/captures/calibration-*` directory.
The same directory contains `camera.toml` with reusable locked-focus settings.
The manifest records actual configured resolution, lens position, camera identity,
configuration, and per-image metadata. An explicit `--output-dir` must name a new
directory; existing datasets are never overwritten. No graphical display is needed
with `--preview-backend null`, so collection can also be prompted over SSH.

Focus can also be set explicitly on preview, snapshot, and collection:

```bash
# Replace 1.25 with the locked LensPosition reported by YOUR session.
.venv/bin/parcel-dimensioner camera snapshot --lens-position 1.25
.venv/bin/parcel-dimensioner camera preview --lens-position 1.25 --preview-backend qtgl
```

LensPosition is a camera focus control, not a distance in inches. Valid limits come
from the camera. To reuse the session settings, select its `camera.toml` with the
global `--config PATH` option, or copy it to `config/local.toml` and use
`parcel-dimensioner --config config/local.toml camera snapshot`. If the session
used the null preview backend for SSH, override preview with `--preview-backend qtgl`
when using the desktop. Use the same resolution, crop,
focus, and camera for subsequent station images and measurement. Re-capture the
empty scene and reference box with that focus; earlier captures may differ.

Manual focus requires a camera exposing AfMode and LensPosition (such as Camera
Module 3). Unsupported controls or failed autofocus produce an actionable error.
The camera always closes on completion or interruption. Collection verifies the
reported lens position for each saved frame; absent or inconsistent metadata
discards that frame and stops collection.

See the [official Picamera2 manual](https://datasheets.raspberrypi.com/camera/picamera2-manual.pdf)
for autofocus and manual lens controls.

## Common failures

- **No cameras available:** shut down, verify ribbon orientation, reseat both
  ends, boot, and retry `rpicam-hello --list-cameras`.
- **Picamera2 import fails in the venv:** recreate it with
  `python3 -m venv --system-site-packages .venv`.
- **Camera is busy:** close other preview/camera applications. Only one process
  should own a sensor during bring-up.
- **Qt preview errors:** run from the attached graphical desktop or try
  `--preview-backend drm` from a local console.
- **Image is soft:** clean the lens, make the mount rigid, confirm the parcel is
  within the lens focus range, and determine whether the module is fixed-focus
  or autofocus before changing software controls.

