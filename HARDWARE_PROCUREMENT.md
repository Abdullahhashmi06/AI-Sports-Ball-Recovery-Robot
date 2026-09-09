# HARDWARE PROCUREMENT — what to buy now, what to wait on

**Audience:** the team, for urgent purchasing decisions.
**Purpose:** unblock the next stage — real robot/court data collection — without
specifying hardware the software evidence does not yet justify.

**This document is a procurement aid, not an authoritative spec.** It defers to:

- `docs/HARDWARE_BRINGUP_PLAN.md` §2 — the existing buy-now/buy-later plan for the
  robot electronics (this file stays consistent with it and adds the data-collection
  view; where they differ, the bring-up plan wins).
- `docs/ARCHITECTURE.md`, `docs/COMMUNICATION_PROTOCOL.md`, `docs/TEAM_INTERFACE_CONTRACT.md`,
  `docs/DECISION_LOG.md` — authoritative.
- `ai/detection/README.md` §4.6/§5 — the capture matrix and labeling rules that this
  hardware must serve.

**The critical path is DATA COLLECTION, not the robot.** The current ML baseline (V3)
is validated on public COCO data only. The single measurement the whole project now
needs is: *does V3 detect tennis balls on our court, from our camera?* Everything in
§A exists to answer that. Nothing in §A requires building the robot.

---

## A. MUST BUY NOW — required for data collection

These items are justified by measured V3 failure modes (all from the corrected cat-37
dataset; see `PROJECT_STATUS.md` → Current ML Baseline) and by the capture matrix in
`ai/detection/README.md` §4.6.

### A1. Camera

| Requirement | Minimum | Why — traceable to evidence |
|---|---|---|
| Native resolution | **≥ 1280×720**; frames exported at ≥ 640 px on the long side | 640 px is the operating resolution *and the small-ball mechanism*: measured small-ball recall 0.474 @ 640 px vs 0.246 @ 320 px, and 8/66 test balls were invisible even at conf 0.01. Fewer pixels per ball directly reproduces the dominant failure mode. |
| Sensor/family | Modern **USB webcam** (any current model) | The architecture requires a front-facing camera connected to the laptop; `docs/HARDWARE_BRINGUP_PLAN.md` §2-A explicitly approves "any modern USB webcam" — no OD gates the model. |
| FPS | **25–30 FPS** | Detection runs on single frames; high FPS exists so sharp and motion-blurred frames of the same scene can both be harvested (capture scenarios S6/S7: rolling balls, moving camera). |
| Manual-exposure/focus lock | Required (or software-lockable) | Locked settings per session keep lighting-scenario sessions (S8/S9) comparable and make PROVENANCE records meaningful. |
| Mounting flexibility | Standard tripod thread or clamp; must mount at **≥ 2 distinct heights** | OD-01 (camera world-pose derivation) is open; until it is resolved, capture diversity (low robot-eye + higher view) beats guessing a mount. |
| Cable | Long enough USB lead (≥ 2–3 m) or USB extension | Sessions S7 (moving camera) and wide court coverage need slack; also serves later on-robot placement trials. |
| Scale reference | Any object of **known size** (a tennis ball itself + a ruler/tape) | The capture plan requires a per-session known-size reference clip for future distance/size analysis (supports the small/distant-ball evidence chain; scenario S13). |

### A2. Storage

| Requirement | Minimum | Why |
|---|---|---|
| Free disk for footage | **≥ 64 GB free** (USB drive or cleared laptop space) | Volume target ≈ 30–60 min footage ≈ 3–6 k frames per capture campaign; native-resolution video is GB-scale. Datasets stay gitignored (`ai/datasets/`, `runs/`). |

### A3. Court-side session support (cheap but mandatory)

| Requirement | Why |
|---|---|
| **A second person / tripod** | Scenario S7 (camera moving with static ball) and stable sessions need the camera fixed while a ball handler works; the capture matrix assumes a 2-person setup. |
| Notebook/template for per-session `notes.txt` | Session discipline (date, location, lighting, camera position, anomalies) is what makes the dataset auditable and the provenance chain complete. |
| Balls in known condition (new/worn/dirty mix if available) | Appearance variation is part of domain shift; the capture plan's deliberately-difficult cases need it. |

### Lighting note (no purchase required)

