# Romulan

A Python toolchain for working with a **Pico-as-ROM 65C02 system** — a Raspberry Pi Pico that emulates a 32 KB ROM chip for a WDC 65C02 CPU.

## What It Does

Romulan builds a 32 KB ROM image from an annotated hex dump and uploads it to the Pico over a USB serial connection. The Pico then presents that image to the 65C02 as memory at addresses `$8000`–`$FFFF`.

## Input File Format

The input file is an annotated hex dump where each line contains:

```
0xADDR   0xBYTE   @ optional comment
```

- `ADDR` is the file address in the range `0x0000–0x7FFF` (maps to CPU `$8000–$FFFF`)
- `BYTE` is the hex value to write at that address
- Multi-byte instructions span multiple consecutive address lines

Example:
```
0x0000   0x18   @ CLC
0x0001   0xA9   @ LDA 0x5
0x0002   0x05
0x0003   0x8D   @ STA $4000
0x0004   0x00
0x0005   0x40
...
0x7FFC   0x00   @ Reset vector low
0x7FFD   0x80   @ Reset vector high
0x7FFE   0x00   @ IRQ vector low
0x7FFF   0x80   @ IRQ vector high
```

**Important:** File addresses are offset by `0x8000` to get CPU addresses. The reset and IRQ vectors must be present at file offsets `0x7FFC–0x7FFF` (CPU `$FFFC–$FFFF`).

## Quick Start

```bash
# Install dependencies (creates .venv automatically)
uv sync

# Build a ROM from a hex dump (default output: bin/rom.bin)
uv run romulan program.txt --build

# Build to a custom path
uv run romulan program.txt --build -o out/rom.bin

# Upload an existing ROM to the Pico (default: bin/rom.bin)
uv run romulan --upload

# Upload a custom ROM file
uv run romulan --upload -o out/rom.bin

# Build and upload in one step
uv run romulan program.txt --build --upload

# Build and upload to a custom path
uv run romulan program.txt --build --upload -o out/rom.bin

# Specify a serial port explicitly
uv run romulan program.txt --build --upload --port /dev/ttyACM0
```

## CLI Reference

```
usage: romulan [-h] [--build] [--upload] [-o OUTPUT] [--port PORT] [input]

positional arguments:
  input                Path to the annotated hex dump input file (required
                       with --build)

options:
  -h, --help           show this help message and exit
  --build              Build a .bin ROM image from the input file
  --upload             Upload the ROM image to the Pico
  -o, --output OUTPUT  Output ROM binary path (default: bin/rom.bin)
  --port PORT          Serial port for the Pico (auto-detected if omitted)
```

### Auto-Detection

If `--port` is omitted, Romulan tries to find your Pico automatically:

- **Linux:** `/dev/ttyACM*`, `/dev/ttyUSB*`
- **macOS:** `/dev/cu.usbmodem*`, `/dev/tty.usbmodem*`
- **Windows:** `COM*`

It uses the Raspberry Pi USB vendor ID (`0x2E8A`) to identify the device. If multiple ports are found, it asks you to specify one explicitly.

## Project Structure

| File | Purpose |
|------|---------|
| `src/romulan/main.py` | CLI entry point. Parses arguments and orchestrates build/upload. |
| `src/romulan/build_rom.py` | Parses annotated hex dumps and assembles a 32 KB ROM binary (`bin/rom.bin`). |
| `src/romulan/upload_rom.py` | Uploads the binary to the Pico via USB serial using the `loadbin` protocol. |
| `pyproject.toml` | Project configuration. Managed with [uv](https://docs.astral.sh/uv/). |

## Running Tests

```bash
uv run pytest tests/ -v
```

## Notes

- The default ROM output path is `bin/rom.bin`. Use `-o` / `--output` to change it.
- `--upload` without `--build` requires the specified output file (or `bin/rom.bin` by default) to exist.
- The firmware expects exactly 32 KB (`32768` bytes).
