"""Raw telemetry bridge: protocol ``TELE`` dict → :class:`TelemetrySnapshot`.

Consumes the laptop-side representation of an ESP32 ``TELE`` frame exactly
as defined in ``docs/COMMUNICATION_PROTOCOL.md`` §4.2 and converts it into
typed, validated containers for Module 2 consumers.

**Everything here is RAW passthrough — no interpretation:**

* ``enc`` deltas stay in **raw encoder counts**; converting them to meters
  is gated by **OD-12** and is deliberately absent.
* ``imu`` values stay raw (g / deg/s as sent by the ESP32); heading
  estimation is gated by **OD-13**.
* ``us`` distances stay in mm with the protocol's value semantics
  (0–4000 valid, 9999 no echo, −1 fault) exposed via helpers.
* ``mot`` / ``collect`` / ``balls`` / ``faults`` are stored verbatim —
  the ESP32 is authoritative for all of them (DEC-020).

The input is expected to be a protocol v1 ``TELE`` message dict.  The link
layer (``integration/communication/laptop_link.py``) already applies
``inbound_shape_ok`` before callbacks, but this module validates its own
required structure so it can be used standalone (and unit-tested) without
the link.  A structurally invalid input raises :class:`TelemetryFormatError`
— it is never silently coerced.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional, Tuple

# Protocol value semantics (COMMUNICATION_PROTOCOL.md §4.2)
US_VALID_MIN = 0
US_VALID_MAX = 4000
US_NO_ECHO = 9999
US_FAULT = -1

_US_LENGTH = 4  # fixed array order [front_left, front_right, left, right]


class TelemetryFormatError(ValueError):
    """Raised when a ``TELE`` dict does not have the protocol-required shape."""


def _require_number(value: Any, name: str, *, integer: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TelemetryFormatError(f"TELE.{name} must be a number")
    if integer:
        if not isinstance(value, int):
            raise TelemetryFormatError(f"TELE.{name} must be an integer")
        return float(value)
    result = float(value)
    if result != result or result in (float("inf"), float("-inf")):
        raise TelemetryFormatError(f"TELE.{name} must be finite")
    return result


def _require_string(value: Any, name: str) -> str:
    if not isinstance(value, str):
        raise TelemetryFormatError(f"TELE.{name} must be a string")
    return value


def _require_bool(value: Any, name: str) -> bool:
    if not isinstance(value, bool):
        raise TelemetryFormatError(f"TELE.{name} must be a bool")
    return value


@dataclass(frozen=True)
class ImuSample:
    """Raw IMU readings (MPU6050, ESP32-scaled, unfiltered) per protocol §4.2.

    ``yaw`` is the optional field the ESP32 may populate only if Person 5
    later adds onboard heading estimation (open decision OD-13) — it is
    ``None`` unless present on the wire.
    """

    ax: float
    ay: float
    az: float
    gx: float
    gy: float
    gz: float
    yaw: Optional[float] = None


@dataclass(frozen=True)
class MotSample:
    """Motor state per protocol §4.2: commanded duty and measured speed."""

    pwm_l: int
    pwm_r: int
    spd_l: float
    spd_r: float


@dataclass(frozen=True)
class TelemetrySnapshot:
    """Typed, raw view of one protocol ``TELE`` frame.

    Field semantics are exactly those of ``COMMUNICATION_PROTOCOL.md``
    §4.2.  Encoder values are raw counts (no meter conversion — OD-12);
    ultrasonic values are raw mm (helpers below expose the protocol's
    value classes).
    """

    seq: int
    ts: Optional[int]
    enc_dl: int
    enc_dr: int
    us: Tuple[int, int, int, int]
    imu: ImuSample
    mot: MotSample
    collect_trip: bool
    collect_state: str
    balls_count: int
    balls_full: bool
    faults: Tuple[int, ...]

    # -- protocol value semantics (COMMUNICATION_PROTOCOL.md §4.2) ---------

    def us_status(self, index: int) -> str:
        """Value class of ``us[index]``: ``"valid"`` | ``"no_echo"`` | ``"fault"``."""
        value = self.us[index]
        if value == US_FAULT:
            return "fault"
        if value == US_NO_ECHO:
            return "no_echo"
        return "valid"

    @property
    def has_us_fault(self) -> bool:
        """True if any ultrasonic slot reports a sensor fault (``-1``)."""
        return any(v == US_FAULT for v in self.us)


def from_telemetry(msg: Mapping[str, Any]) -> TelemetrySnapshot:
    """Build a :class:`TelemetrySnapshot` from a protocol ``TELE`` dict.

    Validates the protocol-required structure of §4.2 and raises
    :class:`TelemetryFormatError` on a malformed frame.  Value ranges are
    **not** re-checked: the ESP32 is authoritative for telemetry values and
    consumers decide how to treat unusual readings (only the *structure* is
    protocol-required).
    """
    if not isinstance(msg, Mapping):
        raise TelemetryFormatError("TELE message must be a mapping")

    enc = msg.get("enc")
    us = msg.get("us")
    imu_raw = msg.get("imu")
    mot_raw = msg.get("mot")
    collect = msg.get("collect")
    balls = msg.get("balls")
    faults = msg.get("faults")

    if not isinstance(enc, Mapping) or "dl" not in enc or "dr" not in enc:
        raise TelemetryFormatError("TELE.enc must contain dl and dr")
    if not isinstance(us, (list, tuple)) or len(us) != _US_LENGTH:
        raise TelemetryFormatError(
            f"TELE.us must be an array of {_US_LENGTH} ints "
            f"(order [front_left, front_right, left, right])"
        )
    if not isinstance(imu_raw, Mapping):
        raise TelemetryFormatError("TELE.imu must be an object")
    if not isinstance(mot_raw, Mapping):
        raise TelemetryFormatError("TELE.mot must be an object")
    if not isinstance(collect, Mapping) or "trip" not in collect or "state" not in collect:
        raise TelemetryFormatError("TELE.collect must contain trip and state")
    if not isinstance(balls, Mapping) or "count" not in balls or "full" not in balls:
        raise TelemetryFormatError("TELE.balls must contain count and full")
    if not isinstance(faults, (list, tuple)):
        raise TelemetryFormatError("TELE.faults must be an array")

    seq = msg.get("seq")
    if isinstance(seq, bool) or not isinstance(seq, int):
        raise TelemetryFormatError("TELE.seq must be an int")
    ts = msg.get("ts")
    if ts is not None and (isinstance(ts, bool) or not isinstance(ts, int)):
        raise TelemetryFormatError("TELE.ts must be an int or absent")

    us_values = []
    for i, value in enumerate(us):
        if isinstance(value, bool) or not isinstance(value, int):
            raise TelemetryFormatError(f"TELE.us[{i}] must be an integer")
        us_values.append(value)
    us_values = tuple(us_values)  # snapshot field is a fixed-length tuple

    imu = ImuSample(
        ax=_require_number(imu_raw.get("ax"), "imu.ax"),
        ay=_require_number(imu_raw.get("ay"), "imu.ay"),
        az=_require_number(imu_raw.get("az"), "imu.az"),
        gx=_require_number(imu_raw.get("gx"), "imu.gx"),
        gy=_require_number(imu_raw.get("gy"), "imu.gy"),
        gz=_require_number(imu_raw.get("gz"), "imu.gz"),
        yaw=None if "yaw" not in imu_raw else _require_number(imu_raw["yaw"], "imu.yaw"),
    )

    mot = MotSample(
        pwm_l=int(_require_number(mot_raw.get("pwm_l"), "mot.pwm_l", integer=True)),
        pwm_r=int(_require_number(mot_raw.get("pwm_r"), "mot.pwm_r", integer=True)),
        spd_l=_require_number(mot_raw.get("spd_l"), "mot.spd_l"),
        spd_r=_require_number(mot_raw.get("spd_r"), "mot.spd_r"),
    )

    fault_values = []
    for i, code in enumerate(faults):
        if isinstance(code, bool) or not isinstance(code, int):
            raise TelemetryFormatError(f"TELE.faults[{i}] must be an integer")
        fault_values.append(code)

    return TelemetrySnapshot(
        seq=seq,
        ts=ts,
        enc_dl=int(_require_number(enc["dl"], "enc.dl", integer=True)),
        enc_dr=int(_require_number(enc["dr"], "enc.dr", integer=True)),
        us=us_values,
        imu=imu,
        mot=mot,
        collect_trip=_require_bool(collect["trip"], "collect.trip"),
        collect_state=_require_string(collect["state"], "collect.state"),
        balls_count=int(_require_number(balls["count"], "balls.count", integer=True)),
        balls_full=_require_bool(balls["full"], "balls.full"),
        faults=tuple(fault_values),
    )