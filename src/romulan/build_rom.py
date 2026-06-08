"""Build a 32 KB ROM image for the Pico-as-ROM 65C02 system.

The 32 KB image maps to CPU addresses $8000-$FFFF.
File offset $0000 = CPU address $8000
File offset $7FFC = CPU address $FFFC (reset vector low byte)
File offset $7FFF = CPU address $FFFF
"""

import os

ROM_SIZE      = 0x8000   # 32 KB
ROM_BASE_ADDR = 0x8000   # ROM starts at CPU address $8000


class InvalidInstructionError(Exception):
    """Exception raised for instances of invalid instructions for the 65C02 system."""

    def __init__(self, message):
        """Defines an instance of the custom exception class."""
        self.message = super().__init__(message)

    def __str__(self):
        """Defines the default string method for instances of the class."""
        return f"{self.message}"


def cpu_to_offset(cpu_addr: int) -> int:
    """Convert a CPU address ($8000-$FFFF) to a file offset (0-$7FFF)."""
    if not (ROM_BASE_ADDR <= cpu_addr <= 0xFFFF):
        raise ValueError(
            f"CPU address ${cpu_addr:04X} is outside the ROM region "
            f"(${ROM_BASE_ADDR:04X}-$FFFF)"
        )
    return cpu_addr - ROM_BASE_ADDR


def write_bytes(rom: bytearray, cpu_addr: int, *data: int) -> int:
    """Write one or more bytes into the ROM at the given CPU address.
    Returns the next CPU address (so you can chain calls)."""
    # verify_data(*data) # Leaving this out for ease of testing purposes right now
    offset = cpu_to_offset(cpu_addr)
    for i, b in enumerate(data):
        rom[offset + i] = b & 0xFF
    return cpu_addr + len(data)

def verify_instructions(*data: int) -> None:
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
                    raise InvalidInstructionError(f"Invalid instruction: ${d} is undefined")
        # Handles all data bytes outside the valid range of 0x00 - 0xFF
        else:
            raise InvalidInstructionError(f"Invalid instruction: ${d} is out of range (0x00 - 0xFF)")


def main():
    # Fill with NOPs ($EA) so any unintentionally-executed bytes are harmless.
    rom = bytearray([0xEA] * ROM_SIZE)

    # ─── Program code, starting at CPU address $8000 ─────────────────────
    #
    # CLC                    ; clear carry so ADC is predictable
    # LDA #$05               ; A = 5
    # STA $4000              ; write 5 to $4000 (Pico watch port — avoid $5000;
    #                        ;   high byte $50 couples onto the data bus)
    # ADC #$03               ; A = 5 + 3 + 0 = 8
    # STA $4000              ; write 8 to $4000
    # JMP $8000              ; loop back to the top
    #
    pc = 0x8000
    pc = write_bytes(rom, pc, 0x18)               # CLC
    pc = write_bytes(rom, pc, 0xA9, 0x05)         # LDA #$05
    pc = write_bytes(rom, pc, 0x8D, 0x00, 0x40)   # STA $4000
    pc = write_bytes(rom, pc, 0x69, 0x03)         # ADC #$03
    pc = write_bytes(rom, pc, 0x8D, 0x00, 0x40)   # STA $4000
    pc = write_bytes(rom, pc, 0x4C, 0x00, 0x80)   # JMP $8000

    # ─── Reset / IRQ vectors at the top of ROM ───────────────────────────
    # CPU $FFFC-$FFFD = reset vector (where the CPU jumps on power-up)
    write_bytes(rom, 0xFFFC, 0x00, 0x80)          # reset → $8000

    # CPU $FFFE-$FFFF = IRQ/BRK vector. Point it back at $8000 too so any
    # spurious BRK from broken RAM execution just restarts the program.
    write_bytes(rom, 0xFFFE, 0x00, 0x80)          # IRQ/BRK → $8000

    # ─── Write the file ──────────────────────────────────────────────────
    os.makedirs("bin", exist_ok=True)
    out_path = "bin/rom.bin"
    with open(out_path, "wb") as fh:
        fh.write(rom)

    print(f"Wrote {len(rom)} bytes to {out_path}")
    print(f"  Program at CPU $8000 ({pc - 0x8000} bytes)")
    print(f"  Reset vector → ${rom[0x7FFD]:02X}{rom[0x7FFC]:02X}")
    print(f"  IRQ vector   → ${rom[0x7FFF]:02X}{rom[0x7FFE]:02X}")


if __name__ == "__main__":
    main()
