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
    romulan hardware peek --offset 0x7000 --count 16
    romulan hardware clock --hz 100
    romulan hardware status
"""

import argparse
import sys
from dataclasses import asdict
from pathlib import Path

import serial

from .build_rom import build_rom
from .hardware_api import HardwareAPI, HardwareAPIError
from .output import emit_error, emit_event, emit_result
from .upload_rom import find_pico_port


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
        help="Path to the input file: annotated hex dump or 6502 assembly "
        "(format auto-detected; required with --build)",
    )
    parser.add_argument(
        "--build",
        action="store_true",
        help="Build a .bin ROM image from the input file",
    )
    parser.add_argument(
        "--upload",
        action="store_true",
        help="Upload the ROM image to the Pico (framed Hardware API)",
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
    parser.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        help="Idle timeout in seconds with no framing progress (default: 30.0)",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Print hardware protocol messages (SEND/RECV trace) during --upload",
    )
    return parser


def _create_hardware_parser_standalone() -> argparse.ArgumentParser:
    """Create a dedicated parser for the ``hardware`` sub-command.

    The ``hardware`` command has its own subcommands (``upload``, ``capture``,
    ``monitor``, ``reset``, ``request-addr``, ``peek``), each sharing the common
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
            "--timeout",
            type=float,
            default=30.0,
            help="Idle timeout in seconds with no framing/capture progress (default: 30.0)",
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
        help="Enable or disable the JSON monitor output",
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

    # --- peek ---
    peek_parser = sub.add_parser(
        "peek",
        help="Read bytes from the loaded ROM image (--offset/--count) "
        "or live CPU bus/RAM (--addr)",
    )
    peek_parser.add_argument(
        "--offset",
        type=str,
        default=None,
        help="ROM-image offset to read from, hex or decimal (ROM mode)",
    )
    peek_parser.add_argument(
        "--count",
        type=int,
        default=None,
        help="Number of bytes to read, 1-64 (ROM mode only; default: 16)",
    )
    peek_parser.add_argument(
        "--addr",
        type=_parse_cpu_addr,
        default=None,
        help="CPU address to live-peek as hex, e.g. 0x4000 (live mode; "
        "resets CPU briefly)",
    )
    _add_common_args(peek_parser)

    # --- clock ---
    clock_parser = sub.add_parser(
        "clock",
        help="Set the 65C02 PHI2 clock frequency",
    )
    clock_parser.add_argument(
        "--hz",
        type=float,
        required=True,
        help="Clock frequency in Hz (0.1..1000)",
    )
    _add_common_args(clock_parser)

    # --- drive ---
    drive_parser = sub.add_parser(
        "drive",
        help="Force D0-D7 to a byte (diagnostic) or release the bus",
    )
    drive_parser.add_argument(
        "--value",
        type=str,
        default=None,
        help="2-digit hex byte to drive on D0-D7 (omit to release)",
    )
    drive_parser.add_argument(
        "--disable",
        action="store_true",
        dest="disable",
        help="Release D0-D7 and return to normal emulation",
    )
    _add_common_args(drive_parser)

    # --- status ---
    status_parser = sub.add_parser(
        "status",
        help="Query firmware state (clock, reset, ROM, monitor, last bus sample)",
    )
    _add_common_args(status_parser)

    return parser


def _parse_int(value: str) -> int:
    """Parse an integer that may be given in decimal or hex.

    Args:
        value: A string representing an integer, e.g. ``"28672"`` or ``"0x7000"``.

    Returns:
        The parsed integer.

    Raises:
        argparse.ArgumentTypeError: If the value cannot be parsed.
    """
    try:
        return int(value, 0)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid integer: {value!r}") from exc


def _parse_cpu_addr(text: str) -> int:
    """Parse a CPU address from CLI hex (``0x4000``, ``4000``, ``0X4000``)."""
    raw = text.strip()
    try:
        value = int(raw, 16)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"invalid address {text!r}; use hex like 0x4000 or 4000"
        ) from exc
    if not 0 <= value <= 0xFFFF:
        raise argparse.ArgumentTypeError(
            f"address out of range: {text!r} (must be 0000–FFFF)"
        )
    return value


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
        emit_event("port_detected", {"port": port, "auto_detected": True}, stream=sys.stderr)
    else:
        emit_event("port_detected", {"port": port, "auto_detected": False}, stream=sys.stderr)
    return port


