"""Convert COCO ball detections into a single-class YOLO dataset.

Software-only data preparation for Ball Detection V1. Two modes:

* ``--coco8``  — download Ultralytics COCO8 (8 train / 4 val COCO images, ≈1 MB)
  via the ultralytics utility API and rewrite it as a single-class ``ball``
  dataset. Purpose: prove the training pipeline end-to-end. NOT a performance
  dataset (see README §4.1).
* ``--coco-json <instances_train2017.json> [--coco-val-json <…val2017.json>]``
  — convert official COCO 2017 annotation files, keeping only category
  ``sports ball`` (COCO category id 32) remapped to class 0 ``ball``, with a
  seeded train/val/test split. The heavy download (~19 GB images) is left to
  the operator; this script consumes the annotation JSON plus the image
  directory it references.

This file documents its dataset sources and licenses; it contains no hardware
values, thresholds, or calibration constants and resolves no OD-01…OD-16.
"""

from __future__ import annotations

import argparse
import json
import random
import shutil
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

# COCO 2017 **official JSON annotation id** for "sports ball" — verified against
# instances_{train,val}2017.json "categories": id 32 = "tie", id 37 = "sports ball".
# WARNING: this is NOT the ultralytics YOLO 80-class index (where "sports ball"
# happens to be index 32); official COCO JSON ids are non-contiguous. Using the
# YOLO index here silently converts a different category — V2 was trained and
# evaluated on NECKTIES because of exactly this bug (proven 2026-09-06: every
# GT box in the V2 dataset matched a COCO "tie" annotation with IoU 1.0).
COCO_SPORTS_BALL_CATEGORY_ID = 37
# Ultralytics COCO8 ships YOLO-format labels whose class ids are the *YOLO 80-
# class indices* (0..79), where "sports ball" is index 32. Distinct from the
# official COCO JSON annotation id above — do not unify them.
COCO8_YOLO_SPORTS_BALL_CLASS_INDEX = 32
SINGLE_CLASS_NAME = "ball"
# Ultralytics COCO8 dataset identifier (auto-downloaded on first use).
COCO8_URIS = ("https://ultralytics.com/assets/coco8.zip",)

# Synthetic fixture palette (SIMULATION / TEST ONLY — not real ball appearance).
_SYNTH_BALL_COLORS = [(230, 126, 34), (236, 240, 241), (149, 165, 166), (192, 57, 43)]
_SYNTH_BG_COLORS = [(40, 60, 80), (60, 120, 60), (120, 120, 120), (200, 200, 200)]


@dataclass(frozen=True)
class SplitCounts:
    train: int
    val: int
    test: int


def _coco_to_yolo_box(
    bbox: Sequence[float], img_w: float, img_h: float
) -> Tuple[float, float, float, float]:
    """Convert a COCO ``[x, y, w, h]`` absolute box to YOLO normalized
    ``[x_center, y_center, w, h]``, clipping the box to the image bounds
    first (so the emitted box can never exceed the image)."""
    x, y, w, h = bbox
    if img_w <= 0 or img_h <= 0:
        raise ValueError(f"non-positive image dimensions {img_w}x{img_h}")

    def _clamp(v: float, lo: float, hi: float) -> float:
        return min(max(v, lo), hi)

    x0 = _clamp(float(x), 0.0, float(img_w))
    y0 = _clamp(float(y), 0.0, float(img_h))
    x1 = _clamp(float(x) + float(w), 0.0, float(img_w))
    y1 = _clamp(float(y) + float(h), 0.0, float(img_h))
    if x1 < x0 or y1 < y0:  # degenerate after clipping
        x1, y1 = x0, y0
    cx = (x0 + x1) / 2.0 / float(img_w)
    cy = (y0 + y1) / 2.0 / float(img_h)
    return (cx, cy, (x1 - x0) / float(img_w), (y1 - y0) / float(img_h))


