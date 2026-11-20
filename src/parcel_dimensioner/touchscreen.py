"""Two-step touchscreen workflow: show a barcode, then measure the box."""

from __future__ import annotations

import queue
import sqlite3
import subprocess
import sys
import time
from contextlib import suppress
from pathlib import Path

from parcel_dimensioner.barcode_watch import GUIDE
from parcel_dimensioner.camera.contracts import CameraError
from parcel_dimensioner.feedback import barcode_chime
from parcel_dimensioner.measurement import _vision
from parcel_dimensioner.measurement_store import ApiError
from parcel_dimensioner.package_id_editor import display_text, open_package_id_editor
from parcel_dimensioner.review import box_detail
from parcel_dimensioner.settings import ScaleSettings
from parcel_dimensioner.station_worker import StationWorker

# Background, border, heading, and message colors for the operator status panel.
_STATUS_STYLE = {
    "normal": ("#152a38", "#2b4150", "#f4f8fa", "#c3d6e2"),
    "request": ("#1c2b3a", "#b98645", "#f5f8fb", "#d6e2ec"),
    "success": ("#17332e", "#397565", "#8fe5cd", "#d2eee6"),
    "warning": ("#372a1c", "#bf8544", "#ffdb9b", "#ffeacb"),
    "error": ("#39242c", "#b56b78", "#ffc4cb", "#ffe2e6"),
}