def _handle_hardware(args: argparse.Namespace) -> None:
    """Dispatch a parsed ``hardware`` sub-command against the hardware API.

    Opens a :class:`~romulan.hardware_api.HardwareAPI` on the resolved port and
    runs the requested operation (upload, capture, monitor, reset,
    request-addr, peek, clock, or status), printing results to stdout.

    Side effects:
        Opens the serial port and drives the hardware. May call
        :func:`sys.exit` with status 1 on conflicting flags or API errors.

    Args:
        args: Parsed arguments from the ``hardware`` sub-parser; must include
            ``hw_cmd``, ``port``, and ``verbose``.
    """
    cmd = args.hw_cmd
    try:
        port = _resolve_port(args.port)
    except RuntimeError as exc:
        emit_error("port", str(exc))
        sys.exit(1)

    try:
        with HardwareAPI(port, timeout=args.timeout, verbose=args.verbose) as api:
            if cmd == "upload":
                data = args.bin_path.read_bytes()
                result = api.upload_rom(data)
                result["note"] = (
                    "CPU held in reset — run `hardware capture` "
                    "or `hardware reset --release` to run."
                )
                emit_result("upload_rom", result)

            elif cmd == "capture":
                def _print_cycle(cycle) -> None:
                    emit_event(
                        "cycle",
                        {
                            "seq": cycle.seq,
                            "addr": cycle.addr,
                            "data": cycle.data,
                            "rw": cycle.rw,
                        },
                    )

                result = api.read_until_stp(
                    max_cycles=args.max_cycles,
                    on_cycle=_print_cycle,
                )
                emit_result(
                    "read",
                    {"reason": result.reason, "cycles": len(result.cycles)},
                )

            elif cmd == "monitor":
                if args.enable and args.disable:
                    emit_error("bad_args", "Cannot specify both --enable and --disable")
                    sys.exit(1)
                if not args.enable and not args.disable:
                    emit_error("bad_args", "Must specify either --enable or --disable")
                    sys.exit(1)
                api.monitor(enable=args.enable)
                emit_result("monitor", {"enabled": bool(args.enable)})

            elif cmd == "reset":
                if args.assert_reset and args.release_reset:
                    emit_error("bad_args", "Cannot specify both --assert and --release")
                    sys.exit(1)
                if not args.assert_reset and not args.release_reset:
                    emit_error("bad_args", "Must specify either --assert or --release")
                    sys.exit(1)
                api.reset(assert_reset=args.assert_reset)
                emit_result("reset", {"asserted": bool(args.assert_reset)})

            elif cmd == "request-addr":
                addr = api.request_addr()
                emit_result("request_addr", {"addr": f"{addr:04X}"})

            elif cmd == "peek":
                live = args.addr is not None
                rom = args.offset is not None
                if live and rom:
                    emit_error("bad_args", "--addr and --offset are mutually exclusive")
                    sys.exit(1)
                if live and args.count is not None:
                    emit_error("bad_args", "--count is only valid with --offset")
                    sys.exit(1)
                if not live and not rom:
                    emit_error(
                        "bad_args",
                        "Must specify --addr (live bus) or --offset (ROM image)",
                    )
                    sys.exit(1)
                if live:
                    result = api.live_peek(args.addr)
                    emit_result(
                        "peek",
                        {
                            "mode": "live",
                            "addr": f"{result.addr:04X}",
                            "data": f"{result.data:02X}",
                        },
                    )
                else:
                    result = api.peek(offset=_parse_int(args.offset), count=args.count or 16)
                    emit_result(
                        "peek",
                        {
                            "mode": "rom",
                            "offset": result.offset,
                            "count": result.count,
                            "data": result.data.hex(),
                        },
                    )

            elif cmd == "clock":
                api.set_clock(hz=args.hz)
                emit_result("clock", {"hz": args.hz})

            elif cmd == "drive":
                if args.disable and args.value is not None:
                    emit_error("bad_args", "Cannot specify both --value and --disable")
                    sys.exit(1)
                if args.disable:
                    result = api.drive(None)
                else:
                    result = api.drive(args.value)
                emit_result(
                    "drive",
                    {"enabled": result.enabled, "value": result.value},
                )

            elif cmd == "status":
                st = api.status()
                emit_result("status", asdict(st))

            elif cmd == "peek":
                result = api.peek(args.addr)
                print(f"${result.addr:04X} = ${result.data:02X}")

    except (HardwareAPIError, TimeoutError) as exc:
        emit_error("hardware_api", f"Hardware API failed: {exc}")
        sys.exit(1)
    except serial.SerialException as exc:
        emit_error("serial", f"Serial communication failed: {exc}")
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
            emit_error("not_found", f"Input file not found: {args.input}")
            sys.exit(1)

        try:
            build_rom(args.input, args.output)
        except ValueError as exc:
            emit_error("build_failed", f"Build failed: {exc}")
            sys.exit(1)

    if args.upload:
        if not args.output.exists():
            emit_error(
                "no_rom",
                f"ROM file not found: {args.output}\n"
                "Run with --build first to produce the ROM image.",
            )
            sys.exit(1)

        port = args.port
        if port is None:
            try:
                port = find_pico_port()
                emit_event(
                    "port_detected",
                    {"port": port, "auto_detected": True},
                    stream=sys.stderr,
                )
            except RuntimeError as exc:
                emit_error("port", str(exc))
                sys.exit(1)
        else:
            emit_event(
                "port_detected",
                {"port": port, "auto_detected": False},
                stream=sys.stderr,
            )

        try:
            with HardwareAPI(
                port, timeout=args.timeout, verbose=args.verbose
            ) as api:
                result = api.upload_rom(args.output.read_bytes())
            result["note"] = (
                "CPU held in reset — run `romulan hardware capture` "
                "or `romulan hardware reset --release` to run."
            )
            emit_result("upload_rom", result)
        except (HardwareAPIError, TimeoutError) as exc:
            emit_error("hardware_api", f"Hardware API failed: {exc}")
            sys.exit(1)
        except serial.SerialException as exc:
            emit_error("serial", f"Serial communication failed: {exc}")
            sys.exit(1)
        except ValueError as exc:
            emit_error("upload", f"Upload failed: {exc}")
            sys.exit(1)


if __name__ == "__main__":
    main()
