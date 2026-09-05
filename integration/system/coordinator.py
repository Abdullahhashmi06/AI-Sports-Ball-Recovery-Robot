"""Thin system coordinator: wires the laptop-side pieces into one pipeline.

Owned by Person 1 (System Architecture & Integration Lead).  This is the
*laptop-side plumbing* — explicitly **not** the full physical state machine
(ARCHITECTURE.md §9) and not an autonomous controller:

    LaptopLink (on_tele / on_event)
        → from_telemetry → TelemetrySnapshot
        → RobotSystemState (state hub)
        → state-machine lifecycle / safety reflection
          (ApplicationStateMachine — laptop policy layer)
        → navigation input (state.navigation_input)
        → plan_navigation → NavigationOutput / MovementIntent
        → resolve_movement_command → CommandDirective
        → application-state safety gate
        → [caller applies via LaptopLink only]

Ownership boundary (see ``state_machine.py``): the **ESP32** owns physical
e-stops, motor/encoder/collection/ball truth and the real safety latches
(DEC-007/DEC-020); the laptop owns application lifecycle, mission context
and whether laptop-side policy currently *permits* a requested movement.

The coordinator therefore:

* **never transmits anything automatically** — a ``control_tick`` computes
  the plan + directive + gate state; issuing happens only through an
  explicit ``apply()``, which sends through ``LaptopLink.command`` (never
  around it — R-1/R-6);
* **owns an ``ApplicationStateMachine``** whose safety axis *reflects*
  ESP32-reported faults (``_reflect_esp32_safety``) and whose lifecycle is
  advanced only by an explicit ``complete_startup()`` (protocol §7 startup
  behaviour / flow A: EVT_BOOT + healthy TELE, then startup checks);
* gates normal motion through the machine (lifecycle ``STARTING`` and
  ``FAULT_LATCHED`` block) **and** through the physical checks (link not
  started / frozen, no telemetry, ESP32 fault(s), caller-supplied
  freshness budget);
* allows the safety-layer **emergency stop to always bypass the gate**
  (R-3, DEC-021, protocol §7) and latches laptop policy on a successful
  emergency ``CMD_STOP`` so nothing moves until a verified re-arm;
* clears the machine's fault reflection **only** via ``esp32_reset_all()``
  — a ``CMD_RESET scope=all`` exchange through ``LaptopLink`` whose
  ``RESP_OK`` is recorded as the re-arm (``note_esp32_rearm``).  The ESP32
  latch itself is cleared only by the ESP32;
* keeps MOVE intents *deferred* (CMD_MOVE needs lin/ang — OD-07/OD-13);
  nothing is fabricated, nothing is sent.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from integration.communication.laptop_link import LaptopLink
from integration.communication.protocol import inbound_shape_ok
from integration.system.commands import (
    CommandDirective,
    DirectiveKind,
    emergency_stop_directive,
    resolve_movement_command,
)
from integration.system.state import RobotSystemState
from integration.system.state_machine import (
    ApplicationStateMachine,
    LifecycleState,
    MissionState,
    SafetyCondition,
    TransitionResult,
)
from navigation.localization.state import Pose, WorldPoint  # noqa: F401  (re-exported types)
from navigation.localization.telemetry import TelemetryFormatError, from_telemetry
from navigation.planning.interfaces import (
    ExitHint,
    NavigationOutput,
)
from navigation.planning.planner import plan_navigation
from navigation.planning.zones import ZoneId

LOGGER = logging.getLogger("integration.system.coordinator")


class MotionGated(RuntimeError):
    """A normal-motion command was refused by the coordinator's gate."""


@dataclass(frozen=True)
class CoordinatorStatus:
    """Read-only observability snapshot of the coordinator (no timing side
    effects; deterministic given injected ``now``).

    ``telemetry_fresh`` and ``motion_gate_reason`` are only meaningful when a
    freshness budget was supplied to :meth:`SystemCoordinator.status`.
    ``faults`` is ``()`` when no telemetry has been seen yet (unknown, not
    "no faults"); ``cause`` records the last latched-event description from
    the application state machine.
    """

    lifecycle: LifecycleState
    safety: SafetyCondition
    mission: MissionState
    usable: bool
    boot_observed: bool
    has_telemetry: bool
    telemetry_seq: int
    telemetry_fresh: Optional[bool]
    has_pose: bool
    faults: Tuple[int, ...]
    cause: Optional[str]
    motion_gate_reason: Optional[str]


@dataclass(frozen=True)
class ControlTick:
    """Result of one control tick: plan, command directive, and gate state.

    ``gated`` reports whether normal motion is currently blocked; the plan
    and directive are still computed so the caller can see *why* and what
    would happen.  ``telemetry_fresh`` is ``None`` unless a freshness budget
    was supplied for this tick.
    """

    nav_output: NavigationOutput
    directive: CommandDirective
    gated: bool
    gate_reason: Optional[str]
    telemetry_seq: int
    has_pose: bool
    telemetry_fresh: Optional[bool]