def _write_label(path: Path, boxes: Sequence[Tuple[float, float, float, float]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"0 {cx:.6f} {cy:.6f} {bw:.6f} {bh:.6f}" for cx, cy, bw, bh in boxes]
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def _write_data_yaml(out_dir: Path, names: Dict[int, str]) -> None:
    yaml_text = [
        f"path: {out_dir.resolve().as_posix()}",
        "train: images/train",
        "val: images/val",
        "test: images/test",
        f"nc: {len(names)}",
        "names:",
    ]
    yaml_text.extend(f"  {k}: {v}" for k, v in sorted(names.items()))
    (out_dir / "data.yaml").write_text("\n".join(yaml_text) + "\n", encoding="utf-8")


def _collect_ball_annotations(
    coco: Dict, *, remap_ids: bool
) -> Tuple[Dict[int, List[Tuple[float, float, float, float]]], Dict[int, Tuple[float, float]], Dict[int, str]]:
    """Group ball annotations per image; return (boxes, image sizes, category names).

    ``remap_ids`` for official COCO (category id 32 -> class 0). Ultralytics
    COCO8 ships YOLO labels already; we only read its COCO JSON when present.
    """
    cat_names: Dict[int, str] = {}
    for cat in coco.get("categories", []):
        if not remap_ids or cat["id"] == COCO_SPORTS_BALL_CATEGORY_ID:
            cat_names[cat["id"]] = cat["name"]

    keep = {COCO_SPORTS_BALL_CATEGORY_ID} if remap_ids else set(cat_names)
    boxes: Dict[int, List[Tuple[float, float, float, float]]] = defaultdict(list)
    for ann in coco.get("annotations", []):
        if ann.get("category_id") not in keep:
            continue
        boxes[ann["image_id"]].append(tuple(ann["bbox"]))

    sizes: Dict[int, Tuple[float, float]] = {}
    for img in coco.get("images", []):
        sizes[img["id"]] = (float(img["width"]), float(img["height"]))
    return boxes, sizes, cat_names


def _ball_to_yolo_line(cx_px: float, cy_px: float, r_px: float, img_w: int, img_h: int) -> str:
    """Pure helper: circle spec → YOLO label line (class 0, normalized)."""
    return (
        f"0 {cx_px / img_w:.6f} {cy_px / img_h:.6f} "
        f"{(2.0 * r_px) / img_w:.6f} {(2.0 * r_px) / img_h:.6f}"
    )


def _sample_ball_specs(
    rng: random.Random, img_w: int, img_h: int, max_balls: int
) -> list:
    """Pure helper: pick 0..max_balls non-overlapping-enough in-bounds circles."""
    count = rng.randint(0, max_balls)
    specs = []
    for _ in range(count):
        r = rng.randint(8, 20)
        margin = r + 2
        cx = rng.randint(margin, img_w - margin)
        cy = rng.randint(margin, img_h - margin)
        specs.append((cx, cy, r))
    return specs


def prepare_synthetic(
    out_dir: Path, *, seed: int = 20260906, train: int = 24, val: int = 8, test: int = 8,
    img_w: int = 320, img_h: int = 240, max_balls: int = 2,
) -> SplitCounts:
    """Generate a SYNTHETIC fixture dataset: solid backgrounds with drawn circles.

    SIMULATION / TEST ONLY — these are geometric circles, NOT photographs of
    balls. Purpose: exercise the full train → val → eval → inference machinery
    with positive samples tonight. They say nothing about real-world detection
    performance. COCO8 contains no sports-ball positives (its 8 images are
    vehicles/animals), so it proves negative-image handling only.
    """
    try:
        from PIL import Image, ImageDraw
    except ImportError as exc:  # pragma: no cover - environment error
        raise SystemExit("Pillow is required for --synthetic") from exc

    rng = random.Random(seed)
    out_dir.mkdir(parents=True, exist_ok=True)
    counts = {"train": 0, "val": 0, "test": 0}
    for split, n in (("train", train), ("val", val), ("test", test)):
        img_dir = out_dir / "images" / split
        lbl_dir = out_dir / "labels" / split
        img_dir.mkdir(parents=True, exist_ok=True)
        lbl_dir.mkdir(parents=True, exist_ok=True)
        for i in range(n):
            bg = _SYNTH_BG_COLORS[rng.randrange(len(_SYNTH_BG_COLORS))]
            img = Image.new("RGB", (img_w, img_h), bg)
            draw = ImageDraw.Draw(img)
            lines = []
            for cx, cy, r in _sample_ball_specs(rng, img_w, img_h, max_balls):
                color = _SYNTH_BALL_COLORS[rng.randrange(len(_SYNTH_BALL_COLORS))]
                draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=color)
                lines.append(_ball_to_yolo_line(cx, cy, r, img_w, img_h))
            name = f"synth_{split}_{i:04d}"
            img.save(img_dir / f"{name}.jpg", quality=90)
            (lbl_dir / f"{name}.txt").write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
            counts[split] += 1

    _write_data_yaml(out_dir, {0: SINGLE_CLASS_NAME})
    (out_dir / "PROVENANCE.md").write_text(
        "# Dataset provenance\n\n"
        "- SYNTHETIC FIXTURE (SIMULATION / TEST ONLY): programmatic solid-color\n"
        "  backgrounds with drawn circles. NOT photographs of balls.\n"
        f"- Generated by detection/data/prepare_dataset.py --synthetic, seed={seed}.\n"
        f"- Counts: train={counts['train']} val={counts['val']} test={counts['test']} "
        f"({img_w}x{img_h}, 0..{max_balls} circles per image).\n"
        "- Purpose: prove the train/eval/inference MACHINERY with positive samples.\n"
        "  Says NOTHING about real-world detection performance.\n"
        "- REAL-ROBOT VALIDATION STILL REQUIRED (ai/detection/README.md §4.3).\n",
        encoding="utf-8",
    )
    return SplitCounts(counts["train"], counts["val"], counts["test"])


