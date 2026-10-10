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

