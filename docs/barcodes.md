# Hands-free barcode reading and package IDs

The app automatically watches for a barcode after station setup. Hold the box
close to the camera with its barcode facing the lens and inside the guide.
There is no Scan label button or required tap on the preview. A successful read
locks the ID, gives a green **Barcode read** confirmation, and plays a short
chime through the configured audio output, when available.

Then place that same box against both station walls and tap **Measure box**.
The accepted result contains dimensions, stable weight, and the held ID. The
software cannot verify that the operator moved the same physical package.

## Update the Pi

Commit and push the changes from the laptop. Close Box Scanner, then run in SSH:

```bash
cd ~/parcel-dimensioner
git pull --ff-only
.venv/bin/python -m pip install -e '.[touchscreen]'
```

Open the existing desktop shortcut and capture the empty station. No Pico
firmware update or shortcut reinstall is required. The Pi needs an audio output
for audible feedback: the app tries `paplay` or `aplay`, otherwise the desktop
bell. The visible green confirmation remains available regardless of audio.

## Operator flow

1. Remove all packages and tap **Capture empty station**. The installed wooden
   deck and walls stay in place; this also zeros the scale.
2. Hold the label close and steady in the guide. The camera continually adjusts
   focus while an independent worker decodes frames. Make the barcode large
   enough to show distinct bars and avoid reflections. No capture button is needed.
3. Wait for **Barcode read**, check the displayed ID, then move the box into the
   corner. Reading pauses as soon as a label is accepted; subsequent barcodes
   cannot replace the held ID.
4. Tap **Measure box**, hands off. The camera returns to its normal locked focus
   before capturing dimensions and verifying stable weight. Inspect **Marked
   result** if needed; the barcode is highlighted in its separate label photo.
5. Remove the box, then tap **Next box** to clear the completed result and start
   listening again. This deliberate rearm prevents a previous ID carrying over.
   Identical product UPCs may be read again for a deliberately started new box.

If multiple distinct codes are found in one frame, reading pauses and the app
shows **Choose ID**. Select the intended value or enter an ID. It does not
silently choose the first of several codes. Binary/control-character data or
an excessively long value requires manually entering the actual package ID.

**More** contains Capture empty station, Zero scale, Enter / edit ID, Rescan
label, Measure without barcode, and Exit app. Opening More or the ID editor
pauses reading. Closing it resumes only when a new label is still needed.
Rescan label explicitly discards the held ID. Measure without barcode skips
both live reading and the barcode decoder for the following measurement.

A failed measurement keeps its pending ID for retry. A successful measurement
consumes it. Capturing empty, zeroing, and starting the next box clear it.
Manual edits write the current record atomically; stale edit requests cannot
change a different scan. The editor includes the existing touch keyboard.

## Capture and decoding

- Main capture defaults to **4608 x 2592**, the Camera Module 3 Wide's full
  resolution. A **1280 x 720** stream supplies live decoding; display frames
  are reduced to at most 800 x 450. Dimension detection still works on a copy
  bounded by 1920 x 1080, with outlines mapped back to source coordinates.
- The camera owner copies the small stream, full image, and metadata from
  **one completed request**, then releases it. The decoder never owns a camera
  request or accesses Picamera2. Saved barcode evidence belongs to the exposure
  that produced its ID, rather than a later photograph of a moving package.
- The decoder first tries the small image. If needed, it tries the original
  full-resolution image with cropped, contrast-adjusted, and straightened views.
  One frame is offered at most every 0.4 seconds. Queues hold only the latest
  waiting frame; preview and scale updates run independently of decoding.
- Candidate centers must be inside the guide and the barcode's longest edge
  must span at least 10% of the source width. This deliberately favors a label
  presented close to the camera over small background barcodes. Move the label
  closer if it is readable to a person but too small within the complete view.
- Only decoder-valid values are accepted. No ID is guessed and no confidence
  percentage is invented. A valid product UPC is not necessarily a unique
  shipment ID; the operator still needs to check the displayed value.
- Each listening cycle has a generation identifier. Pausing, setup changes,
  measuring, rescanning, and Next box invalidate outstanding results. A late
  decode cannot overwrite a held ID or attach itself to another package.
- No photograph is written for ordinary unsuccessful live frames. One accepted
  source image is persisted with its metadata. Decoder errors pause listening
  with an explanation and allow manual entry or an explicit barcode skip.
- Continuous autofocus, optional full focus range, and an automatic exposure
  mode favoring short shutters are used only during label listening. Normal
  metering, exposure settings, and locked station focus are restored and checked
  before measurement. Focus-window arguments use numeric tuples, as required by
  the installed Picamera2 0.3.38 control adapter.

The camera controls follow the [Picamera2 manual](https://datasheets.raspberrypi.com/camera/picamera2-manual.pdf).
Decoding uses [ZXing-C++](https://github.com/zxing-cpp/zxing-cpp/tree/master/wrappers/python)
and optional [OpenCV BarcodeDetector](https://docs.opencv.org/4.11.0/dc/df7/classcv_1_1barcode_1_1BarcodeDetector.html).
The older prompted burst capture remains available to camera diagnostics; it is
not the touchscreen's normal label workflow.

## Saved data and review

Accepted labels have a `label-<id>/` folder with `label.jpg`, `label.json`, and
`result.json`. `label_capture.mode` is `live`. The result contains the candidate
values/formats, original-image polygons, raw byte encodings, selected index,
package ID/source, and timestamped ID history. The associated measurement copies
that evidence alongside `box.jpg` and its combined top-level `result.json`.

**Marked result** displays the box guides and a separate highlighted label
picture, plus the selected package ID. Label coordinates are never drawn on the
station image. Editing the ID refreshes the review. Fit/shift diagnostics and
the change mask use processing-image pixels; box guides and barcode polygons
use original-image pixels, as recorded by the image-size fields.

Use the measurement's top-level `result.json` for dimensions, weight, and ID.
`review/result.json` is the vision-only report. Camera-worker failures save
`camera-error.txt` in the session folder with the full traceback.

Live scanning needs testing on the physical Pi with its actual lighting, labels,
and audio output. Software tests cover decoding, latching, stale-result rejection,
focus restoration, and the touchscreen flow; they do not establish a guaranteed
reading distance or label success rate. Sharing/API integration remains planned.
