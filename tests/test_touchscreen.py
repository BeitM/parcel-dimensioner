"""Real Tk event-loop smoke test using a simulated worker, not Pi hardware."""

import queue

import pytest


def test_buttons_result_and_error_clear_stale_dimensions(tmp_path, monkeypatch):
    tk = pytest.importorskip("tkinter")
    image_module = pytest.importorskip("PIL.Image")
    from tkinter import messagebox

    from parcel_dimensioner import touchscreen
    from parcel_dimensioner.settings import CameraSettings

    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("No desktop display available for the Tk smoke test")
    root.withdraw()
    monkeypatch.setattr(tk, "Tk", lambda: root)
    monkeypatch.setattr(touchscreen, "_vision", lambda: None)
    monkeypatch.setattr(messagebox, "askokcancel", lambda *a, **kw: True)
    marked = tmp_path / "marked.jpg"
    image_module.new("RGB", (640, 360), "gray").save(marked)

    class Stop:
        stopped = False

        def set(self):
            self.stopped = True

    class Worker:
        def __init__(self, *_):
            self.events = queue.Queue()
            self.frames = queue.Queue()
            self.stopping = Stop()
            self.commands = self
            self.scans = 0

        def start(self):
            self.events.put(("ready", str(tmp_path)))

        def is_alive(self):
            return not self.stopping.stopped

        def join(self, **_):
            pass

        def put_nowait(self, action):
            if action == "empty":
                self.events.put(("empty", None))
            elif self.scans == 0:
                self.scans += 1
                self.events.put(
                    (
                        "result",
                        (
                            {
                                "dimensions_inches": {"length": 9.0, "width": 8.0, "height": 3.0},
                                "height_by_wall_inches": {"long": 3.0, "short": 3.0},
                            },
                            marked,
                        ),
                    )
                )
            else:
                self.events.put(("error", ("No changed box found", True)))

    monkeypatch.setattr(touchscreen, "StationWorker", Worker)

    def descendants(widget):
        for child in widget.winfo_children():
            yield child
            yield from descendants(child)

    def button(text):
        return next(
            w for w in descendants(root) if isinstance(w, tk.Button) and w.cget("text") == text
        )

    errors = []

    def check(stage):
        try:
            if stage == 0:
                assert button("Measure box").cget("state") == "disabled"
                button("Capture empty station").invoke()
            elif stage == 1:
                assert button("Measure box").cget("state") == "normal"
                button("Measure box").invoke()
            elif stage == 2:
                values = [
                    root.getvar(w.cget("textvariable"))
                    for w in descendants(root)
                    if isinstance(w, tk.Label) and w.cget("textvariable")
                ]
                assert any("9.00" in v for v in values)
                button("Marked result").invoke()
                button("Measure box").invoke()
            else:
                values = [
                    root.getvar(w.cget("textvariable"))
                    for w in descendants(root)
                    if isinstance(w, tk.Label) and w.cget("textvariable")
                ]
                assert not any("9.00" in v for v in values)
                assert any("No changed box" in v for v in values)
                assert button("Measure box").cget("state") == "normal"
        except Exception as exc:
            errors.append(exc)
        if stage == 3:
            button("Exit").invoke()

    for stage in range(4):
        root.after(300 + stage * 300, lambda s=stage: check(s))
    root.after(4000, root.destroy)  # bound any event-loop failure
    touchscreen.launch_touchscreen(CameraSettings(), tmp_path, windowed=True)
    assert not errors, errors
