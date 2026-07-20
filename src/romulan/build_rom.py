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

from .assemble import parse_asm_file
from .output import emit_error, emit_result

ROM_SIZE = 0x8000  # 32 KB
ROM_BASE_ADDR = 0x8000  # ROM starts at CPU address $8000
ERROR_COUNTER = 0  # Global counter for errors encountered during ROM build

# Annotated hex dump line: "0x0000   0x18   @ optional comment"
_HEX_LINE_PATTERN = re.compile(r"^\s*0x([0-9A-Fa-f]+)\s+0x([0-9A-Fa-f]{2})")


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


def detect_format(path: Path) -> str:
    """Detect the input format of a ROM source file.

    Sniffs the first meaningful line (skipping blank lines and full-line
    ``;`` or ``@`` comments): if it matches the annotated hex dump shape
    (``0xADDR 0xBYTE``) the file is a hex dump, otherwise it is treated
    as 6502 assembly. File extensions are not consulted — either format
    may live in a ``.txt`` or ``.s`` file.

    Args:
        path: Path to the input file.

    Returns:
        ``"hex"`` for an annotated hex dump or ``"asm"`` for 6502 assembly.

    Raises:
        ValueError: If the file contains no data lines.
    """
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            stripped = line.strip()
            if not stripped or stripped.startswith((";", "@")):
                continue
            return "hex" if _HEX_LINE_PATTERN.match(stripped) else "asm"
    raise ValueError(f"Input file contains no data: {path}")


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

    with open(path, "r", encoding="utf-8") as fh:
        for line_num, line in enumerate(fh, start=1):
            line = line.split("@")[0].strip()  # strip comments
            if not line:
                continue

            match = _HEX_LINE_PATTERN.match(line)
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


# W65C02 instruction lengths (bytes) indexed by opcode.
# Teaching "invalid" opcodes are still flagged even when they are 1-byte NOPs on 65C02.
_OPCODE_LENGTH: list[int] = [
    # 0x00-0x0F
    2, 2, 1, 1, 2, 2, 2, 2, 1, 2, 1, 1, 3, 3, 3, 3,
    # 0x10-0x1F
    2, 2, 2, 1, 2, 2, 2, 2, 1, 3, 1, 1, 3, 3, 3, 3,
    # 0x20-0x2F
    3, 2, 1, 1, 2, 2, 2, 2, 1, 2, 1, 1, 3, 3, 3, 3,
    # 0x30-0x3F
    2, 2, 2, 1, 2, 2, 2, 2, 1, 3, 1, 1, 3, 3, 3, 3,
    # 0x40-0x4F
    1, 2, 1, 1, 2, 2, 2, 2, 1, 2, 1, 1, 3, 3, 3, 3,
    # 0x50-0x5F
    2, 2, 2, 1, 2, 2, 2, 2, 1, 3, 1, 1, 1, 3, 3, 3,
    # 0x60-0x6F
    1, 2, 1, 1, 2, 2, 2, 2, 1, 2, 1, 1, 3, 3, 3, 3,
    # 0x70-0x7F
    2, 2, 2, 1, 2, 2, 2, 2, 1, 3, 1, 1, 3, 3, 3, 3,
    # 0x80-0x8F
    2, 2, 1, 1, 2, 2, 2, 2, 1, 2, 1, 1, 3, 3, 3, 3,
    # 0x90-0x9F
    2, 2, 2, 1, 2, 2, 2, 2, 1, 3, 1, 1, 3, 3, 3, 3,
    # 0xA0-0xAF
    2, 2, 2, 1, 2, 2, 2, 2, 1, 2, 1, 1, 3, 3, 3, 3,
    # 0xB0-0xBF
    2, 2, 2, 1, 2, 2, 2, 2, 1, 3, 1, 1, 3, 3, 3, 3,
    # 0xC0-0xCF
    2, 2, 1, 1, 2, 2, 2, 2, 1, 2, 1, 1, 3, 3, 3, 3,
    # 0xD0-0xDF
    2, 2, 2, 1, 2, 2, 2, 2, 1, 3, 1, 1, 1, 3, 3, 3,
    # 0xE0-0xEF
    2, 2, 1, 1, 2, 2, 2, 2, 1, 2, 1, 1, 3, 3, 3, 3,
    # 0xF0-0xFF
    2, 2, 2, 1, 2, 2, 2, 2, 1, 3, 1, 1, 1, 3, 3, 3,
]

