"""Romulan CLI entry point.

Usage:
    romulan input.txt --build --upload [--port PORT]
    romulan hardware <subcommand> ...

Examples:
    romulan program.txt --build           # Build bin/rom.bin only
    romulan program.txt --build --upload  # Build and upload
    romulan --upload                      # Upload existing bin/rom.bin
    romulan program.txt --upload --port /dev/ttyACM0
    romulan hardware upload bin/rom.bin --port /dev/ttyACM0
    romulan hardware capture --max-cycles 500
    romulan hardware monitor --disable
    romulan hardware reset --assert
    romulan hardware request-addr
"""

import argparse
import sys
from pathlib import Path

import serial

from .build_rom import build_rom
from .upload_rom import find_pico_port, upload


def create_parser() -> argparse.ArgumentParser:
    """Build the argument parser for the default build/upload workflow.

    Returns:
        A configured :class:`argparse.ArgumentParser` accepting the input
        file plus the ``--build``, ``--upload``, ``--output``, and ``--port``
        options.
    """
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
        help="Upload the ROM image to the Pico (plain-text protocol)",
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


def _create_hardware_parser_standalone() -> argparse.ArgumentParser:
    """Create a dedicated parser for the ``hardware`` sub-command.

    The ``hardware`` command has its own subcommands (``upload``, ``capture``,
    ``monitor``, ``reset``, ``request-addr``), each sharing the common
    ``--port`` and ``--verbose`` options.

    Returns:
        A configured :class:`argparse.ArgumentParser` for ``romulan hardware``.
    """
    parser = argparse.ArgumentParser(
        prog="romulan hardware",
        description="Hardware API commands (framed serial protocol)",
    )
    sub = parser.add_subparsers(dest="hw_cmd", required=True)

    def _add_common_args(p):
        p.add_argument(
            "--port",
            default=None,
            help="Serial port for the Pico (auto-detected if omitted)",
        )
        p.add_argument(
            "--verbose",
            "-v",
            action="store_true",
            help="Print hardware protocol messages (SEND/RECV trace)",
        )

    # --- upload ---
    upload_parser = sub.add_parser(
        "upload",
        help="Upload a ROM binary using the framed protocol",
    )
    upload_parser.add_argument(
        "bin_path",
        type=Path,
        help="Path to the 32 KB ROM binary file",
    )
    _add_common_args(upload_parser)

    # --- capture ---
    capture_parser = sub.add_parser(
        "capture",
        help="Capture CPU bus cycles until STP or max_cycles",
    )
    capture_parser.add_argument(
        "--max-cycles",
        type=int,
        default=500,
        help="Maximum number of cycles to capture (default: 500)",
    )
    _add_common_args(capture_parser)

    # --- monitor ---
    monitor_parser = sub.add_parser(
        "monitor",
        help="Enable or disable the unstructured monitor output",
    )
    monitor_parser.add_argument(
        "--enable",
        action="store_true",
        dest="enable",
        help="Enable monitor output",
    )
    monitor_parser.add_argument(
        "--disable",
        action="store_true",
        dest="disable",
        help="Disable monitor output",
    )
    _add_common_args(monitor_parser)

    # --- reset ---
    reset_parser = sub.add_parser(
        "reset",
        help="Assert or release the CPU reset line",
    )
    reset_parser.add_argument(
        "--assert",
        action="store_true",
        dest="assert_reset",
        help="Hold CPU in reset",
    )
    reset_parser.add_argument(
        "--release",
        action="store_true",
        dest="release_reset",
        help="Release CPU from reset",
    )
    _add_common_args(reset_parser)

    # --- request-addr ---
    addr_parser = sub.add_parser(
        "request-addr",
        help="Request the current CPU address",
    )
    _add_common_args(addr_parser)

    return parser


