# Load-cell soldering and wiring guide

For the photographed hardware: four three-wire load sensors, SparkFun Load
Sensor Combinator v1.1 layout, SparkFun HX711 Load Cell Amp with separate VCC
and VDD terminals, and a Raspberry Pi Pico 2. The Pico will connect to the
Raspberry Pi computer by USB. No soldering is required on the Pi computer.

## 1. Gather tools and choose connections

- Temperature-controlled soldering iron with a small chisel tip, stand, and tip cleaner.
- Thin electronics solder and electronics-compatible flux; no plumbing/acid flux.
- Multimeter, wire stripper suitable for the fine sensor wires, flush cutters,
  helping hands or PCB holder, and magnification if available.
- Flexible insulated hookup wire, heat-shrink for any extensions, and labels.
- The user's Pico already has soldered headers; no additional Pico headers are needed.
- Two 1-by-5, 2.54 mm male headers for the HX711, and one for the combinator's
  amplifier output. These give detachable connections using female jumper leads.
- Nine female-to-female jumper wires: four combinator-to-amplifier and five
  amplifier-to-Pico. Use short wires for the electronics cluster. Secure them
  after testing; ordinary loose jumper connectors are for prototyping.
- A Micro-USB DATA cable to connect the Pico to a Pi USB port.

Recommended assembly: use the Pico's existing headers and solder headers to the two other boards;
solder sensor leads directly into the combinator's twelve sensor holes. Direct
board-to-board wiring is also possible, but use one method per hole rather than
trying to squeeze a header pin and separate wire into the same hole.

Disconnect USB and all other power before soldering or resistance tests. Work
on a heat-resistant surface with ventilation/fume extraction, not directly on
the plywood or near plastic bags. Wear eye protection when clipping leads.
Wash hands after handling solder, particularly lead-containing solder.

## 2. Label the mechanical positions and check wire reach

Viewed from above while standing at the station's open front:

```text
          REAR / patterned-wall corner area
           UL                         UR

           LL                         LR
                   OPEN FRONT
```

Label each sensor and its cable. Put holders near the four platform corners on
the fixed lower base. Plan a protected location for the electronics near the
centre. Leave slack for routing, without hanging wires where the upper platform
can pinch them. Confirm reach before cutting or permanently mounting anything.

If extending a sensor cable, splice each of its three conductors separately.
Label both ends before any colour changes. Slide on heat-shrink BEFORE making
each splice, solder the joint, insulate each conductor individually, and secure
the cable to the base so force does not pull on the fine sensor leads. Avoid
long unsupported bare wire, twisted-only joins, and tape-only permanent joints.
Use similar extension wire and comparable lengths where practical; test and
calibrate with the final wiring installed.

## 3. Make a solder joint

Practice on spare wire or perfboard first if this is your first soldering job.
Use a tip temperature appropriate to the solder manufacturer's recommendation;
around 350 degrees C is a common electronics starting point, not a universal
setting. Do not hold the iron on a sensor lead until its insulation melts.

