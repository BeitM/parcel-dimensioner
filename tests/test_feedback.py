import io
import threading
import wave

from parcel_dimensioner.feedback import barcode_chime


def test_missing_player_uses_desktop_bell(monkeypatch):
    from parcel_dimensioner import feedback

    monkeypatch.setattr(feedback.shutil, "which", lambda name: None)
    calls = []
    barcode_chime(lambda: calls.append(True))
    assert calls == [True]


def test_chime_is_a_bounded_wav_played_off_ui_thread(monkeypatch):
    from parcel_dimensioner import feedback

    ready = threading.Event()
    payload = []
    monkeypatch.setattr(feedback.shutil, "which", lambda name: "player")

    def play(args, **kwargs):
        payload.append(kwargs["input"])
        ready.set()

    monkeypatch.setattr(feedback.subprocess, "run", play)
    barcode_chime(lambda: None)
    assert ready.wait(2)
    with wave.open(io.BytesIO(payload[0]), "rb") as wav:
        assert wav.getnchannels() == 1
        assert 0.20 < wav.getnframes() / wav.getframerate() < 0.3