def prepare_from_coco8(out_dir: Path) -> SplitCounts:
    """Build a single-class ``ball`` dataset from Ultralytics COCO8.

    COCO8's YOLO labels use COCO's 80-class ordering where class 32 is
    ``sports ball``; we copy every image and rewrite each label file keeping
    only class-32 boxes, remapped to class 0.
    """
    try:
        from ultralytics.utils.downloads import download
    except ImportError as exc:  # pragma: no cover - environment error
        raise SystemExit(
            "ultralytics is required for --coco8 (install detection/requirements.txt)"
        ) from exc

    out_dir.mkdir(parents=True, exist_ok=True)
    zip_dir = out_dir / "_coco8"
    # ultralytics ≥8.4 signature: download(url, dir=…, unzip=…, delete=…).
    download(COCO8_URIS[0], dir=zip_dir, unzip=True, delete=True)
    src = zip_dir / "coco8"

    counts = SplitCounts(0, 0, 0)
    for split in ("train", "val"):
        img_src = src / "images" / split
        lbl_src = src / "labels" / split
        img_dst = out_dir / "images" / split
        lbl_dst = out_dir / "labels" / split
        img_dst.mkdir(parents=True, exist_ok=True)
        lbl_dst.mkdir(parents=True, exist_ok=True)
        n = 0
        for img_path in sorted(img_src.glob("*.jpg")):
            lbl_path = lbl_src / (img_path.stem + ".txt")
            kept: List[Tuple[float, float, float, float]] = []
            if lbl_path.exists():
                for line in lbl_path.read_text(encoding="utf-8").splitlines():
                    parts = line.split()
                    if not parts:
                        continue
                    if int(parts[0]) == COCO8_YOLO_SPORTS_BALL_CLASS_INDEX:
                        kept.append(tuple(float(v) for v in parts[1:5]))
            shutil.copy2(img_path, img_dst / img_path.name)
            _write_label(lbl_dst / (img_path.stem + ".txt"), kept)
            n += 1
        if split == "train":
            counts = SplitCounts(n, counts.val, counts.test)
        else:
            counts = SplitCounts(counts.train, n, counts.test)

    # COCO8 has no test split; mirror val (standard ultralytics practice) but
    # mark it in the README — never used for training.
    test_src = out_dir / "images" / "val"
    test_dst = out_dir / "images" / "test"
    if test_src.exists() and not test_dst.exists():
        shutil.copytree(test_src, test_dst)
        shutil.copytree(out_dir / "labels" / "val", out_dir / "labels" / "test")
        counts = SplitCounts(counts.train, counts.val, counts.val)

    names = {0: SINGLE_CLASS_NAME}
    _write_data_yaml(out_dir, names)
    shutil.rmtree(zip_dir, ignore_errors=True)

    (out_dir / "PROVENANCE.md").write_text(
        "# Dataset provenance\n\n"
        f"- Derived from Ultralytics COCO8 ({', '.join(COCO8_URIS)}).\n"
        "- COCO8 is an 8-image/4-val-image subset of COCO train2017/val2017.\n"
        "- Images: Flickr Terms of Use (COCO images; non-commercial research).\n"
        "- Annotations: COCO, CC BY 4.0. Single class 'ball' = COCO 'sports ball' (id 32).\n"
        "- Purpose: PIPELINE PROOF ONLY. Not a performance dataset (see ai/detection/README.md §4).\n",
        encoding="utf-8",
    )
    return counts


