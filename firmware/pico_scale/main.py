"""MicroPython on Pico 2: HX711 DAT=GP2, CLK=GP3, USB JSON lines at about 10 Hz.

Copy to the Pico as main.py, then power-cycle it. Calibration/zero live on the Pi.
"""

import json
from time import sleep_ms, sleep_us, ticks_diff, ticks_ms

from machine import Pin, disable_irq, enable_irq, unique_id
from ubinascii import hexlify

PROTOCOL = "parcel-scale-v1"
DEVICE = hexlify(unique_id()).decode()
data = Pin(2, Pin.IN, Pin.PULL_UP)
clock = Pin(3, Pin.OUT, value=0)


def reset_adc():
    clock.value(1)
    sleep_us(100)
    clock.value(0)
    sleep_ms(500)


def read_raw():
    start = ticks_ms()
    while data.value():
        if ticks_diff(ticks_ms(), start) > 2000:
            raise OSError("HX711 not ready: check DAT=GP2, CLK=GP3 and power")
        sleep_ms(1)
    value = 0
    state = disable_irq()
    try:
        for _ in range(24):
            clock.value(1)
            sleep_us(1)
            value = (value << 1) | data.value()
            clock.value(0)
            sleep_us(1)
        clock.value(1)  # Pulse 25: next conversion channel A, gain 128.
        sleep_us(1)
        clock.value(0)
    finally:
        clock.value(0)
        enable_irq(state)
    if value & 0x800000:
        value -= 0x1000000
    if value in (-8388608, 8388607):
        raise OSError("HX711 saturated: check sensor/combinator wiring")
    return value


def main():
    reset_adc()
    sequence = 0
    while True:
        try:
            raw = read_raw()
            print(
                json.dumps(
                    {
                        "protocol": PROTOCOL,
                        "device": DEVICE,
                        "seq": sequence,
                        "ticks_ms": ticks_ms(),
                        "raw": raw,
                    }
                )
            )
            sequence += 1
            sleep_ms(20)  # Limit USB traffic even if the ADC is configured for 80 Hz.
        except OSError as exc:
            print(json.dumps({"protocol": PROTOCOL, "device": DEVICE, "error": str(exc)}))
            reset_adc()
            sleep_ms(500)


main()
