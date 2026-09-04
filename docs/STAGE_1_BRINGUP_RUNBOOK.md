# AI Sports Ball Recovery Robot — Stage 1 Bring-Up Runbook

**Version:** 1.0
**Status:** Draft for the first bench session
**Scope:** The very first physical bring-up of the robot electronics — **before any drive motors are connected**. This is a runbook (procedure), not an implementation task: it exercises the already-built bring-up skeleton against a real ESP32 over USB serial.

Companion documents:
- `docs/HARDWARE_BRINGUP_PLAN.md` — Stage 1 covers bring-up-plan stages 1 and 2 (ESP32 power-up + USB link); stage 3 (bench power rails) is optional here.
- `docs/COMMUNICATION_PROTOCOL.md` — the exact behavior we expect to observe (§4.2, §7, §8).
- `docs/DECISION_LOG.md` — every hardware-dependent value below is marked **TBD / hardware selection required**; nothing here resolves an OD item.

> **Hard rules:** No drive motors, no intake, no dispenser, no actuators of any kind are connected during Stage 1. Any value not already fixed by the protocol or the skeleton is marked TBD and must not be guessed during the session.

---

## 1. Bench safety

1. **No actuators on the bench.** Drive motors, motor drivers, intake roller, and the dispensing servo stay **disconnected and unpowered** for the whole stage. The only hardware is ESP32 DevKit + USB cable + laptop.
2. **Check the work area** before powering anything: no loose metal, liquids, or people close enough to be touched by a swinging cable; the board sits on a non-conductive surface.
3. **USB precautions.** Use a known-good, short USB cable (a "charge-only" cable is a common cause of "no COM port / no EVT_BOOT"). Do not hot-plug repeatedly in a hurry — connect once, then power on.
4. **Safe power-up procedure:** connect USB → confirm the board enumerates (see §3) → open the serial monitor → reset the board. First observation is the boot log. If anything smells hot or the board fails to enumerate, **power down immediately** (stop condition, §10).
5. **Emergency-stop prototype considerations:** Stage 1 does **not** require the e-stop (there is nothing to stop — no motors). The physical e-stop wiring is bring-up-plan **stage 10** and its hardware is OPEN (**OD-14**). Do not improvise an e-stop circuit during this stage; the software latch semantics are already covered by the native tests.
6. **Before any actuator is EVER connected** (future stages), the bring-up plan requires: physical e-stop wired and bench-verified, safe motor state confirmed, and current-limited power — see `HARDWARE_BRINGUP_PLAN.md` §6. None of that is waived here; it simply does not apply yet because there are no actuators.

---

## 2. Software pre-check (before touching hardware)

Run these in order. They verify the **host/native** side only — a green result here does **not** prove the ESP32 hardware works; it proves the code we are about to flash is sound.

| # | Command | What it verifies | Expected |
|---|---|---|---|
| 1 | `git status --short` (repo root) | Repository is clean before the session | Only the runbook/doc changes you brought; no stray files. Note: `.venv/` and `.pio/` in `firmware/` are gitignored and will **not** appear. |
| 2 | `python -m unittest discover -s tests` | Laptop protocol helpers + link layer | 48 tests, OK |
| 3 | `cd firmware && .venv/Scripts/python.exe -m platformio test -e native` | ESP32 protocol engine (host-compiled, Unity) | 39 test cases, all succeeded |
| 4 | `cd firmware && .venv/Scripts/python.exe -m platformio run` | The firmware **builds** for the `esp32dev` target (no upload) | Build success (`.pio/build/esp32dev/firmware.bin`) |

**Host/native vs hardware:** items 2–4 run on the laptop with no ESP32 attached. They exercise the protocol engine in software. Passing them is a prerequisite, not a substitute, for the hardware checks in §4–§7.

> If the project-local venv is missing on a given machine, the commands above will fail with "command not found" — recreate it (`python -m venv firmware/.venv` + install `platformio`) before the session. Do **not** install PlatformIO globally without team agreement.

---

## 3. ESP32 + USB bench setup

**Physical setup:**
- ESP32 DevKit on the bench (non-conductive surface).
- USB cable: DevKit USB port → laptop.
- Nothing else connected. No GPIO wires, no breadboard.

**Identifying the COM port (Windows):**
1. Open **Device Manager** → **Ports (COM & LPT)**.
2. Look for the entry that appears when the board is plugged in and disappears when unplugged — typically named `USB-SERIAL CH340 (COMx)` or `CP210x USB to UART Bridge (COMx)`. The exact chip/name is hardware-dependent (**OD-15**); note the `COMx` number.
3. Alternative: `cd firmware && .venv/Scripts/python.exe -m platformio device list` — prints detected serial ports with vendor descriptions.
4. If no new port appears at all, the cable or the board is suspect (see §9 — "no COM port").

