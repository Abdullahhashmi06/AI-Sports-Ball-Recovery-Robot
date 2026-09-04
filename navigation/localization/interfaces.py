"""Pluggable pose-source interface for the localization state container.

The agreed docs assign robot pose estimation to Module 2
(``TEAM_INTERFACE_CONTRACT.md`` §7: "Robot pose (localization) — Module 2
(Person 3)") but leave the *sources* open: encoder odometry needs OD-12
(counts per meter) and heading needs OD-13 (IMU handling + theta
convention).  Until those resolve, no concrete source exists in this
package — only the interface real sources will plug into.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol, runtime_checkable

from .state import Pose


@dataclass(frozen=True)
class PoseSample:
    """One externally-produced pose estimate.

    ``source`` is a free-form label supplied by the producing source (e.g.
    ``"odometry"`` or ``"manual"`` in future code) — no source exists yet.
    ``at`` is a timestamp on the caller's clock (seconds); it is used only
    for staleness bookkeeping, never for sensor math.
    """

    pose: Pose
    source: str
    at: float


@runtime_checkable
class PoseSource(Protocol):
    """Anything that can produce a best-known pose sample on demand.

    A concrete implementation (odometry/IMU fusion) will be added after the
    hardware-gated open decisions OD-12/OD-13 are resolved; the container
    in ``estimator.py`` only requires this protocol.
    """

    def sample(self) -> Optional[PoseSample]:
        """Return the current best-known pose, or ``None`` if unavailable."""
        ...