"""Wire-format helpers for the Pico-as-ROM hardware API (protocol v1).

This module defines the version-1 JSON envelope used to talk to the Pico
firmware: constants describing size limits, the dataclasses that model
firmware responses and events, a request builder, and parsers that validate
and unpack each kind of frame. It contains no I/O; :mod:`romulan.hardware_api`
uses these helpers to build commands and interpret replies.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

PROTO_VERSION = 1
# Must match piclone PROTO_JSON_MAX (48 KiB) — fits one full-ROM base64 chunk.
PROTO_JSON_MAX = 48 * 1024
ROM_SIZE = 0x8000
# Must match piclone UPLOAD_CHUNK_RAW_MAX — one chunk can carry the whole ROM.
CHUNK_RAW_MAX = ROM_SIZE
# Default number of capture cycles to request per read_event poll.
READ_EVENT_BATCH_SIZE = 32


class ProtocolV1Error(Exception):
    """Invalid frame or firmware error response."""

    def __init__(
        self,
        message: str,
        *,
        error: str | None = None,
        detail: str | None = None,
    ) -> None:
        """Initialize the error with an optional machine-readable code and detail.

        Args:
            message: Human-readable error message.
            error: Optional short error code reported by the firmware.
            detail: Optional longer detail string reported by the firmware.
        """
        super().__init__(message)
        self.error = error
        self.detail = detail


@dataclass
class CycleEvent:
    """A single captured 65C02 bus cycle.

    Attributes:
        seq: Monotonic sequence number assigned by the firmware.
        addr: Address on the bus, as a hex string.
        data: Data byte on the bus, as a hex string.
        rw: Read/write flag (0 = read, 1 = write).
    """

    seq: int
    addr: str
    data: str
    rw: int


@dataclass
class DoneEvent:
    """Terminating event that ends a bus-capture stream.

    Attributes:
        ok: Whether the capture completed successfully.
        reason: Why capture stopped (e.g. ``"stp"`` or ``"max_cycles"``).
        cycles: Total number of cycles the firmware reports it captured.
        addr: Address at which the capture stopped, as a hex string.
    """

    ok: bool
    reason: str
    cycles: int
    addr: str


@dataclass
class ReadResult:
    """Aggregated result of a capture: the done status plus all cycles.

    Attributes:
        ok: Whether the capture completed successfully.
        reason: Why capture stopped.
        cycles: The captured :class:`CycleEvent` items in order.
        stopped_addr: Address at which the capture stopped, as a hex string.
    """

    ok: bool
    reason: str
    cycles: list[CycleEvent] = field(default_factory=list)
    stopped_addr: str = ""


@dataclass
class PeekResult:
    """Result of a live bus/RAM peek (CPU read cycle, not ROM-image offset).

    Attributes:
        addr: CPU address that was peeked (0–0xFFFF).
        data: Data byte sampled on the matching bus cycle.
    """

    addr: int
    data: int


@dataclass
class StatusResponse:
    """Snapshot of firmware/hardware state returned by the ``status`` command.

    Attributes:
        phi2_hz: Current CPU clock (PHI2) frequency in hertz.
        rom_active: Whether the ROM emulator is driving the bus.
        reset_asserted: Whether the CPU RESET line is asserted.
        last_addr: Last address seen on the bus, as a hex string.
        last_data: Data byte from the last bus sample, as a hex string.
        last_rw: Read/write flag from the last bus sample (0 = read, 1 = write).
        read_active: Whether a bus-capture read is currently running.
        monitor_enabled: Whether the JSON monitor output is enabled.
        upload_active: Whether a ROM upload is in progress.
        resb: Raw RESB (reset) input level (0 = low/asserted, 1 = high/released).
        rwb: Raw RWB input level (0 = write, 1 = read).
        a15: Raw A15 input level (0 = RAM space, 1 = ROM space).
        phi2: Raw PHI2 clock input level (0 = low, 1 = high).
    """

    phi2_hz: float
    rom_active: bool
    reset_asserted: bool
    last_addr: str
    read_active: bool
    monitor_enabled: bool
    upload_active: bool = False
    last_data: str = "00"
    last_rw: int = 0
    resb: int = 0
    rwb: int = 0
    a15: int = 0
    phi2: int = 0


@dataclass
class PeekResponse:
    """Response from the ``peek`` command returning ROM image bytes.

    Attributes:
        offset: Byte offset within ``rom_image[]`` that was read.
        count: Number of bytes returned (may be clipped to ROM bounds).
        data: The returned bytes.
    """

    offset: int
    count: int
    data: bytes


@dataclass
class DriveResponse:
    """Response from the ``drive`` diagnostic command.

    Attributes:
        enabled: Whether the Pico is currently forcing D0-D7 as outputs.
        value: The forced byte as a 2-digit hex string, or ``"00"`` when disabled.
    """

    enabled: bool
    value: str = "00"


@dataclass
class UploadProgress:
    """Progress reported by the firmware during a ROM upload.

    Attributes:
        action: Which upload phase this reply corresponds to
            (``"begin"``, ``"chunk"``, or ``"commit"``).
        received: Total number of bytes received so far.
        expected: Total number of bytes expected (defaults to ``ROM_SIZE``).
        offset: Byte offset of the acknowledged chunk, if reported.
        reset_vector: Reset vector read back after commit, if reported.
    """

    action: str
    received: int
    expected: int = ROM_SIZE
    offset: int | None = None
    reset_vector: str | None = None


def build_request(cmd: str, *, req_id: str | None = None, **fields: Any) -> dict[str, Any]:
    """Build a v1 request envelope for a firmware command.

    Adds the protocol version and command name, and (as a convenience)
    renames the reserved ``assert_reset`` keyword to the wire field ``assert``.

    Args:
        cmd: The command name (e.g. ``"status"`` or ``"upload_rom"``).
        req_id: Optional request id echoed back by the firmware.
        **fields: Extra command-specific fields to include in the envelope.

    Returns:
        A dict ready to be serialized to JSON and sent to the Pico.
    """
    if "assert_reset" in fields:
        fields["assert"] = fields.pop("assert_reset")
    payload: dict[str, Any] = {"v": PROTO_VERSION, "cmd": cmd, **fields}
    if req_id is not None:
        payload["id"] = req_id
    return payload


def _require_version(msg: dict[str, Any]) -> None:
    """Validate that a frame's protocol version matches ``PROTO_VERSION``.

    Args:
        msg: A parsed frame. A missing ``v`` field is accepted.

    Raises:
        ProtocolV1Error: If the frame declares an unsupported version.
    """
    version = msg.get("v")
    if version is None:
        return
    if version != PROTO_VERSION:
        raise ProtocolV1Error(
            f"unsupported protocol version: {version!r}",
            error="unsupported_version",
        )


def parse_frame(raw: bytes | str) -> dict[str, Any]:
    """Parse a JSON frame and validate the v1 envelope.

    Args:
        raw: The raw frame payload as ``bytes`` (UTF-8) or ``str``.

    Returns:
        The decoded frame as a dict.

    Raises:
        ProtocolV1Error: If the payload is not valid JSON, is not a JSON
            object, or declares an unsupported protocol version.
    """
    if isinstance(raw, bytes):
        text = raw.decode("utf-8")
    else:
        text = raw
    try:
        msg = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ProtocolV1Error(f"invalid JSON: {exc}") from exc
    if not isinstance(msg, dict):
        raise ProtocolV1Error("frame must be a JSON object")
    _require_version(msg)
    return msg


def parse_command_response(msg: dict[str, Any]) -> dict[str, Any]:
    """Return a command acknowledgement, or raise on a firmware error.

    Args:
        msg: A parsed frame expected to be a command response (not an event).

    Returns:
        The same frame, unchanged, when it represents a successful ack.

    Raises:
        ProtocolV1Error: If the frame is an event, reports ``ok`` false, or
            declares an unsupported protocol version.
    """
    _require_version(msg)
    if msg.get("type") == "event":
        raise ProtocolV1Error("expected command response, got event frame")
    if msg.get("ok") is False:
        raise ProtocolV1Error(
            msg.get("detail") or msg.get("error") or "command failed",
            error=str(msg.get("error", "")),
            detail=str(msg.get("detail", "")) or None,
        )
    return msg


def parse_cycle_event(msg: dict[str, Any]) -> CycleEvent:
    """Parse a ``cycle`` event frame into a :class:`CycleEvent`.

    Args:
        msg: A parsed frame expected to be a ``cycle`` event.

    Returns:
        The captured bus cycle.

    Raises:
        ProtocolV1Error: If the frame is not a ``cycle`` event or the version
            is unsupported.
    """
    _require_version(msg)
    if msg.get("type") != "event" or msg.get("event") != "cycle":
        raise ProtocolV1Error(f"expected cycle event, got {msg!r}")
    return CycleEvent(
        seq=int(msg["seq"]),
        addr=str(msg["addr"]),
        data=str(msg["data"]),
        rw=int(msg["rw"]),
    )


def parse_done_event(msg: dict[str, Any]) -> DoneEvent:
    """Parse a ``done`` event frame into a :class:`DoneEvent`.

    Args:
        msg: A parsed frame expected to be a ``done`` event.

    Returns:
        The terminating capture event.

    Raises:
        ProtocolV1Error: If the frame is not a ``done`` event or the version
            is unsupported.
    """
    _require_version(msg)
    if msg.get("type") != "event" or msg.get("event") != "done":
        raise ProtocolV1Error(f"expected done event, got {msg!r}")
    return DoneEvent(
        ok=bool(msg.get("ok")),
        reason=str(msg.get("reason", "")),
        cycles=int(msg.get("cycles", 0)),
        addr=str(msg.get("addr", "")),
    )


def parse_cycles_event(msg: dict[str, Any]) -> list[CycleEvent]:
    """Parse a batched ``cycles`` event frame into a list of :class:`CycleEvent`.

    Args:
        msg: A parsed frame expected to be a ``cycles`` event.

    Returns:
        The captured bus cycles in order.

    Raises:
        ProtocolV1Error: If the frame is not a ``cycles`` event, the version
            is unsupported, or a cycle entry is malformed.
    """
    _require_version(msg)
    if msg.get("type") != "event" or msg.get("event") != "cycles":
        raise ProtocolV1Error(f"expected cycles event, got {msg!r}")
    raw_cycles = msg.get("cycles")
    if not isinstance(raw_cycles, list):
        raise ProtocolV1Error("cycles event missing 'cycles' array")

    events: list[CycleEvent] = []
    for item in raw_cycles:
        if not isinstance(item, dict):
            raise ProtocolV1Error(f"cycle entry must be an object, got {item!r}")
        events.append(
            CycleEvent(
                seq=int(item["seq"]),
                addr=str(item["addr"]),
                data=str(item["data"]),
                rw=int(item["rw"]),
            )
        )
    return events


def parse_status(msg: dict[str, Any]) -> StatusResponse:
    """Parse a ``status`` command response into a :class:`StatusResponse`.

    Args:
        msg: A parsed frame expected to be a successful ``status`` response.

    Returns:
        The decoded status snapshot.

    Raises:
        ProtocolV1Error: If the frame reports an error or the version is
            unsupported.
    """
    parse_command_response(msg)
    return StatusResponse(
        phi2_hz=float(msg.get("phi2_hz", 0)),
        rom_active=bool(msg.get("rom_active")),
        reset_asserted=bool(msg.get("reset_asserted")),
        last_addr=str(msg.get("last_addr", "0000")),
        last_data=str(msg.get("last_data", "00")),
        last_rw=int(msg.get("last_rw", 0)),
        read_active=bool(msg.get("read_active")),
        monitor_enabled=bool(msg.get("monitor_enabled")),
        upload_active=bool(msg.get("upload_active")),
        resb=int(msg.get("resb", 0)),
        rwb=int(msg.get("rwb", 0)),
        a15=int(msg.get("a15", 0)),
        phi2=int(msg.get("phi2", 0)),
    )


def parse_peek_response(msg: dict[str, Any]) -> PeekResponse:
    """Parse a ``peek`` command response into a :class:`PeekResponse`.

    Args:
        msg: A parsed frame expected to be a successful ``peek`` response.

    Returns:
        The decoded offset, count, and bytes.

    Raises:
        ProtocolV1Error: If the frame reports an error, the version is
            unsupported, or the hex data is malformed.
    """
    parse_command_response(msg)
    offset = int(msg.get("offset", 0))
    count = int(msg.get("count", 0))
    hex_data = str(msg.get("data", ""))
    try:
        data = bytes.fromhex(hex_data)
    except ValueError as exc:
        raise ProtocolV1Error(f"peek returned invalid hex data: {exc}") from exc
    return PeekResponse(offset=offset, count=count, data=data)


def parse_drive_response(msg: dict[str, Any]) -> DriveResponse:
    """Parse a ``drive`` command response into a :class:`DriveResponse`.

    Args:
        msg: A parsed frame expected to be a successful ``drive`` response.

    Returns:
        The decoded drive force state.

    Raises:
        ProtocolV1Error: If the frame reports an error or the version is
            unsupported.
    """
    parse_command_response(msg)
    return DriveResponse(
        enabled=bool(msg.get("enabled")),
        value=str(msg.get("value", "00")),
    )


def _parse_hex_field(value: Any, *, name: str, max_value: int) -> int:
    """Parse an int or hex string field into an integer in ``0..max_value``."""
    if value is None:
        raise ProtocolV1Error(f"missing {name}")
    if isinstance(value, int):
        parsed = value
    else:
        text = str(value).strip()
        try:
            parsed = int(text, 16)
        except ValueError as exc:
            raise ProtocolV1Error(f"invalid {name}: {value!r}") from exc
    if not 0 <= parsed <= max_value:
        raise ProtocolV1Error(f"{name} out of range: {parsed}")
    return parsed


def parse_live_peek_response(msg: dict[str, Any]) -> PeekResult:
    """Parse a live ``peek`` command response into a :class:`PeekResult`.

    Distinct from any ROM-image offset peek: fields are CPU ``addr`` and
    sampled bus ``data`` (single byte).

    Args:
        msg: A parsed frame expected to be a successful ``peek`` response.

    Returns:
        The decoded address and data byte.

    Raises:
        ProtocolV1Error: If the frame reports an error, the version is
            unsupported, or required fields are missing/invalid.
    """
    parse_command_response(msg)
    return PeekResult(
        addr=_parse_hex_field(msg.get("addr"), name="addr", max_value=0xFFFF),
        data=_parse_hex_field(msg.get("data"), name="data", max_value=0xFF),
    )


def parse_upload_response(msg: dict[str, Any]) -> UploadProgress:
    """Parse an ``upload_rom`` command response into an :class:`UploadProgress`.

    Args:
        msg: A parsed frame expected to be a successful ``upload_rom`` reply
            (begin, chunk, or commit).

    Returns:
        The decoded upload progress.

    Raises:
        ProtocolV1Error: If the frame reports an error or the version is
            unsupported.
    """
    parse_command_response(msg)
    return UploadProgress(
        action=str(msg.get("action", "")),
        received=int(msg.get("received", msg.get("bytes", 0))),
        expected=int(msg.get("expected", ROM_SIZE)),
        offset=int(msg["offset"]) if "offset" in msg else None,
        reset_vector=str(msg["reset_vector"]) if "reset_vector" in msg else None,
    )
