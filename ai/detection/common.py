"""Shared helpers for the detection package (stdlib only).

Keeps YAML/ultralytics imports out of the committed unit-test path: tests
exercise pure helpers here without any ML dependency installed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import yaml


def load_config(path: Path) -> Dict[str, Any]:
    """Load a training YAML config into a plain dict."""
    with open(path, "r", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    if not isinstance(cfg, dict):
        raise ValueError(f"config {path} must be a YAML mapping")
    return cfg


def resolve_under(base: Path, value: str) -> Path:
    """Resolve a config path relative to the repository root (the config files
    use repo-root-relative paths like ``datasets/…`` and ``runs/…``)."""
    p = Path(value)
    return p if p.is_absolute() else base / p


__all__ = ["load_config", "resolve_under"]
