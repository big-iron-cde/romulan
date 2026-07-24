import sys
from pathlib import Path

import pytest

# Ensure the package is importable
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from romulan.assemble import parse_asm_file
from romulan.build_rom import ROM_SIZE, build_rom, detect_format

REPO_ROOT = Path(__file__).parent.parent


def _write(tmp_path: Path, text: str, name: str = "program.s") -> Path:
    """Write source text to a temp file and return its path."""
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def _assemble(tmp_path: Path, text: str) -> dict[int, int]:
    """Assemble source text from a temp file."""
    return parse_asm_file(_write(tmp_path, text))


class TestAddressingModes:
    def test_implied(self, tmp_path: Path) -> None:
        assert _assemble(tmp_path, "CLC\n") == {0x8000: 0x18}

    def test_immediate(self, tmp_path: Path) -> None:
        data = _assemble(tmp_path, "LDA #$05\n")
        assert data == {0x8000: 0xA9, 0x8001: 0x05}

    def test_immediate_formats(self, tmp_path: Path) -> None:
        for text in ("LDA #$05\n", "LDA #0x05\n", "LDA #5\n"):
            data = _assemble(tmp_path, text)
            assert data[0x8001] == 0x05

    def test_zero_page(self, tmp_path: Path) -> None:
        data = _assemble(tmp_path, "LDA $10\n")
        assert data == {0x8000: 0xA5, 0x8001: 0x10}

    def test_absolute(self, tmp_path: Path) -> None:
        data = _assemble(tmp_path, "STA $4000\n")
        assert data == {0x8000: 0x8D, 0x8001: 0x00, 0x8002: 0x40}

    def test_absolute_decimal(self, tmp_path: Path) -> None:
        data = _assemble(tmp_path, "STA 16384\n")
        assert data == {0x8000: 0x8D, 0x8001: 0x00, 0x8002: 0x40}

    def test_absolute_small_address_no_zp_mode(self, tmp_path: Path) -> None:
        # JMP has no zero-page mode: $0000 encodes as a 16-bit absolute address
        data = _assemble(tmp_path, "JMP $0000\n")
        assert data == {0x8000: 0x4C, 0x8001: 0x00, 0x8002: 0x00}

    def test_zero_page_x(self, tmp_path: Path) -> None:
        data = _assemble(tmp_path, "LDA $10,X\n")
        assert data == {0x8000: 0xB5, 0x8001: 0x10}

    def test_absolute_x(self, tmp_path: Path) -> None:
        data = _assemble(tmp_path, "LDA $1234,X\n")
        assert data == {0x8000: 0xBD, 0x8001: 0x34, 0x8002: 0x12}

    def test_absolute_y(self, tmp_path: Path) -> None:
        data = _assemble(tmp_path, "STA $2000,Y\n")
        assert data == {0x8000: 0x99, 0x8001: 0x00, 0x8002: 0x20}

    def test_zero_page_y(self, tmp_path: Path) -> None:
        data = _assemble(tmp_path, "LDX $10,Y\n")
        assert data == {0x8000: 0xB6, 0x8001: 0x10}

    def test_indexed_mode_not_supported(self, tmp_path: Path) -> None:
        # LDX has no ,X indexed modes on the 6502
        with pytest.raises(ValueError, match="addressing mode"):
            _assemble(tmp_path, "LDX $10,X\n")

    def test_indirect_jmp(self, tmp_path: Path) -> None:
        data = _assemble(tmp_path, "JMP ($1234)\n")
        assert data == {0x8000: 0x6C, 0x8001: 0x34, 0x8002: 0x12}

    def test_indexed_indirect(self, tmp_path: Path) -> None:
        data = _assemble(tmp_path, "LDA ($10,X)\n")
        assert data == {0x8000: 0xA1, 0x8001: 0x10}

    def test_indirect_indexed(self, tmp_path: Path) -> None:
        data = _assemble(tmp_path, "LDA ($10),Y\n")
        assert data == {0x8000: 0xB1, 0x8001: 0x10}

    def test_zp_indirect_65c02(self, tmp_path: Path) -> None:
        data = _assemble(tmp_path, "LDA ($10)\n")
        assert data == {0x8000: 0xB2, 0x8001: 0x10}

    def test_accumulator(self, tmp_path: Path) -> None:
        assert _assemble(tmp_path, "ASL\n") == {0x8000: 0x0A}
        assert _assemble(tmp_path, "ASL A\n") == {0x8000: 0x0A}
        data = _assemble(tmp_path, "LSR $10\n")
        assert data == {0x8000: 0x46, 0x8001: 0x10}

    def test_case_insensitive(self, tmp_path: Path) -> None:
        assert _assemble(tmp_path, "lda #$05\n") == {0x8000: 0xA9, 0x8001: 0x05}


