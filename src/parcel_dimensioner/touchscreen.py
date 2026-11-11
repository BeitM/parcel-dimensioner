"""Two-step touchscreen workflow: show a barcode, then measure the box."""

from __future__ import annotations

import queue
import time
from contextlib import suppress
from pathlib import Path

from parcel_dimensioner.barcode_watch import GUIDE
from parcel_dimensioner.camera.contracts import CameraError
from parcel_dimensioner.feedback import barcode_chime
from parcel_dimensioner.measurement import _vision
from parcel_dimensioner.package_id_editor import display_text, open_package_id_editor
from parcel_dimensioner.review import box_detail
from parcel_dimensioner.settings import ScaleSettings
from parcel_dimensioner.station_worker import StationWorker


def launch_touchscreen(settings, captures_dir: Path, *, scale_settings=None, windowed=False):
    try:
        import tkinter as tk

        from PIL import Image, ImageDraw, ImageTk
    except ImportError as exc:
        raise CameraError(
            "Install python3-tk, python3-pil.imagetk, and this project's [touchscreen] extra."
        ) from exc
    _vision()
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        raise CameraError(
            "Open Box Scanner on the Pi desktop or Pi Connect screen sharing."
        ) from exc
    root.title("Box Scanner")
    root.geometry("1000x680")
    root.attributes("-fullscreen", not windowed)
    root.configure(bg="#10202c")
    worker = StationWorker(settings, captures_dir, scale_settings or ScaleSettings())
    state = dict(
        ready=False,
        empty=False,
        busy=True,
        closing=False,
        editing=False,
        menu=None,
        phase="setup",
        pending=None,
        result=None,
        review=None,
        detail=None,
        review_view="live",
        live=None,
        photo=None,
        display_key=None,
        listening=False,
        fallback=None,
        beep_id=None,
        confirm_until=0,
    )
    portrait = root.winfo_screenheight() > root.winfo_screenwidth()
    font = 18 if portrait else 16
    preview_frame = tk.Frame(root, bg="#08131b", height=100)
    preview_frame.pack_propagate(False)
    preview_frame.pack(fill="both", expand=True, padx=8, pady=(8, 6))
    preview = tk.Label(preview_frame, bg="#08131b")
    preview.pack(fill="both", expand=True)

    headline = tk.StringVar(value="Starting camera…")
    headline_label = tk.Label(
        root,
        textvariable=headline,
        bg="#10202c",
        fg="white",
        font=("Sans", 22 if portrait else 20, "bold"),
        wraplength=600,
    )
    headline_label.pack(fill="x", padx=12, pady=(3, 2))
    status = tk.StringVar(value="Connecting to the station.")
    status_label = tk.Label(
        root,
        textvariable=status,
        bg="#10202c",
        fg="#c3d6e2",
        font=("Sans", font),
        wraplength=600,
        justify="center",
    )
    status_label.pack(fill="x", padx=14, pady=(0, 6))
    identity = tk.Frame(root, bg="#193442")
    package_value = tk.StringVar(value="")
    package_label = tk.Label(
        identity,
        textvariable=package_value,
        bg="#193442",
        fg="#71dcc4",
        font=("Sans", font, "bold"),
        anchor="w",
        justify="left",
        wraplength=450,
    )
    package_label.pack(side="left", fill="x", expand=True, padx=10, pady=10)

    results = tk.Frame(root, bg="#10202c")
    values = {}
    for index, (key, label) in enumerate(
        (("length", "Length"), ("width", "Width"), ("height", "Height"), ("weight", "Saved weight"))
    ):
        row, column = divmod(index, 2)
        results.columnconfigure(column, weight=1, uniform="measurements")
        card = tk.Frame(results, bg="#193442")
        card.grid(row=row, column=column, sticky="nsew", padx=3, pady=3)
        tk.Label(card, text=label, bg="#193442", fg="#c3d6e2", font=("Sans", 11)).pack()
        values[key] = tk.StringVar(value="—")
        tk.Label(
            card, textvariable=values[key], bg="#193442", fg="white", font=("Sans", 22, "bold")
        ).pack(pady=(0, 3))
    live_weight = tk.StringVar(value="Scale: connecting…")
    scale_label = tk.Label(
        root,
        textvariable=live_weight,
        bg="#10202c",
        fg="#c3d6e2",
        font=("Sans", 12),
        wraplength=600,
    )
    footer = tk.Frame(root, bg="#10202c")
    footer.pack(side="bottom", pady=(6, 10), padx=12)
    footer.columnconfigure(0, weight=1)

    def idle():
        return (
            state["ready"] and not state["busy"] and not state["closing"] and not state["editing"]
        )

    def refresh():
        target = state["pending"] or state["result"] or {}
        value = target.get("package_id")
        candidates = target.get("barcode", {}).get("candidates", [])
        identity.pack_forget()
        results.pack_forget()
        review_button.pack_forget()
        scale_label.pack_forget()
        fallback_button.pack_forget()
        if target:
            package_value.set(
                "ID: " + (display_text(value, 44) if value else "Choose the correct barcode")
            )
            package_label.configure(fg="#71dcc4" if value else "#ffca78")
            edit_button.configure(
                text="Edit" if value else "Choose ID" if candidates else "Enter ID"
            )
            identity.pack(fill="x", padx=15, pady=4, before=footer)
        else:
            package_value.set("")
        if state["result"]:
            results.pack(fill="x", padx=12, pady=3, before=footer)
            if state["review"]:
                review_button.pack(pady=(3, 0), before=footer)
        else:
            scale_label.pack(fill="x", padx=12, pady=4, before=footer)
        if state["fallback"]:
            fallback_button.pack(pady=4, before=footer)
        if not state["ready"]:
            title = "Starting camera…" if state["busy"] else "Station stopped"
        elif state["phase"] == "setup":
            title = "Prepare station"
        elif state["phase"] == "label":
            title = "1 · Show barcode"
        elif state["phase"] == "choose":
            title = "Choose a barcode"
        elif state["phase"] == "preparing":
            title = "Barcode detected"
        elif state["phase"] == "result":
            title = "Measurement saved"
        elif state["busy"]:
            title = "Measuring…"
        else:
            title = "2 · Place box in corner"
        if state["closing"]:
            title = "Closing…"
        headline.set(title)
        ready_to_measure = idle() and state["phase"] == "measure"
        headline_label.configure(fg="#71dcc4" if state["phase"] == "result" else "white")
        action = (
            "Capture empty station"
            if not state["empty"]
            else "Waiting for barcode"
            if state["phase"] == "label"
            else "Next box"
            if state["phase"] == "result"
            else "Choose ID"
            if state["phase"] == "choose"
            else "Getting ready…"
            if state["phase"] == "preparing"
            else "Measure box"
        )
        primary_button.configure(
            text=action,
            state="normal" if idle() and state["phase"] != "label" else "disabled",
            bg="#009c57" if ready_to_measure else "#007b70",
        )
        more_button.configure(state="normal" if idle() else "disabled")
        edit_button.configure(state="normal" if idle() and target else "disabled")
        review_button.configure(
            state="normal" if idle() else "disabled",
            text={"detail": "Full result", "full": "Live view", "live": "Box detail"}[
                state["review_view"]
            ],
        )
        fallback_button.configure(state="normal" if idle() else "disabled")

    def clear_result():
        state.update(result=None, review=None, detail=None, review_view="live", display_key=None)
        for value in values.values():
            value.set("—")

    def submit(action):
        if not idle():
            return
        reason = state["fallback"] if action == "dimensions_only" else None
        if action == "dimensions_only" and reason is None:
            return
        state.update(busy=True, fallback=None)
        if action in ("empty", "zero", "next", "clear_label", "skip"):
            state["pending"] = None
        if action == "empty":
            state["empty"] = False
        clear_result()
        status.set(
            {
                "empty": "Remove all boxes — capturing empty and zeroing scale…",
                "zero": "Keep the platform empty and hands off — zeroing…",
                "scan": "Keep hands off — waiting for stable weight and measuring…",
                "dimensions_only": "Measuring dimensions only. Weight will be unavailable.",
                "next": "Starting the next box…",
                "clear_label": "Listening for another barcode…",
                "skip": "Preparing to measure without a barcode…",
            }[action]
        )
        refresh()
        worker.commands.put_nowait(("scan", reason) if action == "dimensions_only" else action)

    def primary():
        if not state["empty"]:
            submit("empty")
        elif state["phase"] == "result":
            submit("next")
        elif state["phase"] == "choose":
            edit_package()
        else:
            submit("scan")

    def close_menu(resume=True):
        dialog = state["menu"]
        state["menu"] = None
        if dialog is not None:
            dialog.destroy()
        if resume and not state["editing"]:
            worker.paused.clear()

    def edit_package():
        if not idle():
            return
        close_menu(resume=False)
        worker.paused.set()
        target = state["pending"] or state["result"]
        draft = target is None
        pending = state["pending"] is not None
        target = target or {
            "barcode": {"candidates": [], "selected_index": None},
            "package_id": None,
        }
        state["editing"] = True
        refresh()

        def closed():
            state["editing"] = False
            worker.paused.clear()
            refresh()

        def save(value, index):
            state["busy"] = True
            status.set("Saving package ID…")
            refresh()
            if draft:
                worker.commands.put_nowait(("manual_id", value))
            else:
                worker.commands.put_nowait(
                    (
                        "label_id" if pending else "package_id",
                        {"scan_id": target["scan_id"], "value": value, "candidate_index": index},
                    )
                )

        open_package_id_editor(root, target, save, closed)

    def more():
        if not idle() or state["menu"] is not None:
            return
        worker.paused.set()
        dialog = tk.Toplevel(root)
        state["menu"] = dialog
        dialog.title("Station options")
        dialog.configure(bg="#10202c")
        dialog.transient(root)
        width, height = min(560, root.winfo_width() - 24), min(540, root.winfo_height() - 24)
        x = root.winfo_rootx() + (root.winfo_width() - width) // 2
        y = root.winfo_rooty() + (root.winfo_height() - height) // 2
        dialog.geometry(f"{width}x{height}+{x}+{y}")
        dialog.grab_set()
        tk.Label(
            dialog, text="Station options", font=("Sans", 20, "bold"), bg="#10202c", fg="white"
        ).pack(pady=14)
        body = tk.Frame(dialog, bg="#10202c")
        body.pack(fill="both", expand=True, padx=10)
        for column in (0, 1):
            body.columnconfigure(column, weight=1, uniform="options")

        def perform(action):
            close_menu(resume=False)
            submit(action)
            worker.paused.clear()

        options = (
            ("Capture empty station", lambda: perform("empty"), True),
            ("Zero scale", lambda: perform("zero"), True),
            ("Enter / edit ID", edit_package, state["empty"]),
            ("Rescan label", lambda: perform("clear_label"), state["empty"]),
            ("Measure without barcode", lambda: perform("skip"), state["empty"]),
            ("Exit app", close, True),
        )
        for index, (text, command, enabled) in enumerate(options):
            row, column = divmod(index, 2)
            body.rowconfigure(row, weight=1)
            tk.Button(
                body,
                text=text,
                command=command,
                state="normal" if enabled else "disabled",
                font=("Sans", 14, "bold"),
                bg="#345269",
                fg="white",
                wraplength=max(120, width // 2 - 35),
                relief="flat",
                takefocus=False,
            ).grid(row=row, column=column, sticky="nsew", padx=4, pady=4)
        tk.Button(
            dialog,
            text="Done",
            command=close_menu,
            font=("Sans", 16, "bold"),
            bg="#007b70",
            fg="white",
            height=2,
            width=16,
            takefocus=False,
        ).pack(pady=12)
        dialog.protocol("WM_DELETE_WINDOW", close_menu)

    def toggle_review():
        state["review_view"] = {"detail": "full", "full": "live", "live": "detail"}[
            state["review_view"]
        ]
        refresh()

    def close():
        if state["closing"]:
            return
        close_menu()
        state["closing"] = True
        worker.stopping.set()
        status.set("Closing camera and scale…")
        refresh()

    edit_button = tk.Button(
        identity,
        text="Edit",
        command=edit_package,
        font=("Sans", 14, "bold"),
        bg="#345269",
        fg="white",
        height=2,
        width=8,
        takefocus=False,
    )
    edit_button.pack(side="right", padx=6, pady=4)
    primary_button = tk.Button(
        footer,
        text="Capture empty station",
        command=primary,
        font=("Sans", font, "bold"),
        bg="#007b70",
        fg="white",
        disabledforeground="#a0acb4",
        relief="flat",
        width=20,
        height=4,
        wraplength=220,
        takefocus=False,
    )
    primary_button.grid(row=0, column=0, padx=5, sticky="nsew")
    more_button = tk.Button(
        footer,
        text="More",
        command=more,
        font=("Sans", font, "bold"),
        bg="#345269",
        fg="white",
        relief="flat",
        width=9,
        height=4,
        takefocus=False,
    )
    more_button.grid(row=0, column=1, padx=5, sticky="nsew")
    review_button = tk.Button(
        root,
        text="Marked result",
        command=toggle_review,
        font=("Sans", 14, "bold"),
        bg="#345269",
        fg="white",
        height=2,
        width=20,
        takefocus=False,
    )
    fallback_button = tk.Button(
        root,
        text="Measure dimensions only",
        command=lambda: submit("dimensions_only"),
        font=("Sans", 13, "bold"),
        bg="#805623",
        fg="white",
        height=2,
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
    exit_button.place(relx=1, x=-8, y=8, anchor="ne", width=64, height=64)
    root.protocol("WM_DELETE_WINDOW", close)
    root.bind("<Escape>", lambda event: close())

    def resize(event):
        if event.widget is not root:
            return
        width = max(180, event.width - 30)
        for label in (headline_label, status_label, scale_label):
            label.configure(wraplength=width)
        package_label.configure(wraplength=max(140, width - 115))
        primary_button.configure(wraplength=max(140, min(220, width - 160)))

    root.bind("<Configure>", resize)
    refresh()
    worker.start()

    def load_review(path):
        with Image.open(path) as image:
            state["review"] = image.copy()
        station_image = Path(path).with_name("measurement.jpg")
        if station_image.exists():
            with Image.open(station_image) as image:
                state["detail"] = box_detail(image, state["result"] or {})
        else:
            state["detail"] = box_detail(state["review"], state["result"] or {})
        state["display_key"] = None

    def poll():
        try:
            while True:
                event, payload = worker.events.get_nowait()
                if event == "ready":
                    state.update(ready=True, busy=False, phase="setup")
                    status.set("Remove all boxes, then capture the empty station.")
                elif event in ("empty", "zero", "next"):
                    state.update(busy=False, pending=None, fallback=None, phase="label")
                    if event == "empty":
                        state["empty"] = True
                    clear_result()
                    if not state["empty"]:
                        state["phase"] = "setup"
                    status.set(
                        "Hold the barcode close and steady inside the guide. "
                        "It reads automatically."
                    )
                    if event == "empty" and payload and "Scale:" in payload:
                        status.set(payload)
                    if not state["empty"]:
                        status.set(
                            "Scale zeroed. Remove all boxes and capture the empty station next."
                        )
                elif event == "listening":
                    state["listening"] = payload
                elif event == "label_detected":
                    if not state["closing"]:
                        state.update(busy=True, phase="preparing", listening=False)
                        status.set("Label found. Getting the station ready…")
                elif event == "label":
                    if state["closing"]:
                        continue
                    state.update(
                        busy=False,
                        pending=payload,
                        phase="measure" if payload.get("package_id") else "choose",
                    )
                    clear_result()
                    if payload.get("package_id"):
                        status.set(
                            "ID held. Place this box against both walls, remove your hands, "
                            "then tap the green Measure box button."
                        )
                        if payload.get("package_id_source") != "manual" and state[
                            "beep_id"
                        ] != payload.get("scan_id"):
                            state["beep_id"] = payload.get("scan_id")
                            state["confirm_until"] = time.monotonic() + 4
                            with suppress(tk.TclError):
                                barcode_chime(root.bell)
                    else:
                        status.set(
                            "Several codes or an unusable ID were found. "
                            "Choose or enter the package ID."
                        )
                elif event == "skip":
                    state.update(busy=False, pending=None, phase="measure")
                    status.set("Barcode skipped. Place the box in the corner and tap Measure box.")
                elif event == "result":
                    result, path = payload
                    state.update(
                        busy=False,
                        pending=None,
                        result=result,
                        phase="result",
                        review_view="detail",
                    )
                    load_review(path)
                    for key in ("length", "width", "height"):
                        value = result["dimensions_inches"].get(key)
                        values[key].set("—" if value is None else f"{value:.2f}″")
                    weight = result.get("weight", {}).get("pounds")
                    values["weight"].set("—" if weight is None else f"{weight:.2f} lb")
                    note = (
                        "Dimensions and weight saved."
                        if weight is not None
                        else "Dimensions saved; weight unavailable."
                    )
                    movement = " ".join(result.get("station_movement_warnings", []))
                    outline = " ".join(result.get("outline_warnings", []))
                    status.set(
                        " ".join(
                            filter(
                                None,
                                (note, movement, outline, getattr(worker, "review_warning", "")),
                            )
                        )
                    )
                elif event == "package_id":
                    result, path = payload
                    state.update(busy=False, result=result)
                    load_review(path)
                    status.set("Package ID saved. " + getattr(worker, "review_warning", ""))
                elif event == "package_id_error":
                    state["busy"] = False
                    status.set("Could not save ID: " + payload[0])
                elif event == "watch_error":
                    state.update(busy=False, listening=False, phase="measure")
                    status.set(payload + " Use More to enter an ID or retry.")
                elif event in ("error", "weight_error", "zero_error"):
                    message, has_reference = payload
                    state.update(
                        busy=False,
                        empty=has_reference,
                        phase="measure" if has_reference else "setup",
                    )
                    clear_result()
                    status.set(message)
                    if event == "weight_error" and has_reference:
                        state["fallback"] = message
                        status.set(message + " Retry, or explicitly measure dimensions only.")
                elif event == "fatal":
                    state.update(ready=False, busy=False, listening=False, pending=None)
                    clear_result()
                    status.set("Camera stopped: " + payload + " Reopen the app.")
                elif event == "closed" and state["closing"]:
                    root.destroy()
                    return
                refresh()
        except queue.Empty:
            pass
        if state["closing"] and not worker.is_alive():
            root.destroy()
            return
        # The saved weight appears only in the result cards; this is current scale state.
        scale = getattr(worker, "scale", None)
        if scale is not None:
            reading = scale.snapshot()
            weight = reading["pounds"]
            text = "—" if weight is None else f"{0 if abs(weight) < 0.005 else weight:.2f} lb"
            live_weight.set(f"Scale: {text} · {display_text(reading['message'], 65)}")
            scale_label.configure(fg="#71dcc4" if reading["stable"] else "#ffca78")
        with suppress(queue.Empty):
            state["live"] = worker.frames.get_nowait()
        view = state["review_view"]
        image = (
            state["detail"]
            if view == "detail"
            else state["review"]
            if view == "full"
            else state["live"]
        )
        confirmed = state["phase"] == "measure" and time.monotonic() < state["confirm_until"]
        key = (
            id(image),
            preview_frame.winfo_width(),
            preview_frame.winfo_height(),
            view,
            state["listening"],
            confirmed,
        )
        if image is not None and key != state["display_key"]:
            image = image.copy()
            image.thumbnail(
                (max(10, preview_frame.winfo_width()), max(10, preview_frame.winfo_height()))
            )
            if view == "live" and (state["listening"] or confirmed):
                left, top, right, bottom = GUIDE
                rectangle = (
                    left * image.width,
                    top * image.height,
                    right * image.width,
                    bottom * image.height,
                )
                pen = ImageDraw.Draw(image)
                pen.rectangle(rectangle, outline="black", width=5)
                pen.rectangle(rectangle, outline="#71ffad" if confirmed else "#71dcc4", width=2)
            state["photo"] = ImageTk.PhotoImage(image)
            state["display_key"] = key
            preview.configure(image=state["photo"])
        root.after(100, poll)

    root.after(100, poll)
    try:
        root.mainloop()
    finally:
        worker.stopping.set()
        worker.join(timeout=4)
