"""Tests for the deterministic navigation planning abstraction.

These tests exercise Module 2 *internal* planning only (Person 3's own
pipeline).  The cross-module I-3/I-4 payloads stay indicative until open
decision OD-03; nothing here claims to implement them.  All outcomes are
pure functions of the inputs — no hardware, no tuning constants.
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from navigation.localization.state import Pose, WorldPoint  # noqa: E402
from navigation.planning.interfaces import (  # noqa: E402
    ExitHint,
    MovementIntent,
    MovementKind,
    NavigationError,
    NavigationInput,
    NavigationOutput,
    NavigationStatus,
    Objective,
)
from navigation.planning.planner import plan_navigation  # noqa: E402
from navigation.planning.zones import (  # noqa: E402
    Rectangle,
    SearchFocus,
    ZoneConfig,
    ZoneId,
)


class TestExitHint(unittest.TestCase):
    def test_valid_hint(self):
        hint = ExitHint(ZoneId.WEST, WorldPoint(5.0, 5.0), 0.8)
        self.assertEqual(hint.exit_zone, ZoneId.WEST)
        self.assertEqual(hint.estimated_position, WorldPoint(5.0, 5.0))
        self.assertEqual(hint.confidence, 0.8)

    def test_invalid_hint_rejected(self):
        with self.assertRaises(NavigationError):
            ExitHint("WEST", WorldPoint(0.0, 0.0), 0.5)  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            ExitHint(ZoneId.WEST, WorldPoint(0.0, 0.0), float("nan"))  # type: ignore[arg-type]

    def test_confidence_range_not_enforced(self):
        # §6.4 leaves the useful/low threshold as a calibration parameter;
        # the container must not silently pick a range.
        ExitHint(ZoneId.NORTH, WorldPoint(0.0, 0.0), -3.0)  # finite -> accepted
        ExitHint(ZoneId.NORTH, WorldPoint(0.0, 0.0), 42.0)


class TestNavigationInput(unittest.TestCase):
    def test_defaults_are_empty(self):
        nav = NavigationInput()
        self.assertIsNone(nav.pose)
        self.assertIsNone(nav.target)
        self.assertIsNone(nav.zone)
        self.assertIsNone(nav.hint)

    def test_invalid_field_types_rejected(self):
        with self.assertRaises(NavigationError):
            NavigationInput(target=(1.0, 2.0))  # type: ignore[arg-type]
        with self.assertRaises(NavigationError):
            NavigationInput(zone="WEST")  # type: ignore[arg-type]
        with self.assertRaises(NavigationError):
            NavigationInput(hint={"exit_zone": "WEST"})  # type: ignore[arg-type]


class TestPlanner(unittest.TestCase):
    POSE = Pose(1.0, 2.0, 0.0)

    def test_no_pose_blocks_all_planning(self):
        # Localization is a prerequisite; the planner must not guess a pose.
        out = plan_navigation(NavigationInput(target=WorldPoint(5.0, 5.0)))
        self.assertEqual(out.status, NavigationStatus.NO_POSE)
        self.assertEqual(out.objective, Objective.HOLD)
        self.assertEqual(out.movement.kind, MovementKind.NONE)

    def test_no_objective_returns_hold(self):
        out = plan_navigation(NavigationInput(pose=self.POSE))
        self.assertEqual(out.status, NavigationStatus.NO_OBJECTIVE)
        self.assertEqual(out.objective, Objective.HOLD)

    def test_target_plans_geometry_only(self):
        out = plan_navigation(
            NavigationInput(pose=self.POSE, target=WorldPoint(4.0, 6.0))
        )
        self.assertEqual(out.status, NavigationStatus.PLANNED)
        self.assertEqual(out.objective, Objective.GO_TO_TARGET)
        self.assertEqual(out.movement.kind, MovementKind.MOVE)
        # 3-4-5 triangle: distance 5 m, bearing atan2(4, 3).
        self.assertAlmostEqual(out.movement.distance_m, 5.0)
        self.assertAlmostEqual(out.movement.bearing_world, 0.9272952180016123)
        self.assertEqual(out.target, WorldPoint(4.0, 6.0))

    def test_zone_without_bounds_is_unconfigured_not_invented(self):
        # DEC-018: zone extents are venue configuration — no invented box.
        out = plan_navigation(
            NavigationInput(pose=self.POSE, zone=ZoneId.WEST), zones=[]
        )
        self.assertEqual(out.status, NavigationStatus.ZONE_UNCONFIGURED)
        self.assertEqual(out.objective, Objective.SEARCH_ZONE)
        self.assertEqual(out.movement.kind, MovementKind.NONE)

    def test_configured_zone_plans_search(self):
        zones = [ZoneConfig(ZoneId.WEST, Rectangle(-20.0, 0.0, -10.0, 10.0))]
        out = plan_navigation(
            NavigationInput(pose=self.POSE, zone=ZoneId.WEST), zones=zones
        )
        self.assertEqual(out.status, NavigationStatus.PLANNED)
        self.assertEqual(out.objective, Objective.SEARCH_ZONE)
        self.assertEqual(out.zone, ZoneId.WEST)
        self.assertEqual(out.movement.kind, MovementKind.MOVE)

    def test_hint_selects_zone_and_search_hint_target(self):
        hint = ExitHint(ZoneId.EAST, WorldPoint(8.0, 0.0), 0.9)
        out = plan_navigation(
            NavigationInput(
                pose=self.POSE,
                hint=hint,
                useful_confidence_threshold=0.5,  # SIMULATION/TEST ONLY value
            ),
            zones=[ZoneConfig(ZoneId.EAST, Rectangle(0.0, 20.0, -10.0, 10.0))],
        )
        self.assertEqual(out.status, NavigationStatus.PLANNED)
        self.assertEqual(out.objective, Objective.SEARCH_ZONE)
        self.assertEqual(out.zone, ZoneId.EAST)
        self.assertEqual(out.target, WorldPoint(8.0, 0.0))
        self.assertIn("around_estimate", out.reason)

    def test_hint_low_confidence_leads_to_systematic_search(self):
        hint = ExitHint(ZoneId.EAST, WorldPoint(8.0, 0.0), 0.1)
        out = plan_navigation(
            NavigationInput(pose=self.POSE, hint=hint, useful_confidence_threshold=0.5),
            zones=[ZoneConfig(ZoneId.EAST, Rectangle(0.0, 20.0, -10.0, 10.0))],
        )
        self.assertIn(SearchFocus.SYSTEMATIC.value, out.reason)

    def test_hint_zone_unconfigured(self):
        hint = ExitHint(ZoneId.SOUTH, WorldPoint(0.0, -8.0), 0.9)
        out = plan_navigation(
            NavigationInput(pose=self.POSE, hint=hint, useful_confidence_threshold=0.5),
            zones=[],  # no zone bounds configured anywhere
        )
        self.assertEqual(out.status, NavigationStatus.ZONE_UNCONFIGURED)
        self.assertEqual(out.movement.kind, MovementKind.NONE)

    def test_no_lin_ang_magnitudes_anywhere(self):
        # OD-07/OD-13: the output carries intent + world geometry only.
        out = plan_navigation(
            NavigationInput(pose=self.POSE, target=WorldPoint(0.0, 0.0))
        )
        self.assertIsInstance(out.movement, MovementIntent)
        self.assertFalse(hasattr(out.movement, "lin"))
        self.assertFalse(hasattr(out.movement, "ang"))
        self.assertFalse(hasattr(out, "lin"))

    def test_determinism(self):
        nav = NavigationInput(pose=self.POSE, target=WorldPoint(3.0, -4.0))
        self.assertEqual(plan_navigation(nav), plan_navigation(nav))


if __name__ == "__main__":
    unittest.main()