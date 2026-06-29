"""Test cases for custom Invalid Instruction Error exception class."""


import pytest

from romulan.src.romulan.build_rom import InvalidInstructionError


def test_invalid_instruction_error_is_exception() -> None:
    """Checks that the custom error behaves like a normal Exception."""
    # Checks that raising the error works by using a dummy string
    with pytest.raises(InvalidInstructionError):
        raise InvalidInstructionError("Bad opcode")


def test_invalid_instruction_error_message_stored() -> None:
    """Makes sure the message passed to the exception is accessible."""
    # Checks the message is accessible using an assertion
    err = InvalidInstructionError("Invalid instruction")
    assert str(err) == "Invalid instruction"


def test_invalid_instruction_error_str_representation() -> None:
    """Validates that __str__ returns the message exactly."""
    # Checks the message returned by the string method using an assertion
    message = "Opcode 0xFF is not allowed"
    err = InvalidInstructionError(message)
    assert str(err) == message


def test_invalid_instruction_error_inherits_exception() -> None:
    """Ensure the class is a subclass of Exception."""
    # Checks the inheritance of the class from the Exception class using an assertion
    assert issubclass(InvalidInstructionError, Exception)
