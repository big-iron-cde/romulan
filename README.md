# Romulan

A Python toolchain for building and uploading 32 KB ROM images to a **Pico-as-ROM 65C02 system** — a Raspberry Pi Pico that emulates a 32 KB ROM chip for a WDC 65C02 CPU.

## Overview

Romulan bridges the gap between annotated 65C02 assembly code and a live hardware ROM. It parses annotated hex dump files, assembles a 32 KB ROM binary with proper reset and IRQ vectors, and uploads it to the Pico over USB serial. The Pico then presents the image to the 65C02 as memory at addresses `$8000`–`$FFFF`.

Romulan also supports a **framed JSON serial protocol (v1)** for advanced hardware control — chunked ROM upload, CPU reset, monitor toggle, address peek, bus capture, and status queries — alongside the standard plain-text upload flow.

## Features

- **Parse annotated hex dumps** — Read human-readable annotated hex files with address, byte, and comment fields
- **Auto-fill ROM space** — Unused bytes are filled with NOPs (`$EA`) so accidentally-executed code is harmless
- **Vector validation** — Enforces the required 65C02 reset and IRQ/BRK vectors at `$FFFC`–`$FFFF`
- **Cross-platform port detection** — Auto-detects the Raspberry Pi Pico serial port on Linux, macOS, and Windows
- **Safe upload protocol** — Orchestrates CPU reset, ROM toggle, and firmware handshake during upload
- **Live serial capture** — Streams Pico output for 5 seconds after upload so you can see the CPU running
- **Framed serial protocol (v1)** — Versioned JSON envelopes over byte-level framing (ENQ/STX/ACK/EOT) for safe, structured communication with the Pico
- **65C02 opcode validation** — `verify_instructions` checks for undefined opcodes and distinguishes opcode bytes from immediate/address operands

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
uv run romulan demo.txt --build
```

### Build and upload in one step

```bash
uv run romulan demo.txt --build --upload
```

### Upload an existing binary

```bash
uv run romulan --upload
```

### Specify a custom port or output path

```bash
# Custom serial port
uv run romulan demo.txt --build --upload --port /dev/ttyACM0

# Custom output path
uv run romulan demo.txt --build -o output/rom.bin
```

### Hardware API

The `--port` flag is optional when exactly one Pico is connected — Romulan auto-detects it. Add `--verbose` (or `-v`) to any hardware command to see every message sent and received.

```bash
# Upload a ROM via the framed protocol (auto-detect port, show protocol trace)
uv run romulan hardware upload bin/rom.bin --verbose

# Upload with explicit port
uv run romulan hardware upload bin/rom.bin --port /dev/ttyACM0

# Capture CPU bus cycles until STP or max cycles
uv run romulan hardware capture --max-cycles 500

# Use a longer timeout for slow operations (e.g. large uploads or long captures)
uv run romulan hardware capture --max-cycles 500 --timeout 45

# Hold CPU in reset
uv run romulan hardware reset --assert

# Release CPU from reset
uv run romulan hardware reset --release

# Disable unstructured monitor output
uv run romulan hardware monitor --disable

# Request the current CPU address
uv run romulan hardware request-addr
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
| `hardware upload` | `<bin_path> [--port] [-v]` | Upload a ROM binary via the framed protocol |
| `hardware capture` | `--max-cycles <N> [--port] [-v]` | Capture CPU bus cycles until STP or max cycles reached |
| `hardware monitor` | `--enable \| --disable [--port] [-v]` | Toggle unstructured ASCII monitor output |
| `hardware reset` | `--assert \| --release [--port] [-v]` | Hold or release the CPU reset line |
| `hardware request-addr` | `[--port] [-v]` | Request the current CPU address |

| Flag | Description | Default |
|------|-------------|---------|
| `--verbose`, `-v` | Print every JSON message sent and received over the serial protocol | — |
| `--timeout` | Serial/frame timeout in seconds (applies to all hardware commands) | `30.0` |

#### Verbose example

```bash
$ uv run romulan hardware request-addr --verbose
[HW] Opened /dev/ttyACM0 @ 115200
[HW] CALL request_addr()
[HW] SEND: {"cmd": "request_addr"}
[HW] RECV: {"addr": 32768}
[HW] RET request_addr -> 32768
Current CPU address: 0x8000
```

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

### Framed Protocol (v1)

The hardware API uses a byte-level framed protocol for robust JSON communication. Every JSON payload includes `"v": 1` to identify the protocol version.

**Frame sequence:**

| Step | Direction | Byte | Meaning |
|------|-----------|------|---------|
| 1 | Host → Pico | `ENQ` (0x05) | Start frame |
| 2 | Host → Pico | `STX` (0x02) | Payload follows |
| 3 | Pico → Host | `ACK` (0x06) | Ready for payload |
| 4 | Host → Pico | JSON bytes | Command or response |
| 5 | Host → Pico | `EOT` (0x04) | End of payload |
| 6 | Pico → Host | `ACK` / `NACK` | Accepted or rejected |

