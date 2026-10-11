# Automatic One-Ball Dispensing

Required feature, proposed hardware sequence. This document describes mechanical
and integration work for P4/P5; it does not implement firmware or resolve OD-11.

## Geometry and Singulation

A sloped hopper feeds a 76 mm bore. Two sliding servo gates are 80 mm apart;
their 4 mm thickness leaves a 76 mm pocket between closed faces. This pocket
can contain one nominal 66 mm ball, but not two complete regulation padel balls.
That geometric fact does not guarantee singulation: a second ball can protrude
through the upper gate plane and cause pinching or bridging.

The upper gate needs a rounded/beveled leading edge, compliant contact surface,
force-limited drive and confirmed closure. If closure is blocked, do not open
the lower gate. P4 must prototype separation of the next ball from the pocket,
adjust the hopper/retainer geometry and test worn/dirty balls. No two-ball release
is accepted as a normal outcome. The CAD linkage is a packaging illustration,
not a proved servo-stroke mechanism.

## Proposed Cycle

| Step | Action | Required evidence before advancing |
| --- | --- | --- |
| 1 | At the dispensing location, stop drive and suction intake | Existing READY/safety rules hold; intake inactive; no motion |
| 2 | Open the receiver equalization vent with both gates closed | Receiver pressure returns to an experimentally established near-ambient band |
| 3 | Confirm lower gate closed, then open upper gate | Lower closed-position signal healthy |
| 4 | Allow one ball into the metering chamber | Pocket sensor transition within measured timeout |
| 5 | Close upper gate and isolate queued balls | Upper closed-position signal; no jam/overcurrent |
| 6 | Open lower gate | Upper closure still confirmed; exactly one pocket occupancy |
| 7 | Observe ball leaving the rear chute | One valid exit break-beam sequence; debounce/timeout calibrated |
| 8 | Close lower gate; close vent before later collection | Lower closed signal; exit clear; pressure/valve checks pass |
| 9 | Report successful completion | ESP32 updates count once and emits existing EVT_DISPENSE status ok |

The two gate stages adapt the existing servo-gate intent; they need no new wire
message to request one ball. Existing protocol semantics remain authoritative:
CMD_DISPENSE count=1 for MVP, BUSY for intake/dispense overlap, count unchanged on
failure, and dispense fault 9. P1/P5 must agree the exact local sequence and
sensor mapping before physical implementation. No pin assignments, servo angles,
timeout durations or pressure thresholds are set here.

## Failure Handling

- Empty pocket/bin: confirm physical state and reconcile with the existing
  ESP32-authoritative count rules; an empty release does not decrement count.
- Upper gate blocked: halt the cycle, keep the lower gate closed, and use bounded
  recovery after testing. Do not force a gate through a ball.
- No exit confirmation or unexpected extra exit: fail the cycle and investigate
  count reconciliation under the existing fault/ownership rules.
- Sensor disagreement, e-stop or communications loss: stop intake and dispensing;
  preserve latch/reset behavior. Do not manufacture a successful release event.
- Power-loss closure: a hobby servo alone is not a fail-closed mechanism. Spring
  return/appropriate actuator, linkage, trapping risk and recovery need physical
  design and verification before powered operation.

Do not run collection while the receiver vent or an isolation gate is open.
Do not rely on blower spin-down time alone as evidence of pressure equalization.

## Bench Acceptance Plan

| Trial | Record | Proposed acceptance for review |
| --- | --- | --- |
| Smallest/largest intended balls; new/worn/dirty | Gate clearance, pinch marks, jam frequency | No damaging contact; every failure detected |
| One ball and full queue | Pocket detection and outlet count | Exactly one confirmed release per accepted request |
| At least 100 commanded one-ball cycles | Attempts, successes, double feeds, missed feeds | Zero unreported double/missed releases; report actual reliability |
| Blocked gate/chute and disconnected sensor | Current, position signals, fault outcome | No forced advancement; no false ok/count decrement |
| Intake/dispense overlap | Command rejection, actuator states | Existing BUSY exclusion preserved |
| Duplicate command | Physical release count | Existing duplicate handling prevents a second release |
| Vent/pressure fault | Pressure trace and gate states | Gates remain closed until equalization is confirmed |
| E-stop, communications loss and power loss | Gate/blower/drive response | Existing latch rules; physical safe state proven |

Trials are planned, not passed. Timing, force, stroke, capacity and verification
sensor decisions remain OD-10/OD-11/OD-14 or a team-reviewed extension as needed.

The isolation/metering principle is analogous to industrial double-gate airlocks;
it is not a supplier-certified ball dispenser. See the sources in the materials
document. Our implementation deliberately stops suction and equalizes pressure
before dispensing, rather than claiming continuous vacuum airlock operation.
