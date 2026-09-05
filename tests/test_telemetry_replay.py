"""Deterministic replay / scenario tests for the software-only milestone.

Every scenario runs synchronously through the **same intake path real
``LaptopLink`` data uses** (``SystemCoordinator.ingest_message`` → structural
guard → ``from_telemetry`` → ``RobotSystemState`` → machine reflection) —
no threads, no sleeps, no wall-clock timing, no serial, no hardware.  All
frames are SIMULATION / TEST ONLY synthetic data (``tests/telemetry_replay.py``).

Scenario coverage map (milestone lists):

    TASK-2:  1 startup        sc_startup · 2 healthy      sc_healthy_planning
             3 no telemetry   (unit gate) · 4 stale       sc_stale_budget
             5 fault          sc_fault_latch · 6 e-stop   sc_emergency_policy
             7 reset-all      sc_rearm_required · 8 clean sc_clean_no_autoclear
             9 no pose        sc_no_pose · 10 exit hint   sc_exit_hint_seam
             11 mission       sc_mission_context · 12 normal STOP sc_normal_stop
             13 emergency     sc_emergency_policy · 14 malformed sc_malformed
    TASK-6:  fault/clean/rearm/stale/emergency/frozen(threaded coverage in
             test_system_coordinator.TestPolicyInvariants + TestLaptopLiveness) /
             malformed/unknown-field/unknown-type — see below.

The full-narrative scenario (``test_full_recovery_narrative``) strings the
startup → plan → fault → no-auto-clear → re-arm cycle together.
"""

from __future__ import annotations

import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from integration.communication.laptop_link import LaptopLink  # noqa: E402
from integration.communication.transport import create_memory_pair  # noqa: E402
from integration.system.commands import (  # noqa: E402
    DirectiveKind,
    emergency_stop_directive,
    resolve_movement_command,
)
from integration.system.coordinator import (  # noqa: E402
    CoordinatorStatus,
    MotionGated,
    SystemCoordinator,
)
from integration.system.state_machine import (  # noqa: E402
    LifecycleState,
    MissionState,
    SafetyCondition,
)
from navigation.localization.state import Pose, WorldPoint  # noqa: E402
from navigation.planning.interfaces import (  # noqa: E402
    ExitHint,
    NavigationError,
    NavigationStatus,
    Objective,
    MovementIntent,
    MovementKind,
    exit_hint_from_payload,
)
from navigation.planning.zones import Rectangle, ZoneConfig, ZoneId  # noqa: E402

import telemetry_replay  # noqa: E402  (SIMULATION / TEST ONLY replay helpers)
from telemetry_replay import boot_frame, startup_to_ready, tele_frame  # noqa: E402


class _Base(unittest.TestCase):
    """Started link over an in-memory transport with no ESP32 peer; all
    frame ingestion is synchronous via the coordinator's replay intake."""

    def setUp(self):
        self.laptop_end, _esp_end = create_memory_pair()
        self.link = LaptopLink(
            self.laptop_end,
            heartbeat=False,        # no background traffic in unit tests
            esp32_timeout_s=60.0,   # watchdog never trips within a test
            command_timeout_s=0.5,
        )
        self.zones = (
            ZoneConfig(ZoneId.WEST, Rectangle(-20.0, 0.0, -10.0, 10.0)),
            ZoneConfig(ZoneId.EAST, Rectangle(0.0, 20.0, -10.0, 10.0)),
        )
        self.coord = SystemCoordinator(self.link, zones=self.zones)
        self.link.start()

    def tearDown(self):
        self.link.stop()

    # -- helpers ------------------------------------------------------------

    def _pose(self):
        self.coord.state.set_pose(
            Pose(1.0, 2.0, 0.0), source="test_manual", at=time.monotonic()
        )

    def _record_commands(self):
        """Replace link.command with a recorder (deterministic; no RESP
        needed) and return the recorded (message_type, fields) list."""
        calls = []

        def fake_command(message_type, *, timeout=None, **fields):
            calls.append((message_type, fields))
            return {"type": "RESP_OK", "ack": 1, "test": True}

        self.coord.link.command = fake_command  # type: ignore[method-assign]
        return calls


