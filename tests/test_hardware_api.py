"""Tests for the hardware_api framed serial protocol (v1 JSON)."""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from romulan.hardware_api import CaptureResult, HardwareAPI, HardwareAPIError
from romulan.protocol_v1 import CHUNK_RAW_MAX, ROM_SIZE, build_request, parse_peek_response

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


def _enqueue_capture_arm(mock_serial, *, max_cycles: int = 500):
    """Queue monitor + reset assert + read + reset release exchanges."""
    _enqueue_exchange(mock_serial, b'{"v":1,"ok":true,"cmd":"monitor","enable":false}')
    _enqueue_exchange(mock_serial, b'{"v":1,"ok":true,"cmd":"reset","asserted":true}')
    _enqueue_exchange(
        mock_serial,
        (
            f'{{"v":1,"ok":true,"cmd":"read","until":"stp","max_cycles":{max_cycles}}}'
        ).encode(),
    )
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
            b'{"v":1,"ok":true,"type":"event","event":"cycle","seq":1,"addr":"8000","data":"18","rw":0}',
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
            b'{"v":1,"ok":true,"type":"event","event":"cycle","seq":1,"addr":"8000","data":"18","rw":0}',
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
        assert events[0] == {"type": "call", "method": "request_addr"}
        assert events[1]["type"] == "send"
        assert events[1]["payload"]["cmd"] == "request_addr"
        assert events[2] == {"type": "ack"}
        assert events[3] == {"type": "ack"}
        assert events[4]["type"] == "recv"
        assert events[4]["payload"]["addr"] == "8000"
        assert events[5] == {"type": "ret", "method": "request_addr", "result": 0x8000}

    def test_reset_verbose(self, mock_serial, capsys):
        """verbose=True emits JSON events for reset."""
        _enqueue_transaction_acks(mock_serial)
        _enqueue_response(mock_serial, b'{"v":1,"ok":true,"cmd":"reset"}')

        api = _make_api(mock_serial, verbose=True)
        api.reset(assert_reset=True)

        captured = capsys.readouterr()
        events = _parse_ndjson(captured.err)
        assert events[0] == {"type": "call", "method": "reset", "assert_reset": True}
        assert events[1]["type"] == "send"
        assert events[2] == {"type": "ack"}
        assert events[3] == {"type": "ack"}
        assert events[4]["type"] == "recv"
        assert events[5] == {"type": "ret", "method": "reset"}

    def test_monitor_verbose(self, mock_serial, capsys):
        """verbose=True emits JSON events for monitor."""
        _enqueue_transaction_acks(mock_serial)
        _enqueue_response(mock_serial, b'{"v":1,"ok":true,"cmd":"monitor","enable":false}')

        api = _make_api(mock_serial, verbose=True)
        api.monitor(enable=False)

        captured = capsys.readouterr()
        events = _parse_ndjson(captured.err)
        assert events[0] == {"type": "call", "method": "monitor", "enable": False}
        assert events[1]["type"] == "send"
        assert events[2] == {"type": "ack"}
        assert events[3] == {"type": "ack"}
        assert events[4]["type"] == "recv"
        assert events[5] == {"type": "ret", "method": "monitor"}

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
        assert events[0] == {"type": "call", "method": "upload_rom", "size": ROM_SIZE}
        assert any(e == {"type": "call", "method": "monitor", "enable": False} for e in events)
        assert any(
            e["type"] == "ret" and e["method"] == "upload_rom" and e["result"]["bytes"] == ROM_SIZE
            for e in events
        )

    def test_read_until_stp_verbose(self, mock_serial, capsys):
        """verbose=True emits JSON events for read_until_stp."""
        _enqueue_capture_arm(mock_serial, max_cycles=500)
        _enqueue_exchange(
            mock_serial,
            b'{"v":1,"ok":true,"type":"event","event":"done","reason":"stp","cycles":0,"addr":"8000"}',
        )

        api = _make_api(mock_serial, verbose=True)
        api.read_until_stp(max_cycles=500)

        captured = capsys.readouterr()
        events = _parse_ndjson(captured.err)
        assert events[0] == {"type": "call", "method": "read_until_stp", "max_cycles": 500}
        assert any(e["type"] == "ret" and e["method"] == "read_until_stp" for e in events)

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
        assert events[0] == {"type": "send", "payload": {"binary": True, "bytes": 4}}
        assert events[1] == {"type": "ack"}
        assert events[2] == {"type": "ack"}
        assert events[3] == {"type": "recv", "payload": {"ok": True, "v": 1}}

    def test_error_logs_verbose(self, mock_serial, capsys):
        """NACK is emitted as a JSON error event in verbose mode."""
        _enqueue_ack(mock_serial)
        _enqueue_nack(mock_serial)

        api = _make_api(mock_serial, verbose=True)
        with pytest.raises(HardwareAPIError, match="NACK"):
            api._send_frame(b"x")

        captured = capsys.readouterr()
        events = _parse_ndjson(captured.err)
        assert events[0] == {"type": "send", "payload": {"binary": True, "bytes": 1}}
        assert events[1] == {"type": "ack"}
        assert events[2] == {"type": "nack"}
        assert events[3] == {"type": "error", "error": "nack", "detail": "Pico responded with NACK"}


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


class TestHardwareAPIIntegration:
    @patch("romulan.hardware_api.serial.Serial")
    def test_context_manager(self, mock_serial_cls):
        mock_serial_cls.return_value = MagicMock()
        with HardwareAPI("/dev/ttyFAKE") as api:
            assert api._ser is not None
        mock_serial_cls.return_value.close.assert_called_once()