def prepare_from_coco_json(
    train_json: Path,
    out_dir: Path,
    *,
    val_json: Optional[Path] = None,
    images_dir: Optional[Path] = None,
    seed: int = 20260906,
    val_frac: float = 0.15,
    test_frac: float = 0.05,
    max_train_images: Optional[int] = None,
) -> SplitCounts:
    """Convert official COCO annotation JSON(s) into single-class YOLO splits.

    Only images actually present under ``images_dir`` are used (the operator
    downloads the COCO image archives separately). With no ``val_json``,
    annotated images are split train/val/test with a fixed seed.

    ``max_train_images`` caps the training split after the seeded shuffle — a
    pure software budget for CPU training runs, NOT a robot or scene
    assumption (OD-01 and all other ODs remain open).
    """
    if not 0.0 <= val_frac < 1.0 or not 0.0 <= test_frac < 1.0 or val_frac + test_frac >= 1.0:
        raise ValueError("invalid split fractions")
    if max_train_images is not None and max_train_images <= 0:
        raise ValueError("max_train_images must be positive when given")

    out_dir.mkdir(parents=True, exist_ok=True)
    coco = json.loads(train_json.read_text(encoding="utf-8"))
    boxes, sizes, _ = _collect_ball_annotations(coco, remap_ids=True)
    images: Dict[int, str] = {img["id"]: img["file_name"] for img in coco.get("images", [])}

    rng = random.Random(seed)
    usable = [iid for iid, fname in images.items() if iid in boxes and iid in sizes]
    file_names = {iid: images[iid] for iid in usable}

    if val_json is not None:
        val_coco = json.loads(val_json.read_text(encoding="utf-8"))
        vboxes, vsizes, _ = _collect_ball_annotations(val_coco, remap_ids=True)
        vimages = {img["id"]: img["file_name"] for img in val_coco.get("images", [])}
        for iid, fname in vimages.items():
            if iid in vboxes and iid in vsizes:
                file_names[iid] = fname
                sizes[iid] = vsizes[iid]
        usable_val = [iid for iid in vboxes if iid in vimages and iid in vsizes]
        rng.shuffle(usable_val)
        n_test = int(round(len(usable_val) * test_frac / max(val_frac + test_frac, 1e-9)))
        test_ids = set(usable_val[:n_test])
        val_ids = set(usable_val[n_test:])
        # Software CPU-budget cap on the train split (deterministic: ids are
        # sorted); official val2017-derived val/test images are never capped.
        usable_train = usable if max_train_images is None else usable[:max_train_images]
        split_of = {iid: ("train", boxes[iid], sizes[iid]) for iid in usable_train}
        for iid in val_ids:
            split_of[iid] = ("val", vboxes[iid], vsizes[iid])
        for iid in test_ids:
            split_of[iid] = ("test", vboxes[iid], vsizes[iid])
    else:
        rng.shuffle(usable)
        n_val = int(round(len(usable) * val_frac))
        n_test = int(round(len(usable) * test_frac))
        val_ids = set(usable[:n_val])
        test_ids = set(usable[n_val : n_val + n_test])
        train_ids_all = usable[n_val + n_test :]
        # Software CPU-budget cap: applied AFTER the seeded shuffle so the
        # selection is deterministic. The official val2017 images (with their
        # own val_json) are never capped.
        train_ids = set(train_ids_all if max_train_images is None else train_ids_all[:max_train_images])
        split_of = {
            iid: (
                "test" if iid in test_ids else "val" if iid in val_ids else "train" if iid in train_ids else None,
                boxes[iid],
                sizes[iid],
            )
            for iid in usable
        }
        split_of = {iid: entry for iid, entry in split_of.items() if entry[0] is not None}

    counts = SplitCounts(0, 0, 0)
    missing = 0
    for iid, (split, iboxes, (w, h)) in sorted(split_of.items()):
        fname = file_names[iid]
        # Support the official COCO layout (train2017/…, val2017/…), the
        # download_coco_balls.py layout (train/…, val/…), and flat
        # directories. Note: the test split is carved out of val2017 images,
        # so both val and test resolve to the val2017/val source dir.
        base = images_dir or train_json.parent
        source_dir = "train" if split == "train" else "val"
        official = "train2017" if split == "train" else "val2017"
        candidates = [
            base / source_dir / fname,
            base / official / fname,
            base / fname,
        ]
        src_img = next((c for c in candidates if c.exists()), None)
        if src_img is None:
            missing += 1
            continue
        dst_img = out_dir / "images" / split / Path(fname).name
        dst_img.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_img, dst_img)
        _write_label(
            out_dir / "labels" / split / (Path(fname).stem + ".txt"),
            [_coco_to_yolo_box(b, w, h) for b in iboxes],
        )
        if split == "train":
            counts = SplitCounts(counts.train + 1, counts.val, counts.test)
        elif split == "val":
            counts = SplitCounts(counts.train, counts.val + 1, counts.test)
        else:
            counts = SplitCounts(counts.train, counts.val, counts.test + 1)

    _write_data_yaml(out_dir, {0: SINGLE_CLASS_NAME})
    (out_dir / "PROVENANCE.md").write_text(
        "# Dataset provenance\n\n"
        f"- Source: COCO 2017 annotations ({train_json.name}"
        + (f", {val_json.name}" if val_json else "")
        + ").\n"
        f"- Conversion: sports ball (COCO category {COCO_SPORTS_BALL_CATEGORY_ID}) -> class 0 'ball'.\n"
        f"- Split: seed={seed}, val_frac={val_frac}, test_frac={test_frac}; images missing on disk skipped.\n"
        + (f"- max_train_images cap: {max_train_images} (software CPU budget only).\n" if max_train_images else "")
        + "- Images: Flickr Terms of Use (COCO; non-commercial research use).\n"
        "- Annotations: COCO, CC BY 4.0.\n"
        "- PUBLIC-DATASET VALIDATION ONLY — REAL-ROBOT VALIDATION STILL "
        "REQUIRED (ai/detection/README.md §4.3).\n",
        encoding="utf-8",
    )
    if missing:
        print(f"warning: {missing} annotated images missing on disk and skipped", file=sys.stderr)
    return counts


