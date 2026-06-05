"""Romulan CLI entry point.

Usage:
    romulan input.txt --build --upload [--port PORT]

Examples:
    romulan program.txt --build           # Build bin/rom.bin only
    romulan program.txt --build --upload  # Build and upload
    romulan --upload                      # Upload existing bin/rom.bin
    romulan program.txt --upload --port /dev/ttyACM0
"""

import argparse
import sys
from pathlib import Path

import serial

from .build_rom import build_rom
from .upload_rom import find_pico_port, upload

def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="romulan",
        description="Build and upload ROM images for the Pico-as-ROM 65C02 system.",
    )
    parser.add_argument(
        "input",
        nargs="?",
        type=Path,
        help="Path to the annotated hex dump input file (required with --build)",
    )
    parser.add_argument(
        "--build",
        action="store_true",
        help="Build a .bin ROM image from the input file",
    )
    parser.add_argument(
        "--upload",
        action="store_true",
        help="Upload the ROM image to the Pico",
    )
    parser.add_argument(
        "-o", "--output",
        type=Path,
        default=Path("bin/rom.bin"),
        help="Output ROM binary path (default: bin/rom.bin)",
    )
    parser.add_argument(
        "--port",
        default=None,
        help="Serial port for the Pico (auto-detected if omitted)",
    )
    return parser


def main() -> None:
    parser = create_parser()
    args = parser.parse_args()

    if not args.build and not args.upload:
        parser.error("At least one of --build or --upload is required.")

    if args.build:
        if not args.input:
            parser.error("--build requires an input file.")
        if not args.input.exists():
            print(f"ERROR: Input file not found: {args.input}", file=sys.stderr)
            sys.exit(1)

        try:
            build_rom(args.input, args.output)
        except ValueError as exc:
            print(f"ERROR: Build failed: {exc}", file=sys.stderr)
            sys.exit(1)

    if args.upload:
        if not args.output.exists():
            print(
                f"ERROR: ROM file not found: {args.output}\n"
                "Run with --build first to produce the ROM image.",
                file=sys.stderr,
            )
            sys.exit(1)

        port = args.port
        if port is None:
            try:
                port = find_pico_port()
                print(f"Auto-detected Pico on {port}")
            except RuntimeError as exc:
                print(f"ERROR: {exc}", file=sys.stderr)
                sys.exit(1)

        try:
            upload(port, args.output)
        except serial.SerialException as exc:
            print(f"ERROR: Serial communication failed: {exc}", file=sys.stderr)
            sys.exit(1)
        except TimeoutError as exc:
            print(f"ERROR: Upload timed out: {exc}", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