**Flashing note:** upload uses the board's bootloader over the same USB port (`pio run -t upload` handles the reset/boot-mode sequence automatically).

---

## 4. First flash

From a terminal, using the project's local PlatformIO venv:

```bash
cd firmware
.venv/Scripts/python.exe -m platformio run          # build (if not already done)
.venv/Scripts/python.exe -m platformio run -t upload   # flash the ESP32
```

**Expected upload output:** success lines ending with something like `SUCCESS: ... firmware.bin` / `A fatal error occurred: Failed to connect to ESP32` **only** on failure (see §9).

Then open the serial monitor (keeps the USB port in use, so close it before any other program tries the port):

```bash
.venv/Scripts/python.exe -m platformio device monitor   # monitor_speed 115200 is set in platformio.ini
```

Press the board's **EN/RST button** while the monitor is open to force a fresh boot.

---

## 5. Expected startup behavior

From the **current bring-up skeleton** (no sensors attached), the serial monitor should show, on a fresh boot:

1. **`EVT_BOOT`** — `{"v":1,"type":"EVT_BOOT","seq":…,"ts":…,"reason":"poweron"|"reset"|…,"fw":"0.1.0-skeleton"}` (protocol §4.2). `reason` reflects the ESP32 reset cause.
2. **Startup/READY** — the skeleton's startup checks are intentionally trivial, so READY follows immediately; the engine only executes commands after its checks complete (protocol §7). You will **not** see a visible "READY" line — READY is observable via command responses (a `CMD_RESET scope=all` → `RESP_OK` proves it).
3. **`TELE` frames** at **20 Hz** — one JSON line every ~50 ms.
4. **`HEARTBEAT`** every **500 ms**.

**Current placeholder telemetry — these values are placeholders, not readings:**

| Field | Placeholder value | Why |
|---|---|---|
| `enc` | `{"dl":0,"dr":0}` | No encoders connected |
| `us` | `[9999,9999,9999,9999]` | No ultrasonics; `9999` = "no echo / not populated" (protocol §4.2) |
| `imu` | `ax/ay/az` = 0, `gx/gy/gz` = 0 | No MPU6050 connected |
| `mot` | `pwm 0/0`, `spd 0/0` | Nothing moving |
| `collect` | `{"trip":false,"state":"idle"}` | No verification sensor |
| `balls` | `{"count":0,"full":false}` | Empty bin |
| `faults` | `[]` | No faults |

**Command responses (send via the laptop, §6):**
- `CMD_RESET scope=all` → `RESP_OK` (re-arm path works).
- `CMD_MOVE …` → `RESP_ERR` `INTERNAL` with a message like "drive hardware not implemented (bring-up skeleton)" — this is the **honest** answer: the hardware does not exist yet, and the skeleton must not pretend otherwise.
- `CMD_STOP` (normal) → `RESP_OK` (safe motor state is the default).

---

## 6. Laptop ↔ ESP32 communication test

Prerequisite: §3 COM port identified, firmware flashed, monitor **closed** (the demo needs the port).

```bash
cd <repo root>
python -m integration.communication.demo_laptop --port COMx   # e.g. COM5; on Linux/macOS: /dev/ttyUSB0
```

**What successful communication looks like:**
- `EVT_BOOT received: reason=… fw=0.1.0-skeleton`
- `first TELE: enc=(+0,+0) us=[9999, 9999, 9999, 9999] … balls=0 faults=[]`
- `CMD_RESET scope=all -> RESP_OK (ack=…)`
- `CMD_MOVE -> RESP_ERR INTERNAL: drive hardware not implemented …` (expected on the skeleton)
- `CMD_STOP normal -> RESP_OK`
- a live TELE summary printed roughly once a second for the full `--duration` (default 10 s) and `[demo] done — link stayed up`

**CMD_RESET behavior:** `scope=all` clears any latched fault and re-arms (`RESP_OK`); the demo sends it right after boot. `scope=faults` clears only non-latching faults and would **not** re-arm after an e-stop/comms-loss latch (protocol §7) — not needed on a fresh boot.

