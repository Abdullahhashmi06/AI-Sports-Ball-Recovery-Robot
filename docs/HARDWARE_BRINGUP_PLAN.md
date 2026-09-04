# AI Sports Ball Recovery Robot — Hardware Bring-Up Plan

**Version:** 1.0
**Status:** Draft for team review
**Scope:** Physical hardware bring-up for the MVP robot: what to buy (and when), the integration order, bench acceptance tests, and the safety rules that apply before any motor is powered. This is a **planning document only** — it does not decide any OPEN decision and does not change any authoritative document.

---

## 0. How to read this document

This plan deliberately separates four different things so they are never confused:

| Category | Meaning in this document |
|---|---|
| **AGREED architectural requirement** | Required by `ARCHITECTURE.md`, `COMMUNICATION_PROTOCOL.md`, or `TEAM_INTERFACE_CONTRACT.md` — frozen, not optional. |
| **OPEN decision** | Recorded as OPEN in `docs/DECISION_LOG.md` (OD-01 … OD-16) or the authoritative docs. **Not decided here.** |
| **Hardware choice still to be made** | A specific part number, model, rating, or layout the documents do not specify. Selecting one is an implementation decision for the owning person, not an architectural decision. |
| **Measurement / test needed** | A physical experiment that must happen before an OPEN decision can be resolved or a firmware constant calibrated. |

Nothing in this document authorizes a purchase that an OPEN decision gates; where a purchase would depend on an unresolved decision, this document says **WAIT**.

---

## 1. Hardware Requirements

"Bench" = required for the first bench tests (skeleton link + sensor bring-up), "Mobile" = required for the first full mobile test, "Final" = required for the MVP robot. Owner is the module/person per `TEAM_INTERFACE_CONTRACT.md` §4 and `ARCHITECTURE.md` §12 (P1 = Integration Lead, P3 = Navigation SW, P4 = Mechanical, P5 = Embedded).

