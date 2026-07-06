"""Hardware API v1 — framed JSON serial protocol for the Pico-as-ROM 65C02 system."""

from __future__ import annotations

import base64
import json
import sys
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

    def __repr__(self) -> str:
        return f"CaptureResult(reason={self.reason!r}, cycles={len(self.cycles)})"

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

    def _log(self, msg: str) -> None:
        if self.verbose:
            print(f"[HW] {msg}", file=sys.stderr, flush=True)

    @staticmethod
    def _payload_preview(payload: bytes) -> str:
        try:
            return payload.decode("utf-8")
        except UnicodeDecodeError:
            return f"<binary, {len(payload)} bytes>"

    def _sync_to_byte(self, acceptable: set[int], timeout: float | None = None) -> int:
        wait = self.timeout if timeout is None else timeout
        deadline = time.time() + wait
        while time.time() < deadline:
            b = self.ser.read(1)
            if b and b[0] in acceptable:
                return b[0]
        labels = ", ".join(f"0x{v:02X}" for v in sorted(acceptable))
        self._log(f"ERROR: timed out waiting for {labels}")
        raise TimeoutError(f"timed out waiting for {labels}")

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
        self._log("ERROR: timed out waiting for EOT in response payload")
        raise TimeoutError("timed out waiting for EOT in response payload")

    def _send_frame_host(self, payload: bytes) -> None:
        self._log(f"SEND: {self._payload_preview(payload)}")
        self._write_byte(ENQ)
        self._write_byte(STX)
        if self._sync_to_byte({ACK, NACK}) != ACK:
            self._log("ERROR: Pico responded with NACK")
            raise HardwareAPIError("Pico responded with NACK")
        self.ser.write(payload)
        self._write_byte(EOT)
        if self._sync_to_byte({ACK, NACK}) != ACK:
            self._log("ERROR: Pico responded with NACK")
            raise HardwareAPIError("Pico responded with NACK")

    def _send_frame(self, payload: bytes) -> bytes:
        self._send_frame_host(payload)
        self._sync_to_byte({ENQ})
        raw = self._read_frame_payload()
        self._log(f"RECV: {self._payload_preview(raw)}")
        return raw

    def _recv_json_frame(self, timeout: float | None = None) -> dict[str, Any]:
        wait = self.timeout if timeout is None else timeout
        self._sync_to_byte({ENQ}, timeout=wait)
        raw = self._read_frame_payload(timeout=wait)
        self._log(f"RECV: {self._payload_preview(raw)}")
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
        self._log("CALL request_addr()")
        resp = self._exchange_json(build_request("request_addr", req_id=self._next_id()))
        addr = resp.get("addr")
        if addr is None:
            raise HardwareAPIError(f"Missing 'addr' in response: {resp!r}")
        addr_int = self._parse_addr(addr)
        self._log(f"RET request_addr -> {addr_int}")
        return addr_int

    def reset(self, assert_reset: bool) -> None:
        self._log(f"CALL reset(assert_reset={assert_reset})")
        self._exchange_json(
            build_request("reset", req_id=self._next_id(), assert_reset=assert_reset)
        )
        self._log("RET reset")

    def monitor(self, enable: bool) -> None:
        self._log(f"CALL monitor(enable={enable})")
        self._exchange_json(
            build_request("monitor", req_id=self._next_id(), enable=enable)
        )
        self._log("RET monitor")

    def status(self) -> StatusResponse:
        resp = self._exchange_json(build_request("status", req_id=self._next_id()))
        return parse_status(resp)

    def upload_rom(self, data: bytes) -> dict[str, Any]:
        self._log(f"CALL upload_rom(size={len(data)})")
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
        self._log(f"RET upload_rom -> {result}")
        return result

    def read_until_stp(
        self,
        max_cycles: int = 10000,
        frame_timeout: float = READ_FRAME_TIMEOUT,
    ) -> CaptureResult:
        self._log(f"CALL read_until_stp(max_cycles={max_cycles})")
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

        capture = CaptureResult.from_read_result(result)
        self._log(f"RET read_until_stp -> {capture}")
        return capture


def open_hardware_api(port: str | None = None) -> HardwareAPI:
    resolved = port or find_pico_port()
    if not resolved:
        raise HardwareAPIError("No Pico serial port found")
    return HardwareAPI(resolved)