S8/S9 vary lighting using **existing** conditions (lights on/off, blinds, overcast vs
sunny, shadows). Buy nothing for this; record conditions per session instead. If the
venue is severely under-lit, revisit after the first session — do not pre-purchase
lighting equipment.

---

## B. SHOULD BUY NOW — recommended, not hard requirements

| Item | Why it helps | Cost class |
|---|---|---|
| **Small tripod / camera clamp** | Makes height/position repeatable across sessions; required pattern for the "≥ 2 mounting heights" rule | Low |
| **Spare USB cable + USB-A/C adapters** | The #1 field-session failure is cable/connectors; OD-15 (USB specifics) is open, so covering both connector types is cheap insurance | Low |
| **Measuring tape (≥ 10 m)** | Distance bands (near/mid/far) in the capture matrix are approximate, but recording actual distances per session makes the small-ball evidence quantitative | Low |
| **Spare SD/USB drive (second)** | Immediate backup of raw footage before any processing — raw recordings are unrepeatable (specific court, lighting, people) | Medium |
| **Field card/laptop stand for outdoor sessions** | Keeps the capture station usable in sun/glare during S8/S14 | Low |

These are **recommendations**: they make collection reliable, but the §A items alone
unblock the pipeline.

---

## C. DO NOT BUY YET — and why we cannot responsibly specify these

The software evidence base (V3 on COCO; software-only integration tests) does not
establish **any** specification for the categories below. Every one is gated by an OPEN
decision and/or a physical measurement that does not exist yet. Buying early risks
wasting money on parts chosen by guesswork.

| Deferred category | Why we cannot responsibly specify it yet |
|---|---|
| **Motors (drive)** | No counts-per-meter (OD-12), no speed/ramp decision (OD-07), no chassis mass/dimensions exist. Motor choice depends on all three. |
| **Battery / power system** | Depends on motor current draw, which depends on motors (not chosen); `docs/HARDWARE_BRINGUP_PLAN.md` §2-B explicitly defers chemistry/voltage/capacity. |
| **Motor drivers** | Current rating must match the motors; the plan defers it to motor selection (§2 item 5). |
| **Wheels / wheel dimensions** | OD-07/OD-12 territory; geometry must be measured on a real chassis. |
| **Chassis / mechanical structure** | Intake geometry (OD-04), sensor positions (OD-09), e-stop wiring (OD-14) all shape it; Person 4's mechanism design is not settled. |
| **Compute board (on-robot)** | The architecture runs AI on the **laptop**; no on-robot compute is required by any document. Do not buy Jetson/RPi/etc. without an architecture change. |
| **Exact robot camera placement hardware** | OD-01 (world-pose derivation) and the final mount are unresolved; buy the *flexible* mount (§A1/§B), not a fixed robot-mount part. |
| **Communication hardware beyond the ESP32 board** | DEC-005 fixes USB serial; OD-15 (native USB vs UART bridge, cable specifics) is only answerable with the board in hand (a USB-UART DevKit satisfies it today per the bring-up plan). |
| **Ultrasonic / IMU / servo / intake parts** | These **are** approved buys under `docs/HARDWARE_BRINGUP_PLAN.md` §2-A for *bench bring-up* (ESP32 DevKit, MPU6050, 4× HC-SR04-class, e-stop switch, breadboard kit) — but they are not on the data-collection critical path. Follow that document's list and §2-B's WAIT list; do not let them displace the camera kit. |
| **ToF / VL53L0X sensors** | Explicitly not required for MVP (`docs/ARCHITECTURE.md` §5). Do not buy. |

**Conservative rule (inherited from the bring-up plan):** if a purchase is not justified
by §A/§B here or by the bring-up plan's §2-A/§2-C, it waits.

---

## Hardware → software traceability

BUY THIS → COLLECT THIS DATA → RUN THIS PIPELINE → MEASURE THIS → MAKE THIS DECISION.

