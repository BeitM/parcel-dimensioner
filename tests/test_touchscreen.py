"""Real Tk workflows with a simulated camera worker, isolated per interpreter."""

import os
import queue
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest


def isolated_ui_case(request):
    if os.environ.get("PARCEL_TK_TEST_CHILD") == "1":
        return False
    result = subprocess.run(
        [sys.executable, "-m", "pytest", request.node.nodeid, "-q", "-ra"],
        cwd=Path(__file__).resolve().parents[1],
        env=dict(os.environ, PARCEL_TK_TEST_CHILD="1"),
        capture_output=True,
        text=True,
        timeout=25,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    if "1 skipped" in result.stdout:
        pytest.skip("Tk unavailable: " + result.stdout)
    return True


def label_result(value="PKG-42", capture="label-1"):
    return {
        "scan_id": capture,
        "package_id": value,
        "package_id_source": "barcode",
        "barcode": {
            "status": "found",
            "selected_index": 0,
            "candidates": [{"value": value, "formats": ["Code 128"]}],
        },
    }


def ui(tmp_path, monkeypatch, width=480, height=853):
    tk = pytest.importorskip("tkinter")
    image = pytest.importorskip("PIL.Image")
    from parcel_dimensioner import touchscreen

    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("No display for Tk tests")
    root.overrideredirect(True)
    geometry = root.geometry
    monkeypatch.setattr(root, "geometry", lambda value: geometry(f"{width}x{height}+0+0"))
    monkeypatch.setattr(root, "winfo_screenwidth", lambda: width)
    monkeypatch.setattr(root, "winfo_screenheight", lambda: height)
    monkeypatch.setattr(tk, "Tk", lambda: root)
    monkeypatch.setattr(touchscreen, "_vision", lambda: None)
    sounds, commands = [], []
    monkeypatch.setattr(touchscreen, "barcode_chime", lambda bell: sounds.append(True))
    review = tmp_path / "marked.jpg"
    image.new("RGB", (800, 450), "#eeeeee").save(review)
    shared = SimpleNamespace(on_command=lambda action: None, review=review)

    class Worker:
        def __init__(self, *args):
            self.events, self.frames = queue.Queue(), queue.Queue()
            self.stopping, self.paused = threading.Event(), threading.Event()
            self.commands = self
            self.scale = SimpleNamespace(
                snapshot=lambda: {"pounds": 0, "stable": True, "message": "Stable"}
            )
            shared.worker = self

        def start(self):
            self.events.put(("ready", str(tmp_path)))
            self.frames.put(image.new("RGB", (800, 450), "#25485d"))

        def put_nowait(self, action):
            commands.append(action)
            shared.on_command(action)

        def is_alive(self):
            return not self.stopping.is_set()

        def join(self, **kwargs):
            pass

    monkeypatch.setattr(touchscreen, "StationWorker", Worker)

    def descendants(widget):
        for child in widget.winfo_children():
            yield child
            yield from descendants(child)

    def button(text):
        root.update_idletasks()
        return next(
            w
            for w in descendants(root)
            if isinstance(w, tk.Button) and w.cget("text") == text and w.winfo_ismapped()
        )

    def texts():
        return [
            root.getvar(w.cget("textvariable"))
            for w in descendants(root)
            if isinstance(w, tk.Label) and w.cget("textvariable")
        ]

    def snapshot(name):
        directory = os.environ.get("PARCEL_UI_SNAPSHOT_DIR")
        if directory and sys.platform == "win32":
            from PIL import ImageGrab

            Path(directory).mkdir(parents=True, exist_ok=True)
            dialogs = [child for child in root.winfo_children() if isinstance(child, tk.Toplevel)]
            target = dialogs[-1] if dialogs else root
            ImageGrab.grab(window=target.winfo_id()).save(Path(directory) / name)

    def run(check, stages):
        from parcel_dimensioner.settings import CameraSettings

        errors = []

        def tick(stage):
            try:
                check(stage)
            except Exception as exc:
                errors.append(exc)
            if stage == stages - 1:
                for child in list(root.winfo_children()):
                    if isinstance(child, tk.Toplevel):
                        child.destroy()
                button("Exit").invoke()

        for stage in range(stages):
            root.after(350 + stage * 350, lambda s=stage: tick(s))
        root.after(stages * 350 + 2200, root.destroy)
        touchscreen.launch_touchscreen(CameraSettings(), tmp_path, windowed=True)
        assert not errors, errors

    return SimpleNamespace(
        root=root,
        tk=tk,
        image=image,
        shared=shared,
        commands=commands,
        sounds=sounds,
        button=button,
        texts=texts,
        descendants=descendants,
        snapshot=snapshot,
        run=run,
    )


@pytest.mark.parametrize("width,height", [(480, 853), (720, 1280), (1000, 680)])
def test_automatic_label_measure_and_next_box_layout(tmp_path, monkeypatch, request, width, height):
    if isolated_ui_case(request):
        return
    app = ui(tmp_path, monkeypatch, width, height)
    record = {
        **label_result(),
        "scan_id": "scan-1",
        "dimensions_inches": {"length": 9, "width": 8, "height": 3},
        "weight": {"pounds": 5},
    }

    def command(action):
        events = app.shared.worker.events
        if action == "empty":
            events.put(("empty", "Ready"))
            events.put(("listening", True))
        elif action == "scan":
            events.put(("listening", False))
            events.put(("result", (record, app.shared.review)))
        elif action == "next":
            events.put(("next", None))
            events.put(("listening", True))

    app.shared.on_command = command

    def check(stage):
        if stage == 0:
            visible = [
                w.cget("text")
                for w in app.descendants(app.root)
                if isinstance(w, app.tk.Button) and w.winfo_ismapped()
            ]
            assert "Scan label" not in visible and "Zero scale" not in visible
            assert visible == ["Exit", "Capture empty station", "More"] or set(visible) == {
                "Exit",
                "Capture empty station",
                "More",
            }
            app.button("Capture empty station").invoke()
        elif stage == 1:
            assert "1 · Show barcode" in app.texts()
            assert app.button("Waiting for barcode").cget("state") == "disabled"
            app.snapshot(f"listening-{width}.png")
            app.shared.worker.events.put(("label", label_result()))
        elif stage == 2:
            assert "ID: PKG-42" in app.texts()
            assert "Barcode read" in app.texts()
            assert app.sounds == [True]
            app.snapshot(f"barcode-read-{width}.png")
            app.button("Measure box").invoke()
        elif stage == 3:
            assert "5.00 lb" in app.texts() and any("9.00" in text for text in app.texts())
            assert "Measurement saved" in app.texts()
            for widget in app.descendants(app.root):
                if isinstance(widget, app.tk.Button) and widget.winfo_ismapped():
                    x, y = (
                        widget.winfo_rootx() - app.root.winfo_rootx(),
                        widget.winfo_rooty() - app.root.winfo_rooty(),
                    )
                    assert x >= 0 and x + widget.winfo_width() <= width
                    assert y >= 0 and y + widget.winfo_height() <= height
            app.snapshot(f"result-{width}.png")
            app.button("Marked result").invoke()
            app.button("Next box").invoke()
        else:
            assert not any("PKG-42" in text for text in app.texts())
            assert not any("9.00" in text for text in app.texts())
            assert app.sounds == [True]
            assert app.commands == ["empty", "scan", "next"]

    app.run(check, 5)


def test_more_pauses_reader_and_manual_id_keyboard(tmp_path, monkeypatch, request):
    if isolated_ui_case(request):
        return
    app = ui(tmp_path, monkeypatch)

    def command(action):
        if action == "empty":
            app.shared.worker.events.put(("empty", "Ready"))
        elif action[0] == "manual_id":
            assert action[1] == "42"
            app.shared.worker.events.put(
                ("label", {**label_result("42"), "package_id_source": "manual"})
            )

    app.shared.on_command = command

    def check(stage):
        if stage == 0:
            app.button("Capture empty station").invoke()
        elif stage == 1:
            app.button("More").invoke()
        elif stage == 2:
            assert app.shared.worker.paused.is_set()
            app.snapshot("more-options.png")
            app.button("Enter / edit ID").invoke()
        elif stage == 3:
            app.button("4").invoke()
            app.button("2").invoke()
            app.button("Save ID").invoke()
        else:
            assert "ID: 42" in app.texts()
            assert not app.shared.worker.paused.is_set()
            assert not app.sounds
            assert app.button("Measure box").cget("state") == "normal"

    app.run(check, 5)


def test_multiple_codes_require_choice_and_marked_id_refreshes(tmp_path, monkeypatch, request):
    if isolated_ui_case(request):
        return
    app = ui(tmp_path, monkeypatch)
    from parcel_dimensioner.barcodes import with_identity

    pending = {
        "scan_id": "label-multi",
        "package_id": None,
        "barcode": {
            "status": "multiple",
            "selected_index": None,
            "candidates": [{"value": v, "formats": ["Code 128"]} for v in ("A", "B")],
        },
    }
    saved = []

    def command(action):
        events = app.shared.worker.events
        if action == "empty":
            events.put(("empty", "Ready"))
            events.put(("label", pending))
        elif action == "scan":
            saved.append(
                {
                    **with_identity(pending, "B", 1),
                    "scan_id": "scan-1",
                    "dimensions_inches": {"length": 9},
                    "weight": {"pounds": 5},
                }
            )
            events.put(("result", (saved[-1], app.shared.review)))
        elif action[0] == "label_id":
            events.put(
                ("label", with_identity(pending, action[1]["value"], action[1]["candidate_index"]))
            )
        else:
            saved[-1] = with_identity(saved[-1], action[1]["value"], action[1]["candidate_index"])
            app.image.new("RGB", (800, 450), "#990000").save(app.shared.review)
            events.put(("package_id", (saved[-1], app.shared.review)))

    app.shared.on_command = command

    def check(stage):
        if stage == 0:
            app.button("Capture empty station").invoke()
        elif stage == 1:
            assert not app.sounds
            app.button("Choose ID").invoke()
        elif stage == 2:
            listing = next(w for w in app.descendants(app.root) if isinstance(w, app.tk.Listbox))
            listing.selection_set(1)
            listing.event_generate("<<ListboxSelect>>")
            app.button("Save ID").invoke()
        elif stage == 3:
            assert "ID: B" in app.texts() and app.sounds == [True]
            app.button("Measure box").invoke()
        elif stage == 4:
            app.button("Marked result").invoke()
            app.button("Edit").invoke()
        elif stage == 5:
            app.button("Clear").invoke()
            app.button("4").invoke()
            app.button("2").invoke()
            app.button("Save ID").invoke()
        else:
            assert "ID: 42" in app.texts()
            preview = next(
                w
                for w in app.descendants(app.root)
                if isinstance(w, app.tk.Label) and w.cget("image")
            )
            pixel = app.root.tk.call(preview.cget("image"), "get", 0, 0)
            assert pixel[0] > 80 and pixel[1] < 20
            assert saved[-1]["weight"]["pounds"] == 5
            assert app.sounds == [True]

    app.run(check, 7)


def test_scale_failure_retains_pending_id_and_explicit_dimensions_fallback(
    tmp_path, monkeypatch, request
):
    if isolated_ui_case(request):
        return
    app = ui(tmp_path, monkeypatch)

    def command(action):
        events = app.shared.worker.events
        if action == "empty":
            events.put(("empty", "Ready"))
            events.put(("label", label_result()))
        elif action == "scan":
            events.put(("weight_error", ("USB disconnected", True)))
        else:
            assert action == ("scan", "USB disconnected")
            record = {
                **label_result(),
                "scan_id": "scan-1",
                "dimensions_inches": {"length": 9},
                "weight": {"pounds": None},
            }
            events.put(("result", (record, app.shared.review)))

    app.shared.on_command = command

    def check(stage):
        if stage == 0:
            app.button("Capture empty station").invoke()
        elif stage == 1:
            app.button("Measure box").invoke()
        elif stage == 2:
            assert "ID: PKG-42" in app.texts()
            assert not any("9.00" in text for text in app.texts())
            app.button("Measure dimensions only").invoke()
        else:
            assert any("weight unavailable" in text for text in app.texts())
            assert any("9.00" in text for text in app.texts())
            assert "ID: PKG-42" in app.texts()

    app.run(check, 4)
