"""Touchscreen scanner: separate camera/vision and continuous USB scale workers."""

from __future__ import annotations

import queue
import threading
from contextlib import suppress
from pathlib import Path

from parcel_dimensioner.camera.contracts import CameraError
from parcel_dimensioner.measurement import _vision
from parcel_dimensioner.package_id_editor import display_text, open_package_id_editor
from parcel_dimensioner.scale import ScaleError, SerialScale
from parcel_dimensioner.settings import ScaleSettings
from parcel_dimensioner.station import ScanSession, StationCamera


class StationWorker(threading.Thread):
    def __init__(self, settings, captures_dir, scale_settings=None):
        super().__init__(daemon=True)
        self.settings, self.captures_dir = settings, captures_dir
        self.commands = queue.Queue(maxsize=1)
        self.events = queue.Queue()
        self.frames = queue.Queue(maxsize=1)
        self.stopping = threading.Event()
        self.scale = SerialScale(scale_settings)
        self.review_warning = ""

    def run(self):
        camera = StationCamera(self.settings)
        try:
            self.scale.start()
            camera.start()
            session = ScanSession(
                self.captures_dir, camera, self.settings, scale=self.scale, cancel=self.stopping
            )
            self.events.put(("ready", str(session.directory)))
            while not self.stopping.is_set():
                try:
                    action = self.commands.get_nowait()
                except queue.Empty:
                    action = None
                if action:
                    command, reason = action if isinstance(action, tuple) else (action, None)
                    try:
                        if command == "empty":
                            session.capture_empty()
                            try:
                                self.scale.zero(self.stopping)
                                note = "Empty reference captured and scale zeroed."
                            except ScaleError as exc:
                                note = f"Empty reference captured. Scale: {exc}"
                            self.events.put(("empty", note))
                        elif command == "zero":
                            session.last_scan = None
                            session.clear_label()
                            self.scale.zero(self.stopping)
                            self.events.put(("zero", "Scale zeroed. Place a box to measure."))
                        elif command == "label":
                            self.events.put(("label", session.scan_label()))
                        elif command == "clear_label":
                            session.clear_label()
                            self.events.put(("label", None))
                        elif command == "label_id":
                            self.events.put(("label", session.set_label_id(**reason)))
                        elif command == "package_id":
                            result = session.set_package_id(**reason)
                            self.review_warning = session.review_warning
                            self.events.put(("package_id", (result, session.review_path)))
                        else:
                            result = session.scan(dimensions_only_reason=reason)
                            self.review_warning = session.review_warning
                            self.events.put(("result", result))
                    except ScaleError as exc:
                        event = "zero_error" if command == "zero" else "weight_error"
                        self.events.put((event, (str(exc), session.reference is not None)))
                    except Exception as exc:
                        event = (
                            "package_id_error"
                            if command in ("package_id", "label_id")
                            else "label_error"
                            if command == "label"
                            else "error"
                        )
                        self.events.put((event, (str(exc), session.reference is not None)))
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
                self.scale.close()
                self.events.put(("closed", None))


