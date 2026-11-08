# Box Scanner touchscreen app

The Pi can run a local fullscreen app with a live camera view, large touch
buttons, dimensions in inches, weight in pounds, and automatic barcode/package
ID reading. No browser, network server, or company
API is needed. The desktop launcher opens this app; it does not start on boot.

## Transfer the changes from the laptop

Commit the app and Pico firmware changes on the laptop first, if they are not already
committed. Then run this in **Windows PowerShell on the laptop** to upload the
commit to GitHub and connect to the Pi:

```powershell
cd C:\Code\parcel-dimensioner
git push origin main
ssh box-scanner@192.168.1.228
```

Enter the Pi login password when SSH prompts for it. Once the prompt is the Pi's
shell, run the installation block below. If the push fails, resolve that before
pulling on the Pi. Git transfers committed code; ignored captures stay on their
current device.

## One-time setup on the Pi

Run these in the **Pi shell**, either over SSH or in a Pi desktop terminal:

```bash
cd ~/parcel-dimensioner
git pull --ff-only
sudo apt update
sudo apt install -y python3-tk python3-pil.imagetk python3-picamera2
.venv/bin/python -m pip install -e '.[touchscreen]'
.venv/bin/parcel-dimensioner desktop-install --lens-position 1.018378
```

For weighing, also follow the **one-time Pico program installation** in
[the scale setup guide](scale.md). The temporary raw-count test previously run
with `mpremote run` does not install that startup program. Stop any running
terminal scale test before opening the app; one program owns the USB port.

If the pull fails because of local changes, stop and inspect those changes;
do not discard them to force the update. After installation, use the Pi desktop
or Pi Connect screen sharing to launch **Box Scanner**. The remote shell alone
does not display the app on your laptop.

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
2. Clear the platform, keeping the wooden deck and attached walls installed.
   Tap **Capture empty station** to act immediately. The app checks both walls and the
   floor edge and zeros the scale. An unavailable scale does not prevent saving
   the image reference; the app explains that weight is unavailable.
3. Place a box snugly against both walls and take your hands off. Tap **Measure
   box**. The app waits for stable weight, captures the image, and checks for
   movement across capture before calculating dimensions. The preview pauses
   during this operation; the live scale status continues updating.
4. Read **Length, Width, Height** in inches and **Saved weight** in pounds.
   The **Package ID** row shows whether a barcode was found. Use **Choose ID**
   for multiple codes, or **Enter ID / Edit ID** for manual entry/correction with
   the built-in touch keyboard. **Save ID** updates this scan's saved record.
   Barcode failure does not discard dimensions or weight. See [barcode setup
   and operation](barcodes.md) for details and label tests.
   **Live scale** is a separate current reading; removing a box does not change
   its saved weight. Tap **Marked result** to inspect the chosen points,
   then **Live view** to return to the camera. Results are saved automatically.
5. Replace the box and tap **Measure box** again. No new empty image is needed
   while the station, camera, focus, platform, and lighting stay unchanged.
6. Use **Zero scale** only with the empty platform. This leaves the camera
   reference intact. Capture a fresh empty reference after a physical or lighting
   change. Every restart requires a fresh reference and explicit scale zero.
   USB reconnects also require another zero. The calibration factor persists.
7. If weight cannot be obtained, retry with **Measure box**, or explicitly tap
   **Measure dimensions only**. That takes a new image and saves weight as
   unavailable with the reason. There is no silent fallback or reused old weight.
8. Tap the square **Exit** button in the preview's upper-right to close both
   camera and scale. The taller main buttons form two columns: Capture empty
   station / Zero scale on the left; Measure box / Marked result on the right.

A failed scan clears the previous dimensions so they cannot be mistaken for
the new box. Fix the reported problem and retry. If camera startup fails, close
other camera applications and reopen Box Scanner. If capture reports changed
focus, reopen the app and capture a new empty reference. Substantial station
movement is rejected by the existing measurement command; retake the empty image.

Images and results stay on the Pi under
`data/captures/station-<time>-<id>/`. Each empty capture has its own JPEG and JSON
metadata. Each scan gets a numbered folder with `box.jpg`, capture metadata,
`review/result.json`, `review/measurement.jpg`, and `review/change-mask.png`.
**Marked result** displays `review/scan.jpg`, which combines thicker box outlines,
measurement guides, barcode highlights, and the selected/manual package ID.
Green is the detected boundary, yellow is the measured top edges, and blue dashed
lines are inferred cuboid edges. Selected barcode regions are pink; other
decoded candidates are orange. The ID and barcode values are listed below the
image. Editing an ID refreshes this review, including while it is being viewed.
The scan's **top-level `result.json`** contains dimensions AND weight (or the
explicit missing-weight reason); the review JSON is the vision-only report.
The top-level file also contains the barcode candidates, selected/manual package
ID, and timestamped ID-edit history. A new scan clears the previous package ID.
Failed attempts retain an `error.txt` and `attempt.json`, plus any images already
captured. A failure before capture has no box image. Original capture images are
not overwritten. The derived `review/scan.jpg` is regenerated after ID edits.
These runtime files are ignored by Git.

The application owns one camera for preview and all captures, with unchanged
resolution and locked focus throughout the session. Camera and measurement work
run on one background worker; a separate USB reader continuously acquires scale
samples. Tk widgets are updated only on the UI thread.
There is no automatic detection of when a box has been placed or removed: each
scan is explicitly triggered by a tap. Review results before using them elsewhere.

## Current limits

This app automates the existing **experimental** measurement method; it does not
improve dimension accuracy or add lens calibration or webhook
delivery. It uses the current project's wall layouts and nominal 1.5-inch square
spacing. The empty station must actually be empty; the wall
checks cannot prove that all objects have been removed.

Weight uses the station's initial 5 lb / 10 lb calibration. The known corner
spread (about 0.21 lb under the 10 lb test) is not corrected by this integration.
See [scale setup and validation](scale.md) for configuration and checks.

Laptop tests exercise the workflow with simulated camera/scale data and the
real Tk event loop, including portrait layouts. Raspberry Pi camera, Pico USB
timing, and touchscreen behavior still need an on-device smoke test.
