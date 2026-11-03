"""Install a user-level Raspberry Pi desktop/menu shortcut, without sudo or autostart."""

import os
import subprocess
import sys
from pathlib import Path

from parcel_dimensioner.camera.contracts import CameraError

MARKER = "# Managed by parcel-dimensioner desktop-install"


def desktop_entry(project: Path, executable: str, config: Path | None, lens_position: float | None):
    def quote(value):
        value = str(value).replace("%", "%%")
        for character in ("\\", '"', "`", "$"):
            value = value.replace(character, "\\" + character)
        # Desktop files interpret backslash escapes before parsing the Exec command.
        return '"' + value.replace("\\", "\\\\") + '"'

    args = [executable, "-m", "parcel_dimensioner"]
    if config:
        args += ["--config", str(config.absolute())]
    args += ["touchscreen"]
    if lens_position is not None:
        args += ["--lens-position", str(lens_position)]
    path = str(project).replace("\\", "\\\\").replace("\n", "\\n")
    return (
        f"[Desktop Entry]\n{MARKER}\nType=Application\nName=Box Scanner\n"
        f"Comment=Capture and measure boxes\nExec={' '.join(map(quote, args))}\n"
        f"Path={path}\nIcon=camera-photo\nTerminal=false\nCategories=Utility;\n"
    )


def install_desktop(config=None, lens_position=None):
    if not sys.platform.startswith("linux"):
        raise CameraError(
            "Install the desktop shortcut on the Raspberry Pi, from its project folder."
        )
    project = Path.cwd()
    if not (project / "pyproject.toml").is_file():
        raise CameraError("Run desktop-install from the parcel-dimensioner project folder.")
    desktop = Path.home() / "Desktop"
    try:
        result = subprocess.run(
            ["xdg-user-dir", "DESKTOP"], capture_output=True, text=True, timeout=5, check=True
        )
        if result.stdout.strip():
            desktop = Path(result.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        pass
    applications = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share")))
    targets = [
        applications / "applications/parcel-dimensioner.desktop",
        desktop / "parcel-dimensioner.desktop",
    ]
    for target in targets:
        if target.exists():
            existing = target.read_text(encoding="utf-8")
            if not existing.startswith(
                (MARKER + "\n[Desktop Entry]\n", "[Desktop Entry]\n" + MARKER + "\n")
            ):
                raise CameraError(f"Will not replace an unrelated shortcut: {target}")
    text = desktop_entry(project, sys.executable, config, lens_position)
    try:
        for target in targets:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
            target.chmod(target.stat().st_mode | 0o100)
    except OSError as exc:
        raise CameraError(f"Could not install shortcut: {exc}") from exc
    return targets
