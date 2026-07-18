"""Tests for the hardware_api framed serial protocol (v1 JSON)."""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from romulan.hardware_api import CaptureResult, HardwareAPI, HardwareAPIError
from romulan.protocol_v1 import CHUNK_RAW_MAX, ROM_SIZE, build_request, parse_drive_response, parse_peek_response

ENQ = b"\x05"
STX = b"\x02"
ACK = b"\x06"
EOT = b"\x04"
NACK = b"\x15"


@pytest.fixture
def mock_serial():
    ser = MagicMock()
    ser._read_buffer: list[bytes] = []

    def read_one(n=1):
        if ser._read_buffer:
            return ser._read_buffer.pop(0)
        return b""

    ser.read.side_effect = read_one
    ser.reset_input_buffer = MagicMock()
    return ser


def _make_api(mock_serial, verbose=False):
    """Instantiate HardwareAPI with a pre-injected mock serial object."""
    api = HardwareAPI.__new__(HardwareAPI)
    api._ser = mock_serial
    api.port = "/dev/ttyFAKE"
    api.baudrate = 115200
    api.timeout = 3.0
    api.verbose = verbose
    return api


def _enqueue_response(mock_serial, response_payload: bytes):
    frame = ENQ + STX + response_payload + EOT
    for byte in frame:
        mock_serial._read_buffer.append(bytes([byte]))


def _enqueue_transaction_acks(mock_serial):
    mock_serial._read_buffer.append(ACK)
    mock_serial._read_buffer.append(ACK)


def _enqueue_ack(mock_serial):
    mock_serial._read_buffer.append(ACK)


def _enqueue_nack(mock_serial):
    mock_serial._read_buffer.append(NACK)


def _parse_ndjson(stderr: str) -> list[dict]:
    return [json.loads(line) for line in stderr.strip().splitlines() if line.strip()]


def _enqueue_exchange(mock_serial, response_payload: bytes):
    """Queue host→pico ACKs plus a pico→host response frame."""
    _enqueue_transaction_acks(mock_serial)
    _enqueue_response(mock_serial, response_payload)


def _enqueue_capture_arm(
    mock_serial, *, max_cycles: int = 500, batch_size: int = 32, phi2_hz: float | None = None
):
    """Queue monitor + reset assert + read + reset release exchanges."""
    _enqueue_exchange(mock_serial, b'{"v":1,"ok":true,"cmd":"monitor","enable":false}')
    _enqueue_exchange(mock_serial, b'{"v":1,"ok":true,"cmd":"reset","asserted":true}')
    read_payload = (
        f'{{"v":1,"ok":true,"cmd":"read","until":"stp",'
        f'"max_cycles":{max_cycles},"batch_size":{batch_size}'
    )
    if phi2_hz is not None:
        read_payload += f',"phi2_hz":{phi2_hz}'
    read_payload += "}"
    _enqueue_exchange(mock_serial, read_payload.encode())
    _enqueue_exchange(mock_serial, b'{"v":1,"ok":true,"cmd":"reset","asserted":false}')


class TestSendFrame:
    def test_basic_json_roundtrip(self, mock_serial):
        request = json.dumps(build_request("request_addr", req_id="t1")).encode()
        response = json.dumps(
            {"v": 1, "id": "t1", "ok": True, "cmd": "request_addr", "addr": "4000", "phi2_hz": 0.2}
        ).encode()

        _enqueue_transaction_acks(mock_serial)
        _enqueue_response(mock_serial, response)

        api = _make_api(mock_serial)
        result = api._send_frame(request)
        assert json.loads(result) == json.loads(response)


class TestReset:
    def test_reset_assert(self, mock_serial):
        _enqueue_transaction_acks(mock_serial)
        _enqueue_response(mock_serial, b'{"v":1,"ok":true,"cmd":"reset","asserted":true}')

        api = _make_api(mock_serial)
        api.reset(assert_reset=True)

        payload = mock_serial.write.call_args_list[2][0][0]
        msg = json.loads(payload)
        assert msg["v"] == 1
        assert msg["cmd"] == "reset"
        assert msg["assert"] is True


