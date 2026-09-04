# AI-Sports-Ball-Recovery-Robot

An AI-powered autonomous mobile robot that recovers tennis balls during a sports rally. The robot watches the court with a camera, detects and tracks the ball, and when the ball leaves play it navigates autonomously to recover it, avoid obstacles, collect the ball, and return it to a base station for storage and later dispensing.

## High-Level Capabilities

- Monitor an active rally and detect when the ball leaves the court
- Estimate which side/direction the ball exited from
- Navigate autonomously to the surrounding recovery area
- Detect obstacles (people, chairs, bags, etc.) and dynamically reroute
- Search intelligently for a missing ball, expanding the search area if needed
- Detect and physically collect the ball, then verify collection succeeded
- Store collected balls and return to a designated base/collection station
- Dispense stored balls back to players when requested

## Hardware Overview

- **Laptop** — AI / computer-vision inference (ball detection, tracking, rally state)
- **ESP32** — low-level robot control (motors, sensors, communication)
- **Camera** — visual perception
- **DC geared motors with encoders** — drive system
- **Ultrasonic / ToF sensors** — proximity and obstacle detection
- **MPU6050 IMU** — orientation
- **Motor drivers** — motor power stage
- **Front roller-based intake** — ball collection
- **Storage bin + servo-based dispenser** — ball storage and dispensing

## Repository Layout

```
ai/          Python AI / computer-vision work (detection, tracking, rally state)
navigation/  Localization, path planning, obstacle avoidance
firmware/    ESP32 firmware (motor control, sensors, communication)
hardware/    Wiring diagrams, pin assignments, electronics
mechanical/  CAD, 3D-print files, mechanical assembly
integration/ End-to-end integration: combining AI, navigation, and firmware
tests/       Unit, integration, and field tests
docs/        Design docs, meeting notes, reports
media/       Photos, videos, and demo recordings
```

## Implementation Status

The repository now contains the **laptop ↔ ESP32 communication bring-up skeleton**: a laptop-side protocol library (`integration/communication/`), a PlatformIO ESP32 firmware skeleton with a host-testable protocol engine (`firmware/`), and unit/integration tests for both sides. Physical actuation (motors, intake, dispenser), sensor integration, AI/perception, navigation, and the full recovery workflow are **not implemented yet** — see `docs/ARCHITECTURE.md`, `docs/DECISION_LOG.md`, and the in-code `TODO(P5)` / "NOT YET IMPLEMENTED — HARDWARE REQUIRED" markers for what remains.

## Team

5-person university semester project.

## License

TBD.