#!/usr/bin/env python3
"""Plain-text ROM upload for the Pico-as-ROM 65C02 firmware.

Uploads a 32 KB ROM image over USB serial using the firmware's ``loadbin``
handshake: the host sends ``loadbin``, the Pico responds with
``OK send 32768 bytes``, the host streams 32,768 raw bytes, and the Pico
acknowledges with ``loaded 32768 bytes``.

Also handles CPU reset sequencing and ROM-emulator toggling so the CPU
restarts cleanly on the new image.
"""

from __future__ import annotations

import glob
import sys
import time
from pathlib import Path

try:
    import serial
    import serial.tools.list_ports
except ImportError:
    sys.stderr.write(
        "ERROR: pyserial is required.  Install with:\n"
        "    pip install --user pyserial\n"
    )
    sys.exit(1)


ROM_SIZE = 0x8000  # 32 KB — must match firmware
# Raspberry Pi Pico USB VID
RPI_VID = 0x2E8A


def find_pico_port() -> str:
    """Auto-detect a Raspberry Pi Pico serial port.

    Tries USB vendor ID ``0x2E8A`` (Raspberry Pi) first, then falls back to
    common port name patterns on Linux, macOS, and Windows.

    Returns:
        The device path of the detected Pico (e.g. ``/dev/ttyACM0``).

    Raises:
        RuntimeError: If zero or more than one matching port is found.
    """
    # First try pyserial's list_ports with VID filter
    ports = list(serial.tools.list_ports.comports())
    pico_ports = [
        p.device
        for p in ports
        if p.vid == RPI_VID or (p.manufacturer and "Raspberry Pi" in p.manufacturer)
    ]

    if len(pico_ports) == 1:
        return pico_ports[0]
    if len(pico_ports) > 1:
        raise RuntimeError(
            f"Multiple Raspberry Pi Pico devices found: {pico_ports}. "
            "Please specify one with --port."
        )

    # Fallback: guess by port name patterns
    patterns = [
        "/dev/ttyACM*",
        "/dev/ttyUSB*",
        "/dev/cu.usbmodem*",
        "/dev/tty.usbmodem*",
    ]
    candidates = []
    for pattern in patterns:
        candidates.extend(glob.glob(pattern))

    # On Windows, COM ports don't glob; list_ports should have caught them.
    # If we still have nothing, check for COM ports via list_ports.
    if not candidates and sys.platform == "win32":
        candidates = [p.device for p in ports]

    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        raise RuntimeError(
            f"Multiple serial ports found: {candidates}. "
            "Please specify one with --port."
        )

    raise RuntimeError(
        "No Raspberry Pi Pico serial port found.\n"
        "Please ensure your Pico is connected and running the firmware, "
        "or specify the port explicitly with --port."
    )


def read_until(ser: serial.Serial, needle: str, timeout: float = 3.0) -> str:
    """Read from a serial port until a substring appears or time runs out.

    Non-empty lines received are echoed to stdout prefixed with ``<<``.

    Args:
        ser: An open :class:`serial.Serial` connection.
        needle: Substring to search for in the accumulated receive buffer.
        timeout: Maximum wait time in seconds. Defaults to ``3.0``.

    Returns:
        The full receive buffer once ``needle`` is found.

    Raises:
        TimeoutError: If ``needle`` is not seen before the deadline.
    """
    deadline = time.time() + timeout
    buf = ""
    while time.time() < deadline:
        chunk = ser.read(256).decode(errors="replace")
        if chunk:
            buf += chunk
            for line in chunk.splitlines():
                line = line.strip()
                if line:
                    print(f"  << {line}")
            if needle in buf:
                return buf
    raise TimeoutError(f"never saw {needle!r} in {buf!r}")


def upload(port: str, path: Path) -> None:
    """Upload a 32 KB ROM image via the plain-text ``loadbin`` protocol.

    Side effects:
        Opens the serial port at 115200 baud, asserts CPU reset (``r0``),
        disables ROM emulation (``roms``), streams the binary, re-enables ROM
        and clock (``rom``, ``c100``), releases reset (``r1``), captures ~5 s
        of live output, then closes the port.

    Args:
        port: Serial device path for the Pico.
        path: Path to the ROM binary; must be exactly :data:`ROM_SIZE` bytes.

    Raises:
        SystemExit: If ``path`` is not exactly 32 KB (via ``sys.exit``).
        TimeoutError: If the firmware does not respond during handshake.
    """
    data = path.read_bytes()
    if len(data) != ROM_SIZE:
        sys.exit(
            f"ERROR: {path} is {len(data)} bytes, expected exactly "
            f"{ROM_SIZE} ({ROM_SIZE // 1024} KB)"
        )

    print(f"Opening {port} ...")
    ser = serial.Serial(port, 115200, timeout=0.2)
    time.sleep(0.3)
    ser.reset_input_buffer()

    # Make sure the CPU isn't actively reading from the ROM region while we
    # rewrite it — the firmware also disables `rom` internally, but flipping
    # it explicitly + asserting reset is cleaner and stops the CPU.
    print(">> assert RESET")
    ser.write(b"r0\n")
    time.sleep(0.05)
    print(">> rom off")
    ser.write(b"roms\n")
    time.sleep(0.05)
    ser.reset_input_buffer()

    print(">> loadbin")
    ser.write(b"loadbin\n")
    read_until(ser, "OK send", timeout=3.0)

    print(f">> sending {len(data)} bytes ...")
    t0 = time.time()
    ser.write(data)
    ser.flush()
    read_until(ser, "loaded", timeout=10.0)
    dt = time.time() - t0
    print(f"   ({dt:.2f} s, {len(data) / dt / 1024:.1f} KB/s)")

    print(">> rom on")
    ser.write(b"rom\n")
    time.sleep(0.05)
    print(">> watch 4000")
    ser.write(b"watch 4000\n")
    time.sleep(0.05)
    print(">> c100  (start 100 kHz clock — safe with flaky 3.3 V RAM)")
    ser.write(b"c100\n")
    time.sleep(0.05)
    print(">> release RESET")
    ser.write(b"r1\n")

    print("\n--- live output for 5 s (Ctrl-C to stop early) ---")
    try:
        deadline = time.time() + 5.0
        while time.time() < deadline:
            chunk = ser.read(256).decode(errors="replace")
            if chunk:
                sys.stdout.write(chunk)
                sys.stdout.flush()
    except KeyboardInterrupt:
        pass
    print("\n--- end of capture ---")

    ser.close()
    print("Done.  CPU is still running your new ROM image.")
    print("Re-attach with:  screen /dev/ttyACM0 115200")


def main() -> None:
    """CLI entry point for standalone ``upload-rom.py`` usage.

    Accepts optional ``[PORT] [BIN]`` arguments; auto-detects the port and
    defaults to ``bin/rom.bin`` when omitted.

    Side effects:
        Delegates to :func:`upload`, which opens the serial port and drives
        the Pico firmware.
    """
    args = sys.argv[1:]
    port = args[0] if len(args) >= 1 else find_pico_port()
    binp = Path(args[1]) if len(args) >= 2 else Path(__file__).parent / "bin" / "rom.bin"
    upload(port, binp)


if __name__ == "__main__":
    main()
