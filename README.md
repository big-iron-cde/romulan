# Romulan

> Host client for the [**Piclone**](https://github.com/big-iron-cde/piclone) 65C02 system — assembles ROM images from annotated hex dumps and talks to the Pico over the framed v1 JSON Hardware API (USB serial).

[![Docs](https://github.com/big-iron-cde/piclone/actions/workflows/docs.yml/badge.svg)](https://github.com/big-iron-cde/romulan)
[![Documentation](https://img.shields.io/badge/docs-GitHub%20Pages-blue)](https://big-iron-cde.github.io/romulan/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](https://github.com/big-iron-cde/romulan/blob/main/LICENSE)

**Full documentation:** [romulan.big-iron.dev](https://romulan.big-iron.dev)

## Features

- **Parse annotated hex dumps** — address, byte, and optional comment per line
- **Build 32 KB ROM images** — auto-fill with NOPs (`$EA`), validate reset/IRQ vectors
- **Framed Hardware API (v1)** — JSON over ENQ/STX/ACK/EOT for scripted control and ROM upload
- **Bus capture** — stream CPU cycles until `STP` or a cycle limit
- **Cross-platform port detection** — auto-detect the Pico on Linux, macOS, and Windows
- **65C02 opcode validation** — catch undefined opcodes before they reach hardware

## Installation

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/):

```bash
git clone https://github.com/big-iron-cde/romulan.git
cd romulan
uv sync
```

## Quick Usage

Build a ROM from an annotated hex file:

```bash
uv run romulan demo.txt --build
```

Build and upload in one step (framed Hardware API):

```bash
uv run romulan demo.txt --build --upload
```

Upload an existing binary:

```bash
uv run romulan --upload
```

### Hardware API commands

The `--port` flag is optional when exactly one Pico is connected. Add `--verbose` (`-v`) to see protocol traffic.

```bash
uv run romulan hardware upload bin/rom.bin
uv run romulan hardware capture --max-cycles 500

# Use a longer timeout for slow operations (e.g. large uploads or long captures)
uv run romulan hardware capture --max-cycles 500 --timeout 45

# Hold CPU in reset
uv run romulan hardware reset --assert
uv run romulan hardware reset --release
uv run romulan hardware monitor --disable
uv run romulan hardware request-addr

# Read back bytes from the loaded ROM image (CPU $F000 == offset 0x7000)
uv run romulan hardware peek --offset 0x7000 --count 16
```

### Python client

```python
from romulan.hardware_api import HardwareAPI

with HardwareAPI("/dev/ttyACM0") as api:
    print(api.status())
    api.reset(assert_reset=True)
    api.upload_rom(open("bin/rom.bin", "rb").read())
    # Verify the byte at CPU $F000 before releasing reset
    print(api.peek(offset=0x7000, count=16).data.hex())
    api.reset(assert_reset=False)
    capture = api.read_until_stp(max_cycles=500)
    print(capture.reason, len(capture.cycles))
```

### Annotated hex format

Each line is `address`, `byte`, and an optional `@ comment`:

```text
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
| `--upload` | Upload the ROM image to the Pico (framed Hardware API) | — |
| `-o, --output` | Output ROM binary path | `bin/rom.bin` |
| `--port` | Serial port for the Pico (auto-detected if omitted) | Auto-detect |

At least one of `--build` or `--upload` is required, but `--upload` can only be used after a successful `--build` or if a valid ROM binary already exists at the output path.

### Hardware API

Romulan speaks the Piclone firmware's **v1 JSON protocol** over USB-CDC at 115200 baud. Each transaction uses byte-level framing (ENQ → STX → ACK → payload → EOT → ACK/NACK); all payloads include `"v": 1`.


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

Full firmware-side protocol reference: [Piclone Hardware API docs](https://big-iron-cde.github.io/piclone/hardware-api.html).

#### Verbose example

```bash
$ uv run romulan hardware request-addr --verbose
{"type":"port_detected","port":"/dev/ttyACM0","auto_detected":true}
{"type":"call","method":"request_addr"}
{"type":"send","payload":{"v":1,"cmd":"request_addr","id":"abc123"}}
{"type":"ack"}
{"type":"ack"}
{"type":"recv","payload":{"v":1,"ok":true,"addr":"8000"}}
{"type":"ret","method":"request_addr","result":32768}
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


## Documentation

The complete documentation — getting started, CLI reference, Hardware API client guide, and Python API reference — is published at **https://big-iron-cde.github.io/romulan/**.

Build and view locally:

```bash
make docs-serve    # build + serve at http://127.0.0.1:8000
make docs          # build only → docs/_build/html
```

Or manually:

```bash
uv sync --group docs
uv run sphinx-build -W docs docs/_build/html
uv run python -m http.server 8000 --directory docs/_build/html
```

## Testing

```bash
uv run pytest
```

## License

Released under the [MIT License](https://github.com/big-iron-cde/romulan/blob/main/LICENSE).
