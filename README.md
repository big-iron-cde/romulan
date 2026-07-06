# Romulan

A Python toolchain for building and uploading 32 KB ROM images to a **Pico-as-ROM 65C02 system** — a Raspberry Pi Pico that emulates a 32 KB ROM chip for a WDC 65C02 CPU.

## Overview

Romulan bridges the gap between annotated 65C02 assembly code and a live hardware ROM. It parses annotated hex dump files, assembles a 32 KB ROM binary with proper reset and IRQ vectors, and uploads it to the Pico over USB serial. The Pico then presents the image to the 65C02 as memory at addresses `$8000`–`$FFFF`.

Romulan also supports a **framed JSON serial protocol** for advanced hardware control — CPU reset, monitor toggle, address peek, and cycle capture — alongside the standard plain-text upload flow.

## Features

- **Parse annotated hex dumps** — Read human-readable annotated hex files with address, byte, and comment fields
- **Auto-fill ROM space** — Unused bytes are filled with NOPs (`$EA`) so accidentally-executed code is harmless
- **Vector validation** — Enforces the required 65C02 reset and IRQ/BRK vectors at `$FFFC`–`$FFFF`
- **Cross-platform port detection** — Auto-detects the Raspberry Pi Pico serial port on Linux, macOS, and Windows
- **Safe upload protocol** — Orchestrates CPU reset, ROM toggle, and firmware handshake during upload
- **Live serial capture** — Streams Pico output for 5 seconds after upload so you can see the CPU running
- **Framed serial protocol** — JSON-based hardware API with byte-level framing (ENQ/STX/ACK/EOT) for safe, structured communication with the Pico

## Requirements

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (recommended for dependency management)
- Raspberry Pi Pico running the Pico-as-ROM firmware
- USB cable to connect the Pico to your computer

## Installation

```bash
# Clone the repository
git clone <repository-url>
cd romulan

# Install dependencies and create virtual environment
uv sync
```

## Quick Start

### Build a ROM binary

```bash
uv run romulan program.txt --build
```

### Build and upload in one step

```bash
uv run romulan program.txt --build --upload
```

### Upload an existing binary

```bash
uv run romulan --upload
```

### Specify a custom port or output path

```bash
# Custom serial port
uv run romulan program.txt --build --upload --port /dev/ttyACM0

# Custom output path
uv run romulan program.txt --build -o output/rom.bin
```

### Hardware API

```bash
# Upload a ROM via the framed protocol
uv run romulan hardware upload bin/rom.bin --port /dev/ttyACM0

# Capture CPU bus cycles until STP or max cycles
uv run romulan hardware capture --max-cycles 500 --port /dev/ttyACM0

# Hold CPU in reset
uv run romulan hardware reset --assert --port /dev/ttyACM0

# Release CPU from reset
uv run romulan hardware reset --release --port /dev/ttyACM0

# Disable unstructured monitor output
uv run romulan hardware monitor --disable --port /dev/ttyACM0

# Request the current CPU address
uv run romulan hardware request-addr --port /dev/ttyACM0
```

## Input File Format

Romulan expects an annotated hex dump file with one byte per line. Each line contains a file address, a byte value, and an optional comment after `@`.

```
0x0000   0x18   @ CLC
0x0001   0xA9   @ LDA 0x05
0x0002   0x05
0x0003   0x8D   @ STA $4000
0x0004   0x00
0x0005   0x40
...
0x7FFC   0x00   @ Reset vector (low)
0x7FFD   0x80   @ Reset vector (high)
0x7FFE   0x00   @ IRQ/BRK vector (low)
0x7FFF   0x80   @ IRQ/BRK vector (high)
```

- **File addresses** (`0x0000`–`0x7FFF`) map to **CPU addresses** `$8000`–`$FFFF`
- **Comments** are optional — everything after `@` is ignored
- **Vectors** at `0x7FFC`–`0x7FFF` are **required** — the builder will reject images without them

## CLI Reference

### Standard CLI

| Flag | Description | Default |
|------|-------------|---------|
| `input` | Path to the annotated hex dump file (required with `--build`) | — |
| `--build` | Build a `.bin` ROM image from the input file | — |
| `--upload` | Upload the ROM image to the Pico | — |
| `-o, --output` | Output ROM binary path | `bin/rom.bin` |
| `--port` | Serial port for the Pico (auto-detected if omitted) | Auto-detect |

At least one of `--build` or `--upload` is required, but `--upload` can only be used after a successful `--build` or if a valid ROM binary already exists at the output path.

### Hardware API

