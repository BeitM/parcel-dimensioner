"""Short success chime using the Pi's configured audio output, if available."""

import io
import math
import shutil
import struct
import subprocess
import threading
import wave
from contextlib import suppress


def barcode_chime(bell):
    player = shutil.which("paplay") or shutil.which("aplay")
    if player is None:
        bell()
        return

    def play():
        data = io.BytesIO()
        with wave.open(data, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(22050)
            samples = []
            for frequency in (880, 1175):
                count = int(22050 * 0.12)
                for i in range(count):
                    envelope = min(1, i / 220, (count - i) / 440)
                    samples.append(
                        struct.pack(
                            "<h",
                            round(9000 * envelope * math.sin(2 * math.pi * frequency * i / 22050)),
                        )
                    )
            wav.writeframes(b"".join(samples))
        with suppress(OSError, subprocess.TimeoutExpired):
            subprocess.run(
                [player],
                input=data.getvalue(),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=2,
                check=False,
            )

    threading.Thread(target=play, daemon=True).start()
