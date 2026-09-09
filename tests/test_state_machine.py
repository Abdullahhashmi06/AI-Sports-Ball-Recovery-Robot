"""Tests for the application state-machine skeleton (integration layer, P1).

This skeleton represents **application lifecycle only** — it is an
orchestration layer and must not, by itself, cause any robot behaviour:

* no movement, PWM, lin/ang, intake/dispense, or protocol traffic;
* no transport, no threading, no hardware — the machine is pure policy over
  three explicit axes (lifecycle / safety / mission) so future perception,
  navigation, collection and hardware integration can plug in later.

Authoritative grounding (see ``state_machine.py`` docstrings):

* lifecycle  — protocol §7 startup behaviour and flow A (EVT_BOOT + healthy
  TELE before commanding; ESP32 startup checks -> READY);
* safety     — DEC-007 latching: the real latch is owned by the ESP32 and
  exits only via ``CMD_RESET scope=all``.  This laptop-side machine only
  *reflects* it and can never clear it itself;
* mission    — coarse labels of ARCHITECTURE.md §9 (IDLE / NAVIGATING /
  SEARCHING / APPROACHING / COLLECTING / RETURNING).  Mission labels are
  context only: entering NAVIGATING produces no movement command — the
  existing command boundary defers CMD_MOVE (OD-07/OD-13).
"""

from __future__ import annotations

import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from integration.communication.laptop_link import LaptopLink  # noqa: E402
from integration.communication.transport import create_memory_pair  # noqa: E402
from integration.system.coordinator import SystemCoordinator  # noqa: E402
from integration.system.state_machine import (  # noqa: E402
    ApplicationStateMachine,
    ApplicationStateView,
    LifecycleState,
    MissionState,
    SafetyCondition,
    TransitionResult,
)

from fake_esp32 import FakeEsp32  # noqa: E402

ALL_MISSIONS = tuple(MissionState)