def _resolve_port(port: str | None) -> str:
    """Return a usable serial port, auto-detecting one if not given.

    Args:
        port: An explicit serial device path, or ``None`` to auto-detect.

    Returns:
        The resolved serial device path.

    Raises:
        RuntimeError: If no port is given and none can be auto-detected.
    """
    if port is None:
        port = find_pico_port()
        print(f"Auto-detected Pico on {port}")
    return port


def _handle_hardware(args: argparse.Namespace) -> None:
    """Dispatch a parsed ``hardware`` sub-command against the hardware API.

    Opens a :class:`~romulan.hardware_api.HardwareAPI` on the resolved port and
    runs the requested operation (upload, capture, monitor, reset, or
    request-addr), printing results to stdout.

    Side effects:
        Opens the serial port and drives the hardware. May call
        :func:`sys.exit` with status 1 on conflicting flags or API errors.

    Args:
        args: Parsed arguments from the ``hardware`` sub-parser; must include
            ``hw_cmd``, ``port``, and ``verbose``.
    """
    from .hardware_api import HardwareAPI, HardwareAPIError

    cmd = args.hw_cmd
    port = _resolve_port(args.port)

    try:
        with HardwareAPI(port, verbose=args.verbose) as api:
            if cmd == "upload":
                data = args.bin_path.read_bytes()
                result = api.upload_rom(data)
                print(f"Upload result: {result}")

            elif cmd == "capture":
                result = api.read_until_stp(max_cycles=args.max_cycles)
                print(f"Capture finished: {result.reason}")
                print(f"Cycles captured: {len(result.cycles)}")
                for cycle in result.cycles:
                    print(f"  {cycle}")

            elif cmd == "monitor":
                if args.enable and args.disable:
                    print("ERROR: Cannot specify both --enable and --disable", file=sys.stderr)
                    sys.exit(1)
                if not args.enable and not args.disable:
                    print("ERROR: Must specify either --enable or --disable", file=sys.stderr)
                    sys.exit(1)
                api.monitor(enable=args.enable)
                print(f"Monitor {'enabled' if args.enable else 'disabled'}")

            elif cmd == "reset":
                if args.assert_reset and args.release_reset:
                    print("ERROR: Cannot specify both --assert and --release", file=sys.stderr)
                    sys.exit(1)
                if not args.assert_reset and not args.release_reset:
                    print("ERROR: Must specify either --assert or --release", file=sys.stderr)
                    sys.exit(1)
                api.reset(assert_reset=args.assert_reset)
                print(f"Reset {'asserted' if args.assert_reset else 'released'}")

            elif cmd == "request-addr":
                addr = api.request_addr()
                print(f"Current CPU address: 0x{addr:04X}")

    except (HardwareAPIError, TimeoutError) as exc:
        print(f"ERROR: Hardware API failed: {exc}", file=sys.stderr)
        sys.exit(1)
    except serial.SerialException as exc:
        print(f"ERROR: Serial communication failed: {exc}", file=sys.stderr)
        sys.exit(1)


def main() -> None:
    """Entry point for the ``romulan`` command-line tool.

    Routes to the ``hardware`` sub-command when it is the first argument;
    otherwise runs the default workflow, which builds a ROM image from an
    input file (``--build``) and/or uploads it to the Pico (``--upload``).

    Side effects:
        Parses ``sys.argv``, may read/write files, and may open the serial
        port to talk to the Pico. Exits via :func:`sys.exit` (or
        ``parser.error``) on invalid arguments or failures.
    """
    # When the first argument is "hardware" we dispatch to a dedicated
    # sub-parser so that the positional ``input`` argument does not
    # conflict with the sub-command name.
    if len(sys.argv) > 1 and sys.argv[1] == "hardware":
        hw_parser = _create_hardware_parser_standalone()
        args = hw_parser.parse_args(sys.argv[2:])
        _handle_hardware(args)
        return

    parser = create_parser()
    args = parser.parse_args()

    # Default (legacy) workflow
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
