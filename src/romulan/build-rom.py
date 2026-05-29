"""Build a 32 KB ROM image for the Pico-as-ROM 65C02 system.

The 32 KB image maps to CPU addresses $8000-$FFFF.
File offset $0000 = CPU address $8000
File offset $7FFC = CPU address $FFFC (reset vector low byte)
File offset $7FFF = CPU address $FFFF
"""

import os

ROM_SIZE      = 0x8000   # 32 KB
ROM_BASE_ADDR = 0x8000   # ROM starts at CPU address $8000


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
    offset = cpu_to_offset(cpu_addr)
    for i, b in enumerate(data):
        rom[offset + i] = b & 0xFF
    return cpu_addr + len(data)


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