def _poweroff():
    if not sys.platform.startswith("linux"):
        raise OSError("Shutdown is available on the Raspberry Pi desktop.")
    subprocess.run(
        ["systemctl", "poweroff"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )


def launch_touchscreen(
    settings, captures_dir: Path, *, scale_settings=None, api_settings=None, windowed=False
):
    try:
        import tkinter as tk
        from tkinter import messagebox

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
    api_enabled = bool(api_settings and api_settings.enabled)
    options = {"api_settings": api_settings} if api_enabled else {}
    worker = StationWorker(settings, captures_dir, scale_settings or ScaleSettings(), **options)
    state = dict(
        ready=False,
        empty=False,
        busy=True,
        closing=False,
        poweroff=False,
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
        request_id=None,
        barcode_already_scanned=False,
        measuring_request_id=None,
        request_ended=False,
        status_message="Connecting to the station.",
        alert=None,
    )
    portrait = root.winfo_screenheight() > root.winfo_screenwidth()
    font = 18 if portrait else 16
    preview_frame = tk.Frame(root, bg="#08131b", height=100)
    preview_frame.pack_propagate(False)
    preview_frame.pack(fill="both", expand=True, padx=8, pady=(8, 6))
    preview = tk.Label(preview_frame, bg="#08131b")
    preview.pack(fill="both", expand=True)

    status_panel = tk.Frame(root, bg="#152a38", highlightthickness=1, highlightbackground="#2b4150")
    status_panel.pack(fill="x", padx=12, pady=(1, 4))
    headline = tk.StringVar(value="Starting camera…")
    headline_label = tk.Label(
        status_panel,
        textvariable=headline,
        font=("Sans", 21 if portrait else 19, "bold"),
        anchor="w",
        justify="left",
        wraplength=600,
    )
    headline_label.pack(fill="x", padx=10, pady=(3, 0))
    status = tk.StringVar(value=state["status_message"])
    status_label = tk.Label(
        status_panel,
        textvariable=status,
        font=("Sans", 15 if portrait else 13),
        anchor="w",
        wraplength=600,
        justify="left",
    )
    status_label.pack(fill="x", padx=10, pady=(0, 6))
    request_notice = tk.Label(
        status_panel,
        bg="#ffcf87",
        fg="#251b10",
        font=("Sans", 23 if portrait else 22, "bold"),
        anchor="w",
        padx=11,
        pady=4,
        justify="left",
    )
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

    def controls_available():
        return not (state["busy"] or state["closing"] or state["editing"])

    def idle():
        return state["ready"] and controls_available()

    def set_status(message, *, alert=None, tone="warning"):
        state.update(status_message=message, request_ended=False)
        state["alert"] = (alert, tone) if alert else None

    def refresh():
        target = state["pending"] or state["result"] or {}
        value = target.get("package_id")
        candidates = target.get("barcode", {}).get("candidates", [])
        identity.pack_forget()
        results.pack_forget()
        review_button.pack_forget()
        scale_label.pack_forget()
        fallback_button.pack_forget()
        continue_button.pack_forget()
        request_active = state["request_id"] is not None and state["ready"] and not state["closing"]
        if request_active:
            notice = (
                "Capture in progress"
                if state["measuring_request_id"] is not None
                else "Capture requested"
            )
            request_notice.configure(text=notice)
            request_notice.pack(fill="x", before=headline_label)
        else:
            request_notice.pack_forget()
        if api_enabled and state["phase"] == "label" and idle():
            continue_button.pack(pady=(2, 0), before=footer)
        if target:
            if value:
                package_value.set("ID: " + display_text(value, 44))
            elif candidates:
                package_value.set("Choose the correct barcode")
            elif target.get("barcode", {}).get("status") == "skipped":
                package_value.set("Barcode skipped")
            else:
                package_value.set("No barcode detected")
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
        elif state["barcode_already_scanned"]:
            title = "Place box in corner"
        else:
            title = "2 · Place box in corner"
        if state["closing"]:
            title = "Shutting down…" if state["poweroff"] else "Closing…"
        tone = "request" if request_active else "normal"
        if state["phase"] == "result" and not request_active and not state["closing"]:
            tone = "success"
        detail = state["status_message"]
        if state["request_ended"]:
            title, tone = "Capture request ended", "warning"
            detail = "Ask the receiving app for a new request to link the next scan."
        if state["alert"]:
            title, tone = state["alert"]
            detail = state["status_message"]
        if request_active and state["phase"] == "result":
            next_box = "Tap Next box to continue."
            detail = f"{detail} {next_box}" if state["alert"] else next_box
        background, border, heading, message = _STATUS_STYLE[tone]
        status_panel.configure(bg=background, highlightbackground=border, highlightcolor=border)
        headline_label.configure(bg=background, fg=heading)
        status_label.configure(bg=background, fg=message)
        headline.set(title)
        status.set(detail)
        ready_to_measure = idle() and state["phase"] == "measure"
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
        controls = "normal" if controls_available() else "disabled"
        more_button.configure(state=controls)
        shutdown_button.configure(state=controls)
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
        if state["barcode_already_scanned"] and action in ("clear_label", "skip"):
            return
        reason = state["fallback"] if action == "dimensions_only" else None
        if action == "dimensions_only" and reason is None:
            return
        command = ("scan", reason) if action == "dimensions_only" else action
        if api_enabled and action in ("scan", "dimensions_only"):
            try:
                command = worker.begin_measurement(state["request_id"], reason)
            except ApiError as exc:
                if exc.payload["error"]["code"] == "request_changed":
                    set_status(
                        "Check the request shown here, then tap Measure box again.",
                        alert="Capture request changed",
                    )
                else:
                    set_status(str(exc), alert="Capture unavailable", tone="error")
                refresh()
                return
            except (sqlite3.Error, OSError):
                set_status("Check the Pi and retry.", alert="Storage unavailable", tone="error")
                refresh()
                return
            state["measuring_request_id"] = state["request_id"]
        state.update(busy=True, fallback=None)
        if action in ("empty", "zero", "next", "clear_label", "skip"):
            state["pending"] = None
        if action == "empty":
            state["empty"] = False
        clear_result()
        set_status(
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
        worker.commands.put_nowait(command)

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
        if not idle() or (state["barcode_already_scanned"] and state["result"] is None):
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
            if (draft or pending) and state["barcode_already_scanned"]:
                return
            state["busy"] = True
            set_status("Saving package ID…")
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
        if not controls_available() or state["menu"] is not None:
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

        barcode_options = idle() and state["empty"] and not state["barcode_already_scanned"]
        can_edit_id = barcode_options or (idle() and state["result"] is not None)
        options = (
            ("Capture empty station", lambda: perform("empty"), idle()),
            ("Zero scale", lambda: perform("zero"), idle()),
            ("Enter / edit ID", edit_package, can_edit_id),
            ("Rescan label", lambda: perform("clear_label"), barcode_options),
            ("Measure without barcode", lambda: perform("skip"), barcode_options),
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

    def close(*, poweroff=False):
        if state["closing"]:
            return
        close_menu()
        state.update(closing=True, poweroff=poweroff)
        worker.stopping.set()
        set_status("Closing the scanner and releasing devices…")
        refresh()

    def confirm_shutdown():
        if not controls_available():
            return
        worker.paused.set()
        confirmed = messagebox.askyesno(
            "Shut down Raspberry Pi?",
            "This will close Box Scanner and power off the Pi.",
            default=messagebox.NO,
            parent=root,
        )
        worker.paused.clear()
        if confirmed:
            close(poweroff=True)

    def finish_close():
        if state["poweroff"]:
            try:
                _poweroff()
            except (OSError, subprocess.SubprocessError) as exc:
                reason = str(exc)
                if isinstance(exc, subprocess.CalledProcessError) and exc.stderr:
                    reason = exc.stderr.strip()
                messagebox.showerror(
                    "Shutdown failed",
                    f"{reason}\n\nYou can shut down from the Raspberry Pi desktop.",
                    parent=root,
                )
        root.destroy()

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
    continue_button = tk.Button(
        root,
        text="Continue without barcode",
        command=lambda: submit("skip"),
        font=("Sans", 15, "bold"),
        bg="#345269",
        fg="white",
        height=2,
        padx=10,
        takefocus=False,
    )
    shutdown_button = tk.Button(
        preview_frame,
        text="Shutdown",
        command=confirm_shutdown,
        font=("Sans", 11, "bold"),
        bg="#543f4a",
        fg="white",
        disabledforeground="#a0acb4",
        relief="flat",
        takefocus=False,
    )
    shutdown_button.place(relx=1, x=-8, y=8, anchor="ne", width=104, height=64)
    root.protocol("WM_DELETE_WINDOW", close)
    root.bind("<Escape>", lambda event: close())

    def resize(event):
        if event.widget is not root:
            return
        width = max(180, event.width - 30)
        for label in (headline_label, status_label, request_notice):
            label.configure(wraplength=width - 22)
        scale_label.configure(wraplength=width)
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

    def show_box_step():
        state.update(pending=None, fallback=None, confirm_until=0)
        if not state["empty"]:
            state["phase"] = "setup"
            set_status("Scale zeroed. Remove all boxes and capture the empty station next.")
        elif state["barcode_already_scanned"]:
            state["phase"] = "measure"
            set_status("Barcode already scanned in the receiving app. Tap Measure box when ready.")
        else:
            state["phase"] = "label"
            set_status("Hold the barcode steady in the guide.")

    def poll():
        try:
            while True:
                event, payload = worker.events.get_nowait()
                if event == "ready":
                    state.update(ready=True, busy=False, phase="setup")
                    set_status("Remove all boxes, then capture the empty station.")
                elif event == "capture_request":
                    previous_request = state["request_id"]
                    previous = state["barcode_already_scanned"]
                    state["request_id"], state["barcode_already_scanned"] = payload or (None, False)
                    if (
                        previous != state["barcode_already_scanned"]
                        and state["empty"]
                        and state["phase"] != "result"
                        and not state["busy"]
                    ):
                        show_box_step()
                    state["request_ended"] = previous_request is not None and payload is None
                elif event in ("empty", "zero", "next"):
                    state["busy"] = False
                    if event == "empty":
                        state["empty"] = True
                    clear_result()
                    show_box_step()
                    if event == "empty" and payload and "Scale:" in payload:
                        set_status(payload, alert="Check the scale")
                elif event == "listening":
                    state["listening"] = payload
                elif event == "label_detected":
                    if not state["closing"] and not state["barcode_already_scanned"]:
                        state.update(busy=True, phase="preparing", listening=False)
                        set_status("Label found. Getting the station ready…")
                elif event == "label" and state["barcode_already_scanned"]:
                    state["busy"] = False
                    show_box_step()
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
                        set_status(
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
                        set_status(
                            "Several codes or an unusable ID were found. "
                            "Choose or enter the package ID."
                        )
                elif event == "skip":
                    state.update(busy=False, pending=None, phase="measure")
                    set_status("Barcode skipped. Place the box in the corner and tap Measure box.")
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
                    completed_request = state["measuring_request_id"]
                    state["measuring_request_id"] = None
                    note = (
                        "Dimensions and weight saved."
                        if weight is not None
                        else "Dimensions saved; weight unavailable."
                    )
                    if completed_request is not None and weight is not None:
                        note = "Ready for the receiving app."
                        if state["request_id"] == completed_request:
                            state.update(request_id=None, barcode_already_scanned=False)
                    if api_enabled and weight is None:
                        note = "Sharing requires a measured weight."
                    movement = " ".join(result.get("station_movement_warnings", []))
                    outline = " ".join(result.get("outline_warnings", []))
                    alert = None
                    if api_enabled and weight is None:
                        alert = "Dimensions saved locally"
                    elif movement or outline or getattr(worker, "review_warning", ""):
                        alert = "Measurement saved"
                    set_status(
                        " ".join(
                            filter(
                                None,
                                (note, movement, outline, getattr(worker, "review_warning", "")),
                            )
                        ),
                        alert=alert,
                    )
                elif event == "package_id":
                    result, path = payload
                    state.update(busy=False, result=result)
                    load_review(path)
                    note = (
                        "Local ID saved. The shared barcode is fixed at capture."
                        if api_enabled and result.get("weight", {}).get("pounds") is not None
                        else "Package ID saved."
                    )
                    warning = getattr(worker, "review_warning", "")
                    set_status(
                        " ".join(filter(None, (note, warning))),
                        alert="Check the marked result" if warning else None,
                    )
                elif event == "package_id_error":
                    state["busy"] = False
                    set_status(payload[0], alert="Could not save ID", tone="error")
                elif event == "watch_error":
                    state.update(
                        busy=False, listening=False, phase="label" if api_enabled else "measure"
                    )
                    set_status(
                        payload + " Use More to enter an ID or retry.",
                        alert="Barcode reader unavailable",
                    )
                elif event in ("error", "weight_error", "zero_error"):
                    message, has_reference = payload
                    state.update(
                        busy=False,
                        empty=has_reference,
                        phase="measure" if has_reference else "setup",
                        measuring_request_id=None,
                    )
                    clear_result()
                    if event == "weight_error" and has_reference:
                        state["fallback"] = message
                        set_status(
                            message + " Tap Measure box to retry.",
                            alert="Weight unavailable",
                        )
                    else:
                        alert = "Scan needs attention"
                        if event == "zero_error":
                            alert = "Scale zero failed"
                        set_status(message, alert=alert, tone="error")
                elif event in ("fatal", "api_error"):
                    state.update(ready=False, busy=False, listening=False, pending=None)
                    clear_result()
                    if event == "api_error":
                        set_status(
                            payload + " Check the API settings and reopen Box Scanner.",
                            alert="Station API unavailable",
                            tone="error",
                        )
                    else:
                        set_status(
                            payload + " Reopen Box Scanner.", alert="Camera stopped", tone="error"
                        )
                refresh()
        except queue.Empty:
            pass
        if state["closing"] and not worker.is_alive():
            finish_close()
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
