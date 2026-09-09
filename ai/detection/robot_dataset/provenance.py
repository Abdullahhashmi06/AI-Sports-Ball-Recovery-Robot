"""Dataset provenance metadata for the robot dataset.

Writes a machine-readable ``provenance.json`` recording what data exists, how
it was produced, and — critically — its **validation domain** (public dataset
vs real robot footage), so public-data metrics can never silently masquerade
as robot validation.
"""

from __future__ import annotations

import argparse
import json
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

PROVENANCE_VERSION = 1


def write_provenance(
    out_path: Path,
    *,
    dataset_name: str,
    validation_domain: str,
    sources: List[Dict[str, Any]],
    capture_sessions: Optional[List[Dict[str, Any]]] = None,
    notes: str = "",
    **extra: Any,
) -> Path:
    """Write ``provenance.json``; returns the path.

    ``validation_domain`` must be ``"public_dataset"`` or ``"robot"`` — this is
    the field every downstream consumer reads to interpret the metrics.
    Capture-session details (date, location, camera) are supplied by the
    operator at capture time; this module never invents them.
    """
    if validation_domain not in ("public_dataset", "robot"):
        raise ValueError("validation_domain must be 'public_dataset' or 'robot'")
    record: Dict[str, Any] = {
        "provenance_version": PROVENANCE_VERSION,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "dataset_name": dataset_name,
        "validation_domain": validation_domain,
        "sources": sources,
        "capture_sessions": capture_sessions or [],
        "environment": {
            "python": platform.python_version(),
        },
        "notes": notes,
    }
    record.update(extra)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return out_path


def main(argv: Optional[Sequence[str]] = None) -> int:  # pragma: no cover - thin CLI
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("out_path", type=Path)
    p.add_argument("--name", required=True)
    p.add_argument("--domain", choices=("public_dataset", "robot"), required=True)
    p.add_argument("--source", action="append", default=[], help="source description (repeatable)")
    p.add_argument("--notes", default="")
    args = p.parse_args(argv)

    write_provenance(
        args.out_path,
        dataset_name=args.name,
        validation_domain=args.domain,
        sources=[{"description": s} for s in args.source],
        notes=args.notes,
    )
    print(f"provenance written: {args.out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