class TestFrameResync:
    def test_reset_response_prefixed_with_monitor_garbage(self, mock_serial):
        """ASCII monitor output preceding the JSON payload is skipped."""
        garbage = b"| 01 |  EA  |  8000   |  0 | 1000.0 |\r\n"
        _enqueue_transaction_acks(mock_serial)
        _enqueue_response(
            mock_serial,
            garbage + b'{"v":1,"ok":true,"cmd":"reset","asserted":true}',
        )

        api = _make_api(mock_serial)
        api.reset(assert_reset=True)

        payload = mock_serial.write.call_args_list[2][0][0]
        msg = json.loads(payload)
        assert msg["cmd"] == "reset"
        assert msg["assert"] is True

    def test_reset_release_response_prefixed_with_crlf(self, mock_serial):
        """A CRLF-terminated monitor fragment before the payload is skipped."""
        _enqueue_transaction_acks(mock_serial)
        _enqueue_response(
            mock_serial,
            b"\r\n" + b'{"v":1,"ok":true,"cmd":"reset","asserted":false}',
        )

        api = _make_api(mock_serial)
        api.reset(assert_reset=False)

    def test_resync_event_emitted_when_verbose(self, mock_serial, capsys):
        """verbose=True reports how many stray bytes were skipped."""
        garbage = b"\r\n+----+------+---------+----+-------+\r\n"
        _enqueue_transaction_acks(mock_serial)
        _enqueue_response(
            mock_serial,
            garbage + b'{"v":1,"ok":true,"cmd":"reset","asserted":true}',
        )

        api = _make_api(mock_serial, verbose=True)
        api.reset(assert_reset=True)

        captured = capsys.readouterr()
        events = _parse_ndjson(captured.err)
        resync_events = [e for e in events if e.get("event") == "resync"]
        assert resync_events == [
            {
                "v": 1,
                "type": "event",
                "event": "resync",
                "data": {"skipped_bytes": len(garbage)},
            }
        ]

    def test_reset_response_prefixed_with_json_monitor_line(self, mock_serial):
        """A JSON monitor event line before the payload is not mistaken for it."""
        garbage = (
            b'{"v":1,"type":"event","event":"monitor","data":'
            b'{"seq":1,"addr":"8000","data":"EA","rw":0,"hz":1000.0}}\r\n'
        )
        _enqueue_transaction_acks(mock_serial)
        _enqueue_response(
            mock_serial,
            garbage + b'{"v":1,"ok":true,"cmd":"reset","asserted":true}',
        )

        api = _make_api(mock_serial)
        api.reset(assert_reset=True)

        payload = mock_serial.write.call_args_list[2][0][0]
        msg = json.loads(payload)
        assert msg["cmd"] == "reset"
        assert msg["assert"] is True


class TestPeek:
    def test_peek_success(self, mock_serial):
        _enqueue_transaction_acks(mock_serial)
        _enqueue_response(
            mock_serial,
            b'{"v":1,"ok":true,"cmd":"peek","offset":28672,"count":3,"data":"A9AA05"}',
        )

        api = _make_api(mock_serial)
        result = api.peek(offset=0x7000, count=3)
        assert result.offset == 0x7000
        assert result.count == 3
        assert result.data == b"\xA9\xAA\x05"

    def test_peek_offset_out_of_range(self, mock_serial):
        api = _make_api(mock_serial)
        with pytest.raises(ValueError, match="offset must be"):
            api.peek(offset=ROM_SIZE, count=1)

    def test_peek_count_out_of_range(self, mock_serial):
        api = _make_api(mock_serial)
        with pytest.raises(ValueError, match="count must be"):
            api.peek(offset=0, count=0)
        with pytest.raises(ValueError, match="count must be"):
            api.peek(offset=0, count=65)


class TestSetClock:
    def test_set_clock_success(self, mock_serial):
        _enqueue_transaction_acks(mock_serial)
        _enqueue_response(
            mock_serial,
            b'{"v":1,"ok":true,"cmd":"clock","hz":100.0}',
        )

        api = _make_api(mock_serial)
        api.set_clock(hz=100.0)

        payload = mock_serial.write.call_args_list[2][0][0]
        msg = json.loads(payload)
        assert msg["v"] == 1
        assert msg["cmd"] == "clock"
        assert msg["hz"] == 100.0

    def test_set_clock_out_of_range(self, mock_serial):
        api = _make_api(mock_serial)
        with pytest.raises(ValueError, match="hz must be"):
            api.set_clock(hz=0.05)
        with pytest.raises(ValueError, match="hz must be"):
            api.set_clock(hz=2000.0)


