"""Protocol constants, message builders and wire helpers.

Laptop-side mirror of ``docs/COMMUNICATION_PROTOCOL.md`` (protocol version 1).

Everything in this module is **pure Python** (no serial, no I/O) so it can be
unit-tested without any hardware and reused by the AI, navigation and
state-machine modules.

Wire format (protocol §2, §3):
    * one message per line, terminated by LF (a trailing CR is ignored)
    * UTF-8, compact JSON, one object per line, <= 512 bytes
    * every message carries the header {v, type, seq} and optional ts

This module deliberately does not decide any behaviour the protocol leaves
open; the few local choices are documented inline.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional, Tuple

# --------------------------------------------------------------------------
# Protocol constants (from docs/COMMUNICATION_PROTOCOL.md)
# --------------------------------------------------------------------------

PROTOCOL_VERSION = 1

MAX_LINE_BYTES = 512

# Timing (protocol §8)
HEARTBEAT_INTERVAL_S = 0.5        # both directions
TELEMETRY_PERIOD_S = 0.05         # ESP32 TELE rate = 20 Hz
COMMAND_TIMEOUT_S = 0.3           # laptop waits for a RESP before retransmit
ESP32_LIVENESS_TIMEOUT_S = 1.5    # laptop declares the ESP32 lost
LAPTOP_LIVENESS_TIMEOUT_S = 1.0   # ESP32 declares the laptop lost (1000 ms)
COMMAND_MAX_RATE_HZ = 20          # laptop never floods faster than 20 Hz

# Message catalog (protocol §3)
LAPTOP_TO_ESP32 = (
    "HEARTBEAT",
    "CMD_MOVE",
    "CMD_STOP",
    "CMD_INTAKE",
    "CMD_DISPENSE",
    "CMD_RESET",
)

ESP32_TO_LAPTOP = (
    "HEARTBEAT",
    "EVT_BOOT",
    "TELE",
    "EVT_COLLECT",
    "EVT_DISPENSE",
    "EVT_FAULT",
    "RESP_OK",
    "RESP_ERR",
)

ALL_MESSAGE_TYPES = LAPTOP_TO_ESP32 + ESP32_TO_LAPTOP

# Error codes (protocol §3, in RESP_ERR.code)
ERROR_CODES = (
    "PARSE",
    "UNKNOWN_TYPE",
    "RANGE",
    "VERSION",
    "NOT_READY",
    "BUSY",
    "INTERNAL",
)

# Fault codes (protocol §7 / Appendix A)
FAULT_CODES = {
    1: "e-stop latched",
    2: "comms lost",
    3: "obstacle hard stop",
    4: "ultrasonic fault",
    5: "IMU fault",
    6: "motor fault",
    7: "reserved",
    8: "protocol version mismatch",
    9: "dispense mechanism",
}

# Command schemas used by the laptop to validate a message *before* sending.
# The ESP32 performs the same checks on receipt.  Field types:
#   ("float", lo, hi)          -- finite float in [lo, hi]
#   ("int", lo, hi)            -- integer in [lo, hi]
#   ("enum", (a, b, ...))      -- one of the given strings
COMMAND_SCHEMAS: Dict[str, Dict[str, Any]] = {
    # Continuous velocity command; normalized fractions of max speed.
    "CMD_MOVE": {
        "required": ("lin", "ang"),
        "fields": {
            "lin": ("float", -1.0, 1.0),
            "ang": ("float", -1.0, 1.0),
        },
    },
    # mode defaults to "normal" if omitted (protocol §4.1).
    "CMD_STOP": {
        "required": (),
        "fields": {"mode": ("enum", ("normal", "emergency"))},
        "defaults": {"mode": "normal"},
    },
    "CMD_INTAKE": {
        "required": ("action",),
        "fields": {"action": ("enum", ("start", "stop"))},
    },
    "CMD_DISPENSE": {
        "required": ("count",),
        "fields": {"count": ("int", 1, 10)},
    },
    "CMD_RESET": {
        "required": ("scope",),
        "fields": {"scope": ("enum", ("faults", "all"))},
    },
}


class ValidationError(ValueError):
    """Raised when a message payload violates the protocol schema.

    ``code`` is the RESP_ERR error code the ESP32 would answer with
    (``RANGE`` for an out-of-range value, ``PARSE`` for a missing/invalid
    required field).  The laptop validates locally so it never sends a
    message the ESP32 would reject on schema grounds.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _check_float(name: str, spec: Tuple[Any, ...], value: Any) -> None:
    lo, hi = spec[1], spec[2]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError("PARSE", f"{name} must be a number")
    value = float(value)
    if value != value or value in (float("inf"), float("-inf")):
        raise ValidationError("RANGE", f"{name} must be finite")
    if not (lo <= value <= hi):
        raise ValidationError(
            "RANGE", f"{name} {value} outside [{lo}, {hi}]"
        )


