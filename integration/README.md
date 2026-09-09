# integration

End-to-end integration: connecting the AI subsystem (laptop), navigation, and firmware (ESP32) into a working system, plus demo scripts and bring-up guides.

## Status

Bring-up phase: the **laptop side of the laptop ↔ ESP32 link** lives in
`communication/` (protocol helpers, transport, `LaptopLink`, and the
`demo_laptop` bench console — see its README).

The **integration foundation** lives in `system/` (owned by Person 1):

- `system/state.py` — `RobotSystemState`, the laptop's raw,
  ESP32-authoritative picture of the robot (latest `TELE` snapshot hub +
  composed localization container; never derives a pose from telemetry —
  OD-12/OD-13).
- `system/commands.py` — the command boundary: deterministic mapping of a
  movement intent onto a protocol command. `STOP`/emergency are fully
  expressible and schema-validated; `MOVE` is *deferred* (CMD_MOVE needs
  `lin`/`ang`, gated by OD-07/OD-13 — no values invented).
- `system/coordinator.py` — `SystemCoordinator`: wires `LaptopLink`
  (`on_tele`/`on_event`) into the state hub and **owns an
  `ApplicationStateMachine`** as the laptop policy layer.  It observes
  `EVT_BOOT`, reflects ESP32-reported faults into the machine, runs the
  telemetry → navigation → command pipeline as one `control_tick`, and
  gates normal motion through the machine (`STARTING` / `FAULT_LATCHED`)
  plus the physical checks (link not started/frozen, no telemetry, ESP32
  faults, caller-supplied freshness budget). `complete_startup()` is the
  explicit STARTING → READY entry (protocol flow A); `apply()` issues only
  through `LaptopLink`, the emergency stop always bypasses the gate and
  latches laptop policy; `esp32_reset_all()` (`CMD_RESET scope=all`) is the
  only recorded re-arm back to NOMINAL. `ingest_message()` is a
  deterministic in-process intake (same path real link data takes) for
  synthetic/replay scenarios, and `status()` returns a read-only
  observability snapshot (lifecycle / safety / mission / freshness /
  faults / gate reason).
- `system/state_machine.py` — pure application state-machine skeleton
  (stdlib only, no transport/threads): three explicit axes — lifecycle
  (`STARTING`/`READY`), safety reflection (`NOMINAL`/`FAULT_LATCHED`, where
  only a caller-performed `CMD_RESET scope=all` re-arm is recorded, never
  cleared locally), and mission context (`IDLE`/`NAVIGATING`/`SEARCHING`/
  `APPROACHING`/`COLLECTING`/`RETURNING`, coarse `ARCHITECTURE.md` §9
  labels with no attached behaviour). Fault/e-stop abort any mission and
  latch; undefined transitions are rejected explicitly. Entering a mission
  never generates commands — CMD_MOVE stays deferred downstream.

AI perception is now **started but not robot-ready**: `perception/` holds the
software-only observation models, validation boundary, and objective seams,
and `ai/detection/` adds the **Ball Detection training/inference pipeline**
(V1→V3; V3 is the current public-dataset baseline — YOLO11n transfer learning,
corrected COCO sports-ball dataset, NOT robot-validated; see
`ai/detection/README.md`).
Obstacle avoidance and the full §9 behaviour state machine
(perception/task-directive staging, OD-03-gated) are **not implemented yet**
— they will build on the foundation above.

Run the laptop-side tests from the repository root:

```bash
python -m unittest discover -s tests -v
```