"""Ball Detection V2 error analysis — WHY is recall 0.347?

Software-only analysis of a trained detector against a YOLO-format test set.
NO training happens here; the V2 model and dataset are read-only inputs.

Structure:

    detector (any BallDetector seam — fake in tests, ultralytics in production)
      → per-image predictions + ground-truth label files
      → IoU matching (greedy, confidence-descending, best-IoU pairing)
      → per-image error classification:
            missed_ball (FN) / false_positive (FP) / poor_localization (TP)
            duplicate (extra detection on an already-matched GT)
      → aggregate failure-mode report (JSON) + annotated images

All numeric criteria below are **analysis parameters** (constants are
documented, every one is exposed or overridable where it matters); none is a
robot/engineering requirement and none resolves OD-01…OD-16. The V2 model and
dataset are never modified by this module.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol, Sequence, Tuple

try:  # package import (tests) or direct-script execution (CLI)
    from .inference import BallDetection, BallDetector
except ImportError:  # pragma: no cover - script mode
    from inference import BallDetection, BallDetector

# --- analysis parameters (configurable via CLI where noted) ---
DEFAULT_IOU_THRESHOLD = 0.5       # TP criterion (COCO-standard)      [--iou-threshold]
DEFAULT_CONF_THRESHOLD = 0.25     # reporting operating point        [--conf-threshold]
SMALL_BALL_FRACTION = 0.005       # "small/distant" evidence bucket: GT box area < 0.5% of image area
POOR_LOCALIZATION_AREA_RATIO = 1.5  # TP whose box is ≥1.5× smaller in area than its matched GT
NEAR_MISS_IOU = 0.1               # FP with best unmatched-GT IoU ≥ this ⇒ localization near-miss

IMAGE_EXTS = (".jpg", ".jpeg", ".png")

Box = Tuple[float, float, float, float]


def iou(a: Box, b: Box) -> float:
    """IoU of two (x_min, y_min, x_max, y_max) boxes; 0.0 for degenerate boxes."""
    ix0, iy0 = max(a[0], b[0]), max(a[1], b[1])
    ix1, iy1 = min(a[2], b[2]), min(a[3], b[3])
    iw, ih = max(0.0, ix1 - ix0), max(0.0, iy1 - iy0)
    inter = iw * ih
    if inter <= 0.0:
        return 0.0
    area_a = max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])
    area_b = max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])
    union = area_a + area_b - inter
    return inter / union if union > 0.0 else 0.0


def yolo_to_xyxy(
    cx: float, cy: float, w: float, h: float, img_w: float, img_h: float
) -> Box:
    """Normalized YOLO box → absolute pixel corners (clamped to the image)."""
    x0, y0 = (cx - w / 2.0) * img_w, (cy - h / 2.0) * img_h
    x1, y1 = (cx + w / 2.0) * img_w, (cy + h / 2.0) * img_h
    return (
        max(0.0, min(x0, x1)),
        max(0.0, min(y0, y1)),
        min(img_w, max(x0, x1)),
        min(img_h, max(y0, y1)),
    )


def _pred_box(p: BallDetection) -> Box:
    return (p.x - p.width / 2, p.y - p.height / 2, p.x + p.width / 2, p.y + p.height / 2)


def load_ground_truth(label_path: Path, img_w: float, img_h: float) -> List[Dict[str, Any]]:
    """Read a YOLO label file into absolute-corner GT boxes with area fraction."""
    gts: List[Dict[str, Any]] = []
    if not label_path.exists():
        return gts
    for line in label_path.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) < 5:
            continue
        cls_id = int(parts[0])
        cx, cy, w, h = (float(v) for v in parts[1:5])
        box = yolo_to_xyxy(cx, cy, w, h, img_w, img_h)
        gts.append(
            {
                "class": cls_id,
                "box": box,
                "area_fraction": ((box[2] - box[0]) * (box[3] - box[1])) / max(img_w * img_h, 1.0),
            }
        )
    return gts


def classify_image(
    gts: List[Dict[str, Any]],
    preds: Sequence[BallDetection],
    *,
    image_size: Tuple[int, int],
    iou_threshold: float = DEFAULT_IOU_THRESHOLD,
    conf_threshold: float = DEFAULT_CONF_THRESHOLD,
) -> Dict[str, Any]:
    """Match predictions to ground truth for one image and classify errors.

    Matching: predictions in confidence-descending order each take their
    best-IoU unmatched GT (greedy, one-to-one; IoU ≥ threshold ⇒ TP).

    Classification:
    * GT with no matching prediction      → ``missed`` (FN), with size evidence
    * Prediction matching nothing         → ``fp`` (with best unmatched-GT IoU)
    * TP ≥POOR_LOCALIZATION_AREA_RATIO smaller than its GT → ``poor_localization`` TP
    * Extra prediction on an already-matched GT → ``duplicate``
    """
    img_w, img_h = image_size
    preds_sorted = sorted(
        (p for p in preds if p.confidence >= conf_threshold),
        key=lambda d: d.confidence,
        reverse=True,
    )
    unmatched_gt = set(range(len(gts)))
    gt_taken_by: Dict[int, List[int]] = {i: [] for i in range(len(gts))}
    tp: List[Dict[str, Any]] = []
    fp: List[Dict[str, Any]] = []

    for p_idx, p in enumerate(preds_sorted):
        pbox = _pred_box(p)
        best_iou, best_gt = 0.0, -1
        for g_idx, gt in enumerate(gts):
            if g_idx not in unmatched_gt:
                continue
            v = iou(pbox, gt["box"])
            if v > best_iou:
                best_iou, best_gt = v, g_idx
        if best_gt >= 0 and best_iou >= iou_threshold:
            unmatched_gt.discard(best_gt)
            gt_taken_by[best_gt].append(p_idx)
            gt = gts[best_gt]
            gt_area = max(gt["box"][2] - gt["box"][0], 1.0) * max(gt["box"][3] - gt["box"][1], 1.0)
            pred_area = max(p.width, 1.0) * max(p.height, 1.0)
            tp.append(
                {
                    "pred_index": p_idx,
                    "gt_index": best_gt,
                    "iou": round(best_iou, 4),
                    "confidence": round(p.confidence, 4),
                    "gt_area_fraction": round(gt["area_fraction"], 6),
                    "gt_small": gt["area_fraction"] < SMALL_BALL_FRACTION,
                    "poor_localization": gt_area >= POOR_LOCALIZATION_AREA_RATIO * pred_area,
                }
            )
        else:
            # FP classification (duplicates stay inside the FP count — that is
            # the standard detection-metric convention — but carry a ``kind``
            # so error analysis can separate them from background FPs):
            #   duplicate — best IoU over ALL GTs ≥ threshold (the GT is just
            #               already taken ⇒ this is a repeat detection)
            #   near_miss — best IoU over unmatched GTs ≥ NEAR_MISS_IOU
            #               (localization/nearby-object confusion)
            #   background — everything else
            best_any_iou = round(max((iou(pbox, g["box"]) for g in gts), default=0.0), 4)
            best_unmatched = round(
                max((iou(pbox, gts[i]["box"]) for i in unmatched_gt), default=0.0), 4
            )
            if best_any_iou >= iou_threshold:
                kind = "duplicate"
            elif best_unmatched >= NEAR_MISS_IOU:
                kind = "near_miss"
            else:
                kind = "background"
            fp.append(
                {
                    "pred_index": p_idx,
                    "best_unmatched_iou": best_unmatched,
                    "best_any_iou": best_any_iou,
                    "kind": kind,
                    "confidence": round(p.confidence, 4),
                }
            )

    missed = [
        {
            "gt_index": i,
            "area_fraction": round(gts[i]["area_fraction"], 6),
            "small_ball": gts[i]["area_fraction"] < SMALL_BALL_FRACTION,
        }
        for i in sorted(unmatched_gt)
    ]
    duplicates = sorted(
        (
            {"pred_index": lst[j], "gt_index": g, "confidence": round(preds_sorted[lst[j]].confidence, 4)}
            for g, lst in gt_taken_by.items()
            for j in range(1, len(lst))
        ),
        key=lambda d: d["pred_index"],
    )

    return {
        "image_size": {"width": img_w, "height": img_h},
        "num_gt": len(gts),
        "num_predictions": len(preds_sorted),
        "tp": tp,
        "fp": fp,
        "missed": missed,
        "duplicates": [f for f in fp if f["kind"] == "duplicate"],
        "counts": {
            "tp": len(tp),
            "fp": len(fp),
            "fn": len(missed),
            "duplicates": sum(1 for f in fp if f["kind"] == "duplicate"),
            "poor_localization": sum(1 for t in tp if t["poor_localization"]),
        },
    }


def aggregate(per_image: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Roll per-image results into the failure-mode summary."""
    counts: Counter = Counter({"tp": 0, "fp": 0, "fn": 0, "duplicates": 0, "poor_localization": 0})
    small_total = small_found = normal_total = normal_found = 0
    missed_area_fractions: List[float] = []
    fp_kind: Counter = Counter({"duplicate": 0, "near_miss": 0, "background": 0})
    fp_confidences: List[float] = []
    tp_confidences: List[float] = []
    tp_ious: List[float] = []

    for r in per_image:
        for k in counts:
            counts[k] += r["counts"][k]
        for t in r["tp"]:
            tp_confidences.append(t["confidence"])
            tp_ious.append(t["iou"])
            if t["gt_small"]:
                small_total += 1
                small_found += 1
            else:
                normal_total += 1
                normal_found += 1
        for m in r["missed"]:
            missed_area_fractions.append(m["area_fraction"])
            if m["small_ball"]:
                small_total += 1
            else:
                normal_total += 1
        for f in r["fp"]:
            fp_kind[f.get("kind", "background")] += 1
            fp_confidences.append(f["confidence"])

    total_gt = counts["tp"] + counts["fn"]
    total_fp = counts["fp"]
    return {
        "counts": dict(counts),
        "recall_at_thresholds": round(counts["tp"] / total_gt, 4) if total_gt else None,
        "precision_at_thresholds": round(
            counts["tp"] / (counts["tp"] + counts["fp"]), 4
        ) if (counts["tp"] + counts["fp"]) else None,
        "recall_by_ball_size": {
            "note": "evidence only; small = GT area < small-ball fraction of the image",
            "small": {"found": small_found, "total": small_total,
                      "recall": round(small_found / small_total, 4) if small_total else None},
            "normal": {"found": normal_found, "total": normal_total,
                       "recall": round(normal_found / normal_total, 4) if normal_total else None},
        },
        "missed_gt_area_fraction": {
            "n": len(missed_area_fractions),
            "median": _median(missed_area_fractions),
            "max": max(missed_area_fractions) if missed_area_fractions else None,
            "min": min(missed_area_fractions) if missed_area_fractions else None,
        },
        "fp_breakdown": {
            "note": "duplicate = repeat detection of an already-matched ball; "
                    "near_miss = IoU≥" f"{NEAR_MISS_IOU} with an unmatched ball (localization/nearby-object "
                    "confusion); background = no ball nearby",
            "duplicate": fp_kind["duplicate"],
            "near_miss": fp_kind["near_miss"],
            "background": fp_kind["background"],
            "median_confidence": _median(fp_confidences),
        },
        "tp_quality": {
            "median_confidence": _median(tp_confidences),
            "median_iou": _median(tp_ious),
        },
    }


