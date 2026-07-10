"""Test cases for custom Skipped Instruction Error exception class."""


import pytest

from romulan.build_rom import SkippedInstructionError


def test_skipped_instruction_error_is_exception() -> None:
    """Checks that the custom error behaves like a normal Exception."""
    # Checks that raising the error works by using a dummy string
    with pytest.raises(SkippedInstructionError):
        raise SkippedInstructionError("Bad opcode")


def test_skipped_instruction_error_message_stored() -> None:
    """Makes sure the message passed to the exception is accessible."""
    # Checks the message is accessible using an assertion
    err = SkippedInstructionError("Skipped instruction")
    assert str(err) == "Skipped instruction"


def test_skipped_instruction_error_str_representation() -> None:
    """Validates that __str__ returns the message exactly."""
    # Checks the message returned by the string method using an assertion
    message = "Opcode 0xFF is not allowed"
    err = SkippedInstructionError(message)
    assert str(err) == message


def test_skipped_instruction_error_inherits_exception() -> None:
    """Ensure the class is a subclass of Exception."""
    # Checks the inheritance of the class from the Exception class using an assertion
    assert issubclass(SkippedInstructionError, Exception)