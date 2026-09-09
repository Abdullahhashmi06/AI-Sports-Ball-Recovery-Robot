"""Tests for the laptop-side robot state hub (integration layer, Person 1).

``RobotSystemState`` holds the *raw authoritative* picture of the robot from
the latest protocol ``TELE`` frame (DEC-020: the ESP32 is authoritative for
motor/encoder/collection/balls — the hub never interprets those values) and
composes the existing ``LocalizationEstimator`` for the best-known pose.

Pose is **never** derived from telemetry here: encoder-count → meters is
OD-12 and heading/IMU fusion is OD-13, so a pose only exists when an
external source supplies one (e.g. a future calibrated odometry/IMU source
or a test-only manual sample).  Telemetry fixtures are SIMULATION / TEST
ONLY.
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from navigation.localization.state import Pose, WorldPoint  # noqa: E402
from navigation.localization.telemetry import (  # noqa: E402
    TelemetryFormatError,
    from_telemetry,
)
from navigation.planning.interfaces import NavigationInput  # noqa: E402
from telemetry_fixtures import sample_telemetry  # noqa: E402

from integration.system.state import RobotSystemState  # noqa: E402


def _snapshot(**overrides):
    return from_telemetry(sample_telemetry(**overrides))


class TestInitialState(unittest.TestCase):
    def test_starts_empty(self):
        st = RobotSystemState()
        self.assertFalse(st.has_telemetry)
        self.assertIsNone(st.snapshot)
        self.assertIsNone(st.telemetry_at)
        self.assertEqual(st.telemetry_seq, 0)
        self.assertFalse(st.faults_known)
        self.assertEqual(st.faults, ())
        self.assertFalse(st.has_faults)
        self.assertFalse(st.has_pose)
        self.assertIsNone(st.pose)

    def test_telemetry_age_is_none_without_telemetry(self):
        st = RobotSystemState()
        self.assertIsNone(st.telemetry_age(123.0))
        self.assertFalse(st.telemetry_fresh(123.0, max_age_s=1.0))


class TestTelemetryHolding(unittest.TestCase):
    def test_set_telemetry_stores_raw_snapshot(self):
        st = RobotSystemState()
        snap = _snapshot(seq=7, enc_dl=3, enc_dr=4)
        st.set_telemetry(snap, at=100.0)
        self.assertTrue(st.has_telemetry)
        self.assertIs(st.snapshot, snap)
        self.assertEqual(st.telemetry_seq, 1)
        self.assertEqual(st.telemetry_at, 100.0)
        # Raw authoritative values pass through untouched (no meter
        # conversion — OD-12; no interpretation).
        self.assertEqual(st.snapshot.enc_dl, 3)

    def test_set_telemetry_requires_snapshot_type(self):
        st = RobotSystemState()
        with self.assertRaises(TypeError):
            st.set_telemetry({"enc": {"dl": 0, "dr": 0}}, at=0.0)  # type: ignore[arg-type]
        # And malformed telemetry never reaches the hub: the bridge rejects
        # it first (structure is protocol-required, §4.2).
        bad = sample_telemetry()
        del bad["enc"]
        with self.assertRaises(TelemetryFormatError):
            from_telemetry(bad)

    def test_new_telemetry_replaces_old_and_increments_seq(self):
        st = RobotSystemState()
        st.set_telemetry(_snapshot(seq=1), at=1.0)
        st.set_telemetry(_snapshot(seq=2), at=2.0)
        self.assertEqual(st.telemetry_seq, 2)
        self.assertEqual(st.snapshot.seq, 2)
        self.assertEqual(st.telemetry_at, 2.0)

    def test_invalidate_telemetry_drops_snapshot(self):
        st = RobotSystemState()
        st.set_telemetry(_snapshot(), at=1.0)
        st.invalidate_telemetry()
        self.assertFalse(st.has_telemetry)
        self.assertIsNone(st.snapshot)
        self.assertFalse(st.faults_known)

    def test_freshness_uses_caller_supplied_clock_and_budget(self):
        st = RobotSystemState()
        st.set_telemetry(_snapshot(), at=100.0)
        # age = now - at; stale when age > max_age_s
        self.assertAlmostEqual(st.telemetry_age(110.0), 10.0)
        self.assertTrue(st.telemetry_fresh(110.0, max_age_s=10.0))   # == budget -> fresh
        self.assertFalse(st.telemetry_fresh(110.0, max_age_s=9.9))   # older -> stale

    def test_no_clock_recorded_means_conservatively_stale(self):
        # set_telemetry without an `at` gives no basis for freshness; the
        # hub must not pretend the data is fresh.
        st = RobotSystemState()
        st.set_telemetry(_snapshot())
        self.assertIsNone(st.telemetry_age(5.0))
        self.assertFalse(st.telemetry_fresh(5.0, max_age_s=1.0))

    def test_telemetry_seq_tracks_snapshot_wire_seq(self):
        st = RobotSystemState()
        st.set_telemetry(_snapshot(seq=44050), at=1.0)
        self.assertEqual(st.telemetry_seq, 1)  # count of frames, not wire seq


class TestFaults(unittest.TestCase):
    def test_faults_verbatim_authoritative(self):
        st = RobotSystemState()
        self.assertFalse(st.faults_known)  # no telemetry -> unknown, not "no faults"
        st.set_telemetry(_snapshot(faults=(1, 6)), at=1.0)
        self.assertTrue(st.faults_known)
        self.assertEqual(st.faults, (1, 6))
        self.assertTrue(st.has_faults)

    def test_no_faults_reported(self):
        st = RobotSystemState()
        st.set_telemetry(_snapshot(), at=1.0)
        self.assertTrue(st.faults_known)
        self.assertEqual(st.faults, ())
        self.assertFalse(st.has_faults)

    def test_hub_does_not_classify_codes(self):
        # Which codes are latched vs. informational is protocol/firmware
        # semantics (§7).  The hub stores them verbatim and never invents a
        # classification.
        st = RobotSystemState()
        st.set_telemetry(_snapshot(faults=(7, 9)), at=1.0)
        self.assertEqual(st.faults, (7, 9))
        self.assertFalse(hasattr(st, "latched_codes"))


class TestPose(unittest.TestCase):
    def test_pose_never_derived_from_telemetry(self):
        # OD-12/OD-13: raw encoders/IMU cannot produce a pose in this code.
        st = RobotSystemState()
        st.set_telemetry(_snapshot(enc_dl=50, enc_dr=48), at=1.0)
        self.assertFalse(st.has_pose)
        self.assertIsNone(st.pose)

    def test_external_pose_sample_adopted_with_provenance(self):
        st = RobotSystemState()
        st.set_pose(Pose(1.0, 2.0, 0.5), source="test_manual", at=10.0)
        self.assertTrue(st.has_pose)
        self.assertEqual(st.pose, Pose(1.0, 2.0, 0.5))
        self.assertEqual(st.localization.source, "test_manual")

    def test_invalid_pose_rejected(self):
        st = RobotSystemState()
        with self.assertRaises(ValueError):
            st.set_pose(Pose(float("nan"), 0.0, 0.0), source="test_manual")  # type: ignore[arg-type]

    def test_localization_container_is_the_same_object(self):
        # Future calibrated PoseSources attach to the estimator the state
        # composes; nothing is duplicated.
        st = RobotSystemState()
        est = st.localization
        est.set_pose(Pose(0.0, 0.0, 0.0), source="attached", at=1.0)
        self.assertTrue(st.has_pose)


class TestNavigationInputBuilder(unittest.TestCase):
    def setUp(self):
        self.st = RobotSystemState()

    def test_empty_input_when_unlocated_and_objective_given(self):
        nav = self.st.navigation_input(target=WorldPoint(5.0, 5.0))
        self.assertIsInstance(nav, NavigationInput)
        self.assertIsNone(nav.pose)  # planner must answer NO_POSE, not guess
        self.assertEqual(nav.target, WorldPoint(5.0, 5.0))

    def test_pose_flows_from_localization(self):
        self.st.set_pose(Pose(1.0, 2.0, 0.0), source="test_manual", at=1.0)
        nav = self.st.navigation_input(target=WorldPoint(4.0, 6.0))
        self.assertEqual(nav.pose, Pose(1.0, 2.0, 0.0))

    def test_hint_and_threshold_pass_through(self):
        from navigation.planning.interfaces import ExitHint
        from navigation.planning.zones import ZoneId

        hint = ExitHint(ZoneId.WEST, WorldPoint(-3.0, 0.0), 0.9)
        nav = self.st.navigation_input(
            hint=hint, useful_confidence_threshold=0.5  # SIMULATION/TEST ONLY value
        )
        self.assertIs(nav.hint, hint)
        self.assertEqual(nav.useful_confidence_threshold, 0.5)

    def test_invalid_target_rejected(self):
        with self.assertRaises(Exception):
            self.st.navigation_input(target=(1.0, 2.0))  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