| # | Component | Architectural purpose (source) | Owner / uses it | Bench | Mobile | Final | Specification still to be selected / tested |
|---|---|---|---|---|---|---|---|
| 1 | **ESP32** (dev board) | Low-level real-time controller; USB serial link endpoint (`ARCHITECTURE.md` §3, `COMMUNICATION_PROTOCOL.md` §2) | P5 | ✔ | ✔ | ✔ | Board variant (classic ESP32 vs S3), native-USB vs UART bridge — OD-15. The firmware already builds for `esp32dev` (generic DevKit, USB-UART). |
| 2 | **Laptop + front-facing camera** | All AI/perception input; camera mounted on the robot, connected to the laptop (`ARCHITECTURE.md` §5, §assumptions) | P2 | ✔ | ✔ | ✔ | Camera model, resolution/FPS, mounting angle. Any modern USB webcam satisfies the MVP; nothing in the docs gates the model. |
| 3 | **4 × DC geared drive motors** | 4-wheel differential drive (`ARCHITECTURE.md` §4, §5) | P5 / P3 | — | ✔ | ✔ | Torque, RPM, voltage, shaft/wheel interface. **Gated by OD-07** (max speeds) and chassis mass (P4 design). |
| 4 | **Wheel encoders** | Odometry + closed-loop speed feedback (`TELE.enc`; `ARCHITECTURE.md` §5) | P5 / P3 | — | ✔ | ✔ | Usually integrated into the drive-motor gearbox; counts/rev. Calibration (counts per meter) is **OD-12**. |
| 5 | **2 × motor drivers** | Convert ESP32 PWM to motor power; one channel per side minimum (`ARCHITECTURE.md` §3; protocol never carries PWM, §5) | P5 | — | ✔ | ✔ | Type (dual H-bridge class), current rating — **depends on motor current**, so chosen with the motors. |
| 6 | **Battery / power system** | Onboard energy source (Person 5 owns battery/power, `ARCHITECTURE.md` §12) | P5 | — | ✔ | ✔ | Chemistry, voltage, capacity, discharge rating — **depends on motor + ESP32 + sensor current**, chosen after motors. |
| 7 | **Voltage regulation / power distribution** | Stable rails for ESP32 (3.3 V), logic (5 V), and motors; fuse + polarity protection | P5 | ✔ (USB-only bench power is fine first) | ✔ | ✔ | Regulator ratings follow the motor/battery selection; bench stage can run on USB. |
| 8 | **3–4 × ultrasonic sensors** | Proximity / obstacle distances for the laptop (`TELE.us`; `ARCHITECTURE.md` §5) | P5 / P3 | ✔ | ✔ | ✔ | Module class (common HC-SR04-class trigger/echo); count (3 vs 4) and positions are **OD-08/OD-09**; threshold **OD-08**. ToF (e.g., VL53L0X) is explicitly **not required** for MVP (`ARCHITECTURE.md` §5). |
| 9 | **MPU6050 IMU** | Orientation / heading (`TELE.imu`; `ARCHITECTURE.md` §5 — MPU6050 named explicitly) | P5 / P3 | ✔ | ✔ | ✔ | Breakout board variant; I2C wiring; axis orientation vs robot frame — **OD-13**. |
| 10 | **Intake mechanism (front roller + guide plates/funnel)** | Ball collection (`ARCHITECTURE.md` §6) | P4 | — | — | ✔ | Roller diameter/materials, funnel geometry, standoff/orientation — **OD-04** feeds the approach interface. |
| 11 | **Intake motor** | Drives the roller (`ARCHITECTURE.md` §3, §6) | P5 / P4 | — | — | ✔ | Small DC motor; rating depends on roller geometry/ball friction — mechanical design first. |
| 12 | **Ball verification sensor** | `TELE.collect.trip` + `EVT_COLLECT` (`COMMUNICATION_PROTOCOL.md` §4.2, §10) | P5 / P4 | — | — | ✔ | Type (e.g., IR break-beam / photo-interrupter class) and placement — **OD-10**. |
| 13 | **Storage mechanism (bin)** | Holds collected balls; ESP32-authoritative count (`COMMUNICATION_PROTOCOL.md` §10; capacity is a firmware constant, not protocol) | P4 / P5 | — | — | ✔ | Bin volume/layout; capacity constant to confirm with P4 (current placeholder = 10). |
| 14 | **Dispensing mechanism (servo gate)** | Releases one ball on `CMD_DISPENSE` (`ARCHITECTURE.md` §6; `COMMUNICATION_PROTOCOL.md` §4.1) | P4 / P5 | — | — | ✔ | Servo size/torque and gate geometry; open duration — **OD-11**. |
| 15 | **Physical emergency stop** | Independent hardware stop wired to an ESP32 interrupt pin; latches fault 1 (`COMMUNICATION_PROTOCOL.md` §7 rule 1) | P5 | ✔ (before any motor power) | ✔ | ✔ | Switch type (momentary vs latching), wiring, interrupt pin — **OD-14**. |
| 16 | **Chassis / mechanical structure** | Carries all subsystems; differential-drive geometry (`ARCHITECTURE.md` §4, §assumptions: 3D printer/shop available) | P4 | — | ✔ | ✔ | Wheelbase/track, wheel diameter, sensor mounts — **OD-08/OD-09** depend on it. |
| 17 | **Wiring / connectors / mounting hardware** | Signal + power interconnect, mounts for sensors/camera/IMU | P5 / P4 | ✔ | ✔ | ✔ | Generic — no spec decision needed beyond physical layout. |

---

## 2. Buy Now vs Buy Later

### A. Safe to purchase now (architecture already requires it)

