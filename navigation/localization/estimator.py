"""Deterministic localization state container.

Holds the **best-known robot pose** in the agreed world frame
(``TEAM_INTERFACE_CONTRACT.md`` §6.1) and records which source produced it
and when.  It is deliberately *not* an estimator in the sensor-fusion
sense:

* it applies **no kinematics** — encoder-count → meters conversion is
  gated by **OD-12**, and heading/IMU fusion plus the ``theta`` convention
  are gated by **OD-13**;
* it performs **no prediction, no noise model, and no calibration** — there
  are no physical constants anywhere in this module;
* every transition is deterministic and driven by explicit inputs, so it is
  fully testable without hardware.

Future real pose sources (odometry/IMU fusion, manual initialization) plug
in through :class:`~navigation.localization.interfaces.PoseSource`; nothing
about this container changes when they arrive.
"""

from __future__ import annotations

from typing import Optional

from .interfaces import PoseSample, PoseSource
from .state import Pose


class LocalizationEstimator:
    """Best-known-pose container with source provenance.

    ``pose`` is ``None`` until a valid sample is applied — the container
    never fabricates a pose.  ``source`` names the last provider; no
    concrete provider exists yet (OD-12/OD-13 gate the real ones).
    """

    def __init__(self) -> None:
        self._pose: Optional[Pose] = None
        self._source: Optional[str] = None
        self._at: Optional[float] = None
        self._source_obj: Optional[PoseSource] = None
        self._updates = 0
        self._invalidations = 0

    # -- read-only state ---------------------------------------------------

    @property
    def has_estimate(self) -> bool:
        return self._pose is not None

    @property
    def pose(self) -> Optional[Pose]:
        return self._pose

    @property
    def source(self) -> Optional[str]:
        """Label of the source that produced the current pose."""
        return self._source

    @property
    def updated_at(self) -> Optional[float]:
        """Timestamp (caller's clock) of the last applied sample."""
        return self._at

    @property
    def update_count(self) -> int:
        return self._updates

    @property
    def invalidation_count(self) -> int:
        return self._invalidations

    def age(self, now: float) -> Optional[float]:
        """Seconds since the last applied sample, or ``None`` if unset."""
        if self._at is None:
            return None
        return now - self._at

    def is_stale(self, now: float, max_age_s: float) -> bool:
        """True if no usable estimate exists or it is older than ``max_age_s``.

        ``max_age_s`` is a caller-supplied threshold — the docs do not fix a
        staleness budget (latency expectations are **OD-05**), so no default
        is invented here.
        """
        if self._pose is None or self._at is None:
            return True  # no estimate, or no timestamp to judge freshness by
        return now - self._at > max_age_s

    # -- transitions -------------------------------------------------------

    def set_pose(self, pose: Pose, source: str = "external", at: Optional[float] = None) -> None:
        """Adopt an externally-produced pose estimate.

        ``source`` is a provenance label (no source exists yet in this
        package).  Passing ``at=None`` records the update without a
        timestamp (``age``/``is_stale`` then report ``None``/``True``
        respectively — the caller decides when clocks are available).
        """
        self._pose = pose  # Pose dataclass validates (finite numbers)
        self._source = source
        self._at = at
        self._updates += 1

    def invalidate(self) -> None:
        """Drop the estimate (e.g. localization lost); the robot is unlocated.

        Deterministic: always leaves ``has_estimate`` False.  It does not
        invent a fallback pose.
        """
        self._pose = None
        self._source = None
        self._at = None
        self._invalidations += 1

    def reset(self) -> None:
        """Full reset: clear the estimate, provenance and any attached source."""
        self.invalidate()
        self._source_obj = None

    # -- plug-in source support ---------------------------------------------

    def attach(self, source: PoseSource) -> None:
        """Attach a future pose source (see :class:`PoseSource`)."""
        self._source_obj = source

    def poll(self, now: float) -> Optional[PoseSample]:
        """Ask the attached source for a sample and apply it if present.

        Returns the applied sample, or ``None`` when no source is attached
        or the source has no estimate.  No filtering/fusion is performed —
        the container keeps the latest provided sample (deterministic).
        """
        if self._source_obj is None:
            return None
        sample = self._source_obj.sample()
        if sample is None:
            return None
        self.set_pose(sample.pose, source=sample.source, at=now)
        return sample