"""Laptop link layer: reusable laptop ↔ ESP32 protocol endpoint.

Implements the laptop half of ``docs/COMMUNICATION_PROTOCOL.md``:

* opening/closing the transport (USB serial for hardware, in-memory in tests)
* newline framing + JSON encode/decode (via ``protocol.py``)
* rolling sequence numbers and version checks
* acknowledged commands (``RESP_OK`` / ``RESP_ERR``) with a 300 ms timeout
  and one retransmission, per protocol §8/§9
* automatic ``HEARTBEAT`` every 500 ms (protocol §8)
* ESP32-liveness watchdog (1500 ms) that freezes command issuance and flags
  the state machine owner (protocol §7, laptop side)

This class has **no** AI, navigation or motor logic.  It is the transport
used later by the AI module (state coherence), the navigation module
(``CMD_MOVE``/``CMD_STOP`` producers) and the state machine (acquisition and
safety commands) — see ``docs/TEAM_INTERFACE_CONTRACT.md`` §5 I-7.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from typing import Any, Callable, Deque, Dict, Iterable, Optional

from .protocol import (
    COMMAND_TIMEOUT_S,
    COMMAND_SCHEMAS,
    ESP32_LIVENESS_TIMEOUT_S,
    HEARTBEAT_INTERVAL_S,
    ESP32_TO_LAPTOP,
    LAPTOP_TO_ESP32,
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
from .transport import Transport

LOGGER = logging.getLogger("integration.communication.laptop_link")


class LinkError(RuntimeError):
    """Base error for laptop-side link failures."""


class Esp32Lost(LinkError):
    """The ESP32 is considered lost (no heartbeat/telemetry for the timeout)
    or the link is frozen for another reason (e.g. protocol mismatch), so
    commands are not being issued."""


class LinkTimeout(LinkError):
    """A command did not receive a RESP within the timeout budget
    (including the single retransmission).  Per protocol §8 this is treated
    as a communication failure."""


class CommandError(LinkError):
    """The ESP32 answered a command with RESP_ERR."""

    def __init__(self, code: str, message: str, response: Dict[str, Any]) -> None:
        super().__init__(f"command rejected: {code}: {message}")
        self.code = code
        self.message = message
        self.response = response


Callback = Callable[[Dict[str, Any]], None]


class LaptopLink:
    """A protocol endpoint on the laptop side of the link.

    Typical use::

        from integration.communication.laptop_link import LaptopLink
        from integration.communication.transport import SerialTransport

        link = LaptopLink(SerialTransport("/dev/ttyUSB0", 115200))
        link.start()
        try:
            link.wait_for_event("EVT_BOOT", timeout=5.0)
            resp = link.command("CMD_RESET", scope="all")
        finally:
            link.stop()
    """

    def __init__(
        self,
        transport: Transport,
        *,
        heartbeat: bool = True,
        hb_interval_s: float = HEARTBEAT_INTERVAL_S,
        command_timeout_s: float = COMMAND_TIMEOUT_S,
        command_retries: int = 1,  # one retransmission, then comms failure
        esp32_timeout_s: float = ESP32_LIVENESS_TIMEOUT_S,
        auto_estop_on_lost: bool = True,
        max_cmd_rate_hz: float = 20.0,  # protocol §8: never flood the ESP32
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self._transport = transport
        self._hb_enabled = heartbeat
        self._hb_interval = hb_interval_s
        self._cmd_timeout = command_timeout_s
        self._cmd_retries = int(command_retries)
        self._esp32_timeout = esp32_timeout_s
        self._auto_estop = auto_estop_on_lost
        self._cmd_period = (1.0 / max_cmd_rate_hz) if max_cmd_rate_hz > 0 else 0.0
        self._last_cmd_at: Optional[float] = None
        self._log = logger or LOGGER

        self._seq = SequenceCounter()
        self._started = False
        self._closed = False
        self._lock = threading.RLock()

        self._last_rx_monotonic: Optional[float] = None
        self._esp32_lost = False
        self._incompatible = False
        self._estop_sent = False  # one auto emergency stop per loss episode
        self._t0 = time.monotonic()

        # Events (EVT_*) received, oldest first, for wait_for_event().
        self._events: Deque[Dict[str, Any]] = deque()
        self._cv_events = threading.Condition(self._lock)
        # Telemetry tracking (TELE is periodic; never queued).
        self._tele_seq = 0
        self._cv_tele = threading.Condition(self._lock)

        # Pending command acknowledgements: ack seq -> decoded RESP message.
        self._pending: Dict[int, Dict[str, Any]] = {}
        self._cv_resp = threading.Condition(self._lock)

        self._threads: list = []
        self.last_tele: Optional[Dict[str, Any]] = None

        # Received-message counters (also useful for malformed-line counts).
        self.stats = {"sent": 0, "rx": 0, "malformed": 0,
                      "incompatible": 0, "invalid_shape": 0}

        # Optional callbacks (set by the owner module).  All callbacks take
        # the decoded message dict, except on_esp32_lost/on_esp32_restored
        # which take no arguments (state is read from the link properties).
        self.on_tele: Optional[Callback] = None
        self.on_event: Optional[Callback] = None
        self.on_heartbeat: Optional[Callback] = None
        self.on_esp32_lost: Optional[Callback] = None
        self.on_esp32_restored: Optional[Callback] = None
        self.on_protocol_mismatch: Optional[Callback] = None  # v != 1 msg

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Open the transport and start the reader/heartbeat/watchdog threads."""
        if self._started:
            return
        # SerialTransport opens lazily; memory transports need no open().
        opener = getattr(self._transport, "open", None)
        if opener is not None:
            opener()
        self._started = True
        self._closed = False
        self._t0 = time.monotonic()
        self._last_rx_monotonic = self._t0
        self._threads = [
            threading.Thread(target=self._reader_loop, name="laptop-link-reader", daemon=True),
        ]
        if self._hb_enabled:
            self._threads.append(
                threading.Thread(target=self._heartbeat_loop, name="laptop-link-heartbeat", daemon=True)
            )
        self._threads.append(
            threading.Thread(target=self._watchdog_loop, name="laptop-link-watchdog", daemon=True)
        )
        for t in self._threads:
            t.start()

    def stop(self) -> None:
        """Close the transport and join the background threads."""
        self._closed = True
        try:
            self._transport.close()
        except Exception:  # pragma: no cover - defensive shutdown
            self._log.exception("error closing transport")
        for t in self._threads:
            t.join(timeout=2.0)
        self._threads = []
        self._started = False

    def __enter__(self) -> "LaptopLink":
        self.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.stop()

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------

    @property
    def started(self) -> bool:
        return self._started

    @property
    def frozen(self) -> bool:
        """True while commands must not be issued (ESP32 lost or v mismatch)."""
        return self._incompatible or self._esp32_lost

    @property
    def esp32_alive(self) -> bool:
        return not self._esp32_lost and not self._incompatible

    @property
    def protocol_version(self) -> int:
        return PROTOCOL_VERSION

    @property
    def tele_seq(self) -> int:
        """Monotonic counter of TELE frames received (for wait_tele)."""
        return self._tele_seq

    def wait_tele(
        self, timeout: Optional[float] = None, after: int = 0
    ) -> Optional[Dict[str, Any]]:
        """Wait until a TELE frame newer than ``after`` (a ``tele_seq``
        baseline) arrives, returning it, or ``None`` on timeout."""
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._cv_tele:
            while self._tele_seq <= after:
                if deadline is not None:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return None
                else:
                    remaining = None
                self._cv_tele.wait(timeout=remaining)
            return self.last_tele

    def _check_started(self) -> None:
        if not self._started or self._closed:
            raise LinkError("link is not started (call start() first)")

    def send_message(self, message_type: str, **fields: Any) -> Dict[str, Any]:
        """Low-level send of an unacknowledged protocol message.

        Increments the laptop sequence number and writes one framed line.
        Raises :class:`ValidationError` locally for schema violations so an
        invalid message is never put on the wire.
        """
        self._check_started()
        if message_type not in LAPTOP_TO_ESP32:
            raise ValidationError(
                "UNKNOWN_TYPE", f"{message_type!r} is not a laptop message"
            )
        payload = complete_command_payload(message_type, fields)
        # HEARTBEAT carries no payload and has no schema entry; all other
        # laptop messages are validated before they go on the wire.
        if message_type in COMMAND_SCHEMAS:
            validate_command(message_type, payload)
        seq = self._seq.next()
        msg = make_message(message_type, seq, payload, ts=self._now_ms())
        self._write(msg)
        return msg

    def command(
        self,
        message_type: str,
        *,
        timeout: Optional[float] = None,
        **fields: Any,
    ) -> Dict[str, Any]:
        """Send an acknowledged command and wait for its RESP.

        Returns the decoded ``RESP_OK`` message.  Raises:

        * :class:`ValidationError` -- the payload violates the schema (never
          transmitted),
        * :class:`CommandError`    -- the ESP32 answered ``RESP_ERR`` (the
          ``code`` attribute carries the protocol error code),
        * :class:`Esp32Lost`       -- the link is frozen, or the ESP32 went
          quiet while waiting,
        * :class:`LinkTimeout`     -- no RESP after the timeout budget
          (retransmitted once per protocol §8, then treated as comms failure).
        """
        timeout = timeout or self._cmd_timeout
        if message_type not in COMMAND_SCHEMAS:
            raise ValidationError("UNKNOWN_TYPE", f"{message_type!r} is not a command")
        payload = complete_command_payload(message_type, fields)
        validate_command(message_type, payload)

        if self.frozen:
            raise Esp32Lost("link frozen; not issuing commands")

        self._pace_command()  # protocol §8: max 20 commands/s at the link
        seq = self._seq.next()
        msg = make_message(message_type, seq, payload, ts=self._now_ms())

        attempts = 1 + self._cmd_retries
        for attempt in range(attempts):
            if self.frozen:
                raise Esp32Lost("ESP32 lost while waiting for a response")
            self._write(msg)
            resp = self._wait_for_resp(seq, timeout)
            if resp is None:
                if self.frozen:
                    raise Esp32Lost("ESP32 lost while waiting for a response")
                if attempt < attempts - 1:
                    self._log.warning(
                        "no RESP for %s seq=%d within %.0f ms; retransmitting (attempt %d)",
                        message_type, seq, timeout * 1000, attempt + 2,
                    )
                    continue
                raise LinkTimeout(
                    f"no RESP for {message_type} seq={seq} after {attempts} "
                    f"attempts ({timeout * 1000:.0f} ms each)"
                )
            return self._handle_resp(resp)
        raise LinkTimeout("command failed")  # pragma: no cover - unreachable

    def wait_for_event(
        self,
        message_type: Optional[str] = None,
        types: Optional[Iterable[str]] = None,
        timeout: Optional[float] = None,
    ) -> Optional[Dict[str, Any]]:
        """Block until an EVT_* message (optionally of a given type) arrives.

        Returns the event payload or ``None`` on timeout.
        """
        wanted = set()
        if message_type is not None:
            wanted.add(message_type)
        if types is not None:
            wanted.update(types)
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._cv_events:
            while True:
                for i, ev in enumerate(self._events):
                    if not wanted or ev.get("type") in wanted:
                        del self._events[i]
                        return ev
                if deadline is not None and time.monotonic() >= deadline:
                    return None
                remaining = None if deadline is None else deadline - time.monotonic()
                self._cv_events.wait(timeout=remaining)

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    def _now_ms(self) -> int:
        return int((time.monotonic() - self._t0) * 1000)

    def _pace_command(self) -> None:
        """Light rate guard: keep acknowledged commands <= max_cmd_rate_hz.

        Simple sleep-based spacing (no queue/scheduler).  Navigation remains
        responsible for sensible command production; this is only a guard so
        a buggy producer cannot flood the ESP32 above the protocol's rate.
        """
        if self._cmd_period <= 0.0:
            return
        now = time.monotonic()
        if self._last_cmd_at is not None:
            wait = self._last_cmd_at + self._cmd_period - now
            if wait > 0:
                time.sleep(wait)
                now = time.monotonic()
        self._last_cmd_at = now

    @staticmethod
    def _notify(cb: Optional[Callback], *args: Any) -> None:
        """Invoke a user callback; a failing callback must never kill the
        reader thread (it is logged and ignored)."""
        if cb is None:
            return
        try:
            cb(*args)
        except Exception:
            LOGGER.exception("callback %r failed", getattr(cb, "__name__", cb))

    def _write(self, msg: Dict[str, Any]) -> None:
        line = encode_line(msg)
        self._transport.send_text(line)
        with self._lock:
            self.stats["sent"] += 1
        self._log.debug("TX %s", line)

    def _handle_resp(self, resp: Dict[str, Any]) -> Dict[str, Any]:
        if resp.get("type") == "RESP_OK":
            return resp
        # RESP_ERR
        code = resp.get("code", "INTERNAL")
        msg = resp.get("msg") or f"RESP_ERR {code}"
        raise CommandError(str(code), str(msg), resp)

    def _wait_for_resp(self, seq: int, timeout: float) -> Optional[Dict[str, Any]]:
        deadline = time.monotonic() + timeout
        with self._cv_resp:
            while True:
                resp = self._pending.pop(seq, None)
                if resp is not None:
                    return resp
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._cv_resp.wait(timeout=remaining)

    # -- background threads ----------------------------------------------

    def _reader_loop(self) -> None:
        while not self._closed:
            line = self._transport.recv_text(timeout=0.25)
            if line is None:
                continue  # idle timeout or closed; re-check _closed
            self._on_line(line)

    def _on_line(self, line: str) -> None:
        kind, msg = decode_line(line)
        if kind == "malformed":
            with self._lock:
                self.stats["malformed"] += 1
            self._log.warning("RX malformed line (counted, ignored): %r", line[:80])
            return
        with self._lock:
            self.stats["rx"] += 1
        self._last_rx_monotonic = time.monotonic()
        if kind == "incompatible":
            # Protocol §13: log incompatibility, freeze, notify the operator.
            self._incompatible = True
            self._esp32_lost = False
            with self._lock:
                self.stats["incompatible"] += 1
            self._log.error(
                "protocol version mismatch: received v=%r, supported v=%d — "
                "link frozen", msg.get("v"), PROTOCOL_VERSION,
            )
            self._notify(self.on_protocol_mismatch, msg)
            return
        self._log.debug("RX %s", line)
        self._dispatch(msg)

    def _dispatch(self, msg: Dict[str, Any]) -> None:
        mtype = msg.get("type")
        # Structural guard: a malformed-but-JSON-valid frame must never reach
        # callbacks/consumers (protocol §4 requires these fields).
        if not inbound_shape_ok(msg):
            with self._lock:
                self.stats["invalid_shape"] += 1
            self._log.warning(
                "inbound %r failed structural validation; dropped", mtype)
            return
        # Loss clears only on a real liveness signal (HEARTBEAT or TELE), per
        # protocol §7 — a stray RESP proves the UART answers but not that the
        # ESP32 is healthy enough to run the robot.
        if mtype in ("HEARTBEAT", "TELE") and self._esp32_lost:
            self._esp32_lost = False
            self._estop_sent = False
            self._log.info("ESP32 link restored")
            self._notify(self.on_esp32_restored)
        if mtype == "HEARTBEAT":
            self._notify(self.on_heartbeat, msg)
        elif mtype == "TELE":
            self.last_tele = msg
            with self._cv_tele:
                self._tele_seq += 1
                self._cv_tele.notify_all()
            self._notify(self.on_tele, msg)
        elif mtype.startswith("EVT_"):
            with self._cv_events:
                self._events.append(msg)
                self._cv_events.notify_all()
            self._notify(self.on_event, msg)
        elif mtype in ("RESP_OK", "RESP_ERR"):
            ack = msg.get("ack")
            if ack is not None:
                with self._cv_resp:
                    self._pending[ack] = msg
                    self._cv_resp.notify_all()
            else:
                self._log.warning("RESP without ack field ignored: %s", line_of(msg))
        else:
            self._log.warning("unknown message type from ESP32 ignored: %r", mtype)

    def _heartbeat_loop(self) -> None:
        while not self._closed:
            time.sleep(self._hb_interval)
            if self._closed:
                break
            try:
                if self._started and not self._incompatible:
                    self.send_message("HEARTBEAT")
            except Exception:  # pragma: no cover - defensive
                self._log.exception("heartbeat send failed")

    def _watchdog_loop(self) -> None:
        while not self._closed:
            time.sleep(0.05)
            if self._closed:
                break
            if self._incompatible:
                continue
            now = time.monotonic()
            silent_for = now - (self._last_rx_monotonic or now)
            if silent_for > self._esp32_timeout and not self._esp32_lost:
                self._esp32_lost = True
                self._log.error(
                    "ESP32 lost: no heartbeat/telemetry for %.0f ms",
                    silent_for * 1000,
                )
                if self._auto_estop and self._started and not self._estop_sent:
                    # Protocol §7 (laptop side): send an emergency stop once,
                    # then keep heartbeating; the state machine is frozen.
                    self._estop_sent = True
                    try:
                        self.send_message("CMD_STOP", mode="emergency")
                    except Exception:  # pragma: no cover - best effort
                        self._log.debug("emergency stop send failed (expected while lost)")
                self._notify(self.on_esp32_lost)


def line_of(msg: Dict[str, Any]) -> str:
    """Compact repr of a message for logging."""
    return encode_line(msg)