class SystemCoordinator:
    """Attaches to a :class:`LaptopLink`, holds :class:`RobotSystemState`,
    and runs the telemetry → navigation → command pipeline on demand."""

    def __init__(
        self,
        link: LaptopLink,
        *,
        zones: Tuple[Any, ...] = (),
        useful_confidence_threshold: Optional[float] = None,
        machine: Optional[ApplicationStateMachine] = None,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.link = link
        self.state = RobotSystemState()
        self.machine = machine if machine is not None else ApplicationStateMachine()
        self._zones = tuple(zones)
        self._useful_threshold = useful_confidence_threshold
        self._log = logger or LOGGER
        self._boot_observed = False
        # Counters useful to tests/telemetry (malformed TELE etc.).
        self.stats: Dict[str, int] = {"invalid_telemetry": 0, "ignored": 0}
        # Claims the link's telemetry/liveness/event callbacks (single owner).
        self._attach()

    # -- link wiring --------------------------------------------------------

    def _attach(self) -> None:
        for name in ("on_tele", "on_event", "on_esp32_lost", "on_esp32_restored"):
            existing = getattr(self.link, name)
            if existing is not None:
                self._log.warning(
                    "SystemCoordinator overwrites an existing link.%s callback",
                    name,
                )
        self.link.on_tele = self._on_tele
        self.link.on_event = self._on_event
        self.link.on_esp32_lost = self._on_lost
        self.link.on_esp32_restored = self._on_restored

    def detach(self) -> None:
        """Release the link callbacks (e.g. before reusing the link)."""
        self.link.on_tele = None
        self.link.on_event = None
        self.link.on_esp32_lost = None
        self.link.on_esp32_restored = None

    def _on_tele(self, msg: Dict[str, Any]) -> None:
        try:
            snapshot = from_telemetry(msg)
        except TelemetryFormatError:
            # The link already drops structurally invalid frames; this is a
            # second, stricter net (value-level).  Never crash the reader.
            self.stats["invalid_telemetry"] += 1
            self._log.warning("coordinator dropped value-invalid TELE frame")
            return
        self.state.set_telemetry(snapshot, at=time.monotonic())
        # Safety reflection runs on the newest frame only (single-threaded
        # callback; the state hub was just updated above).
        self._reflect_esp32_safety()

    def _on_event(self, msg: Dict[str, Any]) -> None:
        """Observe boot only; every other event is ignored (no invented
        behaviour).  ESP32 boot semantics per protocol §4.2/§7."""
        if msg.get("type") == "EVT_BOOT":
            self._boot_observed = True

    @property
    def boot_observed(self) -> bool:
        """True once an ``EVT_BOOT`` frame has been received."""
        return self._boot_observed

    def _on_lost(self) -> None:  # pragma: no cover - bookkeeping only
        self._log.warning("ESP32 link lost (coordinator observes link state)")

    def _on_restored(self) -> None:  # pragma: no cover - bookkeeping only
        self._log.info("ESP32 link restored")

    def _reflect_esp32_safety(self) -> None:
        """Reflect ESP32-authoritative faults into the machine (DEC-020).

        Runs on every applied TELE frame: while the machine's safety axis is
        NOMINAL and the ESP32 reports latched fault(s), the machine latches.
        It deliberately never un-latches here — clearing the reflection
        requires a verified ``CMD_RESET scope=all`` exchange (see
        :meth:`esp32_reset_all`), because the ESP32 owns the real latch.
        """
        if self.machine.safety is SafetyCondition.NOMINAL and self.state.has_faults:
            self.machine.fault_detected(self.state.faults)

    # -- motion gate --------------------------------------------------------

    def motion_gate(
        self,
        *,
        telemetry_max_age_s: Optional[float] = None,
        now: Optional[float] = None,
    ) -> Optional[str]:
        """Reason normal motion is blocked, or ``None`` when it is not.

        Gate rules (all derived from the authoritative documents — none of
        these semantics are invented here):

        * link must be started and not frozen (``LaptopLink`` owns the
          ESP32-lost / version-mismatch determination, protocol §7);
        * the application state machine must be past ``STARTING`` (lifecycle
          is advanced explicitly via :meth:`complete_startup`);
        * the machine's safety axis must be ``NOMINAL`` (FAULT_LATCHED is a
          laptop-policy reflection of ESP32 faults *or* a laptop-initiated
          emergency stop — both block until a verified re-arm);
        * at least one TELE frame must have been received (no telemetry =>
          status unknown, so no motion);
        * the ESP32 must not be reporting fault(s) (DEC-020) — a direct,
          frame-level check independent of the machine reflection;
        * optionally, telemetry must not be older than ``telemetry_max_age_s``
          (only when the caller supplies a budget — no default is invented).
        """
        if not self.link.started:
            return "link not started (call link.start() before commanding)"
        if self.link.frozen:
            return "link frozen: ESP32 lost or protocol version mismatch"
        if self.machine.safety is SafetyCondition.FAULT_LATCHED:
            return (
                "ESP32 fault-latched (laptop policy reflection; re-arm with "
                "CMD_RESET scope=all via esp32_reset_all required)"
            )
        if self.machine.lifecycle is not LifecycleState.READY:
            return "application startup not complete (lifecycle STARTING)"
        if not self.state.has_telemetry:
            return "no ESP32 telemetry received yet"
        if self.state.has_faults:
            codes = ", ".join(str(c) for c in self.state.faults)
            return f"ESP32 reports fault(s): {codes} (latched until CMD_RESET)"
        if telemetry_max_age_s is not None:
            if not self.state.telemetry_fresh(now or time.monotonic(), telemetry_max_age_s):
                return "ESP32 telemetry stale (older than the supplied budget)"
        return None

    # -- in-process ingestion (deterministic replay / observability) --------

    def ingest_message(self, msg: Dict[str, Any]) -> None:
        """Ingest one **already-decoded** protocol message in-process.

        This is the same intake path ``LaptopLink._dispatch`` uses for the
        frames the coordinator consumes — ``TELE`` passes the protocol
        structural guard (``inbound_shape_ok``) then updates the state hub
        and the machine's fault reflection; ``EVT_BOOT`` is observed.
        Framing, JSON decoding, version and length checks belong to
        ``LaptopLink`` (protocol §2/§3) and are *not* repeated here, so
        callers must supply ``decode_line``-kind ``"ok"`` messages.

        Intended for deterministic replay / synthetic scenario tests and for
        in-process producers — it is **not** a transport and never bypasses
        ``RobotSystemState`` or ``LaptopLink`` (commands still flow only via
        ``apply()``).  Unknown types are counted in ``stats["ignored"]`` and
        logged; they create no behaviour.
        """
        if not isinstance(msg, dict):
            raise TypeError(
                f"ingest_message requires a decoded protocol dict, got "
                f"{type(msg).__name__}"
            )
        mtype = msg.get("type")
        if mtype == "TELE":
            if not inbound_shape_ok(msg):
                self.stats["ignored"] += 1
                self._log.warning("ingested TELE failed structural validation; dropped")
                return
            self._on_tele(msg)
            return
        if mtype == "EVT_BOOT":
            self._on_event(msg)
            return
        self.stats["ignored"] += 1
        self._log.warning("ingest_message ignored non-consumed type %r", mtype)

    def status(
        self,
        *,
        telemetry_max_age_s: Optional[float] = None,
        now: Optional[float] = None,
    ) -> CoordinatorStatus:
        """Read-only observability snapshot (lifecycle, safety, mission,
        freshness, faults, motion-gate reason).  Deterministic given an
        injected ``now``; ``telemetry_fresh``/``motion_gate_reason`` are
        computed only when a freshness budget is supplied."""
        gate = self.motion_gate(telemetry_max_age_s=telemetry_max_age_s, now=now)
        fresh: Optional[bool] = None
        if telemetry_max_age_s is not None:
            fresh = self.state.telemetry_fresh(
                now or time.monotonic(), telemetry_max_age_s
            )
        return CoordinatorStatus(
            lifecycle=self.machine.lifecycle,
            safety=self.machine.safety,
            mission=self.machine.mission,
            usable=self.machine.usable,
            boot_observed=self._boot_observed,
            has_telemetry=self.state.has_telemetry,
            telemetry_seq=self.state.telemetry_seq,
            telemetry_fresh=fresh,
            has_pose=self.state.has_pose,
            faults=self.state.faults,
            cause=self.machine.cause,
            motion_gate_reason=gate,
        )

    # -- pipeline -----------------------------------------------------------

    def control_tick(
        self,
        *,
        target: Any = None,
        zone: Optional[ZoneId] = None,
        hint: Optional[ExitHint] = None,
        useful_confidence_threshold: Optional[float] = None,
        telemetry_max_age_s: Optional[float] = None,
        now: Optional[float] = None,
    ) -> ControlTick:
        """Run one full planning + command-boundary pass (never sends).

        ``target``/``zone`` come from the coordination task, ``hint`` from
        Module 1 (I-1, a search hint — not ground truth).  Returns the plan,
        the resolved directive and the gate state for the caller to act on.
        """
        threshold = (
            self._useful_threshold
            if useful_confidence_threshold is None
            else useful_confidence_threshold
        )
        nav_in = self.state.navigation_input(
            target=target,
            zone=zone,
            hint=hint,
            useful_confidence_threshold=threshold,
        )
        nav_out = plan_navigation(nav_in, zones=self._zones)
        directive = resolve_movement_command(nav_out.movement)
        gate = self.motion_gate(telemetry_max_age_s=telemetry_max_age_s, now=now)

        fresh: Optional[bool] = None
        if telemetry_max_age_s is not None:
            fresh = self.state.telemetry_fresh(now or time.monotonic(), telemetry_max_age_s)

        return ControlTick(
            nav_output=nav_out,
            directive=directive,
            gated=gate is not None,
            gate_reason=gate,
            telemetry_seq=self.state.telemetry_seq,
            has_pose=self.state.has_pose,
            telemetry_fresh=fresh,
        )

    # -- lifecycle / re-arm (explicit driver entry points) -------------------

    def complete_startup(self) -> TransitionResult:
        """Advance the application lifecycle ``STARTING → READY`` (explicit).

        Mirrors protocol §7 startup behaviour / flow A: the laptop waits for
        ``EVT_BOOT`` plus healthy TELE frames before commanding, and the
        ESP32 completes its startup checks.  Nothing auto-promotes the
        lifecycle — the application driver calls this once after observing
        the prerequisites.  The ESP32's own READY gating (startup checks)
        remains ESP32-authoritative and is enforced by the ESP32 rejecting
        commands with ``NOT_READY`` until then.
        """
        if not self._boot_observed:
            return TransitionResult(False, "EVT_BOOT not observed yet", self.machine.state)
        if not self.link.started:
            return TransitionResult(False, "link not started", self.machine.state)
        if self.link.frozen:
            return TransitionResult(False, "link frozen", self.machine.state)
        if not self.state.has_telemetry:
            return TransitionResult(False, "no ESP32 telemetry received yet", self.machine.state)
        if self.state.has_faults:
            codes = ", ".join(str(c) for c in self.state.faults)
            return TransitionResult(
                False,
                f"ESP32 reports fault(s): {codes} — resolve and reset first",
                self.machine.state,
            )
        return self.machine.startup_complete()

    def esp32_reset_all(self, timeout: Optional[float] = None) -> Dict[str, Any]:
        """Re-arm path: send ``CMD_RESET scope=all`` through ``LaptopLink``.

        Exempt from the normal motion gate — a latched ESP32 must be able to
        re-arm.  Issued only through ``LaptopLink`` (never direct transport).
        On ``RESP_OK`` the machine records the verified re-arm
        (``note_esp32_rearm``), returning its safety reflection to NOMINAL.
        The ESP32's real latch is cleared by the ESP32 itself.
        """
        resp = self.link.command("CMD_RESET", scope="all", timeout=timeout)
        if resp.get("type") == "RESP_OK":
            self.machine.note_esp32_rearm()
        return resp

    # -- command issuance (explicit, gated) ---------------------------------

    def apply(
        self,
        directive: CommandDirective,
        *,
        timeout: Optional[float] = None,
    ) -> Optional[Dict[str, Any]]:
        """Issue a directive through ``LaptopLink`` and return the RESP.

        * ``NONE`` / ``DEFERRED`` → returns ``None`` (nothing is sent; a
          deferred MOVE has no wire form yet — OD-07/OD-13).
        * ``SEND`` normal commands → refused with :class:`MotionGated` while
          the motion gate is closed.
        * ``SEND`` emergency ``CMD_STOP`` → always issued (R-3 / DEC-021);
          the link transport is the only remaining gate.
        """
        if not isinstance(directive, CommandDirective):
            raise TypeError(
                f"apply requires a CommandDirective, got {type(directive).__name__}"
            )
        if directive.kind is not DirectiveKind.SEND:
            return None  # nothing expressible on the wire (NONE / DEFERRED)

        fields = dict(directive.fields or {})
        is_emergency = (
            directive.message_type == "CMD_STOP"
            and fields.get("mode") == "emergency"
        )
        if not is_emergency:
            reason = self.motion_gate()
            if reason is not None:
                raise MotionGated(
                    f"normal motion gated: {reason} (directive not sent)"
                )
        resp = self.link.command(directive.message_type, timeout=timeout, **fields)
        if is_emergency:
            # Laptop policy latch: an emergency stop was successfully issued,
            # so laptop-side policy must not allow normal motion again until a
            # verified ESP32 re-arm (CMD_RESET scope=all).  The ESP32's own
            # latch/mirror follows via EVT_FAULT/TELE; this closes the gap.
            self.machine.emergency_stop(
                cause="emergency CMD_STOP accepted by the ESP32"
            )
        return resp


__all__ = [
    "ControlTick",
    "CoordinatorStatus",
    "MotionGated",
    "SystemCoordinator",
]

