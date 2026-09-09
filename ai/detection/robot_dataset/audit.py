"""Dataset-level audit for the robot ball dataset.

Sits on top of ``validate`` (per-directory label schema checks) and answers the
questions a *whole dataset* audit must ask before training:

* **contamination** — the same frame (same stem, or pixel-level near-duplicate)
  appearing in more than one split;
* **session leakage** — one recording session straddling train/val/test;
* **suspicious boxes** — degenerately tiny or implausibly huge boxes, flagged
  for human review (never silently deleted);
* **tiny-ball evidence** — the size distribution of labeled balls, so the
  dataset can be checked against the detector's known small-ball weakness;
* **class-id errors, malformed labels, out-of-bounds boxes** — re-reported from
  the per-directory validation.

Review-flag semantics: ``suspicious_*`` findings do NOT fail the audit — they
are listed for human review. Structural failures (malformed labels,
contamination, session leakage, missing split directories) DO fail it
(``ok: false``). No data is ever modified or deleted by this module.

No camera, robot, or hardware assumptions; resolves no OD-01…OD-16.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from .frames import frame_hash, hamming_distance
from .validate import validate_dataset

#: Boxes smaller than this many pixels² are flagged for review: below ~4×4 px
#: a label is barely visible at any training resolution. Evidence-only floor —
#: such boxes are flagged, not rejected (tiny distant balls are exactly the
#: examples V3's error analysis says we must keep).
DEFAULT_MIN_BOX_AREA_PX = 16.0

#: A box covering more than this fraction of the image area is flagged as an
#: implausible ball label (label outlier / accidental giant box).
DEFAULT_SUSPECT_MAX_BOX_FRACTION = 0.9

#: Balls with area below this fraction of the image are counted as "small" for
#: the size-distribution evidence (mirrors the detector error-analysis tool's
#: ``small_ball_fraction`` so counts compare directly across tools).
DEFAULT_TINY_AREA_FRACTION = 0.005

_IMAGE_EXTS = (".jpg", ".jpeg", ".png")


def _find_split_dir(dataset_root: Path, split: str) -> Optional[Path]:
    """Locate a split's image directory under either layout convention.

    Supported layouts (both occur in this repository's tooling):
      * ``<root>/images/<split>`` + ``<root>/labels/<split>``  (data.yaml style)
      * ``<root>/<split>/images`` + ``<root>/<split>/labels``  (split-tool style)
    """
    for images_rel, labels_rel in (
        (Path("images") / split, Path("labels") / split),
        (Path(split) / "images", Path(split) / "labels"),
    ):
        if (dataset_root / images_rel).is_dir() and (dataset_root / labels_rel).is_dir():
            return dataset_root / images_rel
    return None


def _labels_dir_for(image_dir: Path) -> Path:
    """Mirror of ``_find_split_dir`` for the labels directory."""
    if image_dir.parent.name == "images":
        return image_dir.parent.parent / "labels" / image_dir.name
    return image_dir.parent / "labels"


def _image_sizes(image_dir: Path) -> Dict[str, Optional[tuple]]:
    """Map image *stem* → (width, height); unreadable images map to None.
    Keyed by stem because label files pair with images by stem."""
    try:
        from PIL import Image
    except ImportError:  # pragma: no cover - PIL is a project dependency
        return {}
    sizes: Dict[str, Optional[tuple]] = {}
    for p in sorted(image_dir.iterdir()):
        if p.suffix.lower() in _IMAGE_EXTS:
            try:
                with Image.open(p) as im:
                    sizes[p.stem] = im.size
            except OSError:
                sizes[p.stem] = None
    return sizes


def audit_split(
    image_dir: Path,
    label_dir: Path,
    *,
    num_classes: int = 1,
    min_box_area_px: float = DEFAULT_MIN_BOX_AREA_PX,
    suspect_max_box_fraction: float = DEFAULT_SUSPECT_MAX_BOX_FRACTION,
    tiny_area_fraction: float = DEFAULT_TINY_AREA_FRACTION,
) -> Dict[str, Any]:
    """Audit one split directory pair. Returns a machine-readable report."""
    report = validate_dataset(image_dir, label_dir, num_classes=num_classes)
    sizes = _image_sizes(image_dir)

    suspicious_tiny: List[Dict[str, Any]] = []
    suspicious_large: List[Dict[str, Any]] = []
    small_box_count = 0
    box_area_px_total = 0.0
    areas: List[float] = []
    box_count = 0

    for lbl_name, file_info in report["per_file"].items():
        image_name = Path(lbl_name).stem
        img_size = sizes.get(image_name)
        if img_size is None:
            continue
        img_w, img_h = img_size
        img_area = max(img_w * img_h, 1)
        for rec in file_info["records"]:
            box_count += 1
            area_px = (rec["w"] * img_w) * (rec["h"] * img_h)
            area_fraction = area_px / img_area
            areas.append(area_px)
            box_area_px_total += area_px
            if area_fraction < tiny_area_fraction:
                small_box_count += 1
            if area_px < min_box_area_px:
                suspicious_tiny.append(
                    {
                        "image": image_name,
                        "box": [rec["cx"], rec["cy"], rec["w"], rec["h"]],
                        "area_px": round(area_px, 2),
                    }
                )
            if area_fraction > suspect_max_box_fraction:
                suspicious_large.append(
                    {
                        "image": image_name,
                        "box": [rec["cx"], rec["cy"], rec["w"], rec["h"]],
                        "area_fraction": round(area_fraction, 4),
                    }
                )

    areas.sort()
    # split name: ``images/<split>`` layout → image_dir.name; ``<split>/images`` layout → parent
    report["split"] = image_dir.parent.name if image_dir.parent.name != "images" else image_dir.name
    report["box_statistics"] = {
        "num_boxes": box_count,
        "median_box_area_px": round(areas[len(areas) // 2], 2) if areas else None,
        "mean_box_area_px": round(box_area_px_total / box_count, 2) if box_count else 0.0,
        "small_ball_boxes": small_box_count,
        "small_ball_fraction_of_boxes": round(small_box_count / box_count, 4) if box_count else 0.0,
    }
    report["flags_for_review"] = {
        "suspicious_tiny_boxes": suspicious_tiny,
        "suspicious_large_boxes": suspicious_large,
        "note": "flags are for human review only — this module never deletes data",
    }
    return report


def _pixel_duplicate_check(
    roots: Sequence[tuple], *, hash_size: int = 8, threshold_bits: int = 5
) -> List[Dict[str, Any]]:
    """Cross-split perceptual near-duplicate check. O(n²) across splits — opt-in."""
    hashed: List[tuple] = []  # (split, image_name, hash)
    for split, image_dir in roots:
        for p in sorted(image_dir.iterdir()):
            if p.suffix.lower() in _IMAGE_EXTS:
                try:
                    hashed.append((split, p.name, frame_hash(p, hash_size=hash_size)))
                except OSError:
                    continue
    findings: List[Dict[str, Any]] = []
    for i, (s1, n1, h1) in enumerate(hashed):
        for s2, n2, h2 in hashed[i + 1 :]:
            if s1 == s2:
                continue
            d = hamming_distance(h1, h2)
            if d <= threshold_bits:
                findings.append(
                    {
                        "a": {"split": s1, "image": n1},
                        "b": {"split": s2, "image": n2},
                        "hamming_distance": d,
                    }
                )
    return findings


def audit_dataset(
    dataset_root: Path,
    *,
    splits: Sequence[str] = ("train", "val", "test"),
    num_classes: int = 1,
    sessions: Optional[Dict[str, str]] = None,
    pixel_duplicate_check: bool = False,
    hash_size: int = 8,
    dup_threshold_bits: int = 5,
    min_box_area_px: float = DEFAULT_MIN_BOX_AREA_PX,
    suspect_max_box_fraction: float = DEFAULT_SUSPECT_MAX_BOX_FRACTION,
    tiny_area_fraction: float = DEFAULT_TINY_AREA_FRACTION,
) -> Dict[str, Any]:
    """Audit a whole multi-split dataset. Structural failures set ``ok=False``.

    ``sessions`` optionally maps image stem → session id; when given, any
    session spanning more than one split is reported as leakage.
    ``pixel_duplicate_check`` enables the (quadratic) perceptual-hash
    cross-split near-duplicate scan; matches are review flags, not hard
    failures, since perceptual hashes can over-match on similar court scenes.
    """
    split_reports: Dict[str, Dict[str, Any]] = {}
    errors: List[str] = []
    stems_by_split: Dict[str, set] = {}
    image_dirs: Dict[str, Path] = {}

    for split in splits:
        image_dir = _find_split_dir(dataset_root, split)
        if image_dir is None:
            errors.append(f"split '{split}': image/label directories not found under {dataset_root}")
            continue
        image_dirs[split] = image_dir
        rep = audit_split(
            image_dir,
            _labels_dir_for(image_dir),
            num_classes=num_classes,
            min_box_area_px=min_box_area_px,
            suspect_max_box_fraction=suspect_max_box_fraction,
            tiny_area_fraction=tiny_area_fraction,
        )
        split_reports[split] = rep
        errors.extend(f"{split}: {e}" for e in rep["errors"])
        errors.extend(f"{split}: image missing label: {m}" for m in rep["missing_labels"])
        errors.extend(f"{split}: label missing image: {m}" for m in rep["missing_images"])
        stems_by_split[split] = {
            p.stem for p in image_dir.iterdir() if p.suffix.lower() in _IMAGE_EXTS
        }

    # exact-stem contamination across splits
    contamination: List[Dict[str, Any]] = []
    split_names = sorted(stems_by_split)
    for i, s1 in enumerate(split_names):
        for s2 in split_names[i + 1 :]:
            overlap = sorted(stems_by_split[s1] & stems_by_split[s2])
            if overlap:
                contamination.append({"splits": [s1, s2], "stems": overlap[:20], "count": len(overlap)})
    if contamination:
        errors.append(f"cross-split stem contamination in {len(contamination)} split pair(s)")

    # session leakage
    session_leakage: List[Dict[str, Any]] = []
    if sessions is not None:
        span: Dict[str, set] = {}
        for split, stems in stems_by_split.items():
            for stem in stems:
                sid = sessions.get(stem)
                if sid is not None:
                    span.setdefault(sid, set()).add(split)
        session_leakage = [
            {"session": sid, "splits": sorted(sp)} for sid, sp in sorted(span.items()) if len(sp) > 1
        ]
        if session_leakage:
            errors.append(f"{len(session_leakage)} session(s) span multiple splits")

    pixel_duplicates: List[Dict[str, Any]] = []
    if pixel_duplicate_check and len(image_dirs) > 1:
        pixel_duplicates = _pixel_duplicate_check(
            [(s, image_dirs[s]) for s in sorted(image_dirs)],
            hash_size=hash_size,
            threshold_bits=dup_threshold_bits,
        )

    totals = {
        split: {
            "images": rep["num_images"],
            "boxes": rep["num_boxes"],
            "small_ball_boxes": rep["box_statistics"]["small_ball_boxes"],
            "flags_for_review": (
                len(rep["flags_for_review"]["suspicious_tiny_boxes"])
                + len(rep["flags_for_review"]["suspicious_large_boxes"])
            ),
        }
        for split, rep in split_reports.items()
    }

    return {
        "dataset_root": str(dataset_root),
        "splits": {s: {k: v for k, v in rep.items() if k != "per_file"} for s, rep in split_reports.items()},
        "totals": totals,
        "contamination": contamination,
        "session_leakage": session_leakage,
        "pixel_duplicates": pixel_duplicates,
        "errors": errors,
        "ok": not errors,
    }


def main(argv: Optional[Sequence[str]] = None) -> int:  # pragma: no cover - thin CLI
    import json

    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("dataset_root", type=Path)
    p.add_argument("--splits", default="train,val,test", help="comma-separated split names")
    p.add_argument("--num-classes", type=int, default=1)
    p.add_argument("--sessions", type=Path, default=None, help="JSON file mapping stem → session id")
    p.add_argument("--pixel-duplicate-check", action="store_true", help="quadratic perceptual-hash cross-split scan")
    p.add_argument("--min-box-area-px", type=float, default=DEFAULT_MIN_BOX_AREA_PX)
    p.add_argument("--suspect-max-box-fraction", type=float, default=DEFAULT_SUSPECT_MAX_BOX_FRACTION)
    p.add_argument("--tiny-area-fraction", type=float, default=DEFAULT_TINY_AREA_FRACTION)
    args = p.parse_args(argv)

    sessions = None
    if args.sessions is not None:
        sessions = {k: str(v) for k, v in json.loads(args.sessions.read_text(encoding="utf-8")).items()}

    report = audit_dataset(
        args.dataset_root,
        splits=tuple(s.strip() for s in args.splits.split(",") if s.strip()),
        num_classes=args.num_classes,
        sessions=sessions,
        pixel_duplicate_check=args.pixel_duplicate_check,
        min_box_area_px=args.min_box_area_px,
        suspect_max_box_fraction=args.suspect_max_box_fraction,
        tiny_area_fraction=args.tiny_area_fraction,
    )
    print(json.dumps(report, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
