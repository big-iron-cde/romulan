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
- **Verbose protocol traces** — `-v` / `--verbose` on `--upload` and on `hardware` subcommands (NDJSON SEND/RECV)
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
uv run romulan demo.txt --build --upload -v   # NDJSON protocol trace during upload
```

Upload an existing binary (optional `-v` / `--timeout`):

```bash
uv run romulan --upload
uv run romulan --upload -v --timeout 45
```

`-v` / `--verbose` works on the standard `--upload` path as well as on `romulan hardware …` commands.

### Hardware API commands

The `--port` flag is optional when exactly one Pico is connected. Add `--verbose` (`-v`) for NDJSON protocol traffic on any hardware command (and on `--upload` above).

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

# Live-peek a CPU bus/RAM byte (briefly resets the CPU)
uv run romulan hardware peek --addr 0x4000

# Set the 65C02 clock speed
uv run romulan hardware clock --hz 100

# Query firmware state
uv run romulan hardware status
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
    api.set_clock(hz=100.0)
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
| `--timeout` | Idle timeout (seconds) with no framing progress on upload | `30.0` |
| `--verbose`, `-v` | Hardware API NDJSON traces during `--upload` | — |

At least one of `--build` or `--upload` is required, but `--upload` can only be used after a successful `--build` or if a valid ROM binary already exists at the output path.

### Hardware API

Romulan speaks the Piclone firmware's **v1 JSON protocol** over USB-CDC at 115200 baud. Each transaction uses byte-level framing (ENQ → STX → ACK → payload → EOT → ACK/NACK); all payloads include `"v": 1`.


| Subcommand | Arguments | Description |
|------------|-----------|-------------|
| `hardware upload` | `<bin_path> [--port] [-v]` | Upload a ROM binary via the framed protocol |
| `hardware capture` | `--max-cycles <N> [--port] [-v]` | Capture CPU bus cycles until STP or max cycles reached |
| `hardware monitor` | `--enable \| --disable [--port] [-v]` | Toggle JSON monitor output |
| `hardware reset` | `--assert \| --release [--port] [-v]` | Hold or release the CPU reset line |
| `hardware request-addr` | `[--port] [-v]` | Request the current CPU address |
| `hardware peek` | `--offset <hex> [--count N]` or `--addr <hex> [--port] [-v]` | Read ROM-image bytes (`--offset`) or live-peek one CPU bus/RAM byte (`--addr`) |

| Flag | Description | Default |
|------|-------------|---------|
| `--verbose`, `-v` | Print every JSON message sent and received over the serial protocol | — |
| `--timeout` | Idle timeout in seconds with no framing/capture progress | `30.0` |

Full firmware-side protocol reference: [Piclone Hardware API docs](https://big-iron-cde.github.io/piclone/hardware-api.html).

Captured cycles include `rw`: **0 = read**, **1 = write**. Piclone firmware on Pico 2 **infers** this from **A15** (ROM region = read, RAM region = write), not from a wired RWB sense pin—so STA/store cycles report `rw=1` and opcode fetches report `rw=0`.

`hardware peek --addr` is a **live** bus/RAM read (the firmware runs a short LDA/STP stub and samples the matching cycle). It is not a ROM-image offset read — use `--offset` for that. The CPU is held in reset around the peek. Requires piclone wiring **RAM OE# = NOT(RWB)** and live-peek-capable firmware; the ROM-image firmware does not error on live requests, it answers with misleading ROM data instead.

#### Verbose example

```bash
$ uv run romulan hardware request-addr --verbose
{"v":1,"type":"event","event":"port_detected","data":{"port":"/dev/ttyACM0","auto_detected":true}}
{"v":1,"type":"event","event":"call","data":{"method":"request_addr"}}
{"v":1,"type":"event","event":"send","data":{"payload":{"v":1,"cmd":"request_addr","id":"abc123"}}}
{"v":1,"type":"event","event":"ack"}
{"v":1,"type":"event","event":"ack"}
{"v":1,"type":"event","event":"recv","data":{"payload":{"v":1,"ok":true,"addr":"8000"}}}
{"v":1,"type":"event","event":"ret","data":{"method":"request_addr","result":32768}}
{"v":1,"type":"result","cmd":"request_addr","data":{"addr":"8000"}}
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