def _median(values: Sequence[float]) -> Optional[float]:
    if not values:
        return None
    s = sorted(values)
    n = len(s)
    return round(s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2.0, 4)


def run_analysis(
    detector: BallDetector,
    image_paths: Sequence[Path],
    label_paths: Sequence[Path],
    *,
    iou_threshold: float = DEFAULT_IOU_THRESHOLD,
    conf_threshold: float = DEFAULT_CONF_THRESHOLD,
) -> Dict[str, Any]:
    """Run the detector over the test set and produce the full analysis report.

    ``detector`` is any BallDetector (fake in tests, UltralyticsBallDetector in
    production). Per-image entries carry GT, predictions, matching, and error
    classification; ``aggregate`` identifies the dominant failure modes.
    """
    per_image: List[Dict[str, Any]] = []
    for img_path, lbl_path in zip(image_paths, label_paths):
        from PIL import Image  # local import keeps module import light

        with Image.open(img_path) as im:
            size = (im.width, im.height)
        gts = load_ground_truth(lbl_path, size[0], size[1])
        preds = detector.detect(str(img_path))
        result = classify_image(
            gts,
            preds,
            image_size=size,
            iou_threshold=iou_threshold,
            conf_threshold=conf_threshold,
        )
        result["image"] = img_path.name
        result["ground_truth"] = [
            {
                "class": g["class"],
                "box_xyxy_abs": [round(v, 1) for v in g["box"]],
                "area_fraction": round(g["area_fraction"], 6),
            }
            for g in gts
        ]
        result["predictions"] = [
            {
                "x": p.x,
                "y": p.y,
                "width": p.width,
                "height": p.height,
                "confidence": round(p.confidence, 4),
            }
            for p in preds
        ]
        per_image.append(result)

    return {
        "analysis_parameters": {
            "iou_threshold": iou_threshold,
            "conf_threshold": conf_threshold,
            "small_ball_fraction": SMALL_BALL_FRACTION,
            "poor_localization_area_ratio": POOR_LOCALIZATION_AREA_RATIO,
            "near_miss_iou": NEAR_MISS_IOU,
        },
        "num_images": len(per_image),
        "per_image": per_image,
        "aggregate": aggregate(per_image),
    }