| Item | Why now (and why safe) |
|---|---|
| **ESP32 dev board** (classic ESP32 DevKit, USB-UART) | The ESP32 is an agreed architectural component and the firmware builds for this exact target. The OD-15 (native USB vs bridge) question does **not** block purchase: a USB-UART DevKit satisfies the agreed USB-serial transport today, and the choice can be revisited without buying anything else. |
| **MPU6050 IMU breakout** | The architecture names the MPU6050 explicitly; no OD questions the part itself, only wiring/orientation (OD-13), which needs the part in hand. |
| **4 × ultrasonic module** (HC-SR04-class or equivalent) | Architecture requires 3–4 ultrasonics for the MVP. Buying 4 covers both the 3- and 4-sensor layouts (OD-08); the open questions are mounting/order (OD-09), not the sensor class. The specific module model is a P5 implementation choice. |
| **USB webcam for the laptop** | Architecture requires a front-facing camera connected to the laptop; no OD or document gates the model. Any modern USB webcam works for MVP perception bring-up. |
| **Bench wiring / breadboard / jumper kit** | Needed from the first ESP32 power-up; no spec decision involved. |
| **Momentary push button + pull-up resistor (e-stop prototype)** | Enough to wire and verify the e-stop interrupt/latch **semantics** (OD-14 is about the final switch type/wiring — see B). |

### B. Should NOT be purchased yet (spec depends on an unresolved decision or measurement)

| Item | What it waits for |
|---|---|
| **Drive motors + encoders** | OD-07 (max speeds/ramps) + chassis mass (P4) determine torque/RPM/gearing. |
| **Motor drivers** | Current/voltage rating depends on the chosen motors. |
| **Battery + regulators** | Capacity/discharge/rails depend on motors + electronics once selected. |
| **Final e-stop switch** | OD-14 (momentary vs latching, wiring, pin) — prototype first (A). |
| **Ball verification sensor** | OD-10 (type and placement) needs the intake geometry (P4) and a bench test with real balls. |
| **Dispensing servo (final)** | OD-11 (gate timing) and gate geometry (P4) determine size/torque. |
| **Intake motor (final)** | Roller mechanism design (P4) determines rating. |
| **ToF / VL53L0X sensors** | Explicitly **not required** for MVP (`ARCHITECTURE.md` §5). Do not buy. |
| **Camera with exotic requirements** (high-speed, wide-angle telemetry-grade) | Not needed for MVP; a plain webcam (A) suffices until perception tests show otherwise. |

### C. Prototype / borrow / substitute first

- **Chassis + storage bin**: cardboard / foam-core / 3D-printed mockups until P4 locks the geometry. Drive testing needs only a rigid plate with 4 wheels.
- **Intake roller**: any salvaged DC motor + rubber/foam roller to prove the verification-sensor flow (real ball pass) before buying the final motor.
- **Dispensing gate**: any standard hobby servo (e.g., 9 g class) to prove one-ball release timing.
- **Power**: for bench bring-up, USB power for the ESP32 and a bench supply (borrowed) with current limit for any motor experiments — never a raw battery until B items are chosen.

> **Conservative rule:** if this repository's documents do not give enough information to justify a purchase, it goes in **B**. Only the items in **A** (and prototypes in **C**) are approved spending for the current milestone.

---

## 3. Bring-Up Order

Each stage has a **PASS** criterion and a **do-not-enable** guard. Stages 1–3 require no motors at all. **Stages 4–9 are wheels-off/current-limited bench tests with no free robot motion.** The physical e-stop gate (§6, item 1) must be verified before any test in which the robot can move freely. Hardware-dependent firmware work extends the `DeviceHardware` stub in `firmware/src/main.cpp` at the existing `TODO(P5)` markers — the protocol engine (`firmware/lib/protocol`) must **not** be modified by bring-up work; it is already covered by `pio test -e native`.

