# tests

Automated tests and test plans: unit tests, integration tests, and field tests.

## Status

Laptop-side protocol tests exist (run from the repository root):

```bash
python -m unittest discover -s tests -v
```

- `test_protocol.py` — pure protocol helpers: framing, schema validation, seq handling
- `test_laptop_link.py` — `LaptopLink` flows against a scripted fake ESP32 (`fake_esp32.py`): boot, RESP_OK/RESP_ERR, NOT_READY, e-stop/comms-loss latch + CMD_RESET re-arm, retransmission, ESP32-lost handling
- `test_navigation_state.py` / `test_navigation_zones.py` / `test_navigation_planning.py` — laptop-side navigation foundation: world-frame types (TEAM_INTERFACE_CONTRACT §6.1), zone configuration/selection, deterministic planning abstraction. No hardware values invented (OD-12/OD-13 gated).
- `test_navigation_telemetry.py` — raw protocol `TELE` → `TelemetrySnapshot` bridge; fixtures in `telemetry_fixtures.py` are **SIMULATION / TEST ONLY** synthetic data, never real measurements.

ESP32-side protocol tests live in `firmware/test/` (PlatformIO native: `pio test -e native`).  No AI/perception or motor code is tested here yet.