# Opcodes treated as errors for this course (avoid NMOS-illegal / unused slots).
_INVALID_OPCODES = frozenset({
    0x02, 0x03, 0x0B, 0x13, 0x1B, 0x22, 0x23, 0x2B, 0x33,
    0x3B, 0x42, 0x43, 0x44, 0x4B, 0x53, 0x54, 0x5B, 0x5C,
    0x62, 0x63, 0x6B, 0x73, 0x7B, 0x82, 0x83, 0x8B, 0x93,
    0x9B, 0xA3, 0xAB, 0xB3, 0xBB, 0xC2, 0xC3, 0xD3, 0xD4,
    0xDC, 0xE2, 0xE3, 0xEB, 0xF3, 0xF4, 0xFB, 0xFC,
})


def verify_instructions(data: List[int], error_list: List) -> None:
    """Validate a contiguous instruction stream and append errors.

    Walks opcode lengths so operand bytes are never treated as opcodes.
    Errors are appended to ``error_list`` and :data:`ERROR_COUNTER` is incremented.

    Args:
        data: Contiguous program bytes (not including reset/IRQ vector slots).
        error_list: List that receives human-readable error messages.
    """
    global ERROR_COUNTER

    i = 0
    while i < len(data):
        op = data[i]
        if not (0x00 <= op <= 0xFF):
            error_list.append(
                f"Invalid instruction: ${op} is out of range (0x00 - 0xFF)"
            )
            ERROR_COUNTER += 1
            i += 1
            continue

        if op in _INVALID_OPCODES:
            error_list.append(f"Invalid instruction: ${op:02X} is undefined")
            ERROR_COUNTER += 1

        length = _OPCODE_LENGTH[op]
        if i + length > len(data):
            # Truncated final instruction at the end of the supplied region.
            break
        i += length


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
    VECTOR_FILE_BASE = 0x7FFC
    ordered = sorted(data_dict.items())
    file_addrs = [cpu - ROM_BASE_ADDR for cpu, _ in ordered]
    program_bytes = [
        value
        for cpu, value in ordered
        if (cpu - ROM_BASE_ADDR) < VECTOR_FILE_BASE
    ]
    error_list_final: List = []
    verify_instructions(data=program_bytes, error_list=error_list_final)
    verify_instruction_order(data=file_addrs, error_list=error_list_final)
    return error_list_final


def build_rom(input_path: Path, output_path: Path) -> None:
    """Parse a ROM source file and write a 32 KB ROM binary.

    The input may be an annotated hex dump or 6502 assembly (auto-detected
    by :func:`detect_format`). Hex dumps get opcode and address-order
    validation before writing; assembled input is valid by construction and
    may contain gaps from ``.org`` directives, so that validation is skipped
    for it. Unused bytes are filled with ``$EA`` (NOP). The reset and
    IRQ/BRK vectors at ``$FFFC``–``$FFFF`` must be present in the input or
    the build fails.

    Side effects:
        Resets :data:`ERROR_COUNTER`, may print errors and call ``sys.exit(1)``,
        creates parent directories for ``output_path``, writes the binary file,
        and prints a summary to stdout.

    Args:
        input_path: Path to the annotated hex dump or 6502 assembly file.
        output_path: Destination path for the 32 KB ``.bin`` file.

    Raises:
        ValueError: If parsing fails or required vectors are missing.
    """
    global ERROR_COUNTER
    ERROR_COUNTER = 0
    input_format = detect_format(input_path)
    if input_format == "hex":
        parsed = parse_hex_file(input_path)

        master_error_list = error_processing(parsed)
        if ERROR_COUNTER > 0:
            emit_error(
                "build_failed",
                f"Encountered {ERROR_COUNTER} errors while building ROM",
                errors=[str(error) for error in master_error_list],
            )
            sys.exit(1)
    else:
        parsed = parse_asm_file(input_path)

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

    emit_result(
        "build",
        {
            "bytes": len(rom),
            "output": str(output_path),
            "format": input_format,
            "reset_vector": f"{rom[0x7FFD]:02X}{rom[0x7FFC]:02X}",
            "irq_vector": f"{rom[0x7FFF]:02X}{rom[0x7FFE]:02X}",
        },
    )