| Stage | Connect | Verify | Software / test | PASS | Must NOT enable |
|---|---|---|---|---|---|
| 1. **ESP32 power-up** | ESP32 alone via USB; nothing else | Board enumerates; serial console opens | Existing firmware skeleton + serial monitor | `EVT_BOOT` (reason, fw) then continuous `TELE` @ 20 Hz and `HEARTBEAT` @ 500 ms; `RESP_ERR NOT_READY` until startup checks complete (skeleton: immediate READY) | Any motor/sensor wiring |
| 2. **USB comms (link)** | Laptop ↔ ESP32 USB cable | Full protocol round-trip | `demo_laptop.py --port <port>` (integration/communication) + Python suite | Flow A (EVT_BOOT + first TELE), `CMD_RESET scope=all` → `RESP_OK`, `CMD_MOVE` → `RESP_ERR INTERNAL` (honest "not implemented"), live TELE stream stays up for minutes | Motors, sensors |
| 3. **Battery / power rails** (bench) | Borrowed/current-limited bench PSU and/or generic low-current regulator (Category C prototype equipment) → ESP32 + sensors; fuse + polarity protection — the final motor-rated battery/regulator remains **Category B** (not selected or purchased at this stage) | Stable 3.3 V/5 V under load; no brownout at boot; clean reset reason | Firmware + `demo_laptop.py`; measure rails | Rails within spec at boot and during TELE; `EVT_BOOT` reason reports clean power-up; ESP32 stays READY | Motors; drawing motor current from USB/bench rail without current limit |
| 4. **One motor + driver** (bench, wheels OFF the ground) | Driver IN1/IN2 + PWM + one motor, current-limited bench supply | Direction control; no smoke; current sane | Small P5 firmware test driving the driver (fills the drive `TODO(P5)`), **not** the laptop | Motor spins forward/backward on command; driver enable/disable works; current within driver rating | The other 3 motors; any closed-loop control yet; laptop-driven motion |
| 5. **Two motors (one side)** | Second motor on the same driver channel pair | Both wheels of one side move together, same speed, same direction | Same test, both outputs | Consistent direction/speed; wiring polarity fixed and labeled | The other side |
| 6. **Four motors (both sides)** | Second driver + remaining motors | Differential behavior: forward, backward, rotate-left/right | Firmware side test then laptop `CMD_MOVE` (`lin`/`ang` only — never PWM) | Both sides match at equal command; rotation direction matches `ARCHITECTURE.md`/protocol convention; straight line is straight | Full-speed operation; untested obstacle handling; camera/IMU used for control |
| 7. **Encoders** | Encoder A/B (or A only) wires to ESP32 | Counts change with rotation; polarity vs drive direction | Extend snapshot `TODO(P5)`; observe `TELE.enc.dl/dr` deltas @ 20 Hz | Positive delta for forward on each side; ~0 when stationary; counts repeatable | Using encoder data for any control logic yet — calibration first (OD-12) |
| 8. **IMU** | MPU6050 I²C | Raw accel/gyro sane; orientation maps to robot frame | `TELE.imu` fields; static + rotate test | Static: ~1 g on the vertical axis, gyro ~0; known rotations produce the expected sign per axis (OD-13) | Trusting `yaw` from the ESP32 — laptop-side fusion is the default until OD-13 resolves |
| 9. **Ultrasonic sensors** | 3–4 ultrasonics on GPIO trigger/echo | Distances track measured distances; no-echo = `9999`; fault = `−1` | `TELE.us` with targets at known mm; sensor-fault check (OD-08/OD-09 begin here) | Reading within tolerance of ruler distance; array order agreed and frozen per OD-09 | The ESP32 **hard-stop rule** (fault 3) — threshold is OD-08, calibrate on the bench before enabling |
| 10. **E-stop** | Physical button → interrupt pin | Press = motors + intake off instantly, fault 1 latched, `NOT_READY`; `CMD_RESET scope=all` re-arms; heartbeat does not clear | Firmware interrupt handler (fills e-stop `TODO(P5)`) + laptop flow G | Measured stop latency acceptable; latch persists across heartbeat; re-arm works (OD-14) | Motor power until this stage passes (see §6) |
| 11. **Intake + verification** | Roller motor driver + verification sensor in the intake path | Roller runs on `CMD_INTAKE start`; sensor trip auto-stops roller; `EVT_COLLECT collected` + count +1; stop-without-trip → `failed` | Extend intake `TODO(P5)`; protocol flow D with a real tennis ball | Correct `EVT_COLLECT` per run; no false trips; count matches physical bin (OD-10) | Dispensing while intake runs (BUSY rule is software, but verify physically) |
| 12. **Storage + dispenser** | Bin + servo gate | Count tracking to full; `CMD_DISPENSE` releases exactly one ball; `EVT_DISPENSE ok` decrements; empty → `empty` event; jam/timeout → fault 9 | Extend dispense `TODO(P5)`; protocol flow E with real balls | One ball per `ok`; count matches bin; empty case handled (OD-11) | Dispensing during motion or intake |
| 13. **Full chassis integration** | All subsystems mounted (P4 final layout), camera on laptop mount | Everything from stages 1–12 still passes assembled; camera sees the court/ball zone | All bench checklists (§5) on the assembled robot | No regressions; wiring labeled; access for service | Autonomous navigation / localization — that is a later milestone, not bring-up |

