# Automatic fixed-wall measurement experiment

This command removes manual corner selection from the current two-image trial.
It estimates rigid rectangular boxes that touch both station walls. It is not a
validated dimensioner or a general object-reconstruction system. No known box
dimensions or manually selected pixels are inputs.

## Run on saved images

Use the existing locked-focus capture procedure, first capturing the empty
station and then the box. Keep the camera, lens position, crop, walls, lighting,
and platform fixed. The camera preview must be closed before starting another
camera session; measurement itself only reads saved files.

Install dependencies once in the project environment:

```bash
.venv/bin/python -m pip install -e '.[vision]'
```

Measure one placement:

```bash
.venv/bin/parcel-dimensioner measure --empty data/captures/repeat-test-02/calibration-001.jpg --box data/captures/repeat-test-02/calibration-002.jpg --output-dir data/captures/measurement-02-002
```

Measure all three saved placements in a Pi terminal:

```bash
for n in 002 003 004; do
  .venv/bin/parcel-dimensioner measure --empty data/captures/repeat-test-02/calibration-001.jpg --box "data/captures/repeat-test-02/calibration-$n.jpg" --output-dir "data/captures/measurement-02-$n"
done
```

Use different output folder names for a second run. Existing folders are
protected from overwriting. A failed measurement returns exit code 2 with a
reason; successful experimental estimates return 0. Outputs are:

- `result.json`: full precision estimates, detected points, wall fit/movement
  diagnostics, independent wall height estimates, and limitations.
- `measurement.jpg`: automatically chosen floor/rear/top-edge points overlaid
  on the box image. The floor point is inferred from the empty image, even
  though it is hidden by the box in this overlay.
- `change-mask.png`: regions classified as changes in the station.

Length is the larger of the two horizontal dimensions; width is the smaller.
Rotating the box swaps which wall supplies length. Height averages estimates
from the two walls; the JSON retains their disagreement. Displaying two decimal
places does not imply hundredth-inch accuracy.

On Windows, install with `python -m pip install -e ".[vision]"`, then use
`parcel-dimensioner measure` with local image paths and the same arguments.

## Geometry and automatic detection

1. Find the main group of markers belonging to each wall. Small spare prints
   outside those groups are excluded without fixed screen-coordinate masks.
2. Detect ChArUco corners on each main board and fit its plane-to-image mapping.
   Compare fitted wall geometry at common corners across images. At 1920 pixels
   wide, consistent shifts up to 6 pixels may be aligned before subtraction;
   shifts above 3 pixels add a visible warning. Thresholds scale with resolution.
   A single alignment must match both walls within 2 pixels. Larger shifts or
   incompatible wall movements still require a new empty image. Original files
   are unchanged; raw corner shifts, fitted shifts, alignment, and warnings are
   saved in the result. This compensates small image shifts, not changed station
   geometry or major camera movement.
3. Rectify each empty wall view and find the transition from the white lower
   margin to the colored platform. Intersect the two floor lines to locate the
   floor corner. Border widths do not have to match.
4. Normalize modest brightness changes and segment the occupied-versus-empty
   difference within a region derived from the boards. Find a changed contour
   that overlaps both wall views. Large competing candidates are rejected.
5. Locate dominant horizontal contact edges in each wall's coordinates. Infer
   the rear vertical joint from the floor point and the two grids' vertical
   directions, then intersect each top edge with that joint.
6. Average the rear top detections, find the outer contact-edge endpoints on
   the contour, and convert wall-coordinate spans into inches. Each wall also
   supplies a separate floor-to-top height.

The board layouts are currently specific to this project: `DICT_6X6_1000`,
non-legacy ChArUco, long wall 15 by 11 squares with IDs 0–81, short wall 11 by 15
with IDs 200–281. Default square size is 38.1 mm and marker size 28 mm. If the
printed sizes differ, use `--square-mm` and `--marker-mm`; do not change the
scale just to make a known box match. Lens calibration is not performed.

The approach uses [OpenCV ChArUco detection](https://docs.opencv.org/4.x/df/d4a/tutorial_charuco_detection.html)
and [planar homographies](https://docs.opencv.org/4.x/d9/dab/tutorial_homography.html).
Only contact edges lying in the wall planes are measured. The same mapping
cannot correctly measure arbitrary edges floating away from those planes.

## Limits and practical checks

- Both walls must be vertical with horizontal grid rows and a roughly right
  angle between them. Their inferred joint must match the box's actual contact
  corner. Unequal printed margins are supported; physical gaps, stepped joints,
  offsets, bowed walls, or a box that is not flush introduce uncorrected errors.
- Automatic floor detection currently needs a white paper margin meeting a
  contrasting colored platform near the bottom grid row. A raised platform,
  white floor, hidden border, or unrelated edge there can cause a failed or
  incorrect floor estimate. Examine the overlay.
- Contrast thresholds are experimental and evaluated on 1920 by 1080 captures.
  Shadows, reflections, gloss, background changes, low-contrast boxes, or missing
  top-edge pixels can bias the contour. Threshold failures do not always trigger
  rejection. This implementation does not identify general parcel shapes.
- A camera/wall shift of more than 3 pixels at the 95th percentile of common
  detected corners is rejected. Smaller motion can still affect measurements.
  Matching resolution alone does not establish matching focus or lens crop.
- A warning is included when the two height estimates differ by more than
  0.25 inch. Agreement between estimates does not prove accuracy.
- No live measurement loop, touchscreen measurement UI, barcode, or scale is
  added by this command. Existing preview and capture commands still operate
  separately.

The algorithm was developed against the provided capture sets. Those results
are development checks, not independent blind validation. Automated tests also
render boards and a cuboid through a synthetic camera with unequal margins,
and exercise empty frames, movement, invalid inputs, and output preservation.
Future accuracy work should use new boxes, measured printed scale, rigid wall
geometry, and lens calibration before setting a measurement tolerance.

The first automatic run on those seven occupied images gave the following
results (inches; these replace no earlier manually assisted results):

| Capture set | Image | Automatic L | Automatic W | Automatic H |
| --- | --- | ---: | ---: | ---: |
| dimension-test-01 | 002 | 11.95 | 9.45 | 10.73 |
| repeat-test-01 | 002 | 11.80 | 9.50 | 10.97 |
| repeat-test-01 | 003 | 11.82 | 9.65 | 11.03 |
| repeat-test-01 | 004 | 11.81 | 9.47 | 10.96 |
| repeat-test-02 | 002 | 9.00 | 7.90 | 2.86 |
| repeat-test-02 | 003 | 8.90 | 8.41 | 2.85 |
| repeat-test-02 | 004 | 8.86 | 8.06 | 2.79 |

Compared afterward with the supplied hand dimensions, individual dimension
errors reached about 0.28 inch. Small-box width estimates spanned 0.51 inch
across placements. These are meaningful detection limitations even when an
average happens to be close. The next improvements are edge localization,
measured station geometry, and lens correction, followed by new held-out images.
