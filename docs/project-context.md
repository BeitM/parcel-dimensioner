# Recovered project decisions

Reviewed the complete available turns in these chats on 2026-10-10:

- Plan CubiScan box scanner: 019ff8ac-682f-7611-ad61-ae68007f7fa2
- Find project information: 01a07884-f75e-7222-9097-448d0bd2d0c9
- Plan initial project structure: 01a0d5af-c214-7c82-b769-5aeaf65dcd1f

This note distinguishes operator requirements, historical proposals, and things
still requiring physical verification. Older assistant performance estimates are
targets, not measurements or guarantees.

## Intended product

- A low-cost CubiScan-style station for manually placed rigid rectangular boxes.
- Capture length, width, height, weight, and a visible barcode in one convenient
  operator workflow. No conveyor or bag measurement in the first version.
- One fixed oblique camera near the open diagonal corner, looking down toward the
  intersection of two datum fences. It needs to see the top and two exposed faces.
- The operator seats the box against both fences, releases it, and triggers Scan.
- Known floor/wall geometry constrains a 3D cuboid fit. This is not an unconstrained
  single-image depth estimate, or simply counting occluded checker squares.
- ChArUco patterns remain installed as station references. Lens calibration also
  needs varied board views; permanent walls alone are not the planned collection.

## Scope changes

The original volume was 24 x 24 x 36 inches with a 100 lb design maximum and common
loads nearer 50 lb. Later conversations explored horizontal 36-inch layouts and
then reduced the footprint. The latest pattern-generation instructions specify
24 inches long x 18 inches deep x 18 inches high. Treat that as the current planned
scope pending validation of full-volume framing and visible marker coverage.

The suggested initial dimensional target was +/- 1/4 inch (6.35 mm), with +/- 1/8
inch an improvement goal. Suggested 1-3 second cycles and scale display increments
were not tested results. Confirm the actual acceptance requirements before use.

## Hardware baseline

- Raspberry Pi 5 and Camera Module 3 Wide were selected in the purchasing plan.
- Pi and Touch Display 2 are mounted together; preserve the short camera ribbon
  reach when positioning the display/computer assembly near the camera.
- Purchased-parts photo review reported a 5-inch display, Pico 2, four three-wire
  50 kg load sensors, SparkFun combinator and HX711, momentary Scan button, LED bars,
  and a protective scale-electronics enclosure.
- The photo review could not conclusively verify camera Wide variant, Pi RAM, or
  ribbon length. Verify labels before relying on those exact specifications.
- Pico owns scale acquisition, filtering/stability, tare, and the physical button.
  Main Pi owns images, dimension fitting, barcode decoding, UI, and record storage.
- No dedicated barcode reader or additional depth sensor was selected for the
  baseline; consider changes only if representative tests expose a limitation.

## Received board definitions

Sources inspected: C:/Users/beitl/Downloads/longwall.pdf and shortwall.pdf.

| Parameter | Long wall | Short wall |
|---|---|---|
| Page size | 609.6 x 457.2 mm | 457.2 x 609.6 mm |
| Columns x rows | 15 x 11 | 11 x 15 |
| Checker size in PDF footer | 38.1 mm | 38.1 mm |
| Marker size in PDF footer | 28 mm | 28 mm |
| Dictionary family in PDF | ArUco DICT_6X6 | ArUco DICT_6X6 |
| Planned dictionary from chat | DICT_6X6_1000 | DICT_6X6_1000 |
| Planned starting ID | 0 | 200 (also printed in PDF footer) |
| Planned legacy layout | Off | Off |
| Initial bottom elevation | 6 inches above support surface | At support surface |

The historical instructions suggested 28.6 mm markers if manually specified, but
the actual supplied PDFs label them 28 mm. Use the supplied artifact and measured
print, not that earlier suggested value. Verify decoded marker IDs and actual
marker geometry before saving the detector configuration; the footer names only
the dictionary family, not its full capacity. PDFs specify nominal dimensions,
not the delivered boards' measured dimensions or print-scale accuracy.

## Mechanical inconsistency to resolve

The original detailed design explicitly attaches both fences and their braces to
the floating weighing deck; the fixed base owns the camera, lights, screen, and
Pi. Tare removes the deck/fence dead weight. This avoids the parcel transferring
part of its weight into fixed fences.

A later structural layout instead describes fences isolated from the weighing
deck. These are inconsistent descriptions. Keep the original weighed-fence design
as the baseline, verify the actual build, and do not assume fixed-wall contact is
harmless. Check off-centre loads, wall contact, cable forces, flex, and pose changes.

The elevated long-board print is a reference panel, not a complete lower physical
fence. Provide registration rails/supports and measure their relationship to the
printed patterns and parcel origin. Pattern face, protective rail contact plane,
and support surface may have nonzero offsets.

## Next implementation

1. Configure and detect both actual boards in saved images; annotate IDs/corners.
2. Capture varied views for lens calibration with stable imaging settings.
3. Establish both panels' measured relation to the support plane and datum corner.
4. Prove three-dimensional box geometry using manually selected corners.
5. Automate edge detection, then integrate scale, barcode, and operator workflow.

Do not change the historical purchase PDFs or the supplied board PDFs unless the
operator asks. The implementation roadmap is in project-roadmap.md.
