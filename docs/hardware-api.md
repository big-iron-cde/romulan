# Hardware API Client

Romulan's `HardwareAPI` class wraps the Piclone firmware's **v1 JSON protocol** over a framed USB-serial link. Use it from Python scripts, tests, or the `romulan hardware` CLI subcommands.

For the full firmware-side protocol specification, see the [Piclone Hardware API docs](https://big-iron-cde.github.io/piclone/hardware-api.html).

## Quick start

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

`HardwareAPI` opens the serial port on construction and closes it on exit from a `with` block.

## Output schema (v1)

Everything the `romulan` CLI prints is a single self-contained JSON object per line
(NDJSON), always carrying `"v":1` and `"type"`:

| Type | Shape | Stream |
|------|-------|--------|
| `result` | `{"v":1,"type":"result","cmd":"<cmd>","data":{...}}` | stdout |
| `event` | `{"v":1,"type":"event","event":"<name>","data":{...}}` | stdout (domain events), stderr (trace/port detection) |
| `error` | `{"v":1,"type":"error","error":"<code>","detail":"<msg>"}` (+ optional `"errors":[...]`) | stderr |

```bash
$ uv run romulan hardware reset --assert
{"v":1,"type":"result","cmd":"reset","data":{"asserted":true}}
```

Domain events are emitted for streamed data (one `cycle` event per captured bus
cycle) and for port auto-detection (`port_detected`, stderr). With `--verbose`,
the protocol trace (`open`/`send`/`recv`/`ack`/`call`/`ret`/`resync`, …) is
emitted as `event` objects on stderr, so stdout stays parseable as pure command
output. See `romulan/output.py` for the canonical definition.

## Framed protocol

Every command and response travels inside a byte-level frame:

| Step | Direction | Byte | Meaning |
|------|-----------|------|---------|
| 1 | Host → Pico | `ENQ` (0x05) | Start frame |
| 2 | Host → Pico | `STX` (0x02) | Payload follows |
| 3 | Pico → Host | `ACK` (0x06) | Ready for payload |
| 4 | Host → Pico | JSON bytes | Command or response |
| 5 | Host → Pico | `EOT` (0x04) | End of payload |
| 6 | Pico → Host | `ACK` / `NACK` | Accepted or rejected |

All JSON payloads include `"v": 1`. An optional `"id"` field is echoed in responses.

## Commands

| Command | Purpose |
|---------|---------|
| `upload_rom` | Upload 32 KB ROM (begin → chunk × N → commit) |
| `reset` | Assert or release CPU reset |
| `monitor` | Enable or disable the JSON bus monitor |
| `request_addr` | Read current CPU address |
| `peek` | Read bytes back from the loaded ROM image (`offset`/`count`) |
| `peek` (live) | Live-peek one CPU bus/RAM byte (`addr`; LDA stub, briefly resets CPU) |
| `read` | Capture bus cycles until STP or max cycles |
| `clock` | Set PHI2 clock frequency (0.1–1000 Hz) |
| `status` | Query firmware state (clock, reset, ROM, monitor, last bus sample) |

### ROM upload

The upload is a three-phase sequence with base64-encoded chunks (max 1,476 raw bytes each):

1. `{"v":1,"cmd":"upload_rom","action":"begin","size":32768}`
2. `{"v":1,"cmd":"upload_rom","action":"chunk","offset":N,"data":"<base64>"}` — repeated
3. `{"v":1,"cmd":"upload_rom","action":"commit"}` — returns `reset_vector`

`upload_rom()` disables the JSON monitor and flushes serial input before transferring.

### Bus capture

Send `{"v":1,"cmd":"read","until":"stp","max_cycles":N,"batch_size":32}` and receive batched event frames:

- `{"type":"event","event":"cycles","cycles":[...]}` — up to `batch_size` CPU bus cycles
- `{"type":"event","event":"done",...}` — capture finished

`read_until_stp()` disables the monitor before starting capture. The current PHI2 clock speed is preserved unless you pass `phi2_hz`. Batching reduces USB round trips; the default batch size is `READ_EVENT_BATCH_SIZE` (32).

### Clock

Set the 65C02 PHI2 frequency without starting a capture:

```python
api.set_clock(hz=100.0)
```

Or from the CLI:

```bash
uv run romulan hardware clock --hz 100
```

The supported range is **0.1–1000 Hz**. The `read` command also accepts an
optional `phi2_hz` argument if you want to change the clock and capture in
one step.

### Status

Query the firmware for its current state:

```bash
uv run romulan hardware status
```

This prints the current PHI2 frequency, ROM/reset/monitor state, and the last
bus sample (`last_addr`, `last_data`, `last_rw`).

### Peek (ROM image)

`peek(offset, count)` reads bytes back from the loaded ``rom_image[]`` in Pico
SRAM — useful for verifying an upload landed at the expected offsets before
releasing RESET. `count` is 1–64.

```bash
uv run romulan hardware peek --offset 0x7000 --count 16
```

### Live peek

`live_peek(addr)` asks the firmware to reset the CPU, run `LDA $addr` / `STP` from `$8000`, and return the data byte sampled on the matching address cycle. Use this to read live RAM (e.g. `$4000` after an STA), not a host ROM-image offset. The breadboard must wire **RAM OE# = NOT(RWB)**; with OE# tied high, peeks see open bus (often the address high byte). Requires firmware with live-peek support.

```python
result = api.live_peek(0x4000)
print(f"${result.addr:04X} = ${result.data:02X}")
```

Or from the CLI:

```bash
uv run romulan hardware live-peek --addr 0x4000
```

## Important notes

- Do not open a plain serial monitor on the port while using the framed protocol — unstructured output corrupts framing.
- Disable the JSON monitor before scripted upload or capture (the client methods do this automatically).
- If unstructured lines (monitor output) do precede a response frame, the client resynchronizes by taking the text after the last newline, from the first `{` — this skips both legacy ASCII monitor rows and newline-terminated JSON monitor lines. With `--verbose` this is reported as a `{"v":1,"type":"event","event":"resync","data":{"skipped_bytes":N}}` event. Bytes interleaved *inside* a payload still fail parsing, so keeping the monitor off during scripted sessions remains the recommendation.
- `live_peek` briefly asserts reset around the stub program; do not rely on CPU state surviving a live peek.
- The ROM image in Pico SRAM is lost on power cycle — re-upload after each reboot.

## Python API reference

See the [Python API](python-api.md) page for autodoc of `HardwareAPI`, `protocol_v1`, and related modules.
