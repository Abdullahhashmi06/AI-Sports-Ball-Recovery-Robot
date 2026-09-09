# PROJECT STATUS

**Snapshot date:** 2026-09-09 · **Branch:** `abd/system-integration` · **Python tests:** 388 OK · **ESP32 native tests:** 39 OK · **Firmware build (esp32dev):** SUCCESS

Legend — read the labels literally:

| Label | Meaning |
|---|---|
| **DONE** | Implemented, in the working tree, with tests. |
| **PROVEN** | Measured by a recorded experiment whose artifacts/reports exist in the repo (or are reproducible from gitignored tooling). |
| **PLANNED** | A written, frozen plan exists. Nothing has been run. |
| **BLOCKED ON HARDWARE** | Cannot proceed until physical equipment is bought/available. |
| **OPEN DECISION** | Tracked as OD-01…OD-16 in `docs/DECISION_LOG.md` §4. Must not be resolved by assumption. |

> This document is a status snapshot, not an authoritative spec. The authoritative
> documents are `docs/ARCHITECTURE.md`, `docs/COMMUNICATION_PROTOCOL.md`,
> `docs/TEAM_INTERFACE_CONTRACT.md`, `docs/DECISION_LOG.md`.

---

## Executive Summary

The laptop-side software foundation for the ball-recovery robot is built and tested
end-to-end in software: communication protocol + safety engine (both sides), world-frame
localization/planning abstractions, an application state machine wired into a system
coordinator with a motion gate, a validated perception boundary, and a **real, trained ML
ball detector (V3)** with a measured evaluation and error analysis. No physical hardware
exists yet, so **nothing physical is validated** and movement commands are deliberately
deferred. The single highest-value next action is **buying the data-collection camera kit**
(`HARDWARE_PROCUREMENT.md`) and collecting robot-domain footage to (a) validate V3 on real
court data and (b) fine-tune it.

---

## Completed — DONE

| Milestone | What was built | Verified by |
|---|---|---|
| Communication bring-up skeleton | Protocol v1 framing/validation, transport, `LaptopLink` (laptop); ESP32 protocol + safety engine (latched fail-safes) | Laptop-side unit tests + 39 native PlatformIO tests; esp32dev build |
| Localization/navigation foundation | World-frame state (`Pose`, `WorldPoint`), geometry helpers, configurable zones, geometry-level planner, `MovementIntent` (no velocities/PWM) | Navigation test suites |
| System-integration foundation | `RobotSystemState` telemetry hub, command boundary (STOP expressible, MOVE **DEFERRED**), `SystemCoordinator`, `ApplicationOrchestrator`, deterministic replay/scenario harness | `tests/test_system_*`, `tests/test_application*` |
| Application state machine | Pure lifecycle/safety-reflection/mission-context machine; e-stop + fault latching; only a verified `CMD_RESET scope=all` records re-arm | `tests/test_state_machine.py` + coordinator integration tests |
| Perception seam | `BallObservation`/`ObstacleObservation`/`PerceptionFrame` validation, exit-hint path, objective derivation — detector-agnostic | `tests/test_perception*` |
| Ball Detection V1 | Train/eval/inference pipeline + perception-boundary glue | Suite (synthetic fixtures) |
| Ball Detection V3 (corrected) | COCO cat-37 dataset build + verification guards, YOLO11n transfer-learning training, formal evaluation, error analysis | Suite + recorded reports (see Current ML Baseline) |
| Robot dataset tooling | Frame extraction, label validation, session-grouped splits, dedupe, provenance, whole-dataset audit, labeling guide + QC workflow | `tests/test_robot_dataset.py` + end-to-end dry run (§4.5 of `ai/detection/README.md`) |

## Currently Proven — PROVEN

- The **software pipeline works end-to-end**: synthetic TELE → `TelemetrySnapshot` →
  `RobotSystemState` → state machine → navigation → `MovementIntent` → command boundary →
  motion gate → `LaptopLink` — with malformed-input, missing-pose, stale-telemetry, and
  fault scenarios covered.
- **Safety invariants hold in software**: e-stop → `CMD_STOP` only (never MOVE, never
  gate-blocked); normal STOP does not latch a fault; missing/stale telemetry blocks motion;
  ESP32-reported faults block motion and only a verified reset re-arms; no path bypasses
  `LaptopLink`.
- **V3 detection on public data** (all numbers public-dataset, NOT robot):
  test split (42 imgs / 66 balls) @ 640 px, conf 0.25, IoU 0.5 → **precision 0.786,
  recall 0.500, small-ball recall 0.474**; test mAP50 0.547, mAP50-95 0.378
  (val split, epoch 15: mAP50 0.604, mAP50-95 0.414);
  workstation-CPU latency ≈ 55 ms/img.
- **640 px ≫ 320 px for this task** (measured on correct labels): +65 % relative recall at
  equal precision, 1.9× small-ball recall, only ~1.07× latency. 640 px is the operating
  resolution for future work.
- **Dataset tooling survives a full dry run** (video → frames → labels → validation →
  session-grouped split → dedupe → provenance) — the tooling is proven, not the robot.

## Not Yet Proven — PLANNED / BLOCKED ON HARDWARE

