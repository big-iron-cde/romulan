"""Tests for the hardware_api framed serial protocol (v1 JSON)."""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from romulan.hardware_api import CaptureResult, HardwareAPI
from romulan.protocol_v1 import CHUNK_RAW_MAX, ROM_SIZE, build_request

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


def _make_api(mock_serial):
    api = HardwareAPI.__new__(HardwareAPI)
    api._ser = mock_serial
    api.port = "/dev/ttyFAKE"
    api.baudrate = 115200
    api.timeout = 3.0
    return api


def _enqueue_response(mock_serial, response_payload: bytes):
    frame = ENQ + STX + response_payload + EOT
    for byte in frame:
        mock_serial._read_buffer.append(bytes([byte]))


def _enqueue_transaction_acks(mock_serial):
    mock_serial._read_buffer.append(ACK)
    mock_serial._read_buffer.append(ACK)


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
        _enqueue_transaction_acks(mock_serial)
        _enqueue_response(mock_serial, b'{"v":1,"ok":true,"cmd":"monitor","enable":false}')

        _enqueue_transaction_acks(mock_serial)
        _enqueue_response(
            mock_serial,
            b'{"v":1,"ok":true,"cmd":"read","until":"stp","max_cycles":500}',
        )

        _enqueue_response(
            mock_serial,
            b'{"v":1,"type":"event","event":"cycle","seq":1,"addr":"8000","data":"18","rw":0}',
        )
        _enqueue_response(
            mock_serial,
            b'{"v":1,"type":"event","event":"done","ok":true,"reason":"stp","cycles":1,"addr":"8001"}',
        )

        api = _make_api(mock_serial)
        result = api.read_until_stp(max_cycles=500)
        assert isinstance(result, CaptureResult)
        assert result.reason == "stp"
        assert len(result.cycles) == 1


class TestHardwareAPIIntegration:
    @patch("romulan.hardware_api.serial.Serial")
    def test_context_manager(self, mock_serial_cls):
        mock_serial_cls.return_value = MagicMock()
        with HardwareAPI("/dev/ttyFAKE") as api:
            assert api._ser is not None
        mock_serial_cls.return_value.close.assert_called_once()