| Subcommand | Arguments | Description |
|------------|-----------|-------------|
| `hardware upload` | `<bin_path> [--port]` | Upload a ROM binary via the framed protocol |
| `hardware capture` | `--max-cycles <N> [--port]` | Capture CPU bus cycles until STP or max cycles reached |
| `hardware monitor` | `--enable \| --disable [--port]` | Toggle unstructured ASCII monitor output |
| `hardware reset` | `--assert \| --release [--port]` | Hold or release the CPU reset line |
| `hardware request-addr` | `[--port]` | Request the current CPU address |

## Architecture

### Memory Map

The 32 KB ROM image maps directly to the 65C02 address space:

| File Offset | CPU Address | Purpose |
|-------------|-------------|---------|
| `0x0000` | `$8000` | Start of ROM |
| `0x7FFC` | `$FFFC` | Reset vector (low byte) |
| `0x7FFD` | `$FFFD` | Reset vector (high byte) |
| `0x7FFE` | `$FFFE` | IRQ/BRK vector (low byte) |
| `0x7FFF` | `$FFFF` | IRQ/BRK vector (high byte) |

### Upload Protocol

The standard Pico-as-ROM firmware exposes a simple binary upload protocol over USB-CDC:

1. Host sends `loadbin\n`
2. Pico responds with `OK send 32768 bytes`
3. Host streams 32,768 raw bytes
4. Pico responds with `loaded 32768 bytes`

Romulan wraps this with additional safety steps:

1. Assert CPU reset (`r0`)
2. Disable ROM emulation (`roms`)
3. Upload the binary (`loadbin`)
4. Re-enable ROM emulation (`rom`)
5. Start 100 kHz clock (`c100`)
6. Release CPU reset (`r1`)
7. Capture live output for 5 seconds

### Framed Protocol

The hardware API uses a byte-level framed protocol for robust JSON communication:

- **Frame start:** `ENQ` (0x05)
- **Payload start:** `STX` (0x02)
- **Receiver ready:** `ACK` (0x06)
- **Payload:** JSON (except raw binary ROM upload)
- **End of payload:** `EOT` (0x04)
- **Accepted:** `ACK` (0x06)
- **Rejected:** `NACK` (0x15)

The implementation silently discards stray bytes (monitor lines, echoed ACKs) while resyncing to frame boundaries.

**ROM upload** uses a two-phase exchange:
1. JSON command frame: `{"cmd": "loadbin", "size": 32768}`
2. Raw binary frame: the 32,768 bytes

**Supported commands:**
- `loadbin` — upload 32 KB ROM image
- `reset` — assert or release CPU reset
- `monitor` — enable or disable ASCII monitor output
- `request_addr` — read current CPU address
- `read_until_stp` — capture CPU bus cycles until STP or max cycles

## Project Structure

```
romulan/
├── src/romulan/
│   ├── __init__.py
│   ├── main.py          # CLI entry point and argument parsing
│   ├── build_rom.py     # Hex dump parser and ROM binary builder
│   ├── upload_rom.py    # Serial port detection and standard upload protocol
│   ├── hardware_api.py  # Framed serial protocol and hardware API
│   └── demo.txt         # Sample annotated hex dump
├── tests/
│   ├── test_main.py     # Test suite for parser, builder, CLI, and upload
│   └── test_hardware_api.py  # Tests for the framed serial protocol
├── bin/
│   └── rom.bin          # Generated ROM binary (gitignored)
├── pyproject.toml       # Project configuration (uv-managed)
├── uv.lock              # Locked dependency tree
└── README.md            # This file
```

## Testing

Run the test suite with `pytest`:

```bash
uv run pytest
```

Tests cover:

- Hex dump parsing (valid files, out-of-range addresses, invalid lines)
- ROM building (valid builds)
- CLI behavior (build-only, upload-only, build+upload, error handling)
- Port detection (single Pico, multiple Picos, no Pico found)
- Framed serial protocol (frame construction, error handling, command dispatch)
- Hardware API state machine (mock serial interactions)

## Hardware Notes

### Serial Port Detection

- **Linux:** `/dev/ttyACM*` or `/dev/ttyUSB*`
- **macOS:** `/dev/cu.usbmodem*` or `/dev/tty.usbmodem*`
- **Windows:** `COM*` (detected via `pyserial` port enumeration)

If auto-detection fails or finds multiple ports, specify the port explicitly with `--port`.

### Firmware Compatibility

This toolchain supports two modes:

- **Standard plain-text mode:** commands `loadbin`, `rom`, `roms`, `r0`, `r1`, `c100`, `watch`
- **Framed protocol mode:** commands `loadbin`, `reset`, `monitor`, `read_until_stp`, `request_addr`

Both modes are compatible with the Pico-as-ROM firmware.

### Safety

Both upload protocols assert CPU reset before writing to ROM and release it only after the new image is active. This prevents the 65C02 from reading inconsistent data during the upload.

