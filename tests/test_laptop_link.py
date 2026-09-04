"""Integration tests for the laptop link layer against the fake ESP32.

Runs entirely in-process over in-memory transports (no serial port needed).
Covers: startup (EVT_BOOT + TELE), acknowledged commands, NOT_READY gating,
schema validation before transmission, heartbeat traffic, e-stop latch +
CMD_RESET re-arm semantics, retransmission, laptop-side liveness loss/
restore, protocol-version mismatch, and malformed-line handling.

Run from the repository root:

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from integration.communication.laptop_link import (  # noqa: E402
    CommandError,
    Esp32Lost,
    LaptopLink,
    LinkTimeout,
)
from integration.communication.protocol import (  # noqa: E402
    ValidationError,
    encode_line,
    make_message,
)
from integration.communication.transport import create_memory_pair  # noqa: E402

from fake_esp32 import FakeEsp32  # noqa: E402


def _wait_until(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


class LaptopLinkTestBase(unittest.TestCase):
    """Sets up a laptop <-> fake-ESP32 pair over an in-memory transport."""

    def make_link(self, **kwargs):
        laptop_end, esp_end = create_memory_pair()
        esp = FakeEsp32(esp_end)
        link = LaptopLink(
            laptop_end,
            hb_interval_s=0.05,
            command_timeout_s=0.5,
            command_retries=1,
            **kwargs,
        )
        return link, esp

    def setUp(self):
        self.link, self.esp = self.make_link()
        self.events = []
        self.link.on_event = lambda m: self.events.append(m)
        self.teles = []
        self.link.on_tele = lambda m: self.teles.append(m)
        self.esp.start()
        self.link.start()

    def tearDown(self):
        self.link.stop()
        self.esp.stop()


class TestStartup(LaptopLinkTestBase):
    def test_boot_event_then_telemetry(self):
        boot = self.link.wait_for_event("EVT_BOOT", timeout=2.0)
        self.assertIsNotNone(boot)
        self.assertEqual(boot["type"], "EVT_BOOT")
        self.assertEqual(boot["reason"], "poweron")
        self.assertEqual(boot["fw"], "fake-esp32-0.1")

        tele = self.link.wait_tele(timeout=2.0, after=0)
        self.assertIsNotNone(tele)
        self.assertEqual(tele["type"], "TELE")

    def test_telemetry_structure_and_callback(self):
        tele = self.link.wait_tele(timeout=2.0, after=0)
        self.assertIsNotNone(tele)
        for key in ("enc", "us", "imu", "mot", "collect", "balls", "faults"):
            self.assertIn(key, tele)
        self.assertEqual(len(tele["us"]), 4)
        self.assertIn("dl", tele["enc"])
        self.assertIn("dr", tele["enc"])
        self.assertIn("pwm_l", tele["mot"])
        self.assertIsInstance(tele["balls"]["count"], int)
        # on_tele callback receives the same frames.
        self.assertTrue(_wait_until(lambda: len(self.teles) >= 1))
        self.assertIn("type", self.teles[0])

    def test_laptop_sends_heartbeats(self):
        self.assertTrue(_wait_until(lambda: self.esp.heartbeats_from_laptop >= 1,
                                    timeout=2.0))


class TestCommandFlow(LaptopLinkTestBase):
    def test_command_ok_returns_resp(self):
        self.esp.set_ready()
        resp = self.link.command("CMD_RESET", scope="all")
        self.assertEqual(resp["type"], "RESP_OK")
        self.assertIn("ack", resp)

    def test_not_ready_rejection_before_startup_checks(self):
        # The fake ESP32 has not finished startup checks yet.
        with self.assertRaises(CommandError) as ctx:
            self.link.command("CMD_MOVE", lin=0.1, ang=0.0)
        self.assertEqual(ctx.exception.code, "NOT_READY")
        self.assertEqual(len(self.esp.executed_commands), 0)

        self.esp.set_ready()
        resp = self.link.command("CMD_MOVE", lin=0.0, ang=0.0)
        self.assertEqual(resp["type"], "RESP_OK")
        self.assertEqual(len(self.esp.executed_commands), 1)

    def test_invalid_command_is_rejected_locally_never_sent(self):
        for kwargs in ({"lin": 1.5, "ang": 0.0},
                       {"lin": 0.0},                 # missing ang
                       {"lin": "fast", "ang": 0.0}):  # wrong type
            with self.assertRaises(ValidationError):
                self.link.command("CMD_MOVE", **kwargs)
        with self.assertRaises(ValidationError):
            self.link.command("CMD_RESET", scope="everything")
        self.assertEqual(len(self.esp.executed_commands), 0)
        self.assertEqual(self.esp.malformed_from_laptop, 0)

    def test_command_error_raises_command_error_with_code(self):
        # NOT ready -> the ESP32 answers RESP_ERR NOT_READY.
        with self.assertRaises(CommandError) as ctx:
            self.link.command("CMD_STOP", mode="normal")
        self.assertEqual(ctx.exception.code, "NOT_READY")
        self.assertEqual(ctx.exception.response["type"], "RESP_ERR")

    def test_retransmission_when_response_is_dropped(self):
        self.esp.set_ready()
        self.esp.drop_next_commands("CMD_MOVE")
        resp = self.link.command("CMD_MOVE", lin=0.2, ang=0.0, timeout=0.2)
        self.assertEqual(resp["type"], "RESP_OK")
        # The dropped first attempt must not have been executed.
        self.assertEqual(len(self.esp.executed_commands), 1)

    def test_timeout_after_all_retries(self):
        self.esp.set_ready()
        # Drop every CMD_RESET (original + 1 retransmission + margin): no
        # response ever arrives -> LinkTimeout after the retry budget.
        self.esp.drop_next_commands("CMD_RESET", count=3)
        with self.assertRaises(LinkTimeout):
            self.link.command("CMD_RESET", scope="all", timeout=0.15)


class TestSafetyLatches(LaptopLinkTestBase):
    def _estop(self):
        self.esp.set_ready()
        resp = self.link.command("CMD_STOP", mode="emergency")
        self.assertEqual(resp["type"], "RESP_OK")
        self.assertTrue(_wait_until(lambda: self.esp.latched))

    def test_emergency_stop_latches_and_requires_reset_all(self):
        self._estop()
        # Latch reported via EVT_FAULT code 1.
        self.assertTrue(_wait_until(lambda: 1 in self.esp.faults))
        # Motion now rejected with NOT_READY.
        with self.assertRaises(CommandError) as ctx:
            self.link.command("CMD_MOVE", lin=0.1, ang=0.0)
        self.assertEqual(ctx.exception.code, "NOT_READY")

        # scope=faults must NOT re-arm after an e-stop.
        resp = self.link.command("CMD_RESET", scope="faults")
        self.assertEqual(resp["type"], "RESP_OK")
        with self.assertRaises(CommandError) as ctx:
            self.link.command("CMD_MOVE", lin=0.1, ang=0.0)
        self.assertEqual(ctx.exception.code, "NOT_READY")

        # scope=all re-arms: RESP_OK then motion works again.
        resp = self.link.command("CMD_RESET", scope="all")
        self.assertEqual(resp["type"], "RESP_OK")
        resp = self.link.command("CMD_MOVE", lin=0.1, ang=0.0)
        self.assertEqual(resp["type"], "RESP_OK")

    def test_returning_heartbeat_does_not_clear_latch(self):
        self._estop()
        # Several laptop heartbeats arrive at the latched ESP32...
        for _ in range(3):
            self.link.send_message("HEARTBEAT")
            time.sleep(0.02)
        # ...but the latch stays: motion is still rejected.
        self.assertTrue(self.esp.latched)
        with self.assertRaises(CommandError) as ctx:
            self.link.command("CMD_MOVE", lin=0.0, ang=0.0)
        self.assertEqual(ctx.exception.code, "NOT_READY")
        # Only CMD_RESET scope=all re-arms.
        self.link.command("CMD_RESET", scope="all")
        resp = self.link.command("CMD_MOVE", lin=0.0, ang=0.0)
        self.assertEqual(resp["type"], "RESP_OK")

    def test_malformed_line_is_counted_and_ignored(self):
        self.esp.set_ready()
        self.esp.send_raw("this is not json at all {{{")
        self.assertTrue(_wait_until(lambda: self.link.stats["malformed"] >= 1))
        # Link is still alive and commands still work.
        resp = self.link.command("CMD_RESET", scope="all")
        self.assertEqual(resp["type"], "RESP_OK")


class TestLaptopLiveness(unittest.TestCase):
    def test_esp32_lost_freeze_and_restore(self):
        laptop_end, esp_end = create_memory_pair()
        esp = FakeEsp32(esp_end)
        link = LaptopLink(
            laptop_end,
            hb_interval_s=0.05,
            command_timeout_s=0.4,
            esp32_timeout_s=0.2,
            auto_estop_on_lost=True,
        )
        lost_events = []
        link.on_esp32_lost = lambda: lost_events.append("lost")
        link.on_esp32_restored = lambda: lost_events.append("restored")
        esp.start()
        link.start()

        try:
            esp.set_ready()
            self.assertIsNotNone(link.wait_tele(timeout=2.0, after=0))

            # ESP32 goes silent (stops sending heartbeats/telemetry).
            esp.pause_output = True
            self.assertTrue(_wait_until(lambda: link.frozen, timeout=2.0))
            self.assertIn("lost", lost_events)
            with self.assertRaises(Esp32Lost):
                link.command("CMD_MOVE", lin=0.0, ang=0.0)

            # ESP32 resumes -> link unfreezes and reports the restore.
            esp.pause_output = False
            self.assertTrue(_wait_until(lambda: not link.frozen, timeout=3.0))
            self.assertIn("restored", lost_events)

            # The laptop's own emergency stop (sent on loss detection) latched
            # the ESP32, so motion needs a CMD_RESET scope=all first — exactly
            # the protocol's re-arm sequence.
            with self.assertRaises(CommandError) as ctx:
                link.command("CMD_MOVE", lin=0.1, ang=0.0)
            self.assertEqual(ctx.exception.code, "NOT_READY")
            link.command("CMD_RESET", scope="all")
            resp = link.command("CMD_MOVE", lin=0.1, ang=0.0)
            self.assertEqual(resp["type"], "RESP_OK")
        finally:
            link.stop()
            esp.stop()

    def test_protocol_version_mismatch_freezes_link(self):
        laptop_end, esp_end = create_memory_pair()
        esp = FakeEsp32(esp_end)
        link = LaptopLink(laptop_end, hb_interval_s=0.05)
        mismatches = []
        link.on_protocol_mismatch = lambda m: mismatches.append(m)
        esp.start()
        link.start()
        try:
            # A v2 HEARTBEAT arrives from the ESP32 (old/new firmware mix).
            esp.send_raw('{"v":2,"type":"HEARTBEAT","seq":1}')
            self.assertTrue(_wait_until(lambda: link.frozen, timeout=2.0))
            self.assertEqual(link.stats["incompatible"], 1)
            self.assertEqual(len(mismatches), 1)
            with self.assertRaises(Esp32Lost):
                link.command("CMD_MOVE", lin=0.0, ang=0.0)
        finally:
            link.stop()
            esp.stop()


class TestInboundRobustness(LaptopLinkTestBase):
    """Receive-side robustness: structural validation of inbound frames
    before they reach callbacks/consumers (review fix B2)."""

    def test_duplicate_command_replay_is_not_reexecuted(self):
        self.esp.set_ready()
        resp = self.link.command("CMD_MOVE", lin=0.1, ang=0.0)
        self.assertEqual(resp["type"], "RESP_OK")
        self.assertEqual(len(self.esp.executed_commands), 1)

        # Replay the identical frame (same seq + type).  The ESP32 must
        # answer from its stored response and NOT re-execute (protocol §9).
        replay = make_message("CMD_MOVE", resp["ack"],
                              {"lin": 0.1, "ang": 0.0})
        self.esp.send_raw(encode_line(replay))
        self.assertTrue(_wait_until(
            lambda: len(self.esp.executed_commands) == 1, timeout=1.0))
        time.sleep(0.05)
        self.assertEqual(len(self.esp.executed_commands), 1)

        # Link is still healthy afterwards.
        resp = self.link.command("CMD_RESET", scope="all")
        self.assertEqual(resp["type"], "RESP_OK")

    def test_unknown_inbound_type_is_logged_and_ignored(self):
        self.esp.set_ready()
        self.esp.send_raw('{"v":1,"type":"MYSTERY_EVENT","seq":2}')
        self.assertTrue(_wait_until(
            lambda: self.link.stats["rx"] >= 1, timeout=1.0))
        self.assertFalse(self.link.frozen)
        # Link still usable.
        resp = self.link.command("CMD_RESET", scope="all")
        self.assertEqual(resp["type"], "RESP_OK")

    def test_oversized_inbound_frame_is_discarded(self):
        # > 512 bytes but valid JSON with a valid header: the length guard
        # must treat it as malformed (protocol §2) without a crash.
        big = '{"v":1,"type":"HEARTBEAT","seq":1,"pad":"' + "x" * 600 + '"}'
        self.esp.send_raw(big)
        self.assertTrue(_wait_until(
            lambda: self.link.stats["malformed"] >= 1, timeout=1.0))
        self.assertFalse(self.link.frozen)

    def test_malformed_tele_shape_is_dropped_before_callbacks(self):
        # JSON-valid but missing protocol-required TELE fields: must not
        # reach consumers (no KeyError, no last_tele update from it).
        self.esp.send_raw('{"v":1,"type":"TELE","seq":7,"enc":{}}')
        self.assertTrue(_wait_until(
            lambda: self.link.stats["invalid_shape"] >= 1, timeout=1.0))
        self.assertFalse(self.link.frozen)
        # A healthy TELE frame still flows afterwards.
        tele = self.link.wait_tele(timeout=1.0, after=0)
        self.assertIsNotNone(tele)

    def test_resp_without_pending_command_is_ignored(self):
        self.esp.set_ready()
        self.esp.send_raw('{"v":1,"type":"RESP_OK","seq":50,"ack":4321}')
        time.sleep(0.1)  # let it arrive with nothing pending
        # No crash, no stale state: a normal command still works.
        resp = self.link.command("CMD_RESET", scope="all")
        self.assertEqual(resp["type"], "RESP_OK")

    def test_wait_tele_after_baseline_returns_only_newer_frames(self):
        baseline = self.link.tele_seq
        frame = self.link.wait_tele(timeout=1.0, after=baseline)
        self.assertIsNotNone(frame)
        self.assertGreater(self.link.tele_seq, baseline)
        # Asking for a frame beyond the current sequence times out cleanly.
        self.assertIsNone(self.link.wait_tele(timeout=0.1, after=10 ** 9))


if __name__ == "__main__":
    unittest.main()