def annotate_images(
    report: Dict[str, Any],
    image_dir: Path,
    out_dir: Path,
    *,
    max_images: Optional[int] = None,
) -> int:
    """Render GT (green solid / red where missed), TP (blue), FP (orange) onto
    copies of the images under ``out_dir``. Returns the number rendered."""
    from PIL import Image, ImageDraw

    out_dir.mkdir(parents=True, exist_ok=True)
    rendered = 0
    for r in report["per_image"]:
        if max_images is not None and rendered >= max_images:
            break
        src = image_dir / r["image"]
        if not src.exists():
            continue
        with Image.open(src) as im:
            im = im.convert("RGB")
        draw = ImageDraw.Draw(im)
        missed_idx = {m["gt_index"] for m in r["missed"]}
        for i, g in enumerate(r["ground_truth"]):
            box = tuple(g["box_xyxy_abs"])
            draw.rectangle(box, outline=(255, 0, 0) if i in missed_idx else (0, 200, 0), width=3)
        preds_sorted = sorted(r["predictions"], key=lambda p: p["confidence"], reverse=True)
        tp_idx = {t["pred_index"] for t in r["tp"]}
        fp_idx = {f["pred_index"] for f in r["fp"]}
        for i, p in enumerate(preds_sorted):
            box = (
                p["x"] - p["width"] / 2, p["y"] - p["height"] / 2,
                p["x"] + p["width"] / 2, p["y"] + p["height"] / 2,
            )
            color = (30, 100, 255) if i in tp_idx else (255, 140, 0) if i in fp_idx else (160, 160, 160)
            draw.rectangle(box, outline=color, width=2)
        im.save(out_dir / r["image"], quality=90)
        rendered += 1
    return rendered


