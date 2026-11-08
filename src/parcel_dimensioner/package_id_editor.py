"""Touch-friendly candidate selection and ID entry, with no OS keyboard dependency."""

from parcel_dimensioner.barcodes import PackageIdError, validate_package_id


def display_text(value, limit=56):
    text = "".join(c if c.isprintable() else " " for c in value)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def open_package_id_editor(parent, result, save, closed):
    import tkinter as tk

    dialog = tk.Toplevel(parent)
    dialog.title("Package ID")
    dialog.configure(bg="#10202c")
    dialog.transient(parent)
    width = max(300, min(760, parent.winfo_width() - 24))
    height = min(650, parent.winfo_height() - 24)
    x = parent.winfo_rootx() + (parent.winfo_width() - width) // 2
    y = parent.winfo_rooty() + max(0, (parent.winfo_height() - height) // 2)
    dialog.geometry(f"{width}x{height}+{x}+{y}")
    dialog.minsize(300, 470)
    dialog.grab_set()
    value = tk.StringVar(value=result.get("package_id") or "")
    error = tk.StringVar(value="")
    selected = [result.get("barcode", {}).get("selected_index")]
    candidates = result.get("barcode", {}).get("candidates", [])
    tk.Label(dialog, text="Package ID", bg="#10202c", fg="white", font=("Sans", 19, "bold")).pack(
        pady=(8, 4)
    )

    if candidates:
        tk.Label(
            dialog,
            text="Tap the correct barcode, or enter an ID below.",
            bg="#10202c",
            fg="#c3d6e2",
            wraplength=width - 20,
            font=("Sans", 12),
        ).pack()
        choices = tk.Frame(dialog, bg="#10202c")
        choices.pack(fill="x", padx=10, pady=5)
        listing = tk.Listbox(
            choices,
            height=min(3, len(candidates)),
            font=("Sans", 14),
            exportselection=False,
            bg="#193442",
            fg="white",
            selectbackground="#007b70",
            activestyle="none",
        )
        scrollbar = tk.Scrollbar(choices, command=listing.yview)
        listing.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        listing.pack(side="left", fill="x", expand=True)
        for candidate in candidates:
            listing.insert(
                "end",
                display_text(candidate["value"], 80) + "  [" + "/".join(candidate["formats"]) + "]",
            )

        def choose(_):
            choice = listing.curselection()
            if choice:
                index = choice[0]
                value.set(candidates[index]["value"])
                selected[0] = index

        listing.bind("<<ListboxSelect>>", choose)
        if selected[0] is not None:
            listing.selection_set(selected[0])
    else:
        tk.Label(
            dialog,
            text="Enter the package/shipment ID manually.",
            bg="#10202c",
            fg="#ffca78",
            wraplength=width - 20,
            font=("Sans", 12),
        ).pack(pady=4)

    entry = tk.Entry(dialog, textvariable=value, font=("Sans", 18), relief="flat")
    entry.pack(fill="x", padx=10, pady=8, ipady=6)

    def edited(*_):
        selected[0] = None

    value.trace_add("write", edited)
    tk.Label(
        dialog,
        textvariable=error,
        bg="#10202c",
        fg="#ffca78",
        wraplength=width - 20,
        font=("Sans", 11),
    ).pack(fill="x", padx=10)

    def insert(text):
        if entry.selection_present():
            entry.delete("sel.first", "sel.last")
        entry.insert("insert", text)
        entry.focus_set()

    def backspace():
        if entry.selection_present():
            entry.delete("sel.first", "sel.last")
        elif entry.index("insert"):
            index = entry.index("insert")
            entry.delete(index - 1, index)
        entry.focus_set()

    def dismiss():
        dialog.grab_release()
        dialog.destroy()
        closed()

    def commit():
        try:
            validate_package_id(value.get())
        except PackageIdError as exc:
            error.set(str(exc))
            return
        chosen, index = value.get(), selected[0]
        dismiss()
        save(chosen, index)

    footer = tk.Frame(dialog, bg="#10202c")
    footer.pack(side="bottom", fill="x", padx=8, pady=8)
    for column, (text, command, color) in enumerate(
        [("Save ID", commit, "#007b70"), ("Close", dismiss, "#543f4a")]
    ):
        footer.columnconfigure(column, weight=1)
        tk.Button(
            footer,
            text=text,
            command=command,
            font=("Sans", 14, "bold"),
            bg=color,
            fg="white",
            height=2,
            takefocus=False,
        ).grid(row=0, column=column, sticky="ew", padx=3)

    keyboard = tk.Frame(dialog, bg="#10202c")
    keyboard.pack(fill="both", expand=True, padx=7, pady=4)
    letters, lower = [], [False]
    for row, keys in enumerate(("1234567890", "QWERTYUIOP", "ASDFGHJKL-", "ZXCVBNM_/.")):
        keyboard.rowconfigure(row, weight=1)
        for column, character in enumerate(keys):
            keyboard.columnconfigure(column, weight=1, uniform="keys")
            key = tk.Button(
                keyboard,
                text=character,
                font=("Sans", 13, "bold"),
                bg="#345269",
                fg="white",
                takefocus=False,
                width=1,
            )
            key.configure(command=lambda k=key: insert(k.cget("text")))
            key.grid(row=row, column=column, sticky="nsew", padx=2, pady=2)
            if character.isalpha():
                letters.append(key)

    def change_case():
        lower[0] = not lower[0]
        for key in letters:
            text = key.cget("text")
            key.configure(text=text.lower() if lower[0] else text.upper())

    keyboard.rowconfigure(4, weight=1)
    for column, span, text, command in (
        (0, 2, "ABC/abc", change_case),
        (2, 3, "Space", lambda: insert(" ")),
        (5, 3, "Backspace", backspace),
        (8, 2, "Clear", lambda: value.set("")),
    ):
        tk.Button(
            keyboard,
            text=text,
            command=command,
            font=("Sans", 11),
            bg="#345269",
            fg="white",
            takefocus=False,
        ).grid(row=4, column=column, columnspan=span, sticky="nsew", padx=2, pady=2)
    dialog.protocol("WM_DELETE_WINDOW", dismiss)
    dialog.bind("<Escape>", lambda _: (dismiss(), "break")[1])
    entry.focus_set()
    entry.selection_range(0, "end")
    return dialog