class Test65C02Extensions:
    def test_stp_wai(self, tmp_path: Path) -> None:
        assert _assemble(tmp_path, "STP\n") == {0x8000: 0xDB}
        assert _assemble(tmp_path, "WAI\n") == {0x8000: 0xCB}

    def test_stack(self, tmp_path: Path) -> None:
        data = _assemble(tmp_path, "PHX\nPHY\nPLX\nPLY\n")
        assert data == {0x8000: 0xDA, 0x8001: 0x5A, 0x8002: 0xFA, 0x8003: 0x7A}

    def test_stz(self, tmp_path: Path) -> None:
        assert _assemble(tmp_path, "STZ $10\n") == {0x8000: 0x64, 0x8001: 0x10}
        data = _assemble(tmp_path, "STZ $1234,X\n")
        assert data == {0x8000: 0x9E, 0x8001: 0x34, 0x8002: 0x12}

    def test_trb_tsb(self, tmp_path: Path) -> None:
        data = _assemble(tmp_path, "TRB $10\nTSB $1234\n")
        assert data == {
            0x8000: 0x14, 0x8001: 0x10,
            0x8002: 0x0C, 0x8003: 0x34, 0x8004: 0x12,
        }

    def test_inc_dec_accumulator(self, tmp_path: Path) -> None:
        assert _assemble(tmp_path, "INC A\n") == {0x8000: 0x1A}
        assert _assemble(tmp_path, "DEC A\n") == {0x8000: 0x3A}


class TestLabelsAndBranches:
    def test_forward_jump(self, tmp_path: Path) -> None:
        data = _assemble(tmp_path, "JMP later\nSTP\nlater:\nSTP\n")
        assert data == {
            0x8000: 0x4C, 0x8001: 0x04, 0x8002: 0x80,
            0x8003: 0xDB,
            0x8004: 0xDB,
        }

    def test_backward_branch(self, tmp_path: Path) -> None:
        data = _assemble(tmp_path, "loop:\nCLC\nBNE loop\n")
        assert data == {0x8000: 0x18, 0x8001: 0xD0, 0x8002: 0xFD}

    def test_forward_branch(self, tmp_path: Path) -> None:
        data = _assemble(tmp_path, "BEQ done\nCLC\ndone:\nSTP\n")
        assert data == {
            0x8000: 0xF0, 0x8001: 0x01,
            0x8002: 0x18,
            0x8003: 0xDB,
        }

    def test_branch_to_numeric_address(self, tmp_path: Path) -> None:
        data = _assemble(tmp_path, "BRA $8000\n")
        assert data == {0x8000: 0x80, 0x8001: 0xFE}

    def test_branch_out_of_range(self, tmp_path: Path) -> None:
        text = "loop:\n" + "NOP\n" * 200 + "BNE loop\n"
        with pytest.raises(ValueError, match="out of range"):
            _assemble(tmp_path, text)

    def test_duplicate_label(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="duplicate label"):
            _assemble(tmp_path, "loop:\nCLC\nloop:\nSEC\n")

    def test_undefined_label(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="undefined label"):
            _assemble(tmp_path, "JMP nowhere\n")

    def test_label_operand_is_absolute(self, tmp_path: Path) -> None:
        data = _assemble(tmp_path, "LDA target\nSTP\ntarget:\nSTP\n")
        assert data[0x8000] == 0xAD  # absolute LDA, not zero page
        assert data[0x8001] == 0x04  # target = $8004
        assert data[0x8002] == 0x80


class TestDirectives:
    def test_org(self, tmp_path: Path) -> None:
        data = _assemble(tmp_path, ".org $9000\nCLC\n")
        assert data == {0x9000: 0x18}

    def test_default_origin(self, tmp_path: Path) -> None:
        data = _assemble(tmp_path, "CLC\n")
        assert 0x8000 in data

    def test_org_outside_rom(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="outside the ROM region"):
            _assemble(tmp_path, ".org $1000\nCLC\n")

    def test_org_non_numeric(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match=".org requires a numeric address"):
            _assemble(tmp_path, ".org reset\n")

    def test_byte(self, tmp_path: Path) -> None:
        data = _assemble(tmp_path, ".byte $EA, 5, 0x18\n")
        assert data == {0x8000: 0xEA, 0x8001: 0x05, 0x8002: 0x18}

    def test_byte_range(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="does not fit in 8 bits"):
            _assemble(tmp_path, ".byte $1234\n")

    def test_word(self, tmp_path: Path) -> None:
        data = _assemble(tmp_path, ".word $8000\n")
        assert data == {0x8000: 0x00, 0x8001: 0x80}

    def test_word_label(self, tmp_path: Path) -> None:
        data = _assemble(tmp_path, "reset:\nCLC\n.word reset\n")
        assert data[0x8001] == 0x00
        assert data[0x8002] == 0x80

    def test_vectors(self, tmp_path: Path) -> None:
        text = "reset:\nCLC\n.org $FFFC\n.word reset\n.word reset\n"
        data = _assemble(tmp_path, text)
        assert data[0xFFFC] == 0x00
        assert data[0xFFFD] == 0x80
        assert data[0xFFFE] == 0x00
        assert data[0xFFFF] == 0x80

    def test_unknown_directive(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="unknown directive"):
            _assemble(tmp_path, ".text\n")

    def test_overlap_rejected(self, tmp_path: Path) -> None:
        text = ".org $8000\nCLC\n.org $8000\nSEC\n"
        with pytest.raises(ValueError, match="more than once"):
            _assemble(tmp_path, text)

    def test_emit_beyond_rom_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="outside the ROM region"):
            _assemble(tmp_path, ".org $FFFF\nCLC\nSEC\n")


