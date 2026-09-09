"""Perception -> Objective Seam.

Transforms validated :class:`PerceptionFrame` observations + ApplicationStateMachine
mission context into declarative :class:`TaskObjective` targets.

Crucial constraint:
Perception observations NEVER directly create a CommandDirective or bypass
the command boundary / motion gate / LaptopLink. They produce an objective
which is fed into the existing navigation planning pipeline.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from navigation.localization.state import WorldPoint

from .model import PerceptionFrame

if TYPE_CHECKING:  # runtime import below (cycle-free; see note)
    from integration.system.state_machine import MissionState

# NOTE: integration.system/__init__ eagerly imports application.py, which
# imports this module — a module-level ``from integration.system.state_machine
# import MissionState`` here created a circular import that crashed whenever
# any ``integration.perception`` module was imported BEFORE any
# ``integration.system`` module. The runtime import inside the function keeps
# both import orders working.


def derive_objective_from_perception(
    frame: PerceptionFrame,
    mission: MissionState,
    *,
    explicit_task: Optional[Any] = None,
    base_location: Optional[WorldPoint] = None,
) -> Any:
    """Derive a declarative TaskObjective from a PerceptionFrame + MissionState.

    Rules & Gating:
    * Explicit task overrides perception derivation.
    * IDLE -> TaskType.HOLD
    * NAVIGATING ->
        - If ball visible & position set: GO_TO_TARGET(target=ball.position)
        - Else if exit_hint: SEARCH_ZONE(zone=hint.exit_zone, target=hint.estimated_position)
        - Else: TaskType.NO_OBJECTIVE
    * SEARCHING ->
        - If ball visible & position set: GO_TO_TARGET(target=ball.position)
        - Else if exit_hint: SEARCH_ZONE(zone=hint.exit_zone, target=hint.estimated_position)
        - Else: TaskType.NO_OBJECTIVE (OD-03 blocks wire-level search directive generation)
    * APPROACHING ->
        - If ball visible & position set: APPROACH_TARGET(target=ball.position)
        - Else: TaskType.NO_OBJECTIVE (OD-01 open)
    * COLLECTING -> TaskType.NO_OBJECTIVE (OD-04 open)
    * RETURNING ->
        - If base_location set: RETURN_TO_BASE(target=base_location)
        - Else: TaskType.NO_OBJECTIVE
    """
    from integration.system.application import TaskObjective, TaskType
    # Late import: avoids the integration.system <-> integration.perception
    # circular import at module-load time (see module note).
    from integration.system.state_machine import MissionState

    if explicit_task is not None:
        return explicit_task

    if not isinstance(mission, MissionState):
        raise TypeError(f"mission must be a MissionState, got {type(mission).__name__}")

    if mission is MissionState.IDLE:
        return TaskObjective(TaskType.HOLD, source="perception_seam:idle")

    if mission is MissionState.NAVIGATING:
        if frame.ball is not None and frame.ball.visible and frame.ball.position is not None:
            return TaskObjective(
                TaskType.GO_TO_TARGET,
                target=frame.ball.position,
                source="perception_ball",
                metadata={"confidence": frame.ball.confidence},
            )
        if frame.exit_hint is not None:
            return TaskObjective(
                TaskType.SEARCH_ZONE,
                target=frame.exit_hint.estimated_position,
                zone=frame.exit_hint.exit_zone,
                source="perception_exit_hint",
                metadata={"confidence": frame.exit_hint.confidence},
            )
        return TaskObjective(TaskType.NO_OBJECTIVE, source="perception_seam:navigating_unspecified")

    if mission is MissionState.SEARCHING:
        if frame.ball is not None and frame.ball.visible and frame.ball.position is not None:
            return TaskObjective(
                TaskType.GO_TO_TARGET,
                target=frame.ball.position,
                source="perception_ball_found",
                metadata={"confidence": frame.ball.confidence},
            )
        if frame.exit_hint is not None:
            return TaskObjective(
                TaskType.SEARCH_ZONE,
                target=frame.exit_hint.estimated_position,
                zone=frame.exit_hint.exit_zone,
                source="perception_exit_hint",
                metadata={"confidence": frame.exit_hint.confidence},
            )
        return TaskObjective(
            TaskType.NO_OBJECTIVE,
            source="perception_seam:searching_od03_blocked",
            metadata={"note": "OD-03 blocks wire search directive generation without ball or exit hint"},
        )

    if mission is MissionState.APPROACHING:
        if frame.ball is not None and frame.ball.visible and frame.ball.position is not None:
            return TaskObjective(
                TaskType.APPROACH_TARGET,
                target=frame.ball.position,
                source="perception_ball_approach",
                metadata={"confidence": frame.ball.confidence},
            )
        return TaskObjective(
            TaskType.NO_OBJECTIVE,
            source="perception_seam:approaching_od01_blocked",
            metadata={"note": "OD-01 ball position / approach threshold open"},
        )

    if mission is MissionState.COLLECTING:
        return TaskObjective(
            TaskType.NO_OBJECTIVE,
            source="perception_seam:collecting_od04_blocked",
            metadata={"note": "OD-04 intake/approach pose open"},
        )

    if mission is MissionState.RETURNING:
        if base_location is not None:
            return TaskObjective(
                TaskType.RETURN_TO_BASE,
                target=base_location,
                source="perception_seam:returning",
            )
        return TaskObjective(TaskType.NO_OBJECTIVE, source="perception_seam:returning_no_base")

    return TaskObjective(TaskType.NO_OBJECTIVE, source="perception_seam:unknown")


__all__ = ["derive_objective_from_perception"]
