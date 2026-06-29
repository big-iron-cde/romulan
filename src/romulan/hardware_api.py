"""Hardware API — framed serial protocol for the Pico-as-ROM 65C02 system.

This module provides a structured, JSON-based interface to the Pico firmware
over USB-CDC.  All transactions use a fixed byte-level frame:

    ENQ (0x05)  → start of frame
    STX (0x02)  → start of payload
    ACK (0x06)  ← receiver ready for payload
    <payload>   → JSON (except ROM binary upload)
    EOT (0x04)  → end of payload
    ACK (0x06)  ← accepted  /  NACK (0x15) ← rejected

The API silently discards stray bytes (monitor lines, echoed ACKs) while
resyncing to frame boundaries.
"""

from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict

import serial

from .upload_rom import find_pico_port

# --- Frame constants ---
ENQ = b"\x05"
STX = b"\x02"
ACK = b"\x06"
EOT = b"\x04"
NACK = b"\x15"

# --- Exceptions ---


class HardwareAPIError(Exception):
    """Raised when the Pico responds with NACK or a frame error occurs."""

    pass


class HardwareAPI:
    """Context-manager compatible hardware API for the Pico-as-ROM firmware.

    Usage::

        with HardwareAPI("/dev/ttyACM0") as api:
            print(api.request_addr())
            api.reset(assert_reset=True)
            api.reset(assert_reset=False)
            result = api.upload_rom(open("bin/rom.bin", "rb").read())
            api.monitor(enable=False)
            api.reset(assert_reset=True)
            api.reset(assert_reset=False)
            capture = api.read_until_stp(max_cycles=500)
            print(capture.reason, len(capture.cycles))
    """

    def __init__(self, port: str, baudrate: int = 115200, timeout: float = 3.0, verbose: bool = False):
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.verbose = verbose
        self._ser: serial.Serial | None = None
        self._open()

    def _open(self) -> None:
        self._log(f"Opened {self.port} @ {self.baudrate}")
        self._ser = serial.Serial(
            self.port,
            self.baudrate,
            timeout=self.timeout,
        )
        time.sleep(0.3)
        if self._ser:
            self._ser.reset_input_buffer()

    def close(self) -> None:
        if self._ser is not None:
            self._log("Closed")
            self._ser.close()
            self._ser = None

    def __enter__(self) -> HardwareAPI:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Verbose helpers
    # ------------------------------------------------------------------

    def _log(self, msg: str) -> None:
        if self.verbose:
            print(f"[HW] {msg}", file=sys.stderr, flush=True)

    @staticmethod
    def _payload_preview(payload: bytes) -> str:
        try:
            return payload.decode("utf-8")
        except UnicodeDecodeError:
            return f"<binary, {len(payload)} bytes>"

    # ------------------------------------------------------------------
    # Low-level framing
    # ------------------------------------------------------------------

    def _read_until_byte(self, expected: bytes, deadline: float) -> None:
        """Read one byte at a time until `expected` is seen, or deadline passes."""
        if self._ser is None:
            raise HardwareAPIError("Serial port is closed")
        while time.time() < deadline:
            byte = self._ser.read(1)
            if byte == expected:
                return
            if byte == b"":
                continue
        self._log(f"ERROR: timed out waiting for {expected!r}")
        raise TimeoutError(f"timed out waiting for {expected!r}")

    def _resync(self) -> None:
        """Discard stray bytes until an ENQ byte appears.

        This handles monitor ASCII output, echoed ACKs, and any other
        unstructured data that may have been on the wire.
        """
        if self._ser is None:
            raise HardwareAPIError("Serial port is closed")
        deadline = time.time() + self.timeout
        while time.time() < deadline:
            byte = self._ser.read(1)
            if byte == ENQ:
                return
            if byte == b"":
                continue
        self._log("ERROR: timed out while resyncing to frame boundary")
        raise TimeoutError("timed out while resyncing to frame boundary")

    def _send_frame(self, payload: bytes) -> bytes:
        """Send a complete framed transaction and return the response payload.

        Host → Pico frame:
            ENQ → STX → (wait ACK) → payload → EOT → (wait ACK or NACK)

        Pico → Host response frame:
            ENQ → STX → (host ACK) → payload → EOT → (host ACK)

        Returns the payload bytes that the receiver sent back (if any).
        """
        if self._ser is None:
            raise HardwareAPIError("Serial port is closed")

        self._log(f"SEND: {self._payload_preview(payload)}")

        # Send our frame
        self._ser.write(ENQ)
        self._ser.write(STX)
        self._ser.flush()

        # Wait for ACK (receiver ready)
        self._read_until_byte(ACK, time.time() + self.timeout)

        # Send payload + EOT
        self._ser.write(payload)
        self._ser.write(EOT)
        self._ser.flush()

        # Wait for ACK or NACK (transaction accepted/rejected)
        resp_deadline = time.time() + self.timeout
        while time.time() < resp_deadline:
            byte = self._ser.read(1)
            if byte == ACK:
                break
            if byte == NACK:
                self._log("ERROR: Pico responded with NACK")
                raise HardwareAPIError("Pico responded with NACK")
            if byte == b"":
                continue
        else:
            self._log("ERROR: timed out waiting for ACK/NACK after EOT")
            raise TimeoutError("timed out waiting for ACK/NACK after EOT")

        # Receive the response frame from Pico.
        # Discard any stray bytes until the ENQ boundary.
        self._resync()

        # Read and discard STX (start of payload)
        self._read_until_byte(STX, time.time() + self.timeout)

        # Send ACK (receiver ready for response payload)
        self._ser.write(ACK)
        self._ser.flush()

        # Read response payload bytes until EOT
        resp_buf = bytearray()
        eot_deadline = time.time() + self.timeout
        while time.time() < eot_deadline:
            chunk = self._ser.read(256)
            if not chunk:
                continue
            idx = chunk.find(EOT)
            if idx >= 0:
                resp_buf.extend(chunk[:idx])
                break
            resp_buf.extend(chunk)
        else:
            self._log("ERROR: timed out waiting for EOT in response payload")
            raise TimeoutError("timed out waiting for EOT in response payload")

        # Send ACK (response accepted)
        self._ser.write(ACK)
        self._ser.flush()

        resp_bytes = bytes(resp_buf)
        self._log(f"RECV: {self._payload_preview(resp_bytes)}")
        return resp_bytes

    def _send_json(self, cmd: str, **kwargs: Any) -> Dict[str, Any]:
        """Send a JSON command and return the parsed JSON response."""
        payload = json.dumps({"cmd": cmd, **kwargs}).encode("utf-8")
        resp_bytes = self._send_frame(payload)
        try:
            return json.loads(resp_bytes.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise HardwareAPIError(
                f"Invalid JSON response from Pico: {resp_bytes!r}"
            ) from exc

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def request_addr(self) -> int:
        """Request the current CPU address from the Pico.

        Returns the address as an integer.
        """
        self._log("CALL request_addr()")
        resp = self._send_json("request_addr")
        addr = resp.get("addr")
        if addr is None:
            raise HardwareAPIError(
                f"Missing 'addr' in response: {resp!r}"
            )
        addr = int(addr)
        self._log(f"RET request_addr -> {addr}")
        return addr

    def reset(self, assert_reset: bool) -> None:
        """Hold or release the CPU reset line.

        :param assert_reset: ``True`` to assert (hold) reset, ``False`` to release.
        """
        self._log(f"CALL reset(assert_reset={assert_reset})")
        value = 0 if assert_reset else 1
        self._send_json("reset", value=value)
        self._log("RET reset")

    def monitor(self, enable: bool) -> None:
        """Enable or disable the unstructured monitor output.

        .. important::
            Monitor output must be disabled before any framed transaction
            (``upload_rom``, ``read_until_stp``, etc.) or the ASCII lines will
            corrupt the frame stream.  ``upload_rom`` and ``read_until_stp``
            disable it automatically.
        """
        self._log(f"CALL monitor(enable={enable})")
        self._send_json("monitor", enable=enable)
        self._log("RET monitor")

    def upload_rom(self, data: bytes) -> Dict[str, Any]:
        """Upload a 32 KB ROM image to the Pico.

        The upload uses two frames:

        1. JSON command frame: ``{"cmd": "loadbin", "size": 32768}``
        2. Raw binary frame: the 32,768 bytes

        :param data: The raw ROM binary (must be exactly 32,768 bytes).
        :returns: The Pico response dict (e.g. ``{"loaded": 32768}``).
        """
        self._log(f"CALL upload_rom(size={len(data)})")
        if len(data) != 0x8000:
            raise ValueError(
                f"ROM must be exactly {0x8000} bytes, got {len(data)}"
            )

        # Ensure monitor is disabled so ASCII does not corrupt framing
        self.monitor(enable=False)

        # Step 1 — command frame
        self._send_json("loadbin", size=len(data))

        # Step 2 — raw binary frame
        resp_bytes = self._send_frame(data)

        try:
            result = json.loads(resp_bytes.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise HardwareAPIError(
                f"Invalid JSON response after binary upload: {resp_bytes!r}"
            ) from exc
        self._log(f"RET upload_rom -> {result}")
        return result

    def read_until_stp(self, max_cycles: int) -> "CaptureResult":
        """Capture CPU bus cycles until the STP instruction is hit.

        This automatically disables the monitor before starting the capture.

        :param max_cycles: Maximum number of cycles to capture.
        :returns: A ``CaptureResult`` with ``reason`` and ``cycles``.
        """
        self._log(f"CALL read_until_stp(max_cycles={max_cycles})")
        # Ensure monitor is disabled
        self.monitor(enable=False)

        resp = self._send_json("read_until_stp", max_cycles=max_cycles)

        reason = resp.get("reason", "unknown")
        cycles = resp.get("cycles", [])
        if not isinstance(cycles, list):
            raise HardwareAPIError(
                f"Expected 'cycles' list in response, got {type(cycles).__name__}"
            )

        result = CaptureResult(reason=reason, cycles=cycles)
        self._log(f"RET read_until_stp -> {result}")
        return result


@dataclass
class CaptureResult:
    """Result of a ``read_until_stp`` capture."""

    reason: str
    cycles: list[dict[str, Any]]

    def __repr__(self) -> str:
        return f"CaptureResult(reason={self.reason!r}, cycles={len(self.cycles)})"