def main(argv: Optional[Sequence[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--weights", type=Path, required=True)
    p.add_argument("--images", type=Path, required=True, help="test image directory")
    p.add_argument("--labels", type=Path, required=True, help="test label directory")
    p.add_argument("--report", type=Path, required=True, help="output analysis JSON")
    p.add_argument("--annotated-dir", type=Path, default=None)
    p.add_argument("--max-images", type=int, default=None)
    p.add_argument("--iou-threshold", type=float, default=DEFAULT_IOU_THRESHOLD)
    p.add_argument("--conf-threshold", type=float, default=DEFAULT_CONF_THRESHOLD)
    p.add_argument(
        "--imgsz",
        type=int,
        default=None,
        help="detector inference resolution override (e.g. 640); default = checkpoint's training size",
    )
    args = p.parse_args(argv)

    try:
        from .inference import UltralyticsBallDetector
    except ImportError:  # pragma: no cover
        from inference import UltralyticsBallDetector

    detector = UltralyticsBallDetector(args.weights, confidence=0.01, imgsz=args.imgsz)
    image_paths = sorted(x for x in args.images.iterdir() if x.suffix.lower() in IMAGE_EXTS)
    if args.max_images is not None:
        image_paths = image_paths[: args.max_images]
    label_paths = [args.labels / (x.stem + ".txt") for x in image_paths]

    report = run_analysis(
        detector,
        image_paths,
        label_paths,
        iou_threshold=args.iou_threshold,
        conf_threshold=args.conf_threshold,
    )
    report["analysis_parameters"]["imgsz"] = args.imgsz  # record the operating point
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["aggregate"], indent=2))

    if args.annotated_dir is not None:
        n = annotate_images(report, args.images, args.annotated_dir, max_images=args.max_images)
        print(f"annotated {n} images -> {args.annotated_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
