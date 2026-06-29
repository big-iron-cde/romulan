"""Hardware API v1 — framed JSON serial protocol for the Pico-as-ROM 65C02 system."""

from __future__ import annotations

import base64
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import serial

from .protocol_v1 import (
    CHUNK_RAW_MAX,
    ROM_SIZE,
    CycleEvent,
    ProtocolV1Error,
    ReadResult,
    StatusResponse,
    build_request,
    parse_command_response,
    parse_cycle_event,
    parse_done_event,
    parse_frame,
    parse_status,
    parse_upload_response,
)
from .upload_rom import find_pico_port

ENQ = 0x05
STX = 0x02
ACK = 0x06
EOT = 0x04
NACK = 0x15

READ_FRAME_TIMEOUT = 12.0


class HardwareAPIError(Exception):
    """Raised when the Pico responds with NACK or a frame error occurs."""


@dataclass
class CaptureResult:
    """Result of a bus capture (read until STP)."""

    reason: str
    cycles: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_read_result(cls, result: ReadResult) -> CaptureResult:
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
    """Context-manager compatible hardware API for Pico-as-ROM firmware v1."""

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

    @property
    def ser(self) -> serial.Serial:
        if self._ser is None:
            raise HardwareAPIError("Serial port is closed")
        return self._ser

    def _read_byte(self, timeout: float | None = None) -> int:
        wait = self.timeout if timeout is None else timeout
        deadline = time.time() + wait
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

    def _write_byte(self, value: int) -> None:
        self.ser.write(bytes([value]))

    def _read_frame_payload(self, timeout: float | None = None) -> bytes:
        wait = self.timeout if timeout is None else timeout
        if self._sync_to_byte({STX}, timeout=wait) != STX:
            raise HardwareAPIError("expected STX after ENQ")

        self._write_byte(ACK)

        buf = bytearray()
        deadline = time.time() + max(wait, 30.0)
        while time.time() < deadline:
            chunk = self.ser.read(256)
            if not chunk:
                continue
            for byte in chunk:
                if byte == EOT:
                    self._write_byte(ACK)
                    return bytes(buf)
                buf.append(byte)
        raise TimeoutError("timed out waiting for EOT")

    def _send_frame_host(self, payload: bytes) -> None:
        self._write_byte(ENQ)
        self._write_byte(STX)
        if self._sync_to_byte({ACK, NACK}) != ACK:
            raise HardwareAPIError("Pico responded with NACK before payload")
        self.ser.write(payload)
        self._write_byte(EOT)
        if self._sync_to_byte({ACK, NACK}) != ACK:
            raise HardwareAPIError("Pico responded with NACK after EOT")

    def _send_frame(self, payload: bytes) -> bytes:
        self._send_frame_host(payload)
        self._sync_to_byte({ENQ})
        return self._read_frame_payload()

    def _recv_json_frame(self, timeout: float | None = None) -> dict[str, Any]:
        wait = self.timeout if timeout is None else timeout
        self._sync_to_byte({ENQ}, timeout=wait)
        raw = self._read_frame_payload(timeout=wait)
        return parse_frame(raw)

    def _exchange_json(self, command: dict[str, Any]) -> dict[str, Any]:
        payload = json.dumps(command, separators=(",", ":")).encode("utf-8")
        raw = self._send_frame(payload)
        msg = parse_frame(raw)
        try:
            return parse_command_response(msg)
        except ProtocolV1Error as exc:
            raise HardwareAPIError(str(exc)) from exc

    def _next_id(self) -> str:
        return uuid.uuid4().hex[:12]

    def _drain_input(self, settle_s: float = 0.3) -> None:
        time.sleep(settle_s)
        self.ser.reset_input_buffer()

    def request_addr(self) -> int:
        resp = self._exchange_json(build_request("request_addr", req_id=self._next_id()))
        addr = resp.get("addr")
        if addr is None:
            raise HardwareAPIError(f"Missing 'addr' in response: {resp!r}")
        return int(str(addr), 16)

    def reset(self, assert_reset: bool) -> None:
        self._exchange_json(
            build_request("reset", req_id=self._next_id(), assert_reset=assert_reset)
        )

    def monitor(self, enable: bool) -> None:
        self._exchange_json(
            build_request("monitor", req_id=self._next_id(), enable=enable)
        )

    def status(self) -> StatusResponse:
        resp = self._exchange_json(build_request("status", req_id=self._next_id()))
        return parse_status(resp)

    def upload_rom(self, data: bytes) -> dict[str, Any]:
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
        return {
            "ok": True,
            "bytes": final.received,
            "reset_vector": final.reset_vector,
            "expected": progress.expected,
        }

    def read_until_stp(
        self,
        max_cycles: int = 10000,
        frame_timeout: float = READ_FRAME_TIMEOUT,
    ) -> CaptureResult:
        self.monitor(enable=False)
        self._drain_input()

        ack = self._exchange_json(
            build_request(
                "read",
                req_id=self._next_id(),
                until="stp",
                max_cycles=max_cycles,
            )
        )
        if not ack.get("ok"):
            raise HardwareAPIError(f"read rejected: {ack}")

        cycles: list[CycleEvent] = []
        result = ReadResult(ok=False, reason="unknown")

        while True:
            msg = self._recv_json_frame(timeout=frame_timeout)
            if msg.get("type") == "event" and msg.get("event") == "cycle":
                cycles.append(parse_cycle_event(msg))
            elif msg.get("type") == "event" and msg.get("event") == "done":
                done = parse_done_event(msg)
                result = ReadResult(
                    ok=done.ok,
                    reason=done.reason,
                    cycles=cycles,
                    stopped_addr=done.addr,
                )
                break
            else:
                raise HardwareAPIError(f"unexpected frame during read: {msg}")

        return CaptureResult.from_read_result(result)


def open_hardware_api(port: str | None = None) -> HardwareAPI:
    resolved = port or find_pico_port()
    if not resolved:
        raise HardwareAPIError("No Pico serial port found")
    return HardwareAPI(resolved)