def _wait_until(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()



class TestInitialState(unittest.TestCase):
    def test_machine_starts_safe_and_unusable(self):
        m = ApplicationStateMachine()
        self.assertEqual(m.lifecycle, LifecycleState.STARTING)
        self.assertEqual(m.safety, SafetyCondition.NOMINAL)
        self.assertEqual(m.mission, MissionState.IDLE)
        self.assertFalse(m.usable)
        v = m.state
        self.assertIsInstance(v, ApplicationStateView)
        self.assertEqual(v.lifecycle, LifecycleState.STARTING)

    def test_no_movement_or_hardware_at_construction(self):
        # Purity guard: the machine module must not reference transport,
        # protocol, navigation, or the coordinator (no side effects possible).
        import integration.system.state_machine as sm

        for forbidden in (
            "transport",
            "laptop_link",
            "commands",
            "coordinator",
            "protocol",
            "serial",
            "navigation",
        ):
            with open(sm.__file__, encoding="utf-8") as fh:
                self.assertNotIn(f"import {forbidden}", fh.read())
                self.assertNotIn(f"from {forbidden}", fh.read())


class TestLifecycle(unittest.TestCase):
    def test_startup_complete_reaches_ready(self):
        m = ApplicationStateMachine()
        r = m.startup_complete()
        self.assertTrue(r.ok)
        self.assertEqual(m.lifecycle, LifecycleState.READY)
        self.assertTrue(m.usable)

    def test_repeated_startup_is_idempotent(self):
        m = ApplicationStateMachine()
        m.startup_complete()
        r = m.startup_complete()
        self.assertTrue(r.ok)
        self.assertEqual(m.lifecycle, LifecycleState.READY)

    def test_cannot_become_ready_while_fault_latched(self):
        m = ApplicationStateMachine()
        m.emergency_stop(cause="test e-stop")
        r = m.startup_complete()
        self.assertFalse(r.ok)
        self.assertEqual(m.lifecycle, LifecycleState.STARTING)
        self.assertEqual(m.safety, SafetyCondition.FAULT_LATCHED)


class TestMissions(unittest.TestCase):
    def _ready_machine(self):
        m = ApplicationStateMachine()
        m.startup_complete()
        return m

    def test_all_mission_labels_accepted_when_ready(self):
        for mission in ALL_MISSIONS:
            m = self._ready_machine()
            r = m.request_mission(mission)
            self.assertTrue(r.ok, mission)
            self.assertEqual(m.mission, mission)

    def test_mission_requires_ready_and_nominal(self):
        m = ApplicationStateMachine()  # still STARTING
        self.assertFalse(m.request_mission(MissionState.NAVIGATING).ok)
        self.assertEqual(m.mission, MissionState.IDLE)

        m2 = self._ready_machine()
        m2.emergency_stop(cause="test")
        self.assertFalse(m2.request_mission(MissionState.SEARCHING).ok)
        self.assertEqual(m2.mission, MissionState.IDLE)  # aborted by e-stop

    def test_mission_switch_is_explicit_context_change(self):
        m = self._ready_machine()
        m.request_mission(MissionState.NAVIGATING)
        r = m.request_mission(MissionState.SEARCHING)
        self.assertTrue(r.ok)
        self.assertEqual(m.mission, MissionState.SEARCHING)

    def test_end_mission_returns_to_idle(self):
        m = self._ready_machine()
        m.request_mission(MissionState.COLLECTING)
        r = m.end_mission()
        self.assertTrue(r.ok)
        self.assertEqual(m.mission, MissionState.IDLE)

    def test_invalid_mission_argument_is_programmer_error(self):
        m = self._ready_machine()
        with self.assertRaises(TypeError):
            m.request_mission("NAVIGATING")  # type: ignore[arg-type]

    def test_setting_mission_never_generates_commands(self):
        # Entering every mission label changes state only — the machine has
        # no transport and no command concept; nothing can be "sent".
        m = self._ready_machine()
        for mission in ALL_MISSIONS:
            self.assertTrue(m.request_mission(mission).ok)


class TestStop(unittest.TestCase):
    def test_normal_stop_returns_mission_to_idle(self):
        m = ApplicationStateMachine()
        m.startup_complete()
        m.request_mission(MissionState.NAVIGATING)
        r = m.request_stop()
        self.assertTrue(r.ok)
        self.assertEqual(m.mission, MissionState.IDLE)
        self.assertEqual(m.safety, SafetyCondition.NOMINAL)  # not a latch
        self.assertTrue(m.usable)

    def test_repeated_stop_is_safe(self):
        m = ApplicationStateMachine()
        m.startup_complete()
        self.assertTrue(m.request_stop().ok)
        self.assertTrue(m.request_stop().ok)
        self.assertEqual(m.mission, MissionState.IDLE)

    def test_stop_during_fault_is_redundant_not_an_error(self):
        m = ApplicationStateMachine()
        m.emergency_stop(cause="test")
        r = m.request_stop()
        self.assertTrue(r.ok)  # already stopped by the latch
        self.assertEqual(m.safety, SafetyCondition.FAULT_LATCHED)


class TestSafety(unittest.TestCase):
    def test_emergency_stop_always_reaches_latched_idle(self):
        for setup in (
            lambda m: None,  # from STARTING
            lambda m: m.startup_complete(),
            lambda m: (m.startup_complete(),
                       m.request_mission(MissionState.COLLECTING)),
        ):
            m = ApplicationStateMachine()
            setup(m)
            r = m.emergency_stop(cause="test e-stop")
            self.assertTrue(r.ok)
            self.assertEqual(m.safety, SafetyCondition.FAULT_LATCHED)
            self.assertEqual(m.mission, MissionState.IDLE)
            self.assertFalse(m.usable)

    def test_fault_detected_never_leaves_an_active_mission(self):
        m = ApplicationStateMachine()
        m.startup_complete()
        m.request_mission(MissionState.NAVIGATING)
        r = m.fault_detected((6,))
        self.assertTrue(r.ok)
        self.assertEqual(m.safety, SafetyCondition.FAULT_LATCHED)
        self.assertEqual(m.mission, MissionState.IDLE)  # task aborted
        self.assertFalse(m.usable)

    def test_fault_prevents_new_missions_until_rearm(self):
        m = ApplicationStateMachine()
        m.startup_complete()
        m.fault_detected((1,))
        self.assertFalse(m.request_mission(MissionState.SEARCHING).ok)

    def test_repeated_emergency_stop_is_idempotent(self):
        m = ApplicationStateMachine()
        m.startup_complete()
        self.assertTrue(m.emergency_stop(cause="first").ok)
        self.assertEqual(m.estop_count, 1)
        r = m.emergency_stop(cause="second")
        self.assertTrue(r.ok)
        self.assertEqual(m.safety, SafetyCondition.FAULT_LATCHED)
        self.assertEqual(m.estop_count, 2)  # recorded, state unchanged
        self.assertEqual(m.cause, "second")

    def test_fault_detected_requires_codes(self):
        m = ApplicationStateMachine()
        with self.assertRaises(TypeError):
            m.fault_detected("6")  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            m.fault_detected(())
        with self.assertRaises(ValueError):
            m.fault_detected((True,))  # type: ignore[arg-type]

    def test_rearm_only_records_esp32_reset(self):
        m = ApplicationStateMachine()
        m.startup_complete()
        # Calling rearm without a latch is a no-op with an explicit reason.
        r = m.note_esp32_rearm()
        self.assertFalse(r.ok)
        m.emergency_stop(cause="test")
        r = m.note_esp32_rearm()
        self.assertTrue(r.ok)
        self.assertEqual(m.safety, SafetyCondition.NOMINAL)
        self.assertTrue(m.usable)
        self.assertEqual(m.rearm_count, 1)

    def test_machine_cannot_clear_latch_without_rearm_event(self):
        # The machine is pure bookkeeping: nothing except the explicit
        # note_esp32_rearm() (which documents a caller-performed CMD_RESET
        # scope=all on the ESP32) can return safety to NOMINAL.
        m = ApplicationStateMachine()
        m.startup_complete()
        m.emergency_stop(cause="test")
        for op in (lambda: m.request_mission(MissionState.NAVIGATING),
                   lambda: m.end_mission(),
                   lambda: m.request_stop()):
            op()
        self.assertEqual(m.safety, SafetyCondition.FAULT_LATCHED)


class TestReset(unittest.TestCase):
    def test_reset_returns_to_pristine_when_nominal(self):
        m = ApplicationStateMachine()
        m.startup_complete()
        m.request_mission(MissionState.RETURNING)
        r = m.reset()
        self.assertTrue(r.ok)
        self.assertEqual(m.lifecycle, LifecycleState.STARTING)
        self.assertEqual(m.safety, SafetyCondition.NOMINAL)
        self.assertEqual(m.mission, MissionState.IDLE)

    def test_repeated_reset_is_safe(self):
        m = ApplicationStateMachine()
        m.reset()
        r = m.reset()
        self.assertTrue(r.ok)
        self.assertEqual(m.lifecycle, LifecycleState.STARTING)

    def test_reset_does_not_clear_esp32_latch_reflection(self):
        # DEC-007: clearing the *application* state must not pretend to clear
        # the ESP32's latched safety state — that needs CMD_RESET scope=all
        # on the ESP32.  The FAULT_LATCHED reflection therefore survives a
        # local reset.
        m = ApplicationStateMachine()
        m.startup_complete()
        m.emergency_stop(cause="test")
        r = m.reset()
        self.assertTrue(r.ok)
        self.assertEqual(m.lifecycle, LifecycleState.STARTING)
        self.assertEqual(m.safety, SafetyCondition.FAULT_LATCHED)
        self.assertFalse(m.usable)
        # ...and only the caller-performed ESP32 re-arm clears it.
        m.note_esp32_rearm()
        self.assertEqual(m.safety, SafetyCondition.NOMINAL)


class TestResultsAndDeterminism(unittest.TestCase):
    def test_transition_result_snapshot_matches_machine(self):
        m = ApplicationStateMachine()
        m.startup_complete()
        r = m.request_mission(MissionState.NAVIGATING)
        self.assertIsInstance(r, TransitionResult)
        self.assertEqual(r.state.mission, m.mission)
        self.assertEqual(r.state.lifecycle, m.lifecycle)
        self.assertEqual(r.state.safety, m.safety)

    def test_rejected_transition_leaves_state_unchanged(self):
        m = ApplicationStateMachine()
        before = m.state
        r = m.request_mission(MissionState.NAVIGATING)  # STARTING -> rejected
        self.assertFalse(r.ok)
        self.assertIn("not", r.reason.lower())
        self.assertEqual(m.state, before)

    def test_deterministic_rejection_reason(self):
        a, b = ApplicationStateMachine(), ApplicationStateMachine()
        self.assertEqual(
            a.request_mission(MissionState.SEARCHING),
            b.request_mission(MissionState.SEARCHING),
        )


class TestOrchestrationIntegration(unittest.TestCase):
    """The machine consumes the existing coordinator/link abstractions at the
    orchestration level only — it stays pure; the *test* does the wiring.

    Real software flow exercised: EVT_BOOT + healthy TELE -> startup_complete;
    ESP32-reported fault -> fault_detected (latch reflection); CMD_RESET
    scope=all through the link -> note_esp32_rearm.  Throughout, no motion
    command may ever reach the ESP32 from the machine alone.
    """

    def setUp(self):
        self.laptop_end, self.esp_end = create_memory_pair()
        self.esp = FakeEsp32(self.esp_end, tele_period_s=0.01, hb_period_s=0.01)
        self.link = LaptopLink(
            self.laptop_end,
            hb_interval_s=0.05,
            command_timeout_s=0.5,
            esp32_timeout_s=2.0,
        )
        self.coord = SystemCoordinator(self.link)
        self.esp.start()
        self.link.start()

    def tearDown(self):
        self.link.stop()
        self.esp.stop()

    def test_startup_fault_rearm_cycle_over_real_link(self):
        machine = ApplicationStateMachine()
        self.assertFalse(machine.usable)

        # Startup: EVT_BOOT observed, healthy TELE flowing, then READY.
        self.assertIsNotNone(self.link.wait_for_event("EVT_BOOT", timeout=2.0))
        self.assertTrue(_wait_until(lambda: self.coord.state.has_telemetry))
        # The coordinator's own machine gates on lifecycle: advance it via
        # the explicit startup entry point, then the gate is open.
        startup = self.coord.complete_startup()
        self.assertTrue(startup.ok)
        self.assertIsNone(self.coord.motion_gate())  # healthy link
        r = machine.startup_complete()
        self.assertTrue(r.ok)
        self.assertTrue(machine.usable)

        # Mission context may be set (context only — nothing is commanded).
        self.assertTrue(machine.request_mission(MissionState.NAVIGATING).ok)

        # ESP32 reports a fault (motor fault, ESP32-authoritative): reflect it.
        self.esp.faults.append(6)
        self.assertTrue(_wait_until(lambda: self.coord.state.has_faults))
        r = machine.fault_detected(self.coord.state.faults)
        self.assertTrue(r.ok)
        self.assertEqual(machine.safety, SafetyCondition.FAULT_LATCHED)
        self.assertEqual(machine.mission, MissionState.IDLE)  # task aborted
        self.assertFalse(machine.usable)
        self.assertFalse(machine.request_mission(MissionState.SEARCHING).ok)

        # Real re-arm: CMD_RESET scope=all through the link clears the ESP32
        # fault; the machine only *records* it (it cannot clear the latch).
        self.esp.set_ready()
        resp = self.link.command("CMD_RESET", scope="all")
        self.assertEqual(resp["type"], "RESP_OK")
        self.assertTrue(_wait_until(lambda: not self.coord.state.has_faults))
        r = machine.note_esp32_rearm()
        self.assertTrue(r.ok)
        self.assertEqual(machine.safety, SafetyCondition.NOMINAL)
        self.assertTrue(machine.usable)
        self.assertTrue(machine.request_mission(MissionState.SEARCHING).ok)

    def test_machine_alone_never_issues_motion_commands(self):
        machine = ApplicationStateMachine()
        machine.startup_complete()
        # Drive the machine through every mission and safety path.
        for mission in ALL_MISSIONS:
            machine.request_mission(mission)
        machine.request_stop()
        machine.emergency_stop(cause="test")
        machine.reset()
        # The ESP32 must never have executed a motion/intake/dispense command:
        # the machine has no command path of its own (CMD_MOVE stays deferred
        # downstream — OD-07/OD-13).  (Laptop HEARTBEAT traffic is the link's
        # own liveness duty and is unaffected by the machine.)
        self.assertEqual(self.esp.executed_commands, [])


if __name__ == "__main__":
    unittest.main()
