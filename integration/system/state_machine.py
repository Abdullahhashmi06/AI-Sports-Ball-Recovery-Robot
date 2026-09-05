"""Application state-machine skeleton (integration layer, owned by Person 1).

A **pure, deterministic orchestration-layer skeleton** for the laptop-side
application lifecycle.  It models three concerns separately — they are
deliberately *not* collapsed into one giant enum:

1. **Lifecycle** — whether the application/robot is usable yet.
   ``STARTING`` → ``READY`` mirrors the startup behaviour of
   ``COMMUNICATION_PROTOCOL.md`` §7 and flow A (the laptop waits for
   ``EVT_BOOT`` plus healthy ``TELE`` frames, and the ESP32 completes its
   startup checks, before the controller executes motion/intake/dispense
   commands).  The caller invokes ``startup_complete()`` only after it has
   observed those conditions.

2. **Safety** — ``NOMINAL`` / ``FAULT_LATCHED``.  This is a laptop-side
   *reflection* of the ESP32's latched fail-safe state (DEC-007).  The real
   latch is owned by the ESP32 and exits only via ``CMD_RESET scope=all`` on
   the ESP32 (protocol §7).  This machine can **never** clear it itself:
   ``note_esp32_rearm()`` merely records that the caller performed and
   verified that reset.  No ESP32 latch logic is duplicated here.

3. **Mission** — the current objective/context, using the coarse labels of
   ``ARCHITECTURE.md`` §9: ``IDLE``, ``NAVIGATING`` (NAVIGATE_TO_ZONE /
   REPLAN), ``SEARCHING`` (SEARCH / EXPAND_SEARCH), ``APPROACHING``
   (APPROACH / REPOSITION), ``COLLECTING`` (COLLECT / VERIFY / STORE),
   ``RETURNING`` (RETURN_BASE).  Mission labels are **context only**: no
   behaviour is attached to any of them here, and entering ``NAVIGATING``
   does **not** produce movement — CMD_MOVE stays *deferred* in the command
   boundary (OD-07 / OD-13).  Fine-grained §9 staging states driven purely
   by perception / task-directive events (RALLY_ACTIVE, RALLY_ENDED,
   PREDICT_EXIT_ZONE, BALL_FOUND, separate VERIFY/STORE, EXPAND_SEARCH, …)
   are deliberately **not modeled yet**: their triggers belong to Module 1
   (I-1/I-2) and the state-machine ↔ navigation directive set (I-3/I-4),
   which stays indicative until OD-03 resolves.

Semantics:

* ``emergency_stop`` / ``fault_detected`` may fire from **any** combination;
  they latch safety and abort any active mission to ``IDLE`` (§9: an e-stop
  stops the robot from any state; recovery = explicit re-arm → IDLE).
* ``request_stop`` is the *normal* stop: mission → ``IDLE`` while NOMINAL
  (the ESP32 CMD_STOP mode=normal itself is a separate, command-boundary /
  Module 2 action, never generated here).
* Undefined or unsupported transitions are **rejected** with an explicit
  ``ok=False`` result and a reason — never silently invented.

This module imports **only the Python standard library**: no transport, no
protocol, no navigation, no threading, no hardware, no external packages.
The wiring between this machine and the rest of the system (``LaptopLink``,
``RobotSystemState``, ``SystemCoordinator``) happens in the *caller* (see
the coordinator tests); the machine itself never touches them.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Optional, Tuple


class LifecycleState(enum.Enum):
    """Application lifecycle: is the robot usable yet?"""

    STARTING = "starting"  # awaiting EVT_BOOT + healthy TELE + startup checks
    READY = "ready"        # startup complete; robot available (idle or active)


class SafetyCondition(enum.Enum):
    """Laptop-side reflection of the ESP32 fail-safe latch (DEC-007).

    Only the caller-performed ESP32 re-arm (CMD_RESET scope=all) is recorded
    as clearing it — this machine cannot clear the ESP32's real latch.
    """

    NOMINAL = "nominal"
    FAULT_LATCHED = "fault_latched"


class MissionState(enum.Enum):
    """Current objective/context — coarse ARCHITECTURE.md §9 labels.

    Context only: no behaviour is attached, and no command is ever generated
    by setting one of these (see module docstring).
    """

    IDLE = "idle"
    NAVIGATING = "navigating"      # §9 NAVIGATE_TO_ZONE / REPLAN
    SEARCHING = "searching"        # §9 SEARCH / EXPAND_SEARCH
    APPROACHING = "approaching"    # §9 APPROACH / REPOSITION
    COLLECTING = "collecting"      # §9 COLLECT / VERIFY / STORE
    RETURNING = "returning"        # §9 RETURN_BASE


@dataclass(frozen=True)
class ApplicationStateView:
    """Read-only snapshot of the three axes at one moment."""

    lifecycle: LifecycleState
    safety: SafetyCondition
    mission: MissionState

    @property
    def usable(self) -> bool:
        """True when the robot may be given normal work."""
        return (
            self.lifecycle is LifecycleState.READY
            and self.safety is SafetyCondition.NOMINAL
        )


@dataclass(frozen=True)
class TransitionResult:
    """Outcome of one state-machine operation (deterministic)."""

    ok: bool
    reason: str
    state: ApplicationStateView


class ApplicationStateMachine:
    """Pure application-lifecycle state machine (see module docstring).

    Deterministic and side-effect free: every operation only validates the
    request against the current state and returns a :class:`TransitionResult`
    with a snapshot of the (possibly unchanged) state.
    """

    def __init__(self) -> None:
        self._lifecycle = LifecycleState.STARTING
        self._safety = SafetyCondition.NOMINAL
        self._mission = MissionState.IDLE
        self._cause: Optional[str] = None
        self._estop_count = 0
        self._fault_count = 0
        self._rearm_count = 0
        self._startup_count = 0

    # -- inspection ---------------------------------------------------------

    @property
    def lifecycle(self) -> LifecycleState:
        return self._lifecycle

    @property
    def safety(self) -> SafetyCondition:
        return self._safety

    @property
    def mission(self) -> MissionState:
        return self._mission

    @property
    def usable(self) -> bool:
        return self._lifecycle is LifecycleState.READY and (
            self._safety is SafetyCondition.NOMINAL
        )

    @property
    def cause(self) -> Optional[str]:
        """Reason recorded for the current (or last) latched event."""
        return self._cause

    @property
    def estop_count(self) -> int:
        return self._estop_count

    @property
    def fault_count(self) -> int:
        return self._fault_count

    @property
    def rearm_count(self) -> int:
        return self._rearm_count

    @property
    def state(self) -> ApplicationStateView:
        return ApplicationStateView(self._lifecycle, self._safety, self._mission)

    def _result(self, ok: bool, reason: str) -> TransitionResult:
        return TransitionResult(ok=ok, reason=reason, state=self.state)

    # -- lifecycle ----------------------------------------------------------

    def startup_complete(self) -> TransitionResult:
        """Record that startup is complete (EVT_BOOT + healthy TELE + ESP32
        startup checks observed by the caller — protocol §7 / flow A).

        Idempotent once READY.  Rejected while fault-latched: a robot that
        has not been re-armed must not become ready.
        """
        if self._lifecycle is LifecycleState.READY:
            return self._result(True, "already ready")
        if self._safety is SafetyCondition.FAULT_LATCHED:
            return self._result(
                False,
                "cannot become ready while fault-latched (ESP32 re-arm "
                "with CMD_RESET scope=all required first)",
            )
        self._lifecycle = LifecycleState.READY
        self._startup_count += 1
        return self._result(True, "startup complete; robot ready")

    # -- mission (context only — never generates commands) ------------------

    def request_mission(self, mission: MissionState) -> TransitionResult:
        """Set the mission/objective context label.

        Only meaningful while the robot is usable (READY + NOMINAL); from
        any other combination the request is rejected and nothing changes.
        Mission labels carry no behaviour — no movement or actuation is ever
        produced here (OD-07/OD-13 keep CMD_MOVE deferred downstream).
        """
        if not isinstance(mission, MissionState):
            raise TypeError(
                f"mission must be a MissionState, got {type(mission).__name__}"
            )
        reason = self._usable_reason()
        if reason is not None:
            return self._result(False, reason)
        self._mission = mission
        return self._result(True, f"mission set to {mission.value}")

    def end_mission(self) -> TransitionResult:
        """Return the mission context to ``IDLE`` (objective completed)."""
        return self.request_mission(MissionState.IDLE)

    # -- stop ---------------------------------------------------------------

    def request_stop(self) -> TransitionResult:
        """Normal stop request: mission → ``IDLE`` while NOMINAL.

        This is *not* an emergency stop and does not latch anything; issuing
        the actual ``CMD_STOP`` (mode normal) to the ESP32 is a command-
        boundary / Module 2 action, never performed here.  While fault-
        latched the robot is already stopped, so the request is acknowledged
        as redundant rather than an error.
        """
        if self._safety is SafetyCondition.FAULT_LATCHED:
            return self._result(
                True,
                "already fault-latched and stopped; normal stop is redundant",
            )
        reason = self._usable_reason()
        if reason is not None:
            return self._result(False, reason)
        self._mission = MissionState.IDLE
        return self._result(True, "normal stop: mission cleared to IDLE")

    # -- safety (latch reflection — never cleared locally) ------------------

    def emergency_stop(self, cause: str = "emergency stop requested") -> TransitionResult:
        """Emergency stop from any state: latch safety, abort the mission.

        Idempotent and always accepted (a fail-safe must never be rejected).
        ``cause`` is recorded for inspection.  The ESP32's real e-stop /
        latch handling is authoritative and separate (protocol §7, DEC-007).
        """
        self._safety = SafetyCondition.FAULT_LATCHED
        self._mission = MissionState.IDLE
        self._cause = cause
        self._estop_count += 1
        return self._result(True, f"emergency stop latched: {cause}")

    def fault_detected(self, fault_codes: Tuple[int, ...]) -> TransitionResult:
        """Record ESP32-reported fault(s) (ESP32-authoritative, DEC-020).

        Latches safety and aborts any active mission — a fault can never
        leave the robot in an active mission state.  ``fault_codes`` are
        integers (the ESP32's codes, e.g. protocol §7 Appendix A); the range
        semantics belong to the firmware and are not re-checked here.
        """
        if not isinstance(fault_codes, (tuple, list)) or isinstance(
            fault_codes, (str, bytes)
        ):
            raise TypeError("fault_codes must be a tuple/list of int fault codes")
        if not fault_codes:
            raise ValueError("fault_detected requires at least one fault code")
        for code in fault_codes:
            if isinstance(code, bool) or not isinstance(code, int):
                raise ValueError("fault codes must be ints (not bool)")
        self._safety = SafetyCondition.FAULT_LATCHED
        self._mission = MissionState.IDLE
        self._cause = f"fault codes: {', '.join(str(c) for c in fault_codes)}"
        self._fault_count += 1
        return self._result(True, f"fault latched: {self._cause}")

    def note_esp32_rearm(self) -> TransitionResult:
        """Record a caller-performed ESP32 re-arm (``CMD_RESET scope=all``).

        Call this ONLY after the ESP32 accepted the reset and reports no
        latched faults.  This is bookkeeping: the machine itself cannot and
        must not clear the ESP32's latch (DEC-007).  Returns ``ok=False`` if
        nothing is latched.
        """
        if self._safety is not SafetyCondition.FAULT_LATCHED:
            return self._result(False, "not fault-latched — no re-arm to record")
        self._safety = SafetyCondition.NOMINAL
        self._cause = None
        self._rearm_count += 1
        return self._result(True, "ESP32 re-arm recorded; safety nominal")

    # -- reset --------------------------------------------------------------

    def reset(self) -> TransitionResult:
        """Reinitialize the application state to pristine ``STARTING/IDLE``.

        Safety is reset to ``NOMINAL`` **only when it is already nominal**.
        If ``FAULT_LATCHED`` is reflected, it is deliberately preserved: a
        local application reset must not pretend to clear the ESP32's real
        latch — the caller must still perform and record ``CMD_RESET
        scope=all`` on the ESP32 before the robot becomes usable again.
        """
        was_latched = self._safety is SafetyCondition.FAULT_LATCHED
        self._lifecycle = LifecycleState.STARTING
        self._mission = MissionState.IDLE
        self._cause = None
        if was_latched:
            # Keep the latch reflection; re-arm happens at the ESP32.
            return self._result(
                True,
                "application reset; ESP32 latch reflection preserved — "
                "perform CMD_RESET scope=all on the ESP32, then "
                "note_esp32_rearm()",
            )
        self._safety = SafetyCondition.NOMINAL
        self._estop_count = 0
        self._fault_count = 0
        self._rearm_count = 0
        self._startup_count = 0
        return self._result(True, "application reset to initial state")

    # -- helpers ------------------------------------------------------------

    def _usable_reason(self) -> Optional[str]:
        """Reason normal work is unavailable, or ``None`` when usable."""
        if self._safety is SafetyCondition.FAULT_LATCHED:
            return (
                "robot not usable: fault-latched — ESP32 re-arm with "
                "CMD_RESET scope=all required first"
            )
        if self._lifecycle is not LifecycleState.READY:
            return "robot not ready: startup not complete"
        return None


__all__ = [
    "ApplicationStateMachine",
    "ApplicationStateView",
    "LifecycleState",
    "MissionState",
    "SafetyCondition",
    "TransitionResult",
]
