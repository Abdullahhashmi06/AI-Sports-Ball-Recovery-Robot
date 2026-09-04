# tests

Automated tests and test plans: unit tests, integration tests, and field tests.

## Status

Laptop-side protocol tests exist (run from the repository root):

```bash
python -m unittest discover -s tests -v
```

- `test_protocol.py` — pure protocol helpers: framing, schema validation, seq handling
- `test_laptop_link.py` — `LaptopLink` flows against a scripted fake ESP32 (`fake_esp32.py`): boot, RESP_OK/RESP_ERR, NOT_READY, e-stop/comms-loss latch + CMD_RESET re-arm, retransmission, ESP32-lost handling

ESP32-side protocol tests live in `firmware/test/` (PlatformIO native: `pio test -e native`).  No AI, navigation or motor code is tested here yet.