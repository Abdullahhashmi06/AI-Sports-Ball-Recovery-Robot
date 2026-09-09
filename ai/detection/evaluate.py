"""Evaluate a trained Ball Detection V1 checkpoint and write a JSON report.

Records: precision, recall, mAP50/mAP50-95 (from the ultralytics val API),
per-image inference latency (wall-clock, averaged over ``--latency-repeats``),
number of test images, and dataset/version provenance. The report carries an
explicit ``validation_domain`` field — public-dataset metrics must never be
presented as robot-validation results (ai/detection/README.md §4.3).
"""

from __future__ import annotations

import argparse
import json
import platform
import time
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

REPO_ROOT = Path(__file__).resolve().parents[2]


def evaluate(weights: Path, data: Path, *, split: str = "test", latency_repeats: int = 5) -> Dict[str, Any]:
    """Run ultralytics validation + latency measurement; return the report dict."""
    try:
        from ultralytics import YOLO
    except ImportError as exc:  # pragma: no cover - environment error
        raise SystemExit("ultralytics is required (install ai/detection/requirements.txt)") from exc

    model = YOLO(str(weights))
    # ultralytics resolves relative dataset paths against its own settings dir,
    # so hand it absolute paths explicitly.
    data_abs = Path(data).resolve()
    metrics = model.val(data=str(data_abs), split=split, verbose=False)

    # Ultralytics Metric object — read defensively across versions.
    def _get(obj: Any, attr: str, default: Optional[float] = None) -> Optional[float]:
        v = getattr(obj, attr, default)
        try:
            return float(v)
        except (TypeError, ValueError):
            return default

    box_metrics = getattr(metrics, "box", metrics)
    results_dict = getattr(metrics, "results_dict", {}) or {}

    precision = _get(box_metrics, "mp", results_dict.get("metrics/precision(B)"))
    recall = _get(box_metrics, "mr", results_dict.get("metrics/recall(B)"))
    map50 = _get(box_metrics, "map50", results_dict.get("metrics/mAP50(B)"))
    map5095 = _get(box_metrics, "map", results_dict.get("metrics/mAP50-95(B)"))

    # --- latency: per-image predict() wall time on the val/test images ---
    imgs = sorted(Path(data_abs).parent.glob(f"images/{split}/*.*"))
    latencies: list[float] = []
    if imgs:
        for _ in range(max(1, latency_repeats)):
            t0 = time.perf_counter()
            for img in imgs:
                model.predict(str(img), verbose=False)
            latencies.append((time.perf_counter() - t0) / len(imgs))
    avg_latency_s = sum(latencies) / len(latencies) if latencies else None

    return {
        "validation_domain": "public_dataset",  # NEVER claim "robot" until real footage is evaluated
        "robot_validation_performed": False,
        "weights": str(weights),
        "dataset": str(data),
        "split": split,
        "num_images": len(imgs),
        "metrics": {
            "precision": precision,
            "recall": recall,
            "mAP50": map50,
            "mAP50_95": map5095,
        },
        "latency": {
            "avg_per_image_s": avg_latency_s,
            "repeats": max(1, latency_repeats) if imgs else 0,
            "device": "cpu (workstation) — NOT a robot latency budget (OD-05 open)",
        },
        "environment": {
            "python": platform.python_version(),
            "torch": _safe_version("torch"),
            "ultralytics": _safe_version("ultralytics"),
        },
    }


def _safe_version(module_name: str) -> Optional[str]:
    try:
        import importlib

        mod = importlib.import_module(module_name)
        return str(getattr(mod, "__version__", None))
    except Exception:
        return None


def main(argv: Optional[Sequence[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--weights", type=Path, required=True)
    p.add_argument("--data", type=Path, required=True, help="dataset data.yaml")
    p.add_argument("--split", choices=("val", "test"), default="test")
    p.add_argument("--latency-repeats", type=int, default=5)
    p.add_argument("--report", type=Path, required=True, help="output JSON report path")
    args = p.parse_args(argv)

    report = evaluate(args.weights, args.data, split=args.split, latency_repeats=args.latency_repeats)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
