"""Build a 32 KB ROM image for the Pico-as-ROM 65C02 system.

The 32 KB image maps to CPU addresses $8000-$FFFF.
File offset $0000 = CPU address $8000
File offset $7FFC = CPU address $FFFC (reset vector low byte)
File offset $7FFF = CPU address $FFFF
"""

import os
import re
import sys
from pathlib import Path
from typing import Dict, List

ROM_SIZE = 0x8000  # 32 KB
ROM_BASE_ADDR = 0x8000  # ROM starts at CPU address $8000
ERROR_COUNTER = 0  # Global counter for errors encountered during ROM build


class InvalidInstructionError(Exception):
    """Raised when a byte sequence contains an undefined 65C02 opcode."""

    def __init__(self, message):
        """Initialize the error with a human-readable message.

        Args:
            message: Description of the invalid instruction or operand.
        """
        super().__init__(message)
        self.message = message

    def __str__(self):
        """Return the error message.

        Returns:
            The message passed to the constructor.
        """
        return self.message


class SkippedInstructionError(Exception):
    """Raised when a required instruction address is missing from the ROM dump."""

    def __init__(self, message):
        """Initialize the error with a human-readable message.

        Args:
            message: Description of the skipped instruction address.
        """
        super().__init__(message)
        self.message = message

    def __str__(self):
        """Return the error message.

        Returns:
            The message passed to the constructor.
        """
        return self.message


def cpu_to_offset(cpu_addr: int) -> int:
    """Convert a CPU address ($8000-$FFFF) to a file offset (0-$7FFF).

    Args:
        cpu_addr: A 65C02 address in the ROM region.

    Returns:
        The corresponding byte offset in a 32 KB ROM file.

    Raises:
        ValueError: If ``cpu_addr`` is outside ``$8000``–``$FFFF``.
    """
    if not (ROM_BASE_ADDR <= cpu_addr <= 0xFFFF):
        raise ValueError(
            f"CPU address ${cpu_addr:04X} is outside the ROM region "
            f"(${ROM_BASE_ADDR:04X}-$FFFF)"
        )
    return cpu_addr - ROM_BASE_ADDR


def parse_hex_file(path: Path) -> dict[int, int]:
    """Parse an annotated hex dump file into a dict of CPU address -> byte.

    Expected line format::

        0x0000   0x18   @ CLC
        0x0001   0xA9   @ LDA 0x5

    File addresses are in the range ``0x0000``–``0x7FFF`` and are mapped to
    CPU addresses by adding :data:`ROM_BASE_ADDR` (``0x8000``). Everything
    after ``@`` on a line is treated as a comment and ignored.

    Args:
        path: Path to the annotated hex dump file.

    Returns:
        A mapping from CPU address to byte value.

    Raises:
        ValueError: If a line cannot be parsed or an address is out of range.
    """
    data: dict[int, int] = {}
    line_pattern = re.compile(r"^\s*0x([0-9A-Fa-f]+)\s+0x([0-9A-Fa-f]{2})")

    with open(path, "r", encoding="utf-8") as fh:
        for line_num, line in enumerate(fh, start=1):
            line = line.split("@")[0].strip()  # strip comments
            if not line:
                continue

            match = line_pattern.match(line)
            if not match:
                raise ValueError(f"Cannot parse line {line_num}: {line.strip()!r}")

            file_addr = int(match.group(1), 16)
            byte_val = int(match.group(2), 16)

            if not (0 <= file_addr < ROM_SIZE):
                raise ValueError(
                    f"Line {line_num}: file address 0x{file_addr:04X} is outside "
                    f"the valid range 0x0000-0x{ROM_SIZE - 1:04X}"
                )

            cpu_addr = file_addr + ROM_BASE_ADDR
            data[cpu_addr] = byte_val

    return data


def verify_instructions(data: List[int], error_list: List) -> None:
    """Validate opcode bytes and append errors instead of raising immediately.

    Distinguishes undefined opcodes from immediate or address operands that
    follow a valid instruction byte. Errors are appended to ``error_list`` and
    :data:`ERROR_COUNTER` is incremented.

    Args:
        data: Byte values parsed from the ROM image.
        error_list: List that receives human-readable error messages.
    """
    invalid_instructions = [0x02, 0x03, 0x0B, 0x13, 0x1B, 0x22, 0x23, 0x2B, 0x33,
                            0x3B, 0x42, 0x43, 0x44, 0x4B, 0x53, 0x54, 0x5B,0x5C,
                            0x62, 0x63, 0x6B, 0x73, 0x7B, 0x82, 0x83, 0x8B, 0x93,
                            0x9B, 0xA3, 0xAB, 0xB3, 0xBB, 0xC2, 0xC3, 0xD3, 0xD4,
                            0xDC, 0xE2, 0xE3, 0xEB, 0xF3, 0xF4, 0xFB, 0xFC]
    instructions_with_immediate = [0x09, 0x29, 0x49, 0x69, 0x89, 0xA0, 0xA2, 0xA9, 0xC0,
                                   0xC9, 0xE0, 0xE9]
    instructions_with_address = [0x00, 0x0C, 0x0D, 0x0E, 0x19, 0x1C, 0x1D, 0x1E, 0x20,
                                 0x2A, 0x2C, 0x2D, 0x2E, 0x39, 0x3C, 0x3D, 0x3E, 0x4C,
                                 0x4D, 0x4E, 0x59, 0x5D, 0x5E, 0x6C, 0x6D, 0x6E, 0x79,
                                 0x7C, 0x7D, 0x7E, 0x8C, 0x8D, 0x8E, 0x99, 0x9C, 0x9D,
                                 0x9E, 0xAC, 0xAD, 0xAE, 0xBC, 0xBD, 0xBE, 0xCC, 0xCD,
                                 0xCE, 0xD9, 0xDD, 0xDE, 0xEC, 0xED, 0xEE, 0xF9, 0xFD,
                                 0xFE]

    global ERROR_COUNTER

    for d in data:
        if 0x00 <= d <= 0xFF:
            if d in invalid_instructions:
                position = data.index(d)
                if (
                    data[position - 1] in instructions_with_immediate
                    or data[position - 1] in instructions_with_address
                ):
                    continue
                elif (
                    position - 2 >= 0
                    and data[position - 2] in instructions_with_address
                ):
                    continue
                else:
                    error_list.append(f"Invalid instruction: ${d} is undefined")
                    ERROR_COUNTER += 1
        else:
            error_list.append(
                f"Invalid instruction: ${d} is out of range (0x00 - 0xFF)"
            )
            ERROR_COUNTER += 1


