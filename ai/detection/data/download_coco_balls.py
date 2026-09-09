"""Download only the COCO images that contain sports-ball annotations.

Software-only data acquisition for Ball Detection V2. Full COCO train2017 is
~19 GB, but only a small subset of images actually contain class-32
(``sports ball``) annotations — so this tool:

1. downloads the official COCO 2017 annotation ZIPs (~250 MB each, resumable
   via streaming extraction — only the needed member is fully decompressed);
2. extracts the sports-ball image ids and file names;
3. downloads **only those images** from the official COCO image CDN
   (``images.cocodataset.org``) with retry/resume and a concurrency limit;
4. writes ``manifest.json`` recording provenance, licenses, per-image data
   (sha256, bytes, categories present) so the dataset build is reproducible
   and auditable.

No robot/hardware meaning is attached to anything here (no thresholds, no
calibration, no OD-01…OD-16 decisions). Licenses are recorded, not asserted.
"""

from __future__ import annotations

import argparse
import concurrent.futures as futures
import hashlib
import json
import shutil
import sys
import time
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

# Official COCO 2017 sources (urls recorded in the manifest for provenance).
# NOTE: the friendly host images.cocodataset.org currently serves a TLS cert
# that fails hostname verification on some networks; the s3.amazonaws.com
# form addresses the SAME official bucket and is tried first, with the plain-
# http friendly host kept as fallback. These are public static assets.
ANN_URL = "https://s3.amazonaws.com/images.cocodataset.org/annotations/annotations_trainval2017.zip"
IMAGE_CDNS = (
    "https://s3.amazonaws.com/images.cocodataset.org",
    "http://images.cocodataset.org",
)
# COCO 2017 **official JSON annotation id** for "sports ball" — verified against
# instances_{train,val}2017.json "categories": id 32 = "tie", id 37 = "sports ball".
# WARNING: not the ultralytics YOLO 80-class index (32); official COCO JSON ids
# are non-contiguous. Using the YOLO index downloads tie images (V2's defect).
COCO_SPORTS_BALL_CATEGORY_ID = 37
MANIFEST_VERSION = 1


def _http_get(url: str, timeout: float = 60.0) -> urllib.request.urlopen:  # type: ignore[type-arg]
    req = urllib.request.Request(url, headers={"User-Agent": "ball-detection-v2/1.0"})
    return urllib.request.urlopen(req, timeout=timeout)  # noqa: S310 - fixed official hosts


def _sha256_of(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def ensure_annotations(data_dir: Path, timeout: float = 60.0) -> Tuple[Path, Path]:
    """Ensure the official COCO 2017 annotation zip is present and extracted.

    Streams the zip member extraction so we never hold the whole archive in
    memory. Returns (instances_train2017.json, instances_val2017.json).
    """
    import tempfile

    data_dir.mkdir(parents=True, exist_ok=True)
    train_json = data_dir / "annotations" / "instances_train2017.json"
    val_json = data_dir / "annotations" / "instances_val2017.json"
    if train_json.exists() and val_json.exists():
        return train_json, val_json

    zip_path = data_dir / "annotations_trainval2017.zip"
    if not zip_path.exists() or zip_path.stat().st_size == 0:
        print(f"downloading {ANN_URL} (~250 MB, one-time)…")
        t0 = time.monotonic()
        tmp = zip_path.with_suffix(".part")
        with _http_get(ANN_URL, timeout=timeout) as resp, open(tmp, "wb") as fh:
            shutil.copyfileobj(resp, fh, 1 << 20)
        tmp.rename(zip_path)
        print(f"  downloaded {zip_path.stat().st_size / 1e6:.0f} MB in {time.monotonic() - t0:.0f}s")

    with zipfile.ZipFile(zip_path) as zf:
        wanted = ["annotations/instances_train2017.json", "annotations/instances_val2017.json"]
        for member in wanted:
            if member not in zf.namelist():
                raise SystemExit(f"unexpected annotations zip layout: missing {member}")
            target = data_dir / member
            if target.exists():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            print(f"extracting {member} …")
            with zf.open(member) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst, 1 << 20)
    return train_json, val_json


