"""ai/detection — Ball Detection V1 (real trainable ML detector, Module 1).

Public surface:
* :class:`BallDetection` — one image-space detector output.
* :class:`BallDetector` — the seam every backend (ultralytics, fakes) satisfies.
* :class:`UltralyticsBallDetector` — production backend (lazy ultralytics import).
* :func:`bbox_center` — pure bbox-corner → center math.
* :func:`detections_to_perception_payload` — detector output → raw perception payload.
* :func:`payload_to_frame` — payload through the existing validation boundary.

Importing this package requires only the standard library + yaml (via
``common``); ultralytics/torch are needed only by the production backend and
training/evaluation scripts. Committed unit tests run without them.
"""

from .inference import (
    BALL_CLASS_NAME,
    BallDetection,
    BallDetector,
    DetectionError,
    UltralyticsBallDetector,
    bbox_center,
    detections_to_perception_payload,
    payload_to_frame,
)

__all__ = [
    "BALL_CLASS_NAME",
    "BallDetection",
    "BallDetector",
    "DetectionError",
    "UltralyticsBallDetector",
    "bbox_center",
    "detections_to_perception_payload",
    "payload_to_frame",
]
