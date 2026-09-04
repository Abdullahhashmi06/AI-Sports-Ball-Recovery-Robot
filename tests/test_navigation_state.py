"""Tests for the world-frame types and the localization state container.

Covers the agreed coordinate conventions of ``TEAM_INTERFACE_CONTRACT.md``
§6.1 (DEC-008 … DEC-015): meters, negative coordinates valid, theta stored
verbatim (convention open, OD-13), pure world-frame geometry, and the
deterministic, calibration-free ``LocalizationEstimator``.
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from navigation.localization.estimator import LocalizationEstimator  # noqa: E402
from navigation.localization.interfaces import PoseSample, PoseSource  # noqa: E402
from navigation.localization.state import (  # noqa: E402
    Pose,
    WorldPoint,
    world_bearing,
    world_distance,
)


class TestWorldPoint(unittest.TestCase):
    def test_basic_construction(self):
        p = WorldPoint(1.0, 2.0)
        self.assertEqual(p.x, 1.0)
        self.assertEqual(p.y, 2.0)

    def test_negative_coordinates_are_valid(self):
        # DEC-014: negative coordinates are valid; no range may reject them.
        p = WorldPoint(-12.5, -3.25)
        self.assertEqual((p.x, p.y), (-12.5, -3.25))

    def test_int_inputs_coerced_to_float(self):
        p = WorldPoint(1, 2)
        self.assertIsInstance(p.x, float)
        self.assertEqual(p.x, 1.0)

    def test_invalid_values_rejected(self):
        for bad in (float("nan"), float("inf"), float("-inf"), "1", None, True):
            with self.assertRaises(ValueError):
                WorldPoint(bad, 0.0)
            with self.assertRaises(ValueError):
                WorldPoint(0.0, bad)

    def test_world_points_are_immutable(self):
        p = WorldPoint(1.0, 2.0)
        with self.assertRaises(Exception):
            p.x = 5.0  # type: ignore[misc]


class TestPose(unittest.TestCase):
    def test_pose_construction(self):
        pose = Pose(0.5, -1.0, 0.25)
        self.assertEqual(pose.x, 0.5)
        self.assertEqual(pose.y, -1.0)
        self.assertEqual(pose.theta, 0.25)

    def test_theta_stored_verbatim_no_range_no_normalization(self):
        # OD-13: theta units/zero-direction are NOT agreed.  Any finite
        # value must round-trip untouched (no wrap, no range enforcement).
        for theta in (0.0, 3.5, -3.5, 100.0, -100.0, 2 * 3.14159):
            pose = Pose(0.0, 0.0, theta)
            self.assertEqual(pose.theta, theta)

    def test_theta_must_be_finite(self):
        for bad in (float("nan"), float("inf"), "zero", None):
            with self.assertRaises(ValueError):
                Pose(0.0, 0.0, bad)  # type: ignore[arg-type]

    def test_position_drops_theta(self):
        pose = Pose(1.0, 2.0, 9.9)
        self.assertEqual(pose.position(), WorldPoint(1.0, 2.0))


class TestWorldGeometry(unittest.TestCase):
    def test_distance_is_euclidean_in_meters(self):
        a = WorldPoint(0.0, 0.0)
        b = WorldPoint(3.0, 4.0)
        self.assertEqual(world_distance(a, b), 5.0)
        self.assertEqual(a.distance_to(b), 5.0)

    def test_distance_negative_coordinates(self):
        # DEC-014 + meters: geometry works anywhere in the plane.
        self.assertEqual(world_distance(WorldPoint(-1.0, -1.0), WorldPoint(2.0, 3.0)), 5.0)

    def test_bearing_convention_from_plus_x_ccw(self):
        # World-frame bearing is measured from +X toward +Y (CCW), radians.
        origin = WorldPoint(0.0, 0.0)
        self.assertAlmostEqual(world_bearing(origin, WorldPoint(1.0, 0.0)), 0.0)
        self.assertAlmostEqual(world_bearing(origin, WorldPoint(0.0, 1.0)), 1.57079632679)
        self.assertAlmostEqual(world_bearing(origin, WorldPoint(-1.0, 0.0)), 3.14159265359)
        self.assertAlmostEqual(world_bearing(origin, WorldPoint(0.0, -1.0)), -1.57079632679)

    def test_bearing_is_world_frame_not_heading_relative(self):
        # The function ignores the robot's theta entirely — heading-relative
        # steering is deliberately absent (OD-13).
        a = WorldPoint(1.0, 1.0)
        b = WorldPoint(1.0, 5.0)
        self.assertAlmostEqual(world_bearing(a, b), 1.57079632679)


class _FakePoseSource:
    """Test pose source (SIMULATION / TEST ONLY)."""

    def __init__(self, sample):
        self._sample = sample

    def sample(self):
        return self._sample


class TestLocalizationEstimator(unittest.TestCase):
    def test_starts_without_estimate(self):
        est = LocalizationEstimator()
        self.assertFalse(est.has_estimate)
        self.assertIsNone(est.pose)
        self.assertIsNone(est.source)
        self.assertEqual(est.update_count, 0)

    def test_set_pose_records_pose_source_and_time(self):
        est = LocalizationEstimator()
        pose = Pose(1.0, 2.0, 0.5)
        est.set_pose(pose, source="manual", at=10.0)
        self.assertTrue(est.has_estimate)
        self.assertEqual(est.pose, pose)
        self.assertEqual(est.source, "manual")
        self.assertEqual(est.updated_at, 10.0)
        self.assertEqual(est.update_count, 1)

    def test_set_pose_rejects_invalid_pose(self):
        est = LocalizationEstimator()
        with self.assertRaises(ValueError):
            est.set_pose(Pose(float("nan"), 0.0, 0.0))  # type: ignore[arg-type]
        self.assertFalse(est.has_estimate)

    def test_invalidate_drops_estimate(self):
        est = LocalizationEstimator()
        est.set_pose(Pose(0.0, 0.0, 0.0), source="x", at=1.0)
        est.invalidate()
        self.assertFalse(est.has_estimate)
        self.assertIsNone(est.pose)
        self.assertIsNone(est.source)
        self.assertIsNone(est.updated_at)
        self.assertEqual(est.invalidation_count, 1)

    def test_age_and_staleness(self):
        est = LocalizationEstimator()
        self.assertIsNone(est.age(5.0))
        self.assertTrue(est.is_stale(5.0, max_age_s=1.0))  # no estimate = stale

        est.set_pose(Pose(0.0, 0.0, 0.0), source="x", at=10.0)
        self.assertAlmostEqual(est.age(12.0), 2.0)
        self.assertTrue(est.is_stale(12.0, max_age_s=1.0))
        self.assertFalse(est.is_stale(12.0, max_age_s=3.0))

    def test_poll_applies_attached_source_sample(self):
        est = LocalizationEstimator()
        self.assertIsNone(est.poll(0.0))  # nothing attached
        source = _FakePoseSource(PoseSample(Pose(2.0, 3.0, 1.0), source="test-source", at=0.0))
        est.attach(source)
        applied = est.poll(42.0)
        self.assertIsNotNone(applied)
        self.assertEqual(est.pose, Pose(2.0, 3.0, 1.0))
        self.assertEqual(est.source, "test-source")
        self.assertEqual(est.updated_at, 42.0)  # poll stamps caller clock

    def test_poll_ignores_source_without_estimate(self):
        est = LocalizationEstimator()
        est.attach(_FakePoseSource(None))
        self.assertIsNone(est.poll(0.0))
        self.assertFalse(est.has_estimate)

    def test_reset_clears_everything(self):
        est = LocalizationEstimator()
        est.set_pose(Pose(0.0, 0.0, 0.0), source="x", at=1.0)
        est.attach(_FakePoseSource(None))
        est.reset()
        self.assertFalse(est.has_estimate)
        self.assertIsNone(est.poll(1.0))  # source detached

    def test_pose_source_protocol_is_runtime_checkable(self):
        self.assertIsInstance(_FakePoseSource(None), PoseSource)


if __name__ == "__main__":
    unittest.main()