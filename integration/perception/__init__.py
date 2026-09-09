"""Synthetic Perception Foundation (integration layer, owned by Person 2 / Person 1 seam).

Provides declarative, software-only models for ball and obstacle observations,
a narrow validation boundary for perception payloads, and perception -> objective seams.
"""

from .model import BallObservation, ObstacleObservation, PerceptionFrame
from .seams import derive_objective_from_perception
from .validation import PerceptionValidationError, validate_perception_payload

__all__ = [
    "BallObservation",
    "ObstacleObservation",
    "PerceptionFrame",
    "PerceptionValidationError",
    "derive_objective_from_perception",
    "validate_perception_payload",
]
