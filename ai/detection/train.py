"""Transfer-learning training entry point for Ball Detection V1.

Loads the YAML config from ``configs/`` and launches a single-class
ultralytics fine-tune from COCO-pretrained ``yolo11n.pt`` weights. Never
trains from scratch (project rule). Weights/runs are written to gitignored
paths. No hardware values, thresholds, or OD-01…OD-16 decisions are involved.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional, Sequence

try:  # package import (tests) or direct-script execution (CLI)
    from .common import load_config, resolve_under
except ImportError:  # pragma: no cover - script mode
    from common import load_config, resolve_under

REPO_ROOT = Path(__file__).resolve().parents[2]


def validate_config(cfg: dict) -> None:
    """Validate training config *before* any ML dependency is imported
    (keeps this check unit-testable on machines without ultralytics).

    Transfer-learning guard: only *pretrained* checkpoints may start a run —
    training from scratch is not permitted by project rule.
    """
    model_name = str(cfg.get("model", ""))
    if not (model_name.endswith(".pt") or model_name.startswith("yolo")):
        raise ValueError(
            "config 'model' must be a pretrained checkpoint (e.g. yolo11n.pt); "
            "training from scratch is not permitted"
        )
    for key in ("data", "epochs", "batch", "imgsz"):
        if key not in cfg:
            raise ValueError(f"config missing required key '{key}'")


def train(config_path: Path, *, resume: bool = False) -> Path:
    """Run the fine-tune described by ``config_path``; return best-weights path.

    With ``resume=True`` the run named in the config (project/name) continues
    from its ``weights/last.pt`` — useful for long CPU runs chunked across
    multiple invocations (each chunk re-invokes with --resume).
    """
    try:
        from ultralytics import YOLO
    except ImportError as exc:  # pragma: no cover - environment error
        raise SystemExit(
            "ultralytics is required (install ai/detection/requirements.txt into ai/.venv)"
        ) from exc

    cfg = load_config(config_path)
    run_dir = resolve_under(REPO_ROOT, cfg.get("project", "runs")) / cfg.get("name", "ball_v1")

    if resume:
        last = run_dir / "weights" / "last.pt"
        if not last.exists():
            raise SystemExit(f"--resume requested but no checkpoint at {last}")
        model = YOLO(str(last))
        results = model.train(resume=True)
        save_dir = Path(results.save_dir)
    else:
        validate_config(cfg)

        data = resolve_under(REPO_ROOT, cfg["data"])
        if not Path(data).exists():
            raise SystemExit(
                f"dataset not found at {data} — run detection/data/prepare_dataset.py first"
            )

        overrides = {
            k: v
            for k, v in cfg.items()
            if k not in ("model", "data", "single_class", "project", "name")
        }
        model = YOLO(cfg["model"])
        results = model.train(
            data=str(data),
            project=str(resolve_under(REPO_ROOT, cfg.get("project", "runs"))),
            name=cfg.get("name", "ball_v1"),
            exist_ok=True,
            **overrides,
        )
        save_dir = Path(results.save_dir)
    best = save_dir / "weights" / "best.pt"
    if not best.exists():  # ultralytics always writes best.pt; be explicit anyway
        raise SystemExit(f"training finished but best weights missing at {best}")
    print(f"training complete: {best}")
    return best


def main(argv: Optional[Sequence[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--config",
        type=Path,
        default=Path(__file__).resolve().parent / "configs" / "ball_v1.yaml",
    )
    p.add_argument(
        "--resume",
        action="store_true",
        help="continue the config's run (project/name) from weights/last.pt",
    )
    args = p.parse_args(argv)
    try:
        train(args.config, resume=args.resume)
    except SystemExit as exc:
        # propagate explicit exits (missing dataset etc.) with their message
        print(exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