| Hardware / item | Minimum requirement | Why needed | Software milestone depending on it | Status |
|---|---|---|---|---|
| USB webcam (A1) | ≥1280×720 native, ≥640 px long side export, 25–30 FPS, exposure lock | Real-court images at the operating resolution | V3 robot-domain evaluation (`evaluate.py` on robot test split) → OD-01 evidence; future V4 fine-tune | **BUY NOW** |
| Flexible mounting (A1/B) | ≥ 2 heights, stable | Capture diversity until OD-01 fixes the mount | Capture matrix §4.6 (all scenarios) | **BUY NOW** |
| Long USB cable/extenders (A1/B) | ≥ 2–3 m reach | Session S7 (moving camera); on-robot placement trials | S7 motion-blur harvest; future mount decision | **BUY NOW** |
| Storage ≥ 64 GB (A2) | Free disk for raw footage | 3–6 k frames per campaign | `robot_dataset` frames → labels → split → audit pipeline | **BUY NOW** |
| Known-size reference object (A1) | Any ball + ruler | Scale reference clip per session | Small/distant-ball (S13) evidence chain; supports OD-01 measurement design | **BUY NOW** |
| Session-notes discipline (A3) | notes.txt per session | Auditable provenance | `robot_dataset.provenance` → PROVENANCE.md → training-run reproducibility | **BUY NOW (free)** |
| Backup drive (B) | Second copy of raw footage | Raw recordings are unrepeatable | Protects every downstream milestone | RECOMMENDED |
| ESP32 DevKit + MPU6050 + 4× HC-SR04-class + e-stop switch + breadboard kit | As listed in `docs/HARDWARE_BRINGUP_PLAN.md` §2-A | Firmware bench bring-up (`STAGE_1_BRINGUP_RUNBOOK.md` stages 1–2), OD-14/OD-15 bench evidence | Firmware bench validation; later OD resolution | APPROVED (bring-up plan §2-A) — **not the critical path** |
| Motors/battery/drivers/wheels/chassis | — | Cannot be specified without OD-07/OD-12/OD-14 resolutions and physical measurements | — | **WAIT** |

---

## Immediate team roadmap

### NOW (no hardware needed)

1. Review + commit the uncommitted working tree (perception seam, `ai/detection/`,
   orchestrator, tests, docs). 388 Python tests pass.
2. Rehearse the labeling workflow on *any* video: `robot_dataset` frames → labels →
   validate → split → audit → provenance (the §4.5 dry run shows the exact commands).
3. Read `ai/detection/README.md` §4.6 (capture matrix) and §5 (labeling guide) before
   the first session — labelers must know the rules *before* collecting.
4. (Optional, authorized separately) V4 full-data run per the frozen §4.7 plan.

### BUY

Section §A (camera kit first), §B as budget allows; electronics only per the bring-up
plan §2-A.

### FIRST DAY WITH HARDWARE

1. Verify the camera: resolution/FPS/exposure-lock as delivered (do not assume spec-sheet
   values — record what is measured).
2. Record the **scale-reference clip** and one short pilot session (S1 near/mid/far).
3. Run ~20 pilot frames through `frames extract` → `validate` → eyeball a rendered
   sample. Confirm frames are ≥ 640 px long side and labels can be drawn accurately
   before mass capture.

### DATA COLLECTION

Follow the 15-scenario matrix in `ai/detection/README.md` §4.6: 15 scenarios × 2–3
distance bands × 2 lighting conditions; session directories `sess<id>_<frame>`;
target ≈ 3–6 k candidate frames → 1.5–3 k labeled; **20–30 % negative (no-ball)
frames**; S13 (extreme-small/distant balls) is the priority scenario because it attacks
V3's measured dominant failure mode.

### LABEL / QC

Exactly as implemented: `validate` (machine rules) → `audit` (contamination, session
leakage, suspicious boxes — flags, never deletes) → tiny-box review → ≥ 10 % rendered
spot-check → negative-ratio check → `PROVENANCE.md` (`--domain robot`).

### MODEL VALIDATION

Evaluate the **unmodified V3** weights (`runs/ball_v3/weights/best.pt`) on the
robot-domain test split with the same methodology as the COCO evaluation. This is the
first real measurement of domain shift. Quote results as robot-dataset metrics —
they still are not "the robot works" claims.

### ONLY AFTER THAT

1. If robot-domain recall is materially below COCO numbers (expected): fine-tune
   (V4+) on labeled robot data — transfer learning only, never from scratch.
2. Re-run the error analysis on robot data to re-rank failure modes with real evidence.
3. In parallel (separate track, P5): ESP32 Stage-1 bench bring-up per the runbook —
   `EVT_BOOT`/`TELE` round-trip, then the OD-12/OD-13/OD-14/OD-15 bench measurements.

**Reminder:** V3's public COCO performance does NOT equal robot readiness. Nothing in
this document claims the robot can see, move, or collect — only what to buy so those
questions can finally be measured.
