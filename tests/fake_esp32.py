"""Minimal ESP32 protocol emulator — **test support only**.

Re-implements just enough of the ESP32-side protocol behaviour
(``docs/COMMUNICATION_PROTOCOL.md``) for the laptop-link tests to exercise
real message flows without hardware:

* EVT_BOOT once at startup, then periodic HEARTBEAT + TELE
* READY gating (commands rejected with NOT_READY before ``set_ready()``)
* CMD_RESET re-arm semantics (scope ``all`` clears a latch; ``faults`` does
  not) and duplicate-command suppression (same seq+type re-sends the stored
  response without re-executing)
* CMD_STOP emergency -> latched e-stop (EVT_FAULT code 1)

It contains no motor, sensor, AI or navigation behaviour — it is a *link*
double.  The real ESP32-side logic lives in ``firmware/lib/protocol`` and is
covered by the firmware native unit tests.
"""

from __future__ import annotations

import threading
import time
from typing import Dict, List, Optional

from integration.communication.protocol import (
    LAPTOP_TO_ESP32,
    SequenceCounter,
    decode_line,
    encode_line,
    make_message,
)

MALFORMED = "malformed"
INCOMPATIBLE = "incompatible"
OK = "ok"


class FakeEsp32:
    """A scriptable ESP32 protocol endpoint for laptop-link tests."""

    def __init__(
        self,
        transport,
        *,
        fw: str = "fake-esp32-0.1",
        tele_period_s: float = 0.02,
        hb_period_s: float = 0.02,
    ) -> None:
        self._transport = transport
        self._fw = fw
        self._tele_period = tele_period_s
        self._hb_period = hb_period_s

        self._seq = SequenceCounter()
        self._lock = threading.Lock()
        self._closed = False
        self._thread: Optional[threading.Thread] = None

        self.ready = False                     # READY state (startup checks)
        self.latched = False                   # e-stop latch
        self.faults: List[int] = []
        self.ball_count = 0

        # Counters / logs for assertions.
        self.heartbeats_from_laptop = 0
        self.executed_commands: List[Dict] = []
        self.malformed_from_laptop = 0

        # Duplicate-command suppression (protocol §9).
        self._last_cmd_type: Optional[str] = None
        self._last_cmd_seq: Optional[int] = None
        self._last_resp_line: Optional[str] = None

        # Output pause: when True no HEARTBEAT/TELE are sent (simulates a
        # silent ESP32 for the laptop-liveness tests).
        self.pause_output = False

        # Swallow the next ``count`` commands of ``mtype`` without responding
        # (exercises the laptop retransmission / timeout paths).
        self.drop_command_type: Optional[str] = None
        self.drop_remaining = 0

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        self._t0 = time.monotonic()
        self._last_tele = self._t0
        self._last_hb = self._t0
        # EVT_BOOT, sent once immediately after boot (protocol flow A).
        self._send(make_message("EVT_BOOT", self._seq.next(),
                                {"reason": "poweron", "fw": self._fw},
                                ts=self._now_ms()))
        self._thread = threading.Thread(target=self._run, name="fake-esp32", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._closed = True
        try:
            self._transport.close()
        finally:
            if self._thread is not None:
                self._thread.join(timeout=2.0)

    def set_ready(self) -> None:
        """Simulate completion of the ESP32 startup checks."""
        with self._lock:
            self.ready = True

    def drop_next_commands(self, mtype: str, count: int = 1) -> None:
        """Drop (swallow, no response) the next ``count`` commands of ``mtype``."""
        with self._lock:
            self.drop_command_type = mtype
            self.drop_remaining = count

    def send_raw(self, line: str) -> None:
        """Inject an arbitrary raw line (e.g. a v-mismatch message)."""
        self._transport.send_text(line)

    # -- internals ---------------------------------------------------------

    def _now_ms(self) -> int:
        return int((time.monotonic() - self._t0) * 1000)

    def _send(self, msg: Dict) -> None:
        try:
            self._transport.send_text(encode_line(msg))
        except RuntimeError:
            if self._closed:
                return  # normal during teardown
            raise

    def _resp(self, msg: Dict, *, ok: bool, code: str = "") -> None:
        seq = self._seq.next()
        if ok:
            resp: Dict = {"ack": msg["seq"]}
            mtype = "RESP_OK"
        else:
            resp = {"ack": msg["seq"], "code": code, "msg": f"fake: {code}"}
            mtype = "RESP_ERR"
        line = encode_line(make_message(mtype, seq, resp, ts=self._now_ms()))
        self._transport.send_text(line)
        self._last_resp_line = line  # replayed on duplicates (protocol §9)

    def _fault(self, code: int) -> None:
        if code not in self.faults:
            self.faults.append(code)
        self._send(make_message("EVT_FAULT", self._seq.next(),
                                {"code": code, "info": f"fake fault {code}"},
                                ts=self._now_ms()))

    def _run(self) -> None:
        while not self._closed:
            line = self._transport.recv_text(timeout=0.005)
            if line:
                self._handle_line(line)
            now = time.monotonic()
            if not self.pause_output:
                if now - self._last_tele >= self._tele_period:
                    self._last_tele = now
                    self._send(self._make_tele())
                if now - self._last_hb >= self._hb_period:
                    self._last_hb = now
                    self._send(make_message("HEARTBEAT", self._seq.next(),
                                            ts=self._now_ms()))

    def _make_tele(self) -> Dict:
        with self._lock:
            faults = list(self.faults)
            balls = {"count": self.ball_count,
                     "full": self.ball_count >= 10}
        return make_message("TELE", self._seq.next(), {
            "enc": {"dl": 0, "dr": 0},
            "us": [9999, 9999, 9999, 9999],
            "imu": {"ax": 0.0, "ay": 0.0, "az": 1.0,
                    "gx": 0.0, "gy": 0.0, "gz": 0.0},
            "mot": {"pwm_l": 0, "pwm_r": 0, "spd_l": 0.0, "spd_r": 0.0},
            "collect": {"trip": False, "state": "idle"},
            "balls": balls,
            "faults": faults,
        }, ts=self._now_ms())

    def _handle_line(self, line: str) -> None:
        kind, msg = decode_line(line)
        if kind == MALFORMED:
            self.malformed_from_laptop += 1
            return  # protocol §2/§9: discard + count, no RESP_ERR
        if kind == INCOMPATIBLE:
            # Protocol §13: version mismatch -> RESP_ERR VERSION + fault 8.
            self._fault(8)
            self._resp(msg, ok=False, code="VERSION")
            return
        mtype = msg["type"]
        if mtype not in LAPTOP_TO_ESP32:
            self._resp(msg, ok=False, code="UNKNOWN_TYPE")
            return

        if mtype == "HEARTBEAT":
            with self._lock:
                self.heartbeats_from_laptop += 1
            return  # no explicit response (ESP32 heartbeat is independent)

        # Duplicate command (same seq + type) -> replay stored response
        # without re-executing (protocol §9).
        with self._lock:
            seq = msg["seq"]
            duplicate = (seq == self._last_cmd_seq and
                         mtype == self._last_cmd_type and
                         self._last_resp_line is not None)
        if duplicate:
            self._transport.send_text(self._last_resp_line)
            return

        # Test hook: swallow the next commands of a given type (no response).
        with self._lock:
            dropping = (self.drop_remaining > 0 and
                        self.drop_command_type == mtype)
        if dropping:
            with self._lock:
                self.drop_remaining -= 1
                self._last_cmd_type = mtype
                self._last_cmd_seq = seq
                self._last_resp_line = None  # so a retry is not mis-replayed
            return

        if mtype == "CMD_RESET":
            if not self.ready and not self.latched:
                # Still starting up: everything is rejected (incl. reset).
                self._resp(msg, ok=False, code="NOT_READY")
            elif msg.get("scope") == "all":
                self.latched = False
                self.faults = []
                self.ready = True
                self._resp(msg, ok=True)
            else:  # scope=faults: clears non-latching faults only
                self.faults = []
                self._resp(msg, ok=True)
        elif self.latched or not self.ready:
            self._resp(msg, ok=False, code="NOT_READY")
        elif mtype == "CMD_STOP" and msg.get("mode") == "emergency":
            self.latched = True
            self._fault(1)
            self._resp(msg, ok=True)
        else:  # CMD_MOVE / CMD_STOP normal / CMD_INTAKE / CMD_DISPENSE
            self._resp(msg, ok=True)
            with self._lock:
                self.executed_commands.append(dict(msg))

        with self._lock:
            self._last_cmd_type = mtype
            self._last_cmd_seq = seq
