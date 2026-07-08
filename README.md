# Romulan

> Host client for the [**Piclone**](https://github.com/big-iron-cde/piclone) 65C02 system — assembles ROM images from annotated hex dumps and talks to the Pico over the framed v1 JSON Hardware API (USB serial).

[![Docs](https://github.com/big-iron-cde/piclone/actions/workflows/docs.yml/badge.svg)](https://github.com/big-iron-cde/romulan)
[![Documentation](https://img.shields.io/badge/docs-GitHub%20Pages-blue)](https://big-iron-cde.github.io/romulan/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](https://github.com/big-iron-cde/romulan/blob/main/LICENSE)

**Full documentation:** [big-iron-cde.github.io/romulan](https://romulan.big-iron.dev)

## Features

- **Parse annotated hex dumps** — address, byte, and optional comment per line
- **Build 32 KB ROM images** — auto-fill with NOPs (`$EA`), validate reset/IRQ vectors
- **Plain-text upload** — legacy `loadbin` protocol with safe reset/ROM sequencing
- **Framed Hardware API (v1)** — JSON over ENQ/STX/ACK/EOT for scripted control
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

Build and upload in one step (plain-text protocol):

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
uv run romulan hardware reset --assert
uv run romulan hardware reset --release
uv run romulan hardware monitor --disable
uv run romulan hardware request-addr
```

### Python client

```python
from romulan.hardware_api import HardwareAPI

with HardwareAPI("/dev/ttyACM0") as api:
    print(api.status())
    api.reset(assert_reset=True)
    api.upload_rom(open("bin/rom.bin", "rb").read())
    api.reset(assert_reset=False)
    capture = api.read_until_stp(max_cycles=500)
    print(capture.reason, len(capture.cycles))
```

## Hardware API

Romulan speaks the Piclone firmware's **v1 JSON protocol** over USB-CDC at 115200 baud. Each transaction uses byte-level framing (ENQ → STX → ACK → payload → EOT → ACK/NACK); all payloads include `"v": 1`.

| Command | Purpose |
|---------|---------|
| `upload_rom` | Upload 32 KB ROM (begin / chunk / commit) |
| `reset` | Assert or release CPU reset |
| `monitor` | Enable or disable ASCII bus monitor |
| `request_addr` | Read current CPU address |
| `read` | Capture bus cycles until STP or max cycles |
| `status` | Query firmware state |

Full firmware-side protocol reference: [Piclone Hardware API docs](https://big-iron-cde.github.io/piclone/hardware-api.html).

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
