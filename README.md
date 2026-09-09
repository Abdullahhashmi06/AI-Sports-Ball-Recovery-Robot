# AI-Sports-Ball-Recovery-Robot

An AI-powered autonomous mobile robot that recovers tennis balls during a sports rally. The robot watches the court with a camera, detects and tracks the ball, and when the ball leaves play it navigates autonomously to recover it, avoid obstacles, collect the ball, and return it to a base station for storage and later dispensing.

> **PARTNER QUICK START (5 minutes)**
>
> 1. **Read first:** this README → `PROJECT_STATUS.md` (status labels) → `HARDWARE_PROCUREMENT.md` (what to buy).
> 2. **Already complete:** laptop-side software architecture, communication skeleton, perception seam, and a trained ball detector (V3) validated on public data. 388 tests pass. Nothing physical has been built or validated.
> 3. **Do NOT modify:** `docs/ARCHITECTURE.md`, `docs/COMMUNICATION_PROTOCOL.md`, `docs/TEAM_INTERFACE_CONTRACT.md`, `docs/DECISION_LOG.md` (authoritative), and the frozen safety/ownership rules in `PROJECT_STATUS.md` §Safety — including the DEFERRED `CMD_MOVE` gate.
> 4. **Buy:** see **Hardware priority** below — the camera kit is the urgent, project-critical purchase.
> 5. **Bring back from hardware testing:** measured camera resolution/FPS/exposure behavior, per-session lighting notes, actual capture distances — these feed `PROVENANCE.md` and resolve nothing by assumption (all OD-01…OD-16 stay open until measured).
> 6. **Status + procurement:** `PROJECT_STATUS.md` and `HARDWARE_PROCUREMENT.md` are the two living status documents; `ai/detection/README.md` has the full ML history and run commands.

## A. What has been built (all software-only, 388 tests passing)

| Milestone | State |
|---|---|
| System/integration skeleton: protocol v1, `LaptopLink`, ESP32 protocol/safety engine (39 native PlatformIO tests, esp32dev build) | DONE — committed |
| Localization/navigation foundation: world-frame state, zones, geometry-level planner, `MovementIntent` (never actuates) | DONE — committed |
| Application state machine + coordinator + orchestrator: lifecycle/safety-reflection/mission axes, motion gate, emergency-stop path | DONE — committed |
| Perception seam: validated `PerceptionFrame`/`BallObservation`, exit-hint path, objective derivation (detector-agnostic) | DONE — working tree |
| V1 ball detector | DONE — proved the train→evaluate→inference→perception plumbing on synthetic fixtures only |
| V2 attempt + discovered dataset defect | DONE (defect found, fixed, guarded); V2 numbers invalid; dataset quarantined |
| Corrected V3 dataset (COCO sports-ball, cat-37) + V3 training/evaluation + error analysis | DONE — current ML baseline |
| Robot dataset tooling: frames → validate → session-grouped split → dedupe → provenance → audit, dry-run verified end-to-end | DONE |
| Physical actuation, obstacle avoidance, IMU/encoder localization, search, collection, dispensing | BLOCKED ON HARDWARE / OPEN DECISIONS |

## B. Current V3 baseline (honest numbers)

| What | Numbers | Source/tool |
|---|---|---|
| **Validation** (during training, 127 val images, epoch 15) | P 0.717 · R 0.589 · mAP50 0.604 · mAP50-95 0.414 | `runs/ball_v3/results.csv` |
| **Test — formal evaluation** (42 test images / 66 balls) | P 0.749 · R 0.500 · mAP50 0.547 · mAP50-95 0.378 | `evaluate.py` → `runs/ball_v3/eval_v3_test.json` |
| **Test — error analysis** @ conf 0.25, IoU 0.5 | P 0.786 · R 0.500 · TP/FP/FN 33/9/33 · **small-ball recall 0.474** (normal-ball 0.667) | `error_analysis.py` |
| Latency | ≈ 55 ms/img **workstation CPU — NOT a robot latency budget** (OD-05 open) | eval report |
| Domain | **public-dataset validation only** — `robot_validation_performed=false`. Not robot-ready. | eval report |

Setup: YOLO11n transfer learning (never from scratch), corrected COCO sports-ball dataset (4,262 train / 127 val / 42 test images, 6,610 verified boxes, official category_id 37), imgsz 640, fraction 0.20, 15/15 epochs, best = epoch 15, weights `runs/ball_v3/weights/best.pt` (gitignored; regenerate via `ai/detection/README.md` §6).

## C. Why the model improved (V1 → V3)

1. **V1** used synthetic circle fixtures — it proved the *machinery* (train/evaluate/inference/perception glue), nothing about real balls.
2. **V2** trained on real COCO images but with a pipeline defect: the converter used YOLO class index 32, which in official COCO JSON is **"tie"**, not "sports ball" (id 37). V2 was unknowingly a necktie detector; all V2 metrics are invalid and deprecated.
3. The defect was **discovered through error analysis**: false-positive anomalies (all "near-miss" FPs at zero IoU) triggered a cross-check of every ground-truth box against the authoritative COCO annotations — 100 % matched "tie" at IoU 1.0.
4. The pipeline was corrected to official category 37, with regression guards (`TestCocoCategoryGuard`) so it cannot silently recur. The defective dataset is preserved as evidence in `ai/datasets/_quarantine_v2_ties_20260906/` with its `STALE_CATEGORY_BUG.md` marker.
5. **V3** rebuilt from real sports-ball photographs with verified labels (every box IoU≥0.5 vs official annotations, exact count parity, ball-like aspect ratios).
6. **640 px vs 320 px was re-measured on corrected labels** before committing: +65 % relative recall at equal precision, 1.9× small-ball recall, ~1.07× latency — so V3 was trained at 640 px.

