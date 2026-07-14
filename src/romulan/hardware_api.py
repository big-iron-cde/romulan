"""High-level client for the Pico-as-ROM 65C02 hardware over the v1 serial protocol.

This module provides :class:`HardwareAPI`, a client-side wrapper around the
framed JSON protocol implemented by the Pico firmware. It handles the
low-level ENQ/STX/ACK/EOT framing, encodes commands built by
:mod:`romulan.protocol_v1`, and exposes friendly methods for the common
operations: querying the current CPU address, asserting/releasing reset,
toggling the ASCII monitor, reading status, uploading a ROM image, and
capturing bus cycles.

Opening a :class:`HardwareAPI` immediately opens the underlying serial port.
The class supports the context-manager protocol so the port is always closed::

    with HardwareAPI("/dev/ttyACM0") as api:
        api.upload_rom(rom_bytes)
"""

from __future__ import annotations

import base64
import json
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Callable

import serial

from .protocol_v1 import (
    CHUNK_RAW_MAX,
    READ_EVENT_BATCH_SIZE,
    ROM_SIZE,
    CycleEvent,
    PeekResponse,
    ProtocolV1Error,
    ReadResult,
    StatusResponse,
    build_request,
    parse_cycles_event,
    parse_done_event,
    parse_frame,
    parse_peek_response,
    parse_status,
    parse_upload_response,
)
from .upload_rom import find_pico_port

ENQ = 0x05
STX = 0x02
ACK = 0x06
EOT = 0x04
NACK = 0x15


class HardwareAPIError(Exception):
    """Raised when the Pico responds with NACK or a frame error occurs."""


@dataclass
class CaptureResult:
    """Result of a bus capture (read until STP).

    Attributes:
        reason: Why the capture stopped (e.g. ``"stp"`` or ``"max_cycles"``).
        cycles: One dict per captured bus cycle, each with ``seq``, ``addr``,
            ``data``, and ``rw`` keys.
    """

    reason: str
    cycles: list[dict[str, Any]] = field(default_factory=list)

    def __repr__(self) -> str:
        """Return a concise debug representation.

        Returns:
            A string showing the stop reason and cycle count.
        """
        return f"CaptureResult(reason={self.reason!r}, cycles={len(self.cycles)})"

    @classmethod
    def from_read_result(cls, result: ReadResult) -> CaptureResult:
        """Build a :class:`CaptureResult` from a protocol :class:`~romulan.protocol_v1.ReadResult`.

        Args:
            result: The parsed read result returned by the capture loop.

        Returns:
            A :class:`CaptureResult` with each cycle flattened into a plain dict.
        """
        return cls(
            reason=result.reason,
            cycles=[
                {
                    "seq": c.seq,
                    "addr": c.addr,
                    "data": c.data,
                    "rw": c.rw,
                }
                for c in result.cycles
            ],
        )


