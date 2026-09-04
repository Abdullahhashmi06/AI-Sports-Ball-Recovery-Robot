"""World-frame coordinate types and pure geometry.

Implements only the **agreed coordinate conventions** of
``docs/TEAM_INTERFACE_CONTRACT.md`` §6.1 (recorded as DEC-008 … DEC-015 in
``docs/DECISION_LOG.md``):

* fixed global 2D Cartesian world frame, origin does not move with the robot
* origin (0, 0) = the physical court corner nearest HOME/BASE
* ``+X`` along the 20 m court length, ``+Y`` across the 10 m court width
* units are meters
* robot pose is ``(x, y, theta)``; ball position is ``(x, y)``
* negative coordinates are valid
* the frame is independent of the operating/recovery-area size (no world
  boundary is part of this definition)

``theta`` is stored **verbatim**: the docs agree that the pose has a theta
component but do **not** define its units or zero-direction.  That
convention belongs to the IMU/heading open decision (**OD-13**), so this
module applies no range check, no wrap-around, and no normalization to
``theta`` — it is validated only as a finite number.

The pure functions here operate in the world frame only.  Nothing in this
module converts encoder counts, wheel speeds, or any physical quantity
into meters (gated by **OD-12**), and nothing interprets the robot's
heading (gated by **OD-13**).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Tuple, Union

Number = Union[int, float]


def _finite_number(value: object, name: str) -> float:
    """Coerce to a finite float, raising ``ValueError`` otherwise.

    Booleans are rejected (``bool`` is an ``int`` subclass but never a
    valid coordinate).  Infinity/NaN are rejected.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number, got {type(value).__name__}")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite, got {result!r}")
    return result


@dataclass(frozen=True)
class WorldPoint:
    """A position ``(x, y)`` in meters in the agreed world frame.

    ``x`` and ``y`` may be any finite value — negative coordinates are
    valid (DEC-014).  No range is enforced: the world frame has no
    hard-coded boundary (DEC-015).
    """

    x: float
    y: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "x", _finite_number(self.x, "x"))
        object.__setattr__(self, "y", _finite_number(self.y, "y"))

    def distance_to(self, other: "WorldPoint") -> float:
        """Euclidean distance to another world point, in meters."""
        return world_distance(self, other)

    def bearing_to(self, other: "WorldPoint") -> float:
        """World-frame bearing toward ``other``, radians from the +X axis.

        Convention: 0 = +X (along the court length), +pi/2 = +Y (across the
        court width), measured counter-clockwise viewed from above.  This is
        a pure function of the agreed world axes — it does **not** depend on
        the robot's heading/``theta`` convention (OD-13) and must not be
        used as a steering demand until that convention is agreed.
        """
        return world_bearing(self, other)

    def as_tuple(self) -> Tuple[float, float]:
        return (self.x, self.y)


@dataclass(frozen=True)
class Pose:
    """Robot pose ``(x, y, theta)`` in the world frame (DEC-013).

    ``theta`` is stored verbatim: units and zero-direction are **not
    agreed yet** (they belong to OD-13 / IMU-heading).  It is validated
    only as a finite number; no range or normalization is applied.
    """

    x: float
    y: float
    theta: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "x", _finite_number(self.x, "x"))
        object.__setattr__(self, "y", _finite_number(self.y, "y"))
        object.__setattr__(self, "theta", _finite_number(self.theta, "theta"))

    def position(self) -> WorldPoint:
        """The pose's position as a ``WorldPoint`` (drops theta)."""
        return WorldPoint(self.x, self.y)

    def as_tuple(self) -> Tuple[float, float, float]:
        return (self.x, self.y, self.theta)


def world_distance(a: WorldPoint, b: WorldPoint) -> float:
    """Euclidean distance between two world points, in meters."""
    return math.hypot(b.x - a.x, b.y - a.y)


def world_bearing(from_point: WorldPoint, to_point: WorldPoint) -> float:
    """World-frame bearing from ``from_point`` to ``to_point``.

    Returns radians in ``(-pi, pi]`` measured from the +X axis toward +Y
    (counter-clockwise viewed from above).  See :meth:`WorldPoint.bearing_to`
    for the convention note.
    """
    return math.atan2(to_point.y - from_point.y, to_point.x - from_point.x)