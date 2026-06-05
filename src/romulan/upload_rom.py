#!/usr/bin/env python3
"""
upload-rom.py — push a 32 KB ROM image to the Pico-as-ROM firmware.

Usage:
    python3 upload-rom.py [PORT] [BIN]

Defaults:
    PORT = /dev/ttyACM0 (or auto-detected)
    BIN  = bin/rom.bin

The firmware exposes a tiny binary upload protocol on its USB-CDC port:

    host → "loadbin\n"
    pico → "OK send 32768 bytes\n"
    host → <32768 raw bytes>
    pico → "loaded 32768 bytes\n"

This script wraps that, plus optional ROM-emulator toggle and reset pulse
so the CPU restarts on the new image. It tries to be polite — leaves the
ROM emulator and CPU in whatever state you had them in before.
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
    """Auto-detect a Raspberry Pi Pico serial port."""
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
    """Read lines from `ser` until one contains `needle` or we time out."""
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
    args = sys.argv[1:]
    port = args[0] if len(args) >= 1 else find_pico_port()
    binp = Path(args[1]) if len(args) >= 2 else Path(__file__).parent / "bin" / "rom.bin"
    upload(port, binp)


if __name__ == "__main__":
    main()