class TestReplayPath(_Base):
    """Replay enters at the same interfaces as real data and preserves raw
    telemetry values (nothing is converted — OD-12/OD-13 stay open)."""

    def test_tele_frame_reaches_state_with_raw_values(self):
        telemetry_replay.run_frames(
            self.coord,
            [boot_frame(1), tele_frame(2, enc_dl=7, enc_dr=-3, us=(350, 340, 9999, -1),
                         ax=0.02, ay=0.01, az=0.99, faults=(6,))],
        )
        snap = self.coord.state.snapshot
        self.assertIsNotNone(snap)
        self.assertEqual((snap.enc_dl, snap.enc_dr), (7, -3))      # raw counts
        self.assertEqual(snap.us, (350, 340, 9999, -1))            # raw mm
        self.assertEqual((snap.imu.ax, snap.imu.ay, snap.imu.az), (0.02, 0.01, 0.99))
        self.assertEqual(snap.faults, (6,))                        # verbatim
        self.assertFalse(hasattr(snap, "meters"))                  # no conversion

    def test_replay_cannot_bypass_robot_system_state(self):
        # Frames only take effect through from_telemetry -> RobotSystemState;
        # the coordinator's own intake is the sole door (no direct machine or
        # state mutation in the replay helper).
        telemetry_replay.run_frames(self.coord, [tele_frame(3, ts=999)])
        self.assertTrue(self.coord.state.has_telemetry)
        self.assertEqual(self.coord.state.telemetry_seq, 1)
        self.assertEqual(self.coord.state.snapshot.seq, 3)
        # And no frame was ever transmitted anywhere (link untouched).
        self.assertEqual(self.link.stats["sent"], 0)

    def test_ingest_requires_decoded_dict(self):
        with self.assertRaises(TypeError):
            self.coord.ingest_message(["not", "a", "dict"])  # type: ignore[arg-type]

    def test_structural_malformed_tele_is_ignored_not_crashing(self):
        bad = {"v": 1, "type": "TELE", "seq": 9, "enc": {}}  # missing sections
        before = self.coord.stats["ignored"]
        self.coord.ingest_message(bad)
        self.assertEqual(self.coord.stats["ignored"], before + 1)
        self.assertFalse(self.coord.state.has_telemetry)

    def test_value_malformed_tele_is_counted_not_crashing(self):
        # Passes the link structural guard but fails the stricter bridge.
        telemetry_replay.run_frames(
            self.coord, [tele_frame(10, faults=("x",))]  # type: ignore[arg-type]
        )
        self.assertGreaterEqual(self.coord.stats["invalid_telemetry"], 1)
        self.assertFalse(self.coord.state.has_telemetry)

    def test_unknown_telemetry_fields_are_tolerated(self):
        frame = telemetry_replay.with_extra_field(tele_frame(11), "future_field", {"a": 1})
        telemetry_replay.run_frames(self.coord, [frame])
        self.assertTrue(self.coord.state.has_telemetry)
        self.assertEqual(self.coord.state.snapshot.seq, 11)
        # Unknown field created no behaviour (no faults, no latch).
        self.assertEqual(self.coord.machine.safety, SafetyCondition.NOMINAL)
        self.assertFalse(self.coord.state.has_faults)

    def test_unknown_message_types_are_ignored(self):
        before = self.coord.stats["ignored"]
        self.coord.ingest_message({"v": 1, "type": "MYSTERY_EVENT", "seq": 2})
        self.coord.ingest_message({"v": 1, "type": "HEARTBEAT", "seq": 3})
        self.assertEqual(self.coord.stats["ignored"], before + 2)
        self.assertFalse(self.coord.state.has_telemetry)
        self.assertFalse(self.coord.boot_observed)


class TestStatus(_Base):
    def test_status_snapshot_reflects_state(self):
        status = self.coord.status()
        self.assertIsInstance(status, CoordinatorStatus)
        self.assertEqual(status.lifecycle, LifecycleState.STARTING)
        self.assertEqual(status.safety, SafetyCondition.NOMINAL)
        self.assertEqual(status.mission, MissionState.IDLE)
        self.assertFalse(status.usable)
        self.assertFalse(status.boot_observed)
        self.assertFalse(status.has_telemetry)
        self.assertEqual(status.telemetry_seq, 0)
        self.assertEqual(status.faults, ())
        self.assertIsNone(status.telemetry_fresh)
        self.assertIsNotNone(status.motion_gate_reason)

    def test_status_tracks_lifecycle_and_faults(self):
        startup_to_ready(self.coord)
        s = self.coord.status()
        self.assertTrue(s.boot_observed)
        self.assertTrue(s.has_telemetry)
        self.assertTrue(s.usable)
        self.assertIsNone(s.motion_gate_reason)

        telemetry_replay.run_frames(self.coord, [tele_frame(20, faults=(6,))])
        s = self.coord.status()
        self.assertEqual(s.safety, SafetyCondition.FAULT_LATCHED)
        self.assertEqual(s.faults, (6,))
        self.assertEqual(s.cause, "fault codes: 6")
        self.assertIn("fault", s.motion_gate_reason)


