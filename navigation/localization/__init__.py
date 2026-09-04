"""Localization submodule: world-frame state representation and a
deterministic, calibration-free state container.

What is implemented (agreed, codeable per ``TEAM_INTERFACE_CONTRACT.md``
§6.1): world-frame types (``WorldPoint``, ``Pose``), pure world-frame
geometry, a best-known-pose container with pluggable future pose sources,
and a raw telemetry bridge (``TelemetrySnapshot``).

What is deliberately NOT implemented here: encoder-count → meters
conversion (gated by **OD-12**), heading/IMU fusion and the ``theta``
convention (gated by **OD-13**), and any kinematics/pose prediction —
no calibration constants exist in this package.
"""

from .state import Pose, WorldPoint, world_bearing, world_distance
from .interfaces import PoseSample, PoseSource
from .estimator import LocalizationEstimator
from .telemetry import (
    ImuSample,
    MotSample,
    TelemetryFormatError,
    TelemetrySnapshot,
    from_telemetry,
)

__all__ = [
    "Pose",
    "WorldPoint",
    "world_bearing",
    "world_distance",
    "PoseSample",
    "PoseSource",
    "LocalizationEstimator",
    "ImuSample",
    "MotSample",
    "TelemetryFormatError",
    "TelemetrySnapshot",
    "from_telemetry",
]