def _check_int(name: str, spec: Tuple[Any, ...], value: Any) -> None:
    lo, hi = spec[1], spec[2]
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError("PARSE", f"{name} must be an integer")
    if not (lo <= value <= hi):
        raise ValidationError(
            "RANGE", f"{name} {value} outside [{lo}, {hi}]"
        )


def _check_enum(name: str, spec: Tuple[Any, ...], value: Any) -> None:
    allowed = spec[1]
    if not isinstance(value, str) or value not in allowed:
        raise ValidationError(
            "RANGE", f"{name} must be one of {', '.join(allowed)}"
        )


def validate_command(message_type: str, payload: Dict[str, Any]) -> None:
    """Validate a laptop command payload against the protocol schema.

    Raises :class:`ValidationError` (with a RESP_ERR-style ``code``) when the
    payload is invalid.  Unknown extra fields are ignored (protocol §9 and
    the additive-change policy tolerate them).
    """
    if message_type not in COMMAND_SCHEMAS:
        raise ValidationError(
            "UNKNOWN_TYPE", f"unknown command type {message_type!r}"
        )
    schema = COMMAND_SCHEMAS[message_type]
    for name in schema["required"]:
        if name not in payload or payload[name] is None:
            raise ValidationError("PARSE", f"missing required field {name!r}")
    for name, value in payload.items():
        if name not in schema["fields"]:
            continue  # additive tolerance: unknown optional fields ignored
        kind = schema["fields"][name][0]
        if kind == "float":
            _check_float(name, schema["fields"][name], value)
        elif kind == "int":
            _check_int(name, schema["fields"][name], value)
        elif kind == "enum":
            _check_enum(name, schema["fields"][name], value)
        else:  # pragma: no cover - defensive
            raise AssertionError(f"bad schema for {message_type}.{name}")


