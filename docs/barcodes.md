# Barcode reading and package IDs

The default trial uses one **4608 x 2592** photograph for dimensions and barcode
reading, the full resolution of the station's Camera Module 3 Wide. Put the box
against both walls with the label facing the camera and tap **Measure box**.
Do not scan a separate label first; **Clear label** discards a pending close-up ID.

The two-step **Scan label**, then **Measure box** workflow remains optional.
Label capture briefly autofocuses and restores the station's locked lens position
before returning. Measurement and empty captures keep locked focus. There is
one camera owner and no indefinite live decoding loop.

The live preview uses a small 800 x 450 stream. Dimension detection works on a
copy bounded by 1920 x 1080 to keep the existing thresholds and memory costs;
its detected outline/points are mapped back to original image coordinates.
Barcode decoding uses the full saved image. Marked results retain the original
image size before the ID panel is appended. JPEG quality is set to 95.

## Update the Pi

First commit/push these changes from the laptop. Close Box Scanner on the Pi,
then run in its SSH session:

```bash
cd ~/parcel-dimensioner
git pull --ff-only
.venv/bin/python -m pip install -e '.[touchscreen]'
```

Open the existing Box Scanner desktop icon. The Pico program and desktop
shortcut do not need reinstalling for this change.

The built-in and repository camera defaults are now 4608 x 2592. If the launcher
uses a custom `--config`, update that file's `[camera]` width/height too. Capture
a fresh empty station after switching resolution. To force a one-session trial
from a **Pi desktop terminal**, close the app first and run:

```bash
.venv/bin/parcel-dimensioner touchscreen --width 4608 --height 2592 --lens-position 1.018378
```

Use `--width 1920 --height 1080` for a lower-resolution comparison. Command-line
overrides apply to that launch only. SSH is suitable for installing changes;
launching a visible app requires the Pi desktop session.

Higher resolution provides 2.4 times the pixels across a label at the same field
of view, but increases capture/decoding time, memory, storage, and transfer sizes.
The small preview reduces software overhead; it does not restore the sensor's
lower-resolution frame rate. It cannot recover hidden bars, glare, or optical
blur. Real label success and Pi latency still need station testing.

