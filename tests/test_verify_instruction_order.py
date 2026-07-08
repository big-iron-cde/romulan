"""Test cases for romulan.build_rom.verify_instruction_order."""

from romulan.build_rom import verify_instruction_order


# -----------------------------
# ORDER TESTS
# -----------------------------


def test_consecutive_instructions() -> None:
    """Ensures consecutive instructions with no gaps are accepted."""
    data = [0x0000, 0x0001, 0x0002, 0x0003]

    error_list = []
    verify_instruction_order(data=data, error_list=error_list)
    # Checks that the error list is empty, meaning no errors were found
    assert len(error_list) == 0


def test_skipped_instruction() -> None:
    """Ensures skipped instructions are flagged as errors."""
    data = [0x0000, 0x0002]

    error_list = []
    verify_instruction_order(data=data, error_list=error_list)
    # Checks that the error list contains one error, meaning one skipped instruction was found
    assert len(error_list) == 1


def test_stops_at_vector_addresses() -> None:
    """Ensures checking stops when a vector address is reached."""
    data = [0x0000, 0x0001, 0x7FFC, 0x7FFD]

    error_list = []
    verify_instruction_order(data=data, error_list=error_list)
    # Checks that the error list is empty, meaning no errors were found
    assert len(error_list) == 0


def test_multiple_skips() -> None:
    """Ensures multiple skipped instructions are all flagged."""
    data = [0x0000, 0x0002, 0x0003, 0x0005]

    error_list = []
    verify_instruction_order(data=data, error_list=error_list)
    # Checks that the error list contains two errors, meaning two skipped instructions were found
    assert len(error_list) == 2


def test_gap_before_vector() -> None:
    """Ensures gaps before vector addresses are still caught."""
    data = [0x0000, 0x0002, 0x7FFC]

    error_list = []
    verify_instruction_order(data=data, error_list=error_list)
    # Checks that the error list contains one error, meaning one skipped instruction was found
    assert len(error_list) == 1


def test_empty_data() -> None:
    """Ensures empty data produces no errors."""
    data = []

    error_list = []
    verify_instruction_order(data=data, error_list=error_list)
    # Checks that the error list is empty, meaning no errors were found
    assert len(error_list) == 0


def test_single_item() -> None:
    """Ensures single item produces no errors."""
    data = [0x0000]

    error_list = []
    verify_instruction_order(data=data, error_list=error_list)
    # Checks that the error list is empty, meaning no errors were found
    assert len(error_list) == 0
