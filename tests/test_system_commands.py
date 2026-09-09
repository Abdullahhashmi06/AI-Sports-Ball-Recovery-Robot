"""Tests for the command boundary (movement intent → protocol command).

The boundary is a *pure decision function*: a navigation ``MovementIntent``
is mapped onto a protocol command that ``LaptopLink`` could send — or onto
an explicit deferred outcome where an open decision blocks a wire value.

Critical invariants verified here:

* ``MovementIntent`` never becomes ``lin``/``ang`` values — CMD_MOVE
  requires them, and OD-07 (speed calibration) + OD-13 (heading
  convention) are open, so a MOVE intent is *deferred*, never fabricated;
* ``STOP`` is fully expressible today and validates against the protocol
  schema (no duplicated schema — validation reuses
  ``integration.communication.protocol``);
* emergency ``CMD_STOP`` (safety layer fail-safe, R-3 / DEC-021) is a
  separate, always-available directive.
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from integration.communication.protocol import (  # noqa: E402
    complete_command_payload,
    validate_command,
)
from integration.system.commands import (  # noqa: E402
    CommandDirective,
    DirectiveKind,
    emergency_stop_directive,
    resolve_movement_command,
)
from navigation.planning.interfaces import MovementIntent, MovementKind  # noqa: E402


class TestNone(unittest.TestCase):
    def test_none_intent_means_no_command(self):
        d = resolve_movement_command(MovementIntent(MovementKind.NONE))
        self.assertEqual(d.kind, DirectiveKind.NONE)
        self.assertIsNone(d.message_type)
        self.assertIsNone(d.fields)


class TestStop(unittest.TestCase):
    def test_stop_maps_to_validated_normal_stop(self):
        d = resolve_movement_command(MovementIntent(MovementKind.STOP))
        self.assertEqual(d.kind, DirectiveKind.SEND)
        self.assertEqual(d.message_type, "CMD_STOP")
        self.assertEqual(d.fields, {"mode": "normal"})
        # The directive must satisfy the protocol schema (mode enum).
        validate_command("CMD_STOP", complete_command_payload("CMD_STOP", d.fields))

    def test_stop_reason_is_explanatory(self):
        d = resolve_movement_command(MovementIntent(MovementKind.STOP))
        self.assertIn("CMD_STOP", d.reason)


class TestMove(unittest.TestCase):
    def test_move_is_deferred_not_fabricated(self):
        # CMD_MOVE requires lin/ang (protocol §5); OD-07/OD-13 are open, so
        # the boundary must NOT invent values.  It defers with a reason.
        intent = MovementIntent(MovementKind.MOVE, bearing_world=0.5, distance_m=3.0)
        d = resolve_movement_command(intent)
        self.assertEqual(d.kind, DirectiveKind.DEFERRED)
        self.assertEqual(d.message_type, "CMD_MOVE")
        self.assertIsNone(d.fields)  # no lin/ang, no made-up payload
        self.assertFalse(hasattr(d, "lin"))
        self.assertFalse(hasattr(d, "ang"))
        self.assertIn("OD-07", d.reason)
        self.assertIn("OD-13", d.reason)

    def test_move_geometry_is_information_not_command(self):
        # The world geometry on the intent is context for the future motion
        # generator; it must not be smuggled into a wire payload.
        intent = MovementIntent(MovementKind.MOVE, bearing_world=-1.2, distance_m=8.5)
        d = resolve_movement_command(intent)
        self.assertEqual(d.kind, DirectiveKind.DEFERRED)
        self.assertIsNone(d.fields)


class TestEmergency(unittest.TestCase):
    def test_emergency_stop_is_sendable_and_validated(self):
        d = emergency_stop_directive()
        self.assertIsInstance(d, CommandDirective)
        self.assertEqual(d.kind, DirectiveKind.SEND)
        self.assertEqual(d.message_type, "CMD_STOP")
        self.assertEqual(d.fields, {"mode": "emergency"})
        validate_command("CMD_STOP", complete_command_payload("CMD_STOP", d.fields))
        self.assertIn("emergency", d.reason)

    def test_emergency_is_distinct_from_normal_stop(self):
        normal = resolve_movement_command(MovementIntent(MovementKind.STOP))
        emergency = emergency_stop_directive()
        self.assertNotEqual(normal.fields, emergency.fields)


class TestDeterminismAndTypes(unittest.TestCase):
    def test_deterministic(self):
        intent = MovementIntent(MovementKind.STOP)
        self.assertEqual(resolve_movement_command(intent), resolve_movement_command(intent))
        intent2 = MovementIntent(MovementKind.MOVE, bearing_world=0.1, distance_m=1.0)
        self.assertEqual(resolve_movement_command(intent2), resolve_movement_command(intent2))

    def test_directive_is_immutable(self):
        d = resolve_movement_command(MovementIntent(MovementKind.STOP))
        with self.assertRaises(Exception):
            d.fields = {"mode": "emergency"}  # type: ignore[misc]


if __name__ == "__main__":
    unittest.main()
