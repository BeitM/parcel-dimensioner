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

    from parcel_dimensioner import touchscreen
    from parcel_dimensioner.settings import CameraSettings

    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("No desktop display available for the Tk smoke test")
    root.withdraw()
    monkeypatch.setattr(tk, "Tk", lambda: root)
    monkeypatch.setattr(touchscreen, "_vision", lambda: None)
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


def test_barcode_selection_touch_keyboard_and_clearing_next_scan(tmp_path, monkeypatch, request):
    if isolated_ui_case(request):
        return
    tk = pytest.importorskip("tkinter")
    image_module = pytest.importorskip("PIL.Image")
    from parcel_dimensioner import touchscreen
    from parcel_dimensioner.barcodes import with_identity
    from parcel_dimensioner.settings import CameraSettings

    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("No display for barcode editor check")
    root.overrideredirect(True)
    geometry = root.geometry
    geometry("480x853+0+0")
    monkeypatch.setattr(root, "geometry", lambda _: geometry("480x853+0+0"))
    monkeypatch.setattr(root, "winfo_screenwidth", lambda: 480)
    monkeypatch.setattr(root, "winfo_screenheight", lambda: 853)
    monkeypatch.setattr(tk, "Tk", lambda: root)
    monkeypatch.setattr(touchscreen, "_vision", lambda: None)
    marked = tmp_path / "marked.jpg"
    image_module.new("RGB", (640, 360), "gray").save(marked)
    saved = []

    class Worker:
        def __init__(self, *_):
            import threading

            self.events, self.frames = queue.Queue(), queue.Queue()
            self.stopping = threading.Event()
            self.commands = self
            self.result = None
            self.scans = 0

        def start(self):
            self.events.put(("ready", str(tmp_path)))

        def is_alive(self):
            return not self.stopping.is_set()

        def join(self, **_):
            pass

        def put_nowait(self, action):
            if action == "empty":
                self.events.put(("empty", "Empty station captured."))
            elif isinstance(action, tuple) and action[0] == "package_id":
                payload = action[1]
                assert payload["scan_id"] == self.result["scan_id"]
                self.result = with_identity(
                    self.result, payload["value"], payload["candidate_index"]
                )
                saved.append(self.result)
                updated_image = tmp_path / f"updated-id-{len(saved)}.jpg"
                image_module.new("RGB", (640, 360), (70 + len(saved) * 20, 0, 0)).save(
                    updated_image
                )
                self.events.put(("package_id", (self.result, updated_image)))
            else:
                self.scans += 1
                candidates = (
                    [
                        {"value": "PKG-A", "formats": ["Code 128"]},
                        {"value": "PKG-B", "formats": ["QR Code"]},
                    ]
                    if self.scans == 1
                    else []
                )
                self.result = {
                    "scan_id": f"scan-{self.scans}",
                    "dimensions_inches": {"length": 9, "width": 8, "height": 3},
                    "weight": {"pounds": 5},
                    "package_id": None,
                    "package_id_source": None,
                    "package_id_history": [],
                    "barcode": {
                        "status": "multiple" if candidates else "not_found",
                        "candidates": candidates,
                        "selected_index": None,
                    },
                }
                self.events.put(("result", (self.result, marked)))

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
                button("Capture empty station").invoke()
            elif stage == 1:
                button("Measure box").invoke()
            elif stage == 2:
                assert any("2 barcodes found" in v for v in values())
                button("Marked result").invoke()
                button("Choose ID").invoke()
                assert button("Measure box").cget("state") == "disabled"
            elif stage == 3:
                listing = next(w for w in descendants(root) if isinstance(w, tk.Listbox))
                dialog = next(w for w in root.winfo_children() if isinstance(w, tk.Toplevel))
                for control in descendants(dialog):
                    if isinstance(control, tk.Button):
                        x = control.winfo_rootx() - dialog.winfo_rootx()
                        y = control.winfo_rooty() - dialog.winfo_rooty()
                        assert x >= 0 and x + control.winfo_width() <= dialog.winfo_width()
                        assert y >= 0 and y + control.winfo_height() <= dialog.winfo_height()
                snapshot_dir = os.environ.get("PARCEL_UI_SNAPSHOT_DIR")
                if snapshot_dir and sys.platform == "win32":
                    from PIL import ImageGrab

                    Path(snapshot_dir).mkdir(exist_ok=True, parents=True)
                    ImageGrab.grab(window=dialog.winfo_id()).save(
                        Path(snapshot_dir) / "barcode-editor.png"
                    )
                listing.selection_set(1)
                listing.event_generate("<<ListboxSelect>>")
                root.update_idletasks()
                button("Save ID").invoke()
            elif stage == 4:
                assert saved[-1]["package_id"] == "PKG-B"
                assert "Package ID: PKG-B" in values()
                preview = next(
                    w for w in descendants(root) if isinstance(w, tk.Label) and w.cget("image")
                )
                pixel = root.tk.call(preview.cget("image"), "get", 0, 0)
                assert pixel[0] > 50 and pixel[1] < 20 and pixel[2] < 20
                button("Edit ID").invoke()
                button("Clear").invoke()
                for character in "MAN42":
                    button(character).invoke()
                button("Save ID").invoke()
            elif stage == 5:
                assert saved[-1]["package_id"] == "MAN42"
                assert "Manual ID saved" in values()
                button("Measure box").invoke()
                assert not any("MAN42" in v for v in values())
            elif stage == 6:
                assert "Package ID: —" in values()
                assert any("No barcode found" in v for v in values())
                assert button("Enter ID").cget("state") == "normal"
                button("Enter ID").invoke()
                button("4").invoke()
                button("2").invoke()
                button("Save ID").invoke()
            else:
                assert saved[-1]["scan_id"] == "scan-2"
                assert saved[-1]["package_id"] == "42"
                assert saved[-1]["dimensions_inches"]["length"] == 9
                assert saved[-1]["weight"]["pounds"] == 5
        except Exception as exc:
            errors.append(exc)
        if stage == 7:
            # Release any dialog even if a previous stage failed.
            for w in list(root.winfo_children()):
                if isinstance(w, tk.Toplevel):
                    w.destroy()
            button("Exit").invoke()

    for stage in range(8):
        root.after(300 + stage * 350, lambda s=stage: check(s))
    root.after(6000, root.destroy)
    touchscreen.launch_touchscreen(CameraSettings(), tmp_path, windowed=True)
    assert not errors, errors