## D. What will improve the model next (in evidence order)

1. **Real robot/court footage** — the single biggest evidence gap; every public-dataset number stops at domain shift.
2. **Small/distant-ball examples** — V3's measured dominant failure mode (8/66 test balls invisible even at conf 0.01).
3. **Hard negatives** — small round non-ball objects, people near balls (measured FP sources).
4. **Lighting/background/motion variation** — capture scenarios S8–S10, S6–S7.
5. **Real-camera domain data** (mount height, exposure, blur characteristics).
6. **Fine-tuning on robot footage** (transfer learning only) — V4 per the frozen plan in `ai/detection/README.md` §4.7 is **optional, not a prerequisite** for hardware bring-up.
7. **Later:** full-data/longer training (V4 plan) and/or higher resolution — only after robot data, on an uninterrupted machine.
8. **Operating-point tuning** (confidence threshold) — after real deployment error costs are understood (OD-01/OD-05 territory).

## E. Hardware priority

Source of truth: `HARDWARE_PROCUREMENT.md`. Summary:

**BUY NOW / URGENT**
- **USB camera** (≥1280×720 native, frames ≥640 px long side, 25–30 FPS, lockable exposure/focus, mountable at ≥2 heights, ≥2–3 m cable) — **the critical purchase: it unlocks real court footage, real-domain evaluation, labeling, fine-tuning, and camera/lighting/blur evidence — everything the next phase needs.**
- ≥64 GB free storage for footage — video campaigns are GB-scale.
- Known-size scale reference (a tennis ball + tape) — required per-session scale evidence.
- Session discipline aids: second person/tripod, per-session notes — makes the dataset auditable.
- Basic bring-up electronics per `docs/HARDWARE_BRINGUP_PLAN.md` §2-A (ESP32 DevKit, MPU6050, 4× HC-SR04-class, e-stop switch, breadboard kit) — for firmware bench work, not the critical path.

**WAIT — DO NOT BUY YET**
Motors · battery/power system · motor drivers · wheels/chassis · on-robot compute · fixed camera-mount hardware · ToF sensors. Reason: their specifications depend on unresolved decisions and unmeasured physics (OD-04/07/08/09/12/13/14, chassis mass) — buying early means guessing. See `HARDWARE_PROCUREMENT.md` §C.

## F. Team roadmap

| Phase | Scope | Status |
|---|---|---|
| **1** | Software architecture + perception + V1/V2/V3 ML pipeline | **COMPLETE** (verified: 388 tests, documented evidence) |
| **2** | Buy camera/basic bring-up hardware; collect real footage | **NOW** — hardware purchase is the critical path |
| **3** | Label + QC + audit the real robot/court dataset | PLANNED (tooling ready, rules in `ai/detection/README.md` §5/§5.1) |
| **4** | Evaluate V3 on real-domain footage | PLANNED |
| **5** | Fine-tune on real footage if evidence justifies | PLANNED (V4 plan frozen; optional) |
| **6** | Integrate perception with robot behavior | BLOCKED — only after relevant ODs (OD-01…OD-05) are resolved via `docs/DECISION_LOG.md` |
| **7** | Hardware validation and controlled robot testing | BLOCKED ON HARDWARE |

No phase beyond 1 is complete. Nothing here claims robot readiness.

## G. Partner quick start (working the repo)

```bash
git clone <repo-url> && cd AI-Sports-Ball-Recovery-Robot
python -m unittest discover -s tests        # 388 tests, ~50 s, no ML deps needed
# ML work (optional): see ai/detection/README.md §3 for the ai/.venv setup
```

Read `PROJECT_STATUS.md` for the DONE/PROVEN/PLANNED/BLOCKED/OPEN-DECISION ledger, `HARDWARE_PROCUREMENT.md` before spending anything, and `docs/DECISION_LOG.md` §4 before assuming any open decision is settled. Datasets, weights, runs, and virtualenvs are gitignored by design — nothing large is committed.

## Repository layout

```
ai/          Perception/AI: ball detection pipeline (ai/detection/), configs, dataset tooling
navigation/  Localization, planning, zones (laptop-side, geometry-level only)
integration/ communication/ (LaptopLink/protocol/transport), perception/ (observation
             models + validation boundary), system/ (RobotSystemState,
             ApplicationStateMachine, SystemCoordinator, application orchestrator)
firmware/    ESP32 PlatformIO firmware (protocol engine + safety, host-testable)
hardware/    Wiring diagrams, pin assignments, electronics (planning stage)
mechanical/  CAD, 3D-print files, mechanical assembly (planning stage)
tests/       Laptop-side unit/integration tests
docs/        Authoritative architecture/protocol/interface/decision documents
```

## Architecture (agreed, see `docs/ARCHITECTURE.md`)

Two-computer split with a single communication boundary: the ESP32 owns real-time control, sensors, and physical safety latches; the laptop owns perception, localization, planning, and mission policy; `LaptopLink` is the only crossing point. The laptop never sends PWM and never pretends to clear an ESP32 latch. `CMD_MOVE` remains **deferred** (OD-07/OD-13 open). Emergency stop produces `CMD_STOP` only.

## Known incident (evidence preserved)

V2 was trained on COCO "tie" annotations due to a category-id defect (32 vs 37). Found via error analysis, fixed in code, regression-guarded, and the defective dataset quarantined at `ai/datasets/_quarantine_v2_ties_20260906/`. Never quote V2 metrics.

## Team

5-person university semester project.

## License

TBD.
