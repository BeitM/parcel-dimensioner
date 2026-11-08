"""Exercise the actual firmware's ADC routine with simulated GPIO and time."""

import ast
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    "expected",
    [0, 1, -1, 123456, -234567, 8388606, -8388607, -8388608, 8388607, None, "input_error"],
)
def test_adc_decoding_clock_count_and_cleanup(expected):
    source = Path("firmware/pico_scale/main.py").read_text()
    tree = ast.parse(source)
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "read_raw")
    code = compile(ast.Module(body=[function], type_ignores=[]), "pico/read_raw", "exec")
    raw = 123 if expected == "input_error" else expected
    bits = (
        [0] + [(raw & 0xFFFFFF) >> shift & 1 for shift in range(23, -1, -1)]
        if raw is not None
        else []
    )
    edges, restored, elapsed = [], [], [0]

    class Data:
        def value(self):
            if expected == "input_error" and len(bits) == 20:
                raise OSError("input failed")
            return bits.pop(0) if bits else 1

    class Clock:
        def value(self, level):
            edges.append(level)

    def sleep_ms(delay):
        elapsed[0] += delay

    env = {
        "data": Data(),
        "clock": Clock(),
        "ticks_ms": lambda: elapsed[0],
        "ticks_diff": lambda a, b: a - b,
        "sleep_ms": sleep_ms,
        "sleep_us": lambda _: None,
        "disable_irq": lambda: 17,
        "enable_irq": restored.append,
    }
    exec(code, env)
    if expected in (None, "input_error", -8388608, 8388607):
        with pytest.raises(OSError):
            env["read_raw"]()
    else:
        assert env["read_raw"]() == expected
    if expected is not None:
        assert edges[-1] == 0 and restored == [17]
        if expected != "input_error":
            assert edges.count(1) == 25
