# Box-scanner project roadmap

## Goal and current state

Build a Raspberry Pi station that captures a parcel, measures length, width and
height, reads its barcode, records stable weight, and saves a reviewable result.
The laptop is the development machine; the Pi runs the hardware. GitHub transfers
code, SSH provides command access, and Pi Connect shares the desktop.

Confirmed by the operator: SSH and the live camera preview work. The camera
capture CLI is implemented, but saved-image quality still needs to be checked.
ChArUco patterns on foam boards have arrived. Calibration, measurement, barcode,
scale integration, result storage, and the operator UI are not implemented yet.

Earlier project chats have now been reviewed; see [project-context.md](project-context.md).
The latest planning scope is 24 x 18 x 18-inch rectangular parcels, superseding
the original 24 x 24 x 36-inch concept. The intended first measurement method is
one oblique camera looking into two datum fences, with each box seated against
both fences. The planned scale uses four load sensors, a combinator, HX711, and
Pico 2 connected to the Pi over USB. These plans require validation, not redesign
from scratch or additional sensor purchases before testing.

Use the checkpoints below in order. Do not attach boards permanently or purchase
additional sensing hardware until the measurement approach has been selected.

## 1. Define the first working version

Operator tasks:

- Record Pi model, OS version, camera model, and scale model if already owned.
- Record each board's square count, printed square and marker dimensions,
  dictionary, marker IDs, and original pattern file or generation settings.
- Measure actual printed dimensions; do not assume printer scaling was exact.
- Confirm the latest 24 x 18 x 18-inch parcel scope and original 100 lb design
  maximum; actual common parcels were expected to be nearer 50 lb or below.
- Choose minimum parcel dimensions, required accuracy,
  acceptable scan time, and barcode types/label placement.
- Start with rigid rectangular boxes. Treat bags, cylinders, and damaged boxes
  as a later extension with their own validation.

Software tasks: write these requirements into a station specification. Select a
local result format initially, with millimetres and grams as explicit units.

Checkpoint: a written list of supported parcels and measurable success criteria.
If no accuracy requirement is known, agree on a provisional target before testing;
do not label the station accurate simply because its readings look plausible.

## 2. Establish the physical capture setup

Operator tasks:

- Secure the camera and use repeatable lighting. Check the entire intended
  measurement area and largest/tallest intended parcel fit in the view.
- Put one board in view temporarily. Keep it flat, sharp, and free of glare.
- Capture and inspect a board image, empty scene, and several representative boxes.
- Choose capture resolution and focus policy. Calibration and measurement must
  use compatible image geometry and stable focus; recalibrate when these change.
- Plan the scale/platform now: its parcel-support surface defines the measurement
  base plane. A board beside a raised scale platform is not on that same plane.
- Resolve the inconsistent historical wall mounting descriptions. The original
  design puts the deck and both datum fences on the scale and tares them together;
  a later layout describes fixed fences. Use the weighed-fence arrangement as the
  baseline unless the physical build requires a different validated approach.
  Prevent contact with fixed structure and account for movement under load.
- Start with the long board landscape, bottom 6 inches above the support surface,
  and short board portrait at support level, as specified in the latest board plan.
  These are initial positions to test, not guarantees of marker visibility at full
  capacity. Mount panels to supports and provide separate lower registration rails
  where the elevated board leaves a gap; measure their offsets from the box origin.

Software tasks: add persistent metadata sidecars, a dataset capture routine, and
recorded camera/settings identity. These are planned additions, not current commands.

Checkpoint: repeatable sharp photos, stable mounting, known base plane, and a
documented working capture configuration.

## 3. Detect the ChArUco board

Software tasks:

- Add OpenCV board configuration and still-image detection first.
- Save an annotated image showing marker IDs and detected checkerboard corners.
- Report detected corner counts and actionable failures for unsuitable images.
- Handle original board layout and IDs explicitly; a matching square count alone
  does not establish a matching board definition.

Operator tasks: provide the board definition and test photos. If several boards
have repeated IDs, test one at a time until the software has an explicit strategy
for distinguishing boards. Physical board joins do not automatically create a
known three-dimensional calibration rig.

Checkpoint: correct, repeatable detections across the intended image area.

## 4. Calibrate the lens and station geometry

First calibrate the camera's lens:

- Keep the camera settings stable and move one flat board through varied positions,
  tilts, and distances, covering the centre and edges of the image.
- Aim initially for 20–30 useful views; this is a collection target, not a guarantee
  of calibration quality. Reject blurred or weak detections and inspect coverage.
- Estimate camera intrinsics and lens distortion. Store the calibration with board
  definition, image geometry, focus information, timestamp, and error statistics.
- Evaluate held-out images and known dimensions in addition to fitting error.

Then calibrate the installed station:

