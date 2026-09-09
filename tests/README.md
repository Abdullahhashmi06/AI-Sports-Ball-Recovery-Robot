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
- `test_system_state.py` / `test_system_commands.py` / `test_system_coordinator.py` — integration foundation: laptop-side robot state hub, movement-intent → command boundary (STOP/emergency sendable, MOVE deferred per OD-07/OD-13), the `SystemCoordinator` pipeline + motion gate against a real `LaptopLink` over an in-memory transport (`FakeEsp32`), and policy invariants proving the state machine gates STARTING/faults/latches, emergency stop never moves, normal STOP never latches, local reset never clears the ESP32 latch reflection, and only a verified `CMD_RESET scope=all` records re-arm.
- `test_state_machine.py` — application state-machine skeleton: pure lifecycle/safety/mission axes, conservative transitions (fault/e-stop abort any mission and latch; local reset never clears the ESP32 latch reflection), plus an orchestration integration test driving startup → fault → real `CMD_RESET scope=all` re-arm over a live `LaptopLink` + `FakeEsp32`.
- `telemetry_replay.py` + `test_telemetry_replay.py` — SIMULATION / TEST ONLY deterministic replay/scenario suite: synthetic frames are fed through `SystemCoordinator.ingest_message` (the same intake path real `LaptopLink` data uses), preserving raw encoder/ultrasonic/IMU/fault values with no conversion (OD-12/OD-13). Covers the startup/healthy/fault/latch/clean/re-arm/no-pose/hint/mission/stop/malformed scenario matrix, the `exit_hint_from_payload` perception seam (I-1), and boundary proofs (replay cannot bypass `RobotSystemState`, hints have no command surface, unknown fields/types are ignored).
- `test_ball_detection.py` — Ball Detection V1 (ai/detection/): fake-detector-at-seam unit tests (bbox math, detection validation, payload conversion, malformed output, confidence handling) plus an E2E synthetic flow (detector → perception payload → existing validation boundary → `ApplicationOrchestrator` → objective seam, zero motion commands). The committed suite needs NO ML dependencies; the real ultralytics training/eval proof run lives in `ai/.venv` (see `ai/detection/README.md`).

ESP32-side protocol tests live in `firmware/test/` (PlatformIO native: `pio test -e native`).  Motor code is not tested here yet; the ball-detection training pipeline is exercised separately via `ai/detection/` scripts, not the committed suite.