The client resyncs on `ENQ`/`STX` and discards stray bytes (monitor lines, echoed ACKs) while waiting.

**ROM upload** uses a three-phase `upload_rom` command with base64-encoded chunks (max 1,476 raw bytes per chunk):

1. `{"v":1,"cmd":"upload_rom","action":"begin","size":32768}`
2. `{"v":1,"cmd":"upload_rom","action":"chunk","offset":N,"data":"<base64>"}` — repeated until all 32,768 bytes are sent
3. `{"v":1,"cmd":"upload_rom","action":"commit"}` — firmware validates and activates the image

**Bus capture** uses the `read` command. The host sends `{"v":1,"cmd":"read","until":"stp","max_cycles":N}` and then receives streaming event frames until a `done` event arrives:

- `{"type":"event","event":"cycle",...}` — one CPU bus cycle
- `{"type":"event","event":"done",...}` — capture finished (STP hit, max cycles, or error)

**Supported commands:**

| Command | Purpose |
|---------|---------|
| `upload_rom` | Upload 32 KB ROM image (begin / chunk / commit) |
| `reset` | Assert or release CPU reset |
| `monitor` | Enable or disable ASCII monitor output |
| `request_addr` | Read current CPU address |
| `read` | Capture CPU bus cycles until STP or max cycles |
| `status` | Query firmware state (clock rate, ROM active, reset, etc.) |

## Project Structure

```
romulan/
├── src/romulan/
│   ├── main.py          # CLI entry point and argument parsing
│   ├── build_rom.py     # Hex dump parser, ROM builder, and opcode validation
│   ├── upload_rom.py    # Serial port detection and plain-text upload protocol
│   ├── hardware_api.py  # Framed serial protocol client (v1)
│   └── protocol_v1.py   # Protocol v1 JSON envelope parser and request builder
├── tests/
│   ├── test_main.py                  # Parser, builder, CLI, and upload tests
│   ├── test_hardware_api.py          # Hardware API client and mock serial tests
│   ├── test_protocol_v1.py           # Protocol v1 frame parsing and request building
│   ├── test_verify_instructions.py   # 65C02 opcode validation tests
│   └── test_invalid_instruction_error.py
├── demo.txt             # Sample annotated hex dump
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
- ROM building (valid builds, vector validation)
- CLI behavior (build-only, upload-only, build+upload, error handling)
- Port detection (single Pico, multiple Picos, no Pico found)
- Protocol v1 JSON envelopes (version checks, status/upload/cycle/done parsing)
- Framed serial protocol (frame construction, chunked upload, error handling)
- Hardware API client (mock serial interactions for upload, capture, reset)
- 65C02 opcode validation (`verify_instructions`, `InvalidInstructionError`)

## Troubleshooting Serial Communication

If a hardware command fails, run it again with `--verbose` (or `-v`) to see the exact messages sent and received:

```bash
$ uv run romulan hardware request-addr --verbose
[HW] Opened /dev/ttyACM0 @ 115200
[HW] CALL request_addr()
[HW] SEND: {"cmd": "request_addr"}
[HW] RECV: {"addr": 32768}
[HW] RET request_addr -> 32768
Current CPU address: 0x8000
```

### Reading the verbose output

| `[HW]` line | Meaning |
|-------------|---------|
| `Opened ...` | Serial port connected successfully |
| `CALL ...`  | Entering a HardwareAPI method |
| `SEND: ...` | Exact JSON (or binary summary) sent to the Pico |
| `RECV: ...` | Exact JSON response from the Pico |
| `RET ...`   | Method returned successfully |
| `ERROR: timed out ...` | Pico didn't respond in time — check cable, firmware, or port |
| `ERROR: Pico responded with NACK` | Pico rejected the command — check firmware version |
| `SEND: <binary, N bytes>` | ROM upload payload (not printed as raw hex) |

## Hardware Notes

### Serial Port Detection

- **Linux:** `/dev/ttyACM*` or `/dev/ttyUSB*`
- **macOS:** `/dev/cu.usbmodem*` or `/dev/tty.usbmodem*`
- **Windows:** `COM*` (detected via `pyserial` port enumeration)

If auto-detection fails or finds multiple ports, specify the port explicitly with `--port`.

### Firmware Compatibility

This toolchain supports two modes:

- **Standard plain-text mode:** commands `loadbin`, `rom`, `roms`, `r0`, `r1`, `c100`, `watch`
- **Framed protocol v1 mode:** commands `upload_rom`, `reset`, `monitor`, `read`, `request_addr`, `status`

The standard CLI uses plain-text mode. The `romulan hardware` subcommands use framed protocol v1.

### Safety

Both upload protocols assert CPU reset before writing to ROM and release it only after the new image is active. This prevents the 65C02 from reading inconsistent data during the upload.