- Return the board to a known position relative to the actual parcel-support plane.
- Establish camera pose and physical coordinate axes. If using upright boards,
  measure their positions and angles rather than assuming perfect right angles.
- Validate known distances at several locations on the base plane.
- Separate station geometry from lens calibration; moving the camera changes the
  former even if the lens calibration remains valid.

Checkpoint: known planar distances satisfy the agreed error target across the
working area, with a reproducible calibration and validation report.

## 5. Prove all three dimensions before building the full UI

The selected first approach is a calibrated oblique camera and a rectangular box
seated against two datum fences. Fit its visible edges/corners to a projected 3D
cuboid whose base and two rear faces are constrained by the station planes.
ChArUco boards establish the references; parcel edges supply the unknown extents.
Validate this geometry first with manually selected corners, including height.

Fallbacks only if validation of the selected approach fails:

- Multiple calibrated views with known camera relationships.
- An overhead camera combined with independently measured height or validated
  depth sensing.

Software tasks:

- Begin with manually identified box corners and manually entered height where
  appropriate. Prove the geometry before adding automatic segmentation.
- Correct for the top face being above the base plane. A base-plane pixel-to-mm
  transform must not be applied directly to elevated box edges.
- Add automatic edge/corner detection only after reference calculations work.
- Return dimensions with quality checks, or reject the scan with a reason.

Operator tasks: measure reference boxes manually and scan them repeatedly across
different positions, rotations, and heights. Start with 5–10 boxes and 10 placements
per box, then expand to the intended operating range and failure cases.

Checkpoint: report per-axis maximum and typical errors, repeatability, rejection
rate, and scan time on held-out boxes. All must satisfy the agreed requirements.
If they do not, revise geometry, lighting, or sensing before proceeding.

## 6. Integrate barcode and weight

These are separate capabilities and can be developed independently after the
physical setup and interfaces are understood.

Barcode tasks: choose supported symbologies, decode representative real labels,
record the decoded value/type, and handle missing or multiple barcodes explicitly.
Plan for labels hidden from the camera; operator repositioning or a separate reader
may be needed.

Scale tasks: bring up the purchased Pico 2, HX711, combinator, and four load sensors;
define a USB serial protocol for weight, stability, tare, and button events; implement
tare/zero handling, stable-reading detection, timeout, disconnect, and overload
handling. Test known weights and repeat readings at different platform positions.
Verify the installed boards and frame do not affect the weight reading.

Checkpoint: barcode and weight each meet their own test criteria, with clear
failure messages rather than substituted or silently stale values.

## 7. Build the complete scan workflow and operator screen

Software tasks:

- Coordinate: place parcel -> wait for stable weight -> capture -> measure -> decode
  -> validate -> review/accept -> save. Allow a defined retry/manual correction flow.
- Own the camera once per session; coordinate preview and capture so competing
  processes do not try to open it simultaneously. The current CLI opens the camera
  separately for each operation and needs a session lifecycle for this UI.
- Show live alignment guidance, scan status, dimensions, barcode, weight, and
  actionable retry reasons.
- Save a unique scan ID, UTC timestamp, units, measurements, evidence image,
  calibration/settings identifiers, and quality/manual-correction information.
- Use a local database or durable records and add CSV export if needed. Detect
  failed saves and prevent accidental duplicate acceptance.

Checkpoint: complete scans can be reviewed and retrieved; missing hardware,
unreadable labels, invalid measurements, and failed saves are handled visibly.

## 8. Validate and make startup reliable

Operator and software tasks:

- Run a representative trial across the supported parcel range. Track per-axis
  and weight error, barcode success, rejected scans, and end-to-end time.
- Test camera/scale disconnects, reboot, changed lighting, and storage exhaustion.
- Add appropriate desktop/session startup for the UI, logs, recovery instructions,
  retention policy, and backups of calibration and results.
- Verify operation on the Pi without the laptop or network for core scanning.
- Document when to recheck geometry or recalibrate, including camera, lens,
  resolution/crop, focus, and support-surface changes.

Checkpoint: the station boots reliably, survives expected failures, and meets
the written acceptance criteria in its installed configuration.

## Next session

1. Record camera/Pi and board specifications.
2. Temporarily position one board and capture a sharp test image.
3. Confirm the intended camera angle, scale/support plane, parcel size range,
   and accuracy requirement.
4. Implement and verify still-image ChArUco detection.

## Technical references

- [OpenCV ChArUco detection](https://docs.opencv.org/4.10.0/df/d4a/tutorial_charuco_detection.html)
- [OpenCV camera calibration](https://github.com/opencv/opencv/blob/5.x/doc/tutorials/calib3d/camera_calibration/camera_calibration.markdown)
- [OpenCV pose estimation](https://docs.opencv.org/4.12.0/d5/d1f/calib3d_solvePnP.html)
