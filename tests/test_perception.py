"""Unit tests for synthetic perception models, validation boundary, and seams."""

import unittest

from integration.perception.model import BallObservation, ObstacleObservation, PerceptionFrame
from integration.perception.seams import derive_objective_from_perception
from integration.perception.validation import PerceptionValidationError, validate_perception_payload
from integration.system.application import TaskType
from integration.system.state_machine import MissionState
from navigation.localization.state import WorldPoint
from navigation.planning.interfaces import ExitHint
from navigation.planning.zones import ZoneId


class TestPerceptionModelsAndValidation(unittest.TestCase):
    def test_valid_payload_parsing(self):
        payload = {
            "timestamp": 123.45,
            "ball": {
                "visible": True,
                "position": {"x": 2.5, "y": 4.0},
                "confidence": 0.95,
                "velocity": [0.1, -0.2],
            },
            "obstacles": [
                {
                    "type": "person",
                    "world_region": {"center": {"x": 5.0, "y": 5.0}},
                    "confidence": 0.88,
                }
            ],
            "exit_hint": {
                "exit_zone": "WEST",
                "estimated_position": {"x": 1.0, "y": 2.0},
                "confidence": 0.75,
            },
            "unknown_extra_field": "tolerated_value",
        }

        frame = validate_perception_payload(payload)
        self.assertEqual(frame.timestamp, 123.45)
        self.assertIsNotNone(frame.ball)
        self.assertTrue(frame.ball.visible)
        self.assertEqual(frame.ball.position, WorldPoint(2.5, 4.0))
        self.assertEqual(frame.ball.velocity, (0.1, -0.2))
        self.assertEqual(frame.ball.confidence, 0.95)

        self.assertEqual(len(frame.obstacles), 1)
        self.assertEqual(frame.obstacles[0].obstacle_type, "person")
        self.assertEqual(frame.obstacles[0].confidence, 0.88)

        self.assertIsNotNone(frame.exit_hint)
        self.assertEqual(frame.exit_hint.exit_zone, ZoneId.WEST)
        self.assertEqual(frame.metadata.get("unknown_extra_field"), "tolerated_value")

    def test_malformed_payload_types(self):
        with self.assertRaises(PerceptionValidationError):
            validate_perception_payload("not a dict")

        with self.assertRaises(PerceptionValidationError):
            validate_perception_payload({"ball": "not a dict"})

        with self.assertRaises(PerceptionValidationError):
            validate_perception_payload({"ball": {"position": {"x": "invalid", "y": 1.0}}})

        with self.assertRaises(PerceptionValidationError):
            validate_perception_payload({"obstacles": "not a list"})

        with self.assertRaises(PerceptionValidationError):
            validate_perception_payload({"exit_hint": {"exit_zone": "INVALID_ZONE", "estimated_position": {"x": 0, "y": 0}, "confidence": 1}})


class TestPerceptionSeams(unittest.TestCase):
    def test_derive_objective_idle(self):
        frame = validate_perception_payload({})
        obj = derive_objective_from_perception(frame, MissionState.IDLE)
        self.assertEqual(obj.objective_type, TaskType.HOLD)

    def test_derive_objective_searching_with_ball(self):
        frame = validate_perception_payload({
            "ball": {"visible": True, "position": {"x": 3.0, "y": 4.0}, "confidence": 0.9}
        })
        obj = derive_objective_from_perception(frame, MissionState.SEARCHING)
        self.assertEqual(obj.objective_type, TaskType.GO_TO_TARGET)
        self.assertEqual(obj.target, WorldPoint(3.0, 4.0))
        self.assertEqual(obj.source, "perception_ball_found")

    def test_derive_objective_searching_with_exit_hint(self):
        frame = validate_perception_payload({
            "exit_hint": {"exit_zone": "EAST", "estimated_position": {"x": 5.0, "y": 2.0}, "confidence": 0.8}
        })
        obj = derive_objective_from_perception(frame, MissionState.SEARCHING)
        self.assertEqual(obj.objective_type, TaskType.SEARCH_ZONE)
        self.assertEqual(obj.zone, ZoneId.EAST)
        self.assertEqual(obj.target, WorldPoint(5.0, 2.0))

    def test_derive_objective_searching_no_info(self):
        frame = validate_perception_payload({})
        obj = derive_objective_from_perception(frame, MissionState.SEARCHING)
        self.assertEqual(obj.objective_type, TaskType.NO_OBJECTIVE)
        self.assertIn("OD-03", obj.metadata.get("note", ""))

    def test_derive_objective_approaching_blocked_by_od01(self):
        frame = validate_perception_payload({})
        obj = derive_objective_from_perception(frame, MissionState.APPROACHING)
        self.assertEqual(obj.objective_type, TaskType.NO_OBJECTIVE)
        self.assertIn("OD-01", obj.metadata.get("note", ""))

    def test_derive_objective_collecting_blocked_by_od04(self):
        frame = validate_perception_payload({})
        obj = derive_objective_from_perception(frame, MissionState.COLLECTING)
        self.assertEqual(obj.objective_type, TaskType.NO_OBJECTIVE)
        self.assertIn("OD-04", obj.metadata.get("note", ""))


if __name__ == "__main__":
    unittest.main()