---

## 4. Open Decisions That Require Physical Hardware

Resolved via measurement, **not** by this document. Cross-references are `docs/DECISION_LOG.md` (§4.2 for OD-07…OD-16).

| OD | Decision/question | Physical experiment/measurement required |
|---|---|---|
| OD-07 | Max linear/angular speeds (`lin`/`ang` = ±1) and acceleration ramps | Drive the assembled chassis on the floor at increasing command fractions; measure speed (time over distance) and rotation rate (time over 360°); record ramp settings that keep the robot controllable. |
| OD-08 | Hard-stop threshold (default 150 mm) + which sensors are "forward"; 3 vs 4 sensors | Place targets at measured distances while moving forward; find the distance where braking still stops before contact; decide sensor count from coverage of the forward arc. |
| OD-09 | Ultrasonic mount positions + fixed `TELE.us` order | On the assembled chassis, measure each sensor's field/coverage; freeze the mount layout and the `[front_left, front_right, left, right]` order. |
| OD-10 | Verification sensor type/placement + `collect.trip` debounce | Bench test: pass real tennis balls through the intake at roller speeds; measure trip reliability and false-trip rate; tune placement/debounce. |
| OD-11 | Dispense gate open duration; one-ball release | Gravity-drop bench rig with real balls: sweep open duration, count releases per event, detect double/missed releases. |
| OD-12 | Encoder calibration (counts/meter) + 20 Hz delta smoothness | Roll the chassis a measured distance (e.g., 10 m), record counts; verify velocity from 20 Hz deltas is usable for odometry. |
| OD-13 | IMU yaw handling + MPU6050 axis orientation vs robot frame | Static and controlled-rotation bench test against a reference heading; decide laptop-side vs onboard yaw. |
| OD-14 | E-stop switch type/wiring/interrupt pin | Physical wiring prototype (stage 10): measure stop latency, test momentary vs latching behavior, pick the pin and debounce. |
| OD-15 | USB serial specifics (native USB vs UART bridge, cable length, corruption) | Long-run link soak with `demo_laptop.py`; watch for parse errors at 115200 over realistic cable length. |
| OD-16 | 20 Hz telemetry / ≤20 Hz command rate adequacy | Field-like movement with telemetry logged; confirm closed-loop stability before any protocol timing change. |
| OD-01 | Ball world-pose derivation + `BALL_FOUND` threshold | Camera mounted on the robot + dataset/field tests: project detections onto the ground plane, calibrate, measure detection consistency. |
| OD-04 | Intake pose (standoff, orientation tolerance, stop behavior) | With the real intake mechanism: measure capture envelope vs approach angle/standoff. |
| OD-05 | Perception→reaction latency budget | End-to-end timing measurement: camera → laptop decision → ESP32 actuation, under load. |

**Not hardware-gated (software/interface decisions — do not wait for hardware):** OD-02 (camera obstacle delivery form), OD-03 (search-coordination split), OD-06 (laptop-side response ownership). These can be decided in design review.

---

## 5. Hardware Acceptance Tests (bench checklist)

Run each row against the stated stage; all must pass before the next stage's **Must NOT enable** guard lifts.