class TestScenarios(_Base):
    """Deterministic scenario matrix (TASK-2 list) + safety invariants."""

    def test_sc_startup_cannot_move(self):
        # (1) EVT_BOOT + healthy telemetry, but lifecycle not advanced:
        # STARTING blocks every normal command.
        telemetry_replay.run_frames(self.coord, [boot_frame(1), tele_frame(2)])
        s = self.coord.status()
        self.assertEqual(s.lifecycle, LifecycleState.STARTING)
        self.assertIn("startup not complete", s.motion_gate_reason)
        with self.assertRaises(MotionGated):
            self.coord.apply(resolve_movement_command(MovementIntent(MovementKind.STOP)))

    def test_sc_healthy_planning_intent_but_no_physical_motion(self):
        # (2) healthy telemetry + pose + target -> planning intent; (13
        # in TASK-6) intent never becomes physical motion (DEFERRED).
        startup_to_ready(self.coord)
        self._pose()
        sent_before = self.link.stats["sent"]
        tick = self.coord.control_tick(target=WorldPoint(4.0, 6.0))
        self.assertEqual(tick.nav_output.status, NavigationStatus.PLANNED)
        self.assertEqual(tick.directive.kind, DirectiveKind.DEFERRED)
        self.assertEqual(tick.directive.message_type, "CMD_MOVE")
        self.assertIn("OD-07", tick.directive.reason)
        self.assertIn("OD-13", tick.directive.reason)
        self.assertIsNone(self.coord.apply(tick.directive))  # nothing sent
        self.assertEqual(self.link.stats["sent"], sent_before)

    def test_sc_no_pose_is_honest_no_pose(self):
        # (9) valid telemetry with no pose: the planner says NO_POSE.
        startup_to_ready(self.coord)
        tick = self.coord.control_tick(target=WorldPoint(4.0, 6.0))
        self.assertEqual(tick.nav_output.status, NavigationStatus.NO_POSE)
        self.assertEqual(tick.directive.kind, DirectiveKind.NONE)

    def test_sc_stale_telemetry_blocks_motion(self):
        # (4) a caller-supplied freshness budget gates stale telemetry.
        startup_to_ready(self.coord)
        self._pose()
        # No budget -> freshness not judged; huge budget -> fresh.
        self.assertIsNone(self.coord.status().motion_gate_reason)
        self.assertTrue(self.coord.status(telemetry_max_age_s=1e9).telemetry_fresh)
        # A negative budget means "any age is stale" — deterministic on every
        # clock granularity (no wall-clock timing assumptions in the test).
        status = self.coord.status(telemetry_max_age_s=-1.0)
        self.assertFalse(status.telemetry_fresh)
        self.assertIn("stale", status.motion_gate_reason)
        # The same budget gates a control tick (issuance happens only when the
        # caller's control loop runs with the budget supplied).
        tick = self.coord.control_tick(
            target=WorldPoint(4.0, 6.0), telemetry_max_age_s=-1.0
        )
        self.assertTrue(tick.gated)
        self.assertIn("stale", tick.gate_reason)

    def test_sc_fault_blocks_then_clean_does_not_autoclear(self):
        # (5) ESP32 fault reflection blocks; (8/12) clean telemetry never
        # silently clears the latch.
        startup_to_ready(self.coord)
        self._pose()
        telemetry_replay.run_frames(self.coord, [tele_frame(20, faults=(6,))])
        self.assertEqual(self.coord.machine.safety, SafetyCondition.FAULT_LATCHED)
        with self.assertRaises(MotionGated):
            self.coord.apply(resolve_movement_command(MovementIntent(MovementKind.STOP)))
        # Telemetry turns clean -> state has no faults, latch persists.
        telemetry_replay.run_frames(self.coord, [tele_frame(21)])
        self.assertFalse(self.coord.state.has_faults)
        self.assertEqual(self.coord.machine.safety, SafetyCondition.FAULT_LATCHED)
        self.assertIn("fault", self.coord.status().motion_gate_reason)

    def test_sc_rearm_required_and_verified_only(self):
        # (7) a local machine reset never clears the ESP32-latch reflection;
        # only a recorded verified re-arm (caller performed + verified
        # CMD_RESET scope=all) clears it.  The command side of the re-arm is
        # exercised over the real link in TestPolicyInvariants.
        startup_to_ready(self.coord)
        telemetry_replay.run_frames(self.coord, [tele_frame(20, faults=(1,))])
        self.assertEqual(self.coord.machine.safety, SafetyCondition.FAULT_LATCHED)
        # Local reset keeps the latch (DEC-007: only the ESP32 re-arm clears).
        self.coord.machine.reset()
        self.assertEqual(self.coord.machine.safety, SafetyCondition.FAULT_LATCHED)
        # Clean telemetry does not clear it either.
        telemetry_replay.run_frames(self.coord, [tele_frame(21)])
        self.assertEqual(self.coord.machine.safety, SafetyCondition.FAULT_LATCHED)
        # Recording the verified re-arm is the only clearing mechanism.
        r = self.coord.machine.note_esp32_rearm()
        self.assertTrue(r.ok)
        self.assertEqual(self.coord.machine.safety, SafetyCondition.NOMINAL)

    def test_sc_emergency_policy_and_bypass(self):
        # (13 TASK-2 / TASK-6): emergency STOP bypasses the normal gate but
        # still travels through LaptopLink.command as CMD_STOP (never MOVE),
        # and latches laptop policy.
        calls = self._record_commands()
        startup_to_ready(self.coord)
        # Close the gate with an ESP32 fault.
        telemetry_replay.run_frames(self.coord, [tele_frame(20, faults=(6,))])
        directive = emergency_stop_directive()
        resp = self.coord.apply(directive)
        self.assertIsNotNone(resp)
        self.assertEqual(calls, [("CMD_STOP", {"mode": "emergency"})])
        self.assertEqual(self.coord.machine.safety, SafetyCondition.FAULT_LATCHED)
        # Normal motion remains blocked; nothing was ever a movement command.
        with self.assertRaises(MotionGated):
            self.coord.apply(resolve_movement_command(MovementIntent(MovementKind.STOP)))
        self.assertEqual({mtype for mtype, _fields in calls}, {"CMD_STOP"})

    def test_sc_normal_stop_does_not_latch(self):
        # (12) normal STOP: CMD_STOP mode=normal is issued (no latch), and the
        # machine-level stop request clears the mission context.
        calls = self._record_commands()
        startup_to_ready(self.coord)
        self.coord.machine.request_mission(MissionState.NAVIGATING)
        directive = resolve_movement_command(MovementIntent(MovementKind.STOP))
        resp = self.coord.apply(directive)
        self.assertIsNotNone(resp)
        self.assertEqual(calls, [("CMD_STOP", {"mode": "normal"})])
        self.assertEqual(self.coord.machine.safety, SafetyCondition.NOMINAL)
        self.assertFalse(self.coord.state.has_faults)
        # Mission context is declarative: the wire-level stop does not touch
        # it; the machine-level stop request clears it to IDLE.
        self.assertEqual(self.coord.machine.mission, MissionState.NAVIGATING)
        self.assertTrue(self.coord.machine.request_stop().ok)
        self.assertEqual(self.coord.machine.mission, MissionState.IDLE)

    def test_sc_mission_context_is_not_an_objective(self):
        # (11) mission context is declarative and never auto-produces an
        # objective/target (OD-03 semantics are not invented).
        startup_to_ready(self.coord)
        self._pose()
        self.coord.machine.request_mission(MissionState.NAVIGATING)
        self.assertEqual(self.coord.status().mission, MissionState.NAVIGATING)
        # A mission label alone yields no objective (planner NO_OBJECTIVE).
        tick = self.coord.control_tick()  # no target/zone/hint supplied
        self.assertEqual(tick.nav_output.status, NavigationStatus.NO_OBJECTIVE)
        self.assertEqual(tick.directive.kind, DirectiveKind.NONE)
        # The caller must still supply an explicit target/zone/hint.
        tick = self.coord.control_tick(target=WorldPoint(4.0, 6.0))
        self.assertEqual(tick.nav_output.status, NavigationStatus.PLANNED)
        self.assertEqual(tick.nav_output.objective, Objective.GO_TO_TARGET)

    def test_sc_exit_hint_seam(self):
        # (10) perception-style payload (I-1 form) -> ExitHint -> planner
        # selects the zone without perception knowing navigation internals.
        startup_to_ready(self.coord)
        self._pose()
        payload = {"exit_zone": "WEST", "estimated_position": {"x": -3.0, "y": 0.0},
                   "confidence": 0.9}
        hint = exit_hint_from_payload(payload)
        self.assertIsInstance(hint, ExitHint)
        tick = self.coord.control_tick(hint=hint, useful_confidence_threshold=0.5)
        self.assertEqual(tick.nav_output.status, NavigationStatus.PLANNED)
        self.assertEqual(tick.nav_output.zone, ZoneId.WEST)
        self.assertEqual(tick.nav_output.target, WorldPoint(-3.0, 0.0))

    def test_sc_malformed_does_not_crash(self):
        # (14) malformed replay frames are counted/ignored; system survives.
        self.coord.ingest_message({"v": 1, "type": "TELE", "seq": 1, "enc": {}})
        telemetry_replay.run_frames(
            self.coord, [tele_frame(2, faults=("x",))]  # type: ignore[arg-type]
        )
        self.assertEqual(self.coord.machine.lifecycle, LifecycleState.STARTING)
        self.assertFalse(self.link.frozen)

    def test_full_recovery_narrative(self):
        # End-to-end software narrative (startup -> plan -> fault -> no
        # auto-clear -> verified re-arm), synchronous and deterministic.
        startup_to_ready(self.coord)
        self._pose()
        tick = self.coord.control_tick(target=WorldPoint(4.0, 6.0))
        self.assertEqual(tick.directive.kind, DirectiveKind.DEFERRED)

        telemetry_replay.run_frames(self.coord, [tele_frame(50, faults=(2,))])
        self.assertEqual(self.coord.machine.safety, SafetyCondition.FAULT_LATCHED)
        self.assertIn("fault", self.coord.status().motion_gate_reason)

        telemetry_replay.run_frames(self.coord, [tele_frame(51)])  # clean
        self.assertEqual(self.coord.machine.safety, SafetyCondition.FAULT_LATCHED)

        self.coord.machine.note_esp32_rearm()  # caller verified CMD_RESET all
        self.assertEqual(self.coord.machine.safety, SafetyCondition.NOMINAL)
        self.assertIsNone(self.coord.status().motion_gate_reason)


