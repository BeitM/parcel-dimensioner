"""Local touchscreen app: camera/vision on one worker, Tk only on the UI thread."""

from __future__ import annotations

import queue
import threading
from contextlib import suppress
from pathlib import Path

from parcel_dimensioner.camera.contracts import CameraError
from parcel_dimensioner.measurement import _vision
from parcel_dimensioner.station import ScanSession, StationCamera


class StationWorker(threading.Thread):
    def __init__(self, settings, captures_dir):
        super().__init__(daemon=True)
        self.settings, self.captures_dir = settings, captures_dir
        self.commands = queue.Queue(maxsize=1)
        self.events = queue.Queue()
        self.frames = queue.Queue(maxsize=1)
        self.stopping = threading.Event()

    def run(self):
        camera = StationCamera(self.settings)
        try:
            camera.start()
            session = ScanSession(self.captures_dir, camera, self.settings)
            self.events.put(("ready", str(session.directory)))
            while not self.stopping.is_set():
                try:
                    action = self.commands.get_nowait()
                except queue.Empty:
                    action = None
                if action:
                    try:
                        if action == "empty":
                            session.capture_empty()
                            self.events.put(("empty", None))
                        else:
                            self.events.put(("result", session.scan()))
                    except Exception as exc:
                        self.events.put(("error", (str(exc), session.reference is not None)))
                if self.stopping.is_set():
                    break
                frame = camera.preview()
                with suppress(queue.Empty):
                    self.frames.get_nowait()
                self.frames.put_nowait(frame)
                self.stopping.wait(0.15)
        except Exception as exc:
            self.events.put(("fatal", str(exc)))
        finally:
            try:
                camera.close()
            finally:
                self.events.put(("closed", None))


