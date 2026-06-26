"""Build a 32 KB ROM image for the Pico-as-ROM 65C02 system.

The 32 KB image maps to CPU addresses $8000-$FFFF.
File offset $0000 = CPU address $8000
File offset $7FFC = CPU address $FFFC (reset vector low byte)
File offset $7FFF = CPU address $FFFF
"""

import os
import re
import sys
from typing import List
from pathlib import Path

ROM_SIZE = 0x8000  # 32 KB
ROM_BASE_ADDR = 0x8000  # ROM starts at CPU address $8000
ERROR_COUNTER = 0  # Global counter for errors encountered during ROM build

class InvalidInstructionError(Exception):
    """Exception raised for instances of invalid instructions for the 65C02 system."""

    def __init__(self, message):
        """Defines an instance of the custom exception class."""
        super().__init__(message)
        self.message = message

    def __str__(self):
        """Defines the default string method for instances of the class."""
        return self.message

def cpu_to_offset(cpu_addr: int) -> int:
    """Convert a CPU address ($8000-$FFFF) to a file offset (0-$7FFF)."""
    if not (ROM_BASE_ADDR <= cpu_addr <= 0xFFFF):
        raise ValueError(
            f"CPU address ${cpu_addr:04X} is outside the ROM region "
            f"(${ROM_BASE_ADDR:04X}-$FFFF)"
        )
    return cpu_addr - ROM_BASE_ADDR


def parse_hex_file(path: Path) -> dict[int, int]:
    """Parse an annotated hex dump file into a dict of CPU address -> byte.

    Expected line format:
        0x0000   0x18   @ CLC
        0x0001   0xA9   @ LDA 0x5

    File addresses are in the range 0x0000-0x7FFF and are mapped to
    CPU addresses by adding ROM_BASE_ADDR (0x8000).
    """
    data: dict[int, int] = {}
    line_pattern = re.compile(
        r"^\s*0x([0-9A-Fa-f]+)\s+0x([0-9A-Fa-f]{2})"
    )

    with open(path, "r", encoding="utf-8") as fh:
        for line_num, line in enumerate(fh, start=1):
            line = line.split("@")[0].strip()  # strip comments
            if not line:
                continue

            match = line_pattern.match(line)
            if not match:
                raise ValueError(
                    f"Cannot parse line {line_num}: {line.strip()!r}"
                )

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


def build_rom(input_path: Path, output_path: Path) -> None:
    """Parse a hex dump file and write a 32 KB ROM binary."""
    parsed = parse_hex_file(input_path)

    # ─── Error processing ────────────────────────────────────────────────
    # Creates the master error list to be printed if any errors are encountered.
    master_error_list = []
    # If any errors were encountered, print them and exit with a non-zero status.
    if ERROR_COUNTER > 0:
        # Prints the number of errors encountered
        print(f"Encountered {ERROR_COUNTER} errors while building ROM:")
        # Prints each error in the list
        for error in master_error_list:     # This currently doesn't work
            print(f"  {error}")
        # Indicates the build failed and exits with a non-zero status code.
        print("ROM build failed due to errors.")
        sys.exit(1)

    # Fill with NOPs ($EA) so any unintentionally-executed bytes are harmless.
    rom = bytearray([0xEA] * ROM_SIZE)


    # Write parsed bytes into ROM at CPU addresses
    for cpu_addr, byte_val in parsed.items():
        offset = cpu_to_offset(cpu_addr)
        rom[offset] = byte_val

    # Validate required vectors
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
        raise ValueError(
            "ROM is missing required vectors:\n" + "\n".join(missing)
        )

    # Write the file
    os.makedirs(output_path.parent, exist_ok=True)
    with open(output_path, "wb") as fh:
        fh.write(rom)

    print(f"Wrote {len(rom)} bytes to {output_path}")
    print(f"  Reset vector → ${rom[0x7FFD]:02X}{rom[0x7FFC]:02X}")
    print(f"  IRQ vector   → ${rom[0x7FFF]:02X}{rom[0x7FFE]:02X}")
    print(cpu_to_offset)

def verify_instructions(*data: int, error_list: List) -> None:
    """Check all entries in `data` are valid instructions."""
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

    # Loop through each byte in `data`
    for d in data:
        # Check if the byte fits the expected range for an instruction or memory location
        if 0x00 <= d <= 0xFF:
            # Compare d to all instructions in invalid_instructions list
            if d in invalid_instructions:
                position = data.index(d)
                if position == 1:
                    # Compare data[0] to all instructions requiring memory address instructions
                    if data[0] in instructions_with_immediate or instructions_with_address:
                        continue
                # Covers instructions that require 2 bytes of memory address instructions
                elif position == 2:
                    if data[0] in instructions_with_address:
                        continue
                # Handles all invalid and in range instructions
                # (those undefined in the opcode matrix and used in an opcode context)
                else:
                    error_list.append(f"Invalid instruction: ${d} is undefined")
                    ERROR_COUNTER += 1
        # Handles all data bytes outside the valid range of 0x00 - 0xFF
        else:
            error_list.append(f"Invalid instruction: ${d} is out of range (0x00 - 0xFF)")
            ERROR_COUNTER += 1


def error_processing(*data: int)-> List:
    """Handle all error processing for the ROM file."""
    error_list_final = []
    verify_instructions(*data, error_list=error_list_final)
    return error_list_final