class TestPerceptionSeam(_Base):
    """The perception seam (I-1 payload -> ExitHint) has no command surface
    and validates its inputs deterministically."""

    def test_payload_accepts_enum_and_mapping_forms(self):
        hint = exit_hint_from_payload(
            {"exit_zone": ZoneId.EAST,
             "estimated_position": WorldPoint(8.0, 1.0), "confidence": 0.7}
        )
        self.assertEqual(hint.exit_zone, ZoneId.EAST)
        self.assertEqual(hint.estimated_position, WorldPoint(8.0, 1.0))
        self.assertEqual(hint.confidence, 0.7)

    def test_payload_rejects_missing_or_invalid_fields(self):
        for payload in (
            {"estimated_position": {"x": 0.0, "y": 0.0}, "confidence": 0.5},
            {"exit_zone": "NORTHEAST", "estimated_position": {"x": 0.0, "y": 0.0},
             "confidence": 0.5},
            {"exit_zone": "WEST", "estimated_position": {"x": 0.0}, "confidence": 0.5},
            {"exit_zone": "WEST", "estimated_position": {"x": 0.0, "y": 0.0}},
            {"exit_zone": "WEST", "estimated_position": {"x": 0.0, "y": 0.0},
             "confidence": float("nan")},
        ):
            with self.assertRaises(NavigationError):
                exit_hint_from_payload(payload)

    def test_payload_unknown_extra_keys_are_tolerated(self):
        payload = {"exit_zone": "SOUTH", "estimated_position": {"x": 0.0, "y": -8.0},
                   "confidence": 0.6, "future": "field"}
        hint = exit_hint_from_payload(payload)
        self.assertEqual(hint.exit_zone, ZoneId.SOUTH)

    def test_perception_hint_has_no_command_surface(self):
        # Perception can only produce a passive search hint — no commands,
        # no link access, no motion capability.
        hint = exit_hint_from_payload(
            {"exit_zone": "WEST", "estimated_position": {"x": -1.0, "y": 0.0},
             "confidence": 0.8}
        )
        for attr in ("apply", "send", "command", "to_pwm", "lin", "ang"):
            self.assertFalse(hasattr(hint, attr), attr)
        # Building a hint must not touch the coordinator or the link.
        sent_before = self.link.stats["sent"]
        exit_hint_from_payload(
            {"exit_zone": "WEST", "estimated_position": {"x": -1.0, "y": 0.0},
             "confidence": 0.8}
        )
        self.assertEqual(self.link.stats["sent"], sent_before)
        self.assertFalse(self.coord.state.has_telemetry)


if __name__ == "__main__":
    unittest.main()
