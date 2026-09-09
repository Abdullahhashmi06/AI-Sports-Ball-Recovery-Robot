"""YOLO label validation for the robot dataset.

Validates that a candidate dataset directory is internally consistent before
training: every image has a label file, every label file has an image, class
ids are within range, boxes are properly normalized and within bounds, and
box geometry is non-degenerate. Pure functions + a directory walker; no robot
or camera assumptions.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

IMAGE_EXTS = (".jpg", ".jpeg", ".png")


def validate_label_line(
    line: str,
    *,
    num_classes: int = 1,
    line_number: int = 0,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Validate one YOLO label line.

    Returns (record, error). Exactly one is None. A record is
    ``{"class": int, "cx": float, "cy": float, "w": float, "h": float}``.
    """
    parts = line.split()
    if len(parts) != 5:
        return None, f"line {line_number}: expected 5 fields, got {len(parts)}"
    try:
        cls_id = int(parts[0])
        cx, cy, w, h = (float(v) for v in parts[1:])
    except ValueError:
        return None, f"line {line_number}: non-numeric field"
    if not 0 <= cls_id < num_classes:
        return None, f"line {line_number}: class {cls_id} outside [0, {num_classes - 1}]"
    for name, v in (("cx", cx), ("cy", cy), ("w", w), ("h", h)):
        if not 0.0 <= v <= 1.0:
            return None, f"line {line_number}: {name}={v} outside [0, 1] (must be normalized)"
    if w <= 0.0 or h <= 0.0:
        return None, f"line {line_number}: degenerate box (w={w}, h={h})"
    # center must keep the box at least partially inside the image
    if cx - w / 2 >= 1.0 or cx + w / 2 <= 0.0 or cy - h / 2 >= 1.0 or cy + h / 2 <= 0.0:
        return None, f"line {line_number}: box entirely outside the image"
    return {"class": cls_id, "cx": cx, "cy": cy, "w": w, "h": h}, None


def validate_dataset(
    image_dir: Path,
    label_dir: Path,
    *,
    num_classes: int = 1,
) -> Dict[str, Any]:
    """Validate an image/label pair directory; return a machine-readable report.

    Checks: images without labels, labels without images, per-line schema
    errors, and per-file box statistics. Empty label files are legal (negative
   /background frames).
    """
    images = sorted(p for p in image_dir.iterdir() if p.suffix.lower() in IMAGE_EXTS)
    labels = sorted(p for p in label_dir.iterdir() if p.suffix == ".txt")
    image_stems = {p.stem for p in images}
    label_stems = {p.stem for p in labels}

    missing_labels = sorted(f"{p.name}" for p in images if p.stem not in label_stems)
    missing_images = sorted(f"{p.name}" for p in labels if p.stem not in image_stems)

    per_file: Dict[str, Any] = {}
    errors: List[str] = []
    total_boxes = 0
    empty_files = 0

    for lbl in labels:
        records: List[Dict[str, Any]] = []
        for i, line in enumerate(lbl.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            rec, err = validate_label_line(line, num_classes=num_classes, line_number=i)
            if err is not None:
                errors.append(f"{lbl.name}: {err}")
                continue
            records.append(rec)
        if not records:
            empty_files += 1
        per_file[lbl.name] = {"boxes": len(records), "records": records}
        total_boxes += len(records)

    return {
        "num_images": len(images),
        "num_labels": len(labels),
        "num_boxes": total_boxes,
        "num_empty_label_files": empty_files,
        "missing_labels": missing_labels,
        "missing_images": missing_images,
        "errors": errors,
        "ok": not errors and not missing_labels and not missing_images,
        "per_file": per_file,
    }


def main(argv: Optional[Sequence[str]] = None) -> int:  # pragma: no cover - thin CLI
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("image_dir", type=Path)
    p.add_argument("label_dir", type=Path)
    p.add_argument("--num-classes", type=int, default=1)
    args = p.parse_args(argv)

    report = validate_dataset(args.image_dir, args.label_dir, num_classes=args.num_classes)
    import json

    print(json.dumps({k: v for k, v in report.items() if k != "per_file"}, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