class TestDrive:
    def test_drive_enable_with_int(self, mock_serial):
        _enqueue_transaction_acks(mock_serial)
        _enqueue_response(mock_serial, b'{"v":1,"ok":true,"cmd":"drive","enabled":true,"value":"A5"}')

        api = _make_api(mock_serial)
        result = api.drive(0xA5)
        assert result.enabled is True
        assert result.value == "A5"

        payload = mock_serial.write.call_args_list[2][0][0]
        msg = json.loads(payload)
        assert msg["v"] == 1
        assert msg["cmd"] == "drive"
        assert msg["value"] == "A5"

    def test_drive_enable_with_hex_string(self, mock_serial):
        _enqueue_transaction_acks(mock_serial)
        _enqueue_response(mock_serial, b'{"v":1,"ok":true,"cmd":"drive","enabled":true,"value":"FF"}')

        api = _make_api(mock_serial)
        result = api.drive("FF")
        assert result.enabled is True
        assert result.value == "FF"

        payload = mock_serial.write.call_args_list[2][0][0]
        msg = json.loads(payload)
        assert msg["value"] == "FF"

    def test_drive_disable(self, mock_serial):
        _enqueue_transaction_acks(mock_serial)
        _enqueue_response(mock_serial, b'{"v":1,"ok":true,"cmd":"drive","enabled":false,"value":"00"}')

        api = _make_api(mock_serial)
        result = api.drive(None)
        assert result.enabled is False
        assert result.value == "00"

        payload = mock_serial.write.call_args_list[2][0][0]
        msg = json.loads(payload)
        assert msg["cmd"] == "drive"
        assert msg["enable"] is False

    def test_drive_value_out_of_range(self, mock_serial):
        api = _make_api(mock_serial)
        with pytest.raises(ValueError, match="must be a byte"):
            api.drive(256)
        with pytest.raises(ValueError, match="must be a byte"):
            api.drive(-1)


