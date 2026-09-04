"""Deterministic planning for the laptop-side navigation foundation.

``plan_navigation`` decides the *objective and movement intent* from a
:class:`NavigationInput`.  It is deliberately a pure function with no
sensor math, no tuning constants, and no randomness — fully testable
without hardware.

Decision rules (all traceable to the agreed documents):

* No robot pose -> ``NO_POSE`` (the robot must be localized before any
  movement objective; pose estimation is Module 2's job per
  ``TEAM_INTERFACE_CONTRACT.md`` §7, but there is no estimate yet).
* No target, no zone, no hint -> ``NO_OBJECTIVE`` / ``HOLD``.
* Explicit target -> ``GO_TO_TARGET`` with world-frame geometry only
  (distance + bearing); no ``lin``/``ang`` (OD-07, OD-13).
* Zone directed without a target -> ``SEARCH_ZONE``; if the zone has no
  configured bounds we report ``ZONE_UNCONFIGURED`` rather than inventing
  dimensions (DEC-018).
* Exit hint (I-1) -> its ``exit_zone`` becomes the search zone and its
  estimated position becomes the search *start hint*; the focus
  (around-estimate vs. systematic) is classified with the caller-supplied
  §6.4 calibration threshold.
"""

from __future__ import annotations

from ..localization.state import world_bearing, world_distance
from .interfaces import (
    ExitHint,
    MovementIntent,
    MovementKind,
    NavigationInput,
    NavigationOutput,
    NavigationStatus,
    Objective,
)
from .zones import ZoneConfig, search_focus


def _intent_stop(reason: str) -> NavigationOutput:
    return NavigationOutput(
        status=NavigationStatus.PLANNED,
        objective=Objective.HOLD,
        reason=reason,
        movement=MovementIntent(MovementKind.STOP),
    )


def plan_navigation(
    nav_in: NavigationInput,
    zones: object = (),
) -> NavigationOutput:
    """Produce one deterministic planning decision.

    ``zones`` is an iterable of :class:`ZoneConfig` used only to check
    whether a directed zone has physical bounds (venue configuration).

    Return-to-base is expressed by the caller as ``target`` = the base's
    world point — the base's coordinates are venue configuration, not
    something this package invents.
    """

    # -- invalid / missing inputs ------------------------------------------

    if nav_in.pose is None:
        return NavigationOutput(
            status=NavigationStatus.NO_POSE,
            objective=Objective.HOLD,
            reason="no robot pose estimate (localization not initialized)",
        )

    target = nav_in.target
    zone = nav_in.zone
    hint = nav_in.hint

    if target is None and zone is None and hint is None:
        return NavigationOutput(
            status=NavigationStatus.NO_OBJECTIVE,
            objective=Objective.HOLD,
            reason="no target, zone, or exit hint provided",
            movement=MovementIntent(MovementKind.NONE),
        )

    # -- explicit target: geometry only --------------------------------------

    if target is not None:
        distance_m = world_distance(nav_in.pose.position(), target)
        bearing_world = world_bearing(nav_in.pose.position(), target)
        return NavigationOutput(
            status=NavigationStatus.PLANNED,
            objective=Objective.GO_TO_TARGET,
            reason="target given; geometry computed (lin/ang mapping deferred "
                   "to OD-07/OD-13)",
            movement=MovementIntent(
                MovementKind.MOVE,
                bearing_world=bearing_world,
                distance_m=distance_m,
            ),
            target=target,
            zone=zone,
        )

    # -- exit hint (I-1): zone + search start hint ---------------------------

    if hint is not None:
        focus = search_focus(
            has_estimated_position=True,
            confidence=hint.confidence,
            useful_threshold=nav_in.useful_confidence_threshold,
        )
        zone = zone or hint.exit_zone
        if not _zone_configured(zone, zones):
            return NavigationOutput(
                status=NavigationStatus.ZONE_UNCONFIGURED,
                objective=Objective.SEARCH_ZONE,
                reason=(
                    f"exit hint names {zone.value} but no bounds are configured "
                    "(DEC-018: zone extents are venue configuration)"
                ),
                target=hint.estimated_position,
                zone=zone,
                movement=MovementIntent(MovementKind.NONE),
            )
        return NavigationOutput(
            status=NavigationStatus.PLANNED,
            objective=Objective.SEARCH_ZONE,
            reason=(
                f"search {zone.value}, focus={focus.value}; estimated position is "
                "a search hint, not ground truth (I-1/DEC-019)"
            ),
            movement=MovementIntent(MovementKind.MOVE),
            target=hint.estimated_position,
            zone=zone,
        )

    # -- zone only ------------------------------------------------------------

    assert zone is not None  # covered above: target/hint/zone at least one set
    if not _zone_configured(zone, zones):
        return NavigationOutput(
            status=NavigationStatus.ZONE_UNCONFIGURED,
            objective=Objective.SEARCH_ZONE,
            reason=(
                f"zone {zone.value} has no configured bounds (DEC-018); "
                "cannot anchor a search yet"
            ),
            zone=zone,
            movement=MovementIntent(MovementKind.NONE),
        )
    return NavigationOutput(
        status=NavigationStatus.PLANNED,
        objective=Objective.SEARCH_ZONE,
        reason=f"directed to search {zone.value}",
        movement=MovementIntent(MovementKind.MOVE),
        zone=zone,
    )


def _zone_configured(zone: object, zones: object) -> bool:
    """True if any ZoneConfig matches ``zone`` with physical bounds set."""
    for cfg in zones:
        if isinstance(cfg, ZoneConfig) and cfg.zone == zone and cfg.configured:
            return True
    return False