def main(argv: Optional[Sequence[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out", type=Path, default=Path("datasets/coco8-ball"))
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--coco8", action="store_true", help="build the tiny COCO8 pipeline-proof set (no ball positives!)")
    mode.add_argument("--synthetic", action="store_true", help="generate SYNTHETIC circle fixtures (positive samples, simulation only)")
    mode.add_argument("--coco-json", type=Path, metavar="FILE", help="official COCO instances_train2017.json")
    p.add_argument("--coco-val-json", type=Path, default=None, metavar="FILE")
    p.add_argument("--images-dir", type=Path, default=None, help="directory containing COCO train2017/ images")
    p.add_argument("--seed", type=int, default=20260906)
    p.add_argument("--val-frac", type=float, default=0.15)
    p.add_argument("--test-frac", type=float, default=0.05)
    p.add_argument(
        "--max-train-images",
        type=int,
        default=None,
        help="cap the train split after the seeded shuffle (software CPU budget; val/test uncapped)",
    )
    args = p.parse_args(argv)

    if args.coco8:
        counts = prepare_from_coco8(args.out)
    elif args.synthetic:
        counts = prepare_synthetic(args.out, seed=args.seed)
    else:
        counts = prepare_from_coco_json(
            args.coco_json,
            args.out,
            val_json=args.coco_val_json,
            images_dir=args.images_dir,
            seed=args.seed,
            val_frac=args.val_frac,
            test_frac=args.test_frac,
            max_train_images=args.max_train_images,
        )
    print(f"dataset ready at {args.out}: train={counts.train} val={counts.val} test={counts.test}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