class TestUploadRom:
    def test_upload_32kb_chunked(self, mock_serial):
        rom = bytes(range(256)) * (ROM_SIZE // 256)

        _enqueue_transaction_acks(mock_serial)
        _enqueue_response(mock_serial, b'{"v":1,"ok":true,"cmd":"monitor","enable":false}')

        _enqueue_transaction_acks(mock_serial)
        _enqueue_response(
            mock_serial,
            b'{"v":1,"ok":true,"cmd":"upload_rom","action":"begin","received":0,"expected":32768}',
        )

        offset = 0
        while offset < ROM_SIZE:
            chunk = rom[offset : offset + CHUNK_RAW_MAX]
            received = offset + len(chunk)
            resp = json.dumps(
                {
                    "v": 1,
                    "ok": True,
                    "cmd": "upload_rom",
                    "action": "chunk",
                    "offset": offset,
                    "received": received,
                }
            ).encode()
            _enqueue_transaction_acks(mock_serial)
            _enqueue_response(mock_serial, resp)
            offset = received

        _enqueue_transaction_acks(mock_serial)
        _enqueue_response(
            mock_serial,
            b'{"v":1,"ok":true,"cmd":"upload_rom","action":"commit","bytes":32768,"reset_vector":"0080"}',
        )

        api = _make_api(mock_serial)
        result = api.upload_rom(rom)
        assert result["bytes"] == ROM_SIZE
        assert result["reset_vector"] == "0080"

    def test_upload_wrong_size(self, mock_serial):
        api = _make_api(mock_serial)
        with pytest.raises(ValueError, match="exactly 32768"):
            api.upload_rom(b"\x00\x01")


class TestReadUntilStp:
    def test_capture_result(self, mock_serial):
        _enqueue_capture_arm(mock_serial, max_cycles=500)
        _enqueue_exchange(
            mock_serial,
            b'{"v":1,"ok":true,"type":"event","event":"cycles","cycles":[{"seq":1,"addr":"8000","data":"18","rw":0}]}',
        )
        _enqueue_exchange(
            mock_serial,
            b'{"v":1,"ok":true,"type":"event","event":"done","reason":"stp","cycles":1,"addr":"8001"}',
        )

        api = _make_api(mock_serial)
        result = api.read_until_stp(max_cycles=500)
        assert isinstance(result, CaptureResult)
        assert result.reason == "stp"
        assert len(result.cycles) == 1

    def test_uses_instance_timeout_when_no_frame_timeout(self, mock_serial):
        """read_until_stp defaults frame_timeout to self.timeout."""
        _enqueue_capture_arm(mock_serial, max_cycles=10)
        _enqueue_exchange(
            mock_serial,
            b'{"v":1,"ok":true,"type":"event","event":"done","reason":"stp","cycles":0,"addr":"8000"}',
        )

        api = _make_api(mock_serial)
        api.timeout = 99.0
        result = api.read_until_stp(max_cycles=10)
        assert result.reason == "stp"

    def test_on_cycle_callback(self, mock_serial):
        _enqueue_capture_arm(mock_serial, max_cycles=10)
        _enqueue_exchange(
            mock_serial,
            b'{"v":1,"ok":true,"type":"event","event":"cycles","cycles":[{"seq":1,"addr":"8000","data":"18","rw":0}]}',
        )
        _enqueue_exchange(
            mock_serial,
            b'{"v":1,"ok":true,"type":"event","event":"done","reason":"stp","cycles":1,"addr":"8000"}',
        )

        seen = []
        api = _make_api(mock_serial)
        api.read_until_stp(max_cycles=10, on_cycle=seen.append)
        assert len(seen) == 1
        assert seen[0].addr == "8000"

    def test_batch_size_defaults_to_read_event_batch_size(self, mock_serial):
        """read_event requests use the configured batch_size."""
        from romulan.protocol_v1 import READ_EVENT_BATCH_SIZE

        _enqueue_capture_arm(mock_serial, max_cycles=10, batch_size=READ_EVENT_BATCH_SIZE)
        _enqueue_exchange(
            mock_serial,
            b'{"v":1,"ok":true,"type":"event","event":"done","reason":"stp","cycles":0,"addr":"8000"}',
        )

        api = _make_api(mock_serial)
        api.read_until_stp(max_cycles=10)

        # Find the read_event request payload and verify batch_size.
        for call in mock_serial.write.call_args_list:
            payload = call[0][0]
            if b'"cmd":"read_event"' in payload:
                assert f'"batch_size":{READ_EVENT_BATCH_SIZE}'.encode() in payload
                break
        else:
            pytest.fail("never sent read_event request")

    def test_preserves_clock_speed_by_default(self, mock_serial):
        """read command does not include phi2_hz unless explicitly provided."""
        _enqueue_capture_arm(mock_serial, max_cycles=10)
        _enqueue_exchange(
            mock_serial,
            b'{"v":1,"ok":true,"type":"event","event":"done","reason":"stp","cycles":0,"addr":"8000"}',
        )

        api = _make_api(mock_serial)
        api.read_until_stp(max_cycles=10)

        for call in mock_serial.write.call_args_list:
            payload = call[0][0]
            if b'"cmd":"read"' in payload:
                assert b'"phi2_hz"' not in payload
                break
        else:
            pytest.fail("never sent read request")

    def test_explicit_phi2_hz_sent_when_provided(self, mock_serial):
        """read command includes phi2_hz when the caller passes it."""
        _enqueue_capture_arm(mock_serial, max_cycles=10, phi2_hz=250.0)
        _enqueue_exchange(
            mock_serial,
            b'{"v":1,"ok":true,"type":"event","event":"done","reason":"stp","cycles":0,"addr":"8000"}',
        )

        api = _make_api(mock_serial)
        api.read_until_stp(max_cycles=10, phi2_hz=250.0)

        for call in mock_serial.write.call_args_list:
            payload = call[0][0]
            if b'"cmd":"read"' in payload:
                assert b'"phi2_hz":250.0' in payload
                break
        else:
            pytest.fail("never sent read request")


# ---------------------------------------------------------------------------
# CaptureResult
# ---------------------------------------------------------------------------

class TestCaptureResult:
    def test_dataclass_fields(self):
        """CaptureResult stores reason and cycles."""
        cycles = [{"addr": 0x8000, "data": 0x18, "rw": "read"}]
        cr = CaptureResult(reason="stp", cycles=cycles)
        assert cr.reason == "stp"
        assert cr.cycles == cycles

    def test_repr(self):
        """CaptureResult repr shows reason and cycle count."""
        cr = CaptureResult(reason="max_cycles", cycles=[])
        assert repr(cr) == "CaptureResult(reason='max_cycles', cycles=0)"


# ---------------------------------------------------------------------------
# Verbose logging
# ---------------------------------------------------------------------------

class TestVerboseLogging:
    def test_request_addr_verbose(self, mock_serial, capsys):
        """verbose=True emits JSON events for request_addr."""
        _enqueue_transaction_acks(mock_serial)
        _enqueue_response(
            mock_serial,
            b'{"v":1,"ok":true,"cmd":"request_addr","addr":"8000"}',
        )

        api = _make_api(mock_serial, verbose=True)
        addr = api.request_addr()
        assert addr == 0x8000

        captured = capsys.readouterr()
        events = _parse_ndjson(captured.err)
        assert events[0] == {"v": 1, "type": "event", "event": "call", "data": {"method": "request_addr"}}
        assert events[1]["event"] == "send"
        assert events[1]["data"]["payload"]["cmd"] == "request_addr"
        assert events[2] == {"v": 1, "type": "event", "event": "ack"}
        assert events[3] == {"v": 1, "type": "event", "event": "ack"}
        assert events[4]["event"] == "recv"
        assert events[4]["data"]["payload"]["addr"] == "8000"
        assert events[5] == {
            "v": 1,
            "type": "event",
            "event": "ret",
            "data": {"method": "request_addr", "result": 0x8000},
        }

    def test_reset_verbose(self, mock_serial, capsys):
        """verbose=True emits JSON events for reset."""
        _enqueue_transaction_acks(mock_serial)
        _enqueue_response(mock_serial, b'{"v":1,"ok":true,"cmd":"reset"}')

        api = _make_api(mock_serial, verbose=True)
        api.reset(assert_reset=True)

        captured = capsys.readouterr()
        events = _parse_ndjson(captured.err)
        assert events[0] == {
            "v": 1,
            "type": "event",
            "event": "call",
            "data": {"method": "reset", "assert_reset": True},
        }
        assert events[1]["event"] == "send"
        assert events[2] == {"v": 1, "type": "event", "event": "ack"}
        assert events[3] == {"v": 1, "type": "event", "event": "ack"}
        assert events[4]["event"] == "recv"
        assert events[5] == {"v": 1, "type": "event", "event": "ret", "data": {"method": "reset"}}

    def test_monitor_verbose(self, mock_serial, capsys):
        """verbose=True emits JSON events for monitor."""
        _enqueue_transaction_acks(mock_serial)
        _enqueue_response(mock_serial, b'{"v":1,"ok":true,"cmd":"monitor","enable":false}')

        api = _make_api(mock_serial, verbose=True)
        api.monitor(enable=False)

        captured = capsys.readouterr()
        events = _parse_ndjson(captured.err)
        assert events[0] == {
            "v": 1,
            "type": "event",
            "event": "call",
            "data": {"method": "monitor", "enable": False},
        }
        assert events[1]["event"] == "send"
        assert events[2] == {"v": 1, "type": "event", "event": "ack"}
        assert events[3] == {"v": 1, "type": "event", "event": "ack"}
        assert events[4]["event"] == "recv"
        assert events[5] == {"v": 1, "type": "event", "event": "ret", "data": {"method": "monitor"}}

    def test_upload_rom_verbose(self, mock_serial, capsys):
        """verbose=True emits JSON events for upload_rom."""
        rom = b"\xEA" * ROM_SIZE

        _enqueue_transaction_acks(mock_serial)
        _enqueue_response(mock_serial, b'{"v":1,"ok":true,"cmd":"monitor","enable":false}')

        _enqueue_transaction_acks(mock_serial)
        _enqueue_response(
            mock_serial,
            b'{"v":1,"ok":true,"cmd":"upload_rom","action":"begin","received":0,"expected":32768}',
        )

        offset = 0
        while offset < ROM_SIZE:
            received = offset + min(CHUNK_RAW_MAX, ROM_SIZE - offset)
            resp = json.dumps(
                {
                    "v": 1,
                    "ok": True,
                    "cmd": "upload_rom",
                    "action": "chunk",
                    "offset": offset,
                    "received": received,
                }
            ).encode()
            _enqueue_transaction_acks(mock_serial)
            _enqueue_response(mock_serial, resp)
            offset = received

        _enqueue_transaction_acks(mock_serial)
        _enqueue_response(
            mock_serial,
            b'{"v":1,"ok":true,"cmd":"upload_rom","action":"commit","bytes":32768,"reset_vector":"8000"}',
        )

        api = _make_api(mock_serial, verbose=True)
        result = api.upload_rom(rom)
        assert result["bytes"] == ROM_SIZE

        captured = capsys.readouterr()
        events = _parse_ndjson(captured.err)
        assert events[0] == {
            "v": 1,
            "type": "event",
            "event": "call",
            "data": {"method": "upload_rom", "size": ROM_SIZE},
        }
        assert any(
            e.get("event") == "call" and e.get("data") == {"method": "monitor", "enable": False}
            for e in events
        )
        assert any(
            e.get("event") == "ret"
            and e.get("data", {}).get("method") == "upload_rom"
            and e["data"]["result"]["bytes"] == ROM_SIZE
            for e in events
        )

    def test_read_until_stp_verbose(self, mock_serial, capsys):
        """verbose=True emits JSON events for read_until_stp."""
        from romulan.protocol_v1 import READ_EVENT_BATCH_SIZE

        _enqueue_capture_arm(mock_serial, max_cycles=500)
        _enqueue_exchange(
            mock_serial,
            b'{"v":1,"ok":true,"type":"event","event":"done","reason":"stp","cycles":0,"addr":"8000"}',
        )

        api = _make_api(mock_serial, verbose=True)
        api.read_until_stp(max_cycles=500)

        captured = capsys.readouterr()
        events = _parse_ndjson(captured.err)
        assert events[0] == {
            "v": 1,
            "type": "event",
            "event": "call",
            "data": {
                "method": "read_until_stp",
                "max_cycles": 500,
                "batch_size": READ_EVENT_BATCH_SIZE,
            },
        }
        assert any(
            e.get("event") == "ret" and e.get("data", {}).get("method") == "read_until_stp"
            for e in events
        )

    def test_verbose_false_silent(self, mock_serial, capsys):
        """verbose=False produces no stderr output."""
        _enqueue_transaction_acks(mock_serial)
        _enqueue_response(
            mock_serial,
            b'{"v":1,"ok":true,"cmd":"request_addr","addr":"8000"}',
        )

        api = _make_api(mock_serial, verbose=False)
        api.request_addr()

        captured = capsys.readouterr()
        assert captured.err == ""

    def test_binary_payload_preview(self, mock_serial, capsys):
        """Binary payloads are emitted as binary metadata in JSON."""
        _enqueue_transaction_acks(mock_serial)
        _enqueue_response(mock_serial, b'{"v":1,"ok":true}')

        api = _make_api(mock_serial, verbose=True)
        api._send_frame(b"\x80\x81\x82\x83")

        captured = capsys.readouterr()
        events = _parse_ndjson(captured.err)
        assert events[0] == {
            "v": 1,
            "type": "event",
            "event": "send",
            "data": {"payload": {"binary": True, "bytes": 4}},
        }
        assert events[1] == {"v": 1, "type": "event", "event": "ack"}
        assert events[2] == {"v": 1, "type": "event", "event": "ack"}
        assert events[3] == {
            "v": 1,
            "type": "event",
            "event": "recv",
            "data": {"payload": {"ok": True, "v": 1}},
        }

    def test_error_logs_verbose(self, mock_serial, capsys):
        """NACK is emitted as a JSON error event in verbose mode."""
        _enqueue_ack(mock_serial)
        _enqueue_nack(mock_serial)

        api = _make_api(mock_serial, verbose=True)
        with pytest.raises(HardwareAPIError, match="NACK"):
            api._send_frame(b"x")

        captured = capsys.readouterr()
        events = _parse_ndjson(captured.err)
        assert events[0] == {
            "v": 1,
            "type": "event",
            "event": "send",
            "data": {"payload": {"binary": True, "bytes": 1}},
        }
        assert events[1] == {"v": 1, "type": "event", "event": "ack"}
        assert events[2] == {"v": 1, "type": "event", "event": "nack"}
        assert events[3] == {
            "v": 1,
            "type": "error",
            "error": "nack",
            "detail": "Pico responded with NACK",
        }


# ---------------------------------------------------------------------------
# Integration / smoke
# ---------------------------------------------------------------------------

class TestFirmwareWithoutVersionField:
    def test_monitor_without_version_field(self, mock_serial):
        _enqueue_transaction_acks(mock_serial)
        _enqueue_response(mock_serial, b'{"ok":true,"cmd":"monitor","enable":false}')

        api = _make_api(mock_serial)
        api.monitor(enable=False)

    def test_upload_without_version_in_responses(self, mock_serial):
        rom = b"\xEA" * ROM_SIZE

        _enqueue_transaction_acks(mock_serial)
        _enqueue_response(mock_serial, b'{"ok":true,"cmd":"monitor","enable":false}')

        _enqueue_transaction_acks(mock_serial)
        _enqueue_response(
            mock_serial,
            b'{"ok":true,"cmd":"upload_rom","action":"begin","received":0,"expected":32768}',
        )

        offset = 0
        while offset < ROM_SIZE:
            received = offset + min(CHUNK_RAW_MAX, ROM_SIZE - offset)
            resp = json.dumps(
                {
                    "ok": True,
                    "cmd": "upload_rom",
                    "action": "chunk",
                    "offset": offset,
                    "received": received,
                }
            ).encode()
            _enqueue_transaction_acks(mock_serial)
            _enqueue_response(mock_serial, resp)
            offset = received

        _enqueue_transaction_acks(mock_serial)
        _enqueue_response(
            mock_serial,
            b'{"ok":true,"cmd":"upload_rom","action":"commit","bytes":32768,"reset_vector":"8000"}',
        )

        api = _make_api(mock_serial)
        result = api.upload_rom(rom)
        assert result["bytes"] == ROM_SIZE

    def test_request_addr_decimal(self, mock_serial):
        _enqueue_transaction_acks(mock_serial)
        _enqueue_response(mock_serial, b'{"ok":true,"cmd":"request_addr","addr":32768}')

        api = _make_api(mock_serial)
        assert api.request_addr() == 32768


class TestLivePeek:
    def test_live_peek_success(self, mock_serial):
        _enqueue_transaction_acks(mock_serial)
        _enqueue_response(
            mock_serial,
            b'{"v":1,"ok":true,"cmd":"peek","addr":"4000","data":"14"}',
        )

        api = _make_api(mock_serial)
        result = api.live_peek(0x4000)
        assert result.addr == 0x4000
        assert result.data == 0x14

        payload = mock_serial.write.call_args_list[2][0][0]
        msg = json.loads(payload)
        assert msg["cmd"] == "peek"
        assert msg["addr"] == "4000"

    def test_live_peek_missing_cycle_error(self, mock_serial):
        _enqueue_transaction_acks(mock_serial)
        _enqueue_response(
            mock_serial,
            b'{"v":1,"ok":false,"error":"no_cycle","detail":"no bus cycle matched addr"}',
        )

        api = _make_api(mock_serial)
        with pytest.raises(HardwareAPIError, match="no bus cycle matched addr"):
            api.live_peek(0x4000)

    def test_live_peek_addr_out_of_range(self, mock_serial):
        api = _make_api(mock_serial)
        with pytest.raises(ValueError, match="0..0xFFFF"):
            api.live_peek(0x10000)


class TestHardwareAPIIntegration:
    @patch("romulan.hardware_api.serial.Serial")
    def test_context_manager(self, mock_serial_cls):
        mock_serial_cls.return_value = MagicMock()
        with HardwareAPI("/dev/ttyFAKE") as api:
            assert api._ser is not None
        mock_serial_cls.return_value.close.assert_called_once()
