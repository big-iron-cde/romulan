# CLI Reference

Romulan provides two interfaces: the **standard workflow** (build/upload flags) and the **`hardware` subcommands** (framed v1 protocol).

## Standard workflow

```bash
uv run romulan [input] [--build] [--upload] [-o OUTPUT] [--port PORT]
```

| Argument / Flag | Description | Default |
|-----------------|-------------|---------|
| `input` | Annotated hex dump file (required with `--build`) | — |
| `--build` | Build a `.bin` ROM image from the input file | — |
| `--upload` | Upload the ROM via the framed Hardware API | — |
| `-o`, `--output` | Output ROM binary path | `bin/rom.bin` |
| `--port` | Serial port (auto-detected if omitted) | auto-detect |
| `--timeout` | Idle timeout in seconds with no framing progress (upload) | `30.0` |
| `--verbose`, `-v` | Print Hardware API NDJSON traces during `--upload` | — |

At least one of `--build` or `--upload` is required.

### Examples

```bash
# Build only
uv run romulan program.txt --build

# Build and upload
uv run romulan program.txt --build --upload

# Upload an existing binary
uv run romulan --upload

# Custom output path
uv run romulan program.txt --build -o output/rom.bin
```

## Hardware subcommands

```bash
uv run romulan hardware <subcommand> [--port PORT] [--verbose]
```

| Subcommand | Arguments | Description |
|------------|-----------|-------------|
| `upload` | `<bin_path>` | Upload a ROM binary via the framed protocol |
| `capture` | `--max-cycles N` | Capture CPU bus cycles until STP or limit |
| `monitor` | `--enable` or `--disable` | Toggle ASCII monitor output |
| `reset` | `--assert` or `--release` | Hold or release CPU reset |
| `request-addr` | — | Read the current CPU address |

| Flag | Description | Default |
|------|-------------|---------|
| `--timeout` | Idle timeout in seconds with no framing/capture progress | `30.0` |
| `--verbose`, `-v` | Print every JSON message sent and received | — |

### Examples

```bash
uv run romulan hardware upload bin/rom.bin --verbose
uv run romulan hardware capture --max-cycles 500
uv run romulan hardware reset --assert
uv run romulan hardware reset --release
uv run romulan hardware monitor --disable
uv run romulan hardware request-addr
```

## Verbose output

When `--verbose` is set on a hardware command, each protocol exchange is logged to stderr as NDJSON:

```
{"type":"call","method":"request_addr"}
{"type":"send","payload":{"v":1,"cmd":"request_addr","id":"abc123"}}
{"type":"ack"}
{"type":"ack"}
{"type":"recv","payload":{"v":1,"ok":true,"addr":"8000"}}
{"type":"ret","method":"request_addr","result":32768}
```

See [Hardware API](hardware-api.md) for protocol details.