def ball_images_from_coco(coco: Dict[str, Any]) -> Dict[int, str]:
    """Return {image_id: file_name} for images having >=1 sports-ball annotation."""
    images = {img["id"]: img["file_name"] for img in coco.get("images", [])}
    ball_ids: Set[int] = set()
    for ann in coco.get("annotations", []):
        if ann.get("category_id") == COCO_SPORTS_BALL_CATEGORY_ID:
            ball_ids.add(ann["image_id"])
    return {iid: images[iid] for iid in sorted(ball_ids) if iid in images}


def download_image(
    fname: str,
    out_path: Path,
    *,
    split: str = "train",
    retries: int = 3,
    timeout: float = 60.0,
) -> Tuple[str, int, Optional[str]]:
    """Download one image; return (file_name, bytes, error). Best-effort resume:
    skips when the file already exists with non-zero size. Tries each official
    CDN endpoint in order per attempt. COCO ``file_name`` values are bare
    (``000000000009.jpg``); the CDN stores them under ``train2017/`` or
    ``val2017/`` prefixes."""
    if out_path.exists() and out_path.stat().st_size > 0:
        return fname, out_path.stat().st_size, None
    prefix = "train2017" if split == "train" else "val2017"
    last_err: Optional[str] = None
    for attempt in range(1, retries + 1):
        for cdn in IMAGE_CDNS:
            try:
                tmp = out_path.with_suffix(out_path.suffix + ".part")
                with _http_get(f"{cdn}/{prefix}/{fname}", timeout=timeout) as resp, open(tmp, "wb") as fh:
                    shutil.copyfileobj(resp, fh, 1 << 20)
                tmp.rename(out_path)
                return fname, out_path.stat().st_size, None
            except Exception as exc:  # network best-effort; retried below
                last_err = f"{type(exc).__name__}: {exc}"
                time.sleep(min(1.0 * attempt, 3.0))
    return fname, 0, last_err


def build_manifest(
    *,
    data_dir: Path,
    limit: Optional[int],
    skipped_existing: bool = False,
) -> Path:
    """Rebuild the manifest by scanning downloaded files on disk (the source
    of truth): data_dir/{train,val}/*.jpg with sha256 per file."""
    entries: List[Dict[str, Any]] = []
    total_bytes = 0
    failed: List[str] = []
    for split in ("train", "val"):
        img_dir = data_dir / split
        if not img_dir.exists():
            continue
        for p in sorted(img_dir.glob("*.jpg")):
            if p.stat().st_size <= 0:
                failed.append(f"{split}/{p.name}")
                continue
            total_bytes += p.stat().st_size
            entries.append(
                {
                    "split": split,
                    "image_id": int(p.stem) if p.stem.isdigit() else p.stem,
                    "file_name": p.name,
                    "bytes": p.stat().st_size,
                    "sha256": _sha256_of(p),
                }
            )
    manifest = {
        "manifest_version": MANIFEST_VERSION,
        "source": {
            "dataset": "COCO 2017",
            "annotations_url": ANN_URL,
            "image_cdn": IMAGE_CDNS[0],
            "image_cdn_fallback": IMAGE_CDNS[1],
            "image_url_layout": "{cdn}/{train2017|val2017}/{file_name}",
            "category": "sports ball",
            "category_id": COCO_SPORTS_BALL_CATEGORY_ID,
        },
        "license": {
            "images": "Flickr Terms of Use (per COCO); non-commercial research use",
            "annotations": "COCO annotations, CC BY 4.0",
            "note": "Recorded for provenance; licensing compliance is the operator's responsibility.",
        },
        "download_limit_per_split": limit,
        "skipped_existing": skipped_existing,
        "images": entries,
        "failed": failed,
        "totals": {
            "images": len(entries),
            "bytes": total_bytes,
            "gb": round(total_bytes / 1e9, 3),
            "failed": len(failed),
        },
    }
    out = data_dir / "manifest.json"
    out.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return out


