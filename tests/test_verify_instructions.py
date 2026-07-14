"""Test cases for romulan.build-rom.verify_instructions."""

import pytest

from romulan.build_rom import verify_instructions


# -----------------------------
# RANGE TESTS
# -----------------------------


@pytest.mark.range
def test_out_of_range() -> None:
    """Ensures values outside 0x00–0xFF or invalid hex are rejected."""
    out_of_range_list = ["0xFZ", "0x100", "0x-01"]
    valid_hex_list = []

    for item in out_of_range_list:
        # Cleans up the hex code
        clean_hex = item.replace("0x", "")

        # Checks range for hex code
        if any(c not in "0123456789abcdefABCDEF" for c in clean_hex.strip("-")):
            # Python should raise ValueError before verify_instructions is called
            with pytest.raises(ValueError):
                int(clean_hex, 16)
        else:
            valid_hex_list.append(int(clean_hex, 16))

    error_list = []
    verify_instructions(data=valid_hex_list, error_list=error_list)
    assert len(error_list) == len(valid_hex_list)


@pytest.mark.range
def test_in_range() -> None:
    """Ensures valid single-byte opcodes are accepted."""
    in_range_list = [0xEA, 0x18, 0xDB]

    error_list = []
    verify_instructions(data=in_range_list, error_list=error_list)
    assert len(error_list) == 0


# -----------------------------
# OPCODE VALIDITY TESTS
# -----------------------------


@pytest.mark.validity
def test_invalid_opcodes() -> None:
    """Ensures invalid opcodes are flagged as errors."""
    invalid_opcodes_list = [0x63, 0xFC, 0x1B]

    # Checks all opcodes in the list, should produce an error for each invalid opcode
    error_list = []
    verify_instructions(data=invalid_opcodes_list, error_list=error_list)
    # Checks that the error list contains three errors, meaning three invalid opcodes were found
    assert len(error_list) == 3


def test_valid_opcodes() -> None:
    """Ensures valid opcode sequences are accepted."""
    # CLC; LDA #$05; NOP
    valid_opcodes_list = [0x18, 0xA9, 0x05, 0xEA]

    error_list = []
    verify_instructions(data=valid_opcodes_list, error_list=error_list)
    assert len(error_list) == 0


# -----------------------------
# MEMORY / IMMEDIATE TESTS
# -----------------------------


@pytest.mark.memory
def test_immediate_memory() -> None:
    """Ensures that opcodes become valid when used as a memory instruction."""
    immediate_list = [
        [0x09, 0x0B],
        [0xC0, 0xF4],
        [0x89, 0xAB],
    ]

    # Checks all lists of opcodes and memory instructions, should not raise an error
    for instruction_list in immediate_list:
        error_list = []
        verify_instructions(data=instruction_list, error_list=error_list)
        assert len(error_list) == 0


def test_addressing_mode() -> None:
    """Ensures operand bytes that look like invalid opcodes are not flagged."""
    addressing_list = [
        [0x8D, 0x02, 0x03],  # STA $0302
        [0x8E, 0x73, 0xC2],  # STX $C273
        [0xAC, 0x5C, 0xEB],  # LDY $EB5C
    ]

    for address_list in addressing_list:
        error_list = []
        verify_instructions(data=address_list, error_list=error_list)
        assert len(error_list) == 0


def test_leading_invalid_not_excused_by_later_opcode() -> None:
    """Regression: first-byte invalid must not use Python negative indexing."""
    error_list = []
    verify_instructions(data=[0x02, 0xA9], error_list=error_list)
    assert len(error_list) == 1
    assert "02" in error_list[0]
