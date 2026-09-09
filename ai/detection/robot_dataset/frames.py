"""Frame extraction and near-duplicate filtering for robot footage.

Software-only utilities:
* ``extract_frames`` — pull frames from video files at a configurable stride
  (every Nth frame; the stride is a *tooling parameter*, not a robot constant)
  into an output directory.
* ``frame_hash`` / ``filter_near_duplicates`` — deterministic perceptual-ish
  hashing (mean-thresholded grayscale downscales, Hamming distance) to drop
  near-identical frames that would leak between dataset splits.

Dependencies: Pillow only (numpy optional for hashing). No camera/robot
assumptions of any kind.
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

VIDEO_EXTS = (".mp4", ".avi", ".mov", ".mkv", ".wmv")
IMAGE_EXTS = (".jpg", ".jpeg", ".png")


def _video_frame_count(video_path: Path) -> int:
    """Frame count via OpenCV if available; otherwise 0 (unknown)."""
    try:
        import cv2  # optional dependency

        cap = cv2.VideoCapture(str(video_path))
        n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        cap.release()
        return n
    except ImportError:
        return 0


def extract_frames(
    video_path: Path,
    out_dir: Path,
    *,
    stride: int = 10,
    prefix: Optional[str] = None,
    max_frames: Optional[int] = None,
) -> List[Path]:
    """Extract every ``stride``-th frame of ``video_path`` as JPEGs in ``out_dir``.

    Returns the list of written frame paths. Requires OpenCV (``pip install
    opencv-python``) at *use* time — not imported by the committed test suite,
    which exercises the pure helpers instead.
    """
    if stride <= 0:
        raise ValueError("stride must be positive")
    try:
        import cv2
    except ImportError as exc:  # pragma: no cover - environment error
        raise SystemExit(
            "OpenCV (opencv-python) is required for video extraction; "
            "install it into ai/.venv when real footage exists"
        ) from exc

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise SystemExit(f"cannot open video: {video_path}")
    out_dir.mkdir(parents=True, exist_ok=True)
    prefix = prefix or video_path.stem
    written: List[Path] = []
    idx = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if idx % stride == 0:
            name = f"{prefix}_frame{idx:08d}.jpg"
            cv2.imwrite(str(out_dir / name), frame)
            written.append(out_dir / name)
            if max_frames is not None and len(written) >= max_frames:
                break
        idx += 1
    cap.release()
    return written


def frame_hash(image_path: Path, *, hash_size: int = 8) -> str:
    """Deterministic perceptual-ish hash: grayscale, downscale to
    ``hash_size x hash_size``, threshold at the mean → hex string.

    Pure Pillow (no numpy needed). Same-image ⇒ same hash always; visually
    near-identical frames ⇒ hashes with small Hamming distance.
    """
    from PIL import Image

    with Image.open(image_path) as im:
        gray = im.convert("L").resize((hash_size, hash_size))
    pixels = list(gray.getdata())
    mean = sum(pixels) / len(pixels)
    bits = "".join("1" if p > mean else "0" for p in pixels)
    return f"{int(bits, 2):0{hash_size * hash_size // 4}x}"


def hamming_distance(hash_a: str, hash_b: str) -> int:
    """Hamming distance between two hex hashes (bit level)."""
    if len(hash_a) != len(hash_b):
        raise ValueError("hash length mismatch")
    return bin(int(hash_a, 16) ^ int(hash_b, 16)).count("1")


def filter_near_duplicates(
    image_paths: Sequence[Path],
    *,
    threshold_bits: int = 5,
    hash_size: int = 8,
) -> Tuple[List[Path], Dict[str, str]]:
    """Greedy near-duplicate filtering.

    Walks images in order, keeping an image only if its hash differs by more
    than ``threshold_bits`` bits from every kept hash (64-bit hash ⇒ distance
    > 5 of 64 bits). Returns (kept_paths, {dropped_path: kept_path_reason}).
    """
    kept: List[Path] = []
    kept_hashes: List[str] = []
    dropped: Dict[str, str] = {}
    for p in image_paths:
        h = frame_hash(p, hash_size=hash_size)
        match = next(
            (k for k, kh in zip(kept, kept_hashes) if hamming_distance(h, kh) <= threshold_bits),
            None,
        )
        if match is None:
            kept.append(p)
            kept_hashes.append(h)
        else:
            dropped[str(p)] = f"near-duplicate of {match.name} (distance <= {threshold_bits})"
    return kept, dropped


def main(argv: Optional[List[str]] = None) -> int:  # pragma: no cover - thin CLI
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    ex = sub.add_parser("extract", help="extract frames from a video")
    ex.add_argument("video", type=Path)
    ex.add_argument("--out", type=Path, required=True)
    ex.add_argument("--stride", type=int, default=10)
    ex.add_argument("--max-frames", type=int, default=None)

    dd = sub.add_parser("dedupe", help="list near-duplicate frames in a directory")
    dd.add_argument("images", type=Path)
    dd.add_argument("--threshold-bits", type=int, default=5)

    args = p.parse_args(argv)
    if args.cmd == "extract":
        frames = extract_frames(args.video, args.out, stride=args.stride, max_frames=args.max_frames)
        print(f"extracted {len(frames)} frames -> {args.out}")
    else:
        imgs = sorted(x for x in args.images.iterdir() if x.suffix.lower() in IMAGE_EXTS)
        kept, dropped = filter_near_duplicates(imgs)
        print(f"{len(kept)} kept, {len(dropped)} dropped:")
        for k, reason in dropped.items():
            print(f"  {k}: {reason}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
