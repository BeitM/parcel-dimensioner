"""Real Tk event-loop smoke test using a simulated worker, not Pi hardware."""

import os
import queue
import subprocess
import sys
from pathlib import Path

import pytest


def isolated_ui_case(request):
    """Tk keeps process-wide state; give each UI scenario a fresh interpreter."""
    if os.environ.get("PARCEL_TK_TEST_CHILD") == "1":
        return False
    environment = dict(os.environ, PARCEL_TK_TEST_CHILD="1")
    result = subprocess.run(
        [sys.executable, "-m", "pytest", request.node.nodeid, "-q", "-ra"],
        cwd=Path(__file__).resolve().parents[1],
        env=environment,
        capture_output=True,
        text=True,
        timeout=25,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    if "1 skipped" in result.stdout:
        pytest.skip("Tk unavailable in isolated UI process: " + result.stdout)
    return True


def test_buttons_result_and_error_clear_stale_dimensions(tmp_path, monkeypatch, request):
    if isolated_ui_case(request):
        return
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
                                "weight": {"pounds": 5.0, "status": "measured"},
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
                assert any("5.00 lb" in v for v in values)
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


@pytest.mark.parametrize("width,height", [(480, 853), (720, 1280), (1000, 680)])
def test_layout_fallback_and_saved_weight_separate_from_live(
    tmp_path, monkeypatch, width, height, request
):
    if isolated_ui_case(request):
        return
    tk = pytest.importorskip("tkinter")
    image_module = pytest.importorskip("PIL.Image")
    from tkinter import messagebox

    from parcel_dimensioner import touchscreen
    from parcel_dimensioner.settings import CameraSettings

    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"No display for Tk layout check: {exc}")
    root.overrideredirect(True)
    geometry = root.geometry
    geometry(f"{width}x{height}+0+0")
    monkeypatch.setattr(root, "geometry", lambda _: geometry(f"{width}x{height}+0+0"))
    monkeypatch.setattr(root, "winfo_screenwidth", lambda: width)
    monkeypatch.setattr(root, "winfo_screenheight", lambda: height)
    monkeypatch.setattr(tk, "Tk", lambda: root)
    monkeypatch.setattr(touchscreen, "_vision", lambda: None)
    monkeypatch.setattr(messagebox, "askokcancel", lambda *a, **kw: True)
    preview_image = image_module.new("RGB", (800, 450), "#25485d")
    marked = tmp_path / "marked.jpg"
    preview_image.save(marked)
    commands = []

    class Scale:
        def snapshot(self):
            return {"pounds": 0.0, "stable": True, "message": "Stable"}

    class Worker:
        def __init__(self, *_):
            import threading

            self.events, self.frames = queue.Queue(), queue.Queue()
            self.stopping = threading.Event()
            self.scale = Scale()
            self.commands = self

        def start(self):
            self.events.put(("ready", str(tmp_path)))
            self.frames.put(preview_image)

        def is_alive(self):
            return not self.stopping.is_set()

        def join(self, **_):
            pass

        def put_nowait(self, action):
            commands.append(action)
            if action == "empty":
                self.events.put(("empty", "Empty reference captured and scale zeroed."))
            elif action == "scan" and len(commands) == 2:
                self.events.put(("weight_error", ("USB disconnected", True)))
            else:
                weight = None if isinstance(action, tuple) else 5.0
                self.events.put(
                    (
                        "result",
                        (
                            {
                                "dimensions_inches": {"length": 9, "width": 8, "height": 3},
                                "weight": {"pounds": weight},
                            },
                            marked,
                        ),
                    )
                )

    monkeypatch.setattr(touchscreen, "StationWorker", Worker)

    def descendants(widget):
        for child in widget.winfo_children():
            yield child
            yield from descendants(child)

    def button(text):
        return next(
            w for w in descendants(root) if isinstance(w, tk.Button) and w.cget("text") == text
        )

    def values():
        return [
            root.getvar(w.cget("textvariable"))
            for w in descendants(root)
            if isinstance(w, tk.Label) and w.cget("textvariable")
        ]

    errors = []

    def check(stage):
        try:
            if stage == 0:
                empty, zero = button("Capture empty station"), button("Zero scale")
                measure, review = button("Measure box"), button("Marked result")
                assert empty.grid_info()["column"] == zero.grid_info()["column"] == 0
                assert measure.grid_info()["column"] == review.grid_info()["column"] == 1
                assert button("Exit").winfo_width() == button("Exit").winfo_height() == 56
                for control in (empty, zero, measure, review):
                    x = control.winfo_rootx() - root.winfo_rootx()
                    y = control.winfo_rooty() - root.winfo_rooty()
                    assert x >= 0 and x + control.winfo_width() <= width
                    assert y >= 0 and y + control.winfo_height() <= height
                empty.invoke()
            elif stage == 1:
                button("Measure box").invoke()
            elif stage == 2:
                assert button("Measure dimensions only").winfo_ismapped()
                assert not any("9.00" in v for v in values())
                button("Measure dimensions only").invoke()
            elif stage == 3:
                assert commands[-1] == ("scan", "USB disconnected")
                assert any("Dimensions-only" in v for v in values())
                assert not any(v == "5.00 lb" for v in values())
                button("Measure box").invoke()
            else:
                assert "5.00 lb" in values()
                assert any("Live scale: 0.00 lb" in v for v in values())
                snapshot_dir = os.environ.get("PARCEL_UI_SNAPSHOT_DIR")
                if snapshot_dir and sys.platform == "win32":
                    from PIL import ImageGrab

                    target = Path(snapshot_dir)
                    target.mkdir(exist_ok=True, parents=True)
                    ImageGrab.grab(window=root.winfo_id()).save(target / f"scale-ui-{width}.png")
        except Exception as exc:
            errors.append(exc)
        if stage == 4:
            button("Exit").invoke()

    for stage in range(5):
        root.after(300 + stage * 300, lambda s=stage: check(s))
    root.after(4000, root.destroy)
    touchscreen.launch_touchscreen(CameraSettings(), tmp_path, windowed=True)
    assert not errors, errors
