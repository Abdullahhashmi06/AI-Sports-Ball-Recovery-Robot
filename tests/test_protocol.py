"""Unit tests for the pure protocol helpers (integration/communication/protocol.py).

Run from the repository root with the standard library only:

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from integration.communication.protocol import (  # noqa: E402
    COMMAND_SCHEMAS,
    ERROR_CODES,
    MAX_LINE_BYTES,
    PROTOCOL_VERSION,
    SequenceCounter,
    ValidationError,
    complete_command_payload,
    decode_line,
    encode_line,
    inbound_shape_ok,
    make_message,
    validate_command,
)


class TestMessageBuild(unittest.TestCase):
    def test_header_fields_and_order(self):
        msg = make_message("HEARTBEAT", 7)
        self.assertEqual(list(msg), ["v", "type", "seq"])
        self.assertEqual(msg["v"], PROTOCOL_VERSION)
        self.assertEqual(msg["type"], "HEARTBEAT")
        self.assertEqual(msg["seq"], 7)

    def test_payload_after_header_and_optional_ts(self):
        msg = make_message("CMD_MOVE", 3, {"lin": 0.5, "ang": -0.25}, ts=100)
        self.assertEqual(list(msg), ["v", "type", "seq", "ts", "lin", "ang"])
        self.assertEqual(msg["ts"], 100)

    def test_seq_out_of_range_rejected(self):
        with self.assertRaises(ValidationError):
            make_message("HEARTBEAT", 70000)

    def test_sequence_counter_wraps_at_65535(self):
        c = SequenceCounter(65535)
        self.assertEqual(c.next(), 65535)
        self.assertEqual(c.next(), 0)
        self.assertEqual(c.next(), 1)

    def test_encode_line_single_line_and_roundtrip(self):
        msg = make_message("CMD_RESET", 1, {"scope": "all"}, ts=5)
        line = encode_line(msg)
        self.assertNotIn("\n", line)
        self.assertNotIn("\r", line)
        kind, parsed = decode_line(line)
        self.assertEqual(kind, "ok")
        self.assertEqual(parsed, msg)

    def test_encode_line_size_limit(self):
        msg = make_message("HEARTBEAT", 1, {"pad": "x" * 600})
        with self.assertRaises(ValidationError):
            encode_line(msg)


class TestValidation(unittest.TestCase):
    def _ok(self, mtype, payload):
        validate_command(mtype, payload)  # must not raise

    def _err(self, mtype, payload, code):
        with self.assertRaises(ValidationError) as ctx:
            validate_command(mtype, payload)
        self.assertEqual(ctx.exception.code, code, ctx.exception)

    def test_valid_commands(self):
        self._ok("CMD_MOVE", {"lin": 1.0, "ang": -1.0})
        self._ok("CMD_MOVE", {"lin": 0, "ang": 0})
        self._ok("CMD_STOP", {"mode": "normal"})
        self._ok("CMD_STOP", {"mode": "emergency"})
        self._ok("CMD_INTAKE", {"action": "start"})
        self._ok("CMD_DISPENSE", {"count": 1})
        self._ok("CMD_RESET", {"scope": "all"})

    def test_range_violations(self):
        self._err("CMD_MOVE", {"lin": 1.5, "ang": 0.0}, "RANGE")
        self._err("CMD_MOVE", {"lin": 0.0, "ang": -1.01}, "RANGE")
        self._err("CMD_DISPENSE", {"count": 0}, "RANGE")
        self._err("CMD_DISPENSE", {"count": 11}, "RANGE")

    def test_missing_required_fields(self):
        self._err("CMD_MOVE", {"lin": 0.5}, "PARSE")
        self._err("CMD_INTAKE", {}, "PARSE")
        self._err("CMD_RESET", {}, "PARSE")

    def test_wrong_types(self):
        self._err("CMD_MOVE", {"lin": "fast", "ang": 0}, "PARSE")
        self._err("CMD_MOVE", {"lin": True, "ang": 0}, "PARSE")
        self._err("CMD_DISPENSE", {"count": 1.5}, "PARSE")
        self._err("CMD_STOP", {"mode": 3}, "RANGE")      # wrong type + not enum

    def test_bad_enum_value(self):
        self._err("CMD_STOP", {"mode": "panic"}, "RANGE")
        self._err("CMD_RESET", {"scope": "everything"}, "RANGE")

    def test_stop_mode_defaults_to_normal(self):
        self.assertEqual(
            complete_command_payload("CMD_STOP", {})["mode"], "normal")
        self.assertEqual(
            complete_command_payload("CMD_STOP", {"mode": "emergency"})["mode"],
            "emergency")

    def test_unknown_command_type(self):
        with self.assertRaises(ValidationError) as ctx:
            validate_command("CMD_FLY", {})
        self.assertEqual(ctx.exception.code, "UNKNOWN_TYPE")

    def test_unknown_extra_fields_tolerated(self):
        # Additive tolerance: extra fields must not break validation.
        self._ok("CMD_MOVE", {"lin": 0.2, "ang": 0.0, "future_field": 1})

    def test_schema_matches_protocol_catalog(self):
        for mtype in ("CMD_MOVE", "CMD_STOP", "CMD_INTAKE",
                      "CMD_DISPENSE", "CMD_RESET"):
            self.assertIn(mtype, COMMAND_SCHEMAS)
        for code in ("PARSE", "UNKNOWN_TYPE", "RANGE", "VERSION",
                     "NOT_READY", "BUSY", "INTERNAL"):
            self.assertIn(code, ERROR_CODES)


class TestDecode(unittest.TestCase):
    def test_malformed_non_json(self):
        kind, msg = decode_line("this is not json {{{")
        self.assertEqual(kind, "malformed")
        self.assertIsNone(msg)

    def test_malformed_wrong_shape(self):
        for line in ("[1, 2, 3]", '"just a string"', "42"):
            kind, _ = decode_line(line)
            self.assertEqual(kind, "malformed", line)

    def test_malformed_missing_header_fields(self):
        for text in (
            '{"v":1,"seq":3}',                    # no type
            '{"v":1,"type":"HEARTBEAT"}',         # no seq
            '{"v":1,"type":5,"seq":3}',           # type not a string
            '{"v":1,"type":"HEARTBEAT","seq":70000}',  # seq out of range
            '{"v":"one","type":"HEARTBEAT","seq":1}',  # v not an int
        ):
            kind, _ = decode_line(text)
            self.assertEqual(kind, "malformed", text)

    def test_incompatible_version(self):
        kind, msg = decode_line('{"v":2,"type":"HEARTBEAT","seq":4}')
        self.assertEqual(kind, "incompatible")
        self.assertEqual(msg["v"], 2)

    def test_ok_message(self):
        line = encode_line(make_message("TELE", 9, {"a": 1}))
        kind, msg = decode_line(line)
        self.assertEqual(kind, "ok")
        self.assertEqual(msg["type"], "TELE")
        self.assertEqual(msg["seq"], 9)

    def test_crlf_and_whitespace_tolerated(self):
        kind, msg = decode_line('{"v":1,"type":"HEARTBEAT","seq":1}\r')
        self.assertEqual(kind, "ok")
        self.assertEqual(msg["seq"], 1)

    def test_empty_line_is_malformed_not_crash(self):
        kind, _ = decode_line("")
        self.assertEqual(kind, "malformed")

    def test_oversized_frame_is_malformed(self):
        # Valid JSON with a valid header but > 512 bytes: must be rejected
        # by the length guard (protocol §2) before parsing.
        line = '{"v":1,"type":"HEARTBEAT","seq":1,"pad":"' + "x" * 600 + '"}'
        self.assertGreater(len(line.encode("utf-8")), MAX_LINE_BYTES)
        kind, msg = decode_line(line)
        self.assertEqual(kind, "malformed")
        self.assertIsNone(msg)


class TestInboundShape(unittest.TestCase):
    """Structural guard for inbound ESP32 messages (protocol §4)."""

    def _tele(self, **overrides):
        msg = {
            "v": 1, "type": "TELE", "seq": 1,
            "enc": {"dl": 0, "dr": 0},
            "us": [9999, 9999, 9999, 9999],
            "imu": {"ax": 0.0, "ay": 0.0, "az": 1.0,
                    "gx": 0.0, "gy": 0.0, "gz": 0.0},
            "mot": {"pwm_l": 0, "pwm_r": 0, "spd_l": 0.0, "spd_r": 0.0},
            "collect": {"trip": False, "state": "idle"},
            "balls": {"count": 0, "full": False},
            "faults": [],
        }
        msg.update(overrides)
        return msg

    def test_valid_tele_passes(self):
        self.assertTrue(inbound_shape_ok(self._tele()))

    def test_tele_missing_required_parts_fail(self):
        self.assertFalse(inbound_shape_ok(self._tele(imu={})))          # empty imu
        self.assertFalse(inbound_shape_ok(self._tele(us=[1, 2, 3])))    # wrong len
        self.assertFalse(inbound_shape_ok(self._tele(enc={"dl": 0})))  # no dr
        self.assertFalse(inbound_shape_ok(self._tele(faults="oops")))
        self.assertFalse(inbound_shape_ok({**self._tele(), "us": None}))

    def test_event_and_response_shapes(self):
        balls = {"count": 1, "full": False}
        self.assertTrue(inbound_shape_ok(
            {"v": 1, "type": "EVT_COLLECT", "status": "collected", "balls": balls}))
        self.assertFalse(inbound_shape_ok(
            {"v": 1, "type": "EVT_COLLECT", "status": "collected"}))  # no balls
        self.assertFalse(inbound_shape_ok(
            {"v": 1, "type": "EVT_FAULT"}))                             # no code
        self.assertTrue(inbound_shape_ok(
            {"v": 1, "type": "EVT_FAULT", "code": 3}))
        self.assertFalse(inbound_shape_ok(
            {"v": 1, "type": "RESP_OK"}))                              # no ack
        self.assertTrue(inbound_shape_ok(
            {"v": 1, "type": "RESP_OK", "ack": 5}))
        self.assertFalse(inbound_shape_ok(
            {"v": 1, "type": "RESP_ERR", "ack": 5}))                  # no code
        self.assertTrue(inbound_shape_ok(
            {"v": 1, "type": "RESP_ERR", "ack": 5, "code": "BUSY"}))

    def test_heartbeat_and_unknown_types_pass(self):
        # No payload contract: never dropped by the structural guard.
        self.assertTrue(inbound_shape_ok({"v": 1, "type": "HEARTBEAT", "seq": 1}))
        self.assertTrue(inbound_shape_ok({"v": 1, "type": "MYSTERY", "seq": 1}))


class TestGoldenWireBytes(unittest.TestCase):
    """Byte-locked laptop -> ESP32 encodings (protocol §4 examples).

    Cross-language conformance with the C++ engine (firmware/lib/protocol)
    is still a bench step — the golden vectors here lock the Python side; the
    C++ parser must accept exactly these bytes (see TODO in
    integration/communication/README.md).
    """

    def test_golden_encodings(self):
        cases = [
            (make_message("HEARTBEAT", 142, ts=7100),
             '{"v":1,"type":"HEARTBEAT","seq":142,"ts":7100}'),
            (make_message("CMD_MOVE", 150, {"lin": 0.6, "ang": 0.0}),
             '{"v":1,"type":"CMD_MOVE","seq":150,"lin":0.6,"ang":0.0}'),
            (make_message("CMD_STOP", 160, {"mode": "emergency"}),
             '{"v":1,"type":"CMD_STOP","seq":160,"mode":"emergency"}'),
            (make_message("CMD_RESET", 190, {"scope": "all"}),
             '{"v":1,"type":"CMD_RESET","seq":190,"scope":"all"}'),
        ]
        for msg, expected in cases:
            self.assertEqual(encode_line(msg), expected)


if __name__ == "__main__":
    unittest.main()
