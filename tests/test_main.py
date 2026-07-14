import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure the package is importable
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from romulan.build_rom import ROM_SIZE, ROM_BASE_ADDR, build_rom, parse_hex_file
from romulan.main import main, create_parser


@pytest.fixture
def sample_hex_file(tmp_path: Path) -> Path:
    """Create a valid hex dump file."""
    content = """\
0x0000   0x18   @ CLC
0x0001   0xA9   @ LDA 0x5
0x0002   0x05
0x0003   0x8D   @ STA $4000
0x0004   0x00
0x0005   0x40
0x0006   0x69   @ ADC 0x3
0x0007   0x03
0x0008   0x8D   @ STA $4000
0x0009   0x00
0x000A   0x40
0x000B   0x4C   @ JMP $8000
0x000C   0x00
0x000D   0x80
0x7FFC   0x00   @ Reset vector low
0x7FFD   0x80   @ Reset vector high
0x7FFE   0x00   @ IRQ vector low
0x7FFF   0x80   @ IRQ vector high
"""
    path = tmp_path / "program.txt"
    path.write_text(content, encoding="utf-8")
    return path


class TestParseHexFile:
    def test_valid_file(self, sample_hex_file: Path) -> None:
        data = parse_hex_file(sample_hex_file)
        assert data[0x8000] == 0x18  # CLC at CPU $8000
        assert data[0x8001] == 0xA9  # LDA at CPU $8001
        assert data[0xFFFC] == 0x00  # Reset vector low
        assert data[0xFFFD] == 0x80  # Reset vector high
        assert data[0xFFFE] == 0x00  # IRQ vector low
        assert data[0xFFFF] == 0x80  # IRQ vector high

    def test_out_of_range_address(self, tmp_path: Path) -> None:
        content = "0x8000   0xEA\n"
        path = tmp_path / "bad.txt"
        path.write_text(content, encoding="utf-8")
        with pytest.raises(ValueError, match="outside"):
            parse_hex_file(path)

    def test_invalid_line(self, tmp_path: Path) -> None:
        content = "this is not a valid line\n"
        path = tmp_path / "bad.txt"
        path.write_text(content, encoding="utf-8")
        with pytest.raises(ValueError, match="Cannot parse"):
            parse_hex_file(path)


class TestBuildRom:
    def test_builds_valid_rom(self, sample_hex_file: Path, tmp_path: Path) -> None:
        out = tmp_path / "rom.bin"
        build_rom(sample_hex_file, out)
        assert out.exists()
        data = out.read_bytes()
        assert len(data) == ROM_SIZE
        # Check vectors in the binary
        assert data[0x7FFC] == 0x00  # Reset vector low at file offset
        assert data[0x7FFD] == 0x80  # Reset vector high
        assert data[0x7FFE] == 0x00  # IRQ vector low
        assert data[0x7FFF] == 0x80  # IRQ vector high

    def test_missing_vectors(self, tmp_path: Path) -> None:
        content = "0x0000   0xEA\n"
        path = tmp_path / "no_vectors.txt"
        path.write_text(content, encoding="utf-8")
        out = tmp_path / "rom.bin"
        with pytest.raises(ValueError, match="missing required vectors"):
            build_rom(path, out)


