"""Tests for the hardware_api framed serial protocol."""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure the package is importable
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from romulan.hardware_api import (
    ACK,
    EOT,
    ENQ,
    NACK,
    STX,
    CaptureResult,
    HardwareAPI,
    HardwareAPIError,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_serial():
    """Return a mock serial object and a helper to enqueue Pico replies."""
    ser = MagicMock()
    # Simulate the read buffer: a list of bytes objects to be returned
    # by successive read(1) calls.
    ser._read_buffer = []

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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _enqueue_response(mock_serial, response_payload: bytes):
    """Enqueue a framed response that the Pico would send back.

    The Pico sends:
        ENQ STX <payload> EOT
    This helper puts those bytes into the mock serial buffer.
    """
    frame = ENQ + STX + response_payload + EOT
    # Extend the buffer with individual bytes
    for byte in frame:
        mock_serial._read_buffer.append(bytes([byte]))


def _enqueue_transaction_acks(mock_serial):
    """Enqueue the two ACKs the Pico sends during a host-initiated frame."""
    _enqueue_ack(mock_serial)  # receiver ready
    _enqueue_ack(mock_serial)  # transaction accepted


def _enqueue_ack(mock_serial):
    """Enqueue a single ACK byte."""
    mock_serial._read_buffer.append(ACK)


def _enqueue_nack(mock_serial):
    """Enqueue a single NACK byte."""
    mock_serial._read_buffer.append(NACK)


def _enqueue_stray_bytes(mock_serial, data: bytes):
    """Enqueue raw bytes to simulate monitor noise or echoes."""
    for byte in data:
        mock_serial._read_buffer.append(bytes([byte]))


# ---------------------------------------------------------------------------
# Resync
# ---------------------------------------------------------------------------

class TestResync:
    def test_discards_stray_until_enq(self, mock_serial):
        """Stray bytes (monitor lines, echoes) are discarded until ENQ."""
        _enqueue_stray_bytes(mock_serial, b"| 0x8000 0x18 read\n\x06")
        _enqueue_stray_bytes(mock_serial, ENQ)

        api = _make_api(mock_serial)
        api._resync()
        assert mock_serial._read_buffer == []

    def test_timeout_when_no_enq(self, mock_serial):
        """TimeoutError is raised if ENQ never appears."""
        # Make read() return empty forever → timeout
        mock_serial._read_buffer = []
        mock_serial.read.side_effect = lambda n=b"": b""

        api = _make_api(mock_serial)
        api.timeout = 0.01
        with pytest.raises(TimeoutError, match="resyncing"):
            api._resync()


# ---------------------------------------------------------------------------
# Send frame
# ---------------------------------------------------------------------------

class TestSendFrame:
    def test_basic_json_roundtrip(self, mock_serial):
        """A JSON payload is framed and the JSON response is returned."""
        request = b'{"cmd":"request_addr"}'
        response = b'{"addr":0x8000}'

        # Pico: ACK (receiver ready) → ACK (accepted) → response frame
        _enqueue_transaction_acks(mock_serial)
        _enqueue_response(mock_serial, response)

        api = _make_api(mock_serial)
        result = api._send_frame(request)

        assert result == response

        # Verify writes: ENQ STX ... request ... EOT
        writes = mock_serial.write.call_args_list
        assert writes[0][0][0] == ENQ
        assert writes[1][0][0] == STX
        assert writes[2][0][0] == request
        assert writes[3][0][0] == EOT

    def test_nack_raises(self, mock_serial):
        """NACK after EOT raises HardwareAPIError."""
        request = b'{"cmd":"reset","value":0}'

        _enqueue_ack(mock_serial)  # receiver ready
        _enqueue_nack(mock_serial)  # transaction rejected

        api = _make_api(mock_serial)
        with pytest.raises(HardwareAPIError, match="NACK"):
            api._send_frame(request)

    def test_resyncs_with_stray_bytes(self, mock_serial):
        """Stray bytes before the response frame are discarded."""
        request = b'{"cmd":"monitor","enable":false}'
        response = b'{"ok":true}'

        _enqueue_ack(mock_serial)  # receiver ready
        _enqueue_ack(mock_serial)  # accepted
        # Some garbage before the response frame ENQ
        _enqueue_stray_bytes(mock_serial, b"noise\n")
        _enqueue_response(mock_serial, response)

        api = _make_api(mock_serial)
        result = api._send_frame(request)
        assert result == response

    def test_timeout_waiting_for_ack(self, mock_serial):
        """Timeout waiting for ACK after STX."""
        mock_serial.read.side_effect = lambda n=1: b""
        mock_serial._read_buffer = []

        api = _make_api(mock_serial)
        api.timeout = 0.01
        with pytest.raises(TimeoutError, match="timed out waiting"):
            api._send_frame(b"x")


# ---------------------------------------------------------------------------
# Send JSON
# ---------------------------------------------------------------------------

class TestSendJson:
    def test_request_addr(self, mock_serial):
        """request_addr command parses the addr response."""
        _enqueue_transaction_acks(mock_serial)
        _enqueue_response(mock_serial, b'{"addr":32768}')

        api = _make_api(mock_serial)
        addr = api.request_addr()
        assert addr == 32768

    def test_request_addr_missing_key(self, mock_serial):
        """Missing 'addr' in response raises HardwareAPIError."""
        _enqueue_transaction_acks(mock_serial)
        _enqueue_response(mock_serial, b'{"error":"bad"}')

        api = _make_api(mock_serial)
        with pytest.raises(HardwareAPIError, match="Missing 'addr'"):
            api.request_addr()

    def test_reset_assert(self, mock_serial):
        """reset(assert_reset=True) sends value=0."""
        _enqueue_transaction_acks(mock_serial)
        _enqueue_response(mock_serial, b'{"ok":true}')

        api = _make_api(mock_serial)
        api.reset(assert_reset=True)

        # Inspect the payload that was sent
        payload = mock_serial.write.call_args_list[2][0][0]
        assert json.loads(payload) == {"cmd": "reset", "value": 0}

    def test_reset_release(self, mock_serial):
        """reset(assert_reset=False) sends value=1."""
        _enqueue_transaction_acks(mock_serial)
        _enqueue_response(mock_serial, b'{"ok":true}')

        api = _make_api(mock_serial)
        api.reset(assert_reset=False)

        payload = mock_serial.write.call_args_list[2][0][0]
        assert json.loads(payload) == {"cmd": "reset", "value": 1}

    def test_monitor_disable(self, mock_serial):
        """monitor(enable=False) sends the correct JSON."""
        _enqueue_transaction_acks(mock_serial)
        _enqueue_response(mock_serial, b'{"ok":true}')

        api = _make_api(mock_serial)
        api.monitor(enable=False)

        payload = mock_serial.write.call_args_list[2][0][0]
        assert json.loads(payload) == {"cmd": "monitor", "enable": False}

    def test_monitor_enable(self, mock_serial):
        """monitor(enable=True) sends the correct JSON."""
        _enqueue_transaction_acks(mock_serial)
        _enqueue_response(mock_serial, b'{"ok":true}')

        api = _make_api(mock_serial)
        api.monitor(enable=True)

        payload = mock_serial.write.call_args_list[2][0][0]
        assert json.loads(payload) == {"cmd": "monitor", "enable": True}


# ---------------------------------------------------------------------------
# Upload ROM
# ---------------------------------------------------------------------------

class TestUploadRom:
    def test_upload_32kb(self, mock_serial):
        """upload_rom sends the correct two-frame sequence."""
        rom = b"\xEA" * 0x8000

        # First frame: disable monitor
        _enqueue_transaction_acks(mock_serial)
        _enqueue_response(mock_serial, b'{"ok":true}')

        # Second frame: command
        _enqueue_transaction_acks(mock_serial)
        _enqueue_response(mock_serial, b'{"ok":true}')

        # Third frame: binary
        _enqueue_transaction_acks(mock_serial)
        _enqueue_response(mock_serial, b'{"loaded":32768}')

        api = _make_api(mock_serial)
        result = api.upload_rom(rom)
        assert result == {"loaded": 32768}

    def test_upload_wrong_size(self, mock_serial):
        """upload_rom rejects non-32KB data."""
        api = _make_api(mock_serial)
        with pytest.raises(ValueError, match="exactly 32768"):
            api.upload_rom(b"\x00\x01")


# ---------------------------------------------------------------------------
# Read until STP
# ---------------------------------------------------------------------------

class TestReadUntilStp:
    def test_capture_result(self, mock_serial):
        """read_until_stp returns a CaptureResult with cycles."""
        # First frame: disable monitor
        _enqueue_transaction_acks(mock_serial)
        _enqueue_response(mock_serial, b'{"ok":true}')

        # Second frame: read_until_stp
        _enqueue_transaction_acks(mock_serial)
        _enqueue_response(
            mock_serial,
            b'{"reason":"stp","cycles":[{"addr":32768,"data":24,"rw":"read"}]}',
        )

        api = _make_api(mock_serial)
        result = api.read_until_stp(max_cycles=500)
        assert isinstance(result, CaptureResult)
        assert result.reason == "stp"
        assert len(result.cycles) == 1
        assert result.cycles[0]["addr"] == 0x8000

    def test_invalid_cycles_type(self, mock_serial):
        """If 'cycles' is not a list, HardwareAPIError is raised."""
        # First frame: disable monitor
        _enqueue_transaction_acks(mock_serial)
        _enqueue_response(mock_serial, b'{"ok":true}')

        # Second frame: read_until_stp
        _enqueue_transaction_acks(mock_serial)
        _enqueue_response(mock_serial, b'{"reason":"stp","cycles":"bad"}')

        api = _make_api(mock_serial)
        with pytest.raises(HardwareAPIError, match="Expected 'cycles' list"):
            api.read_until_stp(max_cycles=500)


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
        """verbose=True prints CALL, SEND, RECV, RET for request_addr."""
        _enqueue_transaction_acks(mock_serial)
        _enqueue_response(mock_serial, b'{"addr":32768}')

        api = _make_api(mock_serial, verbose=True)
        addr = api.request_addr()
        assert addr == 32768

        captured = capsys.readouterr()
        assert "[HW] CALL request_addr()" in captured.err
        assert "[HW] SEND: " in captured.err
        assert "[HW] RECV: " in captured.err
        assert "[HW] RET request_addr -> 32768" in captured.err

    def test_reset_verbose(self, mock_serial, capsys):
        """verbose=True prints CALL and RET for reset."""
        _enqueue_transaction_acks(mock_serial)
        _enqueue_response(mock_serial, b'{"ok":true}')

        api = _make_api(mock_serial, verbose=True)
        api.reset(assert_reset=True)

        captured = capsys.readouterr()
        assert "[HW] CALL reset(assert_reset=True)" in captured.err
        assert "[HW] RET reset" in captured.err

    def test_monitor_verbose(self, mock_serial, capsys):
        """verbose=True prints CALL and RET for monitor."""
        _enqueue_transaction_acks(mock_serial)
        _enqueue_response(mock_serial, b'{"ok":true}')

        api = _make_api(mock_serial, verbose=True)
        api.monitor(enable=False)

        captured = capsys.readouterr()
        assert "[HW] CALL monitor(enable=False)" in captured.err
        assert "[HW] RET monitor" in captured.err

    def test_upload_rom_verbose(self, mock_serial, capsys):
        """verbose=True prints CALL, nested monitor, binary SEND/RECV, RET."""
        rom = b"\xEA" * 0x8000

        # First frame: disable monitor
        _enqueue_transaction_acks(mock_serial)
        _enqueue_response(mock_serial, b'{"ok":true}')

        # Second frame: command
        _enqueue_transaction_acks(mock_serial)
        _enqueue_response(mock_serial, b'{"ok":true}')

        # Third frame: binary
        _enqueue_transaction_acks(mock_serial)
        _enqueue_response(mock_serial, b'{"loaded":32768}')

        api = _make_api(mock_serial, verbose=True)
        result = api.upload_rom(rom)
        assert result == {"loaded": 32768}

        captured = capsys.readouterr()
        assert "[HW] CALL upload_rom(size=32768)" in captured.err
        assert "[HW] CALL monitor(enable=False)" in captured.err
        assert "[HW] SEND: <binary, 32768 bytes>" in captured.err
        assert "[HW] RECV: {\"loaded\":32768}" in captured.err
        assert "[HW] RET upload_rom -> {'loaded': 32768}" in captured.err

    def test_read_until_stp_verbose(self, mock_serial, capsys):
        """verbose=True prints CALL, SEND, RECV, RET for read_until_stp."""
        # First frame: disable monitor
        _enqueue_transaction_acks(mock_serial)
        _enqueue_response(mock_serial, b'{"ok":true}')

        # Second frame: read_until_stp
        _enqueue_transaction_acks(mock_serial)
        _enqueue_response(
            mock_serial,
            b'{"reason":"stp","cycles":[{"addr":32768,"data":24,"rw":"read"}]}',
        )

        api = _make_api(mock_serial, verbose=True)
        result = api.read_until_stp(max_cycles=500)

        captured = capsys.readouterr()
        assert "[HW] CALL read_until_stp(max_cycles=500)" in captured.err
        assert "[HW] RET read_until_stp ->" in captured.err

    def test_verbose_false_silent(self, mock_serial, capsys):
        """verbose=False produces no [HW] output."""
        _enqueue_transaction_acks(mock_serial)
        _enqueue_response(mock_serial, b'{"addr":32768}')

        api = _make_api(mock_serial, verbose=False)
        api.request_addr()

        captured = capsys.readouterr()
        assert "[HW]" not in captured.err

    def test_binary_payload_preview(self, mock_serial, capsys):
        """Binary payloads are previewed as <binary, N bytes>."""
        _enqueue_transaction_acks(mock_serial)
        _enqueue_response(mock_serial, b'{"ok":true}')

        api = _make_api(mock_serial, verbose=True)
        api._send_frame(b"\x80\x81\x82\x83")

        captured = capsys.readouterr()
        assert "[HW] SEND: <binary, 4 bytes>" in captured.err

    def test_error_logs_verbose(self, mock_serial, capsys):
        """Timeouts and NACK are logged in verbose mode."""
        _enqueue_ack(mock_serial)  # receiver ready
        _enqueue_nack(mock_serial)  # transaction rejected

        api = _make_api(mock_serial, verbose=True)
        with pytest.raises(HardwareAPIError, match="NACK"):
            api._send_frame(b"x")

        captured = capsys.readouterr()
        assert "[HW] ERROR: Pico responded with NACK" in captured.err


# ---------------------------------------------------------------------------
# Integration / smoke
# ---------------------------------------------------------------------------

class TestHardwareAPIIntegration:
    @patch("romulan.hardware_api.serial.Serial")
    def test_context_manager(self, mock_serial_cls):
        """HardwareAPI works as a context manager."""
        mock_serial_cls.return_value = MagicMock()
        with HardwareAPI("/dev/ttyFAKE") as api:
            assert api._ser is not None
        mock_serial_cls.return_value.close.assert_called_once()

    @patch("romulan.hardware_api.serial.Serial")
    def test_close_idempotent(self, mock_serial_cls):
        """close() is safe to call multiple times."""
        mock_serial_cls.return_value = MagicMock()
        api = HardwareAPI("/dev/ttyFAKE")
        api.close()
        assert api._ser is None
        api.close()  # should not raise

    def test_invalid_json_response(self, mock_serial):
        """Garbage JSON response raises HardwareAPIError."""
        _enqueue_transaction_acks(mock_serial)
        _enqueue_response(mock_serial, b"not-json")

        api = _make_api(mock_serial)
        with pytest.raises(HardwareAPIError, match="Invalid JSON"):
            api.request_addr()
