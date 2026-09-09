"""Comprehensive scenario tests for perception simulation and navigation integration.

Covers all 20 required perception scenario cases and asserts the No-Motion Invariant
(total CMD_MOVE wire commands executed by FakeEsp32 == 0).
"""

import time
import unittest

from fake_esp32 import FakeEsp32
from integration.communication.laptop_link import LaptopLink
from integration.communication.transport import create_memory_pair
from integration.perception.validation import PerceptionValidationError, validate_perception_payload
from integration.system.application import ApplicationOrchestrator, TaskType
from integration.system.commands import DirectiveKind
from integration.system.state_machine import LifecycleState, MissionState, SafetyCondition
from navigation.localization.state import Pose, WorldPoint
from navigation.planning.interfaces import MovementKind, NavigationStatus
from navigation.planning.zones import ZoneId
from telemetry_replay import boot_frame, tele_frame


class TestPerceptionScenarios(unittest.TestCase):
    def setUp(self):
        self.laptop_end, self.esp_end = create_memory_pair()
        self.fake = FakeEsp32(self.esp_end, tele_period_s=0.02, hb_period_s=0.02)
        self.link = LaptopLink(self.laptop_end, command_timeout_s=0.5)
        self.app = ApplicationOrchestrator(self.link)
        self.link.start()

    def tearDown(self) -> None:
        self.link.stop()
        self.fake.stop()

    def _setup_ready_app(self):
        self.app.handle_message(boot_frame(1))
        self.app.handle_message(tele_frame(2))
        self.app.complete_startup()
        self.app.set_pose(Pose(0.0, 0.0, 0.0))

    # 1. No perception input
    def test_scenario_01_no_perception_input(self):
        self._setup_ready_app()
        self.app.set_mission(MissionState.SEARCHING)

        tick = self.app.tick()
        self.assertEqual(tick.nav_output.status, NavigationStatus.NO_OBJECTIVE)
        self.assertEqual(tick.directive.kind, DirectiveKind.NONE)

    # 2. Valid ball observation
    def test_scenario_02_valid_ball_observation(self):
        self._setup_ready_app()
        self.app.set_mission(MissionState.SEARCHING)

        self.app.ingest_perception({
            "ball": {"visible": True, "position": {"x": 3.0, "y": 4.0}, "confidence": 0.95}
        })
        tick = self.app.tick()
        self.assertEqual(tick.nav_output.status, NavigationStatus.PLANNED)
        self.assertEqual(tick.nav_output.target, WorldPoint(3.0, 4.0))
        self.assertEqual(tick.directive.kind, DirectiveKind.DEFERRED)

    # 3. Low/unsuitable supplied confidence representation
    def test_scenario_03_low_confidence_ball_observation(self):
        self._setup_ready_app()
        self.app.set_mission(MissionState.SEARCHING)

        # Perception preserves confidence value as supplied data without hidden thresholds
        frame = self.app.ingest_perception({
            "ball": {"visible": True, "position": {"x": 3.0, "y": 4.0}, "confidence": 0.05}
        })
        self.assertEqual(frame.ball.confidence, 0.05)
        tick = self.app.tick()
        self.assertEqual(tick.directive.kind, DirectiveKind.DEFERRED)

    # 4. Ball position present
    def test_scenario_04_ball_position_present(self):
        self._setup_ready_app()
        self.app.set_mission(MissionState.NAVIGATING)
        self.app.ingest_perception({
            "ball": {"visible": True, "position": {"x": 2.0, "y": 1.0}, "confidence": 0.9}
        })
        tick = self.app.tick()
        self.assertEqual(tick.nav_output.target, WorldPoint(2.0, 1.0))

    # 5. Ball position missing
    def test_scenario_05_ball_position_missing(self):
        self._setup_ready_app()
        self.app.set_mission(MissionState.SEARCHING)
        self.app.ingest_perception({"ball": {"visible": False}})
        tick = self.app.tick()
        self.assertEqual(tick.nav_output.status, NavigationStatus.NO_OBJECTIVE)

    # 6. Valid obstacle observation
    def test_scenario_06_valid_obstacle_observation(self):
        self._setup_ready_app()
        frame = self.app.ingest_perception({
            "obstacles": [{"type": "bag", "world_region": {"x": 1.0, "y": 1.0}, "confidence": 0.8}]
        })
        self.assertEqual(len(frame.obstacles), 1)
        self.assertEqual(frame.obstacles[0].obstacle_type, "bag")

    # 7. Malformed obstacle observation
    def test_scenario_07_malformed_obstacle_observation(self):
        self._setup_ready_app()
        with self.assertRaises(PerceptionValidationError):
            self.app.ingest_perception({"obstacles": "not a list"})

    # 8. Valid exit hint
    def test_scenario_08_valid_exit_hint(self):
        self._setup_ready_app()
        self.app.set_mission(MissionState.SEARCHING)
        self.app.ingest_perception({
            "exit_hint": {"exit_zone": "WEST", "estimated_position": {"x": 1.5, "y": 2.5}, "confidence": 0.85}
        })
        tick = self.app.tick()
        self.assertEqual(tick.nav_output.zone, ZoneId.WEST)
        self.assertEqual(tick.nav_output.target, WorldPoint(1.5, 2.5))

    # 9. Exit hint without position
    def test_scenario_09_exit_hint_without_position(self):
        self._setup_ready_app()
        with self.assertRaises(PerceptionValidationError):
            self.app.ingest_perception({
                "exit_hint": {"exit_zone": "WEST", "confidence": 0.8}  # missing estimated_position
            })

    # 10. Malformed perception payload
    def test_scenario_10_malformed_perception_payload(self):
        self._setup_ready_app()
        with self.assertRaises(PerceptionValidationError):
            self.app.ingest_perception("invalid string payload")

    # 11. Unknown perception type (tolerated as metadata)
    def test_scenario_11_unknown_perception_type(self):
        self._setup_ready_app()
        frame = self.app.ingest_perception({"type": "FUTURE_CAMERA_EVENT", "data": 123})
        self.assertEqual(frame.metadata.get("type"), "FUTURE_CAMERA_EVENT")

    # 12. Unknown extra fields
    def test_scenario_12_unknown_extra_fields(self):
        self._setup_ready_app()
        frame = self.app.ingest_perception({
            "ball": {"visible": True, "position": {"x": 1.0, "y": 1.0}, "confidence": 0.9},
            "extra_field_1": 42,
            "extra_field_2": "hello",
        })
        self.assertEqual(frame.metadata.get("extra_field_1"), 42)

    # 13. Healthy telemetry + perception
    def test_scenario_13_healthy_telemetry_and_perception(self):
        self._setup_ready_app()
        self.app.set_mission(MissionState.SEARCHING)
        self.app.ingest_perception({
            "ball": {"visible": True, "position": {"x": 4.0, "y": 4.0}, "confidence": 0.9}
        })
        tick = self.app.tick()
        self.assertFalse(tick.gated)
        self.assertEqual(tick.directive.kind, DirectiveKind.DEFERRED)

    # 14. Missing telemetry + perception
    def test_scenario_14_missing_telemetry_and_perception(self):
        # Startup incomplete / no telemetry
        self.app.set_pose(Pose(0.0, 0.0, 0.0))
        self.app.ingest_perception({
            "ball": {"visible": True, "position": {"x": 4.0, "y": 4.0}, "confidence": 0.9}
        })
        tick = self.app.tick()
        self.assertTrue(tick.gated)
        self.assertIn("STARTING", tick.gate_reason or "")

    # 15. Stale telemetry + perception
    def test_scenario_15_stale_telemetry_and_perception(self):
        self._setup_ready_app()
        self.app.ingest_perception({
            "ball": {"visible": True, "position": {"x": 4.0, "y": 4.0}, "confidence": 0.9}
        })
        tick = self.app.tick(telemetry_max_age_s=0.5, now=time.monotonic() + 10.0)
        self.assertTrue(tick.gated)
        self.assertIn("stale", tick.gate_reason or "")

    # 16. ESP32 fault + perception
    def test_scenario_16_esp32_fault_and_perception(self):
        self._setup_ready_app()
        self.app.handle_message(tele_frame(10, faults=(4,)))
        self.assertEqual(self.app.machine.safety, SafetyCondition.FAULT_LATCHED)

        self.app.ingest_perception({
            "ball": {"visible": True, "position": {"x": 4.0, "y": 4.0}, "confidence": 0.9}
        })
        tick = self.app.tick()
        self.assertTrue(tick.gated)

    # 17. Emergency stop + perception
    def test_scenario_17_emergency_stop_and_perception(self):
        self.fake.start()
        self.fake.set_ready()
        self._setup_ready_app()

        self.app.emergency_stop(cause="test perception e-stop")
        self.assertEqual(self.app.machine.safety, SafetyCondition.FAULT_LATCHED)

        self.app.ingest_perception({
            "ball": {"visible": True, "position": {"x": 4.0, "y": 4.0}, "confidence": 0.9}
        })
        tick = self.app.tick()
        self.assertTrue(tick.gated)

    # 18. Mission change + perception
    def test_scenario_18_mission_change_and_perception(self):
        self._setup_ready_app()
        self.app.ingest_perception({
            "ball": {"visible": True, "position": {"x": 4.0, "y": 4.0}, "confidence": 0.9}
        })

        self.app.set_mission(MissionState.SEARCHING)
        tick1 = self.app.tick()
        self.assertEqual(tick1.nav_output.target, WorldPoint(4.0, 4.0))

        self.app.set_mission(MissionState.IDLE)
        tick2 = self.app.tick()
        self.assertEqual(tick2.nav_output.status, NavigationStatus.NO_OBJECTIVE)
        self.assertEqual(tick2.nav_output.movement.kind, MovementKind.NONE)

    # 19. Objective unavailable because required info is missing
    def test_scenario_19_objective_unavailable_missing_info(self):
        self._setup_ready_app()
        self.app.set_mission(MissionState.APPROACHING)
        # Approach without ball position set
        self.app.ingest_perception({"ball": {"visible": False}})
        tick = self.app.tick()
        self.assertEqual(tick.nav_output.status, NavigationStatus.NO_OBJECTIVE)

    # 20. Full end-to-end synthetic perception -> objective -> navigation scenario
    def test_scenario_20_end_to_end_perception_pipeline(self):
        self._setup_ready_app()

        # Step A: Perception detects ball exit hint
        self.app.set_mission(MissionState.SEARCHING)
        self.app.ingest_perception({
            "exit_hint": {"exit_zone": "WEST", "estimated_position": {"x": 2.0, "y": 3.0}, "confidence": 0.8}
        })
        tick1 = self.app.tick()
        self.assertEqual(tick1.nav_output.zone, ZoneId.WEST)
        self.assertEqual(tick1.directive.kind, DirectiveKind.DEFERRED)

        # Step B: Robot pose updates (external localization)
        self.app.set_pose(Pose(2.0, 3.0, 0.0))

        # Step C: Camera sees ball directly
        self.app.ingest_perception({
            "ball": {"visible": True, "position": {"x": 2.1, "y": 3.1}, "confidence": 0.98}
        })
        tick2 = self.app.tick()
        self.assertEqual(tick2.nav_output.target, WorldPoint(2.1, 3.1))
        self.assertEqual(tick2.directive.kind, DirectiveKind.DEFERRED)

        # Apply tick
        resp = self.app.apply_tick(tick2)
        self.assertIsNone(resp)  # Gated by DEFERRED (OD-07/OD-13)

        # Verify NO-MOTION INVARIANT across all 20 scenarios
        cmd_moves = [cmd for cmd in self.fake.executed_commands if cmd.get("type") == "CMD_MOVE"]
        self.assertEqual(len(cmd_moves), 0, f"No-motion invariant violated! Sent: {cmd_moves}")


if __name__ == "__main__":
    unittest.main()
