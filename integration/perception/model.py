"""Declarative software-only synthetic perception models.

Owned by Module 1 / Perception integration seam. Models observations (WHAT is seen),
NOT robot commands, hardware calibration, sensor thresholds, or motor parameters.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple

from navigation.localization.state import WorldPoint
from navigation.planning.interfaces import ExitHint


@dataclass(frozen=True)
class BallObservation:
    """Observation of a ball in the world frame.

    * ``visible`` — whether the ball is currently visible to perception.
    * ``position`` — optional estimated position in meters in the world frame (§6.1).
    * ``velocity`` — optional estimated 2D velocity (vx, vy) in m/s in world frame.
    * ``confidence`` — finite confidence score (0.0 to 1.0 or raw confidence score).
    * ``timestamp`` — laptop clock time of the observation.
    """

    visible: bool
    position: Optional[WorldPoint] = None
    velocity: Optional[Tuple[float, float]] = None
    confidence: float = 0.0
    timestamp: float = 0.0

    def __post_init__(self) -> None:
        if self.position is not None and not isinstance(self.position, WorldPoint):
            raise TypeError("position must be a WorldPoint or None")


@dataclass(frozen=True)
class ObstacleObservation:
    """Observation of an obstacle in the world frame.

    * ``obstacle_type`` — label string (e.g. "person", "chair", "table", "bag", "wall").
    * ``world_region`` — declarative region descriptor (e.g. {"center": WorldPoint, ...} or dict).
    * ``confidence`` — finite confidence score.
    * ``timestamp`` — laptop clock time of the observation.
    """

    obstacle_type: str
    world_region: Dict[str, Any]
    confidence: float = 0.0
    timestamp: float = 0.0


@dataclass(frozen=True)
class PerceptionFrame:
    """Aggregated perception observation snapshot."""

    timestamp: float
    ball: Optional[BallObservation] = None
    obstacles: Tuple[ObstacleObservation, ...] = field(default_factory=tuple)
    exit_hint: Optional[ExitHint] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


__all__ = [
    "BallObservation",
    "ObstacleObservation",
    "PerceptionFrame",
]
