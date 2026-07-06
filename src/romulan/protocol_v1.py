"""Hardware API v1 — JSON envelope parser and request builder."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

PROTO_VERSION = 1
PROTO_JSON_MAX = 2048
ROM_SIZE = 0x8000
CHUNK_RAW_MAX = 1476


class ProtocolV1Error(Exception):
    """Invalid frame or firmware error response."""

    def __init__(
        self,
        message: str,
        *,
        error: str | None = None,
        detail: str | None = None,
    ) -> None:
        super().__init__(message)
        self.error = error
        self.detail = detail


@dataclass
class CycleEvent:
    seq: int
    addr: str
    data: str
    rw: int


@dataclass
class DoneEvent:
    ok: bool
    reason: str
    cycles: int
    addr: str


@dataclass
class ReadResult:
    ok: bool
    reason: str
    cycles: list[CycleEvent] = field(default_factory=list)
    stopped_addr: str = ""


@dataclass
class StatusResponse:
    phi2_hz: float
    rom_active: bool
    reset_asserted: bool
    last_addr: str
    read_active: bool
    monitor_enabled: bool
    upload_active: bool = False


@dataclass
class UploadProgress:
    action: str
    received: int
    expected: int = ROM_SIZE
    offset: int | None = None
    reset_vector: str | None = None


def build_request(cmd: str, *, req_id: str | None = None, **fields: Any) -> dict[str, Any]:
    if "assert_reset" in fields:
        fields["assert"] = fields.pop("assert_reset")
    payload: dict[str, Any] = {"v": PROTO_VERSION, "cmd": cmd, **fields}
    if req_id is not None:
        payload["id"] = req_id
    return payload


def _require_version(msg: dict[str, Any]) -> None:
    version = msg.get("v")
    if version is None:
        return
    if version != PROTO_VERSION:
        raise ProtocolV1Error(
            f"unsupported protocol version: {version!r}",
            error="unsupported_version",
        )


def parse_frame(raw: bytes | str) -> dict[str, Any]:
    """Parse a JSON frame and validate the v1 envelope."""
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
    """Return a command ack or raise on firmware error."""
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
    _require_version(msg)
    if msg.get("type") != "event" or msg.get("event") != "done":
        raise ProtocolV1Error(f"expected done event, got {msg!r}")
    return DoneEvent(
        ok=bool(msg.get("ok")),
        reason=str(msg.get("reason", "")),
        cycles=int(msg.get("cycles", 0)),
        addr=str(msg.get("addr", "")),
    )


def parse_status(msg: dict[str, Any]) -> StatusResponse:
    parse_command_response(msg)
    return StatusResponse(
        phi2_hz=float(msg.get("phi2_hz", 0)),
        rom_active=bool(msg.get("rom_active")),
        reset_asserted=bool(msg.get("reset_asserted")),
        last_addr=str(msg.get("last_addr", "0000")),
        read_active=bool(msg.get("read_active")),
        monitor_enabled=bool(msg.get("monitor_enabled")),
        upload_active=bool(msg.get("upload_active")),
    )


def parse_upload_response(msg: dict[str, Any]) -> UploadProgress:
    parse_command_response(msg)
    return UploadProgress(
        action=str(msg.get("action", "")),
        received=int(msg.get("received", msg.get("bytes", 0))),
        expected=int(msg.get("expected", ROM_SIZE)),
        offset=int(msg["offset"]) if "offset" in msg else None,
        reset_vector=str(msg["reset_vector"]) if "reset_vector" in msg else None,
    )
