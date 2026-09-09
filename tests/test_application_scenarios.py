"""Deterministic application scenario tests verifying all 16 required scenario cases.

Uses real ApplicationOrchestrator -> real SystemCoordinator -> real ApplicationStateMachine ->
real planner -> real command boundary -> LaptopLink -> FakeEsp32.

Confirms zero physical hardware interaction and asserts the No-Motion Invariant
(0 CMD_MOVE wire commands transmitted).
"""

import time
import unittest

from fake_esp32 import FakeEsp32
from integration.communication.laptop_link import LaptopLink
from integration.communication.transport import create_memory_pair
from integration.system.application import ApplicationOrchestrator, TaskObjective, TaskType
from integration.system.commands import CommandDirective, DirectiveKind
from integration.system.coordinator import MotionGated
from integration.system.state_machine import LifecycleState, MissionState, SafetyCondition
from navigation.localization.state import Pose, WorldPoint
from navigation.planning.interfaces import MovementKind, NavigationStatus
from navigation.planning.zones import ZoneId
from telemetry_replay import boot_frame, tele_frame


class TestApplicationScenarios(unittest.TestCase):
    def setUp(self):
        self.laptop_end, self.esp_end = create_memory_pair()
        self.fake = FakeEsp32(self.esp_end, tele_period_s=0.02, hb_period_s=0.02)
        self.link = LaptopLink(self.laptop_end, command_timeout_s=0.5)
        self.app = ApplicationOrchestrator(self.link)
        self.link.start()

    def tearDown(self) -> None:
        self.link.stop()
        self.fake.stop()

    # -------------------------------------------------------------------------
    # Scenario 1: Fresh startup
    # -------------------------------------------------------------------------
    def test_scenario_01_fresh_startup(self):
        self.assertEqual(self.app.machine.lifecycle, LifecycleState.STARTING)

        # Ingest BOOT + healthy TELE
        self.app.handle_message(boot_frame(1))
        self.app.handle_message(tele_frame(2))

        # Startup complete explicitly
        res = self.app.complete_startup()
        self.assertTrue(res.ok)
        self.assertEqual(self.app.machine.lifecycle, LifecycleState.READY)

    # -------------------------------------------------------------------------
    # Scenario 2: Startup safety
    # -------------------------------------------------------------------------
    def test_scenario_02_startup_safety(self):
        # Before complete_startup(), robot is in STARTING
        self.assertEqual(self.app.machine.lifecycle, LifecycleState.STARTING)
        self.app.handle_message(boot_frame(1))
        self.app.handle_message(tele_frame(2))
        self.app.set_pose(Pose(1.0, 1.0, 0.0))

        tick = self.app.tick()
        self.assertTrue(tick.gated)
        self.assertIn("STARTING", tick.gate_reason or "")

        # Attempting to apply a SEND command while in STARTING must raise MotionGated
        directive = CommandDirective(DirectiveKind.SEND, "CMD_STOP", {"mode": "normal"})
        with self.assertRaises(MotionGated):
            self.app.coordinator.apply(directive)

    # -------------------------------------------------------------------------
    # Scenario 3: Healthy telemetry & CMD_MOVE deferred
    # -------------------------------------------------------------------------
    def test_scenario_03_healthy_telemetry(self):
        self.app.handle_message(boot_frame(1))
        self.app.handle_message(tele_frame(2))
        self.app.complete_startup()
        self.app.set_pose(Pose(1.0, 1.0, 0.0))

        # Set mission and objective
        self.app.set_mission(MissionState.NAVIGATING)
        self.app.set_task(TaskObjective(TaskType.GO_TO_TARGET, target=WorldPoint(5.0, 5.0)))

        tick = self.app.tick()
        self.assertFalse(tick.gated)
        self.assertEqual(tick.nav_output.status, NavigationStatus.PLANNED)
        self.assertEqual(tick.nav_output.movement.kind, MovementKind.MOVE)
        self.assertEqual(tick.directive.kind, DirectiveKind.DEFERRED)

        # Apply tick returns None (nothing sent on wire)
        resp = self.app.apply_tick(tick)
        self.assertIsNone(resp)

        # 0 CMD_MOVE sent to FakeEsp32
        moves = [cmd for cmd in self.fake.executed_commands if cmd.get("type") == "CMD_MOVE"]
        self.assertEqual(len(moves), 0)

    # -------------------------------------------------------------------------
    # Scenario 4: Missing pose
    # -------------------------------------------------------------------------
    def test_scenario_04_missing_pose(self):
        self.app.handle_message(boot_frame(1))
        self.app.handle_message(tele_frame(2))
        self.app.complete_startup()
        self.app.set_mission(MissionState.NAVIGATING)
        self.app.set_task(TaskObjective(TaskType.GO_TO_TARGET, target=WorldPoint(5.0, 5.0)))

        # Explicitly no pose set
        self.app.clear_pose()
        tick = self.app.tick()

        self.assertEqual(tick.nav_output.status, NavigationStatus.NO_POSE)
        self.assertEqual(tick.nav_output.movement.kind, MovementKind.NONE)
        self.assertEqual(tick.directive.kind, DirectiveKind.NONE)

    # -------------------------------------------------------------------------
    # Scenario 5: Stale telemetry
    # -------------------------------------------------------------------------
    def test_scenario_05_stale_telemetry(self):
        self.app.handle_message(boot_frame(1))
        self.app.handle_message(tele_frame(2))
        self.app.complete_startup()
        self.app.set_pose(Pose(1.0, 1.0, 0.0))

        # Check motion gate with very old clock time
        tick = self.app.tick(telemetry_max_age_s=0.5, now=time.monotonic() + 10.0)
        self.assertTrue(tick.gated)
        self.assertIn("stale", tick.gate_reason or "")

    # -------------------------------------------------------------------------
    # Scenario 6: ESP32 fault
    # -------------------------------------------------------------------------
    def test_scenario_06_esp32_fault(self):
        self.app.handle_message(boot_frame(1))
        self.app.handle_message(tele_frame(2))
        self.app.complete_startup()
        self.app.set_mission(MissionState.NAVIGATING)

        # Ingest telemetry with fault code 4 (e.g. overcurrent)
        self.app.handle_message(tele_frame(3, faults=(4,)))

        self.assertEqual(self.app.machine.safety, SafetyCondition.FAULT_LATCHED)
        self.assertEqual(self.app.machine.mission, MissionState.IDLE)  # Mission aborted

        tick = self.app.tick()
        self.assertTrue(tick.gated)

    # -------------------------------------------------------------------------
    # Scenario 7: Clean telemetry after fault does NOT auto-clear
    # -------------------------------------------------------------------------
    def test_scenario_07_clean_telemetry_after_fault(self):
        self.app.handle_message(boot_frame(1))
        self.app.handle_message(tele_frame(2, faults=(4,)))
        self.assertEqual(self.app.machine.safety, SafetyCondition.FAULT_LATCHED)

        # Ingest clean telemetry
        self.app.handle_message(tele_frame(3, faults=()))
        self.assertEqual(self.app.machine.safety, SafetyCondition.FAULT_LATCHED)  # Still latched!

    # -------------------------------------------------------------------------
    # Scenario 8: Local reset does NOT clear ESP32 physical latch reflection
    # -------------------------------------------------------------------------
    def test_scenario_08_local_reset(self):
        self.app.handle_message(boot_frame(1))
        self.app.handle_message(tele_frame(2, faults=(4,)))
        self.assertEqual(self.app.machine.safety, SafetyCondition.FAULT_LATCHED)

        # Local application reset
        res = self.app.reset_local_application()
        self.assertTrue(res.ok)
        self.assertEqual(self.app.machine.safety, SafetyCondition.FAULT_LATCHED)  # Latch preserved!

    # -------------------------------------------------------------------------
    # Scenario 9: Verified reset-all
    # -------------------------------------------------------------------------
    def test_scenario_09_verified_reset_all(self):
        self.fake.start()
        self.fake.set_ready()

        self.app.handle_message(boot_frame(1))
        self.app.handle_message(tele_frame(2, faults=(4,)))
        self.assertEqual(self.app.machine.safety, SafetyCondition.FAULT_LATCHED)

        # Configure fake ESP32 to accept reset
        resp = self.app.esp32_reset_all(timeout=1.0)
        self.assertEqual(resp.get("type"), "RESP_OK")
        self.assertEqual(self.app.machine.safety, SafetyCondition.NOMINAL)  # Safety restored

    # -------------------------------------------------------------------------
    # Scenario 10: Emergency stop
    # -------------------------------------------------------------------------
    def test_scenario_10_emergency_stop(self):
        self.fake.start()
        self.fake.set_ready()

        self.app.handle_message(boot_frame(1))
        self.app.handle_message(tele_frame(2))
        self.app.complete_startup()

        # Issue emergency stop
        res = self.app.emergency_stop(cause="test emergency")
        self.assertTrue(res.ok)
        self.assertEqual(self.app.machine.safety, SafetyCondition.FAULT_LATCHED)

        # Verify fake ESP32 latched and event logger recorded emergency stop
        self.assertTrue(self.fake.latched)
        self.assertTrue(self.app.event_logger.counters["estop"] >= 1)

    # -------------------------------------------------------------------------
    # Scenario 11: Normal stop
    # -------------------------------------------------------------------------
    def test_scenario_11_normal_stop(self):
        self.app.handle_message(boot_frame(1))
        self.app.handle_message(tele_frame(2))
        self.app.complete_startup()
        self.app.set_mission(MissionState.NAVIGATING)

        res = self.app.request_stop()
        self.assertTrue(res.ok)
        self.assertEqual(self.app.machine.mission, MissionState.IDLE)
        self.assertEqual(self.app.machine.safety, SafetyCondition.NOMINAL)

    # -------------------------------------------------------------------------
    # Scenario 12: Perception exit hint
    # -------------------------------------------------------------------------
    def test_scenario_12_perception_exit_hint(self):
        self.app.handle_message(boot_frame(1))
        self.app.handle_message(tele_frame(2))
        self.app.complete_startup()
        self.app.set_pose(Pose(0.0, 0.0, 0.0))
        self.app.set_mission(MissionState.SEARCHING)

        hint_payload = {
            "exit_zone": "WEST",
            "estimated_position": {"x": 1.2, "y": 2.4},
            "confidence": 0.92,
        }
        hint = self.app.set_exit_hint(hint_payload)
        self.assertEqual(hint.exit_zone, ZoneId.WEST)

        tick = self.app.tick()
        self.assertEqual(tick.nav_output.target, WorldPoint(1.2, 2.4))
        self.assertEqual(tick.nav_output.zone, ZoneId.WEST)

    # -------------------------------------------------------------------------
    # Scenario 13: Malformed TELE
    # -------------------------------------------------------------------------
    def test_scenario_13_malformed_tele(self):
        initial_ignored = self.app.coordinator.stats["ignored"]
        # Supply TELE missing required fields (e.g. missing seq/ts/sys)
        malformed_msg = {"type": "TELE", "v": 1}
        self.app.handle_message(malformed_msg)

        self.assertTrue(self.app.coordinator.stats["ignored"] > initial_ignored)
        # Safety remains conservative
        self.assertFalse(self.app.state.has_telemetry)

    # -------------------------------------------------------------------------
    # Scenario 14: Unknown inbound message
    # -------------------------------------------------------------------------
    def test_scenario_14_unknown_inbound(self):
        initial_ignored = self.app.coordinator.stats["ignored"]
        unknown_msg = {"v": 1, "type": "UNKNOWN_FUTURE_EVENT", "seq": 99, "ts": 123}
        self.app.handle_message(unknown_msg)

        self.assertEqual(self.app.coordinator.stats["ignored"], initial_ignored + 1)

    # -------------------------------------------------------------------------
    # Scenario 15: Repeated events
    # -------------------------------------------------------------------------
    def test_scenario_15_repeated_events(self):
        self.app.handle_message(boot_frame(1))
        self.app.handle_message(boot_frame(2))  # Repeated boot
        self.app.handle_message(tele_frame(3))
        self.app.complete_startup()

        # Repeated complete_startup
        res2 = self.app.complete_startup()
        self.assertTrue(res2.ok)
        self.assertEqual(res2.reason, "already ready")

        # Repeated normal stop
        self.app.request_stop()
        res_stop = self.app.request_stop()
        self.assertTrue(res_stop.ok)

    # -------------------------------------------------------------------------
    # Scenario 16: Full no-motion invariant across large scenario matrix
    # -------------------------------------------------------------------------
    def test_scenario_16_full_no_motion_invariant(self):
        # Run a large matrix of state transitions, missions, tasks, faults, and ticks
        self.app.handle_message(boot_frame(1))
        self.app.handle_message(tele_frame(2))
        self.app.complete_startup()
        self.app.set_pose(Pose(0.0, 0.0, 0.0))

        missions = [
            MissionState.IDLE,
            MissionState.NAVIGATING,
            MissionState.SEARCHING,
            MissionState.APPROACHING,
            MissionState.COLLECTING,
            MissionState.RETURNING,
        ]

        targets = [WorldPoint(1.0, 1.0), WorldPoint(-2.0, 3.0), None]
        zones = [ZoneId.WEST, ZoneId.EAST, None]

        for i, m in enumerate(missions):
            self.app.set_mission(m)
            for target in targets:
                for zone in zones:
                    self.app.handle_message(tele_frame(10 + i))
                    if target:
                        self.app.set_task(TaskObjective(TaskType.GO_TO_TARGET, target=target, zone=zone))
                    tick = self.app.tick()
                    if not tick.gated and tick.directive.kind is DirectiveKind.SEND:
                        self.app.apply_tick(tick)

        # Assert exactly ZERO CMD_MOVE commands were transmitted to FakeEsp32
        cmd_moves = [cmd for cmd in self.fake.executed_commands if cmd.get("type") == "CMD_MOVE"]
        self.assertEqual(len(cmd_moves), 0, f"No-motion invariant violated! Sent: {cmd_moves}")


if __name__ == "__main__":
    unittest.main()