class HardwareAPI:
    """Context-manager compatible hardware API for Pico-as-ROM firmware v1.

    Each instance owns a single serial connection to the Pico. The connection
    is opened as soon as the object is constructed, and closed by
    :meth:`close` or on exit from a ``with`` block.

    Attributes:
        port: The serial device path the client is connected to.
        baudrate: The serial baud rate in use.
        timeout: Default per-read timeout in seconds.
        verbose: When ``True``, protocol traffic is logged to stderr.
    """

    def __init__(self, port: str, baudrate: int = 115200, timeout: float = 30.0, verbose: bool = False):
        """Open a serial connection to the Pico and prepare it for commands.

        Side effects:
            Opens the serial port immediately and flushes any pending input.

        Args:
            port: Serial device path (e.g. ``/dev/ttyACM0``).
            baudrate: Serial baud rate. Defaults to ``115200``.
            timeout: Default read timeout in seconds. Defaults to ``30.0``.
            verbose: If ``True``, log SEND/RECV protocol traffic to stderr.
        """
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.verbose = verbose
        self._ser: serial.Serial | None = None
        self._open()

    def _open(self) -> None:
        self._emit({"type": "open", "port": self.port, "baudrate": self.baudrate})
        self._ser = serial.Serial(
            self.port,
            self.baudrate,
            timeout=self.timeout,
        )
        time.sleep(0.3)
        if self._ser:
            self._ser.reset_input_buffer()

    def close(self) -> None:
        """Close the serial port if it is open.

        Safe to call multiple times; subsequent calls are no-ops.
        """
        if self._ser is not None:
            self._emit({"type": "close"})
            self._ser.close()
            self._ser = None

    def __enter__(self) -> HardwareAPI:
        """Enter a ``with`` block and return this client."""
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Exit a ``with`` block, closing the serial port."""
        self.close()

    @property
    def ser(self) -> serial.Serial:
        """The live :class:`serial.Serial` connection.

        Returns:
            The open serial connection.

        Raises:
            HardwareAPIError: If the port has already been closed.
        """
        if self._ser is None:
            raise HardwareAPIError("Serial port is closed")
        return self._ser

    def _emit(self, event: dict[str, Any]) -> None:
        """Write a trace message to stderr when ``verbose`` is enabled."""
        if self.verbose:
            print(json.dumps(event, separators=(",", ":"), ensure_ascii=False), file=sys.stderr, flush=True)

    def _payload_event(self, direction: str, payload: bytes) -> dict[str, Any]:
        event: dict[str, Any] = {"type": direction}
        try:
            text = payload.decode("utf-8")
            event["payload"] = json.loads(text)
        except (UnicodeDecodeError, json.JSONDecodeError):
            event["payload"] = {"binary": True, "bytes": len(payload)}
        return event

    def _sync_to_byte(self, acceptable: set[int], timeout: float | None = None) -> int:
        wait = self.timeout if timeout is None else timeout
        deadline = time.time() + wait
        while time.time() < deadline:
            b = self.ser.read(1)
            if b and b[0] in acceptable:
                if b[0] == ACK:
                    self._emit({"type": "ack"})
                elif b[0] == NACK:
                    self._emit({"type": "nack"})
                return b[0]
        labels = ", ".join(f"0x{v:02X}" for v in sorted(acceptable))
        self._emit({"type": "error", "error": "timeout", "detail": f"timed out waiting for {labels}"})
        raise TimeoutError(f"timed out waiting for {labels}")

    def _write_byte(self, value: int) -> None:
        self.ser.write(bytes([value]))

    def _read_frame_payload(self, timeout: float | None = None) -> bytes:
        wait = self.timeout if timeout is None else timeout
        if self._sync_to_byte({STX}, timeout=wait) != STX:
            raise HardwareAPIError("expected STX after ENQ")

        self._write_byte(ACK)

        buf = bytearray()
        deadline = time.time() + wait
        while time.time() < deadline:
            chunk = self.ser.read(256)
            if not chunk:
                continue
            for byte in chunk:
                if byte == EOT:
                    self._write_byte(ACK)
                    return bytes(buf)
                buf.append(byte)
        self._emit({"type": "error", "error": "timeout", "detail": "timed out waiting for EOT in response payload"})
        raise TimeoutError("timed out waiting for EOT in response payload")

    def _send_frame_host(self, payload: bytes) -> None:
        self._emit(self._payload_event("send", payload))
        self._write_byte(ENQ)
        self._write_byte(STX)
        if self._sync_to_byte({ACK, NACK}) != ACK:
            self._emit({"type": "error", "error": "nack", "detail": "Pico responded with NACK"})
            raise HardwareAPIError("Pico responded with NACK")
        self.ser.write(payload)
        self._write_byte(EOT)
        if self._sync_to_byte({ACK, NACK}) != ACK:
            self._emit({"type": "error", "error": "nack", "detail": "Pico responded with NACK"})
            raise HardwareAPIError("Pico responded with NACK")

    def _send_frame(self, payload: bytes) -> bytes:
        self._send_frame_host(payload)
        self._sync_to_byte({ENQ})
        raw = self._read_frame_payload()
        self._emit(self._payload_event("recv", raw))
        return raw

    def _recv_json_frame(self, timeout: float | None = None) -> dict[str, Any]:
        wait = self.timeout if timeout is None else timeout
        self._sync_to_byte({ENQ}, timeout=wait)
        raw = self._read_frame_payload(timeout=wait)
        self._emit(self._payload_event("recv", raw))
        return parse_frame(raw)

    def _parse_response(self, raw: bytes) -> dict[str, Any]:
        try:
            msg = parse_frame(raw)
        except ProtocolV1Error as exc:
            raise HardwareAPIError(str(exc)) from exc
        if msg.get("ok") is False:
            raise HardwareAPIError(
                msg.get("detail") or msg.get("error") or "command failed"
            )
        return msg

    def _exchange_json(self, command: dict[str, Any]) -> dict[str, Any]:
        payload = json.dumps(command, separators=(",", ":")).encode("utf-8")
        raw = self._send_frame(payload)
        return self._parse_response(raw)

    @staticmethod
    def _parse_addr(addr: Any) -> int:
        if isinstance(addr, int):
            return addr
        text = str(addr)
        try:
            return int(text, 16)
        except ValueError:
            return int(text)

    def _next_id(self) -> str:
        return uuid.uuid4().hex[:12]

    def _drain_input(self, settle_s: float = 0.3) -> None:
        time.sleep(settle_s)
        self.ser.reset_input_buffer()

    def request_addr(self) -> int:
        """Ask the firmware for the address currently on the CPU bus.

        Returns:
            The current CPU address as an integer.

        Raises:
            HardwareAPIError: If the response is missing the ``addr`` field
                or the firmware reports an error.
            TimeoutError: If the Pico does not respond in time.
        """
        self._emit({"type": "call", "method": "request_addr"})
        resp = self._exchange_json(build_request("request_addr", req_id=self._next_id()))
        addr = resp.get("addr")
        if addr is None:
            raise HardwareAPIError(f"Missing 'addr' in response: {resp!r}")
        addr_int = self._parse_addr(addr)
        self._emit({"type": "ret", "method": "request_addr", "result": addr_int})
        return addr_int

    def reset(self, assert_reset: bool) -> None:
        """Assert or release the 65C02 RESET line.

        Side effects:
            Changes the CPU run state: asserting reset halts the CPU, while
            releasing it lets the CPU start executing from its reset vector.

        Args:
            assert_reset: ``True`` to hold the CPU in reset, ``False`` to
                release it.

        Raises:
            HardwareAPIError: If the firmware reports an error.
            TimeoutError: If the Pico does not respond in time.
        """
        self._emit({"type": "call", "method": "reset", "assert_reset": assert_reset})
        self._exchange_json(
            build_request("reset", req_id=self._next_id(), assert_reset=assert_reset)
        )
        self._emit({"type": "ret", "method": "reset"})

    def monitor(self, enable: bool) -> None:
        """Enable or disable the firmware's unstructured ASCII monitor output.

        The ASCII monitor must be disabled before framed operations such as
        :meth:`upload_rom` and :meth:`read_until_stp`, otherwise its free-form
        text would corrupt the framed protocol stream.

        Args:
            enable: ``True`` to turn the monitor on, ``False`` to turn it off.

        Raises:
            HardwareAPIError: If the firmware reports an error.
            TimeoutError: If the Pico does not respond in time.
        """
        self._emit({"type": "call", "method": "monitor", "enable": enable})
        self._exchange_json(
            build_request("monitor", req_id=self._next_id(), enable=enable)
        )
        self._emit({"type": "ret", "method": "monitor"})

    def status(self) -> StatusResponse:
        """Query the firmware for its current status.

        Returns:
            A :class:`~romulan.protocol_v1.StatusResponse` describing the
            clock frequency, ROM/reset/monitor state, and last bus sample.

        Raises:
            HardwareAPIError: If the firmware reports an error.
            TimeoutError: If the Pico does not respond in time.
        """
        resp = self._exchange_json(build_request("status", req_id=self._next_id()))
        return parse_status(resp)

    def peek(self, offset: int, count: int = 16) -> PeekResponse:
        """Read back bytes from the loaded ``rom_image[]``.

        This is useful for verifying that an upload landed at the expected
        offsets before releasing RESET.

        Args:
            offset: Byte offset within the 32 KB ROM image.
            count: Number of bytes to read (1-64; firmware caps at 64).

        Returns:
            A :class:`~romulan.protocol_v1.PeekResponse` with the offset,
            count, and returned bytes.

        Raises:
            ValueError: If ``offset`` or ``count`` is out of range.
            HardwareAPIError: If the firmware reports an error or returns
                malformed data.
            TimeoutError: If the Pico does not respond in time.
        """
        self._emit({"type": "call", "method": "peek", "offset": offset, "count": count})
        if not 0 <= offset < ROM_SIZE:
            raise ValueError(f"offset must be within [0, {ROM_SIZE})")
        if count <= 0 or count > 64:
            raise ValueError("count must be 1..64")

        resp = self._exchange_json(
            build_request(
                "peek",
                req_id=self._next_id(),
                offset=offset,
                count=count,
            )
        )
        result = parse_peek_response(resp)
        self._emit({"type": "return", "method": "peek", "data": result.data.hex()})
        return result

    def set_clock(self, hz: float) -> None:
        """Set the 65C02 PHI2 clock frequency.

        Args:
            hz: Target frequency in hertz. The firmware accepts 0.1..1000 Hz.

        Raises:
            ValueError: If ``hz`` is outside the supported range.
            HardwareAPIError: If the firmware reports an error.
            TimeoutError: If the Pico does not respond in time.
        """
        self._emit({"type": "call", "method": "set_clock", "hz": hz})
        if not 0.1 <= hz <= 1000.0:
            raise ValueError("hz must be between 0.1 and 1000.0")

        self._exchange_json(
            build_request(
                "clock",
                req_id=self._next_id(),
                phi2_hz=hz,
            )
        )
        self._emit({"type": "return", "method": "set_clock"})

    def upload_rom(self, data: bytes) -> dict[str, Any]:
        """Upload a full 32 KB ROM image to the Pico in a begin/chunk/commit sequence.

        The image is sent as base64-encoded chunks and committed at the end.
        The reset vector is reported back by the firmware after commit.

        Side effects:
            Disables the ASCII monitor and flushes serial input before
            transferring, so the framed protocol is not corrupted.

        Args:
            data: The ROM image; must be exactly ``ROM_SIZE`` (32 KB) bytes.

        Returns:
            A dict with keys ``ok``, ``bytes`` (bytes committed),
            ``reset_vector``, and ``expected`` (expected total size).

        Raises:
            ValueError: If ``data`` is not exactly ``ROM_SIZE`` bytes.
            HardwareAPIError: If a chunk stalls or the firmware reports an error.
            TimeoutError: If the Pico does not respond in time.
        """
        self._emit({"type": "call", "method": "upload_rom", "size": len(data)})
        if len(data) != ROM_SIZE:
            raise ValueError(f"ROM must be exactly {ROM_SIZE} bytes, got {len(data)}")

        self.monitor(enable=False)
        self._drain_input()

        begin = self._exchange_json(
            build_request(
                "upload_rom",
                req_id=self._next_id(),
                action="begin",
                size=ROM_SIZE,
            )
        )
        progress = parse_upload_response(begin)

        offset = 0
        while offset < ROM_SIZE:
            chunk = data[offset : offset + CHUNK_RAW_MAX]
            b64 = base64.b64encode(chunk).decode("ascii")
            chunk_resp = self._exchange_json(
                build_request(
                    "upload_rom",
                    action="chunk",
                    offset=offset,
                    data=b64,
                )
            )
            parsed = parse_upload_response(chunk_resp)
            if parsed.received <= offset:
                raise HardwareAPIError(f"upload_rom chunk stalled at offset {offset}")
            offset = parsed.received

        commit = self._exchange_json(
            build_request("upload_rom", req_id=self._next_id(), action="commit")
        )
        final = parse_upload_response(commit)
        result = {
            "ok": True,
            "bytes": final.received,
            "reset_vector": final.reset_vector,
            "expected": progress.expected,
        }
        self._emit({"type": "ret", "method": "upload_rom", "result": result})
        return result

    def read_until_stp(
        self,
        max_cycles: int = 10000,
        frame_timeout: float | None = None,
        on_cycle: Callable[[CycleEvent], None] | None = None,
        batch_size: int = READ_EVENT_BATCH_SIZE,
        phi2_hz: float | None = None,
    ) -> CaptureResult:
        """Capture CPU bus cycles until the CPU executes STP or a limit is hit.

        Holds reset, arms ``read``, then releases reset so capture starts from
        the reset vector (typically ``$8000``). Polls ``read_event`` for batched
        cycle and done frames.

        Side effects:
            Disables the ASCII monitor, asserts then releases CPU reset. The
            current PHI2 clock is preserved unless ``phi2_hz`` is provided.

        Args:
            max_cycles: Maximum number of bus cycles to capture before the
                firmware stops. Defaults to ``10000``.
            frame_timeout: Overall capture timeout in seconds while polling
                for cycle/done events. Defaults to :attr:`timeout`.
            on_cycle: Optional callback invoked for each captured cycle (e.g.
                for live CLI output).
            batch_size: Number of cycles to request per ``read_event`` poll.
                Defaults to :data:`READ_EVENT_BATCH_SIZE`.
            phi2_hz: Optional clock frequency to set when arming capture.
                If omitted, the current clock speed is preserved.

        Returns:
            A :class:`CaptureResult` with the stop reason and captured cycles.

        Raises:
            HardwareAPIError: If the read is rejected or an unexpected frame
                arrives.
            TimeoutError: If the capture does not complete in time.
        """
        resolved_timeout = self.timeout if frame_timeout is None else frame_timeout
        emit_payload: dict[str, Any] = {"max_cycles": max_cycles, "batch_size": batch_size}
        if phi2_hz is not None:
            emit_payload["phi2_hz"] = phi2_hz
        self._emit({"type": "call", "method": "read_until_stp", **emit_payload})
        self.monitor(enable=False)
        self._drain_input()

        # Restart CPU from reset vector once capture is armed.
        self.reset(assert_reset=True)

        read_fields: dict[str, Any] = {
            "until": "stp",
            "max_cycles": max_cycles,
            "batch_size": batch_size,
        }
        if phi2_hz is not None:
            read_fields["phi2_hz"] = phi2_hz

        ack = self._exchange_json(
            build_request("read", req_id=self._next_id(), **read_fields)
        )
        if not ack.get("ok"):
            raise HardwareAPIError(f"read rejected: {ack}")

        time.sleep(0.03)
        self.reset(assert_reset=False)

        self._emit({"type": "waiting", "for": "read_event", "timeout_s": resolved_timeout})

        cycles: list[CycleEvent] = []
        result = ReadResult(ok=False, reason="unknown")
        deadline = time.time() + resolved_timeout
        poll_idle_s = 0.005

        try:
            while time.time() < deadline:
                msg = self._exchange_json(
                    build_request(
                        "read_event",
                        req_id=self._next_id(),
                        batch_size=batch_size,
                    )
                )
                event = msg.get("event")
                if msg.get("type") == "event" and event == "cycles":
                    batch = parse_cycles_event(msg)
                    for cycle in batch:
                        if on_cycle is not None:
                            on_cycle(cycle)
                    cycles.extend(batch)
                    deadline = time.time() + resolved_timeout
                elif msg.get("type") == "event" and event == "done":
                    done = parse_done_event(msg)
                    result = ReadResult(
                        ok=done.ok,
                        reason=done.reason,
                        cycles=cycles,
                        stopped_addr=done.addr,
                    )
                    break
                elif event == "none":
                    time.sleep(poll_idle_s)
                else:
                    raise HardwareAPIError(f"unexpected frame during read: {msg}")
            else:
                raise TimeoutError(
                    f"timed out after read ack (got {len(cycles)} cycles). "
                    "No STP/done yet — is PHI2 running? Try --timeout 60."
                )
        except TimeoutError:
            raise

        capture = CaptureResult.from_read_result(result)
        self._emit({"type": "ret", "method": "read_until_stp", "result": asdict(capture)})
        return capture


def open_hardware_api(port: str | None = None) -> HardwareAPI:
    """Open a :class:`HardwareAPI`, auto-detecting the Pico port if needed.

    Side effects:
        Opens the serial port via :class:`HardwareAPI`.

    Args:
        port: Explicit serial device path. If ``None``, the port is
            auto-detected with :func:`~romulan.upload_rom.find_pico_port`.

    Returns:
        A connected :class:`HardwareAPI` instance.

    Raises:
        HardwareAPIError: If no Pico serial port can be found.
    """
    resolved = port or find_pico_port()
    if not resolved:
        raise HardwareAPIError("No Pico serial port found")
    return HardwareAPI(resolved)
