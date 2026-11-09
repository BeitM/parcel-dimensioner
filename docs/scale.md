# Pico scale setup and combined scanning

The Pico 2 reads the HX711 and streams raw counts over its USB data cable.
The Pi app handles filtering, stable readings, explicit zeroing, pounds, and
pairing a weight with the box image. It works offline. The Pico firmware starts
automatically on power-up; the touchscreen app still opens from its desktop icon.

## Hardware used

Four three-wire sensors -> SparkFun combinator -> SparkFun HX711 -> Pico 2 ->
Pi USB-A port. Pico DAT is GP2 (physical 4), CLK is GP3 (physical 5), and GND is
physical 3. HX711 VDD goes to Pico 3V3 OUT (36); VCC goes to VBUS (40). Keep the
separate 3.3 V logic and 5 V analog connections. Power off USB before rewiring.
See [the soldering guide](load-cell-soldering.md) for the complete connections.

## One-time software installation

First commit/push the implementation from the laptop and pull it on the Pi,
as described in [the touchscreen guide](touchscreen.md#transfer-the-changes-from-the-laptop).
The following are **Pi shell commands**, including when run through SSH.
Close Box Scanner and stop the old scale test with Ctrl+C first.

```bash
cd ~/parcel-dimensioner
git pull --ff-only
.venv/bin/python -m pip install -e '.[touchscreen]'
.venv/bin/python -m pip install mpremote
```

MicroPython is already installed if `/dev/ttyACM0` appeared after the earlier
raw-count test. Otherwise install the correct [Pico 2 MicroPython UF2](https://micropython.org/download/RPI_PICO2/)
first. This firmware is for **Pico 2**, not the Pi computer's operating system.

Copy the startup program to the Pico. This writes/replaces the Pico's `main.py`;
the earlier temporary `scale-test.py` was run from the Pi and was not installed
as a startup program.

```bash
.venv/bin/mpremote connect /dev/ttyACM0 fs cp firmware/pico_scale/main.py :main.py
```

Unplug **only the Pico USB cable**, wait a moment, then plug it back into the Pi
normally, without holding BOOTSEL. Leave the Pi running. No Pi reboot is needed.
Do not leave `mpremote` or Thonny attached while the app is using the scale.
The [official mpremote documentation](https://docs.micropython.org/en/latest/reference/mpremote.html)
explains copying files and accessing the USB serial connection.

Open **Box Scanner** on the Pi desktop or Pi Connect screen share. The existing
desktop shortcut is still valid. Alternatively, from a Pi desktop terminal:

```bash
cd ~/parcel-dimensioner
.venv/bin/parcel-dimensioner touchscreen --lens-position 1.018378
```

## Operator workflow

1. Leave the platform and attached walls installed; remove packages and hands.
2. Tap **Capture empty station**. This captures the camera reference and zeros
   the scale. **More > Zero scale** can subsequently zero an empty platform without
   replacing the image reference. Both buttons act immediately, without a
   confirmation popup; clear the platform before tapping either button.
3. Hold the barcode close to the camera until **Barcode read** appears, then
   place that same box against both walls, take hands off, and tap **Measure box**.
   To skip barcodes explicitly, use **More > Measure without barcode**.
4. The app waits up to 10 seconds for a stable weight, then captures the box and
   verifies a fresh reading after capture. Motion, sensor errors, stale data,
   and disconnection prevent an accepted combined scan.
5. Read inches and **Saved weight** in pounds. The scale status during label
   presentation is a separate live reading. Removing the box never changes the
   accepted result. Tap **Next box** when ready to start another package.
6. On a weight error, fix it and retry, or explicitly choose **Measure dimensions
   only**. This creates a fresh scan with `weight.pounds: null` and a reason.

The app never zeroes a package automatically. Every app restart, USB reconnect,
Pico restart, ADC error, or significant sample gap requires explicit zeroing.
Calibration persists in settings; the session zero does not persist.

## Calibration and stability settings

The `[scale]` section in `config/parcel-dimensioner.toml` defaults to:

```toml
[scale]
port = "auto"
counts_per_pound = -10640.0
calibration_id = "prototype-20261107-5lb-10lb"
stable_seconds = 1.0
stable_range_lb = 0.03
timeout_seconds = 10.0
freshness_seconds = 0.5
```

Pounds = (raw counts - session zero) / counts_per_pound. The negative factor
matches this station's decreasing counts under added load. It was estimated
from the user's 5 lb and 10 lb tests; it is not a factory calibration for other
sensors. Accuracy depends on the actual reference weights and mechanics.
Change both factor and calibration ID when recalibrating, then restart the app.
If using `--config`, put `[scale]` in that file instead.

Stability means at least six consecutive samples spanning at least one second,
with a raw peak-to-peak range no greater than 0.03 lb after conversion. The
latest sample must be within 0.5 seconds. A shorter rolling mean is shown live.
The saved weight is averaged from the stable window immediately before capture;
samples across capture are checked for changes. This pairs measurements in one
operator action, rather than claiming hardware-synchronized camera/ADC clocks.

Auto connection selects one unambiguous Raspberry Pi USB serial device and
verifies the firmware protocol. It never chooses an unrelated serial adapter.
For multiple Picos, set `port` to the scale's `/dev/serial/by-id/...` path, found
with `ls -l /dev/serial/by-id/`. The [pySerial port tools](https://pyserial.readthedocs.io/en/latest/tools.html#serial.tools.list_ports)
provide USB identification. Only one process may own the scale port.

## Saved results

The top-level `scan-NNNN/result.json` contains the existing dimension result plus:

- `scan_mode`: `dimensions_and_weight` or `dimensions_only`;
- `weight.pounds`, unit, status, and stable flag;
- raw average, zero counts/time, sample time/count, and stability range;
- counts-per-pound factor, calibration ID, Pico device ID, USB port, and protocol;
- capture verification time and range, or the explicit missing-weight reason.

The `review/result.json` remains the vision-only report. Failed attempts have
`error.txt` and `attempt.json`; they are not accepted combined records. Failed
weight attempts before the photograph may have no `box.jpg`.

## First on-device checks

1. Empty/reference capture: live scale is near zero and shows Stable.
2. Known 5 lb and 10 lb loads: compare app pounds with the earlier terminal tests.
3. Repeat at the centre and all corners. The earlier observed 10 lb readings
   varied by about 0.21 lb across corners; this implementation does not fix that.
4. Run a complete box scan; inspect its top-level JSON for dimensions and weight.
5. Remove the box: Saved weight remains unchanged. After Next box, the scale
   status should return near zero.
6. Disconnect Pico USB: live weight becomes unavailable; retrying a measurement
   offers an explicit dimensions-only choice. Its saved JSON must have null weight.
7. Reconnect: it must require Zero scale. Restarting the app must also require
   a new empty image and zero.
8. Shift/remove the box during capture: the app should reject changed weight.

No maximum parcel capacity is inferred from these small-load tests. Two decimal
places in the display do not establish 0.01 lb accuracy.

## Troubleshooting

- **Scale support missing:** repeat the touchscreen extra installation.
- **No data / unexpected protocol:** install `firmware/pico_scale/main.py` and
  power-cycle the Pico. The old raw test prints a different format.
- **Port busy:** close Thonny, `mpremote`, and any second Box Scanner instance.
- **Permission denied:** check `id -nG`. If `dialout` is missing, run
  `sudo usermod -aG dialout box-scanner`, then log out of the Pi desktop and back
  in (a reboot also applies the group). Starting another SSH session alone does
  not update the existing desktop's group membership.
- **Needs zero after reconnect:** intentionally remove the package and tap Zero scale.
- **HX711 not ready / saturated:** unplug Pico USB and check wiring; do not
  bypass the error or accept its old weight.
- **Never stable:** remove hands, check rubbing/cables/rocking, and inspect raw
  samples before loosening the stability threshold.
