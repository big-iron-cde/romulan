# Romulan

A Python toolchain for working with a **Pico-as-ROM 65C02 system** — a Raspberry Pi Pico that emulates a 32 KB ROM chip for a WDC 65C02 CPU.

## What It Does

Romulan builds a 32 KB ROM image and uploads it to the Pico over a USB serial connection. The Pico then presents that image to the 65C02 as memory at addresses `$8000`–`$FFFF`.

### Files

| File | Purpose |
|------|---------|
| `src/romulan/main.py` | CLI entry point. Currently a placeholder — will orchestrate the full workflow. |
| `src/romulan/build-rom.py` | Assembles a 32 KB ROM binary (`bin/rom.bin`) with a small test program (loads values into `$4000`, loops forever) and proper 65C02 reset/IRQ vectors. |
| `src/romulan/upload-rom.py` | Uploads the binary to the Pico via USB serial and toggles the CPU reset line so the new image runs immediately. |
| `pyproject.toml` | Project configuration. Managed with [uv](https://docs.astral.sh/uv/). |

## Quick Start

```bash
# Install dependencies (creates .venv automatically)
uv sync

# Run the CLI
uv run romulan
```

## Example ROM Input

`build-rom.py` assembles this small 65C02 test program at CPU address `$0000`:

```
0x0000   0x18          @ CLC
0x0001   0xA9          @ LDA 0x5
0x0002   0x05          
0x0003   0x8D 
0x0004   0x00 
0x0005   0x40
0x0006   0x69 
0x0007   0x03      
0x0008   0x8D 
0x0009   0x00 
0x000A   0x40 
0x000B   0x4C
0x000C   0x00
0x000D   0x00
0xFFFC   0x00 
0xFFFD   0x00
0xFFFE   0x00
0xFFFF   0x00
```

The resulting 32 KB `bin/rom.bin` fills unused space with `$EA` (NOP) and writes the reset/IRQ vectors so the CPU boots into the loop.

## TODOS

1. **Parse and verify the "correctness" of an independent ROM file**  
   Accept an arbitrary 65C02 ROM dump, validate its size (must be exactly 32 KB), and sanity-check the reset/IRQ vectors.

2. **Write a BIN file from the ROM file**  
   Convert the parsed ROM into a flat 32 KB binary image ready for upload.

3. **Upload this ROM to the PICO**  
   Stream the binary to the Pico over USB serial using its `loadbin` protocol.  
   *Windows and Mac users may not have `ttyACM0` (it'll be called something else; how can we detect it?).*
