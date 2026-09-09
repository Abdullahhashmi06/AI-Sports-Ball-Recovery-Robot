"""Ball Detection V1 inference — detector output → existing perception boundary.

The production seam for the trained detector:

    image
      → BallDetector.detect()            (backend behind a narrow seam)
      → List[BallDetection]              (bbox, confidence, image-space center)
      → detections_to_perception_payload()
      → integration.perception.validate_perception_payload()   (existing, unchanged)
      → PerceptionFrame

Hard constraints (enforced in code, not just documented):

* **Image space only.** No world coordinates, camera model, calibration, or
  distance can be derived from a 2D box, so none is. ``BallObservation.position``
  stays ``None`` (OD-01 remains open); image-space data travels only in the
  payload ``metadata`` (``ball_image``), preserved by the existing validation
  boundary's additive policy.
* **No transport, no commands.** This module produces validated perception
  payloads; it never touches LaptopLink, the command boundary, or the ESP32.
* The ultralytics backend is imported **lazily** — the committed unit tests run
  with a deterministic fake detector injected at the ``BallDetector`` seam and
  require no ML dependency at all.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Protocol

# The single label this detector is trained to emit (dataset fact, §README 4).
BALL_CLASS_NAME = "ball"

# Detection coordinates are quantized to whole pixels in the perception payload.
_PIXEL_QUANTUM = 1.0


class DetectionError(ValueError):
    """Raised when a detector backend returns structurally invalid output."""


@dataclass(frozen=True)
class BallDetection:
    """One detector output in **image space**.

    * ``x``/``y`` — ball center in pixels (origin: top-left of the image,
      +x right, +y down — the universal image convention; NOT a world frame).
    * ``width``/``height`` — bounding-box size in pixels.
    * ``confidence`` — detector score as supplied by the backend.
    """

    x: float
    y: float
    width: float
    height: float
    confidence: float

    def __post_init__(self) -> None:
        for name in ("x", "y", "width", "height", "confidence"):
            value = getattr(self, name)
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
                raise DetectionError(f"{name} must be a finite number")
        if self.width < 0 or self.height < 0:
            raise DetectionError("width/height must be non-negative")
        if not 0.0 <= self.confidence <= 1.0:
            raise DetectionError("confidence must be within [0, 1]")

    @property
    def center(self) -> Tuple[float, float]:
        """Bounding-box center (== the box center, not an area-weighted moment)."""
        return (self.x, self.y)


def bbox_center(
    x_min: float, y_min: float, x_max: float, y_max: float
) -> Tuple[float, float]:
    """Center of an axis-aligned bbox given its corners (pure, testable)."""
    for v in (x_min, y_min, x_max, y_max):
        if not math.isfinite(v):
            raise DetectionError("bbox corners must be finite")
    if x_max < x_min or y_max < y_min:
        raise DetectionError("bbox corners inverted (max < min)")
    return ((x_min + x_max) / 2.0, (y_min + y_max) / 2.0)


class BallDetector(Protocol):
    """Narrow seam every detector backend must satisfy."""

    def detect(self, image: Any) -> List[BallDetection]:
        """Return ball detections for one image (image space)."""
        ...


def _quantize_px(value: float) -> float:
    """Round to whole pixels (payload convention) and guard the range."""
    if value > 1e9 or value < -1e9:
        raise DetectionError("pixel coordinate out of representable range")
    return float(round(value / _PIXEL_QUANTUM))


class UltralyticsBallDetector:
    """``BallDetector`` backend wrapping a trained ultralytics YOLO model.

    Constructed with a path to trained weights (e.g. ``runs/ball_v1/weights/
    best.pt``). Import of ultralytics happens lazily inside ``detect`` so that
    this module can be imported (and unit-tested) on machines without it.
    """

    def __init__(
        self, weights: Path, *, confidence: float = 0.25, imgsz: Optional[int] = None
    ) -> None:
        """``imgsz`` optionally overrides the inference resolution (e.g. 640).
        ``None`` keeps the backend default (the checkpoint's training size). It
        is an *analysis/operating-point parameter*, not a robot requirement —
        see README §4.3 for the measured resolution/recall trade-off."""
        self._weights = Path(weights)
        self._confidence = float(confidence)
        if not 0.0 <= self._confidence <= 1.0:
            raise ValueError("confidence threshold must be within [0, 1]")
        if imgsz is not None and (not isinstance(imgsz, int) or isinstance(imgsz, bool) or imgsz <= 0):
            raise ValueError("imgsz must be a positive int or None")
        self._imgsz = imgsz

    def detect(self, image: Any) -> List[BallDetection]:
        try:
            from ultralytics import YOLO
        except ImportError as exc:  # pragma: no cover - environment error
            raise DetectionError(
                "ultralytics is not installed; install ai/detection/requirements.txt"
            ) from exc

        model = YOLO(str(self._weights))
        predict_kwargs: Dict[str, Any] = {"verbose": False, "conf": self._confidence}
        if self._imgsz is not None:
            predict_kwargs["imgsz"] = self._imgsz
        results = model.predict(image, **predict_kwargs)
        out: List[BallDetection] = []
        for result in results:
            names = result.names or {}
            boxes = result.boxes
            if boxes is None:
                continue
            for i in range(len(boxes)):
                cls_id = int(boxes.cls[i].item())
                label = str(names.get(cls_id, cls_id))
                if label != BALL_CLASS_NAME:
                    continue
                x_min, y_min, x_max, y_max = (float(v) for v in boxes.xyxy[i].tolist())
                conf = float(boxes.conf[i].item())
                cx, cy = bbox_center(x_min, y_min, x_max, y_max)
                out.append(
                    BallDetection(
                        x=_quantize_px(cx),
                        y=_quantize_px(cy),
                        width=_quantize_px(x_max - x_min),
                        height=_quantize_px(y_max - y_min),
                        confidence=conf,
                    )
                )
        return out


def detections_to_perception_payload(
    detections: Sequence[BallDetection],
    *,
    timestamp: Optional[float] = None,
    source: str = "ball_detection_v1",
    extra_metadata: Optional[Dict[str, Any]] = None,
    image_size: Optional[Tuple[int, int]] = None,
) -> Dict[str, Any]:
    """Convert detector output into a raw perception payload dict.

    The returned mapping is exactly what
    ``integration.perception.validate_perception_payload`` consumes. With no
    detections the payload has ``"ball": None`` (no ball observed). With
    detections, the highest-confidence one becomes ``ball`` with
    ``visible=True`` and ``position=None`` (image space only — OD-01 untouched).
    All detections (plus optional image size) travel in ``metadata["ball_image"]``
    / ``metadata["image_size"]`` for downstream consumers.
    """
    if image_size is not None:
        if len(image_size) != 2 or any(not isinstance(v, int) or v <= 0 for v in image_size):
            raise DetectionError("image_size must be a (width, height) pair of positive ints")

    meta: Dict[str, Any] = {"source": source}
    if extra_metadata:
        meta.update(extra_metadata)
    if image_size is not None:
        meta["image_size"] = {"width": image_size[0], "height": image_size[1]}
    if detections:
        meta["ball_image"] = {
            "detections": [
                {
                    "x": _quantize_px(d.x),
                    "y": _quantize_px(d.y),
                    "width": _quantize_px(d.width),
                    "height": _quantize_px(d.height),
                    "confidence": d.confidence,
                }
                for d in detections
            ]
        }

    ball: Optional[Dict[str, Any]] = None
    if detections:
        best = max(detections, key=lambda d: d.confidence)
        ball = {
            "visible": True,
            "position": None,  # image space only: no world frame without localization/calibration
            "confidence": best.confidence,
        }

    payload: Dict[str, Any] = {"ball": ball, "metadata_source": source}
    if timestamp is not None:
        payload["timestamp"] = timestamp
    # Extra keys are tolerated by the validation boundary and preserved in
    # PerceptionFrame.metadata; nested under a namespaced key to avoid clashes.
    payload["perception_metadata"] = meta
    return payload


def payload_to_frame(payload: Dict[str, Any]):
    """Convenience: run the payload through the EXISTING validation boundary.

    Kept as a thin wrapper so callers never bypass
    ``integration.perception.validate_perception_payload``. Raises
    ``PerceptionValidationError`` on malformed payloads.
    """
    from integration.perception.validation import validate_perception_payload

    return validate_perception_payload(payload)


def main(argv: Optional[Sequence[str]] = None) -> int:  # pragma: no cover - thin CLI
    import argparse

    p = argparse.ArgumentParser(description="Ball Detection V1 single-image inference")
    p.add_argument("--weights", type=Path, required=True, help="trained weights (best.pt)")
    p.add_argument("image", type=Path, help="image file to run detection on")
    p.add_argument("--confidence", type=float, default=0.25)
    p.add_argument("--imgsz", type=int, default=None, help="inference resolution override (e.g. 640)")
    args = p.parse_args(argv)

    detector: BallDetector = UltralyticsBallDetector(
        args.weights, confidence=args.confidence, imgsz=args.imgsz
    )
    dets = detector.detect(str(args.image))
    payload = detections_to_perception_payload(dets)
    import json

    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