def complete_command_payload(message_type: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Return the payload with schema defaults applied (e.g. CMD_STOP mode)."""
    merged = dict(payload)
    defaults = COMMAND_SCHEMAS.get(message_type, {}).get("defaults", {})
    for name, value in defaults.items():
        merged.setdefault(name, value)
    return merged


class SequenceCounter:
    """Rolling 0..65535 sequence counter (protocol §9)."""

    def __init__(self, start: int = 0) -> None:
        self._value = start % 65536

    def next(self) -> int:
        value = self._value
        self._value = (self._value + 1) % 65536
        return value

    @property
    def value(self) -> int:
        return self._value


# --------------------------------------------------------------------------
# Message construction
# --------------------------------------------------------------------------

def make_message(
    message_type: str,
    seq: int,
    payload: Optional[Dict[str, Any]] = None,
    ts: Optional[int] = None,
    version: int = PROTOCOL_VERSION,
) -> Dict[str, Any]:
    """Build a protocol message dict with the canonical header field order.

    ``v`` and ``type`` and ``seq`` are always present (protocol §3).
    """
    if not isinstance(seq, int) or not (0 <= seq <= 65535):
        raise ValidationError("PARSE", f"seq {seq!r} outside 0..65535")
    msg: Dict[str, Any] = {"v": version, "type": message_type, "seq": seq}
    if ts is not None:
        msg["ts"] = int(ts)
    if payload:
        msg.update(payload)
    return msg


def encode_line(message: Dict[str, Any]) -> str:
    """Serialize one message to a compact, single-line JSON string.

    Does not append the newline (the caller/transport adds it).
    """
    line = json.dumps(
        message, separators=(",", ":"), ensure_ascii=False, sort_keys=False
    )
    if len(line.encode("utf-8")) > MAX_LINE_BYTES:
        raise ValidationError("PARSE", f"message exceeds {MAX_LINE_BYTES} bytes")
    return line


def _has_valid_header(msg: Any) -> bool:
    """True if the parsed line is a JSON object with a usable header.

    \"Usable header\" means the receiver can identify the type and can
    acknowledge it: ``type`` is a string and ``seq`` is an int in range.
    ``v`` is checked separately (a wrong ``v`` is still answerable).
    """
    if not isinstance(msg, dict):
        return False
    if not isinstance(msg.get("type"), str):
        return False
    seq = msg.get("seq")
    return isinstance(seq, int) and not isinstance(seq, bool) and 0 <= seq <= 65535


def inbound_shape_ok(msg: Dict[str, Any]) -> bool:
    """Structural guard for inbound ESP32 messages (protocol §4).

    Checks that the protocol-required fields are present and of the right
    container type so that callbacks/consumers never KeyError on a
    malformed-but-JSON-valid frame.  This is deliberately shallow: it checks
    structure the protocol *requires*, not values (extra fields are
    tolerated per the additive-change policy).
    """
    mtype = msg.get("type")
    if mtype == "TELE":
        enc = msg.get("enc")
        us = msg.get("us")
        imu = msg.get("imu")
        mot = msg.get("mot")
        collect = msg.get("collect")
        balls = msg.get("balls")
        return (
            isinstance(enc, dict) and "dl" in enc and "dr" in enc
            and isinstance(us, list) and len(us) == 4
            and isinstance(imu, dict)
            and all(k in imu for k in ("ax", "ay", "az", "gx", "gy", "gz"))
            and isinstance(mot, dict)
            and all(k in mot for k in ("pwm_l", "pwm_r", "spd_l", "spd_r"))
            and isinstance(collect, dict)
            and "trip" in collect and "state" in collect
            and isinstance(balls, dict)
            and "count" in balls and "full" in balls
            and isinstance(msg.get("faults"), list)
        )
    if mtype in ("EVT_COLLECT", "EVT_DISPENSE"):
        balls = msg.get("balls")
        return (
            isinstance(msg.get("status"), str)
            and isinstance(balls, dict)
            and "count" in balls and "full" in balls
        )
    if mtype == "EVT_FAULT":
        code = msg.get("code")
        return isinstance(code, int) and not isinstance(code, bool)
    if mtype == "RESP_OK":
        ack = msg.get("ack")
        return isinstance(ack, int) and not isinstance(ack, bool)
    if mtype == "RESP_ERR":
        ack = msg.get("ack")
        return (
            isinstance(ack, int) and not isinstance(ack, bool)
            and isinstance(msg.get("code"), str)
        )
    return True  # HEARTBEAT and anything unknown carry no payload contract


def decode_line(line: str) -> Tuple[str, Optional[Dict[str, Any]]]:
    """Parse one received line (without the trailing newline).

    Returns ``(kind, message)`` where ``kind`` is one of:

    * ``"ok"``          -- valid JSON object with a usable header.  Caller
                           still checks ``v`` compatibility.
    * ``"incompatible"``-- valid header but ``v`` is not the supported
                           protocol version (protocol §13: log, freeze).
    * ``"malformed"``   -- no usable header/seq could be recovered (not JSON,
                           wrong shape, or missing type/seq).  Per protocol
                           §2/§9 the receiver discards it, logs/counts it and
                           sends **no** RESP_ERR.

    Never raises: a bad line must not crash either side (protocol §2).
    """
    if not isinstance(line, str):
        line = str(line)
    stripped = line.strip()
    if stripped.endswith("\r"):
        stripped = stripped[:-1]
    if not stripped:
        return "malformed", None
    # Maximum message length is 512 bytes per line (protocol §2); oversized
    # frames are discarded like any other malformed line.
    if len(stripped.encode("utf-8")) > MAX_LINE_BYTES:
        return "malformed", None
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        return "malformed", None
    if not _has_valid_header(parsed):
        return "malformed", None
    version = parsed.get("v")
    if not isinstance(version, int):
        # Header incomplete: v is a required header field (protocol §3).
        return "malformed", None
    if version != PROTOCOL_VERSION:
        return "incompatible", parsed
    return "ok", parsed
