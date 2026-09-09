"""Laptop-side robot state hub (integration layer, owned by Person 1).

One place that keeps the laptop's **current picture of the robot** from the
latest protocol ``TELE`` frame and the localization container, so modules
can consume it without each hand-wiring the link:

* telemetry is held **raw and authoritative** (DEC-020) — ``RobotSystemState``
  never converts encoder counts to meters (OD-12), never interprets IMU
  heading (OD-13), and never classifies fault codes (protocol §7 semantics
  belong to the ESP32 and the firmware);
* the best-known pose lives in the composed :class:`LocalizationEstimator`
  (``navigation.localization``) — it is only ever supplied by an external
  ``PoseSource`` (a future calibrated odometry/IMU source, or manual/test
  initialization); telemetry alone can never produce a pose here;
* freshness bookkeeping uses a caller-supplied clock and an explicit
  ``max_age_s`` budget — no cadence/staleness value is invented
  (protocol §8 rates and OD-05 latency are not re-decided here).

This module adds **no communication and no actuation** — it is a passive
state hub consumed by the coordination layer and by Module 2 later.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from navigation.localization.estimator import LocalizationEstimator
from navigation.localization.state import Pose
from navigation.localization.telemetry import TelemetrySnapshot
from navigation.planning.interfaces import ExitHint, NavigationInput
from navigation.planning.zones import ZoneId


class RobotSystemState:
    """Raw authoritative telemetry holder + localization container.

    Transitions are deterministic and driven by explicit inputs, so the hub
    is fully unit-testable without hardware.
    """

    def __init__(self) -> None:
        self.localization = LocalizationEstimator()
        self._snapshot: Optional[TelemetrySnapshot] = None
        self._at: Optional[float] = None
        self._telemetry_seq = 0

    # -- telemetry ----------------------------------------------------------

    @property
    def has_telemetry(self) -> bool:
        return self._snapshot is not None

    @property
    def snapshot(self) -> Optional[TelemetrySnapshot]:
        return self._snapshot

    @property
    def telemetry_at(self) -> Optional[float]:
        """Laptop-clock time (seconds) of the last applied frame."""
        return self._at

    @property
    def telemetry_seq(self) -> int:
        """Number of TELE frames applied so far (not the wire ``seq``)."""
        return self._telemetry_seq

    def set_telemetry(self, snapshot: TelemetrySnapshot, at: Optional[float] = None) -> None:
        """Adopt one validated telemetry snapshot (raw, authoritative).

        ``snapshot`` must already be a :class:`TelemetrySnapshot` (i.e. pass
        ``from_telemetry`` — malformed frames never reach the hub).  ``at``
        is the laptop-clock receipt time used only for freshness
        bookkeeping; ``None`` records no clock (freshness then reports
        conservatively stale).
        """
        if not isinstance(snapshot, TelemetrySnapshot):
            raise TypeError(
                f"set_telemetry requires a TelemetrySnapshot, got {type(snapshot).__name__}"
            )
        self._snapshot = snapshot
        self._at = at
        self._telemetry_seq += 1

    def invalidate_telemetry(self) -> None:
        """Drop the latest frame (e.g. after a link loss); nothing else."""
        self._snapshot = None
        self._at = None

    def telemetry_age(self, now: float) -> Optional[float]:
        """Seconds since the last frame on the caller's clock, or ``None``."""
        if self._at is None:
            return None
        return now - self._at

    def telemetry_fresh(self, now: float, max_age_s: float) -> bool:
        """True iff a frame exists and is at most ``max_age_s`` old.

        Without a recorded receipt time the answer is conservatively False
        (the hub never assumes freshness it cannot judge).
        """
        if self._snapshot is None or self._at is None:
            return False
        return now - self._at <= max_age_s

    # -- ESP32 faults (verbatim, ESP32-authoritative) -----------------------

    @property
    def faults_known(self) -> bool:
        """True once at least one TELE frame has been seen.

        Before any telemetry, ``faults`` is ``()`` only because there is no
        data — callers must treat that as *unknown*, not \"no faults\".
        """
        return self._snapshot is not None

    @property
    def faults(self) -> Tuple[int, ...]:
        """Fault codes from the latest frame (``()`` if none or unknown)."""
        if self._snapshot is None:
            return ()
        return self._snapshot.faults

    @property
    def has_faults(self) -> bool:
        return self.faults_known and bool(self.faults)

    # -- pose (external sources only — never derived from TELE) -------------

    @property
    def has_pose(self) -> bool:
        return self.localization.has_estimate

    @property
    def pose(self) -> Optional[Pose]:
        return self.localization.pose

    def set_pose(
        self,
        pose: Pose,
        source: str = "external",
        at: Optional[float] = None,
    ) -> None:
        """Adopt an externally-produced pose estimate.

        ``source`` is a provenance label; real calibrated sources (OD-12 /
        OD-13) will arrive through the estimator's ``PoseSource`` interface
        or this setter.  This hub never synthesizes a pose from telemetry.
        """
        self.localization.set_pose(pose, source=source, at=at)

    def clear_pose(self) -> None:
        """Drop the pose estimate (robot is unlocated)."""
        self.localization.invalidate()

    # -- Module 2 input assembly --------------------------------------------

    def navigation_input(
        self,
        *,
        target: Any = None,
        zone: Optional[ZoneId] = None,
        hint: Optional[ExitHint] = None,
        useful_confidence_threshold: Optional[float] = None,
    ) -> NavigationInput:
        """Build a :class:`NavigationInput` from current state + objective.

        ``target``/``zone``/``hint`` come from the coordinator (state-machine
        task) and Module 1 (I-1 exit hint).  The pose comes from the
        localization container and may legitimately be ``None`` — the
        planner answers ``NO_POSE`` rather than guessing.
        """
        return NavigationInput(
            pose=self.localization.pose,
            target=target,
            zone=zone,
            hint=hint,
            useful_confidence_threshold=useful_confidence_threshold,
        )


__all__ = ["RobotSystemState"]
