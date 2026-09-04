"""SIMULATION / TEST ONLY — synthetic protocol ``TELE`` fixtures.

These dicts are *shaped like* real ESP32 telemetry (protocol §4.2) but are
invented test data.  They must never be mistaken for real hardware
measurements: no encoder count, distance, IMU value, or ball count below
represents any physical robot.  Real telemetry arrives at runtime from the
link layer (``integration/communication``); this module exists only so the
localization bridge can be unit-tested without hardware.
"""

from __future__ import annotations

from typing import Any, Dict, Optional


def sample_telemetry(
    *,
    seq: int = 88,
    ts: Optional[int] = 44050,
    enc_dl: int = 14,
    enc_dr: int = 13,
    us: tuple = (350, 340, 9999, 410),
    ax: float = 0.02,
    ay: float = 0.01,
    az: float = 0.99,
    gx: float = 0.5,
    gy: float = -0.2,
    gz: float = 1.4,
    pwm_l: int = 45,
    pwm_r: int = 44,
    spd_l: float = 44.2,
    spd_r: float = 43.8,
    trip: bool = False,
    collect_state: str = "idle",
    balls_count: int = 3,
    balls_full: bool = False,
    faults: tuple = (),
    include_yaw: bool = False,
) -> Dict[str, Any]:
    """Build one synthetic protocol-v1 ``TELE`` dict (SIMULATION / TEST ONLY)."""
    imu: Dict[str, Any] = {
        "ax": ax, "ay": ay, "az": az,
        "gx": gx, "gy": gy, "gz": gz,
    }
    if include_yaw:
        imu["yaw"] = 12.5
    return {
        "v": 1,
        "type": "TELE",
        "seq": seq,
        "ts": ts,
        "enc": {"dl": enc_dl, "dr": enc_dr},
        "us": list(us),
        "imu": imu,
        "mot": {
            "pwm_l": pwm_l, "pwm_r": pwm_r,
            "spd_l": spd_l, "spd_r": spd_r,
        },
        "collect": {"trip": trip, "state": collect_state},
        "balls": {"count": balls_count, "full": balls_full},
        "faults": list(faults),
    }