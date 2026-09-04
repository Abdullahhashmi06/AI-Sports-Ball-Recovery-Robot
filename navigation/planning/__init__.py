"""Planning submodule: recovery-zone configuration/selection and the
navigation abstraction.

Implements only what the agreed docs authorize:

* the four logical recovery zones ``WEST`` / ``EAST`` / ``NORTH`` / ``SOUTH``
  (``TEAM_INTERFACE_CONTRACT.md`` §6.3, DEC-018) — names fixed, physical
  extents configurable and unset by default;
* the exit search hint payload (``exit_zone``, ``estimated_ball_position``,
  ``confidence``) per §6.4 / interface I-1 (DEC-019) — a **search hint, not
  ground truth**;
* a deterministic ``plan_navigation`` abstraction producing a movement
  *intent* without any tuned command values.

What is deliberately NOT implemented here: search *patterns* and the
state-machine ↔ navigation directive set (gated by **OD-03** — I-3/I-4
stay indicative), obstacle avoidance (gated by **OD-02** / **OD-08**),
approach geometry (gated by **OD-01** / **OD-04**), and any ``lin``/``ang``
magnitudes (gated by **OD-07** speed calibration and **OD-13** heading).
"""

from .zones import (
    OperatingArea,
    Rectangle,
    SearchFocus,
    ZoneConfig,
    ZoneId,
    confidence_is_useful,
    lookup_zone_at,
    select_zone,
)
from .interfaces import (
    ExitHint,
    MovementIntent,
    MovementKind,
    NavigationError,
    NavigationInput,
    NavigationOutput,
    NavigationStatus,
    Objective,
)
from .planner import plan_navigation

__all__ = [
    "OperatingArea",
    "Rectangle",
    "SearchFocus",
    "ZoneConfig",
    "ZoneId",
    "confidence_is_useful",
    "lookup_zone_at",
    "select_zone",
    "ExitHint",
    "MovementIntent",
    "MovementKind",
    "NavigationError",
    "NavigationInput",
    "NavigationOutput",
    "NavigationStatus",
    "Objective",
    "plan_navigation",
]