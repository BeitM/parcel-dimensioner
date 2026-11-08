"""Command-line entry point for hardware bring-up and development."""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections.abc import Sequence
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path

from parcel_dimensioner.camera.contracts import CameraError
from parcel_dimensioner.camera.picamera2_backend import Picamera2Backend
from parcel_dimensioner.camera.service import CameraService
from parcel_dimensioner.diagnostics import camera_diagnostics
from parcel_dimensioner.measurement import MeasurementError, save_measurement
from parcel_dimensioner.settings import (
    PREVIEW_BACKENDS,
    AppSettings,
    CameraSettings,
    SettingsError,
    load_settings,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="parcel-dimensioner")
    parser.add_argument("--config", type=Path, help="path to a TOML configuration file")
    commands = parser.add_subparsers(dest="command", required=True)
    camera = commands.add_parser("camera", help="inspect and exercise the camera")
    camera_commands = camera.add_subparsers(dest="camera_command", required=True)

    camera_commands.add_parser("doctor", help="check the Pi camera environment")
    camera_commands.add_parser("list", help="list cameras reported by Picamera2")

    snapshot = camera_commands.add_parser("snapshot", help="capture one JPEG image")
    snapshot.add_argument("--output", type=Path, help="destination JPEG path")
    _add_capture_overrides(snapshot)

    preview = camera_commands.add_parser("preview", help="display the live camera view")
    preview.add_argument(
        "--duration",
        type=_nonnegative_float,
        default=0.0,
        help="seconds to show; zero runs until Ctrl+C (default: 0)",
    )
    preview.add_argument("--width", type=_positive_int)
    preview.add_argument("--height", type=_positive_int)
    preview.add_argument("--camera-index", type=_nonnegative_int)
    preview.add_argument("--preview-backend", choices=sorted(PREVIEW_BACKENDS))
    preview.add_argument("--lens-position", type=_nonnegative_float)

    calibration = camera_commands.add_parser(
        "calibration-capture", help="collect lens-calibration images with locked focus"
    )
    calibration.add_argument("--output-dir", type=Path, help="new directory for this session")
    calibration.add_argument("--count", type=_positive_int, default=25)
    calibration.add_argument("--preview-backend", choices=sorted(PREVIEW_BACKENDS))
    _add_capture_overrides(calibration)
    measure = commands.add_parser(
        "measure", help="experimental automatic fixed-wall box measurement"
    )
    measure.add_argument("--empty", type=Path, required=True, help="matching empty station JPEG")
    measure.add_argument("--box", type=Path, required=True, help="occupied station JPEG")
    measure.add_argument("--output-dir", type=Path, required=True, help="new folder for results")
    measure.add_argument("--square-mm", type=float, default=38.1, help="actual printed square size")
    measure.add_argument("--marker-mm", type=float, default=28.0, help="actual printed marker size")
    touchscreen = commands.add_parser("touchscreen", help="open the Pi touchscreen scanner app")
    touchscreen.add_argument("--windowed", action="store_true", help="open in a regular window")
    touchscreen.add_argument("--lens-position", type=_nonnegative_float)
    desktop = commands.add_parser("desktop-install", help="install a Box Scanner desktop shortcut")
    desktop.add_argument("--lens-position", type=_nonnegative_float)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "desktop-install":
            from parcel_dimensioner.desktop import install_desktop

            for path in install_desktop(args.config, args.lens_position):
                print(f"Installed {path}")
            return 0
        if args.command == "measure":
            result = save_measurement(
                args.empty,
                args.box,
                args.output_dir,
                square_mm=args.square_mm,
                marker_mm=args.marker_mm,
            )
            d = result["dimensions_inches"]
            print(
                f"Experimental estimate: {d['length']:.2f} x {d['width']:.2f} "
                f"x {d['height']:.2f} inches (L x W x H)"
            )
            for warning in result["warnings"]:
                print(f"Note: {warning}")
            print(f"Result and marked image saved in {args.output_dir}")
            return 0
        settings = load_settings(args.config)
        camera_settings = _camera_settings_with_overrides(settings.camera, args)
        if args.command == "touchscreen":
            from parcel_dimensioner.touchscreen import launch_touchscreen

            launch_touchscreen(
                camera_settings,
                settings.captures_dir,
                scale_settings=settings.scale,
                windowed=args.windowed,
            )
            return 0
        backend = Picamera2Backend(camera_settings.index, camera_settings.lens_position)

        if args.camera_command == "doctor":
            return _doctor(camera_settings.index)
        if args.camera_command == "list":
            return _list_cameras(backend)
        if args.camera_command == "snapshot":
            return _snapshot(backend, settings, camera_settings, args.output)
        if args.camera_command == "calibration-capture":
            output_dir = args.output_dir or settings.captures_dir / (
                "calibration-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
            )
            saved = backend.capture_calibration(
                output_dir,
                width=camera_settings.width,
                height=camera_settings.height,
                warmup_seconds=camera_settings.warmup_seconds,
                preview_backend=camera_settings.preview_backend,
                count=args.count,
            )
            print(f"Saved {saved} images in {output_dir}. Calibration has not been solved yet.")
            return 0 if saved else 1
        if args.camera_command == "preview":
            print("Opening preview; press Ctrl+C to close.")
            backend.preview(
                width=camera_settings.width,
                height=camera_settings.height,
                backend=camera_settings.preview_backend,
                duration_seconds=args.duration,
            )
            return 0
    except (SettingsError, CameraError, MeasurementError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 2


def _doctor(camera_index: int) -> int:
    checks = camera_diagnostics(camera_index)
    for check in checks:
        marker = "PASS" if check.ok else "FAIL"
        print(f"[{marker}] {check.name}: {check.detail}")
    return 0 if all(check.ok for check in checks) else 1


def _list_cameras(backend: Picamera2Backend) -> int:
    cameras = backend.list_cameras()
    if not cameras:
        print("No cameras detected.")
        return 1
    for camera in cameras:
        print(
            f"{camera.index}: model={camera.model} id={camera.identifier} "
            f"location={camera.location} rotation={camera.rotation}"
        )
    return 0


def _snapshot(
    backend: Picamera2Backend,
    app_settings: AppSettings,
    camera_settings: CameraSettings,
    output: Path | None,
) -> int:
    service = CameraService(backend, camera_settings, app_settings.captures_dir)
    result = service.capture(output)
    summary = asdict(result)
    summary["path"] = str(result.path)
    summary["captured_at"] = result.captured_at.isoformat()
    print(json.dumps(summary, indent=2))
    return 0


def _add_capture_overrides(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--width", type=_positive_int)
    parser.add_argument("--height", type=_positive_int)
    parser.add_argument("--warmup", type=_nonnegative_float)
    parser.add_argument("--camera-index", type=_nonnegative_int)
    parser.add_argument("--lens-position", type=_nonnegative_float)


def _camera_settings_with_overrides(
    settings: CameraSettings, args: argparse.Namespace
) -> CameraSettings:
    values = {
        "index": getattr(args, "camera_index", None),
        "width": getattr(args, "width", None),
        "height": getattr(args, "height", None),
        "warmup_seconds": getattr(args, "warmup", None),
        "preview_backend": getattr(args, "preview_backend", None),
        "lens_position": getattr(args, "lens_position", None),
    }
    return replace(settings, **{name: value for name, value in values.items() if value is not None})


def _positive_int(raw: str) -> int:
    value = int(raw)
    if value <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return value


def _nonnegative_int(raw: str) -> int:
    value = int(raw)
    if value < 0:
        raise argparse.ArgumentTypeError("must be zero or greater")
    return value


def _nonnegative_float(raw: str) -> float:
    value = float(raw)
    if not math.isfinite(value) or value < 0:
        raise argparse.ArgumentTypeError("must be finite and zero or greater")
    return value


if __name__ == "__main__":
    raise SystemExit(main())
