"""Deterministic train/val/test splitting for the robot dataset.

Moves (or copies) image+label pairs into ``images/{train,val,test}`` and
``labels/{train,val,test}`` using a fixed seed — the same reproducibility
discipline as the COCO pipeline. Split fractions are tooling parameters, not
robot assumptions.
"""

from __future__ import annotations

import argparse
import random
import shutil
import sys
from pathlib import Path
from typing import Dict, Optional, Sequence, Tuple

from .validate import IMAGE_EXTS


def plan_split(
    stems: Sequence[str],
    *,
    seed: int = 20260906,
    val_frac: float = 0.15,
    test_frac: float = 0.05,
    sessions: Optional[Dict[str, str]] = None,
) -> Dict[str, str]:
    """Assign each stem to a split with a seeded shuffle. Pure function —
    returns {stem: split} without touching the filesystem.

    ``sessions`` optionally maps stem → session id. When given, **whole
    sessions** are assigned to splits (never individual frames from the same
    session straddling train/val/test — the classic leakage of video-derived
    datasets). Session assignment is seeded and sized by frame count so large
    sessions spread evenly across splits.
    """
    if not 0.0 <= val_frac < 1.0 or not 0.0 <= test_frac < 1.0 or val_frac + test_frac >= 1.0:
        raise ValueError("invalid split fractions")
    stems = sorted(stems)
    if sessions is None:
        rng = random.Random(seed)
        rng.shuffle(stems)
        n_val = int(round(len(stems) * val_frac))
        n_test = int(round(len(stems) * test_frac))
        assignment: Dict[str, str] = {}
        for i, stem in enumerate(stems):
            if i < n_val:
                assignment[stem] = "val"
            elif i < n_val + n_test:
                assignment[stem] = "test"
            else:
                assignment[stem] = "train"
        return assignment

    # Session-grouped mode: assign sessions (weighted by frame count) to
    # splits, then stamp every frame of a session with its session's split.
    unknown = sorted({s for s in stems if s not in sessions})
    if unknown:
        raise ValueError(f"stems missing from sessions mapping: {unknown[:5]}...")
    by_session: Dict[str, List[str]] = {}
    for s in stems:
        by_session.setdefault(sessions[s], []).append(s)
    # Deterministic order: session name, then distribute by descending size
    # (larger sessions first get the split with the largest remaining budget).
    rng = random.Random(seed)
    session_names = sorted(by_session)
    rng.shuffle(session_names)
    session_names.sort(key=lambda n: -len(by_session[n]))
    sizes = {n: len(by_session[n]) for n in session_names}
    total = len(stems)
    target = {
        "val": total * val_frac,
        "test": total * test_frac,
        "train": total * (1.0 - val_frac - test_frac),
    }
    filled = {"val": 0.0, "test": 0.0, "train": 0.0}
    session_split: Dict[str, str] = {}
    for name in session_names:
        # pick the split whose remaining relative budget is largest
        split = max(target, key=lambda k: (target[k] - filled[k], k))
        session_split[name] = split
        filled[split] += sizes[name]
    assignment = {}
    for s in stems:
        assignment[s] = session_split[sessions[s]]
    return assignment


def split_dataset(
    image_dir: Path,
    label_dir: Path,
    out_dir: Path,
    *,
    seed: int = 20260906,
    val_frac: float = 0.15,
    test_frac: float = 0.05,
    copy: bool = True,
    dedupe: bool = True,
    session_pattern: Optional[str] = None,
) -> Dict[str, int]:
    """Split image+label pairs into ``out_dir``/{images,labels}/{train,val,test}.

    With ``dedupe=True`` near-duplicate frames are dropped first (leaks between
    splits are the classic self-deception of video-derived datasets).

    ``session_pattern`` optionally extracts the session id from each filename
    via a regex with exactly one capture group (e.g. ``r"(sess\\d+)_"``). When
    given, whole sessions are assigned to splits so no recording straddles
    train/val/test. Returns {split: count}.
    """
    import re

    from .frames import filter_near_duplicates

    images = sorted(p for p in image_dir.iterdir() if p.suffix.lower() in IMAGE_EXTS)
    label_by_stem = {p.stem: p for p in label_dir.glob("*.txt")}
    paired = [p for p in images if p.stem in label_by_stem]

    sessions: Optional[Dict[str, str]] = None
    if session_pattern is not None:
        rx = re.compile(session_pattern)
        sessions = {}
        bad = []
        for p in paired:
            m = rx.search(p.stem)
            if m is None or not m.groups():
                bad.append(p.stem)
            else:
                sessions[p.stem] = m.group(1)
        if bad:
            raise ValueError(
                f"{len(bad)} filenames do not match session pattern {session_pattern!r}: {bad[:5]}..."
            )

    if dedupe:
        kept, dropped = filter_near_duplicates(paired)
        print(f"near-duplicate filter: {len(dropped)} dropped, {len(kept)} kept")
        paired = kept

    assignment = plan_split(
        [p.stem for p in paired],
        seed=seed,
        val_frac=val_frac,
        test_frac=test_frac,
        sessions=sessions,
    )
    counts = {"train": 0, "val": 0, "test": 0}
    for img in paired:
        split = assignment[img.stem]
        img_dst = out_dir / "images" / split
        lbl_dst = out_dir / "labels" / split
        img_dst.mkdir(parents=True, exist_ok=True)
        lbl_dst.mkdir(parents=True, exist_ok=True)
        mover = shutil.copy2 if copy else shutil.move
        mover(img, img_dst / img.name)
        mover(label_by_stem[img.stem], lbl_dst / label_by_stem[img.stem].name)
        counts[split] += 1

    # dataset.yaml for ultralytics
    yaml_path = out_dir / "data.yaml"
    yaml_path.write_text(
        f"path: {out_dir.resolve().as_posix()}\n"
        "train: images/train\nval: images/val\ntest: images/test\n"
        "nc: 1\nnames:\n  0: ball\n",
        encoding="utf-8",
    )
    return counts


def main(argv: Optional[Sequence[str]] = None) -> int:  # pragma: no cover - thin CLI
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("image_dir", type=Path)
    p.add_argument("label_dir", type=Path)
    p.add_argument("out_dir", type=Path)
    p.add_argument("--seed", type=int, default=20260906)
    p.add_argument("--val-frac", type=float, default=0.15)
    p.add_argument("--test-frac", type=float, default=0.05)
    p.add_argument("--move", action="store_true", help="move instead of copy")
    p.add_argument("--no-dedupe", action="store_true")
    p.add_argument(
        "--session-pattern",
        type=str,
        default=None,
        help='regex with one capture group extracting the session id from filenames (e.g. "(sess\\d+)_")',
    )
    args = p.parse_args(argv)

    counts = split_dataset(
        args.image_dir,
        args.label_dir,
        args.out_dir,
        seed=args.seed,
        val_frac=args.val_frac,
        test_frac=args.test_frac,
        copy=not args.move,
        dedupe=not args.no_dedupe,
        session_pattern=args.session_pattern,
    )
    print("split counts:", counts)
    return 0


if __name__ == "__main__":
    sys.exit(main())
