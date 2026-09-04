# firmware

ESP32 firmware for low-level robot control.

## Subdirectories

- `motor_control/` — Drive motors, encoder feedback, PID, ball intake and dispensing servos
- `sensors/` — Ultrasonic/ToF sensors, MPU6050 IMU
- `communication/` — Protocol between the laptop (AI) and the ESP32 (control)

## Status

Bring-up phase: the laptop ↔ ESP32 communication skeleton is in place (PlatformIO project).

- `platformio.ini` — env `esp32dev` (firmware) and env `native` (host unit tests)
- `src/main.cpp` — boot/loop glue + `DeviceHardware` stub: **no motors/sensors attached yet**, so actuation answers "not implemented" and telemetry carries placeholder values (marked `TODO(P5)`)
- `lib/protocol/` — the protocol engine (parses/validates messages, READY/NOT_READY gating, e-stop + comms-loss latches, CMD_RESET re-arm, duplicate suppression, 20 Hz TELE + heartbeats); compiled into both the firmware and the host tests
- `test/` — native unit tests for the engine

Build/run: `pio run` · `pio run -t upload` · `pio test -e native`.

The topical subdirectories (`communication/`, `motor_control/`, `sensors/`) remain the future homes of the subsystem components; the skeleton deliberately uses the standard PlatformIO layout first.