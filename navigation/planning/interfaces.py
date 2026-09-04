"""Navigation abstraction: exit hint, input, output, movement intent.

These are **Module 2 internal** types (Person 3's own pipeline).  They are
NOT the cross-module I-3/I-4 payloads — the state-machine ↔ navigation
directive set stays *indicative* until open decision **OD-03** resolves,
and ``navigation_status`` shape may change with it.  This module implements
the deterministic, hardware-free layer the user-facing abstraction needs
now; anything governed by an open decision is explicitly absent.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Optional

from ..localization.state import Pose, WorldPoint, _finite_number
from .zones import ZoneId


class NavigationError(ValueError):
    """Raised when a navigation input/configuration is invalid."""


@dataclass(frozen=True)
class ExitHint:
    """AI → Navigation exit information (I-1, frozen per §6.4 / DEC-019).

    ``estimated_position`` is a **SEARCH HINT, not ground truth**; the
    useful-vs-low ``confidence`` threshold is a calibration parameter
    (see :func:`~navigation.planning.zones.search_focus`).
    """

    exit_zone: ZoneId
    estimated_position: WorldPoint
    confidence: float

    def __post_init__(self) -> None:
        if not isinstance(self.exit_zone, ZoneId):
            raise NavigationError("exit_zone must be a ZoneId (WEST/EAST/NORTH/SOUTH)")
        # WorldPoint validates its own fields; confidence only needs to be a
        # finite number — a numeric range would be deciding the calibration
        # parameter that §6.4 leaves to field testing.
        object.__setattr__(
            self, "confidence", _finite_number(self.confidence, "confidence")
        )


@dataclass(frozen=True)
class NavigationInput:
    """Everything Module 2 needs to produce one planning decision.

    * ``pose`` — best-known robot pose from localization (may be ``None``:
      the planner must then say it cannot plan, not guess).
    * ``target`` — a world-frame goal position (e.g. a ball, a zone anchor,
      the base).  ``None`` with no hint means "no objective".
    * ``zone`` — an explicitly directed recovery zone (state machine, I-3);
      used when no precise target is available yet.
    * ``hint`` — the AI exit search hint (I-1) that prioritizes the search.
    * ``useful_confidence_threshold`` — the §6.4 calibration parameter,
      supplied by configuration; ``None`` = undecided.
    """

    pose: Optional[Pose] = None
    target: Optional[WorldPoint] = None
    zone: Optional[ZoneId] = None
    hint: Optional[ExitHint] = None
    useful_confidence_threshold: Optional[float] = None

    def __post_init__(self) -> None:
        if self.target is not None and not isinstance(self.target, WorldPoint):
            raise NavigationError("target must be a WorldPoint or None")
        if self.zone is not None and not isinstance(self.zone, ZoneId):
            raise NavigationError("zone must be a ZoneId or None")
        if self.hint is not None and not isinstance(self.hint, ExitHint):
            raise NavigationError("hint must be an ExitHint or None")
        if self.useful_confidence_threshold is not None:
            object.__setattr__(
                self,
                "useful_confidence_threshold",
                _finite_number(self.useful_confidence_threshold, "useful_confidence_threshold"),
            )


class NavigationStatus(enum.Enum):
    """Outcome of a planning attempt (deterministic)."""

    PLANNED = "planned"                    # objective + movement intent decided
    HOLD = "hold"                          # deliberately not moving
    NO_POSE = "no_pose"                    # no robot pose estimate (localization)
    NO_OBJECTIVE = "no_objective"          # no target, zone, or hint provided
    ZONE_UNCONFIGURED = "zone_unconfigured"  # zone has no physical bounds yet
    INVALID_INPUT = "invalid_input"        # caller violated the interface


class Objective(enum.Enum):
    """Module 2 internal movement objective (not the I-3 directive set).

    ``RETURN_TO_BASE`` is expressed as ``GO_TO_TARGET`` with the caller
    supplying the base as a world point — the base's coordinates are venue
    configuration, not something this package invents.
    """

    HOLD = "hold"
    GO_TO_TARGET = "go_to_target"
    SEARCH_ZONE = "search_zone"


class MovementKind(enum.Enum):
    """What (if anything) the robot should be commanded to do.

    Only the *kind* is decided here.  Concrete ``lin``/``ang`` values are
    deliberately absent: mapping an objective to normalized velocities
    requires the speed calibration of **OD-07** and the heading convention
    of **OD-13**.  ``MOVE`` below therefore means "the navigation loop
    should produce a CMD_MOVE stream" — the values are a later,
    hardware-gated step.
    """

    NONE = "none"        # send no movement command
    MOVE = "move"        # intent to send CMD_MOVE (values TBD by OD-07/OD-13)
    STOP = "stop"        # intent to send CMD_STOP (mode normal)


@dataclass(frozen=True)
class MovementIntent:
    """Desired movement command *kind* plus planning context.

    ``bearing_world`` and ``distance_m`` (when present) are world-frame
    geometry — pure functions of the target and pose, safe to compute
    today.  They are information for the future motion generator, not
    steering demands.
    """

    kind: MovementKind
    bearing_world: Optional[float] = None  # radians from +X (see WorldPoint.bearing_to)
    distance_m: Optional[float] = None     # meters


@dataclass(frozen=True)
class NavigationOutput:
    """One deterministic planning result."""

    status: NavigationStatus
    objective: Objective
    reason: str
    movement: MovementIntent = field(default_factory=lambda: MovementIntent(MovementKind.NONE))
    target: Optional[WorldPoint] = None      # decided target (hint position, etc.)
    zone: Optional[ZoneId] = None            # relevant recovery zone, if any