| Subsystem | Check | Acceptance |
|---|---|---|
| ESP32 power-up | Serial console, boot event | `EVT_BOOT` with correct `reason`/`fw`; no crash loop |
| USB comms | Link flow, framing | `demo_laptop.py` flow A succeeds; `RESP_OK` for `CMD_RESET scope=all`; no malformed-line count growth |
| Heartbeat/telemetry | Cadence + structure | `HEARTBEAT` ~500 ms; `TELE` ~20 Hz; required fields present (`enc`, `us[4]`, `imu`, `mot`, `collect`, `balls`, `faults`); no `imu.yaw` until OD-13 |
| Motor direction | Each side forward/back | Direction matches protocol convention; both motors per side agree; labeled wiring |
| Encoder polarity/counting | Rotation → counts | Positive count for forward on each side; ~0 when stopped; repeatable over the same rotation (OD-12 data collected) |
| Emergency stop | Physical button + remote `CMD_STOP emergency` | Instant stop; fault 1 latched; `NOT_READY`; heartbeat does not clear; `CMD_RESET scope=all` re-arms |
| Obstacle sensors | Known distances, no-echo, fault | Distance within tolerance of ruler; `9999` on no echo; `−1` on fault (unplug) — without enabling the hard-stop rule |
| IMU | Static + rotation | Static accel ~1 g vertical, gyro ~0; rotation signs match robot frame (OD-13) |
| Intake | Roller + trip | Roller runs on start; sensor trip → auto-stop + `EVT_COLLECT collected`; count +1; stop-without-trip → `failed`; no false trips |
| Ball detection/verification | Real ball pass | 10/10 trial passes detected; count matches physical bin |
| Storage count | Collect to capacity | Count increments to `full`; full flag true; count survives reset of the *laptop* (ESP32-authoritative, `TELE.balls`) |
| Dispensing | Gate + count | Exactly one ball per `EVT_DISPENSE ok`; count decrements; empty bin → `empty` event; jam/timeout → fault 9 |

---

## 6. Safety — minimum requirements before powering motors

These are **hard gates**. Software behavior in the protocol (comms-loss latch, `CMD_RESET` re-arm, telemetry) is implemented and unit-tested, but **no software mechanism is a substitute for the physical protections below** (`COMMUNICATION_PROTOCOL.md` §7 rule 1; `ARCHITECTURE.md` §15 fail-safe principle).

1. **Physical e-stop wired and bench-verified first** (stage 10) — an independent hardware stop on an ESP32 interrupt pin, tested with wheels off the ground *before* any motor is allowed to move the robot. The software e-stop latch and comms-loss stop are additional layers, never the primary one.
2. **Safe motor state enforced in hardware**: motors cannot start until the ESP32 is READY; on any stop/fault the drive returns to zero velocity demand. Verify physically that no firmware path can move a motor while the robot is latched `NOT_READY`.
3. **Current/power protection**: fuses and polarity protection on the battery input; motor drivers operated within their current rating with the chosen motors; first motor power on a **current-limited bench supply**, not a raw battery.
4. **Mechanical precautions**: wheels off the ground (or chassis restrained) for all driver bring-up; hands clear of rollers/gate; work area clear of people/obstacles during any powered test.
5. **First movement protocol**: one operator with a hand on the e-stop, low command fractions only, straight-line testing on a clear floor, no autonomous behavior enabled.
6. **The obstacle hard-stop rule (fault 3) stays disabled** until OD-08 is calibrated on the bench — an uncalibrated threshold must never be treated as safety protection.
7. **Documented sign-off**: each stage's PASS criteria (§5) recorded before the next stage's enable guard lifts; any failure returns the robot to the safe motor state and is diagnosed, not worked around.

---

## 7. Documentation status

- This plan is a **living planning document**, owned by the team (P5 leads, P4/P3/P2 review).
- It records no decisions: **no new DEC entries** were added to `docs/DECISION_LOG.md`, and none of the authoritative documents (`ARCHITECTURE.md`, `COMMUNICATION_PROTOCOL.md`, `TEAM_INTERFACE_CONTRACT.md`, `DECISION_LOG.md`) were modified to create it.
- When an OPEN decision is resolved by a bench measurement (§4), record it in `DECISION_LOG.md` per its change-control rules **and** update the authoritative document that lists it (e.g., a resolved OD-09 freezes the `TELE.us` array order; a resolved OD-12 sets firmware constants).
- Until then: the only approved purchases are §2-A items and §2-C prototypes; everything in §2-B waits.

---

**Version history**

| Version | Date | Summary |
|---|---|---|
| 1.0 | (draft) | Initial hardware bring-up plan: requirements, buy-now/later split, integration order, open decisions requiring physical testing, acceptance checklist, and pre-motor safety gates. |