class TestCli:
    def test_build_only(self, sample_hex_file: Path, tmp_path: Path) -> None:
        out = tmp_path / "rom.bin"
        with patch.object(sys, "argv", ["romulan", str(sample_hex_file), "--build", "-o", str(out)]):
            main()
        assert out.exists()
        data = out.read_bytes()
        assert len(data) == ROM_SIZE

    def test_upload_without_rom(self, tmp_path: Path) -> None:
        out = tmp_path / "rom.bin"
        with patch.object(sys, "argv", ["romulan", "--upload", "-o", str(out)]):
            with pytest.raises(SystemExit) as exc_info:
                main()
        assert exc_info.value.code == 1

    def test_build_upload(self, sample_hex_file: Path, tmp_path: Path) -> None:
        out = tmp_path / "rom.bin"
        mock_api = MagicMock()
        mock_api.__enter__ = MagicMock(return_value=mock_api)
        mock_api.__exit__ = MagicMock(return_value=False)
        mock_api.upload_rom.return_value = {
            "ok": True,
            "bytes": ROM_SIZE,
            "reset_vector": "8000",
            "expected": ROM_SIZE,
        }
        with patch("romulan.main.HardwareAPI", return_value=mock_api) as mock_hw:
            with patch("romulan.main.find_pico_port", return_value="/dev/ttyFAKE"):
                with patch.object(
                    sys, "argv", ["romulan", str(sample_hex_file), "--build", "--upload", "-o", str(out)]
                ):
                    main()
        assert out.exists()
        mock_hw.assert_called_once_with("/dev/ttyFAKE")
        mock_api.upload_rom.assert_called_once()
        assert len(mock_api.upload_rom.call_args[0][0]) == ROM_SIZE

    def test_build_with_custom_output(self, sample_hex_file: Path, tmp_path: Path) -> None:
        out = tmp_path / "custom" / "rom.bin"
        with patch.object(sys, "argv", ["romulan", str(sample_hex_file), "--build", "-o", str(out)]):
            main()
        assert out.exists()
        data = out.read_bytes()
        assert len(data) == ROM_SIZE

    def test_upload_with_custom_output(self, sample_hex_file: Path, tmp_path: Path) -> None:
        out = tmp_path / "custom" / "rom.bin"
        out.parent.mkdir(parents=True, exist_ok=True)
        build_rom(sample_hex_file, out)
        mock_api = MagicMock()
        mock_api.__enter__ = MagicMock(return_value=mock_api)
        mock_api.__exit__ = MagicMock(return_value=False)
        mock_api.upload_rom.return_value = {
            "ok": True,
            "bytes": ROM_SIZE,
            "reset_vector": "8000",
            "expected": ROM_SIZE,
        }
        with patch("romulan.main.HardwareAPI", return_value=mock_api) as mock_hw:
            with patch("romulan.main.find_pico_port", return_value="/dev/ttyFAKE"):
                with patch.object(sys, "argv", ["romulan", "--upload", "-o", str(out)]):
                    main()
        mock_hw.assert_called_once_with("/dev/ttyFAKE")
        mock_api.upload_rom.assert_called_once_with(out.read_bytes())

    def test_neither_flag(self) -> None:
        with patch.object(sys, "argv", ["romulan", "input.txt"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
        assert exc_info.value.code == 2  # argparse exits with 2 on error

    def test_build_no_input(self) -> None:
        with patch.object(sys, "argv", ["romulan", "--build"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
        assert exc_info.value.code == 2


class TestFindPicoPort:
    @patch("romulan.upload_rom.serial.tools.list_ports.comports")
    def test_single_pico(self, mock_comports: MagicMock) -> None:
        port = MagicMock()
        port.device = "/dev/ttyACM0"
        port.vid = 0x2E8A
        port.manufacturer = None
        mock_comports.return_value = [port]

        from romulan.upload_rom import find_pico_port
        assert find_pico_port() == "/dev/ttyACM0"

    @patch("romulan.upload_rom.serial.tools.list_ports.comports")
    def test_multiple_picos(self, mock_comports: MagicMock) -> None:
        p1 = MagicMock(device="/dev/ttyACM0", vid=0x2E8A, manufacturer=None)
        p2 = MagicMock(device="/dev/ttyACM1", vid=0x2E8A, manufacturer=None)
        mock_comports.return_value = [p1, p2]

        from romulan.upload_rom import find_pico_port
        with pytest.raises(RuntimeError, match="Multiple"):
            find_pico_port()

    @patch("romulan.upload_rom.serial.tools.list_ports.comports")
    def test_no_pico_found(self, mock_comports: MagicMock) -> None:
        mock_comports.return_value = []

        from romulan.upload_rom import find_pico_port
        with pytest.raises(RuntimeError, match="No Raspberry Pi Pico"):
            find_pico_port()


class TestHardwareVerbose:
    @patch("romulan.main.HardwareAPI")
    def test_verbose_flag_passed_to_api(self, mock_hw_cls: MagicMock) -> None:
        """The --verbose flag is forwarded to HardwareAPI."""
        mock_api = MagicMock()
        mock_api.__enter__ = MagicMock(return_value=mock_api)
        mock_api.__exit__ = MagicMock(return_value=False)
        mock_api.request_addr.return_value = 0x8000
        mock_hw_cls.return_value = mock_api

        with patch.object(
            sys,
            "argv",
            ["romulan", "hardware", "request-addr", "--port", "/dev/ttyFAKE", "--verbose"],
        ):
            main()

        mock_hw_cls.assert_called_once_with("/dev/ttyFAKE", timeout=30.0, verbose=True)

    @patch("romulan.main.HardwareAPI")
    def test_verbose_false_by_default(self, mock_hw_cls: MagicMock) -> None:
        """Without --verbose, HardwareAPI receives verbose=False."""
        mock_api = MagicMock()
        mock_api.__enter__ = MagicMock(return_value=mock_api)
        mock_api.__exit__ = MagicMock(return_value=False)
        mock_api.request_addr.return_value = 0x8000
        mock_hw_cls.return_value = mock_api

        with patch.object(
            sys,
            "argv",
            ["romulan", "hardware", "request-addr", "--port", "/dev/ttyFAKE"],
        ):
            main()

        mock_hw_cls.assert_called_once_with("/dev/ttyFAKE", timeout=30.0, verbose=False)

    @patch("romulan.main.HardwareAPI")
    def test_timeout_flag_passed_to_api(self, mock_hw_cls: MagicMock) -> None:
        """The --timeout flag is forwarded to HardwareAPI."""
        mock_api = MagicMock()
        mock_api.__enter__ = MagicMock(return_value=mock_api)
        mock_api.__exit__ = MagicMock(return_value=False)
        mock_api.request_addr.return_value = 0x8000
        mock_hw_cls.return_value = mock_api

        with patch.object(
            sys,
            "argv",
            ["romulan", "hardware", "request-addr", "--port", "/dev/ttyFAKE", "--timeout", "45"],
        ):
            main()

        mock_hw_cls.assert_called_once_with("/dev/ttyFAKE", timeout=45.0, verbose=False)

    @patch("romulan.main.HardwareAPI")
    def test_peek_hex_offset(self, mock_hw_cls: MagicMock) -> None:
        """hardware peek parses a hex offset and prints the returned bytes."""
        mock_api = MagicMock()
        mock_api.__enter__ = MagicMock(return_value=mock_api)
        mock_api.__exit__ = MagicMock(return_value=False)
        mock_api.peek.return_value = MagicMock(offset=0x7000, count=3, data=b"\xA9\xAA\x05")
        mock_hw_cls.return_value = mock_api

        with patch.object(
            sys,
            "argv",
            ["romulan", "hardware", "peek", "--port", "/dev/ttyFAKE", "--offset", "0x7000", "--count", "3"],
        ):
            main()

        mock_api.peek.assert_called_once_with(offset=0x7000, count=3)

    @patch("romulan.main.HardwareAPI")
    def test_peek_decimal_offset(self, mock_hw_cls: MagicMock) -> None:
        """hardware peek parses a decimal offset."""
        mock_api = MagicMock()
        mock_api.__enter__ = MagicMock(return_value=mock_api)
        mock_api.__exit__ = MagicMock(return_value=False)
        mock_api.peek.return_value = MagicMock(offset=28672, count=16, data=b"\xEA" * 16)
        mock_hw_cls.return_value = mock_api

        with patch.object(
            sys,
            "argv",
            ["romulan", "hardware", "peek", "--port", "/dev/ttyFAKE", "--offset", "28672"],
        ):
            main()

        mock_api.peek.assert_called_once_with(offset=28672, count=16)

    @patch("romulan.main.HardwareAPI")
    def test_clock(self, mock_hw_cls: MagicMock) -> None:
        """hardware clock calls set_clock with the requested Hz."""
        mock_api = MagicMock()
        mock_api.__enter__ = MagicMock(return_value=mock_api)
        mock_api.__exit__ = MagicMock(return_value=False)
        mock_hw_cls.return_value = mock_api

        with patch.object(
            sys,
            "argv",
            ["romulan", "hardware", "clock", "--port", "/dev/ttyFAKE", "--hz", "100"],
        ):
            main()

        mock_api.set_clock.assert_called_once_with(hz=100.0)

    @patch("romulan.main.HardwareAPI")
    def test_status(self, mock_hw_cls: MagicMock) -> None:
        """hardware status prints the firmware status snapshot."""
        mock_api = MagicMock()
        mock_api.__enter__ = MagicMock(return_value=mock_api)
        mock_api.__exit__ = MagicMock(return_value=False)
        mock_api.status.return_value = MagicMock(
            phi2_hz=1000.0,
            rom_active=True,
            reset_asserted=False,
            read_active=False,
            monitor_enabled=False,
            upload_active=False,
            last_addr="F000",
            last_data="4C",
            last_rw=0,
            resb=1,
            rwb=0,
            a15=1,
            phi2=0,
        )
        mock_hw_cls.return_value = mock_api

        with patch.object(
            sys,
            "argv",
            ["romulan", "hardware", "status", "--port", "/dev/ttyFAKE"],
        ):
            main()

        mock_api.status.assert_called_once_with()

    @patch("romulan.main.HardwareAPI")
    def test_drive_enable(self, mock_hw_cls: MagicMock) -> None:
        """hardware drive --value calls api.drive with the byte."""
        mock_api = MagicMock()
        mock_api.__enter__ = MagicMock(return_value=mock_api)
        mock_api.__exit__ = MagicMock(return_value=False)
        mock_api.drive.return_value = MagicMock(enabled=True, value="A5")
        mock_hw_cls.return_value = mock_api

        with patch.object(
            sys,
            "argv",
            ["romulan", "hardware", "drive", "--port", "/dev/ttyFAKE", "--value", "A5"],
        ):
            main()

        mock_api.drive.assert_called_once_with("A5")

    @patch("romulan.main.HardwareAPI")
    def test_drive_disable(self, mock_hw_cls: MagicMock) -> None:
        """hardware drive --disable calls api.drive(None)."""
        mock_api = MagicMock()
        mock_api.__enter__ = MagicMock(return_value=mock_api)
        mock_api.__exit__ = MagicMock(return_value=False)
        mock_api.drive.return_value = MagicMock(enabled=False, value="00")
        mock_hw_cls.return_value = mock_api

        with patch.object(
            sys,
            "argv",
            ["romulan", "hardware", "drive", "--port", "/dev/ttyFAKE", "--disable"],
        ):
            main()

        mock_api.drive.assert_called_once_with(None)