class TestErrors:
    def test_unknown_mnemonic(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="unknown mnemonic"):
            _assemble(tmp_path, "FROB\n")

    def test_invalid_addressing_mode(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="addressing mode"):
            _assemble(tmp_path, "STA #$05\n")

    def test_immediate_out_of_range(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="does not fit in a byte"):
            _assemble(tmp_path, "LDA #$123\n")

    def test_indirect_zp_out_of_range(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="does not fit in a byte"):
            _assemble(tmp_path, "LDA ($1234,X)\n")

    def test_malformed_indirect(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="cannot parse operand"):
            _assemble(tmp_path, "LDA ($10\n")

    def test_bad_operand(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="cannot parse operand"):
            _assemble(tmp_path, "LDA #%\n")


class TestDetectFormat:
    def test_hex_file(self, tmp_path: Path) -> None:
        path = _write(tmp_path, "0x0000   0x18   @ CLC\n", name="program.s")
        assert detect_format(path) == "hex"

    def test_hex_with_leading_comments(self, tmp_path: Path) -> None:
        path = _write(
            tmp_path,
            "\n@ a hex dump comment\n0x0000   0x18\n",
            name="program.txt",
        )
        assert detect_format(path) == "hex"

    def test_asm_file(self, tmp_path: Path) -> None:
        path = _write(tmp_path, "; assembly\n.org $8000\nCLC\n", name="program.txt")
        assert detect_format(path) == "asm"

    def test_empty_file(self, tmp_path: Path) -> None:
        path = _write(tmp_path, "\n; only comments\n")
        with pytest.raises(ValueError, match="no data"):
            detect_format(path)


class TestBuildRomAsm:
    def _demo_asm(self, tmp_path: Path) -> Path:
        return _write(
            tmp_path,
            "reset:\n"
            "CLC\n"
            "LDA #$05\n"
            "STP\n"
            ".org $FFFC\n"
            ".word reset\n"
            ".word reset\n",
        )

    def test_builds_rom(self, tmp_path: Path) -> None:
        out = tmp_path / "rom.bin"
        build_rom(self._demo_asm(tmp_path), out)
        data = out.read_bytes()
        assert len(data) == ROM_SIZE
        assert data[0x0000] == 0x18  # CLC
        assert data[0x0001] == 0xA9  # LDA #$05
        assert data[0x0002] == 0x05
        assert data[0x0003] == 0xDB  # STP
        assert data[0x0100] == 0xEA  # NOP fill
        assert data[0x7FFC:] == bytes([0x00, 0x80, 0x00, 0x80])

    def test_asm_org_gaps_are_allowed(self, tmp_path: Path) -> None:
        # .org gaps would trip the hex path's contiguity check; asm allows them
        path = _write(
            tmp_path,
            "reset:\nCLC\n.org $9000\nSTP\n.org $FFFC\n.word reset\n.word reset\n",
        )
        out = tmp_path / "rom.bin"
        build_rom(path, out)
        data = out.read_bytes()
        assert data[0x0000] == 0x18
        assert data[0x1000] == 0xDB

    def test_asm_missing_vectors(self, tmp_path: Path) -> None:
        path = _write(tmp_path, "CLC\n")
        out = tmp_path / "rom.bin"
        with pytest.raises(ValueError, match="missing required vectors"):
            build_rom(path, out)

    def test_demo_s_matches_demo_txt(self, tmp_path: Path) -> None:
        hex_out = tmp_path / "rom_hex.bin"
        asm_out = tmp_path / "rom_asm.bin"
        build_rom(REPO_ROOT / "demo.txt", hex_out)
        build_rom(REPO_ROOT / "demo.s", asm_out)
        assert asm_out.read_bytes() == hex_out.read_bytes()