**Invalid command:** the laptop validates locally, so a schema-invalid command is rejected **before** it is sent (e.g., `lin=1.5` raises `ValidationError RANGE` locally). A message that is malformed on the wire is counted/ignored with **no** `RESP_ERR` (protocol §2/§9); a message with a valid header but invalid content gets the matching `RESP_ERR` (`RANGE`/`PARSE`/`UNKNOWN_TYPE`/`VERSION`).

**USB interrupted mid-session:** the demo watches the link; if the ESP32 stops responding, the laptop flags `link frozen (ESP32 lost) — check the USB cable` and exits 1 (see §7 for the controlled version of this test).

---

## 7. Physical disconnect test (communication loss)

**Controlled procedure** (run only after §6 fully passes):

1. **Power note:** if the DevKit is powered **only from USB**, unplugging the cable also power-cycles the board — on reconnect you will see a fresh `EVT_BOOT` (reboot), not a comms-loss latch. To observe the **true comms-loss latch**, power the board from the bench PSU (bring-up-plan stage 3) so the USB data link alone is severed. This distinction is hardware-dependent; record which method was used. **This test has not been performed yet** — it is a procedure for the first bench session, and the session's results must be recorded separately (§12).
2. Establish valid communication (§6) and confirm `link stayed up`.
3. Disconnect the USB cable.
4. **ESP32 side:** with no valid message for **1000 ms**, the ESP32 performs its communication-loss safe stop, latches fault **2** (`COMMS_LOST`) and `NOT_READY`, and keeps streaming heartbeats/telemetry (protocol §7 rule 3, flow F).
5. **Laptop side:** after **1500 ms** without heartbeat/telemetry the link declares the ESP32 lost, sends one emergency `CMD_STOP` (best effort), and freezes command issuance (flow F; `Esp32Lost`).
6. **Reconnect** the cable (keeping board power continuous).
7. **Critical check — heartbeat does NOT clear the latch:** the laptop resumes heartbeats immediately, but the ESP32 must **stay** `NOT_READY`; a `CMD_MOVE` must still be rejected with `RESP_ERR NOT_READY`.
8. **Re-arm:** send `CMD_RESET scope=all` → `RESP_OK`; only then is motion re-enabled.
9. **Negative checks:** confirm `scope=faults` alone does **not** re-arm, and that no message other than `CMD_RESET scope=all` clears the latch.

> Do not claim any of this has been verified on hardware yet — the native tests verify the engine behavior in software; the bench session is the first hardware evidence.

---

## 8. PASS/FAIL checklist

Fill `Actual` / `PASS/FAIL` / `Notes` during the session; record values, timestamps, and any anomalies.

| # | Test | Expected | Actual | PASS/FAIL | Notes |
|---|---|---|---|---|---|
| 1 | Repo clean pre-check (§2.1) | clean (venv/.pio ignored) | | | |
| 2 | Python suite (§2.2) | 48 OK | | | |
| 3 | Native suite (§2.3) | 39 passed | | | |
| 4 | Firmware build (§2.4) | build success | | | |
| 5 | COM port identified (§3) | port appears/disappears with cable | | | port number + bridge chip: ____ |
| 6 | Flash (§4) | upload SUCCESS | | | |
| 7 | Serial monitor opens at 115200 | readable JSON lines | | | |
| 8 | `EVT_BOOT` on reset (§5) | reason + `fw=0.1.0-skeleton` | | | reason: ____ |
| 9 | `TELE` cadence (§5) | ~20 Hz, placeholder values as §5 table | | | |
| 10 | `HEARTBEAT` cadence (§5) | ~500 ms | | | |
| 11 | Demo: `CMD_RESET scope=all` (§6) | `RESP_OK` | | | |
| 12 | Demo: `CMD_MOVE` (§6) | `RESP_ERR INTERNAL` ("not implemented") | | | |
| 13 | Demo: `CMD_STOP normal` (§6) | `RESP_OK` | | | |
| 14 | Demo: live TELE watch full duration (§6) | "link stayed up" | | | |
| 15 | Invalid command local rejection (§6) | `ValidationError` before send | | | |
| 16 | Disconnect test — ESP32 latch (§7) | fault 2 + `NOT_READY` after ~1 s | | | power method: ____ |
| 17 | Disconnect test — laptop lost (§7) | frozen after ~1.5 s | | | |
| 18 | Reconnect — heartbeat does NOT clear latch (§7) | still `NOT_READY`; `CMD_MOVE` → `NOT_READY` | | | |
| 19 | Re-arm only via `CMD_RESET scope=all` (§7) | `RESP_OK`; motion accepted after | | | |
| 20 | `scope=faults` does not re-arm (§7) | stays `NOT_READY` | | | |

