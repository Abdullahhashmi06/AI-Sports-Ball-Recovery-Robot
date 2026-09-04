"""Tests for the raw telemetry bridge (TELE dict → TelemetrySnapshot).

Verifies that protocol §4.2 telemetry is consumed as **raw, authoritative
data** (DEC-020): encoder counts are never converted to meters (OD-12),
IMU values stay raw (OD-13), ultrasonic value semantics (valid / no echo /
fault) are exposed, and structurally invalid frames raise
``TelemetryFormatError`` instead of being silently coerced.

Telemetry fixtures come from ``telemetry_fixtures.py`` — SIMULATION / TEST
ONLY synthetic data, never real measurements.
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from navigation.localization.telemetry import (  # noqa: E402
    TelemetryFormatError,
    TelemetrySnapshot,
    from_telemetry,
)
from telemetry_fixtures import sample_telemetry  # noqa: E402


class TestFromTelemetry(unittest.TestCase):
    def test_representative_frame_parses(self):
        snap = from_telemetry(sample_telemetry())
        self.assertIsInstance(snap, TelemetrySnapshot)
        self.assertEqual(snap.seq, 88)
        self.assertEqual(snap.ts, 44050)

    def test_encoder_values_are_raw_counts_not_meters(self):
        # OD-12: counts → meters is gated.  The snapshot must carry the raw
        # values and expose no meter conversion whatsoever.
        snap = from_telemetry(sample_telemetry(enc_dl=14, enc_dr=13))
        self.assertEqual(snap.enc_dl, 14)
        self.assertEqual(snap.enc_dr, 13)
        self.assertFalse(hasattr(snap, "meters"))
        self.assertFalse(hasattr(snap.enc_dl, "meters"))

    def test_ultrasonic_values_raw_with_protocol_semantics(self):
        # [front_left, front_right, left, right] with 0-4000 valid / 9999
        # no echo / -1 fault (protocol §4.2).
        snap = from_telemetry(sample_telemetry(us=(350, 340, 9999, -1)))
        self.assertEqual(snap.us, (350, 340, 9999, -1))
        self.assertEqual(snap.us_status(0), "valid")
        self.assertEqual(snap.us_status(1), "valid")
        self.assertEqual(snap.us_status(2), "no_echo")
        self.assertEqual(snap.us_status(3), "fault")
        self.assertTrue(snap.has_us_fault)
        self.assertFalse(from_telemetry(sample_telemetry()).has_us_fault)

    def test_imu_raw_and_yaw_absent_by_default(self):
        # OD-13: yaw is optional and only appears if the ESP32 later
        # populates it — the default snapshot has no heading.
        snap = from_telemetry(sample_telemetry())
        self.assertEqual((snap.imu.ax, snap.imu.ay, snap.imu.az), (0.02, 0.01, 0.99))
        self.assertEqual((snap.imu.gx, snap.imu.gy, snap.imu.gz), (0.5, -0.2, 1.4))
        self.assertIsNone(snap.imu.yaw)

    def test_imu_yaw_present_when_sent(self):
        snap = from_telemetry(sample_telemetry(include_yaw=True))
        self.assertEqual(snap.imu.yaw, 12.5)

    def test_motor_collect_balls_faults(self):
        snap = from_telemetry(sample_telemetry(
            pwm_l=45, pwm_r=44, spd_l=44.2, spd_r=43.8,
            trip=True, collect_state="running",
            balls_count=4, balls_full=False,
            faults=(1, 2),
        ))
        self.assertEqual((snap.mot.pwm_l, snap.mot.pwm_r), (45, 44))
        self.assertEqual((snap.mot.spd_l, snap.mot.spd_r), (44.2, 43.8))
        self.assertTrue(snap.collect_trip)
        self.assertEqual(snap.collect_state, "running")
        self.assertEqual(snap.balls_count, 4)
        self.assertFalse(snap.balls_full)
        self.assertEqual(snap.faults, (1, 2))

    def test_empty_faults_list(self):
        snap = from_telemetry(sample_telemetry())
        self.assertEqual(snap.faults, ())

    def test_missing_required_section_raises(self):
        msg = sample_telemetry()
        del msg["enc"]
        with self.assertRaises(TelemetryFormatError):
            from_telemetry(msg)

    def test_bad_encoder_type_raises(self):
        msg = sample_telemetry()
        msg["enc"] = {"dl": 1.5, "dr": 0}  # protocol says ints
        with self.assertRaises(TelemetryFormatError):
            from_telemetry(msg)

    def test_us_wrong_length_raises(self):
        msg = sample_telemetry(us=(350, 340, 9999))  # only 3 slots
        with self.assertRaises(TelemetryFormatError):
            from_telemetry(msg)

    def test_us_non_int_raises(self):
        msg = sample_telemetry(us=(350.5, 340, 9999, 410))
        with self.assertRaises(TelemetryFormatError):
            from_telemetry(msg)

    def test_missing_imu_field_raises(self):
        msg = sample_telemetry()
        del msg["imu"]["az"]
        with self.assertRaises(TelemetryFormatError):
            from_telemetry(msg)

    def test_faults_non_int_raises(self):
        msg = sample_telemetry(faults=(1, "x"))
        with self.assertRaises(TelemetryFormatError):
            from_telemetry(msg)

    def test_collect_state_must_be_string(self):
        msg = sample_telemetry()
        msg["collect"]["state"] = 7
        with self.assertRaises(TelemetryFormatError):
            from_telemetry(msg)

    def test_missing_seq_raises(self):
        msg = sample_telemetry()
        del msg["seq"]
        with self.assertRaises(TelemetryFormatError):
            from_telemetry(msg)

    def test_non_mapping_input_raises(self):
        with self.assertRaises(TelemetryFormatError):
            from_telemetry(["not", "a", "dict"])  # type: ignore[arg-type]

    def test_snapshot_is_immutable(self):
        snap = from_telemetry(sample_telemetry())
        with self.assertRaises(Exception):
            snap.enc_dl = 99  # type: ignore[misc]
        with self.assertRaises(Exception):
            snap.us[0] = 1  # tuples are immutable too

    def test_separation_fixtures_are_test_only(self):
        # The bridge consumes any protocol-shaped dict; the SIMULATION/TEST
        # ONLY fixtures live in the test tree and are not part of the
        # navigation package (no import from navigation.* can reach them).
        import telemetry_fixtures

        self.assertTrue(telemetry_fixtures.__doc__.startswith("SIMULATION / TEST ONLY"))
        self.assertFalse(
            hasattr(sys.modules["navigation.localization.telemetry"], "sample_telemetry")
        )


if __name__ == "__main__":
    unittest.main()