The touchscreen extra now includes `zxing-cpp`. The optional `[barcode]` extra
installs only the barcode-image dependencies for offline/laptop use. Imports
remain lazy, so camera/measurement commands can operate without this decoder.
The decoder uses [ZXing-C++'s Python image-reading API](https://github.com/zxing-cpp/zxing-cpp/tree/master/wrappers/python).

## On-screen workflow

1. Capture the empty station normally, with the platform empty.
2. Hold the label closer to the camera, facing it as squarely as possible. Keep
   the complete barcode, including the blank space at each end, inside the
   central 80% of the preview. Tap **Scan label** and hold still during focusing.
   Check the pending ID. Multiple values require **Choose ID**; a missing code
   can be rescanned or entered with **Enter ID**. **Clear label** discards the
   pending label and allows measurement without one.
3. Put that same box snugly against both walls, remove your hands, then tap
   **Measure box**. Its dimensions and stable weight are saved with the pending
   ID. A successful measurement consumes the pending label; scan a new label
   for the next package. A failed measurement keeps it for a retry. The software
   cannot verify that the operator placed the same physical package.
4. Scanning a close label is optional. Without one, **Measure box** still tries
   barcode decoding from the original station image, as before.
5. Inspect the **Package ID** row beneath the measurement cards:
   - **Barcode found:** one unique usable value was decoded and filled in.
     Check that it is the intended ID. Tap **Edit ID** to change it.
   - **No barcode found:** dimensions/weight are still saved. Tap **Enter ID**
     to enter the shipment/package ID manually, or reposition and measure again.
   - **Multiple barcodes found:** no value is chosen automatically. Tap
     **Choose ID**, then select the correct candidate and tap **Save ID**.
   - **Barcode unavailable:** the decoder could not run; manual entry remains
     available. Details are saved in `barcode.reason` in the scan record.
   - **Manual ID saved:** the operator's entered value is saved on this scan.
6. The editor includes a touch keyboard with letters, numbers, case switching,
   space, `-`, `_`, `/`, `.`, Backspace, and Clear. A physical keyboard and its
   normal paste shortcut also work. Close leaves the saved ID unchanged.
7. **Save ID** writes the choice to the current scan's top-level `result.json`,
   or the pending label record when preparing the next box.
   Clear the entry and save to remove the selected ID. The raw barcode
   candidates remain available. ID changes do not remeasure the package.

Long IDs are abbreviated on the main screen; the editor holds the complete
value. Manually entered IDs are trimmed at their ends, limited to 256 printable
characters, and cannot include line breaks/control characters. Binary, very
long, or structured decoded data is retained as a candidate but may require
manual entry of the actual package ID.

Capturing a new empty station, zeroing, or starting a new label capture clears
the pending label. A failed label capture does not restore a previous ID.
A pending ID stays visible during measurement and retries. After success, it
belongs to the saved result and is not reused on another measurement. Each accepted scan has a unique `scan_id`; edit
requests must match the current scan. Failed ID writes keep the previous saved
value and display an error. Historical records are not edited from this UI.

Sharing, a network API, a browser table, and company-specific tracking-number
extraction are still planned. There is no Share button in this implementation.

## Decoding behavior and limits

- The worker tries RGB, contrast-normalized grayscale, and explicitly inverted
  grayscale, with decoder rotation/downscale support. There are three bounded
  passes rather than an indefinite scan loop. The UI stays on its own thread.
- Candidates whose centers lie outside the measurement algorithm's selected
  package contour are excluded for station-image decoding. Close-up label
  decoding uses the central 80% of its own image instead. This helps avoid labels on background boxes.
  That contour remains experimental, especially with shadows/poor contrast.
- Duplicate decoded text is combined into one candidate, preserving formats,
  positions in the original image, and raw byte encodings. Multiple distinct
  values always need a choice; no company/carrier preference rules are guessed.
- A single decoded value is not proof it is the shipment ID. A product UPC may
  be shared by many identical boxes; a carrier barcode can contain additional
  data. Review the value, and use manual entry when necessary.
- Missing/failed decoding does not discard valid dimensions or weight. A
  dimensions-only scan also attempts barcode reading. No barcode is taken from
  a later live frame and attached to an older box measurement. A pending label
  without a chosen ID must be resolved or cleared before measurement.
- No generic confidence percentage is invented. Only decoder-valid results
  are kept; visibility/sharpness, angle, glare, label size, and barcode format
  still affect success. The installed ChArUco wall patterns are not parcel IDs.

## Saved data

The scan's top-level `result.json` adds:

- `scan_id`: unique ID for this measurement;
- `package_id`: selected/entered string or null;
- `package_id_source`: `barcode`, `selected_barcode`, `manual`, or null;
- `package_id_history`: timestamped automatic selections and operator edits;
- `barcode.status`, decoder, time, original image size, region, and error reason;
- `barcode.candidates`: decoded values, format names, positions, and raw bytes
  encoded as base64;
- `barcode.selected_index`: selected candidate, or null for manual/no selection.
- `barcode_capture`: image source (`box.jpg` or separate `label.jpg`), mode,
  and the original label capture ID for a two-step scan.
- `source_image_size`, `processing_image_size`, `diagnostic_pixel_space`:
  capture and dimension-working sizes. Box points, edges, and barcode polygons
  use source pixels; fit/shift diagnostics, reference alignment, and the saved
  change mask use processing pixels.

Each close-up attempt has a `label-<id>/` directory with its image, metadata,
and ID record. An accepted two-step measurement also includes copies of
`label.jpg` and `label.json` alongside `box.jpg`. The original pending-label
record remains as evidence; it is not an accepted measurement.

Edits replace the JSON file atomically. The vision-only `review/result.json`
does not contain the barcode or later ID edits. Use the top-level result as
the combined record.

**Marked result** displays `review/scan.jpg`: the thicker box outline and
measurement guides plus polygons around all decoded barcode locations. Pink
marks the selected barcode; orange marks other candidates. Numbered labels map
to the candidate values below the image. The selected package ID is shown in
full in that panel, with manual entries explicitly labelled. Up to four
candidate values are listed; additional values remain available in Choose ID.
For a separate label, the highlighted close-up is shown below the station
image; its barcode coordinates are never drawn on the station photograph.
The marked image is regenerated from the same vision image whenever an ID is
saved, so it also refreshes if it is already open. An overlay-write failure
keeps the authoritative JSON/ID saved, shows a warning, and falls back to the
vision-only image rather than displaying an old ID.

## Offline diagnostic command

To test a saved image without opening the camera or the touchscreen:

```bash
.venv/bin/parcel-dimensioner barcode-read --image data/captures/<session>/scan-0001/box.jpg
```

This prints JSON. Exit status 0 means candidates were found, 1 means no barcode
was decoded, and 2 means decoding was unavailable or the image could not be
read. Unlike the app, this standalone command searches the entire image, so
check whether a returned value belongs to a background object. It does not
modify the image or saved scan.

## First physical checks

1. Try a real single-code shipping label at the normal package position; verify
   the exact decoded value against the label, including leading zeroes.
2. Try a label with multiple different codes: confirm no automatic selection,
   select one, and check the top-level JSON.
3. Try repeated identical values: they should form one candidate.
4. Turn/hide the label: dimensions/weight should remain usable, and manual ID
   entry should save correctly without retaking the image.
5. Measure a different box after manual entry: its ID must start fresh.
6. Check a background box with a barcode: it should not be selected for the
   package being measured.

Generated-image tests verify software behavior. They do not establish a field
read rate. The three previously supplied station photographs did not decode in
the initial probe; representative shipping labels still need on-Pi testing.
Increase resolution only if those tests indicate insufficient barcode detail,
then recapture the empty reference and revalidate dimension measurements.