| Item | Status |
|---|---|
| V3 detects balls **on a real court** (domain shift: lighting, court background, motion blur, robot camera height) | **BLOCKED ON HARDWARE** (camera) — this is the explicit purpose of `HARDWARE_PROCUREMENT.md` |
| V4 full-data fine-tune | **PLANNED** only (`ai/detection/README.md` §4.7); needs an uninterrupted ~24 h machine window |
| CMD_MOVE generation / any motion | **BLOCKED** — OPEN DECISIONS OD-07 (speeds/ramps) + OD-13 (IMU/theta convention); firmware `SetDriveVelocity()` is a skeleton |
| Obstacle avoidance, search directives, collection, dispensing | **BLOCKED** — OD-02/OD-03/OD-04/OD-08/OD-10/OD-11 + hardware |
| Encoder odometry, IMU heading | **BLOCKED** — OD-12/OD-13 require bench measurement |
| Any deployment/robot-readiness claim | **NOT PROVEN — do not make it.** |

## Open Decisions (all remain OPEN)

OD-01 (ball world pose / `BALL_FOUND` threshold) · OD-02 (obstacle delivery form) ·
OD-03 (search split) · OD-04 (intake pose) · OD-05 (latency budget) · OD-06 (RESP
ownership) · OD-07 (max speeds/ramps for ±1) · OD-08 (hard-stop threshold, forward
sensors) · OD-09 (ultrasonic layout) · OD-10 (verification sensor) · OD-11 (dispense
timing) · OD-12 (encoder counts/m) · OD-13 (IMU/yaw/theta) · OD-14 (e-stop hardware) ·
OD-15 (USB serial specifics) · OD-16 (rate adequacy).

Resolving any of them requires the process in `docs/DECISION_LOG.md` §5–§6 (record the
decision, update the authoritative document). None may be settled by a default in code.

## Hardware Blockers (see `HARDWARE_PROCUREMENT.md` for the buy list)

1. **Camera + capture kit** — blocks robot-domain data collection, V3 domain validation,
   and every downstream perception decision (OD-01).
2. **ESP32 dev board + USB cable** — blocks firmware bench bring-up (protocol Stage 1 in
   `docs/STAGE_1_BRINGUP_RUNBOOK.md`), OD-14/OD-15 investigations.
3. Drive/battery/intake hardware — **deliberately NOT specifiable yet**; see
   `HARDWARE_PROCUREMENT.md` §C for why.

## Immediate Next Steps

1. **Review + commit** the uncommitted working tree (perception seam, `ai/detection/`,
   application orchestrator, tests, docs). 388 tests pass; authoritative docs are clean.
2. **Buy the §A camera kit** (`HARDWARE_PROCUREMENT.md`) — this is the critical path.
3. Rehearse the labeling workflow on any video (`robot_dataset` tooling, no hardware
   needed).
4. When camera arrives: run the capture matrix (`ai/detection/README.md` §4.6), label per
   §5, QC per §5.1, then evaluate V3 on the robot-domain test set.
5. Only after that: V4 per the frozen plan (§4.7), and ESP32 Stage-1 bench bring-up per the
   runbook.

## Current ML Baseline (V3 — quote exactly, with the public-dataset caveat)

- Model: YOLO11n (COCO-pretrained, transfer learning), single class `ball`.
- Data: corrected COCO sports-ball dataset — 4,262 train / 127 val / 42 test images,
  6,610 boxes, every label verified IoU≥0.5 against official COCO cat-37 annotations.
- Config: imgsz 640, fraction 0.20, epochs 15 (best = epoch 15), batch 8, patience 5,
  seed 20260906, CPU.
- Weights: `runs/ball_v3/weights/best.pt` (gitignored; regenerate via the §6 commands in
  `ai/detection/README.md`).
- Test (conf 0.25 / IoU 0.5): P 0.786 · R 0.500 · small-ball R 0.474 · normal-ball R 0.667.
  Test (formal eval): mAP50 0.547 · mAP50-95 0.378. Val (epoch 15): mAP50 0.604 ·
  mAP50-95 0.414. Latency ≈ 55 ms/img on a workstation CPU (**not** a
  robot budget; OD-05 open).
- Known limits: 8/66 test balls invisible even at conf 0.01; small-ball confidence
  calibration is the dominant failure mode; FP count is small and mostly low-confidence.
- **`validation_domain = "public_dataset"`; `robot_validation_performed = false`.**

## Safety / Scope Constraints (standing rules for every contributor)

- Laptop never sends PWM/GPIO/motor commands; all commands flow through `LaptopLink`.
- `CMD_MOVE` remains **DEFERRED** until OD-07/OD-13 are resolved through the decision log.
- Emergency stop produces `CMD_STOP` only; the laptop never pretends to clear an ESP32
  latch (only a verified `CMD_RESET scope=all` records re-arm).
- No hardware constants (pins, speeds, thresholds, dimensions, calibration) may be
  invented; hardware-dependent work sits behind explicit interfaces/deferred results.
- Synthetic data is always labeled SIMULATION/TEST/REPLAY; public-dataset metrics are never
  quoted as robot performance.
- Datasets, weights, runs, and virtualenvs stay gitignored.
