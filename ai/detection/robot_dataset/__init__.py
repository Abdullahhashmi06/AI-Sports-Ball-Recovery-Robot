"""Robot dataset infrastructure (software-only tooling for Module 1).

Utilities for building the future robot-specific ball dataset from raw robot
footage. This package is **tooling only** — no camera model, no robot geometry,
no thresholds describing the robot, and it resolves no OD-01…OD-16. It exists
so that when hardware arrives, the team can go from raw recordings to a
validated, provenance-tracked, deduplicated YOLO dataset without inventing
processes ad hoc.

Modules:
* ``frames``       — frame extraction from videos + duplicate/near-duplicate filtering
* ``validate``     — YOLO label validation (boxes normalized, in-bounds, files pair with images)
* ``split``        — deterministic seeded train/val/test splitting
* ``provenance``   — dataset provenance metadata records
* ``audit``        — whole-dataset audit: contamination, session leakage, suspicious boxes

See ``ai/detection/README.md`` §5 for the labeling guide and the mandatory
separation between public-data validation and real-robot validation.
"""

from .audit import audit_dataset, audit_split
from .frames import extract_frames, filter_near_duplicates, frame_hash
from .provenance import write_provenance
from .split import split_dataset
from .validate import validate_dataset

__all__ = [
    "audit_dataset",
    "audit_split",
    "extract_frames",
    "filter_near_duplicates",
    "frame_hash",
    "split_dataset",
    "validate_dataset",
    "write_provenance",
]
