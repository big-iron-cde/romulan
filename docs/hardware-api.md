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
| `monitor` | Enable or disable ASCII bus monitor |
| `request_addr` | Read current CPU address |
| `peek` | Live-peek one CPU bus/RAM byte (LDA stub; briefly resets CPU) |
| `read` | Capture bus cycles until STP or max cycles |
| `status` | Query firmware state (clock, reset, ROM, monitor) |

### ROM upload

The upload is a three-phase sequence with base64-encoded chunks (max 1,476 raw bytes each):

1. `{"v":1,"cmd":"upload_rom","action":"begin","size":32768}`
2. `{"v":1,"cmd":"upload_rom","action":"chunk","offset":N,"data":"<base64>"}` — repeated
3. `{"v":1,"cmd":"upload_rom","action":"commit"}` — returns `reset_vector`

`upload_rom()` disables the ASCII monitor and flushes serial input before transferring.

### Bus capture

Send `{"v":1,"cmd":"read","until":"stp","max_cycles":N}` and receive streaming event frames:

- `{"type":"event","event":"cycle",...}` — one CPU bus cycle
- `{"type":"event","event":"done",...}` — capture finished

`read_until_stp()` disables the monitor before starting capture.

### Live peek

`peek(addr)` asks the firmware to reset the CPU, run `LDA $addr` / `STP` from `$8000`, and return the data byte sampled on the matching address cycle. Use this to read live RAM (e.g. `$4000` after an STA), not a host ROM-image offset. The breadboard must wire **RAM OE# = NOT(RWB)**; with OE# tied high, peeks see open bus (often the address high byte).

```python
result = api.peek(0x4000)
print(f"${result.addr:04X} = ${result.data:02X}")
```

## Important notes

- Do not open a plain serial monitor on the port while using the framed protocol — unstructured output corrupts framing.
- Disable the ASCII monitor before scripted upload or capture (the client methods do this automatically).
- Live `peek` briefly asserts reset around the stub program; do not rely on CPU state surviving a peek.
- The ROM image in Pico SRAM is lost on power cycle — re-upload after each reboot.

## Python API reference

See the [Python API](python-api.md) page for autodoc of `HardwareAPI`, `protocol_v1`, and related modules.