---

## 9. Troubleshooting

Stick to what the current repository supports — no hardware-specific fixes beyond that.

| Symptom | Likely cause | Check / fix |
|---|---|---|
| **No COM port** | Charge-only cable; bad port; driver missing | Try another known-good data cable; replug; check Device Manager for the disappearing entry; try another USB port; check `pio device list`. (Bridge driver specifics are **OD-15**.) |
| **Upload failure / "Failed to connect to ESP32"** | Wrong port busy (monitor still open); cable; board in a bad state | Close the serial monitor; confirm the COM port; press and hold BOOT, start upload, release BOOT; retry with a different cable. |
| **Serial monitor unavailable** | Port in use by another program; wrong monitor speed | Close other programs using the port; confirm `monitor_speed = 115200` in `platformio.ini`; try `pio device monitor -b 115200`. |
| **No `EVT_BOOT` on reset** | Wrong baud; board not running the new firmware; TX line issue | Confirm monitor baud 115200; reflash; check for boot messages at 74880 (ROM) vs 115200 (app) — only the app log is relevant. |
| **Malformed/garbled telemetry** | Wrong baud; partial line; cable noise | Confirm 115200 8N1; check the laptop log counts `malformed` lines — a steady count under the demo indicates a link problem (record it; it is exactly the kind of observation **OD-15** needs). |
| **Laptop cannot connect** | Wrong port; firmware not running | Re-run §3 port identification; reflash and reset; watch the monitor for TELE before running the demo. |
| **Unexpected comms-loss latch** | Something is holding the port / flooding; wrong serial device | Confirm no other program opens the port; check laptop heartbeats are flowing (they keep the ESP32 alive); if latched, the correct recovery is `CMD_RESET scope=all` — do not work around the latch. |
| **Reset/re-arm failure** | Latch still active; wrong scope | Only `CMD_RESET scope=all` re-arms; confirm `RESP_OK`; check `TELE.faults` still lists the code before blaming the board. |

---

## 10. Stop conditions

Stop the session immediately and record what happened (§12) if any of these occur:

1. Smoke, heat, or unusual smell from the board/power.
2. The board fails to enumerate or repeatedly fails to flash (two attempts max before stopping to diagnose).
3. No `EVT_BOOT` / no telemetry after a confirmed successful flash — do not keep reflashing blindly.
4. Persistent malformed telemetry or link instability that looks like a hardware/link fault rather than a config error.
5. Unexpected faults appearing in `TELE.faults` that the runbook does not explain.
6. Any attempt to "work around" a latch or a `NOT_READY` state other than the documented `CMD_RESET scope=all` re-arm — that is a safety violation, not a workaround.
7. Anyone reaching for the board while it is powered without saying so (operator discipline).

---

## 11. Next stage (moving to Stage 2)

Stage 1 is complete only when **all** of these hold:

- [ ] §8 checklist rows 1–20: all PASS, Actuals recorded.
- [ ] Disconnect test (§7) passed with the power method noted.
- [ ] No unexplained faults, no stop conditions triggered (§10).
- [ ] Any anomalies are either resolved or recorded as open items for the team.

Only then proceed to **bring-up-plan Stage 3** (`HARDWARE_BRINGUP_PLAN.md` §3): bench power rails (regulator + battery input) **still without motors**. Do **not** connect drive motors, motor drivers, or the intake/dispenser until the bring-up plan's stage 4+ gates and its §6 safety requirements (physical e-stop verified first) are met.

---

## 12. Documentation rule

- This runbook's checklist is filled in **during** the bench session; the filled results belong in a separate session record (meeting notes / test report), **not** in this file's history section, so the procedure stays generic.
- If the physical test reveals a **new architectural, protocol, or interface decision** (e.g., "115200 shows corruption over the long cable" or "TELE order must change"), it must go through the normal change process: record it in `docs/DECISION_LOG.md` (per its §6 change-control rules) and update the authoritative document that lists it — **never** silently encode the discovery into code or into this runbook as if it were agreed.
- Hardware-specific values discovered during the session (COM port, bridge chip, timing measurements) are **TBD / hardware selection required** until recorded as decisions; nothing here resolves any OD-01…OD-16.

---

**Version history**

| Version | Date | Summary |
|---|---|---|
| 1.0 | (draft) | Initial Stage 1 runbook: pre-motor ESP32 + USB link bring-up procedure, expected skeleton behavior, disconnect test, checklist, troubleshooting, stop conditions, and stage-2 handoff criteria. |