def launch_touchscreen(settings, captures_dir: Path, *, scale_settings=None, windowed=False):
    try:
        import tkinter as tk

        from PIL import Image, ImageTk
    except ImportError as exc:
        raise CameraError(
            "Install python3-tk and python3-pil.imagetk with apt, "
            "then install this project's [touchscreen] extra."
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
    worker = StationWorker(settings, captures_dir, scale_settings or ScaleSettings())
    state = {
        "ready": False,
        "empty": False,
        "busy": True,
        "closing": False,
        "review": None,
        "show_review": False,
        "photo": None,
        "fallback": None,
        "result": None,
        "editing": False,
        "pending_label": None,
    }
    portrait = root.winfo_screenheight() > root.winfo_screenwidth()
    font = 16 if portrait else 14

    # The preview is the first content on screen. Exit overlays its upper-right corner.
    preview_frame = tk.Frame(root, bg="#08131b", height=80)
    preview_frame.pack_propagate(False)
    preview_frame.pack(side="top", fill="both", expand=True, padx=8, pady=(8, 4))
    preview = tk.Label(preview_frame, bg="#08131b")
    preview.pack(fill="both", expand=True)

    results = tk.Frame(root, bg="#10202c")
    results.pack(fill="x", padx=12, pady=2)
    values = {}
    for index, (key, label) in enumerate(
        [("length", "Length"), ("width", "Width"), ("height", "Height"), ("weight", "Saved weight")]
    ):
        row, column = divmod(index, 2)
        card = tk.Frame(results, bg="#193442")
        card.grid(row=row, column=column, sticky="nsew", padx=3, pady=3)
        results.columnconfigure(column, weight=1, uniform="measurements")
        tk.Label(card, text=label, bg="#193442", fg="#c3d6e2", font=("Sans", 11)).pack()
        values[key] = tk.StringVar(value="—")
        tk.Label(
            card,
            textvariable=values[key],
            bg="#193442",
            fg="white",
            font=("Sans", 22 if portrait else 19, "bold"),
        ).pack(pady=(0, 3))
    package_frame = tk.Frame(root, bg="#10202c")
    package_frame.pack(fill="x", padx=15, pady=3)
    package_frame.columnconfigure(0, weight=1)
    package_value = tk.StringVar(value="Package ID: —")
    package_status = tk.StringVar(
        value="Measure box reads its barcode too. Scan label is optional."
    )
    package_label = tk.Label(
        package_frame,
        textvariable=package_value,
        anchor="w",
        justify="left",
        bg="#10202c",
        fg="white",
        font=("Sans", font, "bold"),
        wraplength=450,
    )
    package_label.grid(row=0, column=0, sticky="ew")
    barcode_label = tk.Label(
        package_frame,
        textvariable=package_status,
        anchor="w",
        justify="left",
        bg="#10202c",
        fg="#c3d6e2",
        font=("Sans", 11),
        wraplength=450,
    )
    barcode_label.grid(row=1, column=0, sticky="ew")
    live_weight = tk.StringVar(value="Live scale: connecting…")
    live_label = tk.Label(
        root,
        textvariable=live_weight,
        bg="#10202c",
        fg="#c3d6e2",
        font=("Sans", font),
        wraplength=600,
    )
    live_label.pack(fill="x", padx=12, pady=3)
    status = tk.StringVar(value="Starting camera…")
    status_label = tk.Label(
        root,
        textvariable=status,
        bg="#10202c",
        fg="#c3d6e2",
        font=("Sans", font),
        wraplength=600,
        justify="center",
    )
    status_label.pack(fill="x", padx=12, pady=3)
    controls = tk.Frame(root, bg="#10202c")
    controls.pack(padx=8, pady=(4, 8))

    def resize(event):
        width = max(180, event.width - 24)
        status_label.configure(wraplength=width)
        live_label.configure(wraplength=width)
        package_label.configure(wraplength=max(140, width - 120))
        barcode_label.configure(wraplength=max(140, width - 120))
        for control in (empty_button, zero_button, scan_button, review_button):
            control.configure(wraplength=max(100, min(210, width // 2 - 24)))

    root.bind("<Configure>", lambda event: resize(event) if event.widget is root else None)

    def clear_result():
        for value in values.values():
            value.set("—")
        state.update(review=None, show_review=False, result=None)
        review_button.configure(text="Marked result")
        update_package()

    def update_package():
        result = state["pending_label"] or state["result"] or {}
        value = result.get("package_id")
        package_value.set("Package ID: " + (display_text(value, 44) if value else "—"))
        barcode = result.get("barcode", {})
        candidates = barcode.get("candidates", [])
        source = result.get("package_id_source")
        if source == "manual":
            message = "Manual ID saved"
        elif value and source in ("barcode", "selected_barcode"):
            index = barcode.get("selected_index")
            formats = "/".join(candidates[index]["formats"]) if index is not None else ""
            message = "Barcode found • " + formats + " • review ID"
        elif len(candidates) > 1:
            message = f"{len(candidates)} barcodes found • choose the package ID"
        elif candidates:
            message = "Barcode found • choose or enter the package ID"
        elif barcode.get("status") == "not_found":
            message = "No barcode found • enter an ID"
        elif result:
            message = "Barcode unavailable • enter an ID"
        else:
            message = "Measure box reads its barcode too. Scan label is optional."
        if state["pending_label"]:
            message = "Next box • " + message
        package_status.set(message)
        barcode_label.configure(fg="#71dcc4" if value else "#ffca78")
        id_button.configure(text="Edit ID" if value else "Choose ID" if candidates else "Enter ID")

    def enable_buttons():
        idle = (
            state["ready"] and not state["busy"] and not state["closing"] and not state["editing"]
        )
        empty_button.configure(state="normal" if idle else "disabled")
        zero_button.configure(state="normal" if idle else "disabled")
        label_ready = not state["pending_label"] or bool(state["pending_label"].get("package_id"))
        scan_button.configure(
            state="normal" if idle and state["empty"] and label_ready else "disabled"
        )
        review_button.configure(state="normal" if idle and state["review"] else "disabled")
        fallback_button.configure(
            state="normal" if idle and state["empty"] and label_ready else "disabled"
        )
        label_button.configure(state="normal" if idle else "disabled")
        clear_label_button.configure(
            state="normal" if idle and state["pending_label"] else "disabled"
        )
        target = state["pending_label"] or state["result"]
        editable = idle and target and target.get("scan_id")
        id_button.configure(state="normal" if editable else "disabled")

    def submit(action):
        if state["busy"] or not state["ready"] or state["closing"] or state["editing"]:
            return
        if action == "empty":
            state["empty"] = False
        if action in ("empty", "zero", "label", "clear_label"):
            state["pending_label"] = None
        reason = state["fallback"] if action == "dimensions_only" else None
        if action == "dimensions_only" and reason is None:
            return
        state.update(busy=True, fallback=None)
        fallback_button.pack_forget()
        clear_result()
        status.set(
            {
                "empty": "Capturing empty station and zeroing scale…",
                "zero": "Keep hands off — zeroing scale…",
                "scan": "Waiting for stable weight, then capturing…",
                "dimensions_only": "Capturing dimensions only — weight will be unavailable.",
                "label": "Hold label still in the center — focusing and reading barcode…",
                "clear_label": "Clearing pending label…",
            }[action]
        )
        enable_buttons()
        worker.commands.put_nowait(("scan", reason) if action == "dimensions_only" else action)

    def toggle_review():
        state["show_review"] = not state["show_review"]
        review_button.configure(text="Live view" if state["show_review"] else "Marked result")

    def edit_package():
        if state["busy"] or state["editing"] or state["closing"]:
            return
        result = state["pending_label"] or state["result"]
        if not result:
            return
        editing_label = state["pending_label"] is not None
        state["editing"] = True
        enable_buttons()

        def closed():
            state["editing"] = False
            enable_buttons()

        def save(value, candidate_index):
            state["busy"] = True
            enable_buttons()
            status.set("Saving package ID…")
            worker.commands.put_nowait(
                (
                    "label_id" if editing_label else "package_id",
                    {
                        "scan_id": result["scan_id"],
                        "value": value,
                        "candidate_index": candidate_index,
                    },
                )
            )

        open_package_id_editor(root, result, save, closed)

    id_button = tk.Button(
        package_frame,
        text="Enter ID",
        command=edit_package,
        font=("Sans", 11, "bold"),
        bg="#345269",
        fg="white",
        height=2,
        width=9,
        takefocus=False,
    )
    id_button.grid(row=0, column=1, rowspan=2, padx=(8, 0), sticky="e")

    label_actions = tk.Frame(package_frame, bg="#10202c")
    label_actions.grid(row=2, column=0, columnspan=2, pady=(4, 0))
    label_button = tk.Button(
        label_actions,
        text="Scan label",
        command=lambda: submit("label"),
        font=("Sans", 12, "bold"),
        bg="#007b70",
        fg="white",
        height=2,
        width=14,
        takefocus=False,
    )
    label_button.pack(side="left", padx=4)
    clear_label_button = tk.Button(
        label_actions,
        text="Clear label",
        command=lambda: submit("clear_label"),
        font=("Sans", 12, "bold"),
        bg="#345269",
        fg="white",
        height=2,
        width=14,
        takefocus=False,
    )
    clear_label_button.pack(side="left", padx=4)

    def close():
        if state["closing"]:
            return
        state["closing"] = True
        worker.stopping.set()
        status.set("Closing camera and scale…")
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
            width=16,
            height=3,
            padx=4,
            pady=8,
            wraplength=180,
            takefocus=False,
        )

    empty_button = button("Capture empty station", lambda: submit("empty"), "#345269")
    zero_button = button("Zero scale", lambda: submit("zero"), "#345269")
    scan_button = button("Measure box", lambda: submit("scan"), "#007b70")
    review_button = button("Marked result", toggle_review, "#345269")
    for control, row, column in (
        (empty_button, 0, 0),
        (zero_button, 1, 0),
        (scan_button, 0, 1),
        (review_button, 1, 1),
    ):
        control.grid(row=row, column=column, padx=5, pady=4, sticky="nsew")
    fallback_button = tk.Button(
        root,
        text="Measure dimensions only",
        command=lambda: submit("dimensions_only"),
        font=("Sans", font, "bold"),
        bg="#805623",
        fg="white",
        pady=8,
        takefocus=False,
    )
    exit_button = tk.Button(
        preview_frame,
        text="Exit",
        command=close,
        font=("Sans", 11, "bold"),
        bg="#543f4a",
        fg="white",
        relief="flat",
        takefocus=False,
    )
    exit_button.place(relx=1, x=-8, y=8, anchor="ne", width=56, height=56)
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
                        (payload or "Empty reference captured.") + " Place a box to measure."
                    )
                elif event == "zero":
                    state["busy"] = False
                    status.set(
                        payload if state["empty"] else "Scale zeroed. Capture empty station next."
                    )
                elif event == "result":
                    result, path = payload
                    state["pending_label"] = None
                    state["result"] = result
                    update_package()
                    with Image.open(path) as im:
                        state["review"] = im.copy()
                    state["busy"] = False
                    for key, value in result["dimensions_inches"].items():
                        values[key].set(f"{value:.2f}″")
                    weight = result.get("weight", {})
                    if weight.get("pounds") is not None:
                        values["weight"].set(f"{weight['pounds']:.2f} lb")
                        note = "Dimensions and weight saved."
                    else:
                        values["weight"].set("—")
                        note = "Dimensions-only result saved; weight unavailable."
                    heights = result.get("height_by_wall_inches", {})
                    if abs(heights.get("long", 0) - heights.get("short", 0)) > 0.25:
                        note += " Wall heights disagree; inspect marked result."
                    warning = getattr(worker, "review_warning", "")
                    movement = " ".join(result.get("station_movement_warnings", []))
                    if movement:
                        note += " " + movement
                    status.set(
                        note + " " + warning
                        if warning
                        else note + " Replace the box and tap Measure box."
                    )
                elif event == "label":
                    state.update(busy=False, pending_label=payload)
                    update_package()
                    if payload and payload.get("package_id"):
                        status.set(
                            "Label ready. Place this box in the corner, hands off, "
                            "then tap Measure box."
                        )
                    elif payload:
                        status.set(
                            "Choose or enter an ID, rescan the label, or tap Clear label to skip."
                        )
                    else:
                        status.set(
                            "Pending label cleared. Scan another label or measure without one."
                        )
                elif event == "label_error":
                    state.update(busy=False, pending_label=None)
                    update_package()
                    status.set("Could not read label: " + payload[0])
                elif event == "package_id":
                    result, path = payload
                    state.update(busy=False, result=result)
                    with Image.open(path) as im:
                        state["review"] = im.copy()
                    update_package()
                    warning = getattr(worker, "review_warning", "")
                    status.set("Package ID saved with this scan. " + warning)
                elif event == "package_id_error":
                    state["busy"] = False
                    status.set("Could not save package ID: " + payload[0])
                elif event in ("error", "weight_error", "zero_error"):
                    message, has_reference = payload
                    state.update(busy=False, empty=has_reference)
                    clear_result()
                    status.set(("Scale: " if event != "error" else "Could not measure: ") + message)
                    if event == "weight_error" and has_reference:
                        state["fallback"] = message
                        status.set(message + " Retry with Measure box, or choose dimensions only.")
                        fallback_button.pack(before=controls, pady=3)
                elif event == "fatal":
                    state.update(ready=False, busy=False)
                    clear_result()
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
        scale = getattr(worker, "scale", None)
        if scale is not None:
            reading = scale.snapshot()
            weight = reading["pounds"]
            text = "—" if weight is None else f"{0 if abs(weight) < 0.005 else weight:.2f} lb"
            live_weight.set(f"Live scale: {text} • {reading['message']}")
            live_label.configure(fg="#71dcc4" if reading["stable"] else "#ffca78")
        try:
            frame = worker.frames.get_nowait()
        except queue.Empty:
            frame = None
        image = state["review"] if state["show_review"] else frame
        if image is not None:
            image = image.copy()
            image.thumbnail(
                (max(10, preview_frame.winfo_width()), max(10, preview_frame.winfo_height()))
            )
            state["photo"] = ImageTk.PhotoImage(image)
            preview.configure(image=state["photo"])
        root.after(100, poll)

    root.after(100, poll)
    try:
        root.mainloop()
    finally:
        worker.stopping.set()
        worker.join(timeout=4)
