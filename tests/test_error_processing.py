"""Test cases for romulan.build_rom.error_processing."""

import pytest

from romulan.build_rom import error_processing, ERROR_COUNTER


# -----------------------------
# VALID DATA TESTS
# -----------------------------


def test_valid_data_no_errors() -> None:
    """Ensures valid data produces no errors."""
    data_dict = {
        0x8000: 0xA9,
        0x8001: 0x05,
        0x8002: 0x8D,
    }

    error_list = error_processing(data_dict)
    assert len(error_list) == 0


# -----------------------------
# INVALID INSTRUCTION TESTS
# -----------------------------


def test_invalid_instruction_error() -> None:
    """Ensures invalid instructions are detected."""
    data_dict = {
        0x8000: 0xEA,
        0x8001: 0x43,
    }

    error_list = error_processing(data_dict)
    assert len(error_list) == 1
    assert "Invalid instruction" in error_list[0]


def test_multiple_invalid_instructions() -> None:
    """Ensures multiple invalid instructions are all detected."""
    data_dict = {
        0x8000: 0x02,
        0x8001: 0x03,
        0x8002: 0x0B,
    }

    error_list = error_processing(data_dict)
    assert len(error_list) == 3
    for error in error_list:
        assert "Invalid instruction" in error


# -----------------------------
# SKIPPED ADDRESS TESTS
# -----------------------------


def test_skipped_address_error() -> None:
    """Ensures skipped addresses are detected."""
    data_dict = {
        0x8000: 0xA9,
        0x8002: 0x05,
    }

    error_list = error_processing(data_dict)
    assert len(error_list) == 1
    assert "Skipped instruction" in error_list[0]


def test_multiple_skipped_addresses() -> None:
    """Ensures multiple skipped addresses are all detected."""
    data_dict = {
        0x8000: 0xA9,
        0x8002: 0x05,
        0x8003: 0x8D,
        0x8005: 0x00,
    }

    error_list = error_processing(data_dict)
    assert len(error_list) == 2
    for error in error_list:
        assert "Skipped instruction" in error


# -----------------------------
# COMBINED ERROR TESTS
# -----------------------------


def test_invalid_and_skipped() -> None:
    """Ensures both invalid instructions and skipped addresses are detected."""
    data_dict = {
        0x8000: 0x02,
        0x8002: 0x05,
    }

    error_list = error_processing(data_dict)
    assert len(error_list) == 2


# -----------------------------
# EDGE CASE TESTS
# -----------------------------


def test_empty_dict() -> None:
    """Ensures empty dict produces no errors."""
    data_dict = {}

    error_list = error_processing(data_dict)
    assert len(error_list) == 0


def test_single_entry() -> None:
    """Ensures single entry produces no errors."""
    data_dict = {0x8000: 0xA9}

    error_list = error_processing(data_dict)
    assert len(error_list) == 0


def test_vector_addresses_stop_checking() -> None:
    """Ensures vector addresses stop order checking."""
    data_dict = {
        0x8000: 0xA9,
        0xFFFC: 0x00,
        0xFFFD: 0x80,
    }

    error_list = error_processing(data_dict)
    assert len(error_list) == 0


def test_gap_before_vector() -> None:
    """Ensures gaps before vector addresses are still caught."""
    data_dict = {
        0x8000: 0xA9,
        0x8002: 0x05,
        0xFFFC: 0x00,
    }

    error_list = error_processing(data_dict)
    assert len(error_list) == 1
    assert "Skipped instruction" in error_list[0]


def test_out_of_range_value() -> None:
    """Ensures out of range values are detected."""
    data_dict = {
        0x8000: 0x100,
    }

    error_list = error_processing(data_dict)
    assert len(error_list) == 1
    assert "out of range" in error_list[0]