Clean and lightly tin the tip. Heat the pad and lead together, feed a small
amount of solder into that heated junction, withdraw the solder, then the iron.
Hold still until solid. Look for solder wetting both surfaces with a modest
fillet, not a loose ball or a bridge to the next pad. Lead-free joints can be
matte; shininess alone does not establish quality. If solder will not flow,
stop, let the pad cool, clean/add suitable flux, and retry rather than heating
it indefinitely. See [Adafruit's joint-making guide](https://learn.adafruit.com/adafruit-guide-excellent-soldering/making-a-good-solder-joint).

For wire-to-board joints, strip only enough insulation for the hole and joint,
lightly tin the strands, insert, solder, and trim excessive protruding wire.
Keep stray strands out of neighbouring pads. Never solder onto the load
sensor's flexing metal or strain-gauge assembly; use its existing leads.

## 4. Solder the headers

For each header, put the long pins in a breadboard or fixture to hold them
straight, leaving the short ends through the PCB. Keep the plastic spacer
against the board. Solder one end pin, check alignment, then solder the opposite
end before filling in the rest. Keep heat on the PCB joint, not the breadboard.

Pico: this unit is already headered, so skip soldering it. A bare replacement
would need two 20-pin strips. For the photographed assembly, only three five-pin
sections (15 pins total) are needed across the amplifier and combinator.

HX711: fit one five-pin strip on each side, matching the existing labelled holes.
Combinator: fit one five-pin strip to the row labelled E+, E-, A-, A+, SLD.
Leave RJ45, DS18B20, and the four three-hole sensor groups free of headers for
the recommended direct-sensor-lead arrangement. Select header direction so the
boards can sit on insulating standoffs with connector access and platform clearance.

## 5. Identify the sensor centre-tap wires BEFORE soldering them

For EACH disconnected sensor, measure all three pairs in resistance/ohms mode:

| Sensor | Red-black | Red-white | Black-white | Centre-tap colour |
| --- | --- | --- | --- | --- |
| UL | | | | |
| UR | | | | |
| LL | | | | |
| LR | | | | |

The pair with the largest resistance contains the two outer wires. The third
wire is the centre tap; its resistance to either outer wire should be roughly
half the largest reading. Exact resistance depends on the sensor model.

Example only: black-white approximately 2 kilohms, red-black approximately
1 kilohm, red-white approximately 1 kilohm would identify RED as the centre tap.
Do not assume those resistance values or that all sensors share a colour scheme.
Resistance identifies the centre tap; it does NOT identify which outer wire
has the positive load-response polarity. SparkFun's common convention is black
minus, white plus, red centre. If your measurements do not identify red as centre,
record the results and establish the remaining assignments from the sensor's
documentation or a controlled load-response test before soldering permanently.
[SparkFun combinator schematic](https://cdn.sparkfun.com/datasheets/Sensors/ForceFlex/SparkFun%20Load%20Sensor%20Combinator%20v11.pdf).

## 6. Solder the four sensors to the combinator

Orient the board AS IN THE USER'S PHOTO: the face reading Load Combinator,
large mounting holes at the top, UL/UR/LL/LR columns left to right, and the
E+/E-/A-/A+/SLD row at the bottom. This is important: looking at the opposite
face mirrors left and right.

```text
                 UL       UR       LL       LR
top hole          -        -        -        -
middle hole       +        +        +        +
bottom hole       C        C        C        C

                 E+   E-   A-   A+   SLD
```

Centre tap goes in the BOTTOM hole of each column, NOT the middle hole. Mark
the selected holes before turning the board over to solder. Wire one complete
sensor into its matching column. A sensor must not be split between columns.

If the centre tap is confirmed RED and the sensors use SparkFun's common
polarity convention, connect BLACK to top (-), WHITE to middle (+), and RED to
bottom (C). Inspect each column before proceeding to the next. This is
conditional on identifying the actual sensor leads, not a colour-only rule.

The row order was checked against the manufacturer's
[v1.1 PCB design](https://github.com/sparkfun/Load_Sensor_Combinator/blob/master/Hardware/SparkFun%20Load%20Sensor%20Combinator.brd).
Later PCB revisions may label things differently; follow their documented layout.

## 7. Connect combinator to amplifier

Use four short female-to-female jumper leads between soldered headers, following
the LABELS rather than matching the boards' physical left/right orientation:

| Combinator terminal | HX711 terminal |
| --- | --- |
| E+ | RED |
| E- | BLK |
| A- | WHT |
| A+ | GRN |

Leave SLD and YLW disconnected for these unshielded sensor leads. These
amplifier colour labels refer to a complete bridge, not individual sensor wire
colours. The combiner receives sensor excitation through this connection;
it has no separate power lead in this setup.

## 8. Connect amplifier to Pico 2

Use five female-to-female jumper leads. Pico physical pin numbers below are NOT
the same as GPIO numbers:

| HX711 terminal | Pico pin NAME | Pico PHYSICAL pin | Suggested jumper colour |
| --- | --- | ---: | --- |
| VDD | 3V3 OUT | 36 | Orange |
| VCC | VBUS (USB 5 V) | 40 | Red |
| DAT | GP2 | 4 | Blue |
| CLK | GP3 | 5 | Yellow |
| GND | GND | 3 | Black |

With the Pico's component side facing you and USB at the top, physical pin 1
is top left and pin 40 is top right. Left pins count downward 1 through 20;
right pins count upward 21 through 40. Use the
[official pinout](https://datasheets.raspberrypi.com/pico/Pico-2-Pinout.pdf)
while making connections, particularly if soldering from the back.

VCC supplies the amplifier; VDD sets its logic voltage. In THIS arrangement,
VCC receives USB 5 V and VDD receives the Pico's 3.3 V. Do not connect VDD to
VBUS, bridge VCC and VDD together, or put 5 V on GP2/GP3. This follows the
[SparkFun amplifier schematic](https://cdn.sparkfun.com/datasheets/Sensors/ForceFlex/SparkFun_HX711_Load_Cell_v11.pdf).
GP2 and GP3 are chosen assignments for the future firmware, not mandatory HX711 pins.

## 9. Inspect, meter-check, and mount

With ALL power still disconnected:

- Inspect every joint for bridges, loose strands, bare splices, and missed pins.
- Gently check that wires are retained; avoid pulling on the sensor itself.
- Verify each end-to-end connection against the two tables. Continuity mode is
  useful for confirming that a wire reaches the intended terminal.
- Check for a sustained near-zero-ohm short from VBUS/VCC to GND, 3V3/VDD to
  GND, or VCC to VDD. A brief changing reading from charging capacitors is not
  by itself a short; continuity behaviour varies by meter. Investigate a steady
  short before plugging in USB.
- Confirm each sensor's centre tap is in C and that the nine board-to-board
  jumpers have not been offset by one pin.

Mount the electronics to the fixed lower base with insulating standoffs or
enclosures. Keep PCB undersides off metal and screws, and leave room above the
boards/connectors so the weighed platform cannot touch or crush them. Secure
wire bundles to the lower base without clamping a sensor's moving section.
The upper platform must load only the four intended sensor contact points,
not the holder rims, electronics, cables, or fixed frame.

## 10. USB connection and commissioning

Connect a Micro-USB DATA cable from the Pico to a Raspberry Pi USB-A port.
This is the ONLY connection from this scale-electronics assembly to the Pi:
it supplies power and will carry readings. Do not solder anything to the Pi's
GPIO header for this arrangement. No separate external supply is needed.

If something becomes hot, smells abnormal, or the Pi reports a USB power fault,
disconnect the Pico USB immediately and recheck the wiring before retrying.
Avoid repeatedly powering a suspected short.

Wiring alone does not produce a weight display. Pico weighing firmware and
Box Scanner weight integration are now provided; follow [scale setup](scale.md)
to install the startup program. Commissioning is: verify USB communication;
read HX711 raw counts; apply gentle load at each
sensor and check consistent response; resolve polarity or mounting problems;
tare the empty platform/walls; calibrate with a reliable known weight; test
the same weight at the centre and corners. A reading that goes negative overall
can be a sign convention; one corner acting oppositely calls for checking that
sensor's wiring/orientation. Never swap powered wires.

Do not set a maximum parcel capacity by adding the four sensor ratings. The
corner-loaded platform and its own dead weight require a separate capacity
assessment and position testing.