def launch_touchscreen(settings, captures_dir: Path, *, windowed=False):
    try:
        import tkinter as tk
        from tkinter import messagebox

        from PIL import Image, ImageTk
    except ImportError as exc:
        raise CameraError(
            "Install python3-tk with apt and install this project's [touchscreen] extra."
        ) from exc
    _vision()
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        raise CameraError(
            "Open the app on the Pi desktop or through Pi Connect screen sharing."
        ) from exc
    root.title("Box Scanner")
    root.geometry("1000x680")
    root.attributes("-fullscreen", not windowed)
    root.configure(bg="#10202c")
    worker = StationWorker(settings, captures_dir)
    state = {
        "ready": False,
        "empty": False,
        "busy": True,
        "closing": False,
        "review": None,
        "show_review": False,
        "photo": None,
    }
    portrait = root.winfo_screenheight() > root.winfo_screenwidth()
    font = 16 if portrait else 14
    tk.Label(root, text="BOX SCANNER", bg="#10202c", fg="white", font=("Sans", 23, "bold")).pack(
        pady=(12, 2)
    )
    tk.Label(
        root,
        text="Experimental dimensions • inches",
        bg="#10202c",
        fg="#ffca78",
        font=("Sans", font),
    ).pack()
    dimensions = tk.StringVar(value="Length —    Width —    Height —")
    tk.Label(
        root,
        textvariable=dimensions,
        bg="#10202c",
        fg="white",
        font=("Sans", 23 if portrait else 21, "bold"),
        pady=10,
    ).pack()
    status = tk.StringVar(value="Starting camera…")
    status_label = tk.Label(
        root,
        textvariable=status,
        bg="#10202c",
        fg="#c3d6e2",
        font=("Sans", font),
        wraplength=650,
        justify="center",
    )
    status_label.pack(fill="x", padx=18)
    controls = tk.Frame(root, bg="#10202c")
    controls.pack(side="bottom", fill="x", padx=14, pady=10)
    preview = tk.Label(root, bg="#08131b")
    preview.pack(fill="both", expand=True, padx=14, pady=8)
    preview.bind("<Configure>", lambda e: status_label.configure(wraplength=max(200, e.width)))

    def enable_buttons():
        idle = state["ready"] and not state["busy"] and not state["closing"]
        empty_button.configure(state="normal" if idle else "disabled")
        scan_button.configure(state="normal" if idle and state["empty"] else "disabled")
        review_button.configure(state="normal" if idle and state["review"] else "disabled")

    def submit(action):
        if state["busy"] or not state["ready"] or state["closing"]:
            return
        if action == "empty":
            if not messagebox.askokcancel(
                "Empty station", "Remove every box from the platform, then tap OK.", parent=root
            ):
                return
            state["empty"] = False
        state.update(busy=True, review=None, show_review=False)
        dimensions.set("Length —    Width —    Height —")
        review_button.configure(text="Marked result")
        status.set(
            "Hold still — capturing empty station…"
            if action == "empty"
            else "Hold still — capturing and measuring…"
        )
        enable_buttons()
        worker.commands.put_nowait(action)

    def toggle_review():
        state["show_review"] = not state["show_review"]
        review_button.configure(text="Live view" if state["show_review"] else "Marked result")

    def close():
        if state["closing"]:
            return
        state["closing"] = True
        worker.stopping.set()
        status.set("Closing camera…")
        enable_buttons()

    def button(text, command, color):
        return tk.Button(
            controls,
            text=text,
            command=command,
            font=("Sans", font, "bold"),
            bg=color,
            fg="white",
            disabledforeground="#a0acb4",
            relief="flat",
            pady=14,
            takefocus=False,
        )

    empty_button = button("Capture empty station", lambda: submit("empty"), "#345269")
    scan_button = button("Measure box", lambda: submit("scan"), "#007b70")
    review_button = button("Marked result", toggle_review, "#345269")
    exit_button = button("Exit", close, "#543f4a")
    for index, control in enumerate([empty_button, scan_button, review_button, exit_button]):
        row, column = (index, 0) if portrait else divmod(index, 2)
        control.grid(row=row, column=column, sticky="ew", padx=4, pady=4)
        controls.columnconfigure(column, weight=1)
    root.protocol("WM_DELETE_WINDOW", close)
    root.bind("<Escape>", lambda _: close())
    enable_buttons()
    worker.start()

    def poll():
        try:
            while True:
                event, payload = worker.events.get_nowait()
                if event == "ready":
                    state.update(ready=True, busy=False)
                    status.set("Remove all boxes, then tap Capture empty station.")
                elif event == "empty":
                    state.update(empty=True, busy=False)
                    status.set(
                        "Ready. Place a box snugly against both walls, then tap Measure box."
                    )
                elif event == "result":
                    result, path = payload
                    with Image.open(path) as im:
                        state["review"] = im.copy()
                    state["busy"] = False
                    d = result["dimensions_inches"]
                    dimensions.set(
                        f"L {d['length']:.2f}″    W {d['width']:.2f}″    H {d['height']:.2f}″"
                    )
                    status.set(
                        "Saved. Replace the box and tap Measure box again. "
                        + (
                            "Wall heights disagree; inspect marked result."
                            if abs(
                                result["height_by_wall_inches"]["long"]
                                - result["height_by_wall_inches"]["short"]
                            )
                            > 0.25
                            else ""
                        )
                    )
                elif event == "error":
                    message, has_reference = payload
                    state.update(busy=False, empty=has_reference, review=None)
                    status.set("Could not measure: " + message)
                elif event == "fatal":
                    state.update(ready=False, busy=False, review=None)
                    dimensions.set("Length —    Width —    Height —")
                    status.set(
                        "Camera stopped: " + payload + " Close other camera apps and reopen."
                    )
                elif event == "closed" and state["closing"]:
                    root.destroy()
                    return
                enable_buttons()
        except queue.Empty:
            pass
        if state["closing"] and not worker.is_alive():
            root.destroy()
            return
        try:
            frame = worker.frames.get_nowait()
        except queue.Empty:
            frame = None
        image = state["review"] if state["show_review"] else frame
        if image is not None:
            image = image.copy()
            image.thumbnail((max(10, preview.winfo_width()), max(10, preview.winfo_height())))
            state["photo"] = ImageTk.PhotoImage(image)
            preview.configure(image=state["photo"])
        root.after(100, poll)

    root.after(100, poll)
    try:
        root.mainloop()
    finally:
        worker.stopping.set()
        worker.join(timeout=3)
