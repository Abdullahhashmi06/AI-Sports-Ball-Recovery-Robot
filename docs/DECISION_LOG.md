# AI Sports Ball Recovery Robot — Decision Log

**Version:** 1.0
**Status:** Draft for team review
**Scope:** Lightweight record of agreed technical/design decisions, their rationale, status, and source, so the team (and AI coding agents) can see *why* something was chosen and will not accidentally change an agreed decision.

---

## 1. Purpose

This document records the project's important technical and design decisions: what was agreed, why, when (version), and on whose authority. Its goal is to preserve the reasoning behind the system design and to make it obvious which decisions are **AGREED / FROZEN** (must not be changed casually) and which are still **OPEN** (must be resolved before implementation reaches them).

It is **not** an architecture, interface, or protocol document. It does not replace or restate `ARCHITECTURE.md`, `COMMUNICATION_PROTOCOL.md`, or `TEAM_INTERFACE_CONTRACT.md` — it records decisions and points back to those documents as the source of truth (see [Change Control](#7-change-control)).

---

## 2. Status Definitions

| Status | Meaning |
|---|---|
| **AGREED / FROZEN** | The team has committed to this decision. Code and other documents may rely on it. Changing it requires going through change control (see [Change Control](#7-change-control)) and updating the authoritative document that records it. |
| **OPEN** | Not yet decided. Must not be silently assumed or coded against as if agreed. Resolving it should produce a new AGREED entry in this log. |
| **SUPERSEDED** | Was previously agreed/open in an older form, but a later decision replaced it. Kept in the log for history; the newer entry is authoritative. |

---

## 3. Agreed Decisions

Only decisions clearly established in the existing authoritative documents are listed. Rationales are stated where the documents record one; otherwise the entry says so explicitly rather than inventing a reason. Sources: `ARCHITECTURE.md` (**A**), `COMMUNICATION_PROTOCOL.md` (**C**), `TEAM_INTERFACE_CONTRACT.md` (**T**).

| ID | Decision | Status | Rationale | Source / Authority |
|---|---|---|---|---|
| DEC-001 | The **laptop** owns all AI/perception and high-level decisions: computer vision, ball/obstacle detection and tracking, rally and court perception, exit estimation, navigation/search decisions, and the system state machine. | AGREED / FROZEN | Perception and decision-making are computationally expensive and must be separated from real-time control; the robot is a two-computer system with this split. | A §1–§2; C §1 |
| DEC-002 | The **ESP32** owns real-time, low-level robot control: motors, encoders, ultrasonic/ToF and IMU sampling, intake, collection verification, dispensing, e-stop handling — executing laptop commands and reporting status. It runs **no** AI inference and **no** high-level decision logic. | AGREED / FROZEN | Low-level sensing and actuation must be deterministic and close to the hardware; high-level logic does not belong on the microcontroller. | A §3; C §1 |
| DEC-003 | The robot uses a **4-wheel differential-drive** configuration: four fixed wheels, left side and right side controlled as two units. No mecanum wheels, no mechanical steering, no robotic arm. Movement is forward/backward, differential curves, and rotation only. | AGREED / FROZEN | Simplest configuration that meets the movement need; navigation plans must be composed of arcs and turns because the robot cannot strafe. | A §4; C §5 |
| DEC-004 | The laptop and ESP32 communicate across a **single link / single communication boundary**. | AGREED / FROZEN | Clean hardware/software separation between the two compute nodes with one agreed crossing point. | A §10; C §1 |
| DEC-005 | The MVP transport is **USB serial** between laptop and ESP32. | AGREED / FROZEN | Simplest possible link; no extra hardware or networking; already assumed by the architecture. | C §2 (transport table) |
| DEC-006 | The wire protocol is **communication protocol version 1** as specified in `COMMUNICATION_PROTOCOL.md`: newline-framed compact JSON messages, command/status model, common header (`v`/`type`/`seq`/`ts`), defined message catalog, schemas, timing, and safety semantics. | AGREED / FROZEN | Protocol is intentionally simple so five people can implement and debug it independently within a 3-month project. | C (whole document), superseding the open transport decision in A §10 |
| DEC-007 | **Fail-safe ownership:** the ESP32 owns local fail-safes — physical e-stop, remote emergency stop, communication-loss safe stop, local obstacle hard stop — with latched `NOT_READY` state and explicit re-arm via `CMD_RESET scope: all`. The robot must fail safe if laptop communication is lost. | AGREED / FROZEN | Fail-safe behavior is an explicit design principle; latching prevents automatic re-motion after a stop. | C §7; A §9, §15 |
| DEC-008 | Robot positions use a **fixed global 2D Cartesian world coordinate system**; the origin does not move with the robot. | AGREED / FROZEN | Rationale not explicitly recorded; inherited from the agreed architecture. | T §6.1 |
| DEC-009 | World **origin (0, 0)** is the fixed physical court corner **nearest the designated HOME/BASE location**. | AGREED / FROZEN | Rationale not explicitly recorded; inherited from the agreed architecture. | T §6.1 |
| DEC-010 | **+X** runs along the **20 m length** of the padel court. | AGREED / FROZEN | Rationale not explicitly recorded; inherited from the agreed architecture. | T §6.1 |
| DEC-011 | **+Y** runs across the **10 m width** of the padel court. | AGREED / FROZEN | Rationale not explicitly recorded; inherited from the agreed architecture. | T §6.1 |
| DEC-012 | World-frame **units are meters**. | AGREED / FROZEN | Rationale not explicitly recorded; inherited from the agreed architecture. | T §6.1 |
| DEC-013 | **Robot pose** is represented as **(x, y, θ)**; **ball position** is represented as **(x, y)**. | AGREED / FROZEN | Rationale not explicitly recorded; inherited from the agreed architecture. | T §6.1 |
| DEC-014 | **Negative coordinates are valid.** | AGREED / FROZEN | Rationale not explicitly recorded; inherited from the agreed architecture. | T §6.1 |
| DEC-015 | The world coordinate frame is **independent of the size of the operating/recovery area**; no world boundary is part of the frame definition, and no 20 m or 30 m boundary is hard-coded into it. | AGREED / FROZEN | Keeps the convention stable while the operating area stays configurable and extensible (DEC-016, DEC-017). | T §6.1–§6.2 |
| DEC-016 | The **20 m × 10 m padel court** is a known rectangular region **inside** a larger, **configurable** recovery/operating area; the robot operates outside the court to recover balls, and the boundary is venue configuration, not part of the coordinate definition. | AGREED / FROZEN | The robot must travel outside the court into surrounding recovery areas, which differ per venue, so the boundary cannot be a fixed constant. | T §6.2 |
| DEC-017 | Initial target is **~20 m of recovery travel outward** from the court where the physical environment permits, and the design stays extensible to larger areas (e.g., 30 m+) **without changing the coordinate convention**. | AGREED / FROZEN | Recovery reach exceeds the court dimensions, and future venues may need more room; extensibility is achieved by configuration, not by changing the frame. | T §6.2 |
| DEC-018 | There are four **logical recovery/search zones — `WEST`, `EAST`, `NORTH`, `SOUTH`** — named relative to the world frame. They are logical regions, **not permanently hard-coded physical rectangles**; physical extents are configurable venue settings, and no dimensions are defined yet. | AGREED / FROZEN | Zone names and vocabulary are fixed so modules can communicate; physical boundaries depend on the eventual testing venue. | T §6.3; vocabulary used in T §5 (I-1, I-3) |
| DEC-019 | On rally end, AI provides Navigation with `exit_zone`, `estimated_ball_position` (x, y, meters), and `confidence`. The estimated position is a **SEARCH HINT, not guaranteed ground truth**: Navigation prioritizes it when confidence is useful, searches around it, expands if not found, and uses a broader/systematic search when confidence is low. | AGREED / FROZEN | The position estimate cannot be treated as certain; search behavior must degrade gracefully from "hint" to "systematic". | T §6.4 |
| DEC-020 | The **ESP32 is authoritative** for motor state, encoder readings, collection result, and stored-ball count (as specified by the documents); other modules treat these as read-only inputs and the laptop reconciles its copies from telemetry rather than inventing values. | AGREED / FROZEN | Physical truth lives at the sensor/actuator; one authoritative owner per datum avoids conflicting state. | C §6, §10; T §7 |
| DEC-021 | The **AI/perception module never directly commands motors** (or intake/dispensing). Normal motion flows exclusively through the Navigation module; only the state-machine/safety layer may additionally issue an emergency stop as a fail-safe. | AGREED / FROZEN | Keeps perception decoupled from actuation and preserves the single, owned command path. | T §8 R-3; C §1 (non-goals) |
| DEC-022 | **Navigation owns high-level movement decisions** — paths, velocities, route planning — expressed as normalized velocity commands; it does **not** own (or send) low-level motor PWM. | AGREED / FROZEN | Clean split of "what/where to move" (laptop) from "how to drive the motors" (ESP32). | T §8 R-4; C §1, §5 |
| DEC-023 | The **ESP32 owns low-level real-time control**: PWM generation, closed-loop speed control, sensor sampling, intake/dispense actuation, verification sensing, and local fail-safes. The laptop never sends PWM or raw actuator signals. | AGREED / FROZEN | Deterministic, hardware-near control belongs on the ESP32; the laptop commands intent, not signals. | T §8 R-5; A §3; C §1, §5 |

### 3.1 Superseded entries

| ID | Decision / prior state | Status | Replaced by | Source / Authority |
|---|---|---|---|---|
| SUP-01 | "Communication transport & message format" was an **open** technical decision in the architecture. | SUPERSEDED | DEC-005 / DEC-006 (protocol v1) | A §10 (Open Technical Decisions); C (header) |
| SUP-02 | "Coordinate conventions" were an **open** interface decision. | SUPERSEDED | DEC-008 … DEC-015 (agreed conventions, world frame) | T §12 intro; T §6.1 |
| SUP-03 | "Form of the exit-zone estimate" was an **open** interface decision. | SUPERSEDED | DEC-018 / DEC-019 (`exit_zone`, search hint) | T §12 intro; T §6.3–§6.4 |

---

## 4. Open Decisions

Nothing below is decided. These items are recorded here (with their current status) so they are not accidentally treated as agreed; they must be resolved through the normal decision process before implementation depends on them. Where a document records a default working assumption, it is noted — a default is **not** an agreement.

### 4.1 Open interface decisions — from `TEAM_INTERFACE_CONTRACT.md` §12

| ID | Decision / question | Why it matters | Current status | Where documented |
|---|---|---|---|---|
| OD-01 | How Module 1 derives ball **world pose** from image detections (ground-plane assumption, calibration) and how many consistent detections qualify as **`BALL_FOUND`**. | Determines the trust threshold for `SEARCH → BALL_FOUND` and the approach interface; needs dataset and field testing. | OPEN | T §12.1 |
| OD-02 | The **camera obstacle delivery form** (typed world regions per I-2 vs. another form) and how Module 2 fuses it with `TELE.us` distances. | I-2's obstacle payload stays *indicative* (not codeable) until agreed. | OPEN (world frame itself is fixed per DEC-008 … DEC-015) | T §12.2 |
| OD-03 | **Search coordination split**: which search behaviors the state machine commands explicitly vs. which Module 2 chooses internally within the directed zone. | Keeps task-directive interfaces I-3/I-4 minimal. | OPEN | T §12.3 |
| OD-04 | **Intake pose definition**: standoff distance, orientation tolerance, and final stopping behavior for `approach`. | Dictated by Module 3 mechanism geometry (Person 4) and verification-sensor placement; affects the collection handoff. | OPEN | T §12.4; C §14 item 4 |
| OD-05 | **Perception → reaction latency budget**: how stale `ball_state`/obstacle data may be before Module 2 must abort or stop. | Safety-relevant timing must be measured, not guessed. | OPEN | T §12.5 |
| OD-06 | **Laptop-side response ownership**: which laptop component consumes `RESP_OK`/`RESP_ERR` for each command class and how command failures surface to the state machine. | Defines how command failures propagate without being silently dropped. | OPEN | T §12.6 |

### 4.2 Open decisions requiring physical testing — from `COMMUNICATION_PROTOCOL.md` §14

| ID | Decision / question | Why it matters | Current status | Where documented |
|---|---|---|---|---|
| OD-07 | **Maximum linear/angular speeds** and acceleration ramps that `lin`/`ang` = ±1 map to. | The ESP32's configured full-scale values must match the real chassis and motors. | OPEN | C §14 item 1 |
| OD-08 | **Hard-stop obstacle threshold** (default 150 mm) and which ultrasonic sensors count as "forward"; 3 vs. 4 ultrasonic sensors. | Depends on sensor placement and robot braking distance; affects `TELE.us` layout. | OPEN (150 mm is a default to calibrate, not an agreement) | C §14 item 2 |
| OD-09 | **Ultrasonic mount positions** and the fixed `TELE.us` array order (`[front_left, front_right, left, right]`). | Person 4 must confirm against the chassis; the order is frozen once firmware implements it. | OPEN | C §14 item 3 |
| OD-10 | **Collection verification sensor** type and placement, and the debounce/timing for `collect.trip`. | Must reliably detect a tennis ball in the intake path without false trips. | OPEN | C §14 item 4 |
| OD-11 | **Dispense gate open duration** and servo behavior to release exactly one ball reliably. | Dispense correctness depends on mechanism timing. | OPEN | C §14 item 5 |
| OD-12 | **Encoder calibration** — counts per meter (wheel diameter, gear ratio) and whether 20 Hz encoder deltas give smooth enough velocity for laptop odometry. | Feeds Module 2 localization quality. | OPEN | C §14 item 6 |
| OD-13 | **IMU handling**: whether `yaw` estimation stays laptop-side (default) or moves onto the ESP32 via a complementary filter; MPU6050 axis orientation vs. the robot frame. | Determines the optional `TELE.imu.yaw` field and heading correctness. | OPEN (laptop-side is the default, not an agreement) | C §14 item 7 |
| OD-14 | **E-stop hardware**: switch type (momentary vs. latching), wiring, and interrupt pin. | The latch/re-arm semantics are fixed (DEC-007); the hardware is not. | OPEN | C §14 item 8 |
| OD-15 | **USB serial specifics**: native USB vs. UART bridge, cable length, and any observed corruption at 115200. | Confirms the transport choice (DEC-005) holds in practice. | OPEN | C §14 item 9 |
| OD-16 | **Telemetry/command rate adequacy**: confirm 20 Hz `TELE` and ≤ 20 Hz commands give stable closed-loop field behavior. | Rate changes would be a versioned timing change to the protocol. | OPEN (20 Hz is the MVP assumption, not an agreement) | C §14 item 10 |

Resolving any OD item should produce a new AGREED entry in [§3](#3-agreed-decisions) following the [entry format](#5-decision-entry-format), and must update the authoritative document that lists it if the resolution changes an interface or protocol.

---

## 5. Decision Entry Format

New decisions are recorded in the Agreed Decisions table and, for significant ones, expanded with a short entry using the template below. **Numbering continues from DEC-024** (the last agreed entry above).

```
### DEC-XXX — [Short title]
- Date:            YYYY-MM-DD
- Status:          AGREED / FROZEN  (or OPEN / SUPERSEDED, as appropriate)
- Decision:        One or two sentences stating exactly what was agreed.
- Rationale:       Why this option over the alternatives (from the proposing owner).
- Alternatives considered: Options that were discussed and rejected, briefly.
- Impact:          Which modules, interfaces (T §5–§9), protocol messages (C),
                   or architecture sections (A) are affected.
- Owner / approver: Person(s) who proposed and approved (per T §11 sign-off).
- Source/document updated: Which authoritative document was changed to record it.
- Notes:           Anything else — dependencies, calibration items, links to OD-IDs.
```

---

## 6. Change Control

- **Record before implementation:** important decisions should be recorded here (this section or §3) before implementation changes that depend on them begin.
- **Keep authoritative documents in sync:** a decision that changes an authoritative interface or protocol must also update the appropriate authoritative document — `ARCHITECTURE.md`, `COMMUNICATION_PROTOCOL.md`, or `TEAM_INTERFACE_CONTRACT.md` — following their change rules (notably `TEAM_INTERFACE_CONTRACT.md` §11 and the protocol versioning policy in `COMMUNICATION_PROTOCOL.md` §13).
- **This log does not override anything:** it is a record of decisions and their reasoning. `ARCHITECTURE.md`, `COMMUNICATION_PROTOCOL.md`, and `TEAM_INTERFACE_CONTRACT.md` remain the source of truth for their respective areas. Where this log's summary of a decision conflicts with an authoritative document, the authoritative document wins until a change is approved.
- **AI coding agents:** treat entries marked AGREED / FROZEN as binding constraints on code and docs; never silently "fix" or modernize an agreed decision — propose a change through this process instead. Entries marked OPEN must not be treated as decided.

---

## 7. Current Project State

Implementation has **not yet begun**. As of this entry, the repository contains only the scaffolded project structure and the planning documents: the top-level module directories (`ai/`, `navigation/`, `firmware/`, `hardware/`, `mechanical/`, `integration/`, `tests/`, `media/`) hold README placeholders and `.gitkeep` files only, and `docs/` holds the three authoritative planning documents plus this log. The project is therefore at the transition from planning into implementation: this log's OPEN items and the open decisions recorded in the authoritative documents should be resolved (or consciously deferred) before the code they constrain is written.

---

**Version history**

| Version | Date | Summary |
|---|---|---|
| 1.0 | (draft) | Initial decision log: records the agreed architecture/protocol/coordinate decisions from the three authoritative documents and tracks all currently open decisions. |
