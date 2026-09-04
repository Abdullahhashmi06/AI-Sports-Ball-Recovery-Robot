"""Tests for recovery-zone vocabulary, configuration, and search focus.

Covers ``TEAM_INTERFACE_CONTRACT.md`` §6.2–§6.4 (DEC-016 … DEC-019): the
exact four zone names, configurable (never hard-coded) physical extents,
label-based selection, config-gated position lookup, and the §6.4
confidence classification with an explicit calibration threshold.
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from navigation.localization.state import WorldPoint  # noqa: E402
from navigation.planning.zones import (  # noqa: E402
    OperatingArea,
    Rectangle,
    SearchFocus,
    ZoneConfig,
    ZoneId,
    confidence_is_useful,
    lookup_zone_at,
    search_focus,
    select_zone,
)


class TestZoneId(unittest.TestCase):
    def test_vocabulary_is_exactly_four_agreed_zones(self):
        # DEC-018: WEST, EAST, NORTH, SOUTH — nothing else.
        self.assertEqual(
            {z.value for z in ZoneId},
            {"WEST", "EAST", "NORTH", "SOUTH"},
        )

    def test_str_is_the_interface_vocabulary(self):
        self.assertEqual(str(ZoneId.WEST), "WEST")
        self.assertEqual(str(ZoneId.SOUTH), "SOUTH")


class TestRectangle(unittest.TestCase):
    def test_validation(self):
        Rectangle(0.0, 1.0, -5.0, 5.0)  # valid, negative min fine (DEC-014)
        with self.assertRaises(ValueError):
            Rectangle(2.0, 1.0, 0.0, 1.0)  # x_min > x_max
        with self.assertRaises(ValueError):
            Rectangle(0.0, 1.0, 3.0, 2.0)  # y_min > y_max
        with self.assertRaises(ValueError):
            Rectangle(float("nan"), 1.0, 0.0, 1.0)

    def test_contains_is_inclusive(self):
        r = Rectangle(0.0, 10.0, 0.0, 10.0)
        self.assertTrue(r.contains(WorldPoint(5.0, 5.0)))
        self.assertTrue(r.contains(WorldPoint(0.0, 10.0)))  # boundary counts
        self.assertFalse(r.contains(WorldPoint(-0.01, 5.0)))
        self.assertFalse(r.contains(WorldPoint(5.0, 10.01)))


class TestZoneConfig(unittest.TestCase):
    def test_zone_without_bounds_is_unconfigured(self):
        cfg = ZoneConfig(ZoneId.WEST)
        self.assertFalse(cfg.configured)
        self.assertIsNone(cfg.bounds)

    def test_zone_with_bounds_is_configured(self):
        cfg = ZoneConfig(ZoneId.WEST, Rectangle(0.0, 10.0, 0.0, 10.0))
        self.assertTrue(cfg.configured)


class TestOperatingArea(unittest.TestCase):
    def test_unconfigured_by_default_no_invented_boundary(self):
        # DEC-015/DEC-016: no world boundary is part of the convention; the
        # area is venue configuration and starts unset.
        area = OperatingArea()
        self.assertFalse(area.configured)
        self.assertFalse(area.contains(WorldPoint(0.0, 0.0)))

    def test_configured_area_contains(self):
        area = OperatingArea(Rectangle(-20.0, 30.0, -20.0, 20.0))
        self.assertTrue(area.configured)
        self.assertTrue(area.contains(WorldPoint(0.0, 0.0)))
        self.assertFalse(area.contains(WorldPoint(100.0, 0.0)))


class TestSelection(unittest.TestCase):
    def test_select_zone_by_label(self):
        # The AI's exit_zone names the relevant zone directly (I-1).
        self.assertIs(select_zone(ZoneId.NORTH), ZoneId.NORTH)

    def test_lookup_zone_at_requires_configuration(self):
        point = WorldPoint(25.0, 5.0)
        self.assertIsNone(lookup_zone_at(point, []))  # no config at all
        # Even a config list without matching bounds returns None.
        cfg = [ZoneConfig(ZoneId.WEST, Rectangle(0.0, 10.0, 0.0, 10.0))]
        self.assertIsNone(lookup_zone_at(WorldPoint(50.0, 50.0), cfg))

    def test_lookup_zone_at_matches_configured_bounds(self):
        cfg = [
            ZoneConfig(ZoneId.WEST, Rectangle(-20.0, 0.0, -10.0, 10.0)),
            ZoneConfig(ZoneId.NORTH, Rectangle(0.0, 20.0, 10.0, 20.0)),
        ]
        self.assertIs(lookup_zone_at(WorldPoint(-5.0, 0.0), cfg), ZoneId.WEST)
        self.assertIs(lookup_zone_at(WorldPoint(10.0, 15.0), cfg), ZoneId.NORTH)
        self.assertIsNone(lookup_zone_at(WorldPoint(10.0, -15.0), cfg))


class TestSearchFocus(unittest.TestCase):
    def test_threshold_is_a_calibration_parameter(self):
        # §6.4: the useful-vs-low threshold is calibrated in field testing,
        # so no default exists — None means undecided, never guessed.
        self.assertFalse(confidence_is_useful(0.8, None))
        self.assertTrue(confidence_is_useful(0.8, 0.5))
        self.assertFalse(confidence_is_useful(0.3, 0.5))

    def test_search_focus_mapping(self):
        # No estimated position -> systematic (nothing to prioritize).
        self.assertIs(search_focus(False, 0.9, 0.5), SearchFocus.SYSTEMATIC)
        # Threshold unset -> undecided.
        self.assertIs(search_focus(True, 0.9, None), SearchFocus.UNDECIDED)
        # Useful confidence -> around the estimate.
        self.assertIs(search_focus(True, 0.9, 0.5), SearchFocus.AROUND_ESTIMATE)
        # Low confidence -> broader/systematic.
        self.assertIs(search_focus(True, 0.1, 0.5), SearchFocus.SYSTEMATIC)
        # Boundary: >= threshold counts as useful.
        self.assertIs(search_focus(True, 0.5, 0.5), SearchFocus.AROUND_ESTIMATE)


if __name__ == "__main__":
    unittest.main()