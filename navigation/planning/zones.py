"""Recovery zones, operating-area configuration, and search-hint helpers.

All vocabulary is frozen by ``docs/TEAM_INTERFACE_CONTRACT.md`` §6.2–§6.4
(DEC-016 … DEC-019): the four logical zones, the configurable operating
area, and the exit search hint.  No physical dimensions are defined or
invented here — a zone/area without configured bounds simply has none.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Optional

from ..localization.state import WorldPoint, _finite_number


class ZoneId(enum.Enum):
    """The four agreed logical recovery/search zones (DEC-018)."""

    WEST = "WEST"
    EAST = "EAST"
    NORTH = "NORTH"
    SOUTH = "SOUTH"

    def __str__(self) -> str:  # wire/interface vocabulary uses the bare name
        return self.value


class SearchFocus(enum.Enum):
    """Where a search should concentrate, per the agreed §6.4 behavior."""

    AROUND_ESTIMATE = "around_estimate"  # confidence is useful -> search around the hint
    SYSTEMATIC = "systematic"            # confidence is low -> broader/systematic search
    UNDECIDED = "undecided"              # no threshold configured yet (calibration param)


@dataclass(frozen=True)
class Rectangle:
    """Axis-aligned rectangle in the world frame, meters.

    ``min <= max`` is enforced.  Negative coordinates are valid (DEC-014).
    """

    x_min: float
    x_max: float
    y_min: float
    y_max: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "x_min", _finite_number(self.x_min, "x_min"))
        object.__setattr__(self, "x_max", _finite_number(self.x_max, "x_max"))
        object.__setattr__(self, "y_min", _finite_number(self.y_min, "y_min"))
        object.__setattr__(self, "y_max", _finite_number(self.y_max, "y_max"))
        if self.x_min > self.x_max or self.y_min > self.y_max:
            raise ValueError("Rectangle requires x_min <= x_max and y_min <= y_max")

    def contains(self, point: WorldPoint) -> bool:
        """Inclusive containment check (boundary counts as inside)."""
        return (
            self.x_min <= point.x <= self.x_max
            and self.y_min <= point.y <= self.y_max
        )


@dataclass(frozen=True)
class ZoneConfig:
    """One logical recovery zone with *optional* physical bounds.

    Bounds are venue configuration (DEC-018: "not permanently hard-coded
    physical rectangles") and default to ``None`` — an unconfigured zone
    can still be selected by name, but position-based lookup cannot use it.
    """

    zone: ZoneId
    bounds: Optional[Rectangle] = None

    @property
    def configured(self) -> bool:
        return self.bounds is not None


@dataclass(frozen=True)
class OperatingArea:
    """Configurable operating/recovery boundary (DEC-016/DEC-017).

    The docs define this as *venue configuration*, not part of the
    coordinate-system definition (DEC-015) — no default extents exist, so
    ``bounds`` is ``None`` until a venue supplies them.  The ~20 m outward
    target / 30 m+ extensibility lives in the venue's config, not here.
    """

    bounds: Optional[Rectangle] = None

    @property
    def configured(self) -> bool:
        return self.bounds is not None

    def contains(self, point: WorldPoint) -> bool:
        """True only if the area is configured and contains the point."""
        return self.bounds is not None and self.bounds.contains(point)


# --------------------------------------------------------------------------
# Selection helpers (pure, deterministic)
# --------------------------------------------------------------------------


def select_zone(exit_zone: ZoneId) -> ZoneId:
    """Direct label selection: the AI's ``exit_zone`` names the relevant zone.

    This is the primary routing path (I-1: ``exit_zone`` is part of the
    frozen payload) — it needs no physical boundaries.  Returns the same
    ``ZoneId``; the identity function exists to make the routing explicit
    and testable.
    """
    return exit_zone


def lookup_zone_at(point: WorldPoint, configs: object) -> Optional[ZoneId]:
    """Find the configured zone containing ``point``.

    ``configs`` is an iterable of :class:`ZoneConfig`.  Returns the zone id
    of the first configured zone whose bounds contain the point, or ``None``
    when no zone is configured / none matches.  Because zone geometry is
    venue configuration and may overlap in principle, this helper is only a
    convenience — zone *selection* for a task comes from the state machine +
    Module 2 targeting (``TEAM_INTERFACE_CONTRACT.md`` §7).
    """
    for cfg in configs:
        if cfg.configured and cfg.bounds.contains(point):
            return cfg.zone
    return None


def confidence_is_useful(confidence: float, threshold: Optional[float]) -> bool:
    """Classify a hint's confidence against an explicit ``useful`` threshold.

    §6.4: "What counts as 'useful' vs. 'low' confidence is a numeric
    threshold to be tuned during field testing — a calibration parameter,
    not an interface definition."  The threshold must therefore be supplied
    by the caller; with ``threshold=None`` the classification is undecided
    (``SearchFocus.UNDECIDED``) rather than guessed.
    """
    if threshold is None:
        return False  # undecided; see search_focus() for the honest mapping
    return float(confidence) >= float(threshold)


def search_focus(
    has_estimated_position: bool,
    confidence: float,
    useful_threshold: Optional[float],
) -> SearchFocus:
    """Map an exit hint to the agreed search behavior (§6.4).

    * no estimated position at all -> ``SYSTEMATIC`` (nothing to prioritize)
    * threshold configured and confidence >= threshold -> ``AROUND_ESTIMATE``
    * threshold configured and confidence < threshold -> ``SYSTEMATIC``
    * threshold not configured -> ``UNDECIDED`` (calibration parameter open)
    """
    if not has_estimated_position:
        return SearchFocus.SYSTEMATIC
    if useful_threshold is None:
        return SearchFocus.UNDECIDED
    if confidence_is_useful(confidence, useful_threshold):
        return SearchFocus.AROUND_ESTIMATE
    return SearchFocus.SYSTEMATIC