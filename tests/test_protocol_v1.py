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
    parse_done_event,
    parse_frame,
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


def test_parse_status():
    msg = {
        "v": 1,
        "ok": True,
        "cmd": "status",
        "phi2_hz": 0.2,
        "rom_active": True,
        "reset_asserted": False,
        "last_addr": "4000",
        "read_active": False,
        "monitor_enabled": False,
        "upload_active": False,
    }
    st = parse_status(msg)
    assert st.last_addr == "4000"
    assert st.phi2_hz == 0.2


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


def test_chunk_raw_max():
    assert CHUNK_RAW_MAX == 1476
