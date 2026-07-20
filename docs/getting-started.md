# Getting Started

## Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) for dependency management
- A Raspberry Pi Pico running the [Piclone](https://github.com/big-iron-cde/piclone) firmware
- USB cable connecting the Pico to your computer

For hardware wiring and firmware flashing, see the [Piclone documentation](https://big-iron-cde.github.io/piclone/).

## Install

```bash
git clone https://github.com/big-iron-cde/romulan.git
cd romulan
uv sync
```

## First build and upload

Romulan ships with `demo.txt`, a sample annotated hex dump, and `demo.s`, the same program in 6502 assembly. Build and upload either one (the format is auto-detected):

```bash
uv run romulan demo.txt --build --upload
uv run romulan demo.s --build --upload
```

This produces `bin/rom.bin` (32 KB) and uploads it to the Pico via the framed Hardware API.

## Serial port

Romulan auto-detects the Pico when exactly one device is connected. If detection fails or finds multiple ports, specify one explicitly:

```bash
uv run romulan demo.txt --build --upload --port /dev/ttyACM0   # Linux
uv run romulan demo.txt --build --upload --port /dev/cu.usbmodem101  # macOS
uv run romulan demo.txt --build --upload --port COM3             # Windows
```

## Input file formats

`--build` accepts two source formats, auto-detected from the file contents.

### Annotated hex dump

Each line in an annotated hex dump contains a file address, a byte value, and an optional comment after `@`:

```
0x0000   0x18   @ CLC
0x0001   0xA9   @ LDA 0x05
0x0002   0x05
...
0x7FFC   0x00   @ Reset vector (low)
0x7FFD   0x80   @ Reset vector (high)
0x7FFE   0x00   @ IRQ/BRK vector (low)
0x7FFF   0x80   @ IRQ/BRK vector (high)
```

File addresses `0x0000`–`0x7FFF` map to CPU addresses `$8000`–`$FFFF`. Vectors at `0x7FFC`–`0x7FFF` are required.

### 6502 assembly

Assembly source uses CPU addresses directly. Comments start with `;`:

```asm
        .org $8000

reset:  CLC
        LDA #$05
        STA $4000
        STP

        .org $FFFC
        .word reset     ; reset vector
        .word reset     ; IRQ/BRK vector
```

All official NMOS 6502 mnemonics are supported, plus the W65C02 additions used by the course (`STP`, `WAI`, `BRA`, `PHX`/`PHY`/`PLX`/`PLY`, `STZ`, `TRB`/`TSB`, accumulator `INC`/`DEC`, `(zp)` indirect). Numbers may be `$hex`, `0xhex` or decimal; directives are `.org`, `.byte` and `.word`. All emitted bytes must land in `$8000`–`$FFFF`, and the vectors are required here as well.

## Next steps

- [CLI reference](cli.md) — all commands and flags
- [Hardware API client](hardware-api.md) — framed protocol and Python usage
- [Python API reference](python-api.md) — autodoc for all modules