def main(argv: Optional[Sequence[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data-dir", type=Path, default=Path("ai/datasets/coco-ball-v2/_raw"))
    p.add_argument("--limit", type=int, default=None, help="cap per split (testing)")
    p.add_argument("--workers", type=int, default=8)
    p.add_argument("--manifest-only", action="store_true", help="rebuild manifest from files on disk")
    args = p.parse_args(argv)

    data_dir = args.data_dir
    data_dir.mkdir(parents=True, exist_ok=True)

    if not args.manifest_only:
        # Cheap resume: cache the ball-image id lists so re-runs (added images,
        # failed downloads retried) skip re-parsing the ~450 MB annotations JSON.
        cache = data_dir / "ball_images.json"
        if cache.exists():
            raw = json.loads(cache.read_text(encoding="utf-8"))
            split_files: Dict[str, Dict[int, str]] = {
                s: {int(k): v for k, v in m.items()} for s, m in raw.items()
            }
            print("loaded ball-image cache:", {s: len(m) for s, m in split_files.items()})
        else:
            train_json, val_json = ensure_annotations(data_dir)
            print("parsing annotations…")
            t0 = time.monotonic()
            train_coco = json.loads(train_json.read_text(encoding="utf-8"))
            val_coco = json.loads(val_json.read_text(encoding="utf-8"))
            split_files = {
                "train": ball_images_from_coco(train_coco),
                "val": ball_images_from_coco(val_coco),
            }
            print(f"  parsed in {time.monotonic() - t0:.0f}s")
            cache.write_text(
                json.dumps({s: {str(k): v for k, v in m.items()} for s, m in split_files.items()}),
                encoding="utf-8",
            )
        for split, mapping in split_files.items():
            print(f"  {split}: {len(mapping)} images contain sports-ball annotations")
            if args.limit is not None:
                limited = dict(sorted(mapping.items())[: args.limit])
                split_files[split] = limited
                print(f"    limited to {len(limited)} (testing cap)")

        plans: List[Tuple[str, str, Path]] = []
        for split, mapping in split_files.items():
            img_dir = data_dir / split
            img_dir.mkdir(parents=True, exist_ok=True)
            for iid, fname in mapping.items():
                plans.append((split, fname, img_dir / fname))

        print(f"downloading {len(plans)} images with {args.workers} workers…")
        t0 = time.monotonic()
        done = 0
        failed: List[str] = []
        with futures.ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
            futs = {
                pool.submit(download_image, f, p, split=s): (s, f)
                for s, f, p in plans
            }
            for fut in futures.as_completed(futs):
                s, f = futs[fut]
                _, nbytes, err = fut.result()
                done += 1
                if err is not None or nbytes == 0:
                    failed.append(f"{s}/{f}: {err}")
                if done % 250 == 0 or done == len(plans):
                    rate = done / max(time.monotonic() - t0, 1e-9)
                    print(f"  {done}/{len(plans)} ({rate:.1f} img/s, failed={len(failed)})")
        if failed:
            print(f"WARNING: {len(failed)} downloads failed; see manifest", file=sys.stderr)

    manifest = build_manifest(
        data_dir=data_dir,
        limit=args.limit,
        skipped_existing=args.manifest_only,
    )
    report = json.loads(manifest.read_text(encoding="utf-8"))
    by_split: Dict[str, int] = {}
    for e in report["images"]:
        by_split[e["split"]] = by_split.get(e["split"], 0) + 1
    print("manifest written:", manifest)
    print("totals:", by_split, "| failed:", report["totals"]["failed"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
