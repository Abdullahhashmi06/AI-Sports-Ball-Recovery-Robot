"""Tests for the thin system coordinator (integration layer, Person 1).

``SystemCoordinator`` wires the existing pieces into one testable flow:

    LaptopLink TELE → from_telemetry → TelemetrySnapshot → RobotSystemState
        → navigation input → plan_navigation → MovementIntent
        → command boundary (resolve_movement_command) → CommandDirective

plus a conservative *motion gate* (link not started / frozen / no
telemetry / ESP32 faults / stale telemetry) and a gated ``apply()`` that
issues commands only through ``LaptopLink`` — never around it — and always
permits the safety-layer emergency stop (R-3, DEC-021).

The coordinator also owns an :class:`ApplicationStateMachine` as its
laptop-side policy layer: ``EVT_BOOT`` is observed, ESP32-reported faults
are *reflected* into the machine's safety axis, ``complete_startup()`` is
the explicit STARTING → READY entry point, the motion gate consults the
machine, a successful emergency ``CMD_STOP`` latches laptop policy, and
``esp32_reset_all()`` (``CMD_RESET scope=all`` via the link) is the only
path that records a re-arm back to NOMINAL.

Everything is software-only: telemetry fixtures are SIMULATION / TEST ONLY,
poses are manual test samples, and the ESP32 is the in-memory ``FakeEsp32``.
"""

from __future__ import annotations

import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from integration.communication.laptop_link import LaptopLink  # noqa: E402
from integration.communication.protocol import encode_line  # noqa: E402
from integration.communication.transport import create_memory_pair  # noqa: E402
from integration.system.commands import (  # noqa: E402
    DirectiveKind,
    emergency_stop_directive,
    resolve_movement_command,
)
from integration.system.coordinator import (  # noqa: E402
    ControlTick,
    MotionGated,
    SystemCoordinator,
)
from integration.system.state_machine import (  # noqa: E402
    LifecycleState,
    SafetyCondition,
)
from navigation.localization.state import Pose, WorldPoint  # noqa: E402
from navigation.planning.interfaces import (  # noqa: E402
    NavigationStatus,
    Objective,
    MovementIntent,
    MovementKind,
)
from telemetry_fixtures import sample_telemetry  # noqa: E402

from fake_esp32 import FakeEsp32  # noqa: E402

from navigation.localization.telemetry import from_telemetry  # noqa: E402


