"""Unit tests for ApplicationOrchestrator, TaskObjective, derive_objective, and StructuredEventLogger."""

import unittest
from unittest.mock import MagicMock

from integration.communication.laptop_link import LaptopLink
from integration.system.application import (
    ApplicationEvent,
    ApplicationOrchestrator,
    ApplicationStatus,
    StructuredEventLogger,
    TaskObjective,
    TaskType,
    derive_objective,
)
from integration.system.state_machine import LifecycleState, MissionState, SafetyCondition
from navigation.localization.state import Pose, WorldPoint
from navigation.planning.interfaces import ExitHint, NavigationStatus
from navigation.planning.zones import ZoneId


class TestTaskObjectiveAndDerivation(unittest.TestCase):
    def test_derive_objective_idle(self):
        obj = derive_objective(MissionState.IDLE)
        self.assertEqual(obj.objective_type, TaskType.HOLD)
        self.assertEqual(obj.source, "mission_seam:idle")

    def test_derive_objective_navigating_with_target(self):
        target = WorldPoint(1.5, 2.0)
        obj = derive_objective(MissionState.NAVIGATING, target=target)
        self.assertEqual(obj.objective_type, TaskType.GO_TO_TARGET)
        self.assertEqual(obj.target, target)

    def test_derive_objective_navigating_with_zone(self):
        obj = derive_objective(MissionState.NAVIGATING, zone=ZoneId.WEST)
        self.assertEqual(obj.objective_type, TaskType.SEARCH_ZONE)
        self.assertEqual(obj.zone, ZoneId.WEST)

    def test_derive_objective_searching_with_exit_hint(self):
        hint = ExitHint(ZoneId.EAST, WorldPoint(3.0, 1.0), 0.9)
        obj = derive_objective(MissionState.SEARCHING, hint=hint)
        self.assertEqual(obj.objective_type, TaskType.SEARCH_ZONE)
        self.assertEqual(obj.zone, ZoneId.EAST)
        self.assertEqual(obj.target, WorldPoint(3.0, 1.0))
        self.assertEqual(obj.source, "perception_exit_hint")

    def test_derive_objective_searching_blocked_by_od03(self):
        obj = derive_objective(MissionState.SEARCHING)
        self.assertEqual(obj.objective_type, TaskType.NO_OBJECTIVE)
        self.assertIn("OD-03", obj.metadata.get("note", ""))

    def test_derive_objective_approaching_blocked_by_od01(self):
        obj = derive_objective(MissionState.APPROACHING)
        self.assertEqual(obj.objective_type, TaskType.NO_OBJECTIVE)
        self.assertIn("OD-01", obj.metadata.get("note", ""))

    def test_derive_objective_collecting_blocked_by_od04(self):
        obj = derive_objective(MissionState.COLLECTING)
        self.assertEqual(obj.objective_type, TaskType.NO_OBJECTIVE)
        self.assertIn("OD-04", obj.metadata.get("note", ""))

    def test_derive_objective_returning_with_base(self):
        base = WorldPoint(0.0, 0.0)
        obj = derive_objective(MissionState.RETURNING, base_location=base)
        self.assertEqual(obj.objective_type, TaskType.RETURN_TO_BASE)
        self.assertEqual(obj.target, base)


class TestStructuredEventLogger(unittest.TestCase):
    def test_logging_and_counters(self):
        logger = StructuredEventLogger()
        logger.log("telemetry", "TELEMETRY_RECEIVED", {"seq": 1})
        logger.log("fault", "ESP32_FAULT_OBSERVED", {"faults": (4,)})
        logger.log("command", "COMMAND_SENT", {"message_type": "CMD_STOP"})
        logger.log("command", "COMMAND_GATED", {"reason": "starting"})
        logger.log("command", "COMMAND_DEFERRED", {"reason": "OD-07"})
        logger.log("safety", "EMERGENCY_STOP", {"cause": "user"})

        self.assertEqual(len(logger.events), 6)
        counters = logger.counters
        self.assertEqual(counters["telemetry"], 1)
        self.assertEqual(counters["faults"], 1)
        self.assertEqual(counters["commands_sent"], 1)
        self.assertEqual(counters["commands_gated"], 1)
        self.assertEqual(counters["commands_deferred"], 1)
        self.assertEqual(counters["estop"], 1)

        jsonl_str = logger.to_jsonl()
        self.assertIn("TELEMETRY_RECEIVED", jsonl_str)
        self.assertIn("EMERGENCY_STOP", jsonl_str)


class TestApplicationOrchestrator(unittest.TestCase):
    def setUp(self):
        self.mock_link = MagicMock(spec=LaptopLink)
        self.mock_link.on_tele = None
        self.mock_link.on_event = None
        self.mock_link.on_esp32_lost = None
        self.mock_link.on_esp32_restored = None
        self.mock_link.started = True
        self.mock_link.frozen = False
        self.mock_link.command.return_value = {"v": 1, "type": "RESP_OK", "seq": 1, "ack_seq": 1}
        self.app = ApplicationOrchestrator(self.mock_link)

    def test_initialization(self):
        status = self.app.status()
        self.assertEqual(status.lifecycle, LifecycleState.STARTING)
        self.assertEqual(status.safety, SafetyCondition.NOMINAL)
        self.assertEqual(status.mission, MissionState.IDLE)
        self.assertTrue(status.motion_gated)  # STARTING lifecycle gates motion

    def test_set_pose_and_clear_pose(self):
        pose = Pose(1.0, 2.0, 0.5)
        self.app.set_pose(pose)
        self.assertTrue(self.app.state.has_pose)
        self.assertEqual(self.app.state.pose, pose)

        self.app.clear_pose()
        self.assertFalse(self.app.state.has_pose)

    def test_set_exit_hint(self):
        payload = {
            "exit_zone": "WEST",
            "estimated_position": {"x": 0.5, "y": 0.8},
            "confidence": 0.85,
        }
        hint = self.app.set_exit_hint(payload)
        self.assertEqual(hint.exit_zone, ZoneId.WEST)
        self.assertEqual(self.app.current_hint, hint)

        self.app.clear_exit_hint()
        self.assertIsNone(self.app.current_hint)


if __name__ == "__main__":
    unittest.main()
