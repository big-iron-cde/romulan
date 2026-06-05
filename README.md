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

## TODOS
single 
1. **Parse and verify the "correctness" of an independent ROM file**  
   Accept an arbitrary 65C02 ROM dump, validate its size (must be exactly 32 KB), and sanity-check the reset/IRQ vectors.

2. **Write a BIN file from the ROM file**  
   Convert the parsed ROM into a flat 32 KB binary image ready for upload.

3. **Upload this ROM to the PICO**  
   Stream the binary to the Pico over USB serial using its `loadbin` protocol.  
   *Windows and Mac users may not have `ttyACM0` (it'll be called something else; how can we detect it?).*