def _wait_until(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


class _Base(unittest.TestCase):
    def setUp(self):
        # A started link over an in-memory transport with *nothing* on the
        # ESP32 side: alive (long watchdog), no telemetry.
        self.laptop_end, _self_esp_end = create_memory_pair()
        self.link = LaptopLink(
            self.laptop_end,
            heartbeat=False,
            esp32_timeout_s=60.0,
            command_timeout_s=0.5,
        )
        self.coord = SystemCoordinator(self.link)
        self.link.start()

    def tearDown(self):
        self.link.stop()

    def _advance_lifecycle(self):
        """Unit-test seam: the driver advanced startup (no EVT_BOOT/telemetry
        plumbing needed for gate-reason unit tests)."""
        result = self.coord.machine.startup_complete()
        self.assertTrue(result.ok)


class TestMotionGate(_Base):
    def test_not_started_is_gated(self):
        laptop_end, _ = create_memory_pair()
        link = LaptopLink(laptop_end, heartbeat=False)
        coord = SystemCoordinator(link)  # not started
        reason = coord.motion_gate()
        self.assertIsNotNone(reason)
        self.assertIn("not started", reason)
        link.stop()

    def test_lifecycle_starting_blocks_normal_motion(self):
        # STARTING (startup not complete) gates even with healthy telemetry:
        # a robot that has not completed startup must not be commanded.
        self.coord.state.set_telemetry(
            from_telemetry(sample_telemetry()), at=time.monotonic()
        )
        reason = self.coord.motion_gate()
        self.assertIsNotNone(reason)
        self.assertIn("startup not complete", reason)

    def test_no_telemetry_is_gated(self):
        self._advance_lifecycle()
        reason = self.coord.motion_gate()
        self.assertIsNotNone(reason)
        self.assertIn("no ESP32 telemetry", reason)

    def test_faults_gate_motion(self):
        # Faults are ESP32-authoritative (DEC-020); the laptop must not keep
        # commanding normal motion while a latched fault is reported.
        self._advance_lifecycle()
        snap = from_telemetry(sample_telemetry(faults=(1,)))
        self.coord.state.set_telemetry(snap, at=time.monotonic())
        reason = self.coord.motion_gate()
        self.assertIsNotNone(reason)
        self.assertIn("fault", reason)

    def test_machine_fault_latch_blocks_even_when_telemetry_is_clean(self):
        # Laptop-initiated emergency stop latches *policy*; it must block
        # normal motion even before/in the absence of an ESP32 mirror.
        self._advance_lifecycle()
        self.coord.state.set_telemetry(
            from_telemetry(sample_telemetry()), at=time.monotonic()  # no faults
        )
        self.coord.machine.emergency_stop(cause="laptop e-stop (unit test)")
        reason = self.coord.motion_gate()
        self.assertIsNotNone(reason)
        self.assertIn("fault-latched", reason)

    def test_stale_telemetry_gates_only_when_budget_given(self):
        self._advance_lifecycle()
        self.coord.state.set_telemetry(
            from_telemetry(sample_telemetry()), at=time.monotonic() - 5.0
        )
        # No explicit budget -> freshness is not judged (no invented cadence).
        self.assertIsNone(self.coord.motion_gate())
        # Explicit budget -> stale is gated.
        reason = self.coord.motion_gate(telemetry_max_age_s=1.0)
        self.assertIsNotNone(reason)
        self.assertIn("stale", reason)

    def test_healthy_state_is_not_gated(self):
        self._advance_lifecycle()
        self.coord.state.set_telemetry(
            from_telemetry(sample_telemetry()), at=time.monotonic()
        )
        self.assertIsNone(self.coord.motion_gate())


class TestControlTick(_Base):
    def _healthy_state(self, pose=Pose(1.0, 2.0, 0.0)):
        self._advance_lifecycle()
        self.coord.state.set_telemetry(
            from_telemetry(sample_telemetry(seq=1)), at=time.monotonic()
        )
        self.coord.state.set_pose(pose, source="test_manual", at=time.monotonic())

    def test_empty_tick_is_no_pose_no_command(self):
        # Totally empty robot: no pose (localization not initialized) is the
        # planner's first answer; nothing is commanded and the gate is closed
        # (lifecycle STARTING, no telemetry).
        tick = self.coord.control_tick()
        self.assertIsInstance(tick, ControlTick)
        self.assertEqual(tick.nav_output.status, NavigationStatus.NO_POSE)
        self.assertEqual(tick.directive.kind, DirectiveKind.NONE)
        self.assertTrue(tick.gated)

    def test_pose_but_no_objective_is_no_objective_no_command(self):
        self._healthy_state()
        tick = self.coord.control_tick()
        self.assertEqual(tick.nav_output.status, NavigationStatus.NO_OBJECTIVE)
        self.assertEqual(tick.directive.kind, DirectiveKind.NONE)
        self.assertFalse(tick.gated)  # healthy link+telemetry, nothing to do

    def test_no_pose_is_no_pose_not_a_guess(self):
        self._advance_lifecycle()
        self.coord.state.set_telemetry(
            from_telemetry(sample_telemetry()), at=time.monotonic()
        )
        tick = self.coord.control_tick(target=WorldPoint(4.0, 6.0))
        self.assertEqual(tick.nav_output.status, NavigationStatus.NO_POSE)
        self.assertEqual(tick.directive.kind, DirectiveKind.NONE)
        # Even with healthy telemetry + READY lifecycle the planner refuses to
        # fabricate a pose (OD-12/OD-13); the gate itself is open.
        self.assertFalse(tick.gated)

    def test_pose_and_target_flow_to_move_directive_but_never_send(self):
        baseline = self.link.stats["sent"]
        self._healthy_state()
        tick = self.coord.control_tick(target=WorldPoint(4.0, 6.0))
        self.assertEqual(tick.nav_output.status, NavigationStatus.PLANNED)
        self.assertEqual(tick.nav_output.objective, Objective.GO_TO_TARGET)
        self.assertEqual(tick.directive.kind, DirectiveKind.DEFERRED)
        self.assertEqual(tick.directive.message_type, "CMD_MOVE")
        # The control tick must NOT transmit anything by itself — the laptop
        # never sends a CMD_MOVE it cannot express (OD-07/OD-13) and command
        # issuance only happens via an explicit apply().
        self.assertEqual(self.link.stats["sent"], baseline)
        self.assertFalse(tick.gated)

    def test_hint_passes_to_planner(self):
        from navigation.planning.interfaces import ExitHint
        from navigation.planning.zones import ZoneConfig, Rectangle, ZoneId

        zones = (ZoneConfig(ZoneId.WEST, Rectangle(-20.0, 0.0, -10.0, 10.0)),)
        coord2 = SystemCoordinator(self.coord.link, zones=zones)
        # The configured coordinator has its own state hub: feed it directly.
        coord2.machine.startup_complete()
        coord2.state.set_telemetry(
            from_telemetry(sample_telemetry()), at=time.monotonic()
        )
        coord2.state.set_pose(Pose(1.0, 2.0, 0.0), source="test_manual",
                              at=time.monotonic())
        hint = ExitHint(ZoneId.WEST, WorldPoint(-3.0, 0.0), 0.9)
        tick = coord2.control_tick(
            hint=hint, useful_confidence_threshold=0.5  # SIMULATION/TEST ONLY
        )
        self.assertEqual(tick.nav_output.status, NavigationStatus.PLANNED)
        self.assertEqual(tick.nav_output.zone, ZoneId.WEST)
        self.assertIn("around_estimate", tick.nav_output.reason)


class TestApply(_Base):
    """apply() issuance tests use a recorded command stub so no RESP is
    required — what matters is *whether* the gate lets a directive reach
    ``LaptopLink.command`` and with which fields."""

    def setUp(self):
        super().setUp()
        self.calls = []
        self._fake_resp = {"type": "RESP_OK", "ack": 1, "test": True}

        def fake_command(message_type, *, timeout=None, **fields):
            self.calls.append((message_type, fields))
            return self._fake_resp

        self.coord.link.command = fake_command  # type: ignore[method-assign]

    def _healthy_state(self):
        self._advance_lifecycle()
        self.coord.state.set_telemetry(
            from_telemetry(sample_telemetry()), at=time.monotonic()
        )

    def _faulted_state(self):
        self._advance_lifecycle()
        self.coord.state.set_telemetry(
            from_telemetry(sample_telemetry(faults=(1,))), at=time.monotonic()
        )

    def test_apply_never_sends_deferred_or_none(self):
        for intent in (MovementIntent(MovementKind.NONE),
                       MovementIntent(MovementKind.MOVE, distance_m=2.0)):
            directive = resolve_movement_command(intent)
            self.assertIsNone(self.coord.apply(directive))  # no-op / deferred
        self.assertEqual(self.calls, [])

    def test_apply_sends_normal_stop_when_gate_is_open(self):
        self._healthy_state()
        directive = resolve_movement_command(MovementIntent(MovementKind.STOP))
        resp = self.coord.apply(directive)
        self.assertEqual(resp, self._fake_resp)
        self.assertEqual(self.calls, [("CMD_STOP", {"mode": "normal"})])

    def test_apply_refuses_normal_stop_while_gated(self):
        self._faulted_state()  # ESP32 reports a fault -> gate closed
        directive = resolve_movement_command(MovementIntent(MovementKind.STOP))
        with self.assertRaises(MotionGated) as ctx:
            self.coord.apply(directive)
        self.assertIn("fault", str(ctx.exception))
        self.assertEqual(self.calls, [])  # nothing reached the link

    def test_emergency_stop_bypasses_the_gate(self):
        self._faulted_state()  # gate closed
        directive = emergency_stop_directive()
        # Safety-layer emergency stop must not be blocked by laptop
        # bookkeeping (protocol §7, R-3): it reaches the link and returns
        # the RESP.
        resp = self.coord.apply(directive)
        self.assertEqual(resp, self._fake_resp)
        self.assertEqual(self.calls, [("CMD_STOP", {"mode": "emergency"})])
        # A successful emergency stop also latches laptop policy until a
        # verified ESP32 re-arm (even though the ESP32 mirror may lag).
        self.assertEqual(self.coord.machine.safety, SafetyCondition.FAULT_LATCHED)


class TestLinkWiring(unittest.TestCase):
    """Threaded integration: real LaptopLink + FakeEsp32 + coordinator."""

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

    def test_valid_telemetry_reaches_state_and_pipeline_runs(self):
        # (1) real TELE frames from the ESP32 land in RobotSystemState via
        #     the coordinator's on_tele wiring.
        self.assertTrue(_wait_until(lambda: self.coord.state.has_telemetry))
        self.assertGreaterEqual(self.coord.state.telemetry_seq, 1)
        self.assertTrue(self.coord.state.faults_known)

        # (2) Still no pose: honest NO_POSE, no command (nothing sent by the
        #     control tick itself beyond the link's own heartbeat traffic).
        sent_before = self.link.stats["sent"]
        tick = self.coord.control_tick(target=WorldPoint(4.0, 6.0))
        self.assertEqual(tick.nav_output.status, NavigationStatus.NO_POSE)
        self.assertEqual(tick.directive.kind, DirectiveKind.NONE)
        self.assertEqual(self.link.stats["sent"], sent_before)

        # (3) A (test-only) manual pose makes the flow reach the command
        #     boundary: PLANNED intent, MOVE deferred — still nothing sent.
        self.coord.state.set_pose(Pose(1.0, 2.0, 0.0), source="test_manual",
                                  at=time.monotonic())
        tick = self.coord.control_tick(target=WorldPoint(4.0, 6.0))
        self.assertEqual(tick.nav_output.status, NavigationStatus.PLANNED)
        self.assertEqual(tick.directive.kind, DirectiveKind.DEFERRED)
        self.assertEqual(tick.directive.message_type, "CMD_MOVE")
        self.assertEqual(self.link.stats["sent"], sent_before)

    def test_esp32_fault_flows_into_motion_gate(self):
        self.assertTrue(_wait_until(lambda: self.coord.state.has_telemetry))
        # The fake ESP32 starts reporting a fault (its telemetry builder
        # reads self.faults every frame).
        self.esp.faults.append(6)  # motor fault code — ESP32-authoritative
        self.assertTrue(_wait_until(lambda: self.coord.state.has_faults))
        # The machine's safety axis reflects the ESP32-reported fault.
        self.assertEqual(self.coord.machine.safety, SafetyCondition.FAULT_LATCHED)
        reason = self.coord.motion_gate()
        self.assertIsNotNone(reason)
        self.assertIn("fault", reason)
        # A normal STOP directive is refused while gated...
        stop = resolve_movement_command(MovementIntent(MovementKind.STOP))
        with self.assertRaises(MotionGated):
            self.coord.apply(stop)
        # ...but the safety emergency stop goes through and reaches the ESP32.
        self.esp.set_ready()
        resp = self.coord.apply(emergency_stop_directive())
        self.assertIsNotNone(resp)
        self.assertEqual(resp["type"], "RESP_OK")
        self.assertTrue(self.esp.latched)

    def test_malformed_value_telemetry_does_not_crash_reader(self):
        # Structurally-valid-ish TELE that the strict bridge rejects (non-int
        # fault entry) must be counted, not crash the reader thread.
        bad = sample_telemetry(faults=("x",))
        self.esp.send_raw(encode_line(bad))
        self.assertTrue(_wait_until(
            lambda: self.coord.stats["invalid_telemetry"] >= 1, timeout=2.0))
        self.assertFalse(self.link.frozen)
        # A healthy frame still flows afterwards.
        self.assertTrue(_wait_until(
            lambda: self.coord.state.telemetry_seq >= 1, timeout=2.0))


class TestPolicyInvariants(unittest.TestCase):
    """Safety/policy invariants over the real composition:

        LaptopLink -> FakeEsp32 TELE/EVT_BOOT -> RobotSystemState
            -> ApplicationStateMachine -> planner -> command boundary
            -> coordinator motion gate / apply -> LaptopLink

    Coverage map (milestone invariant list):
    (1) STARTING gate             test_starting_cannot_generate_movement
    (2) no telemetry gate         TestMotionGate.test_no_telemetry_is_gated
    (3) stale telemetry gate      TestMotionGate.test_stale_telemetry_gates_only...
    (4) ESP32 fault reflection    test_fault_requires_verified_rearm...
    (5) no auto-unlatch           test_fault_requires_verified_rearm...
    (6/7) emergency semantics     test_emergency_stop_latches_and_never_moves
    (8) normal STOP no latch      test_normal_stop_does_not_latch
    (9) reset no local clear      test_fault_requires_verified_rearm...
    (10) verified re-arm only     test_fault_requires_verified_rearm...
    (11/12) DEFERRED MOVE + OD    test_move_stays_deferred_and_od_blockers
    (13/14/15) malformed/unknown  test_malformed_and_unknown_messages_ignored
    (16) no LaptopLink bypass     all apply() paths + executed-command checks
    (17) no direct hardware       test_no_transport_bypass_or_hardware_tokens
    (18) emergency bypass         test_emergency_stop_latches_and_never_moves
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
        self.esp.set_ready()

    def tearDown(self):
        self.link.stop()
        self.esp.stop()

    # -- helpers -------------------------------------------------------------

    def _boot_and_ready(self):
        """Observe EVT_BOOT + healthy telemetry, then advance the lifecycle."""
        self.assertTrue(_wait_until(lambda: self.coord.boot_observed))
        self.assertTrue(_wait_until(lambda: self.coord.state.has_telemetry))
        result = self.coord.complete_startup()
        self.assertTrue(result.ok)
        self.assertEqual(self.coord.machine.lifecycle, LifecycleState.READY)

    def _pose(self):
        self.coord.state.set_pose(
            Pose(1.0, 2.0, 0.0), source="test_manual", at=time.monotonic()
        )

    def _stop_directive(self):
        return resolve_movement_command(MovementIntent(MovementKind.STOP))

    # -- invariants ----------------------------------------------------------

    def test_starting_cannot_generate_movement(self):
        # (1) Even with healthy telemetry + a pose, lifecycle STARTING gates
        # every normal command; nothing may reach the ESP32.
        self.assertTrue(_wait_until(lambda: self.coord.state.has_telemetry))
        self._pose()
        tick = self.coord.control_tick(target=WorldPoint(4.0, 6.0))
        self.assertTrue(tick.gated)
        self.assertIn("startup not complete", tick.gate_reason)
        with self.assertRaises(MotionGated):
            self.coord.apply(self._stop_directive())
        self.assertEqual(self.esp.executed_commands, [])

    def test_fault_requires_verified_rearm_and_never_auto_clears(self):
        # (4) ESP32-reported fault -> machine reflection latches and gates.
        self._boot_and_ready()
        self._pose()
        self.assertIsNone(self.coord.motion_gate())
        self.esp.faults.append(6)  # motor fault — ESP32-authoritative
        self.assertTrue(_wait_until(lambda: self.coord.state.has_faults))
        self.assertEqual(self.coord.machine.safety, SafetyCondition.FAULT_LATCHED)
        reason = self.coord.motion_gate()
        self.assertIsNotNone(reason)
        self.assertIn("fault", reason)
        with self.assertRaises(MotionGated):
            self.coord.apply(self._stop_directive())

        # (5) Telemetry turns clean again: NO automatic un-latch.
        self.esp.faults = []
        self.assertTrue(_wait_until(lambda: not self.coord.state.has_faults))
        self.assertEqual(self.coord.machine.safety, SafetyCondition.FAULT_LATCHED)
        self.assertIsNotNone(self.coord.motion_gate())

        # (9) A local machine reset does NOT clear the ESP32 latch reflection
        # (and startup cannot complete while the latch is still reflected).
        self.coord.machine.reset()
        self.assertEqual(self.coord.machine.safety, SafetyCondition.FAULT_LATCHED)
        self.assertFalse(self.coord.machine.startup_complete().ok)
        reason = self.coord.motion_gate()
        self.assertIsNotNone(reason)
        self.assertIn("fault-latched", reason)

        # (10) Only a verified CMD_RESET scope=all exchange records re-arm;
        # the reset lifecycle then re-advances normally.
        resp = self.coord.esp32_reset_all()
        self.assertEqual(resp["type"], "RESP_OK")
        self.assertEqual(self.coord.machine.safety, SafetyCondition.NOMINAL)
        reason = self.coord.motion_gate()
        self.assertIsNotNone(reason)
        self.assertIn("startup not complete", reason)  # lifecycle was reset
        self.assertTrue(self.coord.machine.startup_complete().ok)
        self.assertIsNone(self.coord.motion_gate())

    def test_emergency_stop_latches_and_never_moves(self):
        # (6) Emergency stop always goes through (bypasses the gate)...
        self._boot_and_ready()
        resp = self.coord.apply(emergency_stop_directive())
        self.assertEqual(resp["type"], "RESP_OK")
        self.assertTrue(self.esp.latched)  # ESP32 owns the real latch
        self.assertEqual(self.coord.machine.safety, SafetyCondition.FAULT_LATCHED)
        # (7) ...and it is a STOP, never a movement command.
        self.assertEqual(self.esp.executed_commands, [])
        # Normal motion is now blocked until the verified re-arm.
        with self.assertRaises(MotionGated):
            self.coord.apply(self._stop_directive())

    def test_normal_stop_does_not_latch(self):
        # (8) A normal STOP is not a fault: no latch anywhere.
        self._boot_and_ready()
        resp = self.coord.apply(self._stop_directive())
        self.assertEqual(resp["type"], "RESP_OK")
        self.assertEqual(self.coord.machine.safety, SafetyCondition.NOMINAL)
        self.assertFalse(self.esp.latched)
        self.assertIsNone(self.coord.motion_gate())

    def test_move_stays_deferred_and_od_blockers_explicit(self):
        # (11/12) A MOVE intent never becomes a wire command; OD-07/OD-13 are
        # named as the blockers and no value is invented.
        self._boot_and_ready()
        self._pose()
        tick = self.coord.control_tick(target=WorldPoint(4.0, 6.0))
        self.assertEqual(tick.nav_output.status, NavigationStatus.PLANNED)
        self.assertEqual(tick.directive.kind, DirectiveKind.DEFERRED)
        self.assertEqual(tick.directive.message_type, "CMD_MOVE")
        self.assertIn("OD-07", tick.directive.reason)
        self.assertIn("OD-13", tick.directive.reason)
        self.assertIsNone(self.coord.apply(tick.directive))  # nothing sent
        self.assertEqual(self.esp.executed_commands, [])

    def test_malformed_and_unknown_messages_create_no_behavior(self):
        # (13) value-malformed TELE is counted, never crashes the reader.
        self._boot_and_ready()
        before = self.coord.state.telemetry_seq
        bad = sample_telemetry(faults=("x",))
        self.esp.send_raw(encode_line(bad))
        self.assertTrue(_wait_until(
            lambda: self.coord.stats["invalid_telemetry"] >= 1, timeout=2.0))
        # (14) unknown extra TELE fields are tolerated (additive policy), not
        # turned into behavior.
        extra = sample_telemetry()
        extra["future_field"] = {"some": "thing"}
        self.esp.send_raw(encode_line(extra))
        self.assertTrue(_wait_until(
            lambda: self.coord.state.telemetry_seq > before, timeout=2.0))
        # (15) unknown inbound message types are ignored by the link layer.
        self.esp.send_raw('{"v":1,"type":"MYSTERY_EVENT","seq":2}')
        self.assertTrue(_wait_until(
            lambda: self.link.stats["rx"] >= 1, timeout=2.0))
        self.assertFalse(self.link.frozen)
        # The machine and gate remain untouched by all of the above.
        self.assertEqual(self.coord.machine.safety, SafetyCondition.NOMINAL)
        self.assertIsNone(self.coord.motion_gate())
        self.assertEqual(self.esp.executed_commands, [])

    def test_no_transport_bypass_or_hardware_tokens(self):
        # (16/17) The integration/system modules contain no transport wiring,
        # no serial import, and no hardware/PWM/GPIO vocabulary — everything
        # goes through LaptopLink and the documented interfaces.
        import integration.system.coordinator as coord_mod
        import integration.system.state_machine as sm_mod
        import integration.system.commands as cmd_mod
        import integration.system.state as st_mod

        tokens = (
            "GPIO", "gpio", "PWM", "pwm", "servo", "import serial",
            "SerialTransport", "create_memory_pair", "send_text", "pyserial",
        )
        for module in (coord_mod, sm_mod, cmd_mod, st_mod):
            with open(module.__file__, encoding="utf-8") as fh:
                source = fh.read()
            for token in tokens:
                self.assertNotIn(token, source, f"{token!r} found in {module.__file__}")


if __name__ == "__main__":
    unittest.main()
