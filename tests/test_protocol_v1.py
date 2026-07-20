"""Tests for protocol_v1 parser."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from romulan.protocol_v1 import (
    CHUNK_RAW_MAX,
    ROM_SIZE,
    ProtocolV1Error,
    build_request,
    parse_command_response,
    parse_cycle_event,
    parse_cycles_event,
    parse_done_event,
    parse_drive_response,
    parse_frame,
    parse_live_peek_response,
    parse_peek_response,
    parse_status,
    parse_upload_response,
)


def test_build_request_includes_version():
    req = build_request("reset", req_id="abc", assert_reset=True)
    assert req == {"v": 1, "cmd": "reset", "id": "abc", "assert": True}


def test_parse_frame_rejects_wrong_version():
    with pytest.raises(ProtocolV1Error, match="unsupported protocol version"):
        parse_frame(json.dumps({"v": 2, "cmd": "status"}))


def test_parse_frame_accepts_missing_version():
    msg = parse_frame('{"ok":true,"cmd":"monitor","enable":false}')
    assert msg["cmd"] == "monitor"
    assert "v" not in msg


def test_parse_command_response_error():
    msg = {"v": 1, "ok": False, "error": "bad_size", "detail": "size must be 32768"}
    with pytest.raises(ProtocolV1Error) as exc:
        parse_command_response(msg)
    assert exc.value.error == "bad_size"


def test_parse_cycle_event():
    msg = {
        "v": 1,
        "type": "event",
        "event": "cycle",
        "seq": 1,
        "addr": "8000",
        "data": "18",
        "rw": 0,
    }
    ev = parse_cycle_event(msg)
    assert ev.addr == "8000"
    assert ev.data == "18"


def test_parse_done_event():
    msg = {
        "v": 1,
        "type": "event",
        "event": "done",
        "ok": True,
        "reason": "stp",
        "cycles": 3,
        "addr": "800D",
    }
    done = parse_done_event(msg)
    assert done.reason == "stp"
    assert done.cycles == 3


def test_parse_cycles_event():
    msg = {
        "v": 1,
        "type": "event",
        "event": "cycles",
        "cycles": [
            {"seq": 1, "addr": "8000", "data": "18", "rw": 0},
            {"seq": 2, "addr": "8001", "data": "A9", "rw": 0},
        ],
    }
    cycles = parse_cycles_event(msg)
    assert len(cycles) == 2
    assert cycles[0].addr == "8000"
    assert cycles[1].data == "A9"


def test_parse_status():
    msg = {
        "v": 1,
        "ok": True,
        "cmd": "status",
        "phi2_hz": 0.2,
        "rom_active": True,
        "reset_asserted": False,
        "last_addr": "4000",
        "last_data": "18",
        "last_rw": 0,
        "read_active": False,
        "monitor_enabled": False,
        "upload_active": False,
        "resb": 1,
        "rwb": 0,
        "a15": 1,
        "phi2": 0,
    }
    st = parse_status(msg)
    assert st.last_addr == "4000"
    assert st.last_data == "18"
    assert st.last_rw == 0
    assert st.phi2_hz == 0.2
    assert st.resb == 1
    assert st.rwb == 0
    assert st.a15 == 1
    assert st.phi2 == 0


def test_parse_peek_response():
    msg = {
        "v": 1,
        "ok": True,
        "cmd": "peek",
        "offset": 28672,
        "count": 3,
        "data": "A9AA05",
    }
    peek = parse_peek_response(msg)
    assert peek.offset == 28672
    assert peek.count == 3
    assert peek.data == b"\xA9\xAA\x05"


def test_parse_peek_response_rejects_invalid_hex():
    msg = {
        "v": 1,
        "ok": True,
        "cmd": "peek",
        "offset": 0,
        "count": 1,
        "data": "ZZ",
    }
    with pytest.raises(ProtocolV1Error, match="invalid hex"):
        parse_peek_response(msg)


def test_parse_upload_commit():
    msg = {
        "v": 1,
        "ok": True,
        "cmd": "upload_rom",
        "action": "commit",
        "bytes": ROM_SIZE,
        "reset_vector": "8000",
    }
    up = parse_upload_response(msg)
    assert up.reset_vector == "8000"
    assert up.received == ROM_SIZE


def test_parse_drive_response_enabled():
    msg = {
        "v": 1,
        "ok": True,
        "cmd": "drive",
        "enabled": True,
        "value": "A5",
    }
    dr = parse_drive_response(msg)
    assert dr.enabled is True
    assert dr.value == "A5"


def test_parse_drive_response_disabled():
    msg = {
        "v": 1,
        "ok": True,
        "cmd": "drive",
        "enabled": False,
        "value": "00",
    }
    dr = parse_drive_response(msg)
    assert dr.enabled is False
    assert dr.value == "00"


def test_chunk_raw_max():
    assert CHUNK_RAW_MAX == ROM_SIZE
    assert CHUNK_RAW_MAX == 0x8000


def test_parse_live_peek_response():
    msg = {
        "v": 1,
        "ok": True,
        "cmd": "peek",
        "addr": "4000",
        "data": "14",
    }
    peek = parse_live_peek_response(msg)
    assert peek.addr == 0x4000
    assert peek.data == 0x14


def test_parse_live_peek_response_error():
    msg = {"v": 1, "ok": False, "error": "no_cycle", "detail": "no bus cycle matched addr"}
    with pytest.raises(ProtocolV1Error) as exc:
        parse_live_peek_response(msg)
    assert exc.value.error == "no_cycle"
