#!/usr/bin/env python3
"""Serial port discovery for the Pico-as-ROM 65C02 firmware.

ROM upload uses the framed v1 Hardware API
(:meth:`romulan.hardware_api.HardwareAPI.upload_rom`). This module only
auto-detects the Pico USB-CDC port for the CLI and client helpers.
"""

from __future__ import annotations

import glob
import sys

try:
    import serial
    import serial.tools.list_ports
except ImportError:
    sys.stderr.write(
        "ERROR: pyserial is required.  Install with:\n"
        "    pip install --user pyserial\n"
    )
    sys.exit(1)


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