def test_pending_label_survives_retry_and_is_consumed_on_success(tmp_path, monkeypatch, request):
    if isolated_ui_case(request):
        return
    tk = pytest.importorskip("tkinter")
    image = pytest.importorskip("PIL.Image")
    from parcel_dimensioner import touchscreen
    from parcel_dimensioner.settings import CameraSettings

    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("No display for Tk workflow check")
    root.withdraw()
    monkeypatch.setattr(tk, "Tk", lambda: root)
    monkeypatch.setattr(touchscreen, "_vision", lambda: None)
    marked = tmp_path / "marked.jpg"
    image.new("RGB", (640, 360), "gray").save(marked)
    label = {
        "scan_id": "label-1",
        "package_id": "LABEL-42",
        "package_id_source": "barcode",
        "barcode": {
            "selected_index": 0,
            "candidates": [{"value": "LABEL-42", "formats": ["Code 128"]}],
        },
    }

    class Worker:
        def __init__(self, *_):
            import threading

            self.stopping = threading.Event()
            self.events, self.frames = queue.Queue(), queue.Queue()
            self.commands = self
            self.scans = 0

        def start(self):
            self.events.put(("ready", str(tmp_path)))

        def is_alive(self):
            return not self.stopping.is_set()

        def join(self, **_):
            pass

        def put_nowait(self, action):
            if action == "empty":
                self.events.put(("empty", "Ready"))
            elif action == "label":
                self.events.put(("label", label))
            else:
                self.scans += 1
                if self.scans == 1:
                    self.events.put(("error", ("Try again", True)))
                else:
                    record = {
                        **label,
                        "scan_id": "scan-1",
                        "dimensions_inches": {"length": 9},
                        "weight": {"pounds": 5},
                    }
                    if self.scans > 2:
                        record.update(
                            package_id=None, package_id_source=None, barcode={"status": "not_found"}
                        )
                    self.events.put(("result", (record, marked)))

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
                button("Capture empty station").invoke()
            elif stage == 1:
                button("Scan label").invoke()
            elif stage == 2:
                assert "Package ID: LABEL-42" in values()
                assert any(v.startswith("Next box") for v in values())
                assert button("Clear label").cget("state") == "normal"
                button("Measure box").invoke()
            elif stage == 3:
                assert "Package ID: LABEL-42" in values()
                assert "Try again" in " ".join(values())
                button("Measure box").invoke()
            elif stage == 4:
                assert "Package ID: LABEL-42" in values()
                assert button("Clear label").cget("state") == "disabled"
                assert not any(v.startswith("Next box") for v in values())
                button("Measure box").invoke()
            else:
                assert "Package ID: —" in values()
        except Exception as exc:
            errors.append(exc)
        if stage == 5:
            button("Exit").invoke()

    for stage in range(6):
        root.after(300 + stage * 350, lambda s=stage: check(s))
    root.after(4500, root.destroy)
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
                                "scan_id": "layout-scan",
                                "package_id": "PKG-1234567890",
                                "package_id_source": "barcode",
                                "barcode": {
                                    "status": "found",
                                    "selected_index": 0,
                                    "candidates": [
                                        {"value": "PKG-1234567890", "formats": ["Code 128"]}
                                    ],
                                },
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
                    ImageGrab.grab(window=root.winfo_id()).save(target / f"barcode-ui-{width}.png")
        except Exception as exc:
            errors.append(exc)
        if stage == 4:
            button("Exit").invoke()

    for stage in range(5):
        root.after(300 + stage * 300, lambda s=stage: check(s))
    root.after(4000, root.destroy)
    touchscreen.launch_touchscreen(CameraSettings(), tmp_path, windowed=True)
    assert not errors, errors
