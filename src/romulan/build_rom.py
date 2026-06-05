"""Build a 32 KB ROM image for the Pico-as-ROM 65C02 system.

The 32 KB image maps to CPU addresses $8000-$FFFF.
File offset $0000 = CPU address $8000
File offset $7FFC = CPU address $FFFC (reset vector low byte)
File offset $7FFF = CPU address $FFFF
"""

import os
import re
from pathlib import Path

ROM_SIZE = 0x8000  # 32 KB
ROM_BASE_ADDR = 0x8000  # ROM starts at CPU address $8000


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

