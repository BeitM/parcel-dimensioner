# Architecture and roadmap

## Goal

The station should guide an operator through placing one parcel in a known
measurement area and produce a record containing:

- a parcel or shipment identifier from a barcode;
- length, width, and height in calibrated units;
- stable weight from the scale;
- an evidence image and useful capture metadata;
- quality/confidence flags when the result should be retried or reviewed.

The Raspberry Pi owns the hardware and the attached display. The initial design
does not require a cloud service or network connection to complete a scan.

## Boundaries

```text
                       +----------------+
                       | Operator UI    |
                       +-------+--------+
                               |
                       +-------v--------+
                       | Scan workflow  |
                       +---+---+---+----+
                           |   |   |
             +-------------+   |   +-------------+
             |                 |                 |
      +------v------+   +------v------+   +------v------+
      | Camera      |   | Scale       |   | Result store|
      +------+------+   +-------------+   +-------------+
             |
       +-----+----------------+
       |                      |
+------v------+        +------v------+
| Measurement |        | Barcode     |
+-------------+        +-------------+
```

- Hardware libraries remain inside adapters (`Picamera2` for the first adapter).
- Measurement and barcode code consume captured frames; they do not own the
  camera lifecycle.
- The workflow depends on capability contracts, which keeps it testable with
  simulated hardware.
- Configuration contains operating choices. Calibration results will live in a
  separate, versioned calibration artifact so routine settings cannot silently
  alter measurement geometry.

## Camera slice (current)

The implemented path is:

```text
CLI -> settings -> camera contract -> Picamera2 adapter -> CSI camera
```

The adapter owns opening, configuring, starting, and closing Picamera2. Imports
are lazy so the rest of the application can be developed and tested away from a
Pi. Every still capture returns its path, camera identity, dimensions, timestamp,
and Picamera2 metadata.

## Measurement constraint

A single uncalibrated RGB image cannot robustly recover all three dimensions of
an arbitrary parcel. Before implementing dimension estimation, we need to choose
and validate a physical method. Likely options include:

1. A fixed overhead camera and calibrated reference plane for length/width,
   combined with a separately measured or constrained height.
2. Multiple calibrated views.
3. A depth/ToF/stereo sensor.
4. Known rectangular-box geometry with fiducials and controlled placement.

The camera boundary intentionally returns full capture metadata and does not
assume one frame is sufficient. That leaves these strategies open.

## Staged roadmap

### 1. Camera bring-up

- Confirm sensor detection and stable live preview.
- Save repeatable images at the intended mounting position.
- Decide orientation, focus behavior, resolution, lighting, and exposure policy.
- Collect representative parcel images and failure cases.

### 2. Calibration and measurement proof

- Fix the camera and measurement surface mechanically.
- Calibrate intrinsics/lens distortion.
- Establish the measurement-plane transform and physical scale.
- Select and prove a height method against reference boxes.
- Define accuracy, repeatability, and rejection thresholds.

### 3. Peripheral slices

- Add barcode decoding behind its own capability boundary.
- Add the selected scale/load-cell adapter and stable-reading logic.
- Define the scan-result model and local persistence.

### 4. Operator workflow

- Add the full-screen UI and live alignment guidance.
- Coordinate capture, barcode, dimensions, and weight.
- Surface actionable retry reasons rather than low-confidence measurements.
- Add startup/service management and recovery after reboot.

## Decisions still needed

These are intentionally not guessed in the code:

- Raspberry Pi model and Raspberry Pi OS version.
- Camera module/lens and whether autofocus is available.
- Camera mounting height, field of view, and maximum parcel size.
- Required dimensional accuracy and throughput.
- Height-sensing method.
- Scale/load cell and connection protocol.
- Barcode symbologies and whether labels may appear on parcel sides.
- Result destination and retention requirements.

