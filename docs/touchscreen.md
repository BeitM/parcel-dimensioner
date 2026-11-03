# Box Scanner touchscreen app

The Pi can run a local fullscreen app with a live camera view, large touch
buttons, and dimensions shown in inches. No browser, network server, or company
API is needed. The desktop launcher opens this app; it does not start on boot.

## One-time setup on the Pi

First transfer the updated code to the Pi. From its project folder:

```bash
sudo apt update
sudo apt install -y python3-tk python3-picamera2
cd ~/parcel-dimensioner
.venv/bin/python -m pip install -e '.[touchscreen]'
.venv/bin/parcel-dimensioner desktop-install --lens-position 1.018378
```

Use the existing virtual environment created with `--system-site-packages` so
it can see the Pi's Picamera2 and Tk installation. The installer creates **Box
Scanner** in the desktop and application menu, using this environment's Python
and the project folder. If the desktop asks, choose **Allow Launching** or
**Execute** the first time. It does not install a system service or change screen
orientation. Reinstall the shortcut if you move the project or virtual environment.

Open from the desktop or Pi Connect **screen sharing**, not the remote shell.
You can also start directly in a Pi desktop terminal:

```bash
.venv/bin/parcel-dimensioner touchscreen --lens-position 1.018378
```

`--windowed` opens a regular window. To use a saved camera configuration, put
`--config /absolute/path/to/camera.toml` before `touchscreen` or `desktop-install`.
If no lens position is specified in the config or command, the app focuses once
at startup and locks that position; keep the wall patterns clearly visible.

## Daily operation

1. Close any other camera preview/capture apps, then open **Box Scanner**.
2. Clear the platform. Tap **Capture empty station**, then confirm that the
   platform is empty. The app checks that it can see both walls and the floor edge.
3. Place a box snugly against both walls. Tap **Measure box** and hold it still
   while the app captures and calculates. The preview pauses during calculation.
4. Read **L, W, H** in inches. Tap **Marked result** to inspect the chosen points,
   then **Live view** to return to the camera. Results are saved automatically.
5. Replace the box and tap **Measure box** again. No new empty image is needed
   while the station, camera, focus, platform, and lighting stay unchanged.
6. Capture a fresh empty reference after a physical or lighting change. Every
   restart also requires a fresh reference. Tap **Exit** to release the camera.

A failed scan clears the previous dimensions so they cannot be mistaken for
the new box. Fix the reported problem and retry. If camera startup fails, close
other camera applications and reopen Box Scanner. If capture reports changed
focus, reopen the app and capture a new empty reference. Substantial station
movement is rejected by the existing measurement command; retake the empty image.

Images and results stay on the Pi under
`data/captures/station-<time>-<id>/`. Each empty capture has its own JPEG and JSON
metadata. Each scan gets a numbered folder with `box.jpg`, capture metadata,
`review/result.json`, `review/measurement.jpg`, and `review/change-mask.png`.
Failed attempts retain their images and an `error.txt`. Existing images are not
overwritten. These runtime files are ignored by Git.

The application owns one camera for preview and all captures, with unchanged
resolution and locked focus throughout the session. Camera and measurement work
run on one background worker; Tk widgets are updated only on the UI thread.
There is no automatic detection of when a box has been placed or removed: each
scan is explicitly triggered by a tap. Review results before using them elsewhere.

## Current limits

This app automates the existing **experimental** measurement method; it does not
improve its accuracy or add lens calibration, barcode, weighing, or webhook
delivery. It uses the current project's wall layouts and nominal 1.5-inch square
spacing. The empty station must actually be empty; the confirmation and wall
checks cannot prove that all objects have been removed.

The initial laptop tests exercise the workflow with simulated camera data and
the real Tk event loop. The Raspberry Pi camera, touchscreen performance, and
desktop shortcut need an on-device smoke test before regular use.