def verify_instruction_order(data: List[int], error_list: List) -> None:
    """Check that file addresses are contiguous (no gaps before the vectors).

    Args:
        data: File-offset addresses from the parsed ROM dump.
        error_list: List that receives human-readable error messages.
    """
    sorted_data = sorted(data)
    global ERROR_COUNTER
    for i in range(len(sorted_data) - 1):
        current = sorted_data[i]
        next_addr = sorted_data[i + 1]
        if current in (0x7FFC, 0x7FFD, 0x7FFE, 0x7FFF) or next_addr in (0x7FFC, 0x7FFD, 0x7FFE, 0x7FFF):
            break
        if next_addr != current + 1:
            error_list.append(f"Skipped instruction: ${current + 1:04X} is missing")
            ERROR_COUNTER += 1


def error_processing(data_dict: Dict[int, int]) -> List:
    """Run opcode and address-order validation on a parsed ROM dump.

    Args:
        data_dict: Mapping of CPU address to byte value from :func:`parse_hex_file`.

    Returns:
        A list of human-readable error messages (empty when validation passes).
    """
    byte_list = list(data_dict.values())
    instruction_list = []
    for instruction in data_dict.keys():
        new_instruction = instruction - ROM_BASE_ADDR
        instruction_list.append(new_instruction)
    error_list_final = []
    verify_instructions(data=byte_list, error_list=error_list_final)
    verify_instruction_order(data=instruction_list, error_list=error_list_final)
    return error_list_final


def build_rom(input_path: Path, output_path: Path) -> None:
    """Parse a hex dump file and write a 32 KB ROM binary.

    Runs opcode and address-order validation before writing. Unused bytes are
    filled with ``$EA`` (NOP). The reset and IRQ/BRK vectors at ``$FFFC``–``$FFFF``
    must be present in the input or the build fails.

    Side effects:
        Resets :data:`ERROR_COUNTER`, may print errors and call ``sys.exit(1)``,
        creates parent directories for ``output_path``, writes the binary file,
        and prints a summary to stdout.

    Args:
        input_path: Path to the annotated hex dump file.
        output_path: Destination path for the 32 KB ``.bin`` file.

    Raises:
        ValueError: If parsing fails or required vectors are missing.
    """
    global ERROR_COUNTER
    ERROR_COUNTER = 0
    parsed = parse_hex_file(input_path)

    master_error_list = error_processing(parsed)
    if ERROR_COUNTER > 0:
        print(f"Encountered {ERROR_COUNTER} errors while building ROM:")
        for error in master_error_list:
            print(f"  {error}")
        print("ROM build failed due to errors.")
        sys.exit(1)

    rom = bytearray([0xEA] * ROM_SIZE)

    for cpu_addr, byte_val in parsed.items():
        offset = cpu_to_offset(cpu_addr)
        rom[offset] = byte_val

    required_vectors = {
        0xFFFC: "reset vector (low)",
        0xFFFD: "reset vector (high)",
        0xFFFE: "IRQ/BRK vector (low)",
        0xFFFF: "IRQ/BRK vector (high)",
    }
    missing = []
    for addr, desc in required_vectors.items():
        offset = cpu_to_offset(addr)
        if rom[offset] == 0xEA:
            missing.append(f"  ${addr:04X} ({desc})")
    if missing:
        raise ValueError("ROM is missing required vectors:\n" + "\n".join(missing))

    os.makedirs(output_path.parent, exist_ok=True)
    with open(output_path, "wb") as fh:
        fh.write(rom)

    print(f"Wrote {len(rom)} bytes to {output_path}")
    print(f"  Reset vector → ${rom[0x7FFD]:02X}{rom[0x7FFC]:02X}")
    print(f"  IRQ vector   → ${rom[0x7FFF]:02X}{rom[0x7FFE]:02X}")
