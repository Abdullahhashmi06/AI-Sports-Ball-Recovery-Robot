# integration/communication — Laptop ↔ ESP32 link (laptop side)

**Bring-up skeleton.** Establishes and tests the communication boundary between
the laptop (Python) and the ESP32 (firmware) exactly as specified in
`docs/COMMUNICATION_PROTOCOL.md` (protocol version 1).

Contains **no** AI, navigation, motor, or ball-acquisition logic. The AI,
navigation, and state-machine modules will reuse `LaptopLink` later
(`docs/TEAM_INTERFACE_CONTRACT.md` §5 I-7).

## Files

| File | Purpose |
|---|---|
| `protocol.py` | Protocol constants, message builders, schema validation, framing encode/decode. Pure Python, no I/O — unit-testable without hardware. |
| `transport.py` | Line-oriented transport interface: `SerialTransport` (pyserial, lazy import) and `create_memory_pair()` for tests. |
| `laptop_link.py` | `LaptopLink`: the reusable laptop-side endpoint (seq numbers, acknowledged commands, heartbeat, liveness watchdog). |
| `demo_laptop.py` | Bench bring-up console: run flows A / reset / watch TELE against the real ESP32. |
| `requirements.txt` | `pyserial` — needed only to talk to real hardware. |

## Quick start

Run the unit tests (standard library only — no pip install needed):

```bash
# from the repository root
python -m unittest discover -s tests -v
```

Test against real hardware (ESP32 running `firmware/`):

```bash
pip install -r integration/communication/requirements.txt
python -m integration.communication.demo_laptop --port COM5        # Windows
python -m integration.communication.demo_laptop --port /dev/ttyUSB0
```

## Minimal API

```python
from integration.communication.laptop_link import LaptopLink, CommandError
from integration.communication.transport import SerialTransport

link = LaptopLink(SerialTransport("/dev/ttyUSB0", 115200))
link.start()
link.wait_for_event("EVT_BOOT", timeout=5.0)          # flow A: laptop waits
resp = link.command("CMD_RESET", scope="all")         # RESP_OK / RESP_ERR
resp = link.command("CMD_MOVE", lin=0.0, ang=0.0)     # navigation later
link.stop()
```

`command()` raises `CommandError` (with `.code`) on `RESP_ERR`, `LinkTimeout`
on no-response-after-retransmit, and `Esp32Lost` when the link is frozen.
Commands are schema-validated on the laptop **before** transmission, so an
out-of-range `CMD_MOVE` is never put on the wire.

Callbacks may be attached: `on_tele`, `on_event`, `on_heartbeat`,
`on_esp32_lost`, `on_esp32_restored`, `on_protocol_mismatch`.

## Implementation notes (skeleton decisions)

These are *implementation details*, not protocol decisions — see the open
decisions in `docs/COMMUNICATION_PROTOCOL.md` §14 and
`docs/TEAM_INTERFACE_CONTRACT.md` §12:

- The laptop-side package lives under `integration/` (Person 1 owns
  laptop↔ESP32 integration/bring-up); the ESP32 side lives in `firmware/`.
- The laptop never sends schema-invalid messages (validated locally); it also
  counts/discards malformed lines without responding (protocol §2/§9), and
  freezes command issuance on a `v` mismatch (protocol §13).
- Missing required fields are treated as `PARSE` (not `RANGE`, which is
  reserved for out-of-range values) — matching the ESP32 engine in
  `firmware/lib/protocol`.

## Cross-language conformance — next validation step (TODO)

Golden JSON vectors are locked on the Python side (`tests/test_protocol.py`,
`TestGoldenWireBytes`). Byte-level conformance between the Python encoder
and the C++ engine (`firmware/lib/protocol`) is **not yet proven by an
automated test**: the Python suite talks to a Python test double, and the
C++ suite runs independently. Before the first integration run, verify on
the bench that the real ESP32 accepts exactly these bytes (watch the serial
monitor / `demo_laptop` output), and consider adding a shared byte fixture
once both sides are